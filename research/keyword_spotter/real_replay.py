"""Whisper alone vs. Whisper + detector on held-out REAL recordings (People's Speech test recordings).

    python real_replay.py real.clips.json clips/ kws_a.npz [kws_b.npz ...]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kws_replay as kr  # noqa: E402  (sets up the repo path)

import numpy as np  # noqa: E402

import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app import wordlists  # noqa: E402
from make_real_dataset import load16  # noqa: E402

R = tr.RATE


def main():
    info = [c for c in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")) if c["test"]]
    clips = Path(sys.argv[2])
    matcher = wordlists.WordMatcher([w for k in wordlists.DEFAULT_BUILTIN for w in wordlists.builtin_words(k)]
                                    + ["crap*", "piss*"])
    model, _, _ = det.load_model("tiny.en", "cpu")
    asr = kr.KwsTranscriber(model, "en", 4.0)
    data = []
    for c in info:
        a16 = load16(clips / c.get("file", f"{c['row']}.flac"))
        audio = np.interp(np.arange(len(a16) * 3) / 3, np.arange(len(a16)), a16).astype(np.float32)  # 16 -> 48 kHz
        ref = [(w, s, e) for w, s, e in c["words"]]
        if any(matcher.match_word(w) for w, _, _ in ref):
            data.append((audio, ref))
    n_words = sum(matcher.match_word(w) is not None for _, ref in data for w, _, _ in ref)
    print(f"{len(data)} held-out real clips from {len({c['id'].split('_SLASH_')[0] for c in info})} recordings, "
          f"{n_words} swear words")
    for path in sys.argv[3:]:
        kws = kr.load_kws(path)
        recorded = [(audio, ref, kr.record(asr, matcher, kws, audio)[0]) for audio, ref in data]
        configs = [("Whisper only (today)", dict(thr=1.1, back=0.3, use_kws=False))]
        configs += [(f"+ {Path(path).stem} thr {thr}{' + veto' if veto else ''}",
                     dict(thr=thr, back=0.3, veto=veto, matcher=matcher)) for veto in (False, True) for thr in (0.8, 0.9)]
        for name, cfg in configs:
            results, n_ev, n_wrong = [], 0, 0
            for audio, ref, checks in recorded:
                mutes, ev, wrong = kr.mutes_for(checks, **cfg)
                results.append((audio, ref, mutes))
                n_ev, n_wrong = n_ev + ev, n_wrong + wrong
            tr.report(f"{name}: {n_ev} firings ({n_wrong} not confirmed)", results, matcher, [300, 400, 500, 600])


if __name__ == "__main__":
    main()
