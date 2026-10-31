"""
End-to-end test of the real engine code path (input callback -> buffer ->
Whisper worker thread -> mutes -> output callback) with real-time pacing,
using Windows TTS speech containing known profanity. No audio hardware.

    python integration_test.py [delay_ms] [model_size]
"""
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
from better_profanity import profanity

from app.audio_engine import AudioEngine

RATE = 48000
BLOCK = 480
PHRASE = ("Hey man listen, that is such bullshit honestly, you are being a fucking idiot right now, "
          "what a bitch move dude. Okay whatever, shit happens, let us just play.")


def synthesize(path: Path):
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{path}');"
        f"$s.Speak('{PHRASE}');$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def load_wav(path: Path, rate: int) -> np.ndarray:
    w = wave.open(str(path), "rb")
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(len(a) * rate / w.getframerate())
    return np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(a)), a).astype(np.float32)


def clean(word: str) -> str:
    return word.strip(" .,!?").lower()


def main():
    delay_ms = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    model_size = sys.argv[2] if len(sys.argv) > 2 else "base.en"

    wav = Path(tempfile.gettempdir()) / "bleepline_test_phrase.wav"
    if not wav.exists():
        synthesize(wav)
    speech = load_wav(wav, RATE)
    full = np.concatenate([speech, np.zeros(RATE * 2, np.float32)])

    catches = []
    eng = AudioEngine(delay_ms=delay_ms, model_size=model_size, samplerate=RATE,
                      on_catch=lambda w, late: catches.append((w, late)))
    print(f"model={model_size} device={eng.device} {eng.device_note} delay={delay_ms}ms")

    segs, _ = eng.model.transcribe(load_wav(wav, 16000), language="en", word_timestamps=True)
    targets = [(clean(w.word), w.start, w.end) for s in segs for w in s.words
               if profanity.contains_profanity(clean(w.word))]

    eng.detector.start()

    outs, t0 = [], time.time()
    for i in range(len(full) // BLOCK):
        eng._in_callback(np.repeat(full[i * BLOCK:(i + 1) * BLOCK, None], 2, axis=1), BLOCK, None, None)
        out = np.zeros((BLOCK, 2), np.float32)
        eng._out_callback(out, BLOCK, None, None)
        outs.append(out[:, 0].copy())
        time.sleep(max(0.0, t0 + (i + 1) * BLOCK / RATE - time.time()))
    eng.detector.stop()

    out = np.concatenate(outs)
    print(f"avg inference {eng.detector.infer_ms:.0f}ms\n")
    print(f"{'word':10s} {'spoken':>12s} {'len':>6s} {'muted':>7s}  verdict")
    caught = 0
    for text, s, e in targets:
        a, b = int(s * RATE), int(e * RATE)
        muted = 1 - np.abs(out[a + eng.delay:b + eng.delay]).mean() / np.abs(speech[a:b]).mean()
        ok = muted > 0.95
        caught += ok
        print(f"{text:10s} {s:5.2f}-{e:5.2f}s {int((e - s) * 1000):5d}ms {muted * 100:6.1f}%  "
              f"{'CAUGHT' if ok else 'LEAKED'}")
    print(f"\n{caught}/{len(targets)} fully muted (>95%)   catch log (word, ms late): {catches}")


if __name__ == "__main__":
    main()
