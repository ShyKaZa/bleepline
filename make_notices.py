"""Writes THIRD_PARTY_NOTICES.txt: every third-party component in the built app, with its license text.

    venv/Scripts/python make_notices.py   (build.py runs it after PyInstaller)

Python packages are found by looking at what PyInstaller actually put in dist/VoiceGuard/_internal;
components that aren't Python packages (FFmpeg, cuDNN, speech models, ...) are listed in EXTRAS.
License texts that a package doesn't ship itself live in notices/.
"""
import re
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "dist" / "VoiceGuard" / "_internal"
NOTICES = ROOT / "notices"
OUT = ROOT / "THIRD_PARTY_NOTICES.txt"
SKIP = {"pip", "setuptools", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes"}
# Packages whose wheels don't include a license file.
OVERRIDES = {"ctranslate2": ("ctranslate2.txt", "MIT"), "tokenizers": ("tokenizers.txt", "Apache-2.0"),
             "flatbuffers": ("flatbuffers.txt", "Apache-2.0")}
PY_BASE = Path(sys.base_prefix)

EXTRAS = [
    ("Python 3.12 runtime", "PSF-2.0 (with the licenses of OpenSSL, libffi, bzip2, xz, zlib, SQLite and expat "
     "used by this Windows build)", "https://www.python.org", PY_BASE / "LICENSE.txt"),
    ("Tcl/Tk 8.6", "TCL (BSD-style)", "https://www.tcl.tk", PY_BASE / "tcl" / "tk8.6" / "license.terms"),
    ("PortAudio (inside sounddevice)", "MIT", "https://www.portaudio.com", NOTICES / "portaudio.txt"),
    ("OpenAI Whisper speech models (downloaded from inside the app; CTranslate2 conversions by SYSTRAN, "
     "Hugging Face distil-whisper and dropbox-dash, all MIT)", "MIT", "https://github.com/openai/whisper",
     NOTICES / "openai-whisper.txt"),
    ("Silero VAD voice activity model (inside faster-whisper)", "MIT", "https://github.com/snakers4/silero-vad",
     NOTICES / "silero-vad.txt"),
    ("FFmpeg libraries (inside PyAV: avcodec, avformat, avutil, avfilter, avdevice, swresample, swscale)",
     "LGPL-3.0-or-later. Built by the PyAV project; build scripts and sources: "
     "https://github.com/PyAV-Org/pyav-ffmpeg", "https://ffmpeg.org", NOTICES / "LGPL-3.0.txt"),
    ("x264 and x265 video encoders (inside PyAV's FFmpeg build; Voice Guard copies video untouched and never "
     "uses them)", "GPL-2.0-or-later. Sources: https://code.videolan.org/videolan/x264 and "
     "https://bitbucket.org/multicoreware/x265_git", "https://www.videolan.org/developers/x264.html",
     NOTICES / "GPL-2.0.txt"),
    ("Other codec libraries inside PyAV's FFmpeg build", "dav1d (BSD-2-Clause), libopus (BSD-3-Clause), libvpx "
     "(BSD-3-Clause), libwebp (BSD-3-Clause), SVT-AV1 (BSD-3-Clause-Clear), libvmaf (BSD-2-Clause-Patent), "
     "libvpl (MIT), zlib (Zlib), libiconv (LGPL-2.1-or-later), MinGW-w64 winpthreads (MIT), GCC runtime "
     "libgcc/libstdc++ (GPL-3.0 with the GCC Runtime Library Exception)", "https://github.com/PyAV-Org/pyav-ffmpeg",
     None),
    ("NVIDIA cuDNN (cudnn64_9.dll, inside CTranslate2) and cuBLAS (downloaded from inside the app for GPU use)",
     "NVIDIA Software License Agreement; redistributed unmodified as permitted by it",
     "https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html", None),
    ("Intel OpenMP runtime (libiomp5md.dll, inside CTranslate2)", "Intel Simplified Software License",
     "https://www.intel.com/content/www/us/en/developer/articles/license/onemkl-license-faq.html", None),
    ("Microsoft Visual C++ and Universal C runtime DLLs", "Microsoft Visual Studio redistributable license",
     "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files", None),
    ("List of Dirty, Naughty, Obscene, and Otherwise Bad Words (language lists downloaded on request)",
     "CC BY 4.0, by the LDNOOBW contributors (Shutterstock)",
     "https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words", None),
    ("OBS Studio / libobs headers (used to build the OBS plugin, which is GPL-2.0-or-later itself)",
     "GPL-2.0-or-later", "https://github.com/obsproject/obs-studio", None),
    ("PyInstaller bootloader", "GPL-2.0-or-later with the PyInstaller bootloader exception (allows any license "
     "for the packaged app)", "https://pyinstaller.org", None),
]


def archived_modules() -> set[str]:
    """Top-level names of the pure-Python modules PyInstaller packed inside VoiceGuard.exe."""
    out = subprocess.run([str(Path(sys.executable).parent / "pyi-archive_viewer"), "--recursive", "--brief",
                          str(BUNDLE.parent / "VoiceGuard.exe")], capture_output=True, text=True).stdout
    return {m.group(1).split(".")[0] for m in re.finditer(r"^\s*([A-Za-z_][\w.]*)\s*$", out, re.M)}


def bundled(dist, modules: set[str]) -> bool:
    tops = {str(f).split("/")[0].split("\\")[0] for f in dist.files or []}
    tops = {t for t in tops if t and t != ".." and not t.endswith((".dist-info", ".egg-info", ".data"))}
    tops = {t.removesuffix(".py") for t in tops} | {t.split(".")[0] for t in tops if t.endswith(".pyd")}
    return any(t in modules or (BUNDLE / t).exists() or (BUNDLE / f"{t}.pyd").exists() for t in tops)


def license_texts(dist) -> list[str]:
    name = dist.metadata["Name"].lower()
    if name in OVERRIDES:
        return [(NOTICES / OVERRIDES[name][0]).read_text(encoding="utf-8")]
    texts = []
    for f in dist.files or []:
        upper = Path(str(f)).name.upper()
        if upper.startswith(("LICEN", "COPYING", "NOTICE")) and not upper.endswith((".PY", ".PYC")):
            try:
                texts.append(Path(f.locate()).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return texts


def main() -> None:
    if not BUNDLE.exists():
        sys.exit("Build the app first (build.py); the notices list what PyInstaller bundled.")
    rule = "=" * 78
    parts = ["Voice Guard - third-party notices", rule,
             "Voice Guard itself is MIT-licensed, Copyright (c) 2026 Gorgho (see LICENSE).",
             "It is built on the components below. Thank you to all of their authors.", ""]
    index = []
    sections = []
    dists = {d.metadata["Name"].lower(): d for d in distributions()}
    modules = archived_modules()
    for key in sorted(dists):
        d = dists[key]
        if key in SKIP or not bundled(d, modules):
            continue
        m = d.metadata
        lic = m.get("License-Expression") or (OVERRIDES[key][1] if key in OVERRIDES else "")
        if not lic or len(lic) > 60 or lic == "UNKNOWN":
            classifiers = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License")]
            first_line = (m.get("License") or "").strip().splitlines()[0] if m.get("License") else ""
            lic = ", ".join(classifiers) or (first_line if 0 < len(first_line) < 40 else "") or "see below"
        url = m.get("Home-page") or next((u.split(",", 1)[1].strip() for u in m.get_all("Project-URL") or []
                                         if "," in u), "")
        title = f"{m['Name']} {d.version}"
        index.append(f"  {title:40s} {lic}")
        sections.append((title, lic, url, license_texts(d)))
    for title, lic, url, path in EXTRAS:
        short = "various (see below)" if lic.count(",") > 7 else re.split(r"\. |;| \(| by ", lic)[0].strip(" ,")
        index.append(f"  {title.split(' (')[0][:38]:40s} {short[:36]}")
        sections.append((title, lic, url, [path.read_text(encoding="utf-8", errors="replace")] if path else []))

    parts += ["Components:", *index, ""]
    for title, lic, url, texts in sections:
        parts += [rule, title, f"License: {lic}"] + ([f"Website: {url}"] if url else []) + [rule, ""]
        parts += [t.strip() + "\n" for t in texts] or ["(See the website above for the full license text.)\n"]
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(sections)} components, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
