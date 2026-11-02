"""The live engine in each placement this PC has (CPU; GPU + CPU and GPU with an NVIDIA GPU), on the replay clips
(test_replay.py): every window the live detector would check. Skipping word timings for windows without a banned
word must not change a single mute, and each placement must find banned words in every clip that has them (which
words exactly can differ a little: int8 on the CPU, float16 on the GPU; the differences are printed).

    python test_engine.py [model]      e.g.  python test_engine.py tiny.en   (default base.en)
"""
import sys
import time

import integration_test as it
import test_replay as tr
from app import detector as det
from app.fast_asr import FastTranscriber


def windows(audio):
    """The live windows over a clip, every 100 ms, resampled for the speech model."""
    win, step = int(det.WINDOW_SEC * tr.RATE), tr.RATE // 10
    return [det.resample_to_asr(audio[max(0, t - win):t], tr.RATE) for t in range(step, len(audio) + 1, step)]


def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "base.en"
    matcher = tr.test_matcher()
    clips = [windows(it.load_wav(w, tr.RATE)) for w in tr.make_clips()]
    found = {}
    for device in ("cpu", "split", "gpu"):
        model, got, note = det.load_model(model_name, device)
        if got != {"split": "split", "gpu": "cuda"}.get(device, "cpu"):
            assert got == "cpu" and device != "cpu"
            print(f"{device}: not on this PC ({note})")
            continue
        assert det.Detector(model, tr.RATE, early_mute=True).asr.needs_timings is None  # early muting needs all
        detector = det.Detector(model, tr.RATE, matcher=matcher)
        assert detector.min_step_ms == (det.CPU_STEP_MS if device == "cpu" else det.MIN_STEP_MS)
        full, lean = FastTranscriber(model, "en", 4.0), detector.asr
        hits, skipped, heard, took = set(), 0, 0, [0.0, 0.0]
        for i, clip in enumerate(clips):
            for w in clip:
                t0 = time.perf_counter()
                every = full.words(w)
                t1 = time.perf_counter()
                some = lean.words(w)
                took[0], took[1] = took[0] + t1 - t0, took[1] + time.perf_counter() - t1
                spans = det.banned_spans(matcher, every, det.DEFAULT_MIN_PROB)
                assert det.banned_spans(matcher, some, det.DEFAULT_MIN_PROB) == spans, (device, i, every, some)
                heard += bool(every)
                skipped += bool(every) and not some
                hits |= {(i, text) for _, _, text in spans}
        found[device] = hits
        n = sum(len(c) for c in clips)
        print(f"{device}: {len(hits)} banned words found; same mutes with timings skipped in {skipped} of {heard} "
              f"windows with words; {took[0] / n * 1000:.0f} -> {took[1] / n * 1000:.0f} ms per check")
    phrases = tr.PHRASES * len(tr.VOICES)  # in the clips' order
    for device, hits in found.items():
        missing, extra = found["cpu"] - hits, hits - found["cpu"]
        if missing or extra:
            print(f"  {device} vs cpu: (clip, word) missing {sorted(missing)}, extra {sorted(extra)}")
        silent = [i for i, text in enumerate(phrases) if matcher.find(text.split()) and i not in {c for c, _ in hits}]
        assert not silent, f"{device} found no banned word in clips {silent}"
    print("engine: OK")


if __name__ == "__main__":
    main()
