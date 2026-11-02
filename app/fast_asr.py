"""
Whisper on just the live window.

faster-whisper pads every input to 30 s before the encoder, so a ~1 s live window
costs as much as 30 s of audio. The encoder accepts shorter input, so here the
window is padded only to the next WINDOW_BUCKET_SEC. That is ~36x less work for
the same words, cheap enough to run on two CPU threads instead of the GPU.
Same models, same languages; word timings come from Whisper's own alignment.

With load_model(device="split") the encoder runs on the GPU and a CPU copy of the
model decodes. Decoding takes one GPU round trip per token, and next to a game the
GPU switches between the two for every one of them, which cost the game a third of
its speed; split, it loses about 6% (research/keyword_spotter/README.md).
"""
from collections import namedtuple

import ctranslate2
import numpy as np
from faster_whisper.tokenizer import Tokenizer
from faster_whisper.vad import get_vad_model

ASR_RATE = 16000
PAD_STEP = 1600  # keep encoder input lengths to multiples of 0.1 s
SPEECH_PROB = 0.5  # Silero VAD probability above which a window counts as speech
NO_SPEECH_PROB = 0.6  # Whisper's own "this is not speech" score above which we ignore the output
TOKENS_PER_SEC = 8  # generous for speech; caps runaway repetition in short windows

Word = namedtuple("Word", "word start end probability")


class FastTranscriber:
    def __init__(self, whisper_model, language: str | None, context_sec: float = 3.0, needs_timings=None):
        """language: a Whisper code, or None to detect it per window (multilingual models only).
        context_sec: encoder input length. Whisper was trained on 30 s; a few seconds keeps it accurate
        while still being far cheaper, and a window shorter than this is padded with silence.
        needs_timings(words) -> bool: whether the words heard need timings (the alignment pass); None = always."""
        self.wm = whisper_model
        self.decoder = getattr(whisper_model, "cpu_decoder", None) or whisper_model  # see load_model "split"
        self.needs_timings = needs_timings
        self.context = int(context_sec * ASR_RATE)
        self.multilingual = bool(getattr(whisper_model.model, "is_multilingual", False))
        self.language = language if self.multilingual else "en"
        self._tokenizers: dict = {}
        self.vad = get_vad_model()

    def _tokenizer(self, language: str | None) -> Tokenizer:
        if language not in self._tokenizers:
            self._tokenizers[language] = Tokenizer(
                self.wm.hf_tokenizer, self.multilingual, task="transcribe", language=language or "en")
        return self._tokenizers[language]

    def has_speech(self, audio: np.ndarray) -> bool:
        n = len(audio) // 512 * 512
        return n > 0 and float(np.max(self.vad(audio[-n:]))) > SPEECH_PROB

    def words(self, audio: np.ndarray) -> list[Word]:
        """Words with start/end in seconds from the start of `audio` (16 kHz mono float32); none if
        needs_timings turned them down."""
        if not self.has_speech(audio):
            return []
        frames = -(-max(len(audio), self.context) // PAD_STEP) * PAD_STEP // 160
        real = self.wm.feature_extractor(audio)[:, :frames]
        # Whisper normalises silence to (loudest - 8) before scaling, i.e. max - 2 after scaling, so the
        # padding can be appended as a constant instead of running the spectrogram over seconds of zeros.
        features = np.full((1, real.shape[0], frames), real.max() - 2.0, dtype=np.float32)
        features[0, :, : real.shape[1]] = real
        split = self.decoder is not self.wm
        encoder_output = self.wm.model.encode(ctranslate2.StorageView.from_array(features), to_cpu=split)
        if split:
            on_cpu = np.asarray(encoder_output).astype(np.float32)  # the CPU copy's int8 decoder takes float32
            encoder_output = ctranslate2.StorageView.from_array(on_cpu)

        language = self.language
        if language is None:
            language = self.decoder.model.detect_language(encoder_output)[0][0][0].strip("<|>")
        tok = self._tokenizer(language)
        prompt = list(tok.sot_sequence) + [tok.no_timestamps]
        max_new = 4 + int(TOKENS_PER_SEC * len(audio) / ASR_RATE)
        result = self.decoder.model.generate(
            encoder_output, [prompt], beam_size=1, max_length=len(prompt) + max_new,
            return_no_speech_prob=True, suppress_blank=True, suppress_tokens=[-1],
            no_repeat_ngram_size=3,
        )[0]
        if result.no_speech_prob > NO_SPEECH_PROB:
            return []
        tokens = [t for t in result.sequences_ids[0] if t < tok.eot]
        if not tokens or (self.needs_timings and not self.needs_timings(tok.split_to_word_tokens(tokens)[0])):
            return []
        real_frames = min(frames, len(audio) // 160)
        alignment = self.decoder.find_alignment(tok, [tokens], encoder_output, real_frames)
        return [Word(w["word"], float(w["start"]), float(w["end"]), float(w["probability"]))
                for w in (alignment[0] if alignment else []) if w["word"].strip()]
