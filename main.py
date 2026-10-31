import ctypes
import os
import sys

os.environ.setdefault("KMP_BLOCKTIME", "0")  # speech engine threads sleep between checks (see app/detector.py)

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
    if "--uninstall" in sys.argv:
        from app.uninstall_ui import run

        sys.exit(run(silent="--silent" in sys.argv, keep_data="--keep-data" in sys.argv))

    from app.gui import BleeplineApp

    BleeplineApp(startup="--startup" in sys.argv).mainloop()
