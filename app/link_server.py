"""
Local link between Bleepline and its OBS filter plugin.

Each OBS filter instance connects over TCP on 127.0.0.1 and owns its own delay
line inside OBS. It streams mono audio here; we run a Detector per connection
and send back sample ranges to mute. All integers little-endian.

  plugin -> app   HELLO  b"VGL1"|b"VGL2"|b"VGL3" u32 sample_rate u32 delay_ms u16 name_len name(utf-8)
  plugin -> app   AUDIO  u8 1   u64 end_index   u32 n   float32[n]
  app -> plugin   MUTE   u8 2   u64 start_index u64 end_index
  app -> plugin   SOUND  u8 3   u32 n   float32[n]     (VGL2+: the censor sound, n=0 = silence)
  app -> plugin   EARLY  u8 4   u32 id  u64 start_index u64 end_index   (VGL3: undoable mute; same id = extend)
  app -> plugin   UNDO   u8 5   u32 id                                   (VGL3: remove that early mute)
"""
import socket
import struct
import threading

import numpy as np

from .censor_sound import replacement_clip
from .detector import DEFAULT_MIN_PROB, Detector

PORT = 47811
MAGIC = b"VGL1"  # kept for the reference client in tests
MAGICS = (b"VGL1", b"VGL2", b"VGL3")
MSG_AUDIO, MSG_MUTE, MSG_SOUND, MSG_EARLY, MSG_UNDO = 1, 2, 3, 4, 5
MAX_SAMPLES = 48000 * 2


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)


class LinkServer:
    def __init__(self, model, pad_before_ms=200, pad_after_ms=250, port=PORT,
                 on_catch=None, on_event=None, on_stats=None,
                 matcher=None, min_prob=DEFAULT_MIN_PROB, language="auto", censor=("silence", 1000.0, 0.4, None),
                 early_mute=False):
        self.model = model
        self.pad_before_ms = pad_before_ms
        self.pad_after_ms = pad_after_ms
        self.matcher = matcher
        self.min_prob = min_prob
        self.language = language
        self.censor = censor  # (mode, beep_hz, volume, sound file name)
        self.early_mute = early_mute
        self.port = port
        self.on_catch = on_catch  # (source_name, word, late_ms)
        self.on_event = on_event  # (text)
        self.on_stats = on_stats  # (source_name, infer_ms)
        self._listener: socket.socket | None = None
        self._sessions: list[_Session] = []
        self._lock = threading.Lock()

    def start(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", self.port))
        self._listener.listen()
        threading.Thread(target=self._accept_loop, daemon=True, name="bleepline-link").start()

    def stop(self):
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for s in sessions:
            s.close()

    def source_names(self) -> list[str]:
        with self._lock:
            return [s.name for s in self._sessions if s.name]

    def _accept_loop(self):
        while self._listener is not None:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            session = _Session(self, conn)
            with self._lock:
                self._sessions.append(session)
            session.start()

    def _ended(self, session):
        with self._lock:
            if session in self._sessions:
                self._sessions.remove(session)


class _Session(threading.Thread):
    def __init__(self, server: LinkServer, conn: socket.socket):
        super().__init__(daemon=True, name="bleepline-link-session")
        self.server = server
        self.conn = conn
        self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.name = ""
        self.detector: Detector | None = None
        self.delay_samples = 0
        self.last_end = 0
        self._next_early = 0
        self._send_lock = threading.Lock()

    def run(self):
        srv = self.server
        try:
            magic = _recv_exact(self.conn, 4)
            if magic not in MAGICS:
                return
            rate, delay_ms, name_len = struct.unpack("<IIH", _recv_exact(self.conn, 10))
            self.name = _recv_exact(self.conn, name_len).decode("utf-8", "replace") or "OBS source"
            self.delay_samples = rate * delay_ms // 1000
            if magic in (b"VGL2", b"VGL3"):
                mode, beep_hz, volume, sound = srv.censor
                clip = replacement_clip(mode, rate, beep_hz, volume, sound).astype("<f4")
                with self._send_lock:
                    self.conn.sendall(struct.pack("<BI", MSG_SOUND, len(clip)) + clip.tobytes())
            self.detector = Detector(
                srv.model, rate, srv.pad_before_ms, srv.pad_after_ms,
                on_mute=self._mute,
                on_catch=lambda w, late: srv.on_catch and srv.on_catch(self.name, w, late),
                on_stats=lambda ms: srv.on_stats and srv.on_stats(self.name, ms),
                on_error=lambda e: srv.on_event and srv.on_event(f"{self.name}: {e}"),
                matcher=srv.matcher, min_prob=srv.min_prob, language=srv.language,
                early_mute=srv.early_mute and magic == b"VGL3",  # older plugins can't undo a mute
                on_tentative=self._early, on_retract=self._undo,
            )
            self.detector.start()
            if srv.on_event:
                srv.on_event(f"OBS source connected: {self.name} ({rate} Hz, {delay_ms} ms delay)")
            while True:
                (kind,) = _recv_exact(self.conn, 1)
                if kind != MSG_AUDIO:
                    return
                end_index, n = struct.unpack("<QI", _recv_exact(self.conn, 12))
                if n > MAX_SAMPLES:
                    return
                samples = np.frombuffer(_recv_exact(self.conn, n * 4), dtype="<f4")
                self.last_end = end_index
                self.detector.feed(samples, end_index)
        except (ConnectionError, OSError):
            pass
        finally:
            self.close()
            srv._ended(self)
            if srv.on_event and self.name:
                srv.on_event(f"OBS source disconnected: {self.name}")

    def _send(self, data: bytes) -> None:
        try:
            with self._send_lock:
                self.conn.sendall(data)
        except OSError:
            pass

    def _early(self, start_abs: int, end_abs: int, tid: int | None) -> int:
        if tid is None:
            self._next_early += 1
            tid = self._next_early
        self._send(struct.pack("<BIQQ", MSG_EARLY, tid, max(0, start_abs), max(0, end_abs)))
        return tid

    def _undo(self, tid: int) -> None:
        self._send(struct.pack("<BI", MSG_UNDO, tid))

    def _mute(self, start_abs: int, end_abs: int) -> int:
        try:
            with self._send_lock:
                self.conn.sendall(struct.pack("<BQQ", MSG_MUTE, max(0, start_abs), max(0, end_abs)))
        except OSError:
            pass
        return self.last_end - self.delay_samples  # where the plugin's delayed output is right now

    def close(self):
        if self.detector is not None:
            self.detector.stop()
            self.detector = None
        try:
            self.conn.close()
        except OSError:
            pass
