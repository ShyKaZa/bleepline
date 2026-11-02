"""Synthetic training data for a swear-word onset detector on top of the live engine's Whisper encoder.

Each sentence is spoken by a random LibriTTS-R speaker (piper), timed with base.en word timestamps, then cut into
the same 1.2 s windows the live engine checks. Features: tiny.en encoder output at the window's edge, which is all
the live engine has at that moment. Label per window: a banned word has been under way for >= 60 ms (or just ended).

    python make_dataset.py <out.npz> <n_train> <n_val>
"""
import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_BLOCKTIME", "0")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE / "piperlib"))
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import ctranslate2  # noqa: E402
import numpy as np  # noqa: E402
from piper import PiperVoice, SynthesisConfig  # noqa: E402

import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app.fast_asr import PAD_STEP, FastTranscriber  # noqa: E402

SR = 16000
VOICE_SR = 22050
TRAIN_SPEAKERS, VAL_SPEAKERS = range(0, 700), range(700, 904)

TARGETS = ["fuck", "fucking", "fucker", "motherfucker", "shit", "bullshit", "shitty", "bitch", "bitches",
           "asshole", "dick", "bastard", "cunt", "crap", "piss", "pissed", "fucked", "dickhead", "son of a bitch"]
LOOKALIKES = ["fun", "funny", "fudge", "folk", "fox", "fuss", "full", "foot", "fact", "shift", "ship", "sheet",
              "shut", "shot", "shoot", "shell", "shield", "bit", "bitter", "beach", "pitch", "witch", "bastion",
              "ask", "assess", "class", "pass", "glass", "assassin", "dig", "deck", "duck", "dock", "pick", "sick",
              "trap", "crab", "cap", "piece", "kiss", "miss", "cut", "count", "country", "hunt", "cocktail",
              "Dickens", "pistol", "fortune", "fuel", "should", "sugar", "bucket", "pocket", "fork", "fire"]
PRE = ["oh", "hey man", "dude", "honestly", "bro", "come on", "that is such", "you are a", "look at this",
       "seriously", "wow", "okay so", "I swear", "listen", "no way", "behind you", "push left", "nice shot",
       "he is camping", "we lost again", "watch out", "I need ammo", "heal me", "what a", "that was", "stop being a",
       "this game is", "my team is", "are you kidding me", "why would you", "he just", "", "", ""]
POST = ["right now", "again", "move", "dude", "honestly", "I am dead", "let us go", "he flanked us",
        "on the left", "in the corner", "what a play", "just push", "stop it", "I cannot believe it",
        "they are everywhere", "reload", "behind the wall", "get over here", "that is not fair", "", "", ""]
FILLER = ["the round starts in ten seconds", "grab the armor on the roof", "we need to rotate to the other site",
          "can someone drop me a weapon", "I am going to flank from the right", "the enemy team is pushing mid",
          "nice work everyone", "let us try again next round", "my internet is lagging so bad",
          "check the corner by the stairs", "wait for the cooldown", "throw the grenade now"]


def sentence(rng, positive):
    if positive:
        parts = [rng.choice(PRE), rng.choice(TARGETS), rng.choice(POST)]
        if rng.random() < 0.25:
            parts += [rng.choice(PRE), rng.choice(TARGETS)]
    else:
        parts = [rng.choice(PRE), rng.choice(LOOKALIKES), rng.choice(POST)]
        if rng.random() < 0.5:
            parts += [rng.choice(FILLER)]
        if rng.random() < 0.5:
            parts.insert(0, rng.choice(LOOKALIKES))
    if rng.random() < 0.3:
        parts.insert(0, rng.choice(FILLER))
    text = " ".join(p for p in parts if p).strip()
    return text[0].upper() + text[1:] + rng.choice([".", "!", "?", ","])


AUGMENT = os.environ.get("KWS_AUGMENT") == "1"


def colour(x, rng):
    """Different microphones and rooms: a random tone tilt (short FIR) and, sometimes, a bit of room echo."""
    taps = np.array([1.0, rng.uniform(-0.6, 0.6), rng.uniform(-0.3, 0.3)], np.float32)
    y = np.convolve(x, taps)[: len(x)]
    if rng.random() < 0.5:
        n = int(SR * rng.uniform(0.08, 0.35))
        ir = np.random.default_rng(rng.randrange(1 << 30)).normal(size=n) * np.exp(-np.arange(n) / (n / 5))
        ir[0] = 1.0 / rng.uniform(0.15, 0.6)
        y = np.convolve(y, ir.astype(np.float32))[: len(x)]
    return (y / (np.abs(y).max() + 1e-6) * np.abs(x).max()).astype(np.float32)


def resample(x, src, dst):
    return np.interp(np.linspace(0, len(x) - 1, int(len(x) * dst / src)), np.arange(len(x)), x).astype(np.float32)


