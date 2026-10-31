"""What Bleepline has put on this PC, and removing it again. Standard library only."""
import shutil
from pathlib import Path

from . import setup_tasks as st
from . import winsetup
from .paths import APP_DIR, CUDA_DIR, CUSTOM_WORDS_PATH, DATA_DIR, IS_FROZEN, SETTINGS_PATH, model_dir
from .wordlists import ALLOWED_PATH, WORDLISTS_DIR

SOUNDS_DIR = DATA_DIR / "sounds"  # same folder as censor_sound.SOUNDS_DIR; not imported to stay numpy-free


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human_size(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


def storage_items() -> list[tuple[str, str, int]]:
    """(key, label, bytes) for each thing Bleepline downloaded or created that is present."""
    items = []
    for name in st.installed_models():
        kind = "Speech model" if name in st.MODEL_CATALOG else "Your model"
        items.append((f"model:{name}", f"{kind} {name}", dir_size(model_dir(name))))
    if CUDA_DIR.exists():
        items.append(("gpu", "GPU runtime (NVIDIA cuBLAS)", dir_size(CUDA_DIR)))
    if st.obs_plugin_installed():
        items.append(("obs", "OBS plugin", dir_size(st.obs_plugin_target())))
    if WORDLISTS_DIR.exists() and any(WORDLISTS_DIR.iterdir()):
        items.append(("wordlists", "Imported and downloaded word lists", dir_size(WORDLISTS_DIR)))
    if SOUNDS_DIR.exists() and any(SOUNDS_DIR.iterdir()):
        items.append(("sounds", "Custom censor sounds", dir_size(SOUNDS_DIR)))
    settings = [p for p in (SETTINGS_PATH, CUSTOM_WORDS_PATH, ALLOWED_PATH) if p.exists()]
    if settings:
        items.append(("settings", "Settings, your words and allow list", sum(p.stat().st_size for p in settings)))
    return items


def remove_item(key: str) -> None:
    if key.startswith("model:"):
        shutil.rmtree(model_dir(key.split(":", 1)[1]))
    elif key == "gpu":
        shutil.rmtree(CUDA_DIR)
    elif key == "obs":
        remove_obs_plugin()
    elif key == "wordlists":
        shutil.rmtree(WORDLISTS_DIR)
    elif key == "sounds":
        shutil.rmtree(SOUNDS_DIR)
    elif key == "settings":
        for p in (SETTINGS_PATH, CUSTOM_WORDS_PATH, ALLOWED_PATH):
            p.unlink(missing_ok=True)


def remove_obs_plugin() -> None:
    plugin_dir = st.obs_plugin_target().parents[2]  # ...\obs-studio\plugins\bleepline-filter
    for folder in (plugin_dir, st.legacy_obs_plugin_dir()):
        if folder.exists():
            shutil.rmtree(folder)
    for parent in (plugin_dir.parent, plugin_dir.parent.parent):  # drop plugins\ and obs-studio\ only if now empty
        try:
            parent.rmdir()
        except OSError:
            break


def remove_all_downloads() -> None:
    """Everything except the app itself: models, GPU runtime, OBS plugin, settings, logs."""
    for key, _, _ in storage_items():
        remove_item(key)
    for leftover in (DATA_DIR / "models", DATA_DIR / "runtime", DATA_DIR / "selftest.log"):
        if leftover.is_dir():
            shutil.rmtree(leftover)
        else:
            leftover.unlink(missing_ok=True)


def is_installed_copy() -> bool:
    """True when this exe is the copy the installer registered (vs. the portable zip or source)."""
    d = winsetup.installed_dir()
    return IS_FROZEN and d is not None and d.resolve() == APP_DIR.resolve()


def uninstall(remove_data: bool, remove_plugin: bool) -> None:
    winsetup.remove_shortcuts()
    winsetup.unregister()
    if remove_plugin and st.obs_plugin_installed():
        remove_obs_plugin()
    if remove_data:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    if IS_FROZEN:
        winsetup.schedule_folder_delete(APP_DIR)
