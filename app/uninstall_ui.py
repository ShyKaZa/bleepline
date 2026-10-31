"""`VoiceGuard.exe --uninstall [--silent] [--keep-data]`: what Windows runs from Apps & features."""
import os
import tkinter as tk
from tkinter import messagebox, ttk

from . import maintenance, setup_tasks as st, theme, winsetup
from .config import Settings
from .paths import ASSETS_DIR, DATA_DIR
from .version import APP_NAME


def run(silent: bool = False, keep_data: bool = False) -> int:
    if silent:
        winsetup.close_running_instances(exclude_pid=os.getpid())
        maintenance.uninstall(remove_data=not keep_data, remove_plugin=not keep_data)
        return 0

    root = tk.Tk()
    root.title(f"Uninstall {APP_NAME}")
    root.resizable(False, False)
    pad = {"padx": 16, "anchor": "w"}
    ttk.Label(root, wraplength=440, justify="left",
              text=(f"This removes {APP_NAME} from this PC: the app, its Start menu and desktop "
                    "shortcuts, and its entry in Apps & features.")).pack(pady=(16, 10), **pad)

    data_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(root, variable=data_var,
                    text=(f"Also remove downloaded speech models, GPU runtime and settings "
                          f"({maintenance.human_size(maintenance.dir_size(DATA_DIR))})")).pack(**pad)
    plugin_var = tk.BooleanVar(value=st.obs_plugin_installed())
    plugin_check = ttk.Checkbutton(root, variable=plugin_var, text="Remove the Voice Guard filter from OBS")
    plugin_check.pack(**pad)
    if not st.obs_plugin_installed():
        plugin_check.state(["disabled"])
    ttk.Label(root, wraplength=440, justify="left", foreground="#666666",
              text=("VB-CABLE, if you installed it, is a separate driver from VB-Audio. Remove it with its "
                    "own installer: run VBCABLE_Setup_x64.exe as administrator and click Remove Driver.")
              ).pack(pady=(10, 10), **pad)

    result = {"code": 1}

    def uninstall():
        winsetup.close_running_instances(exclude_pid=os.getpid())
        try:
            maintenance.uninstall(remove_data=data_var.get(), remove_plugin=plugin_var.get())
        except Exception as exc:
            messagebox.showerror(f"Uninstall {APP_NAME}", f"Some files could not be removed:\n{exc}")
        else:
            messagebox.showinfo(f"Uninstall {APP_NAME}", f"{APP_NAME} has been removed.")
            result["code"] = 0
        root.destroy()

    buttons = ttk.Frame(root)
    buttons.pack(fill="x", padx=16, pady=(0, 16))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right")
    ttk.Button(buttons, text="Uninstall", command=uninstall).pack(side="right", padx=6)
    theme.apply(root, Settings.load().theme)
    if (ASSETS_DIR / "voiceguard.ico").exists():
        root.iconbitmap(default=str(ASSETS_DIR / "voiceguard.ico"))
    root.mainloop()
    return result["code"]
