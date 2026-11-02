"""Train the fast spotter (spotter.py's network) on make_spot_dataset.py's features. PyTorch, dev only (installed to
torchlib/ with pip --target); the weights are exported for spotter.Spotter in plain NumPy, batch norm folded in.

Label per 10 ms frame (by the time the frame ends): 1 from SPOT_SKIP s after a swear word starts until it ends,
ignored just before/after that and around mild words, 0 everywhere else. Augmented with gain changes, mixing in
other clips as background babble, and frequency/time masking.

    python train_spotter.py <dataset.pkl> <out.npz> [width]
"""
import copy
import hashlib
import os
import pickle
import queue
import random
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# torchlib_cuda: PyTorch built for NVIDIA GPUs (pip --target, like torchlib); used when it's there
sys.path.insert(0, str(HERE / os.environ.get("SPOT_TORCHLIB", "torchlib_cuda" if (HERE / "torchlib_cuda").exists()
                                             else "torchlib")))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402

import spotter as sp  # noqa: E402

DEVICE = torch.device(os.environ.get("SPOT_DEVICE", "cuda" if torch.cuda.is_available() else "cpu"))
SEED = int(os.environ.get("SPOT_SEED", "1"))

SKIP = float(os.environ.get("SPOT_SKIP", "0.08"))
POS_WEIGHT = float(os.environ.get("SPOT_POS_WEIGHT", "2"))
CROP = 400  # frames per training example (4 s)
EPOCHS = int(os.environ.get("SPOT_EPOCHS", "40"))
STEPS = int(os.environ.get("SPOT_STEPS", "300"))  # batches per epoch
BATCH = 48
SOURCE_SHARE = dict(zip(("real", "synthetic", "everyday"),
                        map(float, os.environ.get("SPOT_SHARE", "0.4,0.35,0.25").split(","))))
CHANNEL = os.environ.get("SPOT_CHANNEL", "0") == "1"  # also simulate microphones/lines: tilt, band limits, noise
SELECT = os.environ.get("SPOT_SELECT", "all")  # keep the epoch with the best val loss on "all" or "real" voices only
REAL_PART = os.environ.get("SPOT_REAL_PART", "")  # "k/n": train on the k-th of n shares of the real recordings only
DB = np.log(10) / 10  # 1 dB in natural-log power


def labels(x, t=None):
    """Targets and loss mask for outputs available at times t (s); by default the log-mel frames' end times."""
    t = sp.frame_end(np.arange(len(x["feats"]))) if t is None else t
    n = len(t)
    y = np.zeros(n, np.float32)
    mask = np.ones(n, np.float32)
    for s, e in x["mild"]:
        mask[(t >= s - 0.1) & (t <= e + 0.25)] = 0
    for s, e in x["pos"]:
        mask[(t >= s - 0.05) & (t <= e + 0.25)] = 0
    for s, e in x["pos"]:
        on = (t >= s + SKIP) & (t <= max(e + 0.05, s + SKIP + 0.03))
        y[on], mask[on] = 1, 1
    return y, mask


