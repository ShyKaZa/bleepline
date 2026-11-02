"""Log-mel features and word spans for the fast spotter, from all three sources:

- real People's Speech clips with swearing (real_all.clips.json from make_real_dataset.py: base.en word timings),
- real everyday clips without swearing (fetch_negatives.py),
- synthetic piper sentences (make_synth_audio.py).

Whole recordings go to training, validation (10%) or test (30%, the same split as make_real_dataset.py). Labels
are made at training time from the spans, so label rules can change without rebuilding.

    python make_spot_dataset.py <clips json> <clips dir> <negatives json> <negatives dir> <synth.pkl> <out.pkl>
"""
import hashlib
import pickle
import sys
import time
from pathlib import Path

ARGS = [Path(a).resolve() for a in sys.argv[1:7]]  # before make_real_dataset switches to the repo folder
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_real_dataset import is_test, load16, recording  # noqa: E402  (sets up the repo path)

import json  # noqa: E402

import numpy as np  # noqa: E402

import spotter as sp  # noqa: E402
import test_replay as tr  # noqa: E402
from app import wordlists  # noqa: E402


def matchers():
    positive = wordlists.WordMatcher([w for k in wordlists.DEFAULT_BUILTIN for w in wordlists.builtin_words(k)]
                                     + ["crap*", "piss*"])
    return positive, tr.test_matcher()


def split_of(clip_id):
    if is_test(clip_id):
        return "test"
    return "val" if int(hashlib.md5(recording(clip_id).encode()).hexdigest(), 16) % 10 == 9 else "train"


def item(audio16, words, positive, anything, split, source, group):
    pos = [(s, e) for w, s, e in words if positive.match_word(w)]
    mild = [(s, e) for w, s, e in words if anything.match_word(w) and not positive.match_word(w)]
    return {"feats": sp.features(audio16).astype(np.float16), "pos": pos, "mild": mild, "split": split,
            "source": source, "group": group}


def main():
    clips_json, clips, neg_json, neg_dir, synth, out = ARGS
    positive, anything = matchers()
    items, t0 = [], time.time()
    for c in json.loads(clips_json.read_text(encoding="utf-8")):
        items.append(item(load16(clips / c["file"]), c["words"], positive, anything, split_of(c["id"]), "real",
                          recording(c["id"])))
    print(f"{len(items)} swearing clips, {time.time() - t0:.0f}s", flush=True)
    skipped = 0
    for c in json.loads(neg_json.read_text(encoding="utf-8")):
        if any(anything.match_word(w) or positive.match_word(w) for w in c["text"].split()):
            skipped += 1  # a listed word the swear scan didn't look for (slurs, sexual words)
            continue
        items.append(item(load16(neg_dir / c["file"]), [], positive, anything, split_of(c["id"]), "everyday",
                          recording(c["id"])))
    print(f"{len(items)} with everyday clips ({skipped} skipped), {time.time() - t0:.0f}s", flush=True)
    for k, s in enumerate(pickle.loads(synth.read_bytes())):
        items.append(item(s["audio"].astype(np.float32) / 32767, s["words"], positive, anything,
                          "val" if s["val"] else "train", "synthetic", f"synth{k}"))
    out.write_bytes(pickle.dumps(items))
    for source in ("real", "everyday", "synthetic"):
        for split in ("train", "val", "test"):
            sel = [x for x in items if x["source"] == source and x["split"] == split]
            if sel:
                print(f"{source:9s} {split:5s}: {len(sel):5d} clips, {sum(len(x['feats']) for x in sel) / 6000:6.1f} min, "
                      f"{sum(len(x['pos']) for x in sel)} swear words")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
