"""Build the OBS filter plugin with Zig (no Visual Studio needed).

    venv/Scripts/python build_obs_plugin.py  ->  obs-plugin/bleepline-filter.dll

Compiles against OBS 30 headers so the plugin loads in OBS 30 and newer. Linking only needs obs.dll's
export names: they're read from an OBS install in tools/obs-33 when present (and saved to
obs-plugin-src/obs.def), otherwise taken from that saved file, so a clean machine needs just the headers.
"""
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEADERS = ROOT / "tools" / "obs-src-30" / "libobs"
OBS_DLL = ROOT / "tools" / "obs-33" / "bin" / "64bit" / "obs.dll"
BUILD = ROOT / "tools" / "build"
SOURCE = ROOT / "obs-plugin-src" / "bleepline-filter.c"
SAVED_DEF = ROOT / "obs-plugin-src" / "obs.def"
OUT = ROOT / "obs-plugin" / "bleepline-filter.dll"


def dll_exports(path: Path) -> list[str]:
    """Exported symbol names of a PE32+ DLL."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"{path} is not a PE file")
    n_sections, = struct.unpack_from("<H", data, pe + 6)
    opt_size, = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    export_rva, = struct.unpack_from("<I", data, opt + 112)  # PE32+ data directory 0
    sections = opt + opt_size

    def offset(rva):
        for i in range(n_sections):
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, sections + i * 40 + 8)
            if vaddr <= rva < vaddr + max(vsize, rawsize):
                return rawptr + rva - vaddr
        raise ValueError(f"rva {rva:#x} not in any section")

    exp = offset(export_rva)
    n_names, = struct.unpack_from("<I", data, exp + 24)
    names_rva, = struct.unpack_from("<I", data, exp + 32)
    names = []
    for i in range(n_names):
        name_off = offset(struct.unpack_from("<I", data, offset(names_rva) + 4 * i)[0])
        names.append(data[name_off:data.index(b"\0", name_off)].decode())
    return names


def zig(*args):
    subprocess.run([sys.executable, "-m", "ziglang", *map(str, args)], check=True)


def main():
    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OBS_DLL.exists():
        names = dll_exports(OBS_DLL)
        SAVED_DEF.write_text("LIBRARY obs.dll\nEXPORTS\n" + "\n".join(names) + "\n", newline="\n")
    else:
        names = SAVED_DEF.read_text().splitlines()[2:]
    zig("dlltool", "-m", "i386:x86-64", "-d", SAVED_DEF, "-l", BUILD / "libobs.dll.a")
    zig("cc", "-target", "x86_64-windows-gnu", "-shared", "-O2", "-Wall",
        f"-I{ROOT / 'obs-plugin-src' / 'include'}", f"-I{HEADERS}",
        SOURCE, BUILD / "libobs.dll.a", "-lws2_32", "-o", OUT)
    print(f"Built {OUT} ({OUT.stat().st_size // 1024} KB) against {len(names)} obs.dll exports")


if __name__ == "__main__":
    main()
