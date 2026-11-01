"""
Windows integration for the installed app: the "start with Windows" shortcut and finding the installer's
uninstaller. Installing and uninstalling themselves are done by Inno Setup (installer/bleepline.iss).
Standard library only.
"""
import ctypes
import subprocess
import winreg
from pathlib import Path

from .version import APP_NAME

UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}_is1"  # Inno Setup's entry
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _known_folder(csidl: int) -> Path:
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def startup_shortcut() -> Path:
    return _known_folder(0x07) / f"{APP_NAME}.lnk"  # CSIDL_STARTUP (per-user); the uninstaller removes it too


def create_shortcut(link: Path, target: Path, arguments: str = "", workdir: Path | None = None) -> None:
    q = lambda s: str(s).replace("'", "''")  # noqa: E731  (PowerShell single-quote escaping)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{0}');"
        "$s.TargetPath = '{1}'; $s.Arguments = '{4}'; $s.WorkingDirectory = '{2}'; $s.IconLocation = '{1},0';"
        "$s.Description = '{3}'; $s.Save()"
    ).format(q(link), q(target), q(workdir or target.parent), APP_NAME, q(arguments))
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=NO_WINDOW)


def set_start_with_windows(enabled: bool, exe: Path, arguments: str, workdir: Path) -> None:
    if enabled:
        create_shortcut(startup_shortcut(), exe, arguments, workdir)
    else:
        startup_shortcut().unlink(missing_ok=True)


def _uninstall_value(name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def installed_dir() -> Path | None:
    location = _uninstall_value("InstallLocation")
    return Path(location) if location else None


def run_uninstaller() -> bool:
    """Start the installer's uninstaller (it closes Bleepline itself). False if this copy wasn't installed."""
    command = _uninstall_value("UninstallString")
    if not command:
        return False
    subprocess.Popen(command)
    return True
