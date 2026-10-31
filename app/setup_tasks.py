"""First-run setup steps. Standard library only, so this works before any packages are installed."""
import fnmatch
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

from .paths import APP_DIR, CUDA_DIR, IS_FROZEN, MODELS_DIR, REQUIREMENTS_PATH, cuda_dll_dirs, model_dir

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HEADERS = {"User-Agent": "Bleepline/1.0"}

REQUIRED_MODULES = ("numpy", "sounddevice", "faster_whisper", "better_profanity")

# name -> (Hugging Face repo, language, download size, best for). Live timings measured on an RTX 3070 Ti.
MODEL_CATALOG = {
    "tiny.en": ("Systran/faster-whisper-tiny.en", "English", "75 MB", "Live, lightest (~3% CPU)"),
    "base.en": ("Systran/faster-whisper-base.en", "English", "145 MB", "Live, most accurate (~7% CPU); recordings"),
    "small.en": ("Systran/faster-whisper-small.en", "English", "485 MB", "Recordings"),
    "distil-small.en": ("Systran/faster-distil-whisper-small.en", "English", "330 MB",
                        "Recordings, faster than small.en"),
    "tiny": ("Systran/faster-whisper-tiny", "Multilingual", "75 MB", "Live in any language (~3% CPU)"),
    "base": ("Systran/faster-whisper-base", "Multilingual", "145 MB",
             "Live in any language, more accurate; recordings"),
    "small": ("Systran/faster-whisper-small", "Multilingual", "485 MB", "Recordings in any language"),
    "medium": ("Systran/faster-whisper-medium", "Multilingual", "1.5 GB", "Recordings, high accuracy"),
    "large-v3-turbo": ("dropbox-dash/faster-whisper-large-v3-turbo", "Multilingual", "1.6 GB",
                       "Recordings, best accuracy (needs a GPU)"),
}
DEFAULT_DOWNLOADS = ("tiny.en", "base.en")
MODEL_FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*")
REQUIRED_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")

# ctranslate2 ships cuDNN itself; cuBLAS is the only missing piece for GPU mode.
CUBLAS_PACKAGE, CUBLAS_VERSION = "nvidia-cublas-cu12", "12.9.2.10"
CUBLAS_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")

VB_CABLE_URL = "https://vb-audio.com/Cable/"


# -- python packages (only relevant when running from source) ---------------

def missing_packages() -> list[str]:
    if IS_FROZEN:
        return []
    return [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]


def install_packages(log) -> None:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS_PATH)]
    log("Running: pip install -r requirements.txt")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=NO_WINDOW
    )
    for line in proc.stdout:
        log(line.rstrip())
    if proc.wait() != 0:
        raise RuntimeError("pip install failed, see the log above")


# -- GPU runtime ------------------------------------------------------------

def nvidia_gpu_name() -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    lines = out.stdout.strip().splitlines()
    return lines[0].strip() if out.returncode == 0 and lines else None


def gpu_runtime_installed() -> bool:
    return any(all((d / dll).exists() for dll in CUBLAS_DLLS) for d in cuda_dll_dirs())


def install_gpu_runtime(progress, log) -> None:
    meta = _get_json(f"https://pypi.org/pypi/{CUBLAS_PACKAGE}/{CUBLAS_VERSION}/json")
    wheel = next(u for u in meta["urls"] if u["filename"].endswith("win_amd64.whl"))
    log(f"Downloading NVIDIA cuBLAS ({wheel['size'] // 1_000_000} MB) from PyPI...")
    CUDA_DIR.mkdir(parents=True, exist_ok=True)
    archive = CUDA_DIR / "cublas.whl"
    _download(wheel["url"], archive, progress, wheel["digests"]["sha256"])
    log("Extracting DLLs...")
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():
            name = member.rsplit("/", 1)[-1]
            if name in CUBLAS_DLLS:
                with z.open(member) as src, open(CUDA_DIR / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    archive.unlink()
    if not gpu_runtime_installed():
        raise RuntimeError("cuBLAS DLLs were not found in the downloaded package")
    log("GPU runtime installed.")


# -- speech model -------------------------------------------------------------

def model_installed(name: str) -> bool:
    d = model_dir(name)
    return all((d / f).exists() for f in REQUIRED_MODEL_FILES)


def installed_models() -> list[str]:
    """Catalog models that are downloaded, then any other valid model folders (your own)."""
    names = [n for n in MODEL_CATALOG if model_installed(n)]
    if MODELS_DIR.exists():
        names += sorted(p.name for p in MODELS_DIR.iterdir()
                        if p.is_dir() and p.name not in MODEL_CATALOG and model_installed(p.name))
    return names


def model_description(name: str) -> str:
    if name in MODEL_CATALOG:
        _, language, size, use = MODEL_CATALOG[name]
        return f"{language}, {size}. {use}."
    return "Your own model folder."


def validate_model_folder(folder: Path) -> str | None:
    """None if `folder` is a usable faster-whisper model, otherwise what's wrong."""
    missing = [f for f in REQUIRED_MODEL_FILES if not (Path(folder) / f).exists()]
    if missing:
        return (f"That folder is missing {', '.join(missing)}. Bleepline needs a faster-whisper (CTranslate2) "
                "model; convert other Whisper models with ct2-transformers-converter.")
    return None


def add_model_folder(src: Path, progress, log) -> str:
    """Copy a model folder into Bleepline's models folder. Returns the name it's listed under."""
    src = Path(src)
    error = validate_model_folder(src)
    if error:
        raise ValueError(error)
    base = src.name if src.name not in MODEL_CATALOG else f"{src.name} (custom)"
    name, n = base, 2
    while model_dir(name).exists():
        name, n = f"{base} ({n})", n + 1
    files = [p for p in src.rglob("*") if p.is_file()]
    total, done = sum(p.stat().st_size for p in files), 0
    log(f"Copying {src} as model \"{name}\" ({total // 1_000_000} MB)...")
    for f in files:
        dest = model_dir(name) / f.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "rb") as a, open(dest, "wb") as b:
            while chunk := a.read(1 << 20):
                b.write(chunk)
                done += len(chunk)
                progress(done, total)
    log(f"Model \"{name}\" added.")
    return name


