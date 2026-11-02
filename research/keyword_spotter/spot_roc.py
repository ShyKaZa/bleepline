"""Threshold-free comparison: at the threshold where everyday talk (held-out) triggers N times a minute, how many held-out
real swear words does the spotter catch at all, and early enough for 200 ms (first firing <= 180 ms into the word)?

    python spot_roc.py <spotter name in data/> ...
"""
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

import spotter as sp  # noqa: E402

data = pickle.loads((HERE / "data/spot_ds.pkl").read_bytes())
swear = [x for x in data if x["split"] == "test" and x["source"] == "real" and x["pos"]]
everyday = [x for x in data if x["split"] == "test" and x["source"] == "everyday"]
minutes = sum(len(x["feats"]) for x in everyday) / 6000
THRS = np.linspace(0.05, 0.995, 190)
RATES = (0.25, 0.5, 1.0)

for name in sys.argv[1:]:
    spot = sp.Spotter(HERE / f"data/{name}.npz")
    fires = np.zeros(len(THRS))
    for x in everyday:  # separate firings: two frames in a row at or above the threshold, counted once per stretch
        p = spot.probs(x["feats"].astype(np.float32))
        on = (p[1:, None] >= THRS) & (p[:-1, None] >= THRS)
        fires += (on[1:] & ~on[:-1]).sum(0) + on[0]
    per_min = fires / minutes
    peak, early = [], []
    for x in swear:
        p = spot.probs(x["feats"].astype(np.float32))
        t = sp.frame_end(np.arange(len(p)))
        for s, e in x["pos"]:
            w = (t >= s) & (t <= e + 0.3)
            peak.append(p[w].max() if w.any() else 0.0)
            w2 = (t >= s) & (t <= s + 0.18)
            two = np.minimum(p[1:], p[:-1])[w2[1:]]  # two frames in a row
            early.append(two.max() if len(two) else 0.0)
    peak, early = np.array(peak), np.array(early)
    cells = []
    for r in RATES:
        i = np.flatnonzero(per_min <= r)
        thr = THRS[i[0]] if len(i) else 1.0
        cells.append(f"{r:.2f}/min: thr {thr:.2f} caught {np.mean(peak >= thr) * 100:3.0f}% early {np.mean(early >= thr) * 100:3.0f}%")
    print(f"{name:12s} " + " | ".join(cells), flush=True)
