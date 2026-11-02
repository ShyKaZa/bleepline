"""Detector scores on the 12 test clips: highest score during each swear word vs. on other speech.

    python kws_probs.py kws.npz [more_kws.npz ...]
"""
import sys
from pathlib import Path
import numpy as np
import kws_replay as kr
import test_replay as tr
from app import detector as det
for path in sys.argv[1:]:
    kws = kr.load_kws(path)
    model, _, _ = det.load_model("tiny.en", "cpu"); asr = kr.KwsTranscriber(model, "en", 4.0); matcher = tr.test_matcher()
    swear_max, other = [], []
    for audio, ref in tr.clips_with_reference():
        checks, _ = kr.record(asr, matcher, kws, audio)
        spans = [(s, e) for w, s, e in ref if matcher.match_word(w)]
        for s, e in spans:
            ps = [p for t, took, sp, p in checks if s * kr.R <= t <= (e + 0.3) * kr.R]
            swear_max.append(max(ps) if ps else 0)
        other += [p for t, took, sp, p in checks if not any((s - 0.1) * kr.R <= t <= (e + 0.5) * kr.R for s, e in spans)]
    other = np.array(other)
    print(Path(path).name, "| swear words, highest score while spoken:", " ".join(f"{p:.2f}" for p in sorted(swear_max)))
    print("   other speech checks: 90th pct %.2f, 99th pct %.2f, max %.2f (n=%d)" % (np.percentile(other, 90), np.percentile(other, 99), other.max(), len(other)))
