import json
from dataclasses import asdict, dataclass, field, fields

from .paths import CUSTOM_WORDS_PATH, SETTINGS_PATH

MODES = ("obs", "stream", "listen", "custom")
DEVICE_FIELDS = ("desktop_device", "monitor_device", "input_device", "output_device")


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
    live_model: str = "tiny.en"  # fastest reaction; measured 100% at 500-600 ms on real devices
    file_model: str = "base.en"  # no time pressure when cleaning a recording, so favour accuracy
    language: str = "auto"  # Whisper code; English-only models ignore it
    asr_device: str = "cpu"  # live recognition on "cpu" (~3-7% CPU, GPU stays free for games) or "gpu"
    min_word_prob: float = 0.15  # sensitivity: lower = mute on less certain recognitions
    early_mute: bool = False  # mute words that start like a banned word, undo if they turn out harmless
    use_builtin_list: bool = True
    disabled_lists: list = field(default_factory=list)
    catch_censored: bool = True  # treat words the speech model writes as f**k as banned
    auto_start: bool = False
    start_minimized: bool = False
    theme: str = "dark"  # "dark", "light" or "system"

    @staticmethod
    def load() -> "Settings":
        try:
            data = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return Settings()
        known = {f.name for f in fields(Settings)}
        clean = {k: v for k, v in data.items() if k in known}
        for key in DEVICE_FIELDS:
            if not isinstance(clean.get(key), (str, type(None))):
                clean.pop(key, None)  # older versions stored device indices
        if clean.get("mode") not in MODES:
            clean["mode"] = "listen" if data.get("mode") == "incoming" else "obs"
        return Settings(**clean)

    def save(self) -> None:
        SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2))


def load_custom_words() -> list[str]:
    if CUSTOM_WORDS_PATH.exists():
        return [w.strip() for w in CUSTOM_WORDS_PATH.read_text(encoding="utf-8").splitlines() if w.strip()]
    return []


def save_custom_words(words: list[str]) -> None:
    CUSTOM_WORDS_PATH.write_text("\n".join(words), encoding="utf-8")
