"""Clean a finished recording: transcribe it with full context, mute banned words, leave video untouched."""
import contextlib
import os
import tempfile
from pathlib import Path

import av
import numpy as np

from . import wordlists
from .censor_sound import CensorVoice, replacement_clip
from .detector import ASR_RATE, DEFAULT_MIN_PROB, banned_spans, load_model, resolve_language

from faster_whisper import BatchedInferencePipeline  # after .detector, which prepares the engine's environment

RAMP_SEC = 0.005
AUDIO_ONLY_FALLBACK = ".m4a"
FILE_CPU_THREADS = max(2, (os.cpu_count() or 4) // 2)  # one-off job without a GPU: use half the CPU
BATCH_SIZE = 8


def default_output_path(src: Path) -> Path:
    return src.with_name(f"{src.stem} (clean){src.suffix}")


def clean_file(src, dst=None, model_size="base.en", mute_mode="silence", pad_before_ms=200,
               pad_after_ms=250, matcher=None, min_prob=DEFAULT_MIN_PROB, language="auto",
               beep_hz=1000.0, censor_volume=0.4, censor_sound=None, model_bundle=None,
               progress=None, log=None):
    """
    Returns (output_path, catches) where catches is a list of (seconds, word).
    progress(fraction 0..1, stage) and log(text) are optional callbacks.
    """
    src = Path(src)
    dst = Path(dst) if dst else default_output_path(src)
    progress = progress or (lambda f, s: None)
    log = log or (lambda t: None)
    matcher = matcher or wordlists.build_matcher()
    model, device, _ = model_bundle or load_model(model_size, cpu_threads=FILE_CPU_THREADS)
    lang = resolve_language(model, language)
    log(f"Transcribing with {model_size} on {device.upper()} (language: {lang or 'auto-detect'})...")

    catches, ranges = [], []
    with tempfile.TemporaryDirectory(prefix="bleepline-") as tmp:
        tracks, duration = _decode_for_asr(src, Path(tmp), lambda f: progress(f * 0.2, "Reading audio"))
        for n, path in enumerate(tracks.values()):
            base = 0.2 + 0.6 * n / len(tracks)
            words = _transcribe(model, _load_track(path), lang, lambda end: progress(
                base + 0.6 / len(tracks) * min(1.0, end / max(duration, 1e-3)), "Finding banned words"), log)
            # Whole-track matching, so phrases split across Whisper segments are still found.
            for start_s, end_s, text in banned_spans(matcher, words, min_prob):
                catches.append((float(start_s), text))
                ranges.append((start_s - pad_before_ms / 1000, end_s + pad_after_ms / 1000))
    catches.sort()
    ranges = _merge(sorted(ranges))
    log(f"Found {len(catches)} banned word(s). Writing cleaned copy...")

    censor = (mute_mode, beep_hz, censor_volume, censor_sound)
    dst = _write_clean_copy(src, dst, ranges, censor, lambda f: progress(0.8 + 0.2 * f, "Writing file"))
    _write_report(dst, catches)
    progress(1.0, "Done")
    return dst, catches


def _transcribe(model, audio, lang, progress, log):
    """Word objects for a whole track. Batched decoding is ~2x faster (measured on 8 CPU threads); if it
    fails, e.g. a small GPU running out of memory, the track is redone one segment at a time."""
    try:
        segments, _ = BatchedInferencePipeline(model).transcribe(
            audio, language=lang, beam_size=5, word_timestamps=True, batch_size=BATCH_SIZE)
        return _collect(segments, progress)
    except Exception as exc:
        log(f"Batched transcription failed ({str(exc).splitlines()[0][:80]}), retrying one segment at a time...")
    segments, _ = model.transcribe(audio, language=lang, beam_size=5, word_timestamps=True, vad_filter=True,
                                   condition_on_previous_text=False)
    return _collect(segments, progress)


def _collect(segments, progress):
    words = []
    for seg in segments:
        words += seg.words or []
        progress(seg.end)
    return words


def _decode_for_asr(src: Path, tmp: Path, progress):
    """Every audio track decoded to 16 kHz mono 16-bit (as faster-whisper decodes) into a file in `tmp`, in one
    pass: held in memory, 3 hours of 6 tracks would need about 4 GB. Returns ({stream index: path}, duration_s)."""
    with av.open(str(src)) as inp, contextlib.ExitStack() as files:
        streams = [s for s in inp.streams if s.type == "audio"]
        if not streams:
            raise ValueError("This file has no audio track.")
        duration = float(inp.duration / av.time_base) if inp.duration else 0.0
        resamplers = {s.index: av.AudioResampler(format="s16", layout="mono", rate=ASR_RATE) for s in streams}
        paths = {s.index: tmp / f"track{s.index}.pcm" for s in streams}
        outs = {i: files.enter_context(open(p, "wb")) for i, p in paths.items()}
        for packet in inp.demux(*streams):
            for frame in packet.decode():
                for out in resamplers[packet.stream.index].resample(frame):
                    outs[packet.stream.index].write(out.to_ndarray().tobytes())
                if duration and frame.pts is not None:
                    progress(min(1.0, float(frame.pts * frame.time_base) / duration))
    if not duration:
        duration = max(p.stat().st_size for p in paths.values()) / 2 / ASR_RATE
    return paths, duration


def _load_track(path: Path) -> np.ndarray:
    audio = np.fromfile(path, np.int16).astype(np.float32)
    audio /= 32768.0  # in place: a 3-hour track is 700 MB as float32
    return audio if len(audio) else np.zeros(1, np.float32)


def _merge(ranges):
    merged = []
    for a, b in ranges:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((max(0.0, a), b))
    return merged


def _gain(times: np.ndarray, ranges) -> np.ndarray:
    gain = np.ones(len(times), np.float32)
    t0, t1 = times[0] - RAMP_SEC, times[-1] + RAMP_SEC
    for a, b in ranges:
        if b < t0 or a > t1:
            continue
        g = np.maximum(np.clip((a - times) / RAMP_SEC, 0, 1), np.clip((times - b) / RAMP_SEC, 0, 1))
        gain = np.minimum(gain, g.astype(np.float32))
    return gain


def _pick_audio_codec(stream) -> str:
    """Re-encode with the source codec when we can encode it, otherwise AAC."""
    try:
        av.codec.Codec(stream.codec_context.name, "w")
        return stream.codec_context.name
    except Exception:
        return "aac"


def _write_clean_copy(src: Path, dst: Path, ranges, censor, progress) -> Path:
    mute_mode, beep_hz, volume, sound = censor
    with av.open(str(src)) as probe:
        audio_codecs = {s.index: _pick_audio_codec(s) for s in probe.streams if s.type == "audio"}
    if "aac" in audio_codecs.values() and dst.suffix.lower() not in (".mp4", ".m4a", ".mkv", ".mov"):
        dst = dst.with_suffix(AUDIO_ONLY_FALLBACK)  # e.g. an .mp3 we can't re-encode as mp3

    with av.open(str(src)) as inp, av.open(str(dst), "w") as out:
        duration = float(inp.duration / av.time_base) if inp.duration else 0.0
        plan = {}
        for s in inp.streams:
            if s.type == "audio":
                o = out.add_stream(audio_codecs[s.index], rate=s.rate, layout=s.layout.name)
                if s.bit_rate:
                    o.bit_rate = s.bit_rate
                plan[s.index] = ("audio", o, av.AudioResampler(format="fltp", layout=s.layout.name, rate=s.rate))
            elif s.type == "video":
                plan[s.index] = ("copy", out.add_stream_from_template(s), None)
        voices = {s.index: CensorVoice(replacement_clip(mute_mode, s.rate, beep_hz, volume, sound))
                  for s in inp.streams if s.type == "audio"}

        for packet in inp.demux():
            entry = plan.get(packet.stream.index)
            if entry is None:
                continue
            kind, ostream, resampler = entry
            if kind == "copy":
                if packet.dts is None:
                    continue
                packet.stream = ostream
                out.mux(packet)
                continue
            for frame in packet.decode():
                if frame.pts is None:
                    continue
                start_s = float(frame.pts * frame.time_base)
                for f in resampler.resample(frame):
                    samples = f.to_ndarray()  # (channels, n) float planar
                    rate = f.sample_rate
                    times = start_s + np.arange(samples.shape[1]) / rate
                    gain = _gain(times, ranges)
                    samples = samples * gain
                    replacement = voices[packet.stream.index].render(gain)
                    if replacement is not None:
                        samples = samples + replacement
                    new = av.AudioFrame.from_ndarray(samples.astype(np.float32), format="fltp", layout=f.layout.name)
                    new.sample_rate = rate
                    new.pts = f.pts
                    new.time_base = f.time_base
                    for p in ostream.encode(new):
                        out.mux(p)
                    start_s = times[-1] + 1 / rate
                if duration:
                    progress(min(1.0, start_s / duration))
        for kind, ostream, _ in plan.values():
            if kind == "audio":
                for p in ostream.encode(None):
                    out.mux(p)
    return dst


def _write_report(dst: Path, catches) -> None:
    lines = [f"Bleepline: {len(catches)} banned word(s) muted in {dst.name}", ""]
    for t, word in catches:
        lines.append(f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}  {word}")
    dst.with_name(dst.stem + " - muted words.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
