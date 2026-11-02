"""The app's FastTranscriber, with knobs for making each live check cheaper or moving it off the GPU. Dev only.

    load(model, matcher, where): where = "gpu", "cpu" (int8, SPOT_ASR_THREADS threads, as the app's CPU mode),
                                 or "split": the encoder on the GPU, the decoder on a CPU copy of the model
    SPOT_ASR_CTX      encoder input length in seconds (the app: 4.0)
    SPOT_ASR_COMPUTE  CTranslate2 compute type on the GPU (the app: float16)
    SPOT_ASR_THREADS  CPU threads for the CPU model (the app: 2)
    SPOT_ASR_LEAN=1   run the word-timing pass only when the text has a banned word in it; the mutes come out
                      the same, since only banned words need timings

On the GPU the decoder is most of each check: one round trip per token. Next to a game the GPU switches between
the two for every one of those, which costs the game far more than the work itself ("split" avoids them).

`parts` adds up where each check's time goes: speech check + spectrogram, encoder, decoder, word timings.
"""
import os
import time

import ctranslate2
import numpy as np
from faster_whisper import WhisperModel

from app import detector as det
from app.fast_asr import NO_SPEECH_PROB, PAD_STEP, TOKENS_PER_SEC, FastTranscriber, Word

CTX = float(os.environ.get("SPOT_ASR_CTX", "4.0"))
COMPUTE = os.environ.get("SPOT_ASR_COMPUTE", "float16")
THREADS = int(os.environ.get("SPOT_ASR_THREADS", "2"))
LEAN = os.environ.get("SPOT_ASR_LEAN") == "1"


def describe(where):
    on = {"gpu": f"GPU {COMPUTE}", "cpu": f"CPU int8, {THREADS} threads",
          "split": f"encoder on the GPU ({COMPUTE}), decoder on the CPU (int8, {THREADS} threads)"}[where]
    return f"{on}, {CTX:g} s encoder input{', timings only for banned words' if LEAN else ''}"


def load(model_size: str, matcher, where: str = "gpu") -> "LeanTranscriber":
    path = det.model_dir(model_size)
    source = str(path) if (path / "model.bin").exists() else model_size

    def on_cpu():
        return WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=THREADS)
    model = on_cpu() if where == "cpu" else WhisperModel(source, device="cuda", compute_type=COMPUTE)
    det._warm_up(model)
    decoder = on_cpu() if where == "split" else None
    if decoder:
        det._warm_up(decoder)
    return LeanTranscriber(model, matcher, decoder)


class LeanTranscriber(FastTranscriber):
    edge = None  # kws_replay.record reads it: no encoder-feature detector here

    def __init__(self, model, matcher, decoder=None):
        super().__init__(model, "en", CTX)
        self.matcher = matcher
        self.decoder = decoder or model  # the WhisperModel that decodes and times the words
        self.parts = np.zeros(4)

    def words(self, audio: np.ndarray) -> list[Word]:
        t0 = time.perf_counter()
        if not self.has_speech(audio):
            self.parts[0] += time.perf_counter() - t0
            return []
        frames = -(-max(len(audio), self.context) // PAD_STEP) * PAD_STEP // 160
        real = self.wm.feature_extractor(audio)[:, :frames]
        features = np.full((1, real.shape[0], frames), real.max() - 2.0, dtype=np.float32)
        features[0, :, : real.shape[1]] = real
        t1 = time.perf_counter()
        split = self.decoder is not self.wm
        encoder_output = self.wm.model.encode(ctranslate2.StorageView.from_array(features), to_cpu=split)
        if split:  # the CPU copy's int8 decoder takes float32
            encoder_output = ctranslate2.StorageView.from_array(np.array(encoder_output).astype(np.float32))
        t2 = time.perf_counter()
        tok = self._tokenizer(self.language)
        prompt = list(tok.sot_sequence) + [tok.no_timestamps]
        result = self.decoder.model.generate(
            encoder_output, [prompt], beam_size=1, max_length=len(prompt) + 4 + int(TOKENS_PER_SEC * len(audio) / 16000),
            return_no_speech_prob=True, suppress_blank=True, suppress_tokens=[-1], no_repeat_ngram_size=3)[0]
        t3 = time.perf_counter()
        self.parts[:3] += (t1 - t0, t2 - t1, t3 - t2)
        if result.no_speech_prob > NO_SPEECH_PROB:
            return []
        tokens = [t for t in result.sequences_ids[0] if t < tok.eot]
        if not tokens or (LEAN and not self.matcher.find(tok.decode(tokens).split())):
            return []
        alignment = self.decoder.find_alignment(tok, [tokens], encoder_output, min(frames, len(audio) // 160))
        self.parts[3] += time.perf_counter() - t3
        return [Word(w["word"], float(w["start"]), float(w["end"]), float(w["probability"]))
                for w in (alignment[0] if alignment else []) if w["word"].strip()]
