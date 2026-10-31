"""
Tests the OBS link end to end with a Python stand-in for the C plugin: same
protocol, same synchronous delay line an OBS audio filter has (1024-sample
ticks at 48 kHz, real-time pacing).

    python test_link.py [delay_ms] [model_size]
"""
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from better_profanity import profanity

import integration_test as it
from app.detector import load_model
from app.link_server import MAGIC, MSG_AUDIO, MSG_MUTE, LinkServer

RATE = 48000
TICK = 1024  # OBS delivers audio to filters in 1024-frame chunks


class FakePlugin:
    """Mirror of the C plugin's logic: delay line + gain mask, mutes arrive asynchronously."""

    def __init__(self, delay_ms, port):
        self.delay = RATE * delay_ms // 1000
        self.size = self.delay + RATE * 3
        self.buf = np.zeros(self.size, np.float32)
        self.gain = np.ones(self.size, np.float32)
        self.written = 0
        self.mute_until = -(10**12)
        self.lock = threading.Lock()
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        name = b"Desktop Audio"
        self.sock.sendall(MAGIC + struct.pack("<IIH", RATE, delay_ms, len(name)) + name)
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        try:
            while True:
                kind, start, end = struct.unpack("<BQQ", it_recv(self.sock, 17))
                assert kind == MSG_MUTE
                with self.lock:
                    lo = max(start, self.written - self.delay - TICK, self.written - self.size + 1)
                    hi = min(end, self.written)
                    if hi > lo:
                        self.gain[np.arange(lo, hi) % self.size] = 0.0
                    self.mute_until = max(self.mute_until, end)
        except (ConnectionError, OSError):
            pass

    def filter_audio(self, samples):
        n = len(samples)
        with self.lock:
            pos = self.written + np.arange(n)
            idx = pos % self.size
            self.buf[idx] = samples
            self.gain[idx] = np.where(pos < self.mute_until, 0.0, 1.0)
            self.written += n
            rpos = pos - self.delay
            ridx = rpos % self.size
            out = np.where(rpos >= 0, self.buf[ridx] * self.gain[ridx], 0.0)
        self.sock.sendall(struct.pack("<BQI", MSG_AUDIO, self.written, n) + samples.astype("<f4").tobytes())
        return out


def it_recv(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


def main():
    delay_ms = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    model_size = sys.argv[2] if len(sys.argv) > 2 else "tiny.en"
    wav = Path(tempfile.gettempdir()) / "bleepline_test_phrase.wav"
    if not wav.exists():
        it.synthesize(wav)
    speech = it.load_wav(wav, RATE)
    full = np.concatenate([speech, np.zeros(RATE * 2, np.float32)])

    model, device, _ = load_model(model_size)
    events, catches = [], []
    server = LinkServer(model, port=47899, on_event=events.append,
                        on_catch=lambda src, w, late: catches.append((src, w, late)))
    server.start()
    plugin = FakePlugin(delay_ms, 47899)
    time.sleep(0.2)

    outs, t0 = [], time.time()
    for i in range(len(full) // TICK):
        outs.append(plugin.filter_audio(full[i * TICK:(i + 1) * TICK]))
        time.sleep(max(0.0, t0 + (i + 1) * TICK / RATE - time.time()))
    plugin.sock.close()
    time.sleep(0.3)
    server.stop()
    out = np.concatenate(outs)

    print(f"model={model_size} on {device}, delay={delay_ms}ms | events: {events}")
    segs, _ = model.transcribe(it.load_wav(wav, 16000), language="en", word_timestamps=True)
    ok = total = 0
    for w in (w for s in segs for w in s.words):
        word = it.clean(w.word)
        if not profanity.contains_profanity(word):
            continue
        a, b = int(w.start * RATE), int(w.end * RATE)
        muted = 1 - np.abs(out[a + plugin.delay:b + plugin.delay]).mean() / np.abs(speech[a:b]).mean()
        total += 1
        ok += muted > 0.95
        print(f"  {word:10s} {w.start:5.2f}s muted {muted * 100:5.1f}%")
    print(f"{ok}/{total} fully muted | catches: {catches}")


if __name__ == "__main__":
    main()
