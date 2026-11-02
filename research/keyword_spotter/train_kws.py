"""Train the onset detector (NumPy MLP) on make_dataset.py's windows; report on held-out speakers.

    python train_kws.py data.npz [more.npz ...] kws.npz
"""
import sys

import numpy as np

import os
HIDDEN = int(os.environ.get("KWS_HIDDEN", 32))
EPOCHS, BATCH, LR = 25, 256, 1e-3
L2 = float(os.environ.get("KWS_L2", 1e-3))
DROPOUT, INPUT_NOISE = float(os.environ.get("KWS_DROPOUT", 0.3)), 0.1


class MLP:
    def __init__(self, n_in, rng):
        self.W1 = rng.normal(0, np.sqrt(2 / n_in), (n_in, HIDDEN)).astype(np.float32)
        self.b1 = np.zeros(HIDDEN, np.float32)
        self.W2 = rng.normal(0, np.sqrt(1 / HIDDEN), (HIDDEN, 1)).astype(np.float32)
        self.b2 = np.zeros(1, np.float32)

    def params(self):
        return [self.W1, self.b1, self.W2, self.b2]

    def forward(self, x):
        h = np.maximum(x @ self.W1 + self.b1, 0)
        return h, 1 / (1 + np.exp(-(h @ self.W2 + self.b2)[:, 0]))

    def grads(self, x, y, w, rng):
        x = x + rng.normal(0, INPUT_NOISE, x.shape).astype(np.float32)
        mask = (rng.random((len(x), HIDDEN)) > DROPOUT).astype(np.float32) / (1 - DROPOUT)
        h = np.maximum(x @ self.W1 + self.b1, 0) * mask
        p = 1 / (1 + np.exp(-(h @ self.W2 + self.b2)[:, 0]))
        d = ((p - y) * w)[:, None] / len(y)  # weighted binary cross-entropy
        gW2, gb2 = h.T @ d + L2 * self.W2, d.sum(0)
        dh = (d @ self.W2.T) * (h > 0) * mask
        gW1, gb1 = x.T @ dh + L2 * self.W1, dh.sum(0)
        return [gW1, gb1, gW2, gb2]


def bce(p, y, w):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p)) * w))


def onset_report(p, y, utt, thr):
    """Per swear word (a run of positive windows): how many 40 ms windows into it the detector first fires;
    and on sentences without any swear word, how many fire at all."""
    delays, missed, neg_utts, fired_neg = [], 0, 0, 0
    for u in np.unique(utt):
        idx = np.where(utt == u)[0]
        yy, pp = y[idx], p[idx]
        if (yy == 1).any():
            run_starts = [i for i in range(len(yy)) if yy[i] == 1 and (i == 0 or yy[i - 1] != 1)]
            for r in run_starts:
                k = r
                while k < len(yy) and yy[k] == 1 and pp[k] < thr:
                    k += 1
                if k < len(yy) and yy[k] == 1:
                    delays.append(k - r)
                else:
                    missed += 1
        else:
            neg_utts += 1
            fired_neg += bool((pp[yy == 0] >= thr).any())
    d = np.array(delays) * 40 + 60 + 40 * int(os.environ.get("KWS_SKIP", 0))  # ms after the word starts
    return (f"thr {thr:.2f}: caught {len(delays)}/{len(delays) + missed} swear words, first fire median "
            f"{np.median(d) if len(d) else float('nan'):.0f} ms after the word starts (p90 "
            f"{np.percentile(d, 90) if len(d) else float('nan'):.0f}); fired on {fired_neg}/{neg_utts} "
            f"look-alike sentences without swearing")


def main():
    parts = [np.load(f) for f in sys.argv[1:-1]]
    X = np.concatenate([d["X"] for d in parts]).astype(np.float32)
    y = np.concatenate([d["y"] for d in parts]).astype(np.float32)
    utt = np.concatenate([d["utt"] + k * 1_000_000 for k, d in enumerate(parts)])
    val = np.concatenate([d["val"] for d in parts])
    skip = int(os.environ.get("KWS_SKIP", 0))  # ignore the first N 40 ms windows of each swear word
    if skip:
        for k in range(len(y)):
            if y[k] == 1:
                run_pos = 0
                while k - run_pos - 1 >= 0 and utt[k - run_pos - 1] == utt[k] and y[k - run_pos - 1] in (1, -2):
                    run_pos += 1
                if run_pos < skip:
                    y[k] = -2
        y[y == -2] = -1
    keep = y >= 0
    X, y, utt, val = X[keep], y[keep], utt[keep], val[keep]
    mu, sd = X[~val].mean(0), X[~val].std(0) + 1e-5
    X = (X - mu) / sd
    Xt, yt, Xv, yv = X[~val], y[~val], X[val], y[val]
    pos_w = (yt == 0).sum() / max(1, (yt == 1).sum())
    wt, wv = np.where(yt == 1, pos_w, 1.0), np.where(yv == 1, pos_w, 1.0)
    print(f"train {len(yt)} windows ({int(yt.sum())} positive), val {len(yv)} ({int(yv.sum())} positive)")
    rng = np.random.default_rng(0)
    net = MLP(X.shape[1], rng)
    m = [np.zeros_like(q) for q in net.params()]
    v = [np.zeros_like(q) for q in net.params()]
    best, best_params, step = 1e9, None, 0
    for epoch in range(EPOCHS):
        order = rng.permutation(len(yt))
        for b in range(0, len(order), BATCH):
            i = order[b:b + BATCH]
            g = net.grads(Xt[i], yt[i], wt[i], rng)
            step += 1
            for q, gq, mq, vq in zip(net.params(), g, m, v):  # Adam
                mq[:] = 0.9 * mq + 0.1 * gq
                vq[:] = 0.999 * vq + 0.001 * gq * gq
                q -= LR * (mq / (1 - 0.9 ** step)) / (np.sqrt(vq / (1 - 0.999 ** step)) + 1e-8)
        loss = bce(net.forward(Xv)[1], yv, wv)
        if loss < best:
            best, best_params = loss, [q.copy() for q in net.params()]
        if epoch % 5 == 4:
            print(f"epoch {epoch + 1}: val loss {loss:.4f} (best {best:.4f})")
    net.W1, net.b1, net.W2, net.b2 = best_params
    pv = net.forward(Xv)[1]
    order = np.argsort(-pv)
    tp = np.cumsum(yv[order]) / yv.sum()
    fp = np.cumsum(1 - yv[order]) / (1 - yv).sum()
    print(f"held-out speakers, per window: AUC {np.trapezoid(tp, fp):.3f}")
    for thr in (0.5, 0.7, 0.8, 0.9, 0.95):
        print("  " + onset_report(pv, yv, utt[val], thr))
    np.savez(sys.argv[-1], W1=net.W1, b1=net.b1, W2=net.W2, b2=net.b2, mu=mu, sd=sd)
    print(f"saved {sys.argv[-1]}")


if __name__ == "__main__":
    main()
