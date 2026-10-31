"""Build everything:  venv/Scripts/python build.py

  dist/Bleepline/Bleepline.exe      the app (folder build)
  release/Bleepline-Setup.exe        per-user installer (Start menu, Apps & features, uninstaller)
  release/Bleepline-portable-win64.zip   no-install version
  release/Bleepline-OBS-plugin.zip   the OBS filter on its own, for manual installs

Models, the GPU runtime and VB-CABLE are not bundled; the app's Setup tab downloads them.
Run build_obs_plugin.py first so the OBS plugin is included.
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
PLUGIN = ROOT / "obs-plugin" / "bleepline-filter.dll"

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

PyInstaller.__main__.run([
    str(ROOT / "main.py"),
    "--name=Bleepline",
    "--windowed",
    "--noconfirm",
    "--clean",
    f"--icon={ROOT / 'assets' / 'bleepline.ico'}",
    f"--add-data={ROOT / 'assets'}{os.pathsep}assets",
    "--collect-data=sv_ttk",  # dark / light theme
    "--collect-all=faster_whisper",  # includes the Silero VAD .onnx asset
    "--collect-all=ctranslate2",  # includes its bundled cuDNN
    "--collect-data=better_profanity",  # the built-in word list
    "--collect-data=soundcard",  # cffi headers used for desktop-audio (loopback) capture
    "--exclude-module=vosk",
    "--exclude-module=nvidia",
    f"--distpath={DIST}",
    f"--workpath={ROOT / 'build'}",
    f"--specpath={ROOT / 'build'}",
])

if PLUGIN.exists():
    (DIST / "Bleepline" / "obs-plugin").mkdir(exist_ok=True)
    shutil.copy2(PLUGIN, DIST / "Bleepline" / "obs-plugin" / PLUGIN.name)
    shutil.copy2(ROOT / "obs-plugin-src" / "LICENSE", DIST / "Bleepline" / "obs-plugin" / "LICENSE.txt")
    shutil.copy2(ROOT / "obs-plugin-src" / "bleepline-filter.c", DIST / "Bleepline" / "obs-plugin")
else:
    print("WARNING: OBS plugin not built (run build_obs_plugin.py); the app will ship without it.")

# sounddevice ships PortAudio for every platform; keep only the plain 64-bit Windows build (the ASIO
# variants also carry Steinberg's ASIO SDK, which has its own license).
for extra in (DIST / "Bleepline" / "_internal" / "_sounddevice_data" / "portaudio-binaries").glob("*"):
    if extra.name not in ("libportaudio64bit.dll", "README.md"):
        extra.unlink()

# Licenses travel with the app: ours, plus every bundled component's (listed from what was bundled).
subprocess.run([sys.executable, str(ROOT / "make_notices.py")], check=True)
for name in ("LICENSE", "THIRD_PARTY_NOTICES.txt"):
    shutil.copy2(ROOT / name, DIST / "Bleepline" / name)

shutil.make_archive(str(DIST / "Bleepline-win64"), "zip", DIST, "Bleepline")

# The installer is a separate one-file exe carrying the zipped app; run in a fresh process because
# PyInstaller doesn't reliably support two builds in one interpreter.
subprocess.run([
    sys.executable, "-m", "PyInstaller",
    str(ROOT / "installer" / "setup_main.py"),
    "--name=Bleepline-Setup",
    "--onefile",
    "--windowed",
    "--noconfirm",
    f"--add-data={DIST / 'Bleepline-win64.zip'}{os.pathsep}.",
    f"--icon={ROOT / 'assets' / 'bleepline.ico'}",
    f"--add-data={ROOT / 'assets'}{os.pathsep}assets",
    "--collect-data=sv_ttk",
    f"--paths={ROOT}",
    f"--distpath={DIST}",
    f"--workpath={ROOT / 'build' / 'setup'}",
    f"--specpath={ROOT / 'build'}",
], check=True)

RELEASE.mkdir(exist_ok=True)
shutil.copy2(DIST / "Bleepline-Setup.exe", RELEASE / "Bleepline-Setup.exe")
shutil.copy2(DIST / "Bleepline-win64.zip", RELEASE / "Bleepline-portable-win64.zip")
if PLUGIN.exists():
    with zipfile.ZipFile(RELEASE / "Bleepline-OBS-plugin.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(PLUGIN, "bleepline-filter/bin/64bit/bleepline-filter.dll")
        z.writestr("README.txt", PLUGIN_README)
        # GPL-2.0-or-later (it links libobs): the license and the full source travel with the binary.
        z.write(ROOT / "obs-plugin-src" / "LICENSE", "LICENSE.txt")
        for src in sorted((ROOT / "obs-plugin-src").glob("*.c")):
            z.write(src, f"source/{src.name}")

shutil.rmtree(ROOT / "build", ignore_errors=True)
for f in sorted(RELEASE.iterdir()):
    print(f"{f.name:35s} {f.stat().st_size / 1e6:6.1f} MB")
