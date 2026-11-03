import json
import os
import typing
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import CUSTOM_WORDS_PATH, SETTINGS_PATH

MODES = ("obs", "stream", "listen", "custom")
DEFAULT_BUILTIN = ("strong", "slurs", "sexual")  # built-in word lists on by default (see wordlists.BUILTIN_LISTS)
CHOICES = {"asr_device": ("split", "cpu", "gpu"), "mute_mode": ("silence", "beep", "sound"),
           "theme": ("dark", "light", "system")}


@dataclass
class Settings:
    mode: str = "obs"
    # Device names, not indices: indices shift whenever a device is added.
    desktop_device: str | None = None  # stream mode: the device you listen on; its audio is captured
    monitor_device: str | None = None  # listen mode: your headset
    input_device: str | None = None  # custom mode
    output_device: str | None = None  # custom mode
    delay_ms: int = 600
    mute_pad_before_ms: int = 200
    mute_pad_after_ms: int = 250
    mute_mode: str = "silence"  # "silence", "beep" or "sound" (custom file)
    beep_hz: int = 1000
    censor_volume: float = 0.4
    censor_sound: str | None = None  # file name in the sounds folder
    live_model: str = "base.en"  # caught the most at 300-600 ms on real recordings (research/keyword_spotter)
    file_model: str = "base.en"  # no time pressure when cleaning a recording, so favour accuracy
    language: str = "auto"  # Whisper code; English-only models ignore it
    asr_device: str = "split"  # live recognition: "split" (GPU + CPU, see fast_asr), "cpu" or "gpu"
    min_word_prob: float = 0.15  # sensitivity: lower = mute on less certain recognitions
    early_mute: bool = False  # mute words that start like a banned word, undo if they turn out harmless
    builtin_lists: list = field(default_factory=lambda: list(DEFAULT_BUILTIN))  # wordlists.BUILTIN_LISTS keys
    disabled_lists: list = field(default_factory=list)
    catch_censored: bool = True  # treat words the speech model writes as f**k as banned
    auto_start: bool = False
    start_minimized: bool = False
    theme: str = "dark"  # "dark", "light" or "system"
    check_updates: bool | None = None  # None: not chosen in the app yet, so the installer's choice applies
    update_checked_at: float = 0.0  # time of the last successful check
    update_available: str = ""  # newest version seen then, and its release page
    update_url: str = ""
    engine: int = 2  # 1.2 and older saved none and had tiny.en on the CPU as the live defaults

    @staticmethod
    def load() -> "Settings":
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return Settings()
        known = {f.name: f.type for f in fields(Settings)}
        # A value of the wrong type (older versions stored device indices, or a hand edit) gets the default.
        clean = {k: v for k, v in data.items() if k in known and _fits(v, known[k])}
        for key, allowed in CHOICES.items():
            if clean.get(key, allowed[0]) not in allowed:
                del clean[key]
        if clean.get("mode") not in MODES:
            clean["mode"] = "listen" if data.get("mode") == "incoming" else "obs"
        if "engine" not in data:  # 1.2 and older: their live defaults move to the new ones once, other choices stay
            if clean.get("live_model") == "tiny.en":
                clean["live_model"] = "base.en"
            if clean.get("asr_device") == "cpu":
                clean["asr_device"] = "split"
        if "builtin_lists" not in data and data.get("use_builtin_list") is False:
            clean["builtin_lists"] = []  # 1.1 and older: the one built-in list was switched off
        return Settings(**clean)

    def save(self) -> None:
        write_atomic(SETTINGS_PATH, json.dumps(asdict(self), indent=2))


def _fits(value, kind) -> bool:
    """A JSON value of a field's type: true/false don't count as numbers, whole numbers do as decimals."""
    kinds = typing.get_args(kind) or (kind,)
    if float in kinds:
        kinds += (int,)
    if isinstance(value, bool) and bool not in kinds:
        return False
    if isinstance(value, list):
        return list in kinds and all(isinstance(v, str) for v in value)
    return isinstance(value, kinds)


def write_atomic(path: Path, text: str) -> None:
    """Write via a temporary file, so a crash or power cut mid-save never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_custom_words(words: list[str]) -> None:
    write_atomic(CUSTOM_WORDS_PATH, "\n".join(words))
