"""Build everything:  venv/Scripts/python build.py [app|package]

  app       dist/Bleepline/       the app (PyInstaller folder build) with the OBS plugin and license files
  package   release/Bleepline-Setup.exe             installer (Inno Setup: per-user, uninstaller, upgrades)
            release/Bleepline-portable-win64.zip    no-install version
            release/Bleepline-OBS-plugin.zip        the OBS filter on its own, for manual installs
  (none)    both

"package" only reads dist/Bleepline, so files signed in between are what ships: the release workflow
(.github/workflows/release.yml) signs Bleepline.exe and the plugin there, and the setup afterwards.
Run build_obs_plugin.py first so the OBS plugin is included. Needs Inno Setup 6.7+ for "package"
(winget install JRSoftware.InnoSetup, or set ISCC to its ISCC.exe).
Models, the GPU runtime and VB-CABLE are not bundled; the app's Setup tab downloads them.
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "Bleepline"
RELEASE = ROOT / "release"
PLUGIN = ROOT / "obs-plugin" / "bleepline-filter.dll"
sys.path.insert(0, str(ROOT))
from app.version import APP_NAME, AUTHOR, VERSION  # noqa: E402

PLUGIN_README = """Bleepline OBS filter
======================

Easiest: install the Bleepline app and use Setup > OBS plugin > Install.

Manual install (OBS 30 or newer):
1. Close OBS.
2. Copy the "bleepline-filter" folder from this zip into
   C:\\ProgramData\\obs-studio\\plugins\\
   so the plugin ends up at
   C:\\ProgramData\\obs-studio\\plugins\\bleepline-filter\\bin\\64bit\\bleepline-filter.dll
3. Start OBS, right-click an audio source > Filters > + > Bleepline (profanity filter).
4. Keep the Bleepline app open in "OBS Studio" mode and press Start filtering.
   Without the app the filter still passes audio through (delayed), it just doesn't mute anything.

Uninstall: delete C:\\ProgramData\\obs-studio\\plugins\\bleepline-filter
"""


def version_file() -> Path:
    """Windows file properties for Bleepline.exe (product name and version; code signing requires them)."""
    nums = tuple(int(n) for n in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    strings = {"CompanyName": AUTHOR, "FileDescription": APP_NAME, "FileVersion": VERSION,
               "InternalName": APP_NAME, "LegalCopyright": f"Copyright (c) 2026 {AUTHOR}",
               "OriginalFilename": f"{APP_NAME}.exe", "ProductName": APP_NAME, "ProductVersion": VERSION}
    path.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, "
        f"fileType=0x1, subtype=0x0, date=(0, 0)), kids=[StringFileInfo([StringTable('040904B0', ["
        + ", ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
        + "])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n", encoding="utf-8")
    return path


def build_app():
    import PyInstaller.__main__

    PyInstaller.__main__.run([
        str(ROOT / "main.py"),
        f"--name={APP_NAME}",
        "--windowed",
        "--noconfirm",
        "--clean",
        f"--icon={ROOT / 'assets' / 'bleepline.ico'}",
        f"--version-file={version_file()}",
        f"--add-data={ROOT / 'assets'}{os.pathsep}assets",  # icon, logo, built-in word lists
        "--collect-data=sv_ttk",  # dark / light theme
        "--collect-all=faster_whisper",  # includes the Silero VAD .onnx asset
        "--collect-all=ctranslate2",
        "--collect-data=better_profanity",  # the optional strict word list
        "--collect-data=soundcard",  # cffi headers used for desktop-audio (loopback) capture
        "--exclude-module=vosk",
        "--exclude-module=nvidia",
        f"--distpath={DIST}",
        f"--workpath={ROOT / 'build'}",
        f"--specpath={ROOT / 'build'}",
    ])

    if PLUGIN.exists():
        (APP / "obs-plugin").mkdir(exist_ok=True)
        shutil.copy2(PLUGIN, APP / "obs-plugin" / PLUGIN.name)
        shutil.copy2(ROOT / "obs-plugin-src" / "LICENSE", APP / "obs-plugin" / "LICENSE.txt")
        shutil.copy2(ROOT / "obs-plugin-src" / "bleepline-filter.c", APP / "obs-plugin")
    else:
        print("WARNING: OBS plugin not built (run build_obs_plugin.py); the app will ship without it.")

    internal = APP / "_internal"
    # sounddevice ships PortAudio for every platform; keep only the plain 64-bit Windows build (the ASIO
    # variants also carry Steinberg's ASIO SDK, which has its own license).
    for extra in (internal / "_sounddevice_data" / "portaudio-binaries").glob("*"):
        if extra.name not in ("libportaudio64bit.dll", "README.md"):
            extra.unlink()
    # CTranslate2 bundles NVIDIA's cuDNN loader, but Whisper runs on the GPU without it (tested: models and the
    # short-window engine), and leaving it out keeps one more proprietary file out of the download.
    (internal / "ctranslate2" / "cudnn64_9.dll").unlink(missing_ok=True)

    # Licenses travel with the app: ours, plus every bundled component's (listed from what was bundled).
    subprocess.run([sys.executable, str(ROOT / "make_notices.py")], check=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.txt"):
        shutil.copy2(ROOT / name, APP / name)


def find_iscc() -> str:
    candidates = [os.environ.get("ISCC", ""), shutil.which("ISCC") or ""]
    candidates += [str(Path(base) / "Inno Setup 6" / "ISCC.exe") for base in (
        os.environ.get("LOCALAPPDATA", "") + r"\Programs", os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("ProgramFiles", ""))]
    found = next((c for c in candidates if c and Path(c).is_file()), None)
    if not found:
        sys.exit("Inno Setup not found: winget install JRSoftware.InnoSetup (or set ISCC to ISCC.exe)")
    return found


def build_package():
    if not (APP / f"{APP_NAME}.exe").exists():
        sys.exit("Build the app first: build.py app")
    RELEASE.mkdir(exist_ok=True)
    for old in RELEASE.glob("*"):
        old.unlink()
    shutil.make_archive(str(RELEASE / "Bleepline-portable-win64"), "zip", DIST, APP.name)
    subprocess.run([find_iscc(), f"/DAppVersion={VERSION}", "/Q", str(ROOT / "installer" / "bleepline.iss")],
                   check=True)
    shutil.copy2(DIST / "Bleepline-Setup.exe", RELEASE / "Bleepline-Setup.exe")
    plugin = APP / "obs-plugin" / PLUGIN.name  # the copy in the app, so it's signed when the app is
    if plugin.exists():
        with zipfile.ZipFile(RELEASE / "Bleepline-OBS-plugin.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.write(plugin, "bleepline-filter/bin/64bit/bleepline-filter.dll")
            z.writestr("README.txt", PLUGIN_README)
            # GPL-2.0-or-later (it links libobs): the license and the full source travel with the binary.
            z.write(ROOT / "obs-plugin-src" / "LICENSE", "LICENSE.txt")
            for src in sorted((ROOT / "obs-plugin-src").glob("*.c")):
                z.write(src, f"source/{src.name}")
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    for f in sorted(RELEASE.iterdir()):
        print(f"{f.name:35s} {f.stat().st_size / 1e6:6.1f} MB")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage not in ("app", "package", "all"):
        sys.exit(__doc__)
    if stage in ("app", "all"):
        build_app()
    if stage in ("package", "all"):
        build_package()
