"""Detector training/test windows from real People's Speech clips (CC-BY), like research/keyword_spotter/make_dataset.py.

Whole recordings go to either training or test (about 30% test), so the test only has voices never trained on.

    python make_real_dataset.py clips/ hits.json out.npz
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_BLOCKTIME", "0")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))  # make_dataset.py (needs piper in piperlib/, see README)
os.chdir(REPO)

import av  # noqa: E402
import numpy as np  # noqa: E402

import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app import wordlists  # noqa: E402
from app.fast_asr import FastTranscriber  # noqa: E402
from make_dataset import edge_features  # noqa: E402

SR = 16000


def load16(path):
    with av.open(str(path)) as inp:
        rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        return np.concatenate([o.to_ndarray().reshape(-1) for f in inp.decode(audio=0) for o in rs.resample(f)])


def recording(clip_id: str) -> str:
    return clip_id.split("_SLASH_")[0]


def is_test(clip_id: str) -> bool:
    return int(hashlib.md5(recording(clip_id).encode()).hexdigest(), 16) % 10 < 3


def main():
    clips, hits, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")), Path(sys.argv[3])
    aligner, _, _ = det.load_model("base.en", "gpu")
    tiny, _, _ = det.load_model("tiny.en", "cpu", cpu_threads=8)
    asr = FastTranscriber(tiny, "en", 4.0)
    positive = wordlists.WordMatcher([w for k in wordlists.DEFAULT_BUILTIN for w in wordlists.builtin_words(k)]
                                     + ["crap*", "piss*"])
    anything = tr.test_matcher()  # also mild words: around those, windows are left out rather than taught as innocent
    X, y, utt, test, clip_info, t0 = [], [], [], [], [], time.time()
    for n, hit in enumerate(hits):
        path = clips / hit.get("file", f"{hit['row']}.flac")
        if not hit.get("id") or not path.exists():
            continue
        audio = load16(path)
        segs, _ = aligner.transcribe(audio, language="en", word_timestamps=True, beam_size=5)
        words = [(w.word, w.start, w.end) for s in segs for w in s.words]
        pos = [(s, e) for w, s, e in words if positive.match_word(w)]
        mild = [(s, e) for w, s, e in words if anything.match_word(w) and not positive.match_word(w)]
        if not pos and not mild:
            continue  # the timing model didn't hear the swear word: labels would be wrong
        clip_info.append({"row": hit["row"], "id": hit["id"], "file": path.name, "test": is_test(hit["id"]),
                          "words": words})
        for t in np.arange(0.3, len(audio) / SR, 0.04):
            near = any(s - 0.5 <= t <= e + 0.5 for s, e in pos + mild)
            if not near and round(t / 0.04) % 3:
                continue  # every 120 ms away from swear words
            lo = max(0, int((t - det.WINDOW_SEC) * SR))
            window = audio[lo:int(t * SR)]
            if not asr.has_speech(window):
                continue
            if any(s + 0.06 <= t <= e + 0.15 for s, e in pos):
                label = 1
            elif any(s - 0.02 <= t < s + 0.06 for s, e in pos) or any(s - 0.1 <= t <= e + 0.2 for s, e in mild):
                label = -1
            else:
                label = 0
            X.append(edge_features(asr, window))
            y.append(label)
            utt.append(hit["row"])
            test.append(is_test(hit["id"]))
        if (n + 1) % 50 == 0:
            print(f"{n + 1}/{len(hits)} clips, {len(clip_info)} usable, {len(y)} windows, {time.time() - t0:.0f}s",
                  flush=True)
    np.savez_compressed(out, X=np.array(X), y=np.array(y, np.int8), utt=np.array(utt), val=np.array(test))
    out.with_suffix(".clips.json").write_text(json.dumps(clip_info), encoding="utf-8")
    yy = np.array(y)
    print(f"saved {out}: {len(y)} windows ({(yy == 1).sum()} positive), {len(clip_info)} clips "
          f"({sum(c['test'] for c in clip_info)} test)")


if __name__ == "__main__":
    main()
