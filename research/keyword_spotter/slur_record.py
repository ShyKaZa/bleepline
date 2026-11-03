"""Fresh slur/swear sentences (seed from argv), live checks recorded as the 1.3 engine runs them (base.en split, 50 ms),
every check's words kept, for offline tests of extra spellings.   python slur_record.py <out.pkl> <seed>"""
import pickle, random, re, sys, time
from collections import Counter
from pathlib import Path
KWS = Path(__file__).resolve().parent
OUT, SEED = Path(sys.argv[1]).resolve(), int(sys.argv[2])
sys.path.insert(0, str(KWS))
import kws_replay as kr  # sets up the repo path
import make_dataset as md
import numpy as np
from piper import PiperVoice, SynthesisConfig
import lean_asr
import test_replay as tr
from app import detector as det, wordlists

TOKEN = re.compile(r"[a-z']+")
rng = random.Random(SEED)
slurs = sorted({w.rstrip("*") for w in wordlists.builtin_words("slurs")} | {"niggas"})
heavy = {"nigger", "nigga", "niggas", "faggot", "fag", "retard", "tranny"}
jobs = [("slur", w) for w in slurs for _ in range(12 if w in heavy else 6)]
jobs += [("swear", rng.choice([w for w in md.TARGETS if " " not in w])) for _ in range(100)]
voice = PiperVoice.load(str(md.HERE / "voice" / "en_US-libritts_r-medium.onnx"))
aligner = det.load_model("base.en", "cpu", cpu_threads=4)[0]
matcher = tr.test_matcher()
asr = lean_asr.load("base.en", matcher, "split")  # LEAN off: every check's words are kept
rows, dropped, t0 = [], Counter(), time.time()
for i, (kind, word) in enumerate(jobs):
    text = " ".join(p for p in [rng.choice(md.PRE), word, rng.choice(md.POST)] if p)
    if rng.random() < 0.3:
        text = rng.choice(md.FILLER) + " " + text
    text = text[0].upper() + text[1:] + rng.choice([".", "!", "?"])
    cfg = SynthesisConfig(speaker_id=rng.choice(md.VAL_SPEAKERS), length_scale=rng.uniform(0.8, 1.15),
                          volume=rng.uniform(0.4, 1.0), noise_scale=rng.uniform(0.3, 0.9), noise_w_scale=rng.uniform(0.3, 1.0))
    raw = np.concatenate([c.audio_float_array for c in voice.synthesize(text, syn_config=cfg)])
    a16 = np.concatenate([np.zeros(md.SR // 2, np.float32), md.resample(raw, md.VOICE_SR, md.SR), np.zeros(md.SR * 3 // 2, np.float32)])
    segs, _ = aligner.transcribe(a16, language="en", word_timestamps=True, beam_size=5)
    timed = [w for s in segs for w in s.words]
    said = TOKEN.findall(text.lower())
    if len(timed) != len(said):
        dropped[kind] += 1
        continue
    ref = [(w, t.start, t.end) for w, t in zip(said, timed)]
    audio = np.interp(np.arange(len(a16) * 3) / 3, np.arange(len(a16)), a16).astype(np.float32)
    checks, _ = kr.record(asr, matcher, lambda edge: 0.0, audio)
    rows.append(dict(kind=kind, word=word, text=text, ref=ref, n=len(audio),
                     checks=[(t, took, spans, placed) for t, took, spans, _, placed in checks]))
    if (i + 1) % 25 == 0:
        print(f"{i + 1}/{len(jobs)}, {time.time() - t0:.0f}s", flush=True)
OUT.write_bytes(pickle.dumps(dict(rows=rows, dropped=dict(dropped))))
print("saved", len(rows), "dropped", dict(dropped))
