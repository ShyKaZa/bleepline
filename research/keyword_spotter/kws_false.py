"""Which innocent words on the 12 test clips make the detector fire (score >= 0.9).

    python kws_false.py kws.npz
"""
import sys
from collections import defaultdict
import kws_replay as kr, test_replay as tr
from app import detector as det
kws = kr.load_kws(sys.argv[1])
model, _, _ = det.load_model("tiny.en", "cpu"); asr = kr.KwsTranscriber(model, "en", 4.0); matcher = tr.test_matcher()
hits = defaultdict(float)
for audio, ref in tr.clips_with_reference():
    checks, _ = kr.record(asr, matcher, kws, audio)
    spans = [(s, e) for w, s, e in ref if matcher.match_word(w)]
    for t, took, sp, p in checks:
        ts = t / kr.R
        if p < 0.9 or any(s - 0.1 <= ts <= e + 0.5 for s, e in spans):
            continue
        word = next((w.strip(" ,.") for w, s, e in ref if s <= ts <= e + 0.15), "(gap)")
        hits[word] = max(hits[word], p)
print("innocent words scoring >= 0.9:", ", ".join(f"{w} {p:.2f}" for w, p in sorted(hits.items(), key=lambda x: -x[1])))
