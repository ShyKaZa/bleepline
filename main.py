import ctypes
import os
import sys

os.environ.setdefault("KMP_BLOCKTIME", "0")  # speech engine threads sleep between checks (see app/detector.py)

MUTEX_NAME = "Bleepline.App"  # also in installer/bleepline.iss (AppMutex)


def already_running() -> bool:
    """True if Bleepline is already open; that window is brought to the front instead of opening a second one
    (two copies would fight over the OBS link port and the settings file)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)  # owned (and kept) until this process exits
    globals()["_instance_mutex"] = mutex
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    if "--restarted" in sys.argv:  # "Restart Bleepline" after setup: wait for the old copy to finish closing
        return kernel32.WaitForSingleObject(mutex, 15000) not in (0, 0x80)  # WAIT_OBJECT_0, WAIT_ABANDONED
    from app.version import APP_NAME

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def check(hwnd, _):
        title, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 256)
        user32.GetClassNameW(ctypes.c_void_p(hwnd), cls, 64)
        if cls.value == "TkTopLevel" and title.value.startswith(f"{APP_NAME} "):
            found.append(hwnd)
        return not found

    user32.EnumWindows(check, None)
    for hwnd in found:
        user32.ShowWindow(ctypes.c_void_p(hwnd), 9)  # SW_RESTORE
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    return True


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except Exception:
        pass

    try:  # own taskbar group and icon instead of Python's when run from source
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Bleepline.App")
    except Exception:
        pass

    if "--selftest" in sys.argv:
        from app.selftest import run

        sys.exit(run())
    if already_running():
        sys.exit(0)

    from app.gui import BleeplineApp

    BleeplineApp(startup="--startup" in sys.argv).mainloop()
