"""Censor sounds: restart/loop behaviour, custom file import, and a custom sound in a cleaned recording."""
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

from app import censor_sound as cs


def tone_wav(path: Path, hz: float, seconds: float, rate=44100):
    t = np.arange(int(rate * seconds)) / rate
    pcm = (0.6 * np.sin(2 * np.pi * hz * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def peak_hz(x: np.ndarray, rate: int) -> float:
    return float(np.argmax(np.abs(np.fft.rfft(x))) * rate / len(x))


def test_voice_restarts_and_loops():
    clip = np.arange(1, 101, dtype=np.float32)  # recognisable ramp, 100 samples
    voice = cs.CensorVoice(clip)
    gain = np.ones(1000, np.float32)
    gain[100:350] = 0.0  # 250-sample mute: plays the clip 2.5 times
    gain[600:650] = 0.0  # second mute must start from the clip's beginning again
    parts = []
    for i in range(0, 1000, 64):  # rendered block by block, like the audio callback does
        block = gain[i:i + 64]
        rendered = voice.render(block)
        parts.append(rendered if rendered is not None else np.zeros(len(block), np.float32))
    out = np.concatenate(parts)
    assert out[100] == 1 and out[199] == 100 and out[200] == 1 and out[349] == 50  # loops, across blocks
    assert out[600] == 1 and out[649] == 50  # restarted
    assert not out[:100].any() and not out[350:600].any()
    print("PASS censor sound restarts at each mute and loops across audio blocks")


def test_custom_sound_in_cleaned_recording(model):
    import integration_test as it
    import test_file_cleaner as tfc
    from app.file_cleaner import clean_file

    with tempfile.TemporaryDirectory() as tmp:
        cs.SOUNDS_DIR = Path(tmp) / "sounds"
        src_sound = Path(tmp) / "my quack.wav"
        tone_wav(src_sound, 600, 0.3)
        name = cs.import_sound(src_sound)
        assert name == "my quack.wav" and abs(peak_hz(cs.load_sound(cs.SOUNDS_DIR / name, 48000), 48000) - 600) < 5

        wav = Path(tempfile.gettempdir()) / "bleepline_test_phrase.wav"
        speech = it.load_wav(wav, 48000)
        video = Path(tmp) / "clip.mp4"
        tfc.make_test_video(video, speech)
        dst, catches = clean_file(video, model_size=model, mute_mode="sound", censor_sound=name, censor_volume=0.8)
        out = tfc.read_audio(dst)
        freqs = [peak_hz(out[int(t * 48000):int(t * 48000) + 9600], 48000) for t, _ in catches]
        print("dominant frequency in each muted word:", [round(f) for f in freqs])
        assert len(catches) == 4 and all(abs(f - 600) < 15 for f in freqs)
        print("PASS custom sound file replaces every banned word in the cleaned recording")


if __name__ == "__main__":
    test_voice_restarts_and_loops()
    test_custom_sound_in_cleaned_recording(sys.argv[1] if len(sys.argv) > 1 else "base.en")
