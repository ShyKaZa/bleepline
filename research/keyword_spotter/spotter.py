"""The fast spotter: its own sound features (log-mel, 10 ms frames) and a small causal network in plain NumPy.

Causal means each frame's output only uses audio up to that frame, so running it over a whole clip gives exactly
what the live loop would see by checking the newest frame every 20 ms. Trained by train_spotter.py (PyTorch,
dev only); the app side would only need this file and the weights.
"""
import numpy as np

SR = 16000
HOP = 160  # 10 ms
WIN = 400  # 25 ms
NFFT = 512
N_MELS = 40
KERNEL = 3
DILATIONS = (1, 2, 4, 8, 16)
CONTEXT = 1 + (KERNEL - 1) * (1 + sum(DILATIONS))  # frames one output looks back on (65 = 0.65 s)


def _mel_bank():
    def mel(f):
        return 2595 * np.log10(1 + f / 700)
    edges = 700 * (10 ** (np.linspace(mel(60), mel(7600), N_MELS + 2) / 2595) - 1)
    bins = np.fft.rfftfreq(NFFT, 1 / SR)
    bank = np.zeros((N_MELS, len(bins)), np.float32)
    for m in range(N_MELS):
        lo, mid, hi = edges[m:m + 3]
        bank[m] = np.clip(np.minimum((bins - lo) / (mid - lo), (hi - bins) / (hi - mid)), 0, None)
    return bank


BANK = _mel_bank()
WINDOW = np.hanning(WIN).astype(np.float32)


def features(audio16):
    """(frames, N_MELS) log-mel power; frame i ends at (i * HOP + WIN) / SR seconds."""
    if len(audio16) < WIN:
        return np.zeros((0, N_MELS), np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(audio16.astype(np.float32), WIN)[::HOP] * WINDOW
    power = np.abs(np.fft.rfft(frames, NFFT)) ** 2
    return np.log(power @ BANK.T + 1e-6).astype(np.float32)


def frame_end(i):
    return (i * HOP + WIN) / SR


def _causal(x, w, dilation):
    """x (T, Cin), w (Cout, Cin, K) -> (T, Cout); output t sees x[t], x[t-d], ..., x[t-(K-1)d]."""
    k = w.shape[2]
    xp = np.concatenate([np.zeros(((k - 1) * dilation, x.shape[1]), x.dtype), x])
    return sum(xp[j * dilation: j * dilation + len(x)] @ w[:, :, j].T for j in range(k))


def _depthwise(x, w, dilation):
    """x (T, C), w (C, K)."""
    k = w.shape[1]
    xp = np.concatenate([np.zeros(((k - 1) * dilation, x.shape[1]), x.dtype), x])
    return sum(xp[j * dilation: j * dilation + len(x)] * w[:, j] for j in range(k))


class Spotter:
    def __init__(self, path):
        self.p = dict(np.load(path))

    def probs(self, feats):
        """Per-frame probability that a banned word is under way."""
        p = self.p
        x = (feats - p["mu"]) / p["sd"]
        h = np.maximum(_causal(x, p["in_w"], 1) * p["in_scale"] + p["in_shift"], 0)
        for i, d in enumerate(DILATIONS):
            y = _depthwise(h, p[f"dw{i}_w"], d)
            y = np.maximum((y @ p[f"pw{i}_w"].T) * p[f"pw{i}_scale"] + p[f"pw{i}_shift"], 0)
            h = h + y
        logit = h @ p["out_w"] + p["out_b"]
        return 1 / (1 + np.exp(-logit))

    def timeline(self, audio16):
        """(sample at 16 kHz where each output becomes available, probability)."""
        probs = self.probs(features(audio16))
        return np.arange(len(probs)) * HOP + WIN, probs
