"""Extra spellings for slurs, tested offline on recorded live checks.
    python alias_eval.py <slur records.pkl> [alias=slur ...]
Slur sentences: fully muted with and without the extra spellings (1.3 engine checks, slur_record.py).
Real speech (base.en, research cache, 87 swearing clips + 323 everyday clips, 76 min): where an extra spelling
would fire, false mutes per minute, innocent words cut. Plus the human transcripts of all 1077 everyday clips."""
import json, pickle, re, sys
from collections import Counter, defaultdict
from pathlib import Path
KWS = Path(__file__).resolve().parent
REC = Path(sys.argv[1]).resolve()
ALIASES = dict(a.split("=") for a in sys.argv[2:])
sys.argv = [sys.argv[0]]
sys.path.insert(0, str(KWS))
import spot_replay as sr  # sets up the repo path
import numpy as np
import test_replay as tr
from app import wordlists
from make_real_dataset import is_test, load16

R, DELAYS = tr.RATE, [400, 500, 600, 800]
NONE = (np.zeros(0, int), np.zeros(0))
norm = lambda w: wordlists.normalize(w.strip())  # noqa: E731


def with_aliases(checks, aliases):
    out, fired = [], []
    for t, took, spans, placed in checks:
        extra = [(s, e, t) for s, e, w in placed if norm(w) in aliases and not any(a < e and s < b for a, b, _ in spans)]
        fired += [w for s, e, w in placed if norm(w) in aliases]
        out.append((t, took, spans + extra, placed))
    return out, fired


test_matcher = tr.test_matcher()
data = pickle.loads(REC.read_bytes())
rows = data["rows"]
print(f"slur sentences recorded: {sum(r['kind'] == 'slur' for r in rows)} slur, {sum(r['kind'] == 'swear' for r in rows)} swear "
      f"(dropped {data['dropped']})")

# What the live checks wrote where each slur was said (the last check that covers the word)
heard = defaultdict(Counter)
for r in rows:
    if r["kind"] != "slur":
        continue
    i = [w for w, _, _ in r["ref"]].index(r["word"])
    _, s, e = r["ref"][i]
    s, e = int(s * R), int(e * R)
    got = Counter()
    for t, took, spans, placed in r["checks"]:
        if t >= e + R // 4:
            for ps, pe, w in placed:
                if ps < e and s < pe:
                    got[norm(w)] += 1
    heard[r["word"]][got.most_common(1)[0][0] if got else "(nothing)"] += 1

def score(rows, aliases):
    res = {}
    for d in DELAYS:
        hits = n = 0
        cuts = []
        for r in rows:
            checks, _ = with_aliases(r["checks"], aliases)
            mutes, _, _ = sr.spot_mutes(checks, NONE, r["n"], use_spot=False, matcher=test_matcher)
            h, k, f, c = tr.score(mutes, r["ref"], test_matcher, r["n"], d)
            hits, n, cuts = hits + h, n + k, cuts + c
        res[d] = (hits, n, len(cuts))
    return res

slur_rows = [r for r in rows if r["kind"] == "slur"]
base, more = score(slur_rows, {}), score(slur_rows, ALIASES)
print("\nslur sentences, fully muted (innocent words cut):")
for d in DELAYS:
    print(f"  {d} ms: {base[d][0]}/{base[d][1]} ({base[d][2]})  ->  with the extra spellings {more[d][0]}/{more[d][1]} ({more[d][2]})")
per = defaultdict(lambda: [0, 0, 0])
for r in slur_rows:
    for k, al in ((1, {}), (2, ALIASES)):
        checks, _ = with_aliases(r["checks"], al)
        mutes, _, _ = sr.spot_mutes(checks, NONE, r["n"], use_spot=False, matcher=test_matcher)
        per[r["word"]][k] += tr.score(mutes, r["ref"], test_matcher, r["n"], 600)[0]
    per[r["word"]][0] += 1
print("\nper slur: sentences, fully muted at 600 ms without / with, what the last checks wrote there")
for w in sorted(per):
    print(f"  {w:12s} {per[w][0]:3d} {per[w][1]:3d} {per[w][2]:3d}   {dict(heard[w].most_common(5))}")

# Real speech: base.en's live checks (GPU cache, every word kept)
cache = KWS / "data/real/whisper_cache_gpu_base.en"
matcher = wordlists.WordMatcher([w for k in wordlists.DEFAULT_BUILTIN for w in wordlists.builtin_words(k)] + ["crap*", "piss*"])
swear = [c for c in json.loads((KWS / "data/real/real_all.clips.json").read_text(encoding="utf-8"))
         if c["test"] and any(matcher.match_word(w) for w, _, _ in c["words"])]
everyday = [c for c in json.loads((KWS / "data/real/negatives.json").read_text(encoding="utf-8")) if is_test(c["id"])]
fires, ctx = Counter(), []
for kind, group, folder in (("swear", swear, KWS / "data/real/clips_all"), ("everyday", everyday, KWS / "data/real/negatives")):
    stats = {False: [0, 0.0, 0], True: [0, 0.0, 0]}
    for c in group:
        checks = pickle.loads((cache / (c["file"] + ".pkl")).read_bytes())
        n = len(load16(folder / c["file"])) * 3
        for use in (False, True):
            ch, fired = with_aliases(checks, ALIASES if use else {})
            mutes, _, _ = sr.spot_mutes(ch, NONE, n, use_spot=False, matcher=matcher)
            if kind == "everyday":
                k, secs = sr.muted_regions(mutes, n, 500)
                stats[use][0] += k
                stats[use][1] += n / R / 60
            else:
                ref = [(w, s, e) for w, s, e in c["words"]]
                stats[use][2] += len(tr.score(mutes, ref, matcher, n, 500)[3])
            if use and fired:
                fires.update(norm(w) for w in set(fired))
                for t, took, spans, placed in checks:
                    if any(norm(w) in ALIASES for _, _, w in placed):
                        ctx.append(f"{kind}: " + " ".join(w for _, _, w in placed).strip()[:100])
                        break
    if kind == "everyday":
        print(f"\neveryday talk ({stats[False][1]:.1f} min) at 500 ms: {stats[False][0] / stats[False][1]:.2f} -> "
              f"{stats[True][0] / stats[True][1]:.2f} false mutes/min")
    else:
        print(f"\nreal swearing clips at 500 ms: innocent words cut {stats[False][2]} -> {stats[True][2]}")
print("clips where an extra spelling fires:", dict(fires) or "none")
for x in ctx[:15]:
    print("   ", x)
texts = [c["text"].lower() for c in json.loads((KWS / "data/real/negatives.json").read_text(encoding="utf-8"))]
human = {a: sum(len(re.findall(rf"\b{re.escape(a)}\b", t)) for t in texts) for a in ALIASES}
print("in the human transcripts of all 1077 everyday clips:", human)
