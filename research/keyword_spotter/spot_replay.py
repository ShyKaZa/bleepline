"""The two-layer system on held-out real recordings: the fast spotter checks every 20 ms and mutes (undoably) as
soon as it fires; Whisper keeps those mutes when it confirms a banned word there, mutes what the spotter missed,
and the rest are taken back `timeout` s after the spotter's last firing.

Whisper's checks don't depend on the spotter, so they're recorded once per clip (live-loop timing, measured
inference times) into <cache dir> and reused. Run it with nothing else busy on the CPU the first time.

Swearing clips are scored like test_replay.py; everyday clips (no swearing at all) count false mutes per minute.

    python spot_replay.py <clips json> <clips dir> <negatives json> <negatives dir> <cache dir> <spotter.npz> ...
"""
import json
import os
import pickle
import sys
import time
from pathlib import Path

ARGS = [Path(a).resolve() for a in sys.argv[1:]]  # before kws_replay switches to the repo folder
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kws_replay as kr  # noqa: E402  (sets up the repo path)

import numpy as np  # noqa: E402

import lean_asr  # noqa: E402
import spotter as sp  # noqa: E402
import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app import wordlists  # noqa: E402
from make_real_dataset import is_test, load16  # noqa: E402

R = tr.RATE
STEP = 0.02  # spotter check interval
DELAYS = [200, 300, 400, 500, 600]
ASR_MODEL = os.environ.get("SPOT_ASR", "tiny.en")  # layer 2's Whisper model,
ASR_DEVICE = os.environ.get("SPOT_ASR_DEVICE", "cpu")  # "cpu" (2 threads, as the app), "gpu" or "split" (lean_asr)
STEP_MS = int(os.environ.get("SPOT_STEP_MS", det.MIN_STEP_MS))  # Whisper check interval


def whisper_checks(asr, matcher, audio, path: Path):
    if path.exists():
        return pickle.loads(path.read_bytes())
    checks, _ = kr.record(asr, matcher, lambda edge: 0.0, audio)
    checks = [(t, took, spans, placed) for t, took, spans, _, placed in checks]
    path.write_bytes(pickle.dumps(checks))
    return checks


