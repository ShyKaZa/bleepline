"""The onset detector inside the live engine's loop, on test_replay.py's 12 clips (Windows voices: never trained on).

Every check runs the normal Whisper detection; the detector reads the same check's encoder output. When it fires,
an undoable mute starts `back` seconds before that moment; Whisper confirming a banned word there keeps it, and
otherwise it's taken back `timeout` seconds after the last firing (which only helps audio not yet played).

    python kws_replay.py kws.npz
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_BLOCKTIME", "0")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
os.chdir(REPO)

import ctranslate2  # noqa: E402
import numpy as np  # noqa: E402

import test_replay as tr  # noqa: E402
from app import detector as det  # noqa: E402
from app.fast_asr import NO_SPEECH_PROB, PAD_STEP, TOKENS_PER_SEC, FastTranscriber, Word  # noqa: E402

R = tr.RATE


class KwsTranscriber(FastTranscriber):
    """FastTranscriber.words, also keeping the encoder's edge features (as make_dataset.edge_features)."""

    edge = None

    def words(self, audio):
        self.edge = None
        if not self.has_speech(audio):
            return []
        frames = -(-max(len(audio), self.context) // PAD_STEP) * PAD_STEP // 160
        real = self.wm.feature_extractor(audio)[:, :frames]
        features = np.full((1, real.shape[0], frames), real.max() - 2.0, dtype=np.float32)
        features[0, :, : real.shape[1]] = real
        encoder_output = self.wm.model.encode(ctranslate2.StorageView.from_array(features), to_cpu=False)
        enc = np.array(encoder_output)[0]
        last = min(len(audio) // 320, enc.shape[0]) - 1
        groups = [enc[max(0, last - 7):last - 3], enc[last - 3:last + 1], enc[last + 1:last + 3]]
        self.edge = np.concatenate([g.mean(0) for g in groups]).astype(np.float16).astype(np.float32)
        tok = self._tokenizer(self.language)
        prompt = list(tok.sot_sequence) + [tok.no_timestamps]
        result = self.wm.model.generate(
            encoder_output, [prompt], beam_size=1, max_length=len(prompt) + 4 + int(TOKENS_PER_SEC * len(audio) / 16000),
            return_no_speech_prob=True, suppress_blank=True, suppress_tokens=[-1], no_repeat_ngram_size=3)[0]
        if result.no_speech_prob > NO_SPEECH_PROB:
            return []
        tokens = [t for t in result.sequences_ids[0] if t < tok.eot]
        if not tokens:
            return []
        alignment = self.wm.find_alignment(tok, [tokens], encoder_output, min(frames, len(audio) // 160))
        return [Word(w["word"], float(w["start"]), float(w["end"]), float(w["probability"]))
                for w in (alignment[0] if alignment else []) if w["word"].strip()]


def load_kws(path):
    k = np.load(path)

    def prob(x):
        h = np.maximum(((x - k["mu"]) / k["sd"]) @ k["W1"] + k["b1"], 0)
        return float(1 / (1 + np.exp(-(h @ k["W2"] + k["b2"])[0])))
    return prob


def record(asr, matcher, kws, audio):
    """The live loop's checks: [(t, took, whisper spans as (start, end) samples, detector probability)]."""
    win_max = int(det.WINDOW_SEC * R) // tr.BLOCK * tr.BLOCK
    step, t, infer_ms, checks, ktimes = R * det.MIN_STEP_MS // 1000, tr.BLOCK * 10, 0.0, [], []
    while t <= len(audio):
        lo = max(0, t - win_max)
        window, took = audio[lo:t], 0
        if float(np.sqrt(np.mean(window * window))) >= det.SILENCE_RMS:
            t0 = time.perf_counter()
            words = asr.words(det.resample_to_asr(window, R))
            k0 = time.perf_counter()
            p = kws(asr.edge) if asr.edge is not None else 0.0
            ktimes.append(time.perf_counter() - k0)
            ms = (time.perf_counter() - t0) * 1000
            infer_ms = ms if infer_ms == 0 else 0.8 * infer_ms + 0.2 * ms
            step = R * max(det.MIN_STEP_MS, int(infer_ms * 1.25)) // 1000
            took = int(ms * R / 1000)
            spans = [(lo + int(s * R), lo + int(e * R), t) for s, e, _ in
                     det.banned_spans(matcher, words, det.DEFAULT_MIN_PROB)]
            placed = [(lo + int(w.start * R), lo + int(w.end * R), w.word) for w in words]
            checks.append((t, took, spans, p, placed))
        t += -(-max(step, took) // tr.BLOCK) * tr.BLOCK
    return checks, ktimes


def mutes_for(checks, thr, back, timeout=0.5, use_kws=True, pad_before=0.2, pad_after=0.25, persist=1,
              veto=False, matcher=None):
    edge = R * det.EDGE_MS // 1000
    mutes, events, streak = [], [], []
    for t, took, spans, p, placed in checks:
        if veto and p >= thr:
            # Whisper already heard the word under way as something harmless that can't become a swear
            edge_words = [w for ws_, we_, w in placed if we_ > t - int(0.35 * R)]
            if edge_words and all(not matcher.match_word(w) and not matcher.is_partial(w) for w in edge_words):
                p = 0.0
        streak = streak + [t] if p >= thr else []
        now = t + took
        for ws, we, tt in spans:
            end = max(we, tt) + int(pad_after * R) if we > tt - edge else we + int(pad_after * R)
            mutes.append((now, ws - int(pad_before * R), end, None))
            for ev in events:
                if ws < ev["end"] and we > ev["start"]:
                    ev["confirmed"] = True
        if use_kws and len(streak) >= persist:
            ev = events[-1] if events and t - events[-1]["last"] <= 0.3 * R else None
            if ev is None:
                ev = {"start": streak[-persist] - int(back * R), "last": t, "end": t, "pieces": [], "confirmed": False}
                events.append(ev)
            ev["last"], ev["end"] = t, t + int(pad_after * R)
            ev["pieces"].append((now, ev["start"], ev["end"]))
    for ev in events:
        undone = None if ev["confirmed"] else ev["last"] + int(timeout * R)
        mutes += [(a, s, e, undone) for a, s, e in ev["pieces"]]
    return mutes, len(events), sum(not e["confirmed"] for e in events)


def prev_word_loss(mutes, ref, matcher, n, delay_ms):
    gain = np.ones(n, np.float32)
    for a, s, e, u in mutes:
        s = max(s, a - R * delay_ms // 1000, 0)
        if u is not None:
            e = min(e, u - R * delay_ms // 1000)
        gain[s:max(s, min(e, n))] = 0
    out = []
    for i, (w, s, e) in enumerate(ref):
        if matcher.match_word(w) and i > 0 and not matcher.match_word(ref[i - 1][0]):
            out.append(float(1 - gain[int(ref[i - 1][1] * R):int(ref[i - 1][2] * R)].mean()))
    return out


def main():
    kws = load_kws(sys.argv[1])
    model, _, _ = det.load_model("tiny.en", "cpu")
    asr = KwsTranscriber(model, "en", 4.0)
    matcher = tr.test_matcher()
    clips = tr.clips_with_reference()
    recorded, ktimes = [], []
    for audio, ref in clips:
        checks, kt = record(asr, matcher, kws, audio)
        recorded.append((audio, ref, checks))
        ktimes += kt
    print(f"detector cost: {np.mean(ktimes) * 1e6:.0f} microseconds per check")
    configs = [("Whisper only (today)", dict(thr=1.1, back=0.3, use_kws=False))]
    configs += [(f"+ detector thr {thr}{' + Whisper veto' if veto else ''}", dict(thr=thr, back=0.3, veto=veto, matcher=matcher))
                for veto in (False, True) for thr in (0.8, 0.9, 0.95)]
    for name, cfg in configs:
        results, prev, n_ev, n_wrong = [], [], 0, 0
        for audio, ref, checks in recorded:
            mutes, ev, wrong = mutes_for(checks, **cfg)
            results.append((audio, ref, mutes))
            prev += prev_word_loss(mutes, ref, matcher, len(audio), 400)
            n_ev, n_wrong = n_ev + ev, n_wrong + wrong
        tr.report(f"{name}: {n_ev} detector firings ({n_wrong} not confirmed by Whisper); word before a swear "
                  f"loses {np.mean(prev) * 100:.0f}% at 400 ms", results, matcher, [300, 400, 500, 600])


if __name__ == "__main__":
    main()
