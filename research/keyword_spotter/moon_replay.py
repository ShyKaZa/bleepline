"""Moonshine streaming (moonshine-voice, MIT) as the recognizer instead of Whisper, on the held-out real recordings of
spot_replay.py. Audio arrives in 10 ms blocks with an update every STEP; each update's measured time pushes a virtual
clock, so a word only counts as muted where its mute arrived before that audio was played. Mutes follow the same
rules as Whisper's (spot_replay.spot_mutes), alone and with the fast spotter on top.

Moonshine's word times are partly missing (an end before the start, or later words set to the line's start); those
are filled in from the neighbouring words, and the last word of a line still being spoken ends "now".

The package is installed with pip --target moonlib (no dependencies but platformdirs and google-crc32c), the models
with moonshine_voice.download.get_model_for_language(..., include_word_timestamps=True) into data/moonshine/.

    python moon_replay.py <clips json> <clips dir> <negatives json> <negatives dir> <whisper cache dir> \
        <moon cache dir> <tiny|small|medium> <transcription interval s> [spotter.npz ...]
"""
import json
import os
import pickle
import sys
import time
from pathlib import Path

ARGS = [Path(a).resolve() for a in sys.argv[1:]]  # before kws_replay switches to the repo folder
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "moonlib"))
import spot_replay as srp  # noqa: E402  (sets up the repo path)

import numpy as np  # noqa: E402
from moonshine_voice.moonshine_api import ModelArch  # noqa: E402
from moonshine_voice.transcriber import Transcriber  # noqa: E402

import spotter as sp  # noqa: E402
import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app import wordlists  # noqa: E402
from app.fast_asr import Word  # noqa: E402
from make_real_dataset import is_test, load16  # noqa: E402

R, SR = tr.RATE, sp.SR
BLOCK = SR // 100  # 10 ms, as the app's audio callback
STEP = float(os.environ.get("MOON_STEP", "0.05"))  # update every this much audio
DELAYS = [200, 300, 400, 500, 600]
MODELS = HERE / "data" / "moonshine" / "download.moonshine.ai" / "model"
ARCH = {"tiny": ModelArch.TINY_STREAMING, "small": ModelArch.SMALL_STREAMING, "medium": ModelArch.MEDIUM_STREAMING}


def load(model, interval):
    return Transcriber(str(MODELS / f"{model}-streaming-en" / "quantized_26_08_21"), ARCH[model],
                       options={"word_timestamps": "true", "identify_speakers": "false",
                                "transcription_interval": str(interval)})


def timed_words(line, now):
    """[(word, start_s, end_s)] for one transcript line, with missing times filled in from the neighbours."""
    ws = line.words or []
    unset = line.start_time + 1e-3
    starts = [w.start if i == 0 or w.start > unset else None for i, w in enumerate(ws)]
    ends = [w.end if w.end > (starts[i] if starts[i] is not None else unset) + 1e-3 else None
            for i, w in enumerate(ws)]
    line_end = line.start_time + line.duration if line.is_complete else now
    out = []
    for i, w in enumerate(ws):
        s = starts[i] if starts[i] is not None else (out[-1][2] if out else line.start_time)
        e = ends[i] if ends[i] is not None else next((x for x in starts[i + 1:] if x is not None), line_end)
        out.append((w.word, s, max(e, s + 0.05)))
    return out


def moon_checks(trans, matcher, a16, path: Path):
    """The live loop's checks in spot_replay's format, (t, took, spans, placed) in 48 kHz samples; plus CPU s."""
    if path.exists():
        return pickle.loads(path.read_bytes())
    stream = trans.create_stream(update_interval=1e9)  # no implicit updates: this loop decides when
    stream.start()
    clock, cpu, checks, every = 0.0, 0.0, [], round(STEP * SR / BLOCK)
    for i, k in enumerate(range(0, len(a16), BLOCK)):
        stream.add_audio(a16[k:k + BLOCK].tolist(), SR)
        if (i + 1) % every:
            continue
        arrival = min(k + BLOCK, len(a16)) / SR
        clock = max(clock, arrival)
        t0, c0 = time.perf_counter(), time.process_time()
        transcript = stream.update_transcription(0)
        took = time.perf_counter() - t0
        cpu += time.process_time() - c0
        clock += took
        words = [w for line in transcript.lines for w in timed_words(line, arrival)]
        spans = [(int(s * R), int(e * R), int(arrival * R))
                 for s, e, _ in det.banned_spans(matcher, [Word(w, s, e, 1.0) for w, s, e in words], 0.0)]
        checks.append((int(arrival * R), int(took * R), spans, [(int(s * R), int(e * R), w) for w, s, e in words]))
    stream.stop()
    stream.close()
    result = (checks, cpu)
    path.write_bytes(pickle.dumps(result))
    return result


