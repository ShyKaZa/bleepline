"""Live banned-word detection over an audio feed, shared by the local engine and the OBS plugin link."""
import os

# The speech engine's OpenMP threads busy-wait between checks by default; letting them sleep cut CPU use
# by ~2.5x at the same speed (measured: 7.8% -> 3.2% of a 16-thread CPU). Must be set before it loads.
os.environ.setdefault("KMP_BLOCKTIME", "0")

import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import deque  # noqa: E402

import numpy as np  # noqa: E402

from . import wordlists  # noqa: E402
from .paths import ensure_cuda_on_path, model_dir  # noqa: E402

ensure_cuda_on_path()
from faster_whisper import WhisperModel  # noqa: E402

ASR_RATE = 16000
WINDOW_SEC = 1.2
MIN_STEP_MS = 100  # re-transcribe at most this often; slows down automatically if the GPU can't keep up
EDGE_MS = 200  # a word ending this close to the window edge may still be in progress
EARLY_SLACK_MS = 200  # how close two sightings must start to count as the same word
EARLY_UNDO_EDGE_MS = 100  # a word that ended this long before the window edge counts as finished, for undoing
EARLY_MAX_WORD_MS = 500  # a word still being spoken can't have started earlier than this before the edge
EARLY_PAD_MS = 60  # lead-in before an early mute (smaller than the normal pad: these are often undone)
EARLY_TIMEOUT_MS = 1500  # an early mute nobody confirmed by then is undone
DEFAULT_MIN_PROB = 0.15  # "sensitivity": lower mutes on less certain recognitions
SILENCE_RMS = 0.003  # skip inference on near-silent windows (no GPU use when nobody talks)

_model_lock = threading.Lock()  # one Whisper model shared by every feed; serialise GPU calls


