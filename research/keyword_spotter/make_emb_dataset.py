"""Like make_spot_dataset.py, but with Google speech embeddings (speech_embedding.py) instead of log-mel frames:
for every clip, the four interleaved embedding sequences (one every 80 ms each, offset by 20 ms).

    python make_emb_dataset.py <clips json> <clips dir> <negatives json> <negatives dir> <synth.pkl> <out.pkl>
"""
import json
import pickle
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "torchlib"))
sys.path.insert(0, str(HERE))
from make_spot_dataset import ARGS, matchers, split_of  # noqa: E402  (resolves the paths, sets up the repo path)

import numpy as np  # noqa: E402
import torch  # noqa: E402

import speech_embedding as se  # noqa: E402
from make_real_dataset import load16, recording  # noqa: E402


def main():
    clips_json, clips, neg_json, neg_dir, synth, out = ARGS
    positive, anything = matchers()
    torch.set_num_threads(8)
    emb = se.Embedding()

    def item(audio16, words, split, source, group):
        return {"phases": [(t.astype(np.float32), e.astype(np.float16)) for t, e in emb.phases(audio16)],
                "pos": [(s, e) for w, s, e in words if positive.match_word(w)],
                "mild": [(s, e) for w, s, e in words if anything.match_word(w) and not positive.match_word(w)],
                "split": split, "source": source, "group": group}

    items, t0 = [], time.time()
    for c in json.loads(clips_json.read_text(encoding="utf-8")):
        items.append(item(load16(clips / c["file"]), c["words"], split_of(c["id"]), "real", recording(c["id"])))
    print(f"{len(items)} swearing clips, {time.time() - t0:.0f}s", flush=True)
    for c in json.loads(neg_json.read_text(encoding="utf-8")):
        if any(anything.match_word(w) or positive.match_word(w) for w in c["text"].split()):
            continue  # as make_spot_dataset.py
        items.append(item(load16(neg_dir / c["file"]), [], split_of(c["id"]), "everyday", recording(c["id"])))
    print(f"{len(items)} with everyday clips, {time.time() - t0:.0f}s", flush=True)
    for k, s in enumerate(pickle.loads(synth.read_bytes())):
        items.append(item(s["audio"].astype(np.float32) / 32767, s["words"], "val" if s["val"] else "train",
                          "synthetic", f"synth{k}"))
    out.write_bytes(pickle.dumps(items))
    for source in ("real", "everyday", "synthetic"):
        for split in ("train", "val", "test"):
            sel = [x for x in items if x["source"] == source and x["split"] == split]
            if sel:
                print(f"{source:9s} {split:5s}: {len(sel):5d} clips, "
                      f"{sum(len(x['phases'][0][1]) for x in sel) * 0.08 / 60:6.1f} min, "
                      f"{sum(len(x['pos']) for x in sel)} swear words")
    print(f"saved {out}, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
