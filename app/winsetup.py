"""
Per-user install plumbing, no admin needed: Start menu / desktop shortcuts and the
"Apps & features" uninstall entry. Standard library only; shared by the setup exe
and the app's uninstaller.
"""
import ctypes
import os
import subprocess
import tempfile
import winreg
from pathlib import Path

from .version import APP_NAME, AUTHOR, PROJECT_URL, SUPPORT_URL, VERSION

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Bleepline"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
DEFAULT_INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_NAME
EXE_NAME = "Bleepline.exe"
LEGACY_UNINSTALL_KEY = UNINSTALL_KEY.replace("Bleepline", "VoiceGuard")  # versions before 1.1


def _known_folder(csidl: int) -> Path:
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def start_menu_shortcut() -> Path:
    return _known_folder(0x02) / f"{APP_NAME}.lnk"  # CSIDL_PROGRAMS (per-user Start menu)


def desktop_shortcut() -> Path:
    return _known_folder(0x10) / f"{APP_NAME}.lnk"  # CSIDL_DESKTOPDIRECTORY


def startup_shortcut() -> Path:
    return _known_folder(0x07) / f"{APP_NAME}.lnk"  # CSIDL_STARTUP (per-user)


def create_shortcut(link: Path, target: Path, arguments: str = "", workdir: Path | None = None) -> None:
    q = lambda s: str(s).replace("'", "''")  # noqa: E731  (PowerShell single-quote escaping)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{0}');"
        "$s.TargetPath = '{1}'; $s.Arguments = '{4}'; $s.WorkingDirectory = '{2}'; $s.IconLocation = '{1},0';"
        "$s.Description = '{3}'; $s.Save()"
    ).format(q(link), q(target), q(workdir or target.parent), APP_NAME, q(arguments))
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=NO_WINDOW)


def remove_shortcuts() -> None:
    for link in (start_menu_shortcut(), desktop_shortcut(), startup_shortcut()):
        link.unlink(missing_ok=True)


def set_start_with_windows(enabled: bool, exe: Path, arguments: str, workdir: Path) -> None:
    if enabled:
        create_shortcut(startup_shortcut(), exe, arguments, workdir)
    else:
        startup_shortcut().unlink(missing_ok=True)


def register(install_dir: Path, size_kb: int) -> None:
    exe = install_dir / EXE_NAME
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (
            ("DisplayName", APP_NAME),
            ("DisplayVersion", VERSION),
            ("Publisher", AUTHOR),
            ("InstallLocation", str(install_dir)),
            ("DisplayIcon", f"{exe},0"),
            ("UninstallString", f'"{exe}" --uninstall'),
            ("QuietUninstallString", f'"{exe}" --uninstall --silent'),
            *((("URLInfoAbout", PROJECT_URL),) if PROJECT_URL else ()),
            *((("HelpLink", SUPPORT_URL),) if SUPPORT_URL else ()),
        ):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def unregister() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


def installed_dir() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return Path(winreg.QueryValueEx(key, "InstallLocation")[0])
    except OSError:
        return None


def schedule_folder_delete(folder: Path) -> None:
    """Delete `folder` once this process has exited (a running exe can't delete itself); retries ~20 s."""
    f = str(folder)
    # Passed as one string on purpose: Python's list quoting escapes quotes as \", which cmd.exe
    # doesn't understand, so paths with spaces would break. No DETACHED_PROCESS: without a console
    # the command silently does nothing.
    command = (f'cmd /c for /l %i in (1,1,10) do (ping -n 3 127.0.0.1 >nul & rmdir /s /q "{f}" 2>nul & '
               f'if not exist "{f}" exit)')
    subprocess.Popen(command, cwd=tempfile.gettempdir(), creationflags=NO_WINDOW | NEW_GROUP)


def close_running_instances(exclude_pid: int | None = None) -> None:
    """Ask any running Bleepline to close, so its files can be replaced or removed."""
    skip = f" | Where-Object {{ $_.Id -ne {exclude_pid} }}" if exclude_pid else ""
    script = (f"$p = Get-Process Bleepline -ErrorAction SilentlyContinue{skip};"
              "$p | ForEach-Object { $_.CloseMainWindow() | Out-Null };"
              "$p | ForEach-Object { if (-not $_.WaitForExit(5000)) { Stop-Process -Id $_.Id -Force } }")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], creationflags=NO_WINDOW)


def remove_legacy_install() -> None:
    """Uninstall Voice Guard 1.0 (this app's old name), keeping its downloads and settings for Bleepline."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LEGACY_UNINSTALL_KEY) as key:
            command = winreg.QueryValueEx(key, "QuietUninstallString")[0]
    except OSError:
        return
    subprocess.run(f"{command} --keep-data", creationflags=NO_WINDOW, timeout=120)
