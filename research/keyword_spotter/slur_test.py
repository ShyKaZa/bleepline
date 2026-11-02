"""Does the live Whisper detector (tiny.en, the app's default) catch slurs as well as swears? Synthetic sentences
(piper, held-out speakers) with one slur-list word each, and the same templates with swear words as a control,
replayed like test_replay.py. Word timings come from base.en, matched to the known sentence word by word.

    python slur_test.py <out.txt>
"""
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_dataset as md  # noqa: E402  (sets up piper and the repo path)

import numpy as np  # noqa: E402
from piper import PiperVoice, SynthesisConfig  # noqa: E402

import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app import wordlists  # noqa: E402
from app.fast_asr import FastTranscriber  # noqa: E402

DELAYS = [400, 600, 800, 1000]
TOKEN = re.compile(r"[a-z']+")


def main():
    rng = random.Random(7)
    slurs = sorted({w.rstrip("*") for w in wordlists.builtin_words("slurs")} | {"niggas"})
    heavy = {"nigger", "nigga", "niggas", "faggot", "fag", "retard", "tranny"}
    jobs = [("slur", w) for w in slurs for _ in range(10 if w in heavy else 4)]
    jobs += [("swear", rng.choice([w for w in md.TARGETS if " " not in w])) for _ in range(100)]
    voice = PiperVoice.load(str(md.HERE / "voice" / "en_US-libritts_r-medium.onnx"))
    aligner, _, _ = det.load_model("base.en", "cpu", cpu_threads=4)
    model, _, _ = det.load_model("tiny.en", "cpu", cpu_threads=4)
    asr = FastTranscriber(model, "en", 4.0)
    matcher = tr.test_matcher()
    rows, dropped, heard, t0 = [], Counter(), defaultdict(Counter), time.time()
    for i, (kind, word) in enumerate(jobs):
        text = " ".join(p for p in [rng.choice(md.PRE), word, rng.choice(md.POST)] if p)
        if rng.random() < 0.3:
            text = rng.choice(md.FILLER) + " " + text
        text = text[0].upper() + text[1:] + rng.choice([".", "!", "?"])
        cfg = SynthesisConfig(speaker_id=rng.choice(md.VAL_SPEAKERS), length_scale=rng.uniform(0.8, 1.15),
                              volume=rng.uniform(0.4, 1.0), noise_scale=rng.uniform(0.3, 0.9),
                              noise_w_scale=rng.uniform(0.3, 1.0))
        raw = np.concatenate([c.audio_float_array for c in voice.synthesize(text, syn_config=cfg)])
        a16 = np.concatenate([np.zeros(md.SR // 2, np.float32), md.resample(raw, md.VOICE_SR, md.SR),
                              np.zeros(md.SR * 3 // 2, np.float32)])
        segs, _ = aligner.transcribe(a16, language="en", word_timestamps=True, beam_size=5)
        timed = [w for s in segs for w in s.words]
        said = TOKEN.findall(text.lower())
        if len(timed) != len(said):
            dropped[kind] += 1
            continue
        ref = [(w, t.start, t.end) for w, t in zip(said, timed)]
        audio = np.interp(np.arange(len(a16) * 3) / 3, np.arange(len(a16)), a16).astype(np.float32)
        mutes, _ = tr.replay(asr, matcher, audio)
        segs, _ = model.transcribe(a16, language="en", word_timestamps=True)
        whole = [w.word.strip(" ,.!?").lower() for s in segs for w in s.words]
        idx = said.index(word)
        guess = whole[idx] if len(whole) == len(said) else "|".join(whole[max(0, idx - 1):idx + 2])
        heard[word][guess] += 1
        rows.append((kind, word, audio, ref, mutes, any(matcher.match_word(w) for w in whole)))
        if (i + 1) % 25 == 0:
            print(f"{i + 1}/{len(jobs)}, {time.time() - t0:.0f}s", flush=True)

    lines = [f"dropped (base.en word count differs from the sentence): {dict(dropped)}"]
    for kind in ("slur", "swear"):
        sel = [r for r in rows if r[0] == kind]
        lines.append(f"\n== {kind}: {len(sel)} sentences; whole-clip tiny.en writes a listed word in "
                     f"{sum(r[5] for r in sel)}/{len(sel)}")
        for d in DELAYS:
            res = [tr.score(m, ref, matcher, len(a), d) for _, _, a, ref, m, _ in sel]
            fr = [f for r in res for f in r[2]]
            lines.append(f"  {d:4d} ms: {sum(r[0] for r in res)}/{sum(r[1] for r in res)} fully muted, "
                         f"{np.mean(fr) * 100:.0f}% of the word's audio, {sum(f > 0.5 for f in fr)} more than half")
    lines.append("\nper word: sentences, more than half muted at 1000 ms, what whole-clip tiny.en wrote")
    for word in sorted({r[1] for r in rows}, key=lambda w: (w not in {r[1] for r in rows if r[0] == "slur"}, w)):
        sel = [r for r in rows if r[1] == word]
        half = sum(f > 0.5 for _, _, a, ref, m, _ in sel for f in tr.score(m, ref, matcher, len(a), 1000)[2])
        lines.append(f"  {word:12s} {len(sel):3d} {half:3d}  {dict(heard[word].most_common(4))}")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
