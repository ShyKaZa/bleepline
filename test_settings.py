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


if __name__ == "__main__":
    test_live_engine_defaults()
