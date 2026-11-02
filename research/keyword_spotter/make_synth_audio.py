"""Synthetic sentences as in make_dataset.py (piper LibriTTS-R speakers, swear words and look-alikes), kept as audio
with base.en word timings, for training the fast spotter on its own sound features.

    python make_synth_audio.py <out.pkl> <n_train> <n_val>
"""
import pickle
import random
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()  # before make_dataset switches to the repo folder
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_dataset as md  # noqa: E402  (sets up piper and the repo path)

import numpy as np  # noqa: E402
from piper import PiperVoice, SynthesisConfig  # noqa: E402

import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402


def main():
    n_train, n_val = int(sys.argv[2]), int(sys.argv[3])
    rng = random.Random(23)
    voice = PiperVoice.load(str(md.HERE / "voice" / "en_US-libritts_r-medium.onnx"))
    aligner, _, _ = det.load_model("base.en", "gpu")
    matcher = tr.test_matcher()
    jobs = [(False, rng.choice(md.TRAIN_SPEAKERS)) for _ in range(n_train)]
    jobs += [(True, rng.choice(md.VAL_SPEAKERS)) for _ in range(n_val)]
    kept, t0 = [], time.time()
    for i, (val, speaker) in enumerate(jobs):
        text = md.sentence(rng, rng.random() < 0.6)
        cfg = SynthesisConfig(speaker_id=speaker, length_scale=rng.uniform(0.75, 1.2),
                              volume=rng.uniform(0.3, 1.0), noise_scale=rng.uniform(0.3, 0.9),
                              noise_w_scale=rng.uniform(0.3, 1.0))
        raw = np.concatenate([c.audio_float_array for c in voice.synthesize(text, syn_config=cfg)])
        augment = rng.random() < 0.6
        speech = md.resample(raw, md.VOICE_SR * (rng.uniform(0.87, 1.15) if augment else 1.0), md.SR)
        if augment:
            speech = md.colour(speech, rng)
        audio = np.concatenate([np.zeros(int(rng.uniform(0.3, 0.8) * md.SR), np.float32), speech,
                                np.zeros(int(0.6 * md.SR), np.float32)])
        segs, _ = aligner.transcribe(audio, language="en", word_timestamps=True, beam_size=5)
        words = [(w.word, w.start, w.end) for s in segs for w in s.words]
        banned = [w for w, _, _ in words if matcher.match_word(w)]
        expected = len(matcher.find(text.replace(",", " ").replace(".", " ").replace("!", " ").replace("?", " ")
                                    .split()))
        if len(banned) == expected:  # otherwise the timing model didn't hear it as written: labels untrusted
            kept.append({"audio": (np.clip(audio, -1, 1) * 32767).astype(np.int16), "words": words, "val": val,
                         "text": text})
        if (i + 1) % 200 == 0:
            print(f"{i + 1}/{len(jobs)} sentences, {len(kept)} kept, {time.time() - t0:.0f}s", flush=True)
    OUT.write_bytes(pickle.dumps(kept))
    print(f"saved {OUT}: {len(kept)} sentences ({sum(k['val'] for k in kept)} validation)")


if __name__ == "__main__":
    main()
