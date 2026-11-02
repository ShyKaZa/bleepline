"""Where does the spotter fail on held-out swear words: never fires, fires late, or stops early?

    python spot_diag.py [spotter name in data/, default spot_w64]
"""
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

import spotter as sp  # noqa: E402

data = pickle.loads((HERE / "data/spot_ds.pkl").read_bytes())
spot = sp.Spotter(HERE / f"data/{sys.argv[1] if len(sys.argv) > 1 else 'spot_w64'}.npz")

for split, source in (("val", "synthetic"), ("val", "real"), ("test", "real")):
    sel = [x for x in data if x["split"] == split and x["source"] == source and x["pos"]]
    rows = []
    for x in sel:
        p = spot.probs(x["feats"].astype(np.float32))
        t = sp.frame_end(np.arange(len(p)))
        for s, e in x["pos"]:
            w = (t >= s) & (t <= e + 0.3)
            pk = float(p[w].max()) if w.any() else 0.0
            first = {}
            for thr in (0.5, 0.8):
                hit = np.flatnonzero(w & (p >= thr))
                first[thr] = (t[hit[0]] - s) if len(hit) else None
            inside = (t >= s + 0.1) & (t <= e)
            cover = float((p[inside] >= 0.5).mean()) if inside.any() else 0.0
            rows.append((e - s, pk, first[0.5], first[0.8], cover))
    if not rows:
        continue
    dur, pk, f5, f8, cov = zip(*rows)
    pk = np.array(pk)
    lat5 = np.array([f for f in f5 if f is not None])
    print(f"{source} {split}: {len(rows)} swear words, median length {np.median(dur) * 1000:.0f} ms")
    print(f"  peak prob: <0.3 {np.mean(pk < 0.3) * 100:.0f}%, 0.3-0.5 {np.mean((pk >= 0.3) & (pk < 0.5)) * 100:.0f}%, "
          f"0.5-0.8 {np.mean((pk >= 0.5) & (pk < 0.8)) * 100:.0f}%, >=0.8 {np.mean(pk >= 0.8) * 100:.0f}%")
    if len(lat5):
        print(f"  first >=0.5 after word start: median {np.median(lat5) * 1000:.0f} ms, "
              f"<=150 ms {np.mean(lat5 <= 0.15) * 100:.0f}% of those that fire, <=250 ms {np.mean(lat5 <= 0.25) * 100:.0f}%")
    print(f"  word frames (after 100 ms) with prob >=0.5: mean {np.mean(cov) * 100:.0f}%")

for split in ("val", "test"):
    sel = [x for x in data if x["split"] == split and x["source"] == "everyday"]
    frames = sum(len(x["feats"]) for x in sel)
    over = {thr: 0 for thr in (0.5, 0.8)}
    for x in sel:
        p = spot.probs(x["feats"].astype(np.float32))
        for thr in over:
            over[thr] += int((p >= thr).sum())
    print(f"everyday {split}: {frames / 6000:.1f} min, frames >=0.5: {over[0.5] / frames * 100:.2f}%, "
          f">=0.8: {over[0.8] / frames * 100:.2f}%")
