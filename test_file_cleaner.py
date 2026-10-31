"""
Builds a short test video (solid frames + TTS speech with profanity), runs the
file cleaner on it, and checks: video packets copied untouched, duration kept,
each banned word muted in the output audio, clean speech left intact.

    python test_file_cleaner.py [model_size]
"""
import sys
import tempfile
from pathlib import Path

import av
import numpy as np

import integration_test as it
from app.file_cleaner import clean_file

RATE = 48000
FPS = 30


def make_test_video(path: Path, speech: np.ndarray):
    seconds = len(speech) / RATE
    with av.open(str(path), "w") as out:
        v = out.add_stream("mpeg4", rate=FPS)
        v.width, v.height, v.pix_fmt = 320, 240, "yuv420p"
        a = out.add_stream("aac", rate=RATE, layout="mono")
        for i in range(int(seconds * FPS)):
            img = np.full((240, 320, 3), (i * 3) % 255, np.uint8)
            for p in v.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(p)
        for start in range(0, len(speech), 1024):
            chunk = speech[start:start + 1024][None, :]
            frame = av.AudioFrame.from_ndarray(chunk.astype(np.float32), format="fltp", layout="mono")
            frame.sample_rate, frame.pts = RATE, start
            for p in a.encode(frame):
                out.mux(p)
        for s in (v, a):
            for p in s.encode(None):
                out.mux(p)


def read_audio(path: Path) -> np.ndarray:
    with av.open(str(path)) as inp:
        rs = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        return np.concatenate([o.to_ndarray().reshape(-1) for f in inp.decode(audio=0) for o in rs.resample(f)])


def video_packets(path: Path) -> list[bytes]:
    with av.open(str(path)) as inp:
        return [bytes(p) for p in inp.demux(video=0) if p.size]


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "base.en"
    tmp = Path(tempfile.gettempdir())
    wav = tmp / "voiceguard_test_phrase.wav"
    if not wav.exists():
        it.synthesize(wav)
    speech = it.load_wav(wav, RATE)
    src = tmp / "voiceguard_test_video.mp4"
    make_test_video(src, speech)

    stages = []
    dst, catches = clean_file(src, model_size=model,
                              progress=lambda f, s: stages.append(s), log=print)
    print("output:", dst.name, "| catches:", catches)
    print("report:", (dst.with_name(dst.stem + " - muted words.txt")).read_text().strip().replace("\n", " | "))

    assert video_packets(src) == video_packets(dst), "video stream was modified"
    orig, clean = read_audio(src), read_audio(dst)
    print(f"audio length: {len(orig) / RATE:.2f}s -> {len(clean) / RATE:.2f}s")
    assert abs(len(orig) - len(clean)) < RATE * 0.1

    from better_profanity import profanity
    from app.detector import load_model
    m, _, _ = load_model(model)
    segs, _ = m.transcribe(it.load_wav(wav, 16000), language="en", word_timestamps=True)
    ok = total = 0
    for w in (w for s in segs for w in s.words):
        word = it.clean(w.word)
        a, b = int(w.start * RATE), int(w.end * RATE)
        level = np.abs(clean[a:b]).mean() / max(np.abs(orig[a:b]).mean(), 1e-6)
        if profanity.contains_profanity(word):
            total += 1
            ok += level < 0.05
            print(f"  {word:10s} {w.start:5.2f}s  remaining level {level * 100:5.1f}%")
    intact = np.abs(clean[: int(1.5 * RATE)]).mean() / np.abs(orig[: int(1.5 * RATE)]).mean()
    print(f"{ok}/{total} banned words muted; clean speech kept at {intact * 100:.0f}%")
    assert ok == total and intact > 0.9


if __name__ == "__main__":
    main()
