"""Settings saved by older versions: the old live defaults move to the new ones once, choices are kept."""
import json
import tempfile
from pathlib import Path

from app import config


def load(saved: dict) -> config.Settings:
    config.SETTINGS_PATH.write_text(json.dumps(saved), encoding="utf-8")
    return config.Settings.load()


def test_live_engine_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        config.SETTINGS_PATH = Path(tmp) / "settings.json"
        s = load({"live_model": "tiny.en", "asr_device": "cpu"})  # 1.2 saved its defaults like this
        assert (s.live_model, s.asr_device) == ("base.en", "split")
        s = load({"live_model": "small.en", "asr_device": "gpu"})  # picked by hand in 1.2: kept
        assert (s.live_model, s.asr_device) == ("small.en", "gpu")
        s = load({"live_model": "tiny.en", "asr_device": "cpu", "engine": 2})  # picked since: kept
        assert (s.live_model, s.asr_device) == ("tiny.en", "cpu")
        config.Settings(live_model="tiny.en", asr_device="cpu").save()
        s = config.Settings.load()
        assert (s.live_model, s.asr_device) == ("tiny.en", "cpu")
        assert (config.Settings().live_model, config.Settings().asr_device) == ("base.en", "split")
    print("settings: OK")


def test_wrong_types_get_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        config.SETTINGS_PATH = Path(tmp) / "settings.json"
        s = load({"delay_ms": "800", "auto_start": 1, "censor_volume": True, "builtin_lists": "slurs",
                  "disabled_lists": [3], "asr_device": "npu", "theme": "pink", "desktop_device": 4, "engine": 2})
        d = config.Settings()
        for key in ("delay_ms", "auto_start", "censor_volume", "builtin_lists", "disabled_lists", "asr_device",
                    "theme", "desktop_device"):
            assert getattr(s, key) == getattr(d, key), key
        s = load({"delay_ms": 800, "censor_volume": 1, "check_updates": None, "censor_sound": "a.wav",
                  "disabled_lists": ["x.txt"], "mute_mode": "beep", "engine": 2})  # all valid: kept
        assert (s.delay_ms, s.censor_volume, s.check_updates, s.censor_sound, s.disabled_lists, s.mute_mode) == \
            (800, 1, None, "a.wav", ["x.txt"], "beep")
    print("settings types: OK")


if __name__ == "__main__":
    test_live_engine_defaults()
    test_wrong_types_get_defaults()
