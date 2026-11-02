"""
How much of each swear word the live filter mutes at each delay, on 12 synthesized clips (6 phrases x 2 Windows
voices, 24 swear words, plus look-alike words like "shift" and "cocktail").

The live detector is replayed with its real timing but without waiting in real time: each check runs the real
speech engine on the real window, and its measured duration decides when the mute lands, so a word only counts
as muted where the mute arrived before that audio was played. Word positions come from base.en on the whole clip.

    python test_replay.py [model] [delays_ms...] [--early]      e.g.  python test_replay.py tiny.en 500 600 --early
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

import integration_test as it
from app import detector as det
from app import wordlists
from app.fast_asr import FastTranscriber

RATE = 48000
BLOCK = 480
CLIPS = Path(tempfile.gettempdir()) / "bleepline_replay_clips"
VOICES = ("Microsoft Zira Desktop", "Microsoft Hazel Desktop")
PHRASES = (
    it.PHRASE,
    "She said the shift starts at nine, so pass me the sheet and assess the class schedule before the cocktail party.",
    "Oh shit, he is behind us. Fuck, I am dead. That asshole camped the whole round, what a dick.",
    "The boss fight is bitter but we pushed through, so stop whining, you little bastard, and heal me.",
    "Holy crap, that was a sick play. Push left, push left. Motherfucker, he flanked again. Seriously, piss off.",
    "I need ammo, give me some shells. Shoot the tank, then hit the shack by the dock. Nice shot, buddy.",
)


def make_clips() -> list[Path]:
    CLIPS.mkdir(exist_ok=True)
    wavs = [CLIPS / f"p{i + 1}_v{v}.wav" for v in range(len(VOICES)) for i in range(len(PHRASES))]
    if all(w.exists() for w in wavs):
        return wavs
    script = "Add-Type -AssemblyName System.Speech;"
    for w in wavs:
        v, i = int(w.stem.split("_v")[1]), int(w.stem[1:].split("_")[0]) - 1
        script += (f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice('{VOICES[v]}');"
                   f"$s.SetOutputToWaveFile('{w}'); $s.Speak('{PHRASES[i]}'); $s.Dispose();")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    return wavs


def replay(asr, matcher, audio, early=False, pad_before_ms=200):
    """Every mute the live detector would make, like Detector._loop: [(applied_at, start, end, undone_at)]
    in samples; undone_at is None except for early mutes that were taken back. Also the check durations."""
    win_max = int(det.WINDOW_SEC * RATE) // BLOCK * BLOCK
    edge, pad_b, pad_a = RATE * det.EDGE_MS // 1000, RATE * pad_before_ms // 1000, RATE * 250 // 1000
    mutes, times, infer_ms, now = [], [], 0.0, [0]
    tentative: dict[int, list] = {}  # early mutes: id -> [applied, start, end, undone] per extension

    def on_tentative(start, end, tid):
        if tid is None:
            tid = len(tentative) + 1
            tentative[tid] = []
        elif tentative[tid][-1][3] is None:
            tentative[tid][-1][3] = now[0]  # replaced by the extended range
        tentative[tid].append([now[0], start, end, None])
        return tid

    def on_retract(tid):
        tentative[tid][-1][3] = now[0]

    detector = det.Detector(None, RATE, matcher=matcher, early_mute=True, on_tentative=on_tentative,
                            on_retract=on_retract) if early else None
    step, t = RATE * det.CPU_STEP_MS // 1000, BLOCK * 10
    while t <= len(audio):
        lo = max(0, t - win_max)
        window, took = audio[lo:t], 0
        if float(np.sqrt(np.mean(window * window))) >= det.SILENCE_RMS:
            t0 = time.perf_counter()
            words = asr.words(det.resample_to_asr(window, RATE))
            ms = (time.perf_counter() - t0) * 1000
            times.append(ms)
            infer_ms = ms if infer_ms == 0 else 0.8 * infer_ms + 0.2 * ms
            step = RATE * max(det.CPU_STEP_MS, int(infer_ms * 1.25)) // 1000
            took = int(ms * RATE / 1000)
            now[0] = t + took
            confirmed = []
            for s_s, e_s, _ in det.banned_spans(matcher, words, det.DEFAULT_MIN_PROB):
                ws, we = lo + int(s_s * RATE), lo + int(e_s * RATE)
                confirmed.append((ws, we))
                end = max(we + pad_a, t + pad_a) if we > t - edge else we + pad_a
                mutes.append((t + took, ws - pad_b, end, None))
            if detector:
                detector._early(words, lo, t, edge, confirmed)
        t += -(-max(step, took) // BLOCK) * BLOCK
    mutes += [tuple(m) for ranges in tentative.values() for m in ranges]
    return mutes, times


def score(mutes, ref, matcher, n, delay_ms):
    """(fully muted, swear words, muted fraction per swear word, look-alike words cut)."""
    delay = RATE * delay_ms // 1000
    gain = np.ones(n, np.float32)
    for applied, s, e, undone in mutes:
        s = max(s, applied - delay, 0)  # audio before this had already been played
        if undone is not None:
            e = min(e, undone - delay)  # an early mute that was taken back stops muting from then on
        gain[s:max(s, min(e, n))] = 0
    swears = [(s, e) for w, s, e in ref if matcher.match_word(w)]
    fracs, cuts = [], []
    for w, s, e in ref:
        a, b = int(s * RATE), int(e * RATE)
        frac = float(1 - gain[a:b].mean()) if b > a else 0.0
        if matcher.match_word(w):
            fracs.append(frac)
        elif frac > 0.5 and not any(a < (te + 0.3) * RATE and b > (ts - 0.3) * RATE for ts, te in swears):
            cuts.append(w.strip(" ,."))  # an innocent word not just next to a swear word (padding)
    return sum(f > 0.95 for f in fracs), len(fracs), fracs, cuts


def test_matcher():
    """The default lists plus "mild", so "crap" and "piss" count too."""
    return wordlists.build_matcher([*wordlists.DEFAULT_BUILTIN, "mild"], your_words=[])


def clips_with_reference():
    """[(clip audio at RATE with 0.5 s silence before and 1.5 s after, [(word, start_s, end_s)])]."""
    ref_model, _, _ = det.load_model("base.en", "cpu", cpu_threads=max(2, (os.cpu_count() or 4) // 2))
    out = []
    for wav in make_clips():
        speech = it.load_wav(wav, RATE)
        audio = np.concatenate([np.zeros(RATE // 2, np.float32), speech, np.zeros(RATE * 3 // 2, np.float32)])
        segs, _ = ref_model.transcribe(it.load_wav(wav, 16000), language="en", word_timestamps=True, beam_size=5)
        out.append((audio, [(w.word, w.start + 0.5, w.end + 0.5) for s in segs for w in s.words]))
    return out


def report(title, results, matcher, delays):
    """results: [(audio, ref, mutes)] -> one line per delay."""
    print(title)
    for d in delays:
        hits = n = 0
        fracs, cuts = [], []
        for audio, ref, mutes in results:
            h, k, f, c = score(mutes, ref, matcher, len(audio), d)
            hits, n, fracs, cuts = hits + h, n + k, fracs + f, cuts + c
        print(f"  {d:5d} ms delay: {hits:2d}/{n} fully muted, {np.mean(fracs) * 100:3.0f}% of swear-word audio muted, "
              f"{len(cuts)} other words cut" + (f" ({', '.join(sorted(set(cuts)))})" if cuts else ""))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    early = "--early" in sys.argv
    model_name = args[0] if args else "tiny.en"
    delays = [int(d) for d in args[1:]] or [400, 500, 600, 800, 1000]
    model, _, _ = det.load_model(model_name, "cpu")
    matcher = test_matcher()
    asr = FastTranscriber(model, "en", 4.0)
    results, all_times = [], []
    for audio, ref in clips_with_reference():
        mutes, times = replay(asr, matcher, audio, early)
        results.append((audio, ref, mutes))
        all_times += times
    report(f"{model_name}{' with early muting' if early else ''} on 2 CPU threads, {len(all_times)} checks, "
           f"{np.mean(all_times):.0f} ms each (p95 {np.percentile(all_times, 95):.0f} ms)", results, matcher, delays)


if __name__ == "__main__":
    main()