def download_model(name: str, progress, log) -> None:
    repo = MODEL_CATALOG[name][0]
    info = _get_json(f"https://huggingface.co/api/models/{repo}?blobs=true")
    files = [
        (s["rfilename"], s.get("size") or 0)
        for s in info["siblings"]
        if any(fnmatch.fnmatch(s["rfilename"], p) for p in MODEL_FILES)
    ]
    total = sum(size_ for _, size_ in files)
    target = model_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    done = 0
    for file_name, size_ in sorted(files, key=lambda f: f[0] == "model.bin"):
        log(f"Downloading {file_name} ({max(1, size_ // 1_000_000)} MB)...")
        _download(
            f"https://huggingface.co/{repo}/resolve/main/{file_name}",
            target / file_name,
            lambda d, _t, base=done: progress(base + d, total),
        )
        done += size_
    log(f"Model {name} installed.")


# -- virtual audio cable --------------------------------------------------------

def cable_installed() -> bool | None:
    """True/False, or None if it can't be checked yet (packages missing)."""
    try:
        from . import devices

        devices.refresh()
        return devices.find_cable_capture() is not None
    except Exception:
        return None


def open_cable_page() -> None:
    webbrowser.open(VB_CABLE_URL)


# -- OBS plugin -------------------------------------------------------------------

OBS_PLUGIN_NAME = "bleepline-filter"
LEGACY_OBS_PLUGIN_NAME = "voice-guard-filter"  # the plugin's name before 1.1


def bundled_obs_plugin() -> Path | None:
    p = APP_DIR / "obs-plugin" / f"{OBS_PLUGIN_NAME}.dll"
    return p if p.exists() else None


def obs_plugin_target() -> Path:
    # OBS 30+ loads per-machine plugins from here, no matter where OBS itself is installed.
    root = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    return root / "obs-studio" / "plugins" / OBS_PLUGIN_NAME / "bin" / "64bit" / f"{OBS_PLUGIN_NAME}.dll"


def legacy_obs_plugin_dir() -> Path:
    return obs_plugin_target().parents[3] / LEGACY_OBS_PLUGIN_NAME


def obs_installed() -> bool:
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]
    return any(Path(r, "obs-studio", "bin", "64bit", "obs64.exe").exists() for r in roots if r)


def obs_plugin_installed() -> bool:
    return obs_plugin_target().exists()


def install_obs_plugin(log) -> None:
    src = bundled_obs_plugin()
    if src is None:
        raise RuntimeError("This build doesn't include the OBS plugin.")
    target = obs_plugin_target()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    log(f"OBS plugin installed to {target}. Restart OBS to load it.")
    if legacy_obs_plugin_dir().exists():
        shutil.rmtree(legacy_obs_plugin_dir(), ignore_errors=True)
        log("Removed the old Voice Guard plugin. In OBS, re-add the filter to your audio sources: "
            "Filters > + > Bleepline (profanity filter).")


# -- helpers --------------------------------------------------------------------

def restart_app() -> None:
    args = [sys.executable] if IS_FROZEN else [sys.executable, *sys.argv]
    subprocess.Popen(args, creationflags=NO_WINDOW)


def _get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as resp:
        return json.load(resp)


def _download(url: str, dest: Path, progress, sha256: str | None = None) -> None:
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as resp, open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            progress(done, total)
    if sha256 and digest.hexdigest() != sha256:
        part.unlink()
        raise RuntimeError(f"checksum mismatch for {dest.name}")
    part.replace(dest)
