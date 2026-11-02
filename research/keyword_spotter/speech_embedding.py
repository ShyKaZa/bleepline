"""Google's pretrained speech embedding (speech_embedding/1, Apache-2.0) as the spotter's front end.

Google trained it on ~200 million 2 s YouTube clips of ~5,000 keywords (Lin et al. 2020, "Training keyword
spotters with limited and synthesized speech data", arXiv:2002.01322): small heads on top of it learn new words
from few or synthesized examples, which is what the plain log-mel spotter could not do for real voices.

Weights: the PyTorch conversion at huggingface.co/androiddrew/google-speech-embedding-pytorch (model.safetensors,
sha256 80a10015...7be; LICENSE and NOTICE kept next to it in data/gse/). The network below follows that
conversion: 16 kHz audio -> log-mel (32 bins, 25 ms window, 10 ms hop) -> CNN -> one 96-number embedding per
80 ms, each looking back ~0.78 s and never ahead, so it streams. PyTorch, dev only.
"""
import json
import struct
import time
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

WEIGHTS = Path(__file__).resolve().parent / "data/gse/model.safetensors"
SHA256 = "80a10015f4eb8ff9ba1aa10b56b1d6e27ef9f9f3419ebbae56fb43e9afce37be"
LEAD = 11120  # leading zeros the model was trained with
STRIDE = 1280  # 80 ms per embedding
PHASES = (0, 320, 640, 960)  # four interleaved sequences give a fresh embedding every 20 ms
SR = 16000


def load(path=WEIGHTS):
    data = Path(path).read_bytes()
    n = struct.unpack("<Q", data[:8])[0]
    header, base = json.loads(data[8:8 + n]), 8 + n
    kinds = {"F32": np.float32, "I64": np.int64}
    return {k: np.frombuffer(data[base + v["data_offsets"][0]: base + v["data_offsets"][1]],
                             kinds[v["dtype"]]).reshape(v["shape"])
            for k, v in header.items() if k != "__metadata__"}


class Embedding:
    def __init__(self, path=WEIGHTS):
        w = {k: torch.tensor(v.copy()) for k, v in load(path).items() if not k.endswith("num_batches_tracked")}
        self.window, self.mel = w["window"], w["mel_matrix"]
        self.initial, self.output = w["initial.weight"], w["output.weight"]
        self.convs = [w[f"convs.{i}.weight"] for i in range(18)]
        self.norms = []
        for i in range(19):  # inference batch norm as scale and shift
            scale = w[f"norms.{i}.weight"] / torch.sqrt(w[f"norms.{i}.running_var"] + 0.001)
            self.norms.append((scale[:, None, None], (w[f"norms.{i}.bias"] - w[f"norms.{i}.running_mean"] * scale)[:, None, None]))

    @staticmethod
    def _act(x):  # the original graph: max(max(0.2x, x), -0.4)
        return F.leaky_relu(x, 0.2).clamp(min=-0.4)

    def log_mel(self, audio16):
        x = F.pad(torch.as_tensor(audio16, dtype=torch.float32), (LEAD, 0))
        frames = x.unfold(0, 400, 160) * self.window
        return torch.log(torch.fft.rfft(frames, n=512).abs() @ self.mel + 0.001)

    def encode(self, log_mel):
        """(frames, 32) log-mel -> (n, 96)."""
        s, b = self.norms[0]
        x = self._act(F.relu(F.conv2d(log_mel[None, None], self.initial, padding=(0, 1))) * s + b)
        for i, w in enumerate(self.convs):
            s, b = self.norms[i + 1]
            x = self._act(F.conv2d(x, w, padding=(0, 1) if i % 2 == 0 else 0) * s + b)
            if i in (1, 9, 17):
                x = F.max_pool2d(x, (2, 2))
            elif i in (5, 13):
                x = F.max_pool2d(x, (1, 2))
        return F.conv2d(x, self.output)[0, :, :, 0].T

    @torch.inference_mode()
    def __call__(self, audio16):
        """Embeddings for a clip, (len // 1280, 96); embedding i covers the audio up to sample 1280 * (i + 1)."""
        out = self.encode(self.log_mel(audio16))
        assert out.shape == (len(audio16) // STRIDE, 96), out.shape
        return out.numpy()

    def phases(self, audio16):
        """[(end times in s, embeddings)] for each of PHASES."""
        result = []
        for o in PHASES:
            e = self(audio16[o:])
            result.append(((o + STRIDE * (np.arange(len(e)) + 1)) / SR, e))
        return result


HEAD_DILATIONS = (1, 2, 4)  # the head looks back 15 embeddings (1.2 s) on top of each embedding's own 0.78 s


class EmbSpotter:
    """Embeddings plus a small causal head (train_emb_spotter.py; NumPy, batch norm folded in)."""

    def __init__(self, path):
        import spotter as sp
        self._causal = sp._causal
        self.p = dict(np.load(path))
        self.emb = Embedding()

    def head(self, emb):
        """(n, 96) embeddings of one sequence -> (n,) probability that a banned word is under way."""
        p = self.p
        h = np.maximum(self._causal((emb - p["mu"]) / p["sd"], p["in_w"], 1) * p["in_scale"] + p["in_shift"], 0)
        for i, d in enumerate(HEAD_DILATIONS):
            h = h + np.maximum(self._causal(h, p[f"c{i}_w"], d) * p[f"c{i}_scale"] + p[f"c{i}_shift"], 0)
        return 1 / (1 + np.exp(-(h @ p["out_w"] + p["out_b"])))

    def timeline(self, audio16):
        """(sample at 16 kHz where each output becomes available, probability), all phases merged."""
        ends, probs = [], []
        for o, (_, e) in zip(PHASES, self.phases(audio16)):
            ends.append(o + STRIDE * (np.arange(len(e)) + 1))
            probs.append(self.head(e.astype(np.float32)))
        ends, probs = np.concatenate(ends), np.concatenate(probs)
        order = np.argsort(ends, kind="stable")
        return ends[order], probs[order]

    def phases(self, audio16):
        return self.emb.phases(audio16)

    def check_cost(self, audio16):
        """Microseconds per 20 ms check on one thread: (recomputing the newest embedding's whole 0.78 s window,
        streaming with every layer's past outputs kept, estimated from a long clip)."""
        threads = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                lm = self.emb.log_mel(audio16)
                seqs = [self.emb(audio16[o:]).astype(np.float32) for o in PHASES]
                t0, k = time.perf_counter(), 100
                for j in range(k):
                    self.emb.encode(lm[8 * j: 8 * j + 76])
                    self.head(seqs[j % 4][max(0, j // 4 - 14): j // 4 + 1])
                window = (time.perf_counter() - t0) / k * 1e6
                t0 = time.perf_counter()
                for _ in range(3):
                    n = len(self.emb(audio16))
                stream = (time.perf_counter() - t0) / 3 / n * 1e6  # one new embedding of one phase
                t0 = time.perf_counter()
                for j in range(k):
                    self.head(seqs[j % 4][max(0, j // 4 - 14): j // 4 + 1])
                head = (time.perf_counter() - t0) / k * 1e6
            return window, stream + head
        finally:
            torch.set_num_threads(threads)