def edge_features(asr, audio16):
    """tiny.en encoder frames at the window edge, exactly as FastTranscriber.words computes the encoder input."""
    frames = -(-max(len(audio16), asr.context) // PAD_STEP) * PAD_STEP // 160
    real = asr.wm.feature_extractor(audio16)[:, :frames]
    feats = np.full((1, real.shape[0], frames), real.max() - 2.0, dtype=np.float32)
    feats[0, :, : real.shape[1]] = real
    enc = np.array(asr.wm.model.encode(ctranslate2.StorageView.from_array(feats), to_cpu=True))[0]  # (frames/2, 384)
    last = min(len(audio16) // 320, enc.shape[0]) - 1
    groups = [enc[max(0, last - 7):last - 3], enc[last - 3:last + 1], enc[last + 1:last + 3]]
    return np.concatenate([g.mean(0) for g in groups]).astype(np.float16)


def main():
    out, n_train, n_val = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    rng = random.Random(int(os.environ.get("KWS_SEED", "7")))
    voice = PiperVoice.load(str(Path(__file__).parent / "voice" / "en_US-libritts_r-medium.onnx"))
    aligner, _, _ = det.load_model("base.en", "gpu")
    tiny, _, _ = det.load_model("tiny.en", "cpu", cpu_threads=8)
    asr = FastTranscriber(tiny, "en", 4.0)
    matcher = tr.test_matcher()
    X, y, utt, split, kept, t0 = [], [], [], [], 0, time.time()
    jobs = [("train", rng.choice(TRAIN_SPEAKERS), rng.random() < 0.65) for _ in range(n_train)]
    jobs += [("val", rng.choice(VAL_SPEAKERS), rng.random() < 0.65) for _ in range(n_val)]
    for i, (part, speaker, positive) in enumerate(jobs):
        text = sentence(rng, positive)
        cfg = SynthesisConfig(speaker_id=speaker, length_scale=rng.uniform(0.8, 1.2),
                              volume=rng.uniform(0.3, 1.0), noise_scale=rng.uniform(0.3, 0.9),
                              noise_w_scale=rng.uniform(0.3, 1.0))
        raw = np.concatenate([c.audio_float_array for c in voice.synthesize(text, syn_config=cfg)])
        # Speed/pitch shift: playing it back as if recorded at another rate (+-15%)
        speech = resample(raw, VOICE_SR * rng.uniform(0.87, 1.15) if AUGMENT else VOICE_SR, SR)
        if AUGMENT:
            speech = colour(speech, rng)
        lead = rng.uniform(0.3, 0.8)
        audio = np.concatenate([np.zeros(int(lead * SR), np.float32), speech, np.zeros(int(0.6 * SR), np.float32)])
        if rng.random() < 0.4:  # background noise at 8-25 dB SNR
            noise = np.cumsum(np.random.default_rng(i).normal(size=len(audio))).astype(np.float32)
            noise = np.diff(noise, prepend=0) * 0.7 + np.random.default_rng(i + 1).normal(size=len(audio)) * 0.3
            snr = rng.uniform(8, 25)
            p_s, p_n = np.mean(speech ** 2) + 1e-9, np.mean(noise ** 2) + 1e-9
            audio = (audio + noise * np.sqrt(p_s / p_n / 10 ** (snr / 10))).astype(np.float32)
        segs, _ = aligner.transcribe(audio, language="en", word_timestamps=True, beam_size=5)
        words = [(w.word, w.start, w.end) for s in segs for w in s.words]
        banned = [(s, e) for w, s, e in words if matcher.match_word(w)]
        expected = len(matcher.find(text.replace(",", " ").replace(".", " ").replace("!", " ").replace("?", " ")
                                    .split()))
        if len(banned) != expected:
            continue  # the timing model didn't hear the sentence as written: don't trust its labels
        kept += 1
        times = np.arange(0.25, len(audio) / SR, 0.04 if banned else 0.08)
        for t in times:
            near = any(s - 0.4 <= t <= e + 0.5 for s, e in banned)
            if banned and not near and rng.random() < 0.5:
                continue  # fewer far-from-target windows
            lo = max(0, int((t - det.WINDOW_SEC) * SR))
            window = audio[lo:int(t * SR)]
            if not asr.has_speech(window):
                continue  # the live engine skips these too
            if any(s + 0.06 <= t <= e + 0.15 for s, e in banned):
                label = 1
            elif any(s - 0.02 <= t < s + 0.06 for s, e in banned):
                label = -1  # the very first moments of a swear word: neither yes nor no
            else:
                label = 0
            X.append(edge_features(asr, window))
            y.append(label)
            utt.append(i)
            split.append(part == "val")
        if (i + 1) % 50 == 0:
            print(f"{i + 1}/{len(jobs)} sentences, {kept} kept, {len(y)} windows, {time.time() - t0:.0f}s",
                  flush=True)
    np.savez_compressed(out, X=np.array(X), y=np.array(y, np.int8), utt=np.array(utt), val=np.array(split))
    print(f"saved {out}: {len(y)} windows ({sum(v == 1 for v in y)} positive) from {kept} sentences")


if __name__ == "__main__":
    main()