def resample_to_asr(x: np.ndarray, rate: int) -> np.ndarray:
    if rate == ASR_RATE:
        return x
    n_out = int(round(len(x) * ASR_RATE / rate))
    if rate < ASR_RATE:
        return np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)
    spectrum = np.fft.rfft(x)[: n_out // 2 + 1]  # band-limited downsample
    return (np.fft.irfft(spectrum, n_out) * (n_out / len(x))).astype(np.float32)


def _warm_up(model):
    segments, _ = model.transcribe(
        np.zeros(ASR_RATE, dtype=np.float32), language="en", beam_size=1, temperature=0.0, word_timestamps=True
    )
    list(segments)


def load_model(model_size: str, device: str = "gpu", cpu_threads: int = 2):
    """(model, device, note). device="cpu" runs on a few CPU threads and never touches the GPU;
    device="gpu" tries CUDA first and falls back to the CPU with the reason in `note`."""
    path = model_dir(model_size)
    source = str(path) if (path / "model.bin").exists() else model_size
    note = ""
    if device == "cpu":
        model = WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
        _warm_up(model)
        return model, "cpu", note
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            model = WhisperModel(source, device="cuda", compute_type="float16")
            _warm_up(model)  # cuBLAS loads here, so a missing GPU runtime fails now rather than mid-stream
            return model, "cuda", note
        note = "no CUDA GPU found"
    except Exception as exc:
        note = f"GPU unavailable: {str(exc).splitlines()[0][:100]}"
    model = WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
    _warm_up(model)
    return model, "cpu", note


def resolve_language(model, language: str | None) -> str | None:
    """English-only models (*.en) always get 'en'; multilingual ones get the chosen code or None (auto)."""
    if not getattr(model.model, "is_multilingual", True):
        return "en"
    return None if language in (None, "", "auto") else language


def banned_spans(matcher, words, min_prob: float):
    """(start_s, end_s, text) for banned words/phrases among Whisper word objects."""
    spans = []
    for i, j, text in matcher.find([w.word for w in words]):
        group = words[i:j]
        if min(w.probability if w.probability is not None else 1.0 for w in group) < min_prob:
            continue
        spans.append((group[0].start, group[-1].end, text))
    return spans


class Detector:
    """
    Re-transcribes a sliding window of a live feed and reports banned words.

    feed(mono, end_abs) is called from the audio thread with sample indices in
    the feed's own counter. For each banned word, on_mute(start_abs, end_abs)
    must apply the mute and return the feed's current *read* position (or
    None); that tells us whether the word was caught in time.
    """

    def __init__(self, model, rate, pad_before_ms=200, pad_after_ms=250,
                 on_mute=None, on_catch=None, on_stats=None, on_error=None,
                 matcher=None, min_prob=DEFAULT_MIN_PROB, language="auto", context_sec="auto",
                 early_mute=False, on_tentative=None, on_retract=None):
        """context_sec: transcribe a short window this long (see fast_asr); "auto" picks what measured best
        (4 s for English-only models, 3 s for multilingual); None = classic 30 s padding.
        early_mute: mute words that merely *start* like a banned word, via on_tentative(start, end, tid)->tid,
        and undo it with on_retract(tid) once they turn out harmless."""
        self.early_mute = early_mute
        self.on_tentative = on_tentative
        self.on_retract = on_retract
        self._pending: dict[int, int] = {}  # tentative id -> word start (abs samples)
        self.early_stats = {"early": 0, "confirmed": 0, "undone": 0}
        self.model = model
        self.rate = rate
        self.matcher = matcher or wordlists.build_matcher()
        self.min_prob = min_prob
        self.language = resolve_language(model, language) if model is not None else "en"
        self.asr = None
        if model is not None and context_sec is not None:
            from .fast_asr import FastTranscriber

            if context_sec == "auto":
                context_sec = 3.0 if getattr(model.model, "is_multilingual", False) else 4.0
            self.asr = FastTranscriber(model, self.language, context_sec)
        self.pad_before = rate * pad_before_ms // 1000
        self.pad_after = rate * pad_after_ms // 1000
        self.on_mute = on_mute
        self.on_catch = on_catch
        self.on_stats = on_stats
        self.on_error = on_error
        self.infer_ms = 0.0
        self._queue: queue.Queue = queue.Queue(maxsize=2000)
        self._recent: deque = deque(maxlen=32)
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bleepline-detector")
        self._thread.start()

    def stop(self):
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def feed(self, mono: np.ndarray, end_abs: int):
        try:
            self._queue.put_nowait((mono, end_abs))
        except queue.Full:
            pass

    def _is_new_catch(self, start_abs, end_abs):
        slack = self.rate // 5
        for s, e in self._recent:
            if start_abs < e + slack and end_abs > s - slack:
                return False
        self._recent.append((start_abs, end_abs))
        return True

    def _loop(self):
        win_max = int(WINDOW_SEC * self.rate)
        edge = self.rate * EDGE_MS // 1000
        step = self.rate * MIN_STEP_MS // 1000
        chunks: deque = deque()
        window_len = since_run = 0

        while True:
            items = [self._queue.get()]
            while True:  # drain any backlog so we always transcribe the freshest audio
                try:
                    items.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            for item in items:
                if item is None:
                    return
                mono, end_abs = item
                chunks.append((mono, end_abs - len(mono)))
                window_len += len(mono)
                since_run += len(mono)
            while window_len > win_max and len(chunks) > 1:
                window_len -= len(chunks.popleft()[0])
            if since_run < step:
                continue
            since_run = 0

            start_abs = chunks[0][1]
            window = np.concatenate([c for c, _ in chunks])
            window_end = start_abs + len(window)
            if float(np.sqrt(np.mean(window * window))) < SILENCE_RMS:
                continue

            t0 = time.perf_counter()
            try:
                with _model_lock:
                    if self.asr is not None:
                        words = self.asr.words(resample_to_asr(window, self.rate))
                    else:
                        segments, _ = self.model.transcribe(
                            resample_to_asr(window, self.rate),
                            language=self.language,
                            beam_size=1,
                            temperature=0.0,  # no fallback retries: they caused 300ms latency spikes
                            word_timestamps=True,
                            vad_filter=True,
                            condition_on_previous_text=False,
                        )
                        words = [w for seg in segments for w in (seg.words or [])]
            except Exception as exc:
                if self.on_error:
                    self.on_error(f"ASR error: {exc}")
                continue
            ms = (time.perf_counter() - t0) * 1000
            self.infer_ms = ms if self.infer_ms == 0 else 0.8 * self.infer_ms + 0.2 * ms
            step = self.rate * max(MIN_STEP_MS, int(self.infer_ms * 1.25)) // 1000
            if self.on_stats:
                self.on_stats(self.infer_ms)

            # Every sighting is applied, not just the first: Whisper's word timestamps jitter by
            # 100-250ms between passes, and mutes only ever add, so the result is their union.
            # Words still at the window edge are acted on eagerly: a wrong mute is cheap, a leak is not.
            confirmed = []
            for start_s, end_s, text in banned_spans(self.matcher, words, self.min_prob):
                w_start = start_abs + int(start_s * self.rate)
                w_end = start_abs + int(end_s * self.rate)
                confirmed.append((w_start, w_end))
                mute_end = w_end + self.pad_after
                if w_end > window_end - edge:
                    mute_end = max(mute_end, window_end + self.pad_after)  # rest of the word isn't transcribed yet
                read_pos = self.on_mute(w_start - self.pad_before, mute_end) if self.on_mute else None
                if self._is_new_catch(w_start, w_end) and self.on_catch:
                    late_ms = max(0, read_pos - w_start) * 1000 // self.rate if read_pos is not None else 0
                    self.on_catch(text, int(late_ms))
            if self.early_mute and self.on_tentative:
                self._early(words, start_abs, window_end, edge, confirmed)

    def _early(self, words, start_abs, window_end, edge, confirmed):
        """Early muting: words still being spoken that start like a banned word are muted tentatively,
        then confirmed (the real mute takes over) or undone once the finished word turns out harmless."""
        slack = self.rate * EARLY_SLACK_MS // 1000
        undo_edge = self.rate * EARLY_UNDO_EDGE_MS // 1000
        placed = [(start_abs + int(w.start * self.rate), start_abs + int(w.end * self.rate), w) for w in words]

        for tid, ws in list(self._pending.items()):
            if any(s - slack <= ws <= e + slack for s, e in confirmed):
                outcome = "confirmed"
            elif window_end - ws > self.rate * EARLY_TIMEOUT_MS // 1000:
                outcome = "undone"
            else:
                same = [(s, e, w) for s, e, w in placed if abs(s - ws) <= slack]

                def harmless(e, w):
                    if self.matcher.match_word(w.word):
                        return False
                    if not self.matcher.is_partial(w.word):
                        return True  # resolved to something that can't become a banned word ("bitter")
                    return e < window_end - undo_edge  # "she": harmless only once it's clearly finished

                outcome = "undone" if same and all(harmless(e, w) for s, e, w in same) else None
            if outcome:
                self.on_retract(tid)  # a confirmed word keeps its permanent mute
                del self._pending[tid]
                self.early_stats[outcome] += 1

        earliest = window_end - self.rate * EARLY_MAX_WORD_MS // 1000
        early_pad = self.rate * EARLY_PAD_MS // 1000
        for s, e, w in placed:
            if e <= window_end - edge or self.matcher.match_word(w.word) or not self.matcher.is_partial(w.word):
                continue  # finished, already banned (handled above), or not the start of a banned word
            s = max(s, earliest)  # the model sometimes dates an unfinished word back to the window start
            existing = next((tid for tid, ws in self._pending.items() if abs(ws - s) <= slack), None)
            tid = self.on_tentative(s - early_pad, window_end + self.pad_after, existing)
            if existing is None:
                self._pending[tid] = s
                self.early_stats["early"] += 1
