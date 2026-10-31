"""Bleepline setup: installs the app for the current Windows user (no admin rights needed).

    Bleepline-Setup.exe                                        interactive
    Bleepline-Setup.exe --silent [--dir PATH] [--desktop] [--launch]

Re-running a newer setup updates the installed copy in place; downloads and settings are kept.
"""
import argparse
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import theme, winsetup  # noqa: E402
from app.version import APP_NAME, VERSION  # noqa: E402

ZIP_ROOT = "Bleepline/"


def asset(name: str) -> Path:
    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return base / "assets" / name


def payload() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "Bleepline-win64.zip"
    return Path(__file__).resolve().parent.parent / "dist" / "Bleepline-win64.zip"


def install(target: Path, desktop: bool, progress=lambda fraction: None) -> Path:
    winsetup.close_running_instances()
    winsetup.remove_legacy_install()
    if (target / "_internal").exists():
        shutil.rmtree(target / "_internal")  # drop the previous version's libraries
    with zipfile.ZipFile(payload()) as z:
        files = [m for m in z.infolist() if not m.is_dir() and m.filename.startswith(ZIP_ROOT)]
        total, done = sum(m.file_size for m in files), 0
        for m in files:
            dest = target / m.filename[len(ZIP_ROOT):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            done += m.file_size
            progress(done / total)
    exe = target / winsetup.EXE_NAME
    winsetup.create_shortcut(winsetup.start_menu_shortcut(), exe)
    if desktop:
        winsetup.create_shortcut(winsetup.desktop_shortcut(), exe)
    winsetup.register(target, total // 1024)
    return exe


class SetupWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION} Setup")
        if asset("bleepline.ico").exists():
            self.iconbitmap(default=str(asset("bleepline.ico")))
        self.resizable(False, False)
        existing = winsetup.installed_dir()
        pad = {"padx": 16, "anchor": "w"}

        ttk.Label(self, text=f"{APP_NAME} {VERSION}", font=("Segoe UI", 14, "bold")).pack(pady=(16, 2), **pad)
        ttk.Label(self, wraplength=480, justify="left", text=(
            f"Updates the copy installed in {existing}. Your downloads and settings are kept." if existing else
            "Real-time profanity filter for streams and recordings. Installs for your Windows user only, so "
            "no administrator rights are needed. Speech models and the GPU runtime are downloaded from inside "
            "the app afterwards (Setup tab).")).pack(pady=(0, 10), **pad)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=16)
        ttk.Label(row, text="Install to:").pack(side="left")
        self.dir_var = tk.StringVar(value=str(existing or winsetup.DEFAULT_INSTALL_DIR))
        ttk.Entry(row, textvariable=self.dir_var, width=48).pack(side="left", padx=6)
        ttk.Button(row, text="Browse...", command=self._browse).pack(side="left")

        self.desktop_var = tk.BooleanVar(value=True)
        self.launch_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Create a desktop shortcut", variable=self.desktop_var).pack(pady=(10, 0), **pad)
        ttk.Checkbutton(self, text=f"Start {APP_NAME} when setup finishes", variable=self.launch_var).pack(**pad)

        self.bar = ttk.Progressbar(self, maximum=100, length=480)
        self.bar.pack(padx=16, pady=(12, 4))
        self.status = ttk.Label(self, text="", foreground="#666666")
        self.status.pack(**pad)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=16, pady=(8, 16))
        self.cancel_btn = ttk.Button(buttons, text="Cancel", command=self.destroy)
        self.cancel_btn.pack(side="right")
        self.install_btn = ttk.Button(buttons, text="Update" if existing else "Install", command=self._start)
        self.install_btn.pack(side="right", padx=6)
        self._fraction = 0.0
        self._result = None

    def _browse(self):
        folder = filedialog.askdirectory(title="Install Bleepline to")
        if folder:
            self.dir_var.set(str(Path(folder) / APP_NAME))

    def _start(self):
        self.install_btn.state(["disabled"])
        self.cancel_btn.state(["disabled"])
        self.status.config(text="Installing...")
        target, desktop = Path(self.dir_var.get()), self.desktop_var.get()

        def worker():
            try:
                self._result = install(target, desktop, lambda f: setattr(self, "_fraction", f))
            except Exception as exc:
                self._result = exc

        threading.Thread(target=worker, daemon=True).start()
        self._poll()

    def _poll(self):
        self.bar["value"] = self._fraction * 100
        if self._result is None:
            self.after(100, self._poll)
            return
        if isinstance(self._result, Exception):
            messagebox.showerror(f"{APP_NAME} Setup", f"Setup failed:\n{self._result}")
            self.install_btn.state(["!disabled"])
            self.cancel_btn.state(["!disabled"])
            self.status.config(text="")
            return
        if self.launch_var.get():
            subprocess.Popen([str(self._result)], cwd=str(self._result.parent))
        else:
            messagebox.showinfo(f"{APP_NAME} Setup",
                                f"{APP_NAME} is installed. Find it in the Start menu.")
        self.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--dir", default=str(winsetup.DEFAULT_INSTALL_DIR))
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--launch", action="store_true")
    args = ap.parse_args()
    if args.silent:
        exe = install(Path(args.dir), args.desktop)
        if args.launch:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
        return
    window = SetupWindow()
    theme.apply(window, "dark")  # same default look as the app
    window.mainloop()


if __name__ == "__main__":
    main()
