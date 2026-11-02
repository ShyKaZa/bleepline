"""What live filtering costs on the GPU, in real time: the live loop over held-out speech (swearing clips, then
everyday talk), each check waiting for its audio as in the app. Run burn_gpu.py next to it to see what a game
on the same GPU would lose: its products/s while this runs against its rate alone. Dev only.

    python paced_cost.py <model> <seconds>    (SPOT_ASR_DEVICE: gpu, cpu or split; SPOT_ASR_* knobs as in lean_asr.py;
                                               SPOT_STEP_MS: check interval)
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kws_replay  # noqa: E402,F401  (sets up the repo path)

import numpy as np  # noqa: E402

import lean_asr  # noqa: E402
import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app import wordlists  # noqa: E402
from make_real_dataset import is_test, load16  # noqa: E402

HERE = Path(__file__).resolve().parent
R = tr.RATE
STEP_MS = int(os.environ.get("SPOT_STEP_MS", det.MIN_STEP_MS))


def main():
    model_size, seconds = sys.argv[1], float(sys.argv[2])
    matcher = wordlists.WordMatcher([w for k in wordlists.DEFAULT_BUILTIN for w in wordlists.builtin_words(k)]
                                    + ["crap*", "piss*"])
    d = HERE / "data" / "real"
    swear = [c for c in json.loads((d / "real_all.clips.json").read_text(encoding="utf-8")) if c["test"]
             and any(matcher.match_word(w) for w, _, _ in c["words"])]
    everyday = [c for c in json.loads((d / "negatives.json").read_text(encoding="utf-8")) if is_test(c["id"])]
    parts, total = [], 0
    for folder, c in [("clips_all", c) for c in swear] + [("negatives", c) for c in everyday]:
        parts.append(load16(d / folder / c["file"]))
        total += len(parts[-1])
        if total >= seconds * 16000:
            break
    a16 = np.concatenate(parts)[: int(seconds * 16000)]
    audio = np.interp(np.arange(len(a16) * 3) / 3, np.arange(len(a16)), a16).astype(np.float32)
    where = os.environ.get("SPOT_ASR_DEVICE", "gpu")
    asr = lean_asr.load(model_size, matcher, where)

    win_max = int(det.WINDOW_SEC * R)
    step, t, infer_ms, took = R * STEP_MS // 1000, R // 10, 0.0, []
    w0, c0 = time.perf_counter(), time.process_time()
    print(f"start {time.strftime('%H:%M:%S')}", flush=True)
    while t <= len(audio):
        wait = w0 + t / R - time.perf_counter()  # the audio up to t has only just arrived
        if wait > 0:
            time.sleep(wait)
        window = audio[max(0, t - win_max):t]
        ran = 0.0
        if float(np.sqrt(np.mean(window * window))) >= det.SILENCE_RMS:
            t0 = time.perf_counter()
            asr.words(det.resample_to_asr(window, R))
            ran = time.perf_counter() - t0
            took.append(ran * 1000)
            infer_ms = took[-1] if infer_ms == 0 else 0.8 * infer_ms + 0.2 * took[-1]
            step = R * max(STEP_MS, int(infer_ms * 1.25)) // 1000
        t += max(step, int(ran * R))
    wall, cpu = time.perf_counter() - w0, time.process_time() - c0
    took = np.array(took)
    p = asr.parts / max(len(took), 1) * 1000
    print(f"end {time.strftime('%H:%M:%S')}")
    print(f"{model_size}, {lean_asr.describe(where)}, checks every {STEP_MS} ms: {len(took)} checks in {wall:.0f} s "
          f"({len(took) / wall:.1f}/s); check {np.median(took):.0f} ms (95% within {np.percentile(took, 95):.0f} ms), "
          f"busy {took.sum() / 1000 / wall * 100:.0f}% of the time; process CPU {cpu / wall * 100:.0f}% of one core; "
          f"per check: speech check + spectrogram {p[0]:.1f}, encoder {p[1]:.1f}, decoder {p[2]:.1f}, "
          f"word timings {p[3]:.1f} ms")


if __name__ == "__main__":
    main()
