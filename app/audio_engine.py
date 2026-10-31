import ctypes
import threading
import warnings

import numpy as np
import sounddevice as sd

from . import devices
from .censor_sound import CensorVoice, replacement_clip
from .detector import DEFAULT_MIN_PROB, Detector, load_model

BLOCK_MS = 10
RAMP_MS = 5  # fade in/out of mutes so they don't click
DRIFT_TOL_MS = 15
RESYNC_MS = 120

warnings.filterwarnings("ignore", module="soundcard")  # "data discontinuity" chatter from loopback capture


class AudioEngine:
    """
    Capture -> ring buffer held back by `delay_ms` -> output stream, with a
    Detector zeroing (or bleeping) banned words in the buffer before the
    output reaches them.

    capture="device"   records an input device (mic, virtual cable output).
    capture="loopback" records what an *output* device is playing (desktop
                       audio), so the user keeps hearing it live and only the
                       copy sent to `output_device` is delayed and cleaned.

    Capture and output may be different hardware with different clocks, so
    the reader nudges itself by a sample per block to hold the lag at
    `delay_ms`, and never lets it shrink below that.
    """

    def __init__(
        self,
        input_device: str | None = None,
        output_device: str | None = None,
        capture: str = "device",
        delay_ms: int = 600,
        mute_pad_before_ms: int = 200,
        mute_pad_after_ms: int = 250,
        mute_mode: str = "silence",
        model_size: str = "base.en",
        matcher=None,
        min_prob: float = DEFAULT_MIN_PROB,
        language: str = "auto",
        beep_hz: float = 1000.0,
        censor_volume: float = 0.4,
        censor_sound: str | None = None,
        asr_context_sec="auto",
        early_mute: bool = False,
        on_catch=None,
        on_level=None,
        on_error=None,
        on_stats=None,
        samplerate: int | None = None,
        model_bundle=None,
    ):
        if capture == "loopback" and input_device is not None and input_device == output_device:
            raise ValueError("Can't capture and play on the same device - that would feed back into itself.")
        self.capture = capture
        self.input_name = input_device
        self.input_index = devices.resolve(input_device, "input") if capture == "device" else None
        self.output_index = devices.resolve(output_device, "output")
        self.on_level = on_level
        self.on_error = on_error

        if samplerate is None:
            samplerate = int(sd.query_devices(self.output_index, "output")["default_samplerate"])
        self.rate = samplerate
        self.block = self.rate * BLOCK_MS // 1000
        self.delay_ms = delay_ms
        self.delay = self.rate * delay_ms // 1000
        self.buffer_len = self.delay + self.rate * 3
        self.ramp = max(1, self.rate * RAMP_MS // 1000)
        self.drift_tol = self.rate * DRIFT_TOL_MS // 1000
        self.resync = self.rate * RESYNC_MS // 1000

        self.audio_buf = np.zeros((self.buffer_len, 2), dtype=np.float32)
        self.gain_buf = np.ones(self.buffer_len, dtype=np.float32)
        self.total_written = 0
        self.total_read = None
        self._holding = False
        self._mute_until = -(10**12)
        self._tentative: dict[int, tuple[int, int]] = {}  # early mutes that can still be undone
        self._next_tentative = 1
        self._lock = threading.Lock()
        self._level_tick = 0
        self.voice = CensorVoice(replacement_clip(mute_mode, self.rate, beep_hz, censor_volume, censor_sound))

        self.model, self.device, self.device_note = model_bundle or load_model(model_size)
        self.detector = Detector(
            self.model, self.rate, mute_pad_before_ms, mute_pad_after_ms,
            on_mute=self._apply_mute, on_catch=on_catch, on_stats=on_stats, on_error=on_error,
            matcher=matcher, min_prob=min_prob, language=language, context_sec=asr_context_sec,
            early_mute=early_mute, on_tentative=self.add_tentative, on_retract=self.retract,
        )

        self._in_stream = None
        self._out_stream = None
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()
        self.running = False

    # -- lifecycle -----------------------------------------------------

    def start(self):
        """Must be called on the main/UI thread: PortAudio's Windows backend can't start streams elsewhere."""
        if self.running:
            return
        out_idx = self.output_index if self.output_index is not None else sd.default.device[1]
        out_ch = min(2, sd.query_devices(out_idx, "output")["max_output_channels"])

        def extra(idx):
            # WASAPI shared mode only accepts the device's mix rate unless auto-convert is on.
            return sd.WasapiSettings(auto_convert=True) if devices.is_wasapi(idx) else None

        self.running = True
        self.detector.start()
        try:
            self._out_stream = sd.OutputStream(
                device=out_idx, samplerate=self.rate, channels=out_ch, dtype="float32",
                blocksize=self.block, latency="low", extra_settings=extra(out_idx),
                callback=self._out_callback,
            )
            if self.capture == "loopback":
                # Import here on the main thread: soundcard sets up COM at import time and fails if
                # that first happens on a thread where COM is already initialised.
                import soundcard

                self._soundcard = soundcard
                self._capture_stop.clear()
                self._capture_thread = threading.Thread(target=self._loopback_loop, daemon=True,
                                                        name="voiceguard-loopback")
                self._capture_thread.start()
            else:
                in_idx = self.input_index if self.input_index is not None else sd.default.device[0]
                in_ch = min(2, sd.query_devices(in_idx, "input")["max_input_channels"])
                self._in_stream = sd.InputStream(
                    device=in_idx, samplerate=self.rate, channels=in_ch, dtype="float32",
                    blocksize=self.block, latency="low", extra_settings=extra(in_idx),
                    callback=self._in_callback,
                )
                self._in_stream.start()
            self._out_stream.start()
        except Exception:
            self.stop()
            raise

    def stop(self):
        self.running = False
        self._capture_stop.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2)
            self._capture_thread = None
        for stream in (self._in_stream, self._out_stream):
            if stream is not None:
                stream.stop()
                stream.close()
        self._in_stream = self._out_stream = None
        self.detector.stop()

    def _loopback_loop(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # soundcard's WASAPI calls need COM on this thread
        sc = self._soundcard
        try:
            source = sc.get_microphone(id=self.input_name or str(sc.default_speaker().name), include_loopback=True)
            with source.recorder(samplerate=self.rate, channels=2, blocksize=self.block) as rec:
                while not self._capture_stop.is_set():
                    data = rec.record(numframes=self.block).astype(np.float32, copy=False)
                    self._in_callback(data, len(data), None, None)
        except Exception as exc:
            if self.on_error:
                self.on_error(f"desktop audio capture stopped: {exc}")

    # -- real-time callbacks ---------------------------------------------

    def _in_callback(self, indata, frames, time_info, status):
        if status and self.on_error:
            self.on_error(f"input: {status}")
        x = indata[:, :2] if indata.shape[1] >= 2 else np.repeat(indata[:, :1], 2, axis=1)

        with self._lock:
            start = self.total_written
            pos = start + np.arange(frames)
            idx = pos % self.buffer_len
            self.audio_buf[idx] = x
            # Fresh samples are unmuted unless a mute already reaches into the future.
            self.gain_buf[idx] = np.clip((pos - self._mute_until) / self.ramp, 0.0, 1.0)
            self.total_written = start + frames

        mono = x.mean(axis=1)
        self._level_tick += 1
        if self.on_level and self._level_tick % 5 == 0:
            self.on_level(float(np.sqrt(np.mean(mono * mono))))
        self.detector.feed(mono, start + frames)

    def _out_callback(self, outdata, frames, time_info, status):
        if status and self.on_error:
            self.on_error(f"output: {status}")

        with self._lock:
            if self.total_read is None:
                self.total_read = self.total_written - self.delay
            lag = self.total_written - self.total_read
            if self._holding or lag < self.delay - self.resync:
                # Capture stalled: emit silence until the *full* delay is rebuilt, never run with less.
                if lag < self.delay:
                    self._holding = True
                    outdata.fill(0)
                    return
                self._holding = False
            if lag > self.delay + self.resync:
                self.total_read = self.total_written - self.delay
                adj = 0
            elif lag > self.delay + self.drift_tol:
                adj = 1
            elif lag < self.delay - self.drift_tol:
                adj = -1
            else:
                adj = 0

            n = frames + adj
            pos = self.total_read + np.arange(n)
            valid = (pos >= 0) & (pos < self.total_written)
            idx = pos % self.buffer_len
            raw = self.audio_buf[idx] * valid[:, None]
            gain = np.where(valid, self.gain_buf[idx], 1.0).astype(np.float32)
            for tid, (s, e) in list(self._tentative.items()):
                if e + self.ramp < pos[0]:
                    del self._tentative[tid]  # already played past it
                    continue
                g = np.maximum(np.clip((s - pos) / self.ramp, 0.0, 1.0), np.clip((pos - e) / self.ramp, 0.0, 1.0))
                gain = np.minimum(gain, g.astype(np.float32))
            self.total_read += n

        if adj:
            src, dst = np.arange(n), np.linspace(0, n - 1, frames)
            raw = np.stack([np.interp(dst, src, raw[:, c]) for c in range(2)], axis=1).astype(np.float32)
            gain = np.interp(dst, src, gain).astype(np.float32)

        out = raw * gain[:, None]
        replacement = self.voice.render(gain)
        if replacement is not None:
            out += replacement[:, None]

        if outdata.shape[1] == 1:
            outdata[:, 0] = out.mean(axis=1)
        else:
            outdata[:] = out[:, : outdata.shape[1]]

    # -- muting ------------------------------------------------------------

    def add_tentative(self, start_abs: int, end_abs: int, tid: int | None = None) -> int:
        """Early mute that can still be undone (retract) until it's played. Re-using a tid extends it."""
        with self._lock:
            if tid is None:
                tid = self._next_tentative
                self._next_tentative += 1
            self._tentative[tid] = (start_abs, end_abs)
        return tid

    def retract(self, tid: int) -> None:
        with self._lock:
            self._tentative.pop(tid, None)

    def _apply_mute(self, start_abs: int, end_abs: int) -> int:
        """Mute [start_abs, end_abs) with short ramps. Returns the read position at the time of muting."""
        with self._lock:
            read_pos = self.total_read if self.total_read is not None else -(10**12)
            lo = max(start_abs - self.ramp, read_pos, self.total_written - self.buffer_len + 1)
            hi = min(end_abs + self.ramp, self.total_written)
            if hi > lo:
                pos = np.arange(lo, hi)
                g = np.maximum(
                    np.clip((start_abs - pos) / self.ramp, 0.0, 1.0),
                    np.clip((pos - end_abs) / self.ramp, 0.0, 1.0),
                ).astype(np.float32)
                idx = pos % self.buffer_len
                self.gain_buf[idx] = np.minimum(self.gain_buf[idx], g)
            self._mute_until = max(self._mute_until, end_abs)
        return read_pos