def spot_mutes(wchecks, timeline, n, thr=0.8, persist=2, back=0.25, timeout=0.5, veto=False, matcher=None,
               use_spot=True, pad_before=0.2, pad_after=0.25):
    """Mutes as test_replay.score takes them, (applied, start, end, undone) in samples; plus spotter event counts."""
    edge, step = R * det.EDGE_MS // 1000, int(STEP * R)
    items = [(t + took, 0, t, spans, placed) for t, took, spans, placed in wchecks]  # Whisper: when results arrive
    if use_spot:
        ends, probs = timeline
        ts = np.arange(step, n + 1, step)
        idx = np.searchsorted(ends, ts * sp.SR // R, "right") - 1  # the newest output available at each check
        items += [(int(t), 1, int(t), float(probs[i]), None) for t, i in zip(ts, idx) if i >= 0]
    items.sort(key=lambda x: (x[0], x[1]))
    mutes, events, streak, seen = [], [], 0, []
    for now, kind, t, a, placed in items:
        if kind == 0:
            seen = placed
            for ws, we, tt in a:
                end = max(we, tt) + int(pad_after * R) if we > tt - edge else we + int(pad_after * R)
                mutes.append((now, ws - int(pad_before * R), end, None))
                for ev in events:
                    if ws < ev["end"] and we > ev["start"] and ev["confirmed"] is None:
                        ev["confirmed"] = now
            continue
        p = a
        if veto and p >= thr:  # Whisper already wrote the word under way as something that can't become a swear
            edge_words = [w for _, we, w in seen if we > t - int(0.35 * R)]
            if edge_words and all(not matcher.match_word(w) and not matcher.is_partial(w) for w in edge_words):
                p = 0.0
        streak = streak + 1 if p >= thr else 0
        if streak >= persist:
            ev = events[-1] if events and t - events[-1]["last"] <= int(0.3 * R) else None
            if ev is None:
                ev = {"start": t - (persist - 1) * step - int(back * R), "pieces": [], "confirmed": None}
                events.append(ev)
            ev["last"], ev["end"] = t, t + int(pad_after * R)
            ev["pieces"].append((now, ev["start"], ev["end"]))
    for ev in events:
        undo = ev["last"] + int(timeout * R)
        undone = None if ev["confirmed"] is not None and ev["confirmed"] <= undo else undo
        mutes += [(a, s, e, undone) for a, s, e in ev["pieces"]]
    return mutes, len(events), sum(ev["confirmed"] is None for ev in events)


def muted_regions(mutes, n, delay_ms):
    """(number of separate muted stretches, muted seconds) as heard, on audio that has no swearing at all."""
    delay = R * delay_ms // 1000
    gain = np.ones(n + 1, np.int8)
    for applied, s, e, undone in mutes:
        s = max(s, applied - delay, 0)
        if undone is not None:
            e = min(e, undone - delay)
        gain[s:max(s, min(e, n))] = 0
    starts = np.flatnonzero(np.diff(gain.astype(np.int16)) == -1)
    return len(starts) + (gain[0] == 0), float((gain[:n] == 0).sum()) / R


def load_spotter(path):
    if "head" in np.load(path).files:  # speech embedding + head
        sys.path.insert(0, str(Path(__file__).resolve().parent / "torchlib"))
        import speech_embedding as se
        return se.EmbSpotter(path)
    return sp.Spotter(path)


def cost_line(spot, a16):
    def core(us):
        return f"{us * 50 / 1e4:.1f}% of one CPU core"
    if isinstance(spot, sp.Spotter):
        us = check_cost(spot, a16)
        return f"{us:.0f} microseconds per check = {core(us)}"
    window, stream = spot.check_cost(a16)
    return (f"{window:.0f} microseconds per check recomputing the embedding window = {core(window)}; "
            f"{stream:.0f} streaming = {core(stream)}")


def check_cost(spot, a16):
    """Microseconds per 20 ms check: features for the new audio, then the network over its look-back."""
    feats = sp.features(a16[: sp.SR * 2])
    t0, k = time.perf_counter(), 500
    for j in range(k):
        new = a16[j * 320: j * 320 + sp.WIN + sp.HOP]
        sp.features(new)
        spot.probs(feats[j % 100: j % 100 + sp.CONTEXT])
    return (time.perf_counter() - t0) / k * 1e6


def main():
    clips_json, clips, neg_json, neg_dir, cache, *spotters = ARGS
    cache.mkdir(parents=True, exist_ok=True)
    matcher = wordlists.WordMatcher([w for k in wordlists.DEFAULT_BUILTIN for w in wordlists.builtin_words(k)]
                                    + ["crap*", "piss*"])
    swear = [c for c in json.loads(clips_json.read_text(encoding="utf-8"))
             if c["test"] and any(matcher.match_word(w) for w, _, _ in c["words"])]
    everyday = [c for c in json.loads(neg_json.read_text(encoding="utf-8")) if is_test(c["id"])]
    everyday = everyday[:int(os.environ.get("SPOT_EVERYDAY", len(everyday)))]
    asr = lean_asr.load(ASR_MODEL, matcher, ASR_DEVICE)  # as the app unless SPOT_ASR_* say otherwise
    setup = f"{lean_asr.describe(ASR_DEVICE)}, checks every {STEP_MS} ms"
    det.MIN_STEP_MS = STEP_MS  # read by kws_replay.record
    data, t0 = [], time.time()
    for kind, group, folder in (("swear", swear, clips), ("everyday", everyday, neg_dir)):
        for c in group:
            a16 = load16(folder / c["file"])
            audio = np.interp(np.arange(len(a16) * 3) / 3, np.arange(len(a16)), a16).astype(np.float32)
            ref = [(w, s, e) for w, s, e in c.get("words", [])]
            checks = whisper_checks(asr, matcher, audio, cache / (c["file"] + ".pkl"))
            data.append((kind, a16, audio, ref, checks))
    minutes = sum(len(a) for k, a, *_ in data if k == "everyday") / sp.SR / 60
    took = np.array([ch[1] for *_, checks in data for ch in checks]) / R * 1000
    busy = took.sum() / 1000 / sum(len(a) / sp.SR for _, a, *_ in data)
    print(f"{len(swear)} held-out swearing clips ({sum(1 for c in swear for w, _, _ in c['words'] if matcher.match_word(w))}"
          f" swear words), {len(everyday)} everyday clips ({minutes:.1f} min); Whisper {ASR_MODEL} on {setup}: "
          f"checks take {np.median(took):.0f} ms (95% within {np.percentile(took, 95):.0f} ms), busy {busy * 100:.1f}% "
          f"of the time; ready in {time.time() - t0:.0f}s", flush=True)

    def show(name, cfg, timelines):
        lines, n_ev, n_wrong = [], 0, 0
        results, everyday_mutes = [], []
        for (kind, a16, audio, ref, checks), timeline in zip(data, timelines):
            mutes, ev, wrong = spot_mutes(checks, timeline, len(audio), matcher=matcher, **cfg)
            n_ev, n_wrong = n_ev + ev, n_wrong + wrong
            (results if kind == "swear" else everyday_mutes).append((audio, ref, mutes))
        print(f"{name}: {n_ev} spotter firings ({n_wrong} not confirmed)")
        for d in DELAYS:
            hits = n = 0
            fracs, cuts = [], []
            for audio, ref, mutes in results:
                h, k, f, c = tr.score(mutes, ref, matcher, len(audio), d)
                hits, n, fracs, cuts = hits + h, n + k, fracs + f, cuts + c
            regions = [muted_regions(m, len(a), d) for a, _, m in everyday_mutes]
            per_min = sum(r[0] for r in regions) / max(minutes, 1e-9)
            secs = sum(r[1] for r in regions) / max(minutes * 60, 1e-9)
            print(f"  {d:4d} ms: {hits:3d}/{n} fully muted, {np.mean(fracs) * 100:3.0f}% of swear audio | "
                  f"{len(cuts):3d} innocent words cut | everyday talk: {per_min:4.2f} false mutes/min, "
                  f"{secs * 100:4.2f}% muted")

    show("Whisper only (today)", dict(use_spot=False), [(np.zeros(0, int), np.zeros(0))] * len(data))
    long16 = np.concatenate([a16 for _, a16, *_ in data[:10]])  # over 10 s, for the cost measurements
    for path in spotters:
        spot = load_spotter(path)
        timelines = [spot.timeline(a16) for _, a16, *_ in data]
        print(f"\n== {path.stem}: {cost_line(spot, long16)}", flush=True)
        for thr in (0.6, 0.8, 0.9):
            for persist in (2, 3):
                show(f"+ spotter thr {thr} x{persist}", dict(thr=thr, persist=persist), timelines)
        for thr in (0.6, 0.8):
            show(f"+ spotter thr {thr} x2 + Whisper veto", dict(thr=thr, persist=2, veto=True), timelines)


if __name__ == "__main__":
    main()