class Net(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.inp = nn.Conv1d(sp.N_MELS, width, sp.KERNEL)
        self.inp_bn = nn.BatchNorm1d(width)
        self.dw = nn.ModuleList(nn.Conv1d(width, width, sp.KERNEL, dilation=d, groups=width, bias=False)
                                for d in sp.DILATIONS)
        self.pw = nn.ModuleList(nn.Conv1d(width, width, 1) for _ in sp.DILATIONS)
        self.bn = nn.ModuleList(nn.BatchNorm1d(width) for _ in sp.DILATIONS)
        self.drop = nn.Dropout(0.15)
        self.out = nn.Conv1d(width, 1, 1)

    def forward(self, x):  # (B, mels, T) -> (B, T) logits, causal
        h = torch.relu(self.inp_bn(self.inp(nn.functional.pad(x, (sp.KERNEL - 1, 0)))))
        for dw, pw, bn, d in zip(self.dw, self.pw, self.bn, sp.DILATIONS):
            y = dw(nn.functional.pad(h, ((sp.KERNEL - 1) * d, 0)))
            h = h + self.drop(torch.relu(bn(pw(y))))
        return self.out(h)[:, 0]


def export(net, mu, sd, path, sample):
    """Save for spotter.Spotter, and check it computes what the network does (on one clip's features)."""
    net = copy.deepcopy(net).cpu()

    def fold(conv_bias, bn):
        scale = (bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
        shift = (bn.bias - bn.running_mean * bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
        return scale, shift + conv_bias.detach().numpy() * scale
    p = {"mu": mu, "sd": sd, "in_w": net.inp.weight.detach().numpy()}
    p["in_scale"], p["in_shift"] = fold(net.inp.bias, net.inp_bn)
    for i, (dw, pw, bn) in enumerate(zip(net.dw, net.pw, net.bn)):
        p[f"dw{i}_w"] = dw.weight.detach().numpy()[:, 0, :]
        p[f"pw{i}_w"] = pw.weight.detach().numpy()[:, :, 0]
        p[f"pw{i}_scale"], p[f"pw{i}_shift"] = fold(pw.bias, bn)
    p["out_w"] = net.out.weight.detach().numpy()[0, :, 0]
    p["out_b"] = net.out.bias.detach().numpy()
    np.savez(path, **{k: np.asarray(v, np.float32) for k, v in p.items()})
    with torch.no_grad():
        ref = torch.sigmoid(net(torch.tensor(((sample - mu) / sd).astype(np.float32).T[None]))[0]).numpy()
    diff = float(np.abs(sp.Spotter(path).probs(sample.astype(np.float32)) - ref).max())
    assert diff < 1e-3, f"the NumPy spotter differs from the network by {diff}"


def batch(rng, pools, babble, mu, sd):
    """Augmented crops; mixing is done on the raw log-mel power, then normalized."""
    xs, ys, ms = [], [], []
    for _ in range(BATCH):
        source = rng.choices(list(SOURCE_SHARE), weights=list(SOURCE_SHARE.values()))[0]
        x, y, m = rng.choice(pools[source])
        n = len(x)
        swears = np.flatnonzero(y)
        if len(swears) and rng.random() < 0.7:  # mostly crops around a swear word
            c = int(rng.choice(swears))
            lo = min(max(0, c - rng.randrange(CROP // 4, CROP - 20)), max(0, n - CROP))
        else:
            lo = rng.randrange(max(1, n - CROP + 1))
        x, y, m = x[lo:lo + CROP].astype(np.float32), y[lo:lo + CROP], m[lo:lo + CROP]
        x = x + rng.uniform(-2.3, 2.3)  # +-10 dB level
        if rng.random() < 0.4:  # other speech underneath, 5-25 dB quieter
            b = rng.choice(babble)
            off = rng.randrange(max(1, len(b) - len(x) + 1))
            b = b[off:off + len(x)].astype(np.float32)
            if len(b) == len(x):
                x = np.logaddexp(x, b + (x.mean() - b.mean()) - rng.uniform(5, 25) / 10 * np.log(10))
        if CHANNEL:
            if rng.random() < 0.5:  # brighter or duller microphone, up to 8 dB across the band
                x = x + rng.uniform(-8, 8) * DB * np.linspace(-0.5, 0.5, sp.N_MELS, dtype=np.float32)
            if rng.random() < 0.25:  # phone-like band limits
                x[:, :rng.randrange(0, 5)] -= rng.uniform(10, 30) * DB
                x[:, sp.N_MELS - rng.randrange(0, 10):] -= rng.uniform(10, 30) * DB
            if rng.random() < 0.3:  # steady noise, 5-30 dB below the speech level
                shape = np.cumsum(np.array([rng.gauss(0, 0.3) for _ in range(sp.N_MELS)], np.float32))
                x = np.logaddexp(x, np.float32(np.percentile(x, 70)) - rng.uniform(5, 30) * DB + shape
                                 + np.array([rng.gauss(0, 0.5) for _ in range(len(x))], np.float32)[:, None])
            if rng.random() < 0.3:  # a short dropout in time
                t0 = rng.randrange(len(x))
                x[t0:t0 + rng.randrange(2, 8)] = float(x.min())
        if rng.random() < 0.5:
            f = rng.randrange(sp.N_MELS - 6)
            x[:, f:f + rng.randrange(1, 7)] = x.mean()
        pad = CROP - len(x)
        xs.append(np.pad(x, ((0, pad), (0, 0)), constant_values=float(x.min())))
        ys.append(np.pad(y, (0, pad)))
        ms.append(np.pad(m, (0, pad)))
    return ((np.stack(xs) - mu) / sd).astype(np.float32), np.stack(ys), np.stack(ms)


def batches(n, *args):
    """n batches, built a few ahead on a background thread while the network trains on the previous ones."""
    ready = queue.Queue(maxsize=4)

    def fill():
        try:
            for _ in range(n):
                ready.put(batch(*args))
        except Exception as e:  # handed to the training loop, which would otherwise wait forever
            ready.put(e)
    threading.Thread(target=fill, daemon=True).start()
    for _ in range(n):
        item = ready.get()
        if isinstance(item, Exception):
            raise item
        yield tuple(torch.from_numpy(a).to(DEVICE, non_blocking=True) for a in item)


def main():
    data, out = pickle.loads(Path(sys.argv[1]).read_bytes()), Path(sys.argv[2])
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    torch.manual_seed(SEED)
    rng = random.Random(SEED)
    train = [x for x in data if x["split"] == "train"]
    if REAL_PART:  # how results scale with the number of real recordings
        k, n = map(int, REAL_PART.split("/"))
        train = [x for x in train if x["source"] != "real"
                 or int(hashlib.md5(x["group"].encode()).hexdigest(), 16) % n == k]
        real = [x for x in train if x["source"] == "real"]
        print(f"real share {REAL_PART}: {len({x['group'] for x in real})} recordings, "
              f"{sum(len(x['pos']) for x in real)} swear words", flush=True)
    val = [x for x in data if x["split"] == "val"]
    allx = np.concatenate([x["feats"] for x in train[::5]]).astype(np.float32)
    mu, sd = allx.mean(0), allx.std(0) + 1e-3
    pools = {s: [(x["feats"], *labels(x)) for x in train if x["source"] == s] for s in SOURCE_SHARE}
    babble = [x["feats"] for x in train if x["source"] == "everyday"]
    print({s: len(p) for s, p in pools.items()}, f"val {len(val)} clips, skip {SKIP}, width {width}, shares "
          f"{SOURCE_SHARE}, channel {CHANNEL}, select {SELECT}, seed {SEED}, on {DEVICE}", flush=True)
    net = Net(width).to(DEVICE)
    print(f"{sum(p.numel() for p in net.parameters())} parameters", flush=True)
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=EPOCHS * STEPS)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none", pos_weight=torch.tensor(POS_WEIGHT, device=DEVICE))
    vset = [(torch.tensor(((x["feats"] - mu) / sd).astype(np.float32).T[None], device=DEVICE),
             *(torch.from_numpy(a).to(DEVICE) for a in labels(x))) for x in val
            if SELECT == "all" or x["source"] != "synthetic"]
    feed = batches(EPOCHS * STEPS, rng, pools, babble, mu, sd)
    best, t0 = 1e9, time.time()
    for epoch in range(EPOCHS):
        net.train()
        for _ in range(STEPS):
            x, y, m = next(feed)
            logits = net(x.transpose(1, 2))
            loss = (loss_fn(logits, y) * m).sum() / m.sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
        net.eval()
        with torch.no_grad():
            tot = cnt = 0.0
            for x, y, m in vset:
                lv = loss_fn(net(x)[0], y) * m
                tot, cnt = tot + lv.sum(), cnt + m.sum()
        vloss = float(tot / cnt)
        if vloss < best:
            best = vloss
            export(net, mu, sd, out, val[0]["feats"].astype(np.float32))
        print(f"epoch {epoch + 1}: train loss {loss.item():.4f}, val loss {vloss:.4f} (best {best:.4f}), "
              f"{time.time() - t0:.0f}s", flush=True)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
