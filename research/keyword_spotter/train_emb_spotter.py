"""Train the small causal head of speech_embedding.EmbSpotter on make_emb_dataset.py's embeddings; the embedding
network itself stays as Google trained it. Labels, sources and val selection as train_spotter.py (same SPOT_*
settings). PyTorch, dev only; exported to NumPy with batch norm folded in.

    python train_emb_spotter.py <emb dataset.pkl> <out.npz> [width]
"""
import os
import pickle
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "torchlib"))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402

import speech_embedding as se  # noqa: E402
from train_spotter import BATCH, EPOCHS, POS_WEIGHT, SELECT, SKIP, SOURCE_SHARE, STEPS, labels  # noqa: E402

CROP = 50  # embeddings per training example (4 s)
NOISE = float(os.environ.get("SPOT_NOISE", "0.3"))  # Gaussian noise on the normalized embeddings


class Net(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.inp = nn.Conv1d(96, width, 1)
        self.inp_bn = nn.BatchNorm1d(width)
        self.convs = nn.ModuleList(nn.Conv1d(width, width, 3, dilation=d) for d in se.HEAD_DILATIONS)
        self.bns = nn.ModuleList(nn.BatchNorm1d(width) for _ in se.HEAD_DILATIONS)
        self.in_drop, self.drop = nn.Dropout(0.1), nn.Dropout(0.2)
        self.out = nn.Conv1d(width, 1, 1)

    def forward(self, x):  # (B, 96, T) -> (B, T) logits, causal
        h = torch.relu(self.inp_bn(self.inp(self.in_drop(x))))
        for conv, bn, d in zip(self.convs, self.bns, se.HEAD_DILATIONS):
            h = h + self.drop(torch.relu(bn(conv(nn.functional.pad(h, (2 * d, 0))))))
        return self.out(h)[:, 0]


def export(net, mu, sd, path, sample):
    def fold(conv_bias, bn):
        scale = (bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
        shift = (bn.bias - bn.running_mean * bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
        return scale, shift + conv_bias.detach().numpy() * scale
    p = {"head": np.ones(1), "mu": mu, "sd": sd, "in_w": net.inp.weight.detach().numpy()}
    p["in_scale"], p["in_shift"] = fold(net.inp.bias, net.inp_bn)
    for i, (conv, bn) in enumerate(zip(net.convs, net.bns)):
        p[f"c{i}_w"] = conv.weight.detach().numpy()
        p[f"c{i}_scale"], p[f"c{i}_shift"] = fold(conv.bias, bn)
    p["out_w"] = net.out.weight.detach().numpy()[0, :, 0]
    p["out_b"] = net.out.bias.detach().numpy()
    np.savez(path, **{k: np.asarray(v, np.float32) for k, v in p.items()})
    with torch.no_grad():
        ref = torch.sigmoid(net(torch.tensor(((sample - mu) / sd).astype(np.float32).T[None]))[0]).numpy()
    diff = float(np.abs(se.EmbSpotter(path).head(sample) - ref).max())
    assert diff < 1e-3, f"the NumPy head differs from the network by {diff}"


def batch(rng, pools, mu, sd):
    xs, ys, ms = [], [], []
    for _ in range(BATCH):
        source = rng.choices(list(SOURCE_SHARE), weights=list(SOURCE_SHARE.values()))[0]
        x, y, m = rng.choice(rng.choice(pools[source]))  # a clip, then one of its four sequences
        n = len(x)
        swears = np.flatnonzero(y)
        if len(swears) and rng.random() < 0.7:
            c = int(rng.choice(swears))
            lo = min(max(0, c - rng.randrange(CROP // 4, CROP - 2)), max(0, n - CROP))
        else:
            lo = rng.randrange(max(1, n - CROP + 1))
        x = (x[lo:lo + CROP].astype(np.float32) - mu) / sd
        x = x + np.float32(NOISE) * np.random.default_rng(rng.randrange(1 << 30)).standard_normal(x.shape, np.float32)
        pad = CROP - len(x)
        xs.append(np.pad(x, ((0, pad), (0, 0))))
        ys.append(np.pad(y[lo:lo + CROP], (0, pad)))
        ms.append(np.pad(m[lo:lo + CROP], (0, pad)))
    return np.stack(xs), np.stack(ys), np.stack(ms)


def main():
    data, out = pickle.loads(Path(sys.argv[1]).read_bytes()), Path(sys.argv[2])
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    torch.manual_seed(1)
    rng = random.Random(1)
    train = [x for x in data if x["split"] == "train"]
    val = [x for x in data if x["split"] == "val" and (SELECT == "all" or x["source"] != "synthetic")]
    allx = np.concatenate([x["phases"][0][1] for x in train[::3]]).astype(np.float32)
    mu, sd = allx.mean(0), allx.std(0) + 1e-3
    pools = {s: [[(e, *labels(x, t)) for t, e in x["phases"]] for x in train if x["source"] == s]
             for s in SOURCE_SHARE}
    print({s: len(p) for s, p in pools.items()}, f"val {len(val)} clips, skip {SKIP}, width {width}, shares "
          f"{SOURCE_SHARE}, select {SELECT}, noise {NOISE}", flush=True)
    net = Net(width)
    print(f"{sum(p.numel() for p in net.parameters())} parameters", flush=True)
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-2)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=EPOCHS * STEPS)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none", pos_weight=torch.tensor(POS_WEIGHT))
    vset = [(torch.tensor(((e.astype(np.float32) - mu) / sd).T[None]), *labels(x, t)) for x in val
            for t, e in x["phases"]]
    best, t0 = 1e9, time.time()
    for epoch in range(EPOCHS):
        net.train()
        for _ in range(STEPS):
            x, y, m = batch(rng, pools, mu, sd)
            logits = net(torch.tensor(x).transpose(1, 2))
            loss = (loss_fn(logits, torch.tensor(y)) * torch.tensor(m)).sum() / torch.tensor(m).sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
        net.eval()
        with torch.no_grad():
            tot = cnt = 0.0
            for x, y, m in vset:
                lv = loss_fn(net(x)[0], torch.tensor(y)) * torch.tensor(m)
                tot, cnt = tot + float(lv.sum()), cnt + float(m.sum())
        vloss = tot / cnt
        if vloss < best:
            best = vloss
            export(net, mu, sd, out, val[0]["phases"][0][1].astype(np.float32))
        print(f"epoch {epoch + 1}: train loss {loss.item():.4f}, val loss {vloss:.4f} (best {best:.4f}), "
              f"{time.time() - t0:.0f}s", flush=True)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
