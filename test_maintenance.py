"""Removal logic against a throwaway data folder (your real downloads and OBS are never touched)."""
import tempfile
from pathlib import Path

from app import maintenance, setup_tasks as st


def test_storage_and_removal():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "Bleepline"
        plugin = Path(tmp) / "ProgramData" / "obs-studio" / "plugins" / "bleepline-filter" / "bin" / "64bit" / "bleepline-filter.dll"
        for f, size in ((data / "models" / "tiny.en" / "model.bin", 1000), (data / "models" / "base.en" / "model.bin", 2000),
                        (data / "runtime" / "cuda" / "cublas64_12.dll", 3000), (data / "settings.json", 10),
                        (data / "custom_words.txt", 5), (data / "selftest.log", 5), (plugin, 50)):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x" * size)
        for model in ("tiny.en", "base.en"):  # a model counts as installed once these exist
            for name in ("config.json", "tokenizer.json"):
                (data / "models" / model / name).write_bytes(b"")

        maintenance.model_dir = st.model_dir = lambda size: data / "models" / size
        st.MODELS_DIR = data / "models"
        maintenance.CUDA_DIR = data / "runtime" / "cuda"
        maintenance.DATA_DIR = data
        maintenance.SETTINGS_PATH = data / "settings.json"
        maintenance.CUSTOM_WORDS_PATH = data / "custom_words.txt"
        maintenance.ALLOWED_PATH = data / "allowed_words.txt"
        maintenance.WORDLISTS_DIR = data / "wordlists"
        maintenance.SOUNDS_DIR = data / "sounds"
        st.obs_plugin_target = lambda: plugin

        bundled = Path(tmp) / "bundled.dll"  # the plugin this version ships
        st.bundled_obs_plugin = lambda: bundled
        bundled.write_bytes(b"x" * 50)
        assert st.obs_plugin_installed() and not st.obs_plugin_outdated()
        bundled.write_bytes(b"y" * 50)  # same size, other bytes: an older plugin is installed
        assert st.obs_plugin_outdated()
        st.install_obs_plugin(lambda t: None)
        assert not st.obs_plugin_outdated()
        plugin.write_bytes(b"x" * 50)

        items = {k: s for k, _, s in maintenance.storage_items()}
        assert items == {"model:tiny.en": 1000, "model:base.en": 2000, "gpu": 3000, "obs": 50, "settings": 15}, items

        maintenance.remove_item("model:tiny.en")
        assert not (data / "models" / "tiny.en").exists() and (data / "models" / "base.en").exists()
        maintenance.remove_item("obs")
        assert not plugin.exists() and not (Path(tmp) / "ProgramData" / "obs-studio").exists()  # empty parents tidied

        maintenance.remove_all_downloads()
        assert maintenance.storage_items() == []
        assert data.exists() and not any(data.iterdir()), list(data.iterdir())
        print("PASS storage report, single removal, OBS plugin update and removal, remove everything")


if __name__ == "__main__":
    test_storage_and_removal()
