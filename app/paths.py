import os
import sys
from pathlib import Path

IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    # Packaged exe may live in Program Files (read-only), so keep data per-user.
    APP_DIR = Path(sys.executable).resolve().parent
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Bleepline"
    _LEGACY_DATA_DIR = DATA_DIR.with_name("VoiceGuard")  # versions before 1.1 were called Voice Guard
    if _LEGACY_DATA_DIR.is_dir() and not DATA_DIR.exists():
        try:
            _LEGACY_DATA_DIR.rename(DATA_DIR)  # keeps models, settings and word lists
        except OSError:
            pass
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = APP_DIR

ASSETS_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "assets"  # icon and logo (bundled inside the exe)
MODELS_DIR = DATA_DIR / "models"
CUDA_DIR = DATA_DIR / "runtime" / "cuda"
SETTINGS_PATH = DATA_DIR / "settings.json"
CUSTOM_WORDS_PATH = DATA_DIR / "custom_words.txt"
REQUIREMENTS_PATH = APP_DIR / "requirements.txt"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def model_dir(size: str) -> Path:
    return MODELS_DIR / size


def cuda_dll_dirs() -> list[Path]:
    dirs = [CUDA_DIR]
    nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs += [nvidia / "cublas" / "bin", nvidia / "cudnn" / "bin"]
    return [d for d in dirs if d.is_dir()]


def ensure_cuda_on_path() -> None:
    # ctranslate2 resolves cuBLAS through PATH at first GPU use, not via os.add_dll_directory.
    current = os.environ.get("PATH", "").split(os.pathsep)
    for d in cuda_dll_dirs():
        if str(d) not in current:
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
