"""What plays over a muted word: silence, a beep, or your own sound file."""
import shutil
from pathlib import Path

import numpy as np

from .paths import DATA_DIR

SOUNDS_DIR = DATA_DIR / "sounds"
MAX_SOUND_SEC = 5
BEEP_PEAK = 0.5  # beep amplitude at 100% volume
SOUND_TYPES = [("Audio files", "*.wav *.mp3 *.ogg *.flac *.m4a *.aac *.opus"), ("All files", "*.*")]


def sound_names() -> list[str]:
    return sorted(p.name for p in SOUNDS_DIR.iterdir() if p.is_file()) if SOUNDS_DIR.exists() else []


def load_sound(path: Path, rate: int) -> np.ndarray:
    """Decode any audio file to mono float32 at `rate`, at most MAX_SOUND_SEC, peak-normalised to 1.0."""
    import av

    chunks = []
    with av.open(str(path)) as inp:
        if not inp.streams.audio:
            raise ValueError("That file has no audio.")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        for frame in inp.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
            if sum(len(c) for c in chunks) >= rate * MAX_SOUND_SEC:
                break
    sound = np.concatenate(chunks)[: rate * MAX_SOUND_SEC] if chunks else np.zeros(0, np.float32)
    peak = float(np.abs(sound).max()) if len(sound) else 0.0
    if peak < 1e-4:
        raise ValueError("That sound is silent.")
    return (sound / peak).astype(np.float32)


def import_sound(source: Path) -> str:
    """Copy a sound into Bleepline's sounds folder after checking it decodes. Returns its name."""
    source = Path(source)
    load_sound(source, 48000)
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    dest, n = SOUNDS_DIR / source.name, 2
    while dest.exists():
        dest, n = SOUNDS_DIR / f"{source.stem} ({n}){source.suffix}", n + 1
    shutil.copy2(source, dest)
    return dest.name


def beep_loop(rate: int, hz: float) -> np.ndarray:
    """~1 s of sine that loops seamlessly (a whole number of periods)."""
    periods = max(1, round(hz))
    n = int(round(periods * rate / hz))
    return np.sin(2 * np.pi * periods * np.arange(n) / n).astype(np.float32)


def replacement_clip(mode: str, rate: int, beep_hz: float, volume: float, sound_name: str | None) -> np.ndarray:
    """The looping clip that replaces muted audio, already scaled by volume; empty for silence."""
    if mode == "beep":
        return beep_loop(rate, beep_hz) * (BEEP_PEAK * volume)
    if mode == "sound" and sound_name and (SOUNDS_DIR / sound_name).exists():
        return load_sound(SOUNDS_DIR / sound_name, rate) * volume
    return np.zeros(0, np.float32)


class CensorVoice:
    """Renders the replacement clip under a gain mask: it restarts at each mute and loops while it lasts."""

    def __init__(self, clip: np.ndarray):
        self.clip = clip
        self._pos = 0
        self._was_muted = False

    def render(self, gain: np.ndarray) -> np.ndarray | None:
        if len(self.clip) == 0:
            return None
        muted = gain < 0.999
        if not muted.any():
            self._was_muted = False
            self._pos = 0
            return None
        n = len(gain)
        idx = np.arange(n)
        starts = muted & ~np.concatenate(([self._was_muted], muted[:-1]))
        run_start = np.maximum.accumulate(np.where(starts, idx, -1))
        pos = np.where(run_start >= 0, idx - run_start, self._pos + idx)
        out = self.clip[pos % len(self.clip)] * (1.0 - gain)
        self._was_muted = bool(muted[-1])
        self._pos = int(pos[-1]) + 1 if self._was_muted else 0
        return out.astype(np.float32)