def main():
    clips_json, clips, neg_json, neg_dir, wcache, mcache = ARGS[:6]
    model, interval, spotters = sys.argv[7], float(sys.argv[8]), ARGS[8:]
    mcache = mcache / f"{model}_{interval:g}_step{STEP:g}"
    mcache.mkdir(parents=True, exist_ok=True)
    matcher = wordlists.WordMatcher([w for k in wordlists.DEFAULT_BUILTIN for w in wordlists.builtin_words(k)]
                                    + ["crap*", "piss*"])
    swear = [c for c in json.loads(clips_json.read_text(encoding="utf-8"))
             if c["test"] and any(matcher.match_word(w) for w, _, _ in c["words"])]
    swear = swear[:int(os.environ.get("MOON_SWEAR", len(swear)))]
    everyday = [c for c in json.loads(neg_json.read_text(encoding="utf-8")) if is_test(c["id"])]
    everyday = everyday[:int(os.environ.get("SPOT_EVERYDAY", len(everyday)))]
    trans, data, cpu, seconds, t0 = load(model, interval), [], 0.0, 0.0, time.time()
    for kind, group, folder in (("swear", swear, clips), ("everyday", everyday, neg_dir)):
        for c in group:
            a16 = load16(folder / c["file"])
            audio = np.interp(np.arange(len(a16) * 3) / 3, np.arange(len(a16)), a16).astype(np.float32)
            ref = [(w, s, e) for w, s, e in c.get("words", [])]
            mchecks, c_cpu = moon_checks(trans, matcher, a16, mcache / (c["file"] + ".pkl"))
            wchecks = srp.whisper_checks(None, matcher, audio, wcache / (c["file"] + ".pkl"))
            cpu, seconds = cpu + c_cpu, seconds + len(a16) / SR
            data.append((kind, a16, audio, ref, mchecks, wchecks))
    minutes = sum(len(d[1]) for d in data if d[0] == "everyday") / SR / 60
    print(f"Moonshine {model} streaming, transcription interval {interval:g} s, update every {STEP:g} s: "
          f"{cpu / seconds * 1000:.0f} ms of CPU per second of audio (all threads); {len(swear)} swearing clips, "
          f"{minutes:.1f} min everyday talk; {time.time() - t0:.0f}s", flush=True)

    def show(name, layer2, cfg, timelines):
        results, everyday_mutes = [], []
        for d, timeline in zip(data, timelines):
            kind, a16, audio, ref = d[:4]
            mutes, _, _ = srp.spot_mutes(d[4] if layer2 == "moon" else d[5], timeline, len(audio), matcher=matcher,
                                         **cfg)
            (results if kind == "swear" else everyday_mutes).append((audio, ref, mutes))
        print(name)
        for dl in DELAYS:
            hits = n = 0
            fracs, cuts = [], []
            for audio, ref, mutes in results:
                h, k, f, c = tr.score(mutes, ref, matcher, len(audio), dl)
                hits, n, fracs, cuts = hits + h, n + k, fracs + f, cuts + c
            regions = [srp.muted_regions(m, len(a), dl) for a, _, m in everyday_mutes]
            print(f"  {dl:4d} ms: {hits:3d}/{n} fully muted, {np.mean(fracs) * 100:3.0f}% of swear audio | "
                  f"{len(cuts):3d} innocent words cut | everyday talk: "
                  f"{sum(r[0] for r in regions) / max(minutes, 1e-9):4.2f} false mutes/min", flush=True)

    none = [(np.zeros(0, int), np.zeros(0))] * len(data)
    show("Whisper tiny.en only (today)", "whisper", dict(use_spot=False), none)
    show(f"Moonshine {model} only", "moon", dict(use_spot=False), none)
    for path in spotters:
        spot = srp.load_spotter(path)
        timelines = [spot.timeline(d[1]) for d in data]
        for thr in (0.6, 0.8):
            for layer2 in ("whisper", "moon"):
                show(f"{path.stem} thr {thr} x2 + {'Whisper' if layer2 == 'whisper' else 'Moonshine ' + model}",
                     layer2, dict(thr=thr, persist=2), timelines)


if __name__ == "__main__":
    main()
