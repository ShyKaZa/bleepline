"""Tkinter UI. Imports only the standard library at module load so Setup works on a fresh machine."""
import ctypes
import gc
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import maintenance, theme, winsetup, wordlists
from . import setup_tasks as st
from .config import Settings, save_custom_words
from .paths import APP_DIR, ASSETS_DIR, DATA_DIR, IS_FROZEN, MODELS_DIR
from .version import APP_NAME, AUTHOR, PROJECT_URL, SUPPORT_URL, VERSION

# Status colours; replaced by the active theme's palette (see _use_palette).
RED, GREEN, AMBER, GREY = "#c0392b", "#1e8449", "#b9770e", "#666666"


def _use_palette(palette: dict) -> None:
    global RED, GREEN, AMBER, GREY
    RED, GREEN, AMBER, GREY = palette["red"], palette["green"], palette["amber"], palette["muted"]


THEMES = (("dark", "Dark"), ("light", "Light"), ("system", "Same as Windows"))
CABLE_CAPTURE = "CABLE Output"
CABLE_PLAYBACK = "CABLE Input"
WRAP = 640
SAFER_DELAY_MS = 800
SLOW_CHECKS = 100  # slow checks in a row before saying so (~5 s): a burst of swearing slows them briefly too
# From research/keyword_spotter (87 real recordings, 101 swear words, base.en on GPU + CPU); update when the
# engine changes.
DELAY_RESULTS = ("500 ms fully mutes about 2 in 3 swear words and 86% of their sound, "
                 "600 ms about 8 in 10 and 91%")
DEVICE_NAMES = {"cuda": "GPU", "split": "GPU + CPU", "cpu": "CPU"}  # detector.load_model's devices

MODE_LABELS = {
    "obs": "OBS Studio - filter inside OBS with the Bleepline plugin (no drivers)",
    "stream": "Streamlabs, XSplit, Twitch Studio or any other streaming / recording app",
    "listen": "Filter what I hear (voice chat into my headset)",
    "custom": "Custom input and output devices",
}
MODE_HELP = {
    "obs": ("1. Install the OBS plugin (Setup tab), then restart OBS.\n"
            "2. In OBS, right-click an audio source (Desktop Audio, your mic, a game or Discord "
            "capture) > Filters > + > Bleepline. Its delay is set in that filter.\n"
            "3. Add OBS's built-in Render Delay filter to your webcam and game capture with the same "
            "delay so the video stays in sync. Render Delay goes up to 500 ms; for more, add a second one "
            "(500 + 300 = 800 ms).\n"
            "4. Keep Bleepline running in this mode while you stream. You hear everything live; "
            "only the stream is cleaned."),
    "stream": ("1. Bleepline records everything playing on the device below (game, Discord, "
               "browser...) and sends a cleaned copy to \"CABLE Input\". You still hear it all live.\n"
               "2. In your streaming app, turn off its own desktop / system audio and add "
               "\"CABLE Output (VB-Audio Virtual Cable)\" as an audio input instead.\n"
               "3. If your app can delay video, delay it by the same amount to keep lip-sync."),
    "listen": ("1. Discord: User Settings > Voice & Video > Output Device > \"CABLE Input (VB-Audio "
               "Virtual Cable)\", and Input Device > your real microphone (not \"Default\").\n"
               "2. Leave Windows' own default speaker and microphone on your headset. Only Discord's output "
               "goes to the cable.\n"
               "3. Bleepline plays the cleaned call into your headset, so you and anything that captures "
               "your desktop audio get the clean version. Not for game audio - you'd hear the game late."),
    "custom": "Any input to any output, e.g. to try it on your own microphone.",
}
LANGUAGES = [
    ("auto", "Auto-detect"), ("en", "English"), ("cs", "Czech"), ("sk", "Slovak"), ("pl", "Polish"),
    ("de", "German"), ("fr", "French"), ("es", "Spanish"), ("it", "Italian"), ("pt", "Portuguese"),
    ("nl", "Dutch"), ("sv", "Swedish"), ("no", "Norwegian"), ("da", "Danish"), ("fi", "Finnish"),
    ("hu", "Hungarian"), ("ro", "Romanian"), ("ru", "Russian"), ("uk", "Ukrainian"), ("tr", "Turkish"),
    ("ar", "Arabic"), ("hi", "Hindi"), ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"),
]
MEDIA_TYPES = [("Video and audio", "*.mp4 *.mkv *.mov *.flv *.webm *.avi *.m4a *.mp3 *.wav *.aac *.flac *.ogg"),
               ("All files", "*.*")]
SOUND_TYPES = [("Audio files", "*.wav *.mp3 *.ogg *.flac *.m4a *.aac *.opus"), ("All files", "*.*")]
SOUNDS_DIR = maintenance.SOUNDS_DIR


class ScrollFrame(ttk.Frame):
    """A tab whose content scrolls when the window is shorter than it (laptop screens). Build into .body."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self._item = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._fit)
        self.canvas.bind("<Configure>", self._fit)

    def _fit(self, _event=None):
        need, have = self.body.winfo_reqheight(), self.canvas.winfo_height()
        # At least the visible height, so content that expands (the logs) still fills a tall window.
        self.canvas.itemconfigure(self._item, width=self.canvas.winfo_width(), height=max(need, have))
        self.canvas.configure(scrollregion=(0, 0, 0, max(need, have)))
        if need > have + 1 and not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y", before=self.canvas)
        elif need <= have and self.bar.winfo_ismapped():
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)

    def scroll(self, units: int) -> None:
        if self.bar.winfo_ismapped():
            self.canvas.yview_scroll(units, "units")


def _work_area() -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the desktop without the taskbar, in pixels."""
    rect = (ctypes.c_long * 4)()
    if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, rect, 0):  # SPI_GETWORKAREA
        return tuple(rect)
    return (0, 0, 1920, 1040)


class BleeplineApp(tk.Tk):
    def __init__(self, startup: bool = False):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION}")
        left, top, right, bottom = _work_area()
        height = min(980, bottom - top - 50)  # 50: title bar and frame
        self.geometry(f"820x{height}+{max(left, (left + right - 820) // 2)}+{top + 10}")
        self.minsize(760, min(560, height))

        self.settings = Settings.load()
        self.palette = theme.apply(self, self.settings.theme)
        _use_palette(self.palette)
        icon = ASSETS_DIR / "bleepline.ico"
        if icon.exists():
            self.iconbitmap(default=str(icon))  # also used by dialogs
        self.engine = None  # AudioEngine, or LinkServer in OBS mode
        self._live_models: dict = {}
        self._events: queue.Queue = queue.Queue()
        self._starting = self._busy = self._cleaning = False
        self._wiped = False  # after "Remove everything", don't recreate settings.json on close
        self._last_error_at = 0.0
        self._last_sources_at = 0.0
        self._last_output: Path | None = None
        self._windows_problems: list[str] = []
        self._slow_checks = 0
        self._live_device = "cpu"
        self._gpu_name = st.nvidia_gpu_name()
        self.inputs: list[str] = []
        self.outputs: list[str] = []

        self._build_header()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        names = ("live", "clean", "words", "models", "options", "setup", "manage", "about")
        titles = ("Live filter", "Clean a recording", "Banned words", "Models", "Options", "Setup", "Manage", "About")
        for name, title in zip(names, titles):
            tab = ScrollFrame(self.nb)
            setattr(self, f"{name}_tab", tab.body)
            self.nb.add(tab, text=f" {title} ")
        self.bind_all("<MouseWheel>", self._on_wheel)
        self._build_live_tab()
        self._build_clean_tab()
        self._build_words_tab()
        self._build_models_tab()
        self._build_options_tab()
        self._build_setup_tab()
        self._build_manage_tab()
        self._build_about_tab()
        theme.apply(self, self.settings.theme)  # styles the plain text boxes built above

        self._load_devices()
        self._refresh_models()
        self._refresh_setup_status()
        self._refresh_manage()
        self.after(100, self._poll)
        self.after(3000, self._auto_check_updates)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if st.missing_packages() or not st.installed_models():
            self._select(self.setup_tab)
        elif self.settings.auto_start and self._readiness()[0]:
            self.after(300, self._start)
        if startup and self.settings.start_minimized:
            self.after(200, self.iconify)

    # =====================================================================
    # Header (logo, name, status badge visible from every tab)
    # =====================================================================

    def _build_header(self):
        bar = ttk.Frame(self, style="Header.TFrame")
        bar.pack(fill="x")
        logo = ASSETS_DIR / "bleepline-32.png"
        if logo.exists():
            self._logo = tk.PhotoImage(file=str(logo))
            ttk.Label(bar, image=self._logo, style="Header.TLabel").pack(side="left", padx=(14, 8), pady=10)
        ttk.Label(bar, text=APP_NAME, style="Title.TLabel").pack(side="left", padx=(0 if logo.exists() else 14, 0))
        ttk.Label(bar, text=f"v{VERSION}  ·  real-time profanity filter", style="HeaderMuted.TLabel").pack(
            side="left", padx=10, pady=(6, 0))
        self.pill = ttk.Label(bar, text="●  Stopped", style="Pill.TLabel", foreground=RED)
        self.pill.pack(side="right", padx=16)
        self.update_link = ttk.Label(bar, text="", style="Update.TLabel", cursor="hand2")  # shown when there is one
        ttk.Separator(self).pack(fill="x")

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.config(text=text, foreground=color)
        self.pill.config(text=f"●  {text}", foreground=color)

    def _select(self, tab) -> None:
        """Show the notebook tab whose content frame is `tab`."""
        self.nb.select(tab.master.master)  # content frame -> canvas -> ScrollFrame

    def _on_wheel(self, event):
        """Mouse wheel scrolls the tab under the pointer, except over widgets that scroll themselves."""
        widget = event.widget
        if isinstance(widget, str) or isinstance(widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
            return  # str: a combobox's drop-down list
        while widget is not None and not isinstance(widget, ScrollFrame):
            widget = widget.master
        if widget is not None:
            widget.scroll(-1 if event.delta > 0 else 1)

    # =====================================================================
    # Live filter tab
    # =====================================================================

    def _build_live_tab(self):
        f = self.live_tab
        top = ttk.Frame(f)
        top.pack(fill="x", padx=10, pady=(12, 6))
        self.toggle_btn = ttk.Button(top, text="Start filtering", width=18, command=self._toggle)
        self.toggle_btn.pack(side="left")
        self.status_label = ttk.Label(top, text="Stopped", foreground=RED, font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side="left", padx=12)
        self.stats_label = ttk.Label(top, text="", foreground=GREY)
        self.stats_label.pack(side="right")

        self.defaults_warn = ttk.Frame(f)  # shown only when Windows' defaults point at the cable
        self.defaults_label = ttk.Label(self.defaults_warn, foreground=RED, wraplength=WRAP - 120, justify="left")
        self.defaults_label.pack(side="left", fill="x", expand=True)
        warn_btns = ttk.Frame(self.defaults_warn)
        warn_btns.pack(side="right")
        ttk.Button(warn_btns, text="Open sound settings", command=lambda: os.startfile("ms-settings:sound")).pack(
            fill="x")
        ttk.Button(warn_btns, text="Check again", command=self._load_devices).pack(fill="x", pady=(4, 0))

        modes = ttk.LabelFrame(f, text="Where does your audio go?")
        modes.pack(fill="x", padx=10, pady=6)
        self._modes_frame = modes
        self.mode_var = tk.StringVar(value=self.settings.mode)
        self._mode_buttons = []
        for key, label in MODE_LABELS.items():
            rb = ttk.Radiobutton(modes, text=label, variable=self.mode_var, value=key, command=self._mode_changed)
            rb.pack(anchor="w", padx=8, pady=1)
            self._mode_buttons.append(rb)

        self.mode_panel = ttk.Frame(modes)
        self.mode_panel.pack(fill="x", padx=8, pady=(6, 2))
        self.panels = {}

        p = ttk.Frame(self.mode_panel)
        self.obs_status = ttk.Label(p, text="")
        self.obs_status.pack(anchor="w")
        self.obs_sources = ttk.Label(p, text="", foreground=GREY)
        self.obs_sources.pack(anchor="w")
        self.panels["obs"] = p

        p = ttk.Frame(self.mode_panel)
        p.columnconfigure(1, weight=1)
        ttk.Label(p, text="Capture desktop audio from:").grid(row=0, column=0, sticky="w")
        self.desktop_var = tk.StringVar()
        self.desktop_combo = ttk.Combobox(p, textvariable=self.desktop_var, state="readonly")
        self.desktop_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.stream_cable = ttk.Label(p, text="")
        self.stream_cable.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.panels["stream"] = p

        p = ttk.Frame(self.mode_panel)
        p.columnconfigure(1, weight=1)
        self.listen_cable = ttk.Label(p, text="")
        self.listen_cable.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(p, text="Play cleaned audio to:").grid(row=1, column=0, sticky="w")
        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(p, textvariable=self.monitor_var, state="readonly")
        self.monitor_combo.grid(row=1, column=1, sticky="ew", padx=(6, 0))
        self.panels["listen"] = p

        p = ttk.Frame(self.mode_panel)
        p.columnconfigure(1, weight=1)
        ttk.Label(p, text="Input:").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        self.input_combo = ttk.Combobox(p, textvariable=self.input_var, state="readonly")
        self.input_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=2)
        ttk.Label(p, text="Output:").grid(row=1, column=0, sticky="w")
        self.output_var = tk.StringVar()
        self.output_combo = ttk.Combobox(p, textvariable=self.output_var, state="readonly")
        self.output_combo.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=2)
        self.panels["custom"] = p

        self.mode_help = ttk.Label(modes, wraplength=WRAP, foreground=GREY, justify="left")
        self.mode_help.pack(anchor="w", padx=8, pady=(6, 8))

        tune = ttk.LabelFrame(f, text="Settings")
        tune.pack(fill="x", padx=10, pady=6)
        row = ttk.Frame(tune)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Delay:").pack(side="left")
        self.delay_var = tk.IntVar(value=self.settings.delay_ms)
        self.delay_scale = ttk.Scale(row, from_=350, to=1500, variable=self.delay_var, orient="horizontal")
        self.delay_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.delay_label = ttk.Label(row, width=8)
        self.delay_label.pack(side="left")
        self.safer_btn = ttk.Button(row, text="Safer: 800 ms", command=lambda: self.delay_var.set(SAFER_DELAY_MS))
        self.safer_btn.pack(side="left", padx=(4, 0))
        self.delay_var.trace_add("write", lambda *_: self._update_delay_label())
        self._update_delay_label()
        self.delay_hint = ttk.Label(tune, wraplength=WRAP, foreground=GREY, justify="left")
        self.delay_hint.pack(anchor="w", padx=8, pady=(2, 4))

        row2 = ttk.Frame(tune)
        row2.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(row2, text="Speech model:").pack(side="left")
        self.live_model_var = tk.StringVar(value=self.settings.live_model)
        self.live_model_combo = ttk.Combobox(row2, textvariable=self.live_model_var, state="readonly", width=18)
        self.live_model_combo.pack(side="left", padx=6)
        ttk.Label(row2, text="tiny.en is the lightest; base.en catches the most. Multilingual: tiny / base.",
                  foreground=GREY).pack(side="left")

        self.auto_start_var = tk.BooleanVar(value=self.settings.auto_start)
        ttk.Checkbutton(tune, text="Start filtering automatically when Bleepline opens",
                        variable=self.auto_start_var).pack(anchor="w", padx=8, pady=(0, 2))
        ttk.Label(tune, foreground=GREY, text="Language, sensitivity and the censor sound are in the Options tab."
                  ).pack(anchor="w", padx=8, pady=(0, 6))

        meter = ttk.Frame(f)
        meter.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Label(meter, text="Input level:").pack(side="left")
        self.level_bar = ttk.Progressbar(meter, maximum=100)
        self.level_bar.pack(side="left", fill="x", expand=True, padx=6)

        logf = ttk.LabelFrame(f, text="Activity")
        logf.pack(fill="both", expand=True, padx=10, pady=(6, 10))
        self.log_text = self._make_log(logf)

        self._locked_widgets = [*self._mode_buttons, self.desktop_combo, self.monitor_combo, self.input_combo,
                                self.output_combo, self.delay_scale, self.safer_btn, self.live_model_combo]
        self._mode_changed()

    def _update_delay_label(self):
        self.delay_label.config(text=f"{round(self.delay_var.get() / 10) * 10} ms")

    def _mode_changed(self):
        mode = self.mode_var.get()
        for key, panel in self.panels.items():
            panel.pack_forget() if key != mode else panel.pack(fill="x")
        self.mode_help.config(text=MODE_HELP[mode])
        if mode == "obs":
            self.delay_scale.state(["disabled"])
            self.safer_btn.state(["disabled"])
            self.delay_hint.config(text=("In OBS mode the delay is set in each Bleepline filter inside OBS; more "
                                         "than its default 500 ms catches more (" + DELAY_RESULTS + ")."))
        else:
            if self.engine is None:
                self.delay_scale.state(["!disabled"])
                self.safer_btn.state(["!disabled"])
            self.delay_hint.config(text=("Viewers hear audio this much later. Measured on real recordings: "
                                         + DELAY_RESULTS + "; what slips through is usually just the start of a "
                                         "word. If the log says a word was caught late, raise it."))
        self._update_mode_status()

    def _load_devices(self):
        if st.missing_packages():
            return
        from . import devices

        try:
            devices.refresh()
            self.inputs, self.outputs = devices.list_devices()
            default_out = devices.default_output_name()
        except Exception as exc:
            self._log(f"Could not list audio devices: {exc}")
            return

        listening = [o for o in self.outputs if CABLE_PLAYBACK not in o]
        self.desktop_combo["values"] = listening
        self.monitor_combo["values"] = listening
        self.input_combo["values"] = self.inputs
        self.output_combo["values"] = self.outputs

        def pick(var, saved, options):
            var.set(saved if saved in options else default_out if default_out in options else
                    options[0] if options else "")

        s = self.settings
        pick(self.desktop_var, s.desktop_device, listening)
        pick(self.monitor_var, s.monitor_device, listening)
        self.input_var.set(s.input_device if s.input_device in self.inputs else "")
        self.output_var.set(s.output_device if s.output_device in self.outputs else "")
        self._update_mode_status()
        try:
            self._windows_problems = devices.windows_default_problems()
        except Exception:
            self._windows_problems = []
        if self._windows_problems:
            self.defaults_label.config(text="\n\n".join(self._windows_problems))
            self.defaults_warn.pack(fill="x", padx=10, pady=(0, 4), before=self._modes_frame)
        else:
            self.defaults_warn.pack_forget()

    def _cable_capture(self):
        return next((n for n in self.inputs if CABLE_CAPTURE in n), None)

    def _cable_playback(self):
        return next((n for n in self.outputs if CABLE_PLAYBACK in n), None)

    def _update_mode_status(self):
        if st.obs_plugin_installed():
            self.obs_status.config(text="OBS plugin installed.", foreground=GREEN)
        elif st.bundled_obs_plugin():
            self.obs_status.config(text="OBS plugin not installed yet - install it in the Setup tab.",
                                   foreground=AMBER)
        else:
            self.obs_status.config(text="This build doesn't include the OBS plugin.", foreground=AMBER)
        playback, capture = self._cable_playback(), self._cable_capture()
        self.stream_cable.config(
            text=f"Sends the cleaned copy to: {playback}" if playback else
            "VB-CABLE is needed for this mode - see the Setup tab.",
            foreground=GREEN if playback else AMBER)
        self.listen_cable.config(
            text=f"Listens to: {capture}" if capture else "VB-CABLE is needed for this mode - see the Setup tab.",
            foreground=GREEN if capture else AMBER)

    # =====================================================================
    # Clean a recording tab
    # =====================================================================

    def _build_clean_tab(self):
        f = self.clean_tab
        ttk.Label(
            f, wraplength=WRAP, justify="left",
            text=("Clean a finished recording before you upload it - ShadowPlay, Xbox Game Bar, OBS "
                  "recordings, game replays. The whole file is analysed with full context, so this is more "
                  "accurate than live filtering. Video is copied untouched; only the audio changes. Banned "
                  "words, language, sensitivity and the censor sound come from their tabs."),
        ).pack(anchor="w", padx=10, pady=(12, 8))

        grid = ttk.Frame(f)
        grid.pack(fill="x", padx=10)
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="Recording:").grid(row=0, column=0, sticky="w", pady=4)
        self.src_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.src_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(grid, text="Browse...", command=self._browse).grid(row=0, column=2)
        ttk.Label(grid, text="Saves as:").grid(row=1, column=0, sticky="w", pady=4)
        self.dst_label = ttk.Label(grid, text="-", foreground=GREY)
        self.dst_label.grid(row=1, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Label(grid, text="Speech model:").grid(row=2, column=0, sticky="w", pady=4)
        mrow = ttk.Frame(grid)
        mrow.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
        self.file_model_var = tk.StringVar(value=self.settings.file_model)
        self.file_model_combo = ttk.Combobox(mrow, textvariable=self.file_model_var, state="readonly", width=18)
        self.file_model_combo.pack(side="left")
        ttk.Label(mrow, text="Bigger = more accurate, slower. No time pressure here.", foreground=GREY).pack(
            side="left", padx=6)

        act = ttk.Frame(f)
        act.pack(fill="x", padx=10, pady=(10, 0))
        self.clean_btn = ttk.Button(act, text="Clean recording", command=self._clean)
        self.clean_btn.pack(side="left")
        self.open_btn = ttk.Button(act, text="Show in folder", command=self._show_output, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        self.clean_stage = ttk.Label(act, text="", foreground=GREY)
        self.clean_stage.pack(side="left", padx=6)
        self.clean_progress = ttk.Progressbar(f, maximum=100)
        self.clean_progress.pack(fill="x", padx=10, pady=8)

        logf = ttk.LabelFrame(f, text="Result")
        logf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.clean_log = self._make_log(logf)

    def _browse(self):
        path = filedialog.askopenfilename(title="Choose a recording", filetypes=MEDIA_TYPES)
        if path:
            p = Path(path)
            self.src_var.set(str(p))
            self.dst_label.config(text=str(p.with_name(f"{p.stem} (clean){p.suffix}")))

    def _clean(self):
        if self._cleaning:
            return
        src, size = self.src_var.get(), self.file_model_var.get()
        if st.missing_packages():
            return self._append(self.clean_log, "Install the Python packages first (Setup tab).")
        if not size:
            return self._append(self.clean_log, "Download a speech model first (Models tab).")
        if not src:
            return self._append(self.clean_log, "Choose a recording first.")
        self._collect_settings()
        s = self.settings
        self._cleaning = True
        self.clean_btn.state(["disabled"])
        self.open_btn.state(["disabled"])
        self._append(self.clean_log, f"Cleaning {Path(src).name} with {size}...")
        self._warn_language_mismatch(size, self.clean_log)

        def worker():
            try:
                from .detector import load_model
                from .file_cleaner import FILE_CPU_THREADS, clean_file

                # Loaded per job and dropped afterwards, so a big model doesn't keep holding GPU memory
                # while you game. Separate from the live model: a long file job must never stall live filtering.
                bundle = load_model(size, cpu_threads=FILE_CPU_THREADS)
                dst, catches = clean_file(
                    src, model_size=size, mute_mode=s.mute_mode, pad_before_ms=s.mute_pad_before_ms,
                    pad_after_ms=s.mute_pad_after_ms, matcher=self._build_matcher(), min_prob=s.min_word_prob,
                    language=s.language, beep_hz=s.beep_hz, censor_volume=s.censor_volume,
                    censor_sound=s.censor_sound, model_bundle=bundle,
                    progress=lambda frac, stage: self._events.put(("clean_progress", (frac, stage))),
                    log=lambda t: self._events.put(("clean_log", t)),
                )
                self._events.put(("clean_done", (dst, catches)))
            except Exception as exc:
                self._events.put(("clean_failed", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _clean_finished(self, dst=None, catches=None, error=None):
        self._cleaning = False
        self.clean_btn.state(["!disabled"])
        if error:
            self.clean_stage.config(text="Failed")
            self._append(self.clean_log, f"FAILED: {error}")
            return
        self._last_output = dst
        self.open_btn.state(["!disabled"])
        self.clean_stage.config(text="Done")
        for t, word in catches:
            self._append(self.clean_log, f"  {int(t // 60):02d}:{t % 60:05.2f}  muted \"{word}\"")
        self._append(self.clean_log, f"Saved {dst.name} ({len(catches)} word(s) muted). "
                                     "A list of them is saved next to it.")

    def _show_output(self):
        if self._last_output:
            subprocess.Popen(["explorer", "/select,", str(self._last_output)])

    # =====================================================================
    # Banned words tab
    # =====================================================================

    def _build_words_tab(self):
        f = self.words_tab
        lists = ttk.LabelFrame(f, text="Word lists in use")
        lists.pack(fill="x", padx=10, pady=(12, 6))
        builtin = ttk.Frame(lists)
        builtin.pack(fill="x", padx=8, pady=(6, 4))
        self._builtin_vars: dict = {}
        for r, (key, (name, examples)) in enumerate(wordlists.BUILTIN_LISTS.items()):
            var = tk.BooleanVar(value=key in self.settings.builtin_lists)
            self._builtin_vars[key] = var
            try:
                count = f" ({len(wordlists.builtin_words(key))})"
            except ImportError:  # the strict list comes from a package that isn't installed yet
                count = ""
            ttk.Checkbutton(builtin, variable=var, text=f"{name}{count}").grid(row=r, column=0, sticky="w")
            ttk.Label(builtin, text=examples, foreground=GREY).grid(row=r, column=1, sticky="w", padx=(10, 0))
        self.lists_frame = ttk.Frame(lists)
        self.lists_frame.pack(fill="x", padx=8)
        self._list_vars: dict = {}
        btns = ttk.Frame(lists)
        btns.pack(fill="x", padx=8, pady=6)
        ttk.Button(btns, text="Import a word list file...", command=self._import_list).pack(side="left")
        ttk.Label(btns, text="   Download a language:").pack(side="left")
        self.lang_list_var = tk.StringVar()
        self.lang_list_combo = ttk.Combobox(btns, textvariable=self.lang_list_var, state="readonly", width=18,
                                            values=[f"{name} ({code})" for code, (name, _) in
                                                    wordlists.LANGUAGE_LISTS.items()])
        self.lang_list_combo.pack(side="left", padx=4)
        ttk.Button(btns, text="Download", command=self._download_list).pack(side="left")
        ttk.Label(lists, text=wordlists.LDNOOBW_CREDIT, foreground=GREY).pack(anchor="w", padx=8, pady=(0, 6))

        cols = ttk.Frame(f)
        cols.pack(fill="both", expand=True, padx=10, pady=6)
        cols.columnconfigure(0, weight=3)
        cols.columnconfigure(1, weight=2)
        cols.rowconfigure(0, weight=1)
        mine = ttk.LabelFrame(cols, text="Your words")
        mine.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(mine, foreground=GREY, wraplength=290, justify="left",
                  text="One per line. word* catches anything starting with it; several words = a phrase.").pack(
            anchor="w", padx=6, pady=(4, 2))
        self.words_text = tk.Text(mine, height=9, wrap="word")
        self.words_text.pack(fill="both", expand=True, padx=6)
        self.words_text.insert("1.0", "\n".join(wordlists.custom_words()))
        ttk.Button(mine, text="Export to file...", command=self._export_words).pack(anchor="w", padx=6, pady=4)
        allow = ttk.LabelFrame(cols, text="Allow list (never mute)")
        allow.grid(row=0, column=1, sticky="nsew")
        ttk.Label(allow, foreground=GREY, wraplength=200, justify="left",
                  text="Words you're fine saying on stream, e.g. hell, damn. Beats every list.").pack(
            anchor="w", padx=6, pady=(4, 2))
        self.allow_text = tk.Text(allow, height=9, wrap="word")
        self.allow_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.allow_text.insert("1.0", "\n".join(wordlists.allowed_words()))

        self.censored_var = tk.BooleanVar(value=self.settings.catch_censored)
        ttk.Checkbutton(f, variable=self.censored_var,
                        text="Also mute words the speech model writes with * (it sometimes outputs f**k itself)"
                        ).pack(anchor="w", padx=10)

        test = ttk.Frame(f)
        test.pack(fill="x", padx=10, pady=6)
        ttk.Label(test, text="Test a word or phrase:").pack(side="left")
        self.test_var = tk.StringVar()
        entry = ttk.Entry(test, textvariable=self.test_var, width=24)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda _e: self._test_word())
        ttk.Button(test, text="Test", command=self._test_word).pack(side="left")
        self.test_result = ttk.Label(f, text="", wraplength=WRAP, justify="left")
        self.test_result.pack(anchor="w", padx=10)
        ttk.Button(f, text="Save", command=self._save_words).pack(anchor="e", padx=10, pady=(4, 10))
        self._refresh_lists()

    def _refresh_lists(self):
        for w in self.lists_frame.winfo_children():
            w.destroy()
        self._list_vars = {}
        for name, count in wordlists.imported_lists():
            row = ttk.Frame(self.lists_frame)
            row.pack(fill="x")
            var = tk.BooleanVar(value=name not in self.settings.disabled_lists)
            self._list_vars[name] = var
            ttk.Checkbutton(row, variable=var, text=f"{Path(name).stem} ({count})").pack(side="left")
            ttk.Button(row, text="Remove", command=lambda n=name: self._remove_list(n)).pack(side="right")

    def _disabled_lists(self):
        return [n for n, var in self._list_vars.items() if not var.get()]

    def _your_words(self):
        return wordlists.parse_lines(self.words_text.get("1.0", "end"))

    def _allowed(self):
        return wordlists.parse_lines(self.allow_text.get("1.0", "end"))

    def _build_matcher(self):
        save_custom_words(self._your_words())
        wordlists.save_allowed(self._allowed())
        s = self.settings
        return wordlists.build_matcher(s.builtin_lists, s.disabled_lists, self._your_words(), s.catch_censored)

    def _import_list(self):
        path = filedialog.askopenfilename(title="Import a word list",
                                          filetypes=[("Text files", "*.txt *.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            name, count = wordlists.import_file(Path(path))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not import that file:\n{exc}")
            return
        self._refresh_lists()
        self._refresh_manage()
        self._log(f"Imported word list \"{Path(name).stem}\" ({count} entries).")

    def _download_list(self):
        choice = self.lang_list_var.get()
        if not choice:
            return
        code = choice.rsplit("(", 1)[1].rstrip(")")
        self._run_task(f"Download {choice} word list", lambda: self._setup_log_async(
            "Downloaded \"{}\" ({} entries).".format(*wordlists.download_language(code))))

    def _remove_list(self, name):
        if messagebox.askyesno(APP_NAME, f"Remove the word list \"{Path(name).stem}\"?"):
            wordlists.remove_list(name)
            self._refresh_lists()
            self._refresh_manage()

    def _export_words(self):
        path = filedialog.asksaveasfilename(title="Export your words", defaultextension=".txt",
                                            initialfile="bleepline words.txt",
                                            filetypes=[("Text files", "*.txt")])
        if path:
            wordlists.export_words(Path(path), self._your_words())
            self._log(f"Exported {len(self._your_words())} words to {path}")

    def _test_word(self):
        self._collect_settings()
        s = self.settings
        wordlists.save_allowed(self._allowed())
        self.test_result.config(text=wordlists.explain(self.test_var.get().strip(), s.builtin_lists,
                                                       s.disabled_lists, self._your_words(), s.catch_censored))

    def _save_words(self):
        save_custom_words(self._your_words())
        wordlists.save_allowed(self._allowed())
        self._collect_settings()
        self._log("Banned words saved. They apply the next time filtering starts or a recording is cleaned.")

    # =====================================================================
    # Models tab
    # =====================================================================

    def _build_models_tab(self):
        f = self.models_tab
        ttk.Label(
            f, wraplength=WRAP, justify="left",
            text=("Speech models turn audio into words. English models (.en) are the most accurate for English; "
                  "multilingual ones understand other languages too (pick the language in Options). Small models "
                  "react fast enough for live filtering; big ones are for cleaning recordings. You can also add "
                  "your own faster-whisper model folder."),
        ).pack(anchor="w", padx=10, pady=(12, 6))
        cols = ("language", "size", "use", "status")
        self.model_tree = ttk.Treeview(f, columns=cols, height=11, selectmode="browse")
        for col, title, width in (("#0", "Model", 150), ("language", "Language", 100), ("size", "Size", 70),
                                  ("use", "Best for", 270), ("status", "Status", 90)):
            self.model_tree.heading(col, text=title)
            self.model_tree.column(col, width=width, stretch=col == "use")
        self.model_tree.pack(fill="x", padx=10)
        self.model_tree.bind("<<TreeviewSelect>>", lambda _e: self._model_selected())
        btns = ttk.Frame(f)
        btns.pack(fill="x", padx=10, pady=6)
        self.model_dl_btn = ttk.Button(btns, text="Download", command=self._download_selected_model)
        self.model_dl_btn.pack(side="left")
        self.model_rm_btn = ttk.Button(btns, text="Remove", command=self._remove_selected_model)
        self.model_rm_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Add model folder...", command=self._add_model_folder).pack(side="left", padx=(18, 0))
        ttk.Button(btns, text="Open models folder", command=self._open_models_folder).pack(side="left", padx=6)
        self.model_info = ttk.Label(f, text="", foreground=GREY, wraplength=WRAP, justify="left")
        self.model_info.pack(anchor="w", padx=10)
        prog = ttk.Frame(f)
        prog.pack(fill="x", padx=10, pady=(8, 0))
        self.model_progress = ttk.Progressbar(prog, maximum=100)
        self.model_progress.pack(side="left", fill="x", expand=True)
        self.model_progress_label = ttk.Label(prog, text="", width=16)
        self.model_progress_label.pack(side="left", padx=6)
        ttk.Label(
            f, foreground=GREY, wraplength=WRAP, justify="left",
            text=("Your own model: a folder with model.bin, config.json and tokenizer.json (faster-whisper / "
                  "CTranslate2 format), e.g. from Hugging Face. Drop it into the models folder or use Add model "
                  "folder. Live, the Activity log shows how long each check takes so you can see if it keeps up."),
        ).pack(anchor="w", padx=10, pady=(10, 0))

    def _refresh_model_tree(self):
        selected = self.model_tree.selection()
        self.model_tree.delete(*self.model_tree.get_children())
        installed = set(st.installed_models())
        for name, (_, language, size, use) in st.MODEL_CATALOG.items():
            self.model_tree.insert("", "end", iid=name, text=name,
                                   values=(language, size, use, "Installed" if name in installed else ""))
        for name in sorted(installed - set(st.MODEL_CATALOG)):
            size = maintenance.human_size(maintenance.dir_size(MODELS_DIR / name))
            self.model_tree.insert("", "end", iid=name, text=name, values=("?", size, "Your own model", "Installed"))
        if selected and self.model_tree.exists(selected[0]):
            self.model_tree.selection_set(selected[0])
        self._model_selected()

    def _selected_model(self):
        sel = self.model_tree.selection()
        return sel[0] if sel else None

    def _model_selected(self):
        name = self._selected_model()
        installed = name in st.installed_models() if name else False
        idle = not self._busy
        self.model_dl_btn.state(["!disabled"] if name and not installed and name in st.MODEL_CATALOG and idle
                                else ["disabled"])
        self.model_rm_btn.state(["!disabled"] if installed and idle else ["disabled"])
        self.model_info.config(text=f"{name}: {st.model_description(name)}" if name else "Select a model.")

    def _download_selected_model(self):
        name = self._selected_model()
        if name:
            self._run_task(f"Download model {name}",
                           lambda: st.download_model(name, self._progress_async, self._setup_log_async))

    def _remove_selected_model(self):
        name = self._selected_model()
        if name:
            self._remove_item(f"model:{name}", f"model {name}")

    def _add_model_folder(self):
        folder = filedialog.askdirectory(title="Choose a faster-whisper model folder")
        if not folder:
            return
        error = st.validate_model_folder(Path(folder))
        if error:
            messagebox.showerror(APP_NAME, error)
            return
        self._run_task("Add model folder",
                       lambda: st.add_model_folder(Path(folder), self._progress_async, self._setup_log_async))

    def _open_models_folder(self):
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(MODELS_DIR)])

    # =====================================================================
    # Options tab
    # =====================================================================

    def _build_options_tab(self):
        f = self.options_tab
        s = self.settings
        self.options_note = ttk.Label(f, text="", foreground=AMBER)
        self.options_note.pack(anchor="w", padx=10, pady=(10, 0))

        rec = ttk.LabelFrame(f, text="Recognition")
        rec.pack(fill="x", padx=10, pady=6)
        row = ttk.Frame(rec)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Live filtering runs on:").pack(side="left")
        self.asr_device_var = tk.StringVar(value=s.asr_device)
        for value, label in (("split", "GPU + CPU (recommended)"), ("cpu", "CPU only"), ("gpu", "GPU only")):
            ttk.Radiobutton(row, text=label, variable=self.asr_device_var, value=value).pack(side="left", padx=(6, 6))
        ttk.Label(rec, foreground=GREY, wraplength=WRAP, justify="left",
                  text=("GPU + CPU (NVIDIA): the GPU does a few milliseconds of each check and the CPU the rest "
                        "(about one core). A game on the same GPU lost about 6% of its speed in tests. CPU only "
                        "leaves the GPU alone but reacts later: it needs about 100 ms more delay for the same "
                        "catches. GPU only reacts like GPU + CPU but cost that game about a third of its speed: use "
                        "it on a second, streaming PC. Without an NVIDIA GPU, all three run on the CPU.")
                  ).pack(anchor="w", padx=8)
        row = ttk.Frame(rec)
        row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(row, text="Language:").pack(side="left")
        self.language_var = tk.StringVar(value=dict(LANGUAGES).get(s.language, "Auto-detect"))
        ttk.Combobox(row, textvariable=self.language_var, state="readonly", width=16,
                     values=[name for _, name in LANGUAGES]).pack(side="left", padx=6)
        ttk.Label(rec, foreground=GREY, wraplength=WRAP, justify="left",
                  text=("English-only models (.en) always use English. For other languages use a multilingual model "
                        "(Models tab) and add a word list for that language (Banned words tab). Picking the language "
                        "is faster and more reliable than auto-detect.")).pack(anchor="w", padx=8)
        self.sens_var = tk.DoubleVar(value=s.min_word_prob)
        self._slider(rec, "Sensitivity:", self.sens_var, 0.05, 0.6,
                     lambda v: f"{v:.2f}  ({'catch more' if v < 0.12 else 'only when sure' if v > 0.35 else 'balanced'})",
                     "Lower catches more mumbled words but may mute similar-sounding ones; higher only mutes when the "
                     "speech model is confident.")
        self.early_var = tk.BooleanVar(value=s.early_mute)
        ttk.Checkbutton(rec, text="Early muting (experimental)", variable=self.early_var).pack(
            anchor="w", padx=8, pady=(6, 0))
        ttk.Label(rec, foreground=GREY, wraplength=WRAP, justify="left",
                  text=("Mutes a word as soon as it starts like a banned word (\"fu...\", \"kur...\") and undoes it if "
                        "it turns out harmless. Catches more of long words at short delays (250-400 ms); words "
                        "that begin like a banned word (\"she\", \"bitter\") may get briefly cut. Works in every "
                        "live mode, including OBS.")).pack(anchor="w", padx=8)
        self.pad_before_var = tk.IntVar(value=s.mute_pad_before_ms)
        self._slider(rec, "Mute before word:", self.pad_before_var, 0, 500, lambda v: f"{int(v)} ms")
        self.pad_after_var = tk.IntVar(value=s.mute_pad_after_ms)
        self._slider(rec, "Mute after word:", self.pad_after_var, 0, 500, lambda v: f"{int(v)} ms",
                     "Extra audio cut around each word, because word timings from the speech model are approximate.")

        cen = ttk.LabelFrame(f, text="Censor sound (live filtering, OBS and cleaned recordings)")
        cen.pack(fill="x", padx=10, pady=6)
        row = ttk.Frame(cen)
        row.pack(fill="x", padx=8, pady=(6, 2))
        self.censor_var = tk.StringVar(value=s.mute_mode)
        for value, label in (("silence", "Silence"), ("beep", "Beep"), ("sound", "My own sound")):
            ttk.Radiobutton(row, text=label, variable=self.censor_var, value=value).pack(side="left", padx=(0, 12))
        self.beep_var = tk.IntVar(value=s.beep_hz)
        self._slider(cen, "Beep pitch:", self.beep_var, 300, 3000, lambda v: f"{int(v)} Hz")
        row = ttk.Frame(cen)
        row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text="My sound:", width=18).pack(side="left")
        self.sound_var = tk.StringVar(value=s.censor_sound or "")
        self.sound_combo = ttk.Combobox(row, textvariable=self.sound_var, state="readonly", width=28)
        self.sound_combo.pack(side="left")
        ttk.Button(row, text="Add sound...", command=self._add_sound).pack(side="left", padx=6)
        ttk.Button(row, text="Remove", command=self._remove_sound).pack(side="left")
        self.volume_var = tk.IntVar(value=int(s.censor_volume * 100))
        self._slider(cen, "Volume:", self.volume_var, 0, 100, lambda v: f"{int(v)} %")
        row = ttk.Frame(cen)
        row.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Button(row, text="Preview", command=self._preview_sound).pack(side="left")
        ttk.Label(row, foreground=GREY, text="  Any audio file; the first 5 seconds are used, repeated for long mutes."
                  ).pack(side="left")
        self._refresh_sounds()

        look = ttk.LabelFrame(f, text="Appearance")
        look.pack(fill="x", padx=10, pady=6)
        row = ttk.Frame(look)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Theme:", width=18).pack(side="left")
        self.theme_var = tk.StringVar(value=s.theme)
        for value, label in THEMES:
            ttk.Radiobutton(row, text=label, variable=self.theme_var, value=value,
                            command=self._change_theme).pack(side="left", padx=(0, 12))

        start = ttk.LabelFrame(f, text="Startup")
        start.pack(fill="x", padx=10, pady=6)
        self.startup_var = tk.BooleanVar(value=winsetup.startup_shortcut().exists())
        ttk.Checkbutton(start, text="Start Bleepline when Windows starts", variable=self.startup_var,
                        command=self._toggle_startup).pack(anchor="w", padx=8, pady=(6, 0))
        self.minimized_var = tk.BooleanVar(value=s.start_minimized)
        ttk.Checkbutton(start, text="...and start it minimized", variable=self.minimized_var).pack(anchor="w", padx=8)
        ttk.Label(start, foreground=GREY, text="Combine with \"Start filtering automatically\" (Live filter tab) "
                                               "for a set-and-forget setup.").pack(anchor="w", padx=8, pady=(0, 6))
        self.updates_var = tk.BooleanVar(value=self._updates_enabled())
        ttk.Checkbutton(start, text="Check GitHub for a new Bleepline version once a day", variable=self.updates_var,
                        command=self._toggle_updates).pack(anchor="w", padx=8)
        ttk.Label(start, foreground=GREY, wraplength=WRAP, justify="left",
                  text="One small request to GitHub's public release list; nothing about you or what you hear is "
                       "sent.").pack(anchor="w", padx=8, pady=(0, 6))

    def _change_theme(self):
        self.settings.theme = self.theme_var.get()
        self.palette = theme.apply(self, self.settings.theme, previous=self.palette)
        _use_palette(self.palette)
        self.settings.save()

    def _slider(self, parent, label, var, lo, hi, fmt, hint=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text=label, width=18).pack(side="left")
        ttk.Scale(row, from_=lo, to=hi, variable=var, orient="horizontal").pack(side="left", fill="x", expand=True)
        value = ttk.Label(row, width=26)
        value.pack(side="left", padx=6)
        update = lambda *_: value.config(text=fmt(var.get()))  # noqa: E731
        var.trace_add("write", update)
        update()
        if hint:
            ttk.Label(parent, text=hint, foreground=GREY, wraplength=WRAP, justify="left").pack(anchor="w", padx=8)

    def _refresh_sounds(self):
        names = sorted(p.name for p in SOUNDS_DIR.iterdir() if p.is_file()) if SOUNDS_DIR.exists() else []
        self.sound_combo["values"] = names
        if self.sound_var.get() not in names:
            self.sound_var.set(names[0] if names else "")

    def _add_sound(self):
        if st.missing_packages():
            return messagebox.showinfo(APP_NAME, "Install the Python packages first (Setup tab).")
        path = filedialog.askopenfilename(title="Choose a censor sound", filetypes=SOUND_TYPES)
        if not path:
            return
        from .censor_sound import import_sound

        try:
            name = import_sound(Path(path))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not use that sound:\n{exc}")
            return
        self._refresh_sounds()
        self.sound_var.set(name)
        self.censor_var.set("sound")
        self._refresh_manage()

    def _remove_sound(self):
        name = self.sound_var.get()
        if name and messagebox.askyesno(APP_NAME, f"Remove the sound \"{name}\"?"):
            (SOUNDS_DIR / name).unlink(missing_ok=True)
            self._refresh_sounds()
            self._refresh_manage()

    def _preview_sound(self):
        if st.missing_packages():
            return
        self._collect_settings()
        s = self.settings
        try:
            import sounddevice as sd

            from .censor_sound import replacement_clip

            clip = replacement_clip(s.mute_mode, 48000, s.beep_hz, s.censor_volume, s.censor_sound)
            if s.mute_mode == "beep":
                clip = clip[:28800]  # 0.6 s
            if len(clip):
                sd.play(clip, 48000)
            else:
                self._log("Censor sound is set to silence, so there's nothing to preview.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not play the preview:\n{exc}")

    # -- update check ------------------------------------------------------

    def _updates_enabled(self) -> bool:
        if self.settings.check_updates is not None:
            return self.settings.check_updates
        from . import updates

        return bool(updates.installer_choice())  # portable and source copies start with it off

    def _toggle_updates(self):
        self.settings.check_updates = self.updates_var.get()
        self.settings.save()
        self._auto_check_updates()

    def _auto_check_updates(self):
        from . import updates

        s = self.settings
        if s.update_available and updates.is_newer(s.update_available):
            self._show_update(s.update_available, s.update_url)
        if self._updates_enabled() and time.time() - s.update_checked_at > updates.CHECK_EVERY_SEC:
            self._check_updates(manual=False)

    def _check_updates(self, manual: bool = True):
        from . import updates

        if manual:
            self.update_status.config(text="Checking...")

        def worker():
            try:
                self._events.put(("update", (*updates.latest_release(), manual)))
            except Exception as exc:
                self._events.put(("update_failed", (str(exc), manual)))

        threading.Thread(target=worker, daemon=True).start()

    def _update_result(self, version: str, url: str, manual: bool):
        from . import updates

        s = self.settings
        s.update_checked_at, s.update_available, s.update_url = time.time(), version, url
        if not self._wiped:
            s.save()
        if updates.is_newer(version):
            self._show_update(version, url)
            self._log(f"{APP_NAME} {version} is available (you have {VERSION}): {url}")
        elif manual:
            self.update_status.config(text=f"You have the latest version ({VERSION}).")

    def _show_update(self, version: str, url: str):
        self.update_link.config(text=f"⬆  Update available: {version}")
        self.update_link.bind("<Button-1>", lambda _e: webbrowser.open(url))
        if not self.update_link.winfo_ismapped():
            self.update_link.pack(side="right", padx=(0, 4))
        self.update_status.config(text=f"Version {version} is available - click the link at the top to get it.")

    def _toggle_startup(self):
        if IS_FROZEN:
            exe, args = Path(sys.executable), "--startup"
        else:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            exe, args = (pythonw if pythonw.exists() else Path(sys.executable)), f'"{APP_DIR / "main.py"}" --startup'
        try:
            winsetup.set_start_with_windows(self.startup_var.get(), exe, args, APP_DIR)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not change the startup setting:\n{exc}")
            self.startup_var.set(winsetup.startup_shortcut().exists())

    # =====================================================================
    # Setup tab
    # =====================================================================

    def _build_setup_tab(self):
        f = self.setup_tab
        ttk.Label(
            f, wraplength=WRAP, justify="left",
            text=("Everything Bleepline needs. \"Install everything\" installs what's missing below plus the "
                  "tiny.en and base.en speech models (more models in the Models tab). VB-CABLE is a driver from "
                  "VB-Audio and is only needed for the Streamlabs/XSplit/other and Filter-what-I-hear modes. It's "
                  "donationware: free for personal use, so if it helps you, consider supporting VB-Audio."),
        ).pack(anchor="w", padx=10, pady=(12, 6))
        self.install_all_btn = ttk.Button(f, text="Install everything", command=self._install_all)
        self.install_all_btn.pack(anchor="w", padx=10, pady=(0, 8))

        grid = ttk.Frame(f)
        grid.pack(fill="x", padx=10)
        grid.columnconfigure(1, weight=1)

        def row(r, title, button_text, command):
            ttk.Label(grid, text=title, font=("Segoe UI", 9, "bold")).grid(row=r, column=0, sticky="nw", pady=5)
            status = ttk.Label(grid, text="", wraplength=420, justify="left")
            status.grid(row=r, column=1, sticky="w", padx=8)
            button = ttk.Button(grid, text=button_text, command=command)
            button.grid(row=r, column=2, sticky="ne", pady=3)
            return status, button

        self.pkg_status, self.pkg_btn = row(0, "Python packages", "Install", self._install_packages)
        if st.IS_FROZEN:
            for w in grid.grid_slaves(row=0):
                w.grid_remove()
        self.gpu_status, self.gpu_btn = row(1, "GPU acceleration", "Install", self._install_gpu)
        self.models_status, models_btn = row(2, "Speech models", "Models tab",
                                             lambda: self._select(self.models_tab))
        self.obs_plugin_status, self.obs_plugin_btn = row(3, "OBS plugin", "Install", self._install_obs_plugin)
        self.cable_status, _ = row(4, "VB-CABLE", "Get VB-CABLE", st.open_cable_page)
        ttk.Button(grid, text="Re-check", command=self._recheck).grid(row=5, column=2, sticky="ne")
        ttk.Label(
            grid, foreground=GREY, wraplength=420, justify="left",
            text=("Unzip the download, right-click VBCABLE_Setup_x64.exe > Run as administrator > Install "
                  "Driver, reboot, then press Re-check."),
        ).grid(row=5, column=1, sticky="w", padx=8)

        prog = ttk.Frame(f)
        prog.pack(fill="x", padx=10, pady=(12, 0))
        self.progress = ttk.Progressbar(prog, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_label = ttk.Label(prog, text="", width=16)
        self.progress_label.pack(side="left", padx=6)

        self.restart_btn = ttk.Button(f, text="Restart Bleepline to finish setup", command=self._restart)
        self.setup_log_frame = ttk.LabelFrame(f, text="Setup log")
        self.setup_log_frame.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        self.setup_log_text = self._make_log(self.setup_log_frame)

    def _refresh_models(self):
        installed = st.installed_models()
        for combo, var, preferred in ((self.live_model_combo, self.live_model_var, self.settings.live_model),
                                      (self.file_model_combo, self.file_model_var, self.settings.file_model)):
            combo["values"] = installed
            if var.get() not in installed:
                var.set(preferred if preferred in installed else installed[0] if installed else "")
        self._refresh_model_tree()

    def _refresh_setup_status(self):
        missing = st.missing_packages()
        idle = not self._busy
        self.pkg_status.config(text=f"Missing: {', '.join(missing)}" if missing else "Installed",
                               foreground=AMBER if missing else GREEN)
        self.pkg_btn.config(state="normal" if idle and missing else "disabled")

        runtime = st.gpu_runtime_installed()
        if not self._gpu_name:
            self.gpu_status.config(text="No NVIDIA GPU found - that's fine, live filtering runs on the CPU.",
                                   foreground=GREY)
        elif runtime:
            self.gpu_status.config(text=f"{self._gpu_name}: ready (live filtering on GPU + CPU, faster cleaning)",
                                   foreground=GREEN)
        else:
            self.gpu_status.config(text=f"{self._gpu_name} found. Optional runtime (553 MB): live filtering catches "
                                        "more words with it (GPU + CPU) and cleaning recordings is faster; without "
                                        "it everything runs on the CPU.", foreground=GREY)
        self.gpu_btn.config(state="normal" if idle and self._gpu_name and not runtime else "disabled")

        installed = st.installed_models()
        self.models_status.config(text=f"Installed: {', '.join(installed)}" if installed else
                                  "None yet - Install everything downloads tiny.en and base.en.",
                                  foreground=GREEN if installed else AMBER)

        if st.obs_plugin_installed():
            self.obs_plugin_status.config(text="Installed. Restart OBS if it was open.", foreground=GREEN)
        elif not st.bundled_obs_plugin():
            self.obs_plugin_status.config(text="Not included in this build.", foreground=GREY)
        else:
            note = "" if st.obs_installed() else " (OBS not found on this PC, installing anyway is fine)"
            self.obs_plugin_status.config(text="Not installed." + note, foreground=AMBER)
        self.obs_plugin_btn.config(
            state="normal" if idle and st.bundled_obs_plugin() and not st.obs_plugin_installed() else "disabled")

        if missing:
            self.cable_status.config(text="Can be checked once the Python packages are installed.", foreground=GREY)
        elif self._cable_capture():
            self.cable_status.config(text="Installed", foreground=GREEN)
        else:
            self.cable_status.config(text="Not installed. Only needed for the cable modes.", foreground=GREY)
        self.install_all_btn.config(state="normal" if idle else "disabled")

    def _recheck(self):
        self._load_devices()
        self._refresh_models()
        self._refresh_setup_status()
        self._setup_log("VB-CABLE: " + ("found." if self._cable_capture() else
                                        "not found (reboot after installing it)."))

    def _install_packages(self):
        self._run_task("Install Python packages", lambda: st.install_packages(self._setup_log_async))

    def _install_gpu(self):
        self._run_task("Install GPU runtime", lambda: st.install_gpu_runtime(self._progress_async,
                                                                             self._setup_log_async))

    def _install_obs_plugin(self):
        self._run_task("Install OBS plugin", lambda: st.install_obs_plugin(self._setup_log_async))

    def _install_all(self):
        mode = self.mode_var.get()

        def everything():
            if st.missing_packages():
                st.install_packages(self._setup_log_async)
            if self._gpu_name and not st.gpu_runtime_installed():
                st.install_gpu_runtime(self._progress_async, self._setup_log_async)
            for name in st.DEFAULT_DOWNLOADS:
                if not st.model_installed(name):
                    st.download_model(name, self._progress_async, self._setup_log_async)
            if st.bundled_obs_plugin() and not st.obs_plugin_installed() and (mode == "obs" or st.obs_installed()):
                st.install_obs_plugin(self._setup_log_async)
            self._setup_log_async("All downloads complete.")

        self._run_task("Install everything", everything, open_cable=mode in ("stream", "listen"))

    def _run_task(self, label, fn, open_cable=False):
        if self._busy:
            messagebox.showinfo(APP_NAME, "Another download or install is still running.")
            return
        self._busy = True
        packages_were_missing = bool(st.missing_packages())
        self._refresh_setup_status()
        self._model_selected()
        self._setup_log(f"--- {label}")
        self.model_progress_label.config(text=label[:16])

        def worker():
            ok = True
            try:
                fn()
            except Exception as exc:
                ok = False
                self._setup_log_async(f"FAILED: {exc}")
            self._events.put(("task_done", (ok, packages_were_missing, open_cable, label)))

        threading.Thread(target=worker, daemon=True).start()

    def _task_done(self, ok, packages_were_missing, open_cable, label):
        self._busy = False
        for bar, text in ((self.progress, self.progress_label), (self.model_progress, self.model_progress_label)):
            bar["value"] = 0
            text.config(text="")
        if not ok:
            messagebox.showerror(APP_NAME, f"{label} failed - see the Setup tab's log for details.")
        if ok and packages_were_missing and not st.missing_packages():
            self._setup_log("Python packages installed. Restart Bleepline to load them.")
            self.restart_btn.pack(anchor="w", padx=10, pady=(8, 0), before=self.setup_log_frame)
        elif not st.missing_packages():
            self._load_devices()
        self._refresh_models()
        self._refresh_setup_status()
        self._update_mode_status()
        self._refresh_lists()
        self._refresh_manage()
        if ok and open_cable and not st.missing_packages() and not self._cable_capture():
            self._setup_log("Your mode needs VB-CABLE - opening its download page. Reboot after installing, "
                            "then press Re-check.")
            st.open_cable_page()

    def _restart(self):
        self._on_close()
        st.restart_app()

    def _progress_async(self, done, total):
        self._events.put(("progress", (done, total)))

    def _setup_log_async(self, text):
        self._events.put(("setup_log", text))

    # =====================================================================
    # Manage tab: storage, removal, uninstall
    # =====================================================================

    def _build_about_tab(self):
        f = self.about_tab
        top = ttk.Frame(f)
        top.pack(fill="x", padx=16, pady=(16, 8))
        logo = ASSETS_DIR / "bleepline-64.png"
        if logo.exists():
            self._about_logo = tk.PhotoImage(file=str(logo))
            ttk.Label(top, image=self._about_logo).pack(side="left", padx=(0, 14))
        names = ttk.Frame(top)
        names.pack(side="left")
        ttk.Label(names, text=APP_NAME, font=("Segoe UI Semibold", 18)).pack(anchor="w")
        ttk.Label(names, text=f"Version {VERSION}  ·  made by {AUTHOR}", foreground=GREY).pack(anchor="w")
        ttk.Label(names, text="Free and open source (MIT license)", foreground=GREY).pack(anchor="w")

        ttk.Label(f, wraplength=WRAP, justify="left", text=(
            "Mutes banned words in live audio and recordings, running entirely on your PC: no account, no cloud, "
            "nothing you hear leaves your computer.")).pack(anchor="w", padx=16, pady=(4, 8))
        links = ttk.Frame(f)
        links.pack(anchor="w", padx=16, pady=(0, 8))
        if SUPPORT_URL:
            ttk.Button(links, text="♥  Support Bleepline", style="Accent.TButton",
                       command=lambda: webbrowser.open(SUPPORT_URL)).pack(side="left", padx=(0, 8))
        if PROJECT_URL:
            ttk.Button(links, text="Project page", command=lambda: webbrowser.open(PROJECT_URL)).pack(
                side="left", padx=(0, 8))
        ttk.Button(links, text="License", command=lambda: self._open_doc("LICENSE")).pack(side="left", padx=(0, 8))
        ttk.Button(links, text="Third-party licenses",
                   command=lambda: self._open_doc("THIRD_PARTY_NOTICES.txt")).pack(side="left")
        upd = ttk.Frame(f)
        upd.pack(anchor="w", padx=16, pady=(0, 8))
        ttk.Button(upd, text="Check for updates", command=self._check_updates).pack(side="left")
        self.update_status = ttk.Label(upd, text="", foreground=GREY)
        self.update_status.pack(side="left", padx=10)

        credits = ttk.LabelFrame(f, text="Built with")
        credits.pack(fill="x", padx=10, pady=6)
        for row, (name, use, lic) in enumerate((
                ("OpenAI Whisper", "speech recognition models", "MIT"),
                ("faster-whisper and CTranslate2", "fast Whisper engine (SYSTRAN)", "MIT"),
                ("Silero VAD", "detects when someone is speaking", "MIT"),
                ("better_profanity", "the optional strict word list", "MIT"),
                ("LDNOOBW", "word lists for other languages", "CC BY 4.0"),
                ("sounddevice / PortAudio", "audio devices", "MIT"),
                ("SoundCard", "desktop audio capture", "BSD-3-Clause"),
                ("NumPy", "audio processing", "BSD-3-Clause"),
                ("PyAV / FFmpeg", "reading and writing recordings", "BSD-3-Clause / LGPL-3.0"),
                ("Hugging Face Hub", "model downloads", "Apache-2.0"),
                ("Sun Valley ttk theme", "dark and light look", "MIT"),
                ("OBS Studio", "the OBS plugin builds on libobs", "GPL-2.0"),
                ("VB-CABLE by VB-Audio", "virtual cable for some modes (separate download)", "donationware"),
                ("Intel oneMKL and OpenMP", "fast math inside CTranslate2", "Intel Simplified Software License"),
                ("oneDNN", "neural network maths inside CTranslate2", "Apache-2.0"),
                ("NVIDIA cuBLAS", "optional GPU acceleration (downloaded on request)", "NVIDIA license"),
                ("Python and Tcl/Tk", "runtime and window toolkit", "PSF / BSD-style"),
        )):
            ttk.Label(credits, text=name).grid(row=row, column=0, sticky="w", padx=(8, 16), pady=1)
            ttk.Label(credits, text=use, foreground=GREY).grid(row=row, column=1, sticky="w", pady=1)
            ttk.Label(credits, text=lic, foreground=GREY).grid(row=row, column=2, sticky="w", padx=(16, 8), pady=1)
        ttk.Label(credits, foreground=GREY, text="Full license texts of every bundled component: Third-party licenses."
                  ).grid(row=99, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 6))

        ttk.Label(f, foreground=GREY, wraplength=WRAP, justify="left", text=(
            "Bleepline is provided as is, without warranty. Speech recognition makes mistakes: it can miss "
            "words or mute harmless ones, so it lowers the risk of a strike but can't guarantee your stream or "
            "recording is clean.")).pack(anchor="w", padx=16, pady=(8, 12))

    def _open_doc(self, name: str) -> None:
        path = APP_DIR / name
        if path.exists():
            os.startfile(path)
        else:
            messagebox.showinfo(APP_NAME, f"{name} is missing from {APP_DIR}.")

    def _build_manage_tab(self):
        f = self.manage_tab
        store = ttk.LabelFrame(f, text="What Bleepline has downloaded")
        store.pack(fill="x", padx=10, pady=(12, 6))
        self.storage_grid = ttk.Frame(store)
        self.storage_grid.pack(fill="x", padx=8, pady=6)
        self.storage_grid.columnconfigure(0, weight=1)
        row = ttk.Frame(store)
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.storage_total = ttk.Label(row, text="", font=("Segoe UI", 9, "bold"))
        self.storage_total.pack(side="left")
        self.remove_all_btn = ttk.Button(row, text="Remove everything Bleepline downloaded",
                                         command=self._remove_everything)
        self.remove_all_btn.pack(side="right")
        ttk.Label(store, foreground=GREY, wraplength=WRAP, justify="left",
                  text=f"Stored in {DATA_DIR} (the OBS plugin lives in OBS's plugin folder).").pack(
            anchor="w", padx=8, pady=(0, 8))

        app = ttk.LabelFrame(f, text="The app")
        app.pack(fill="x", padx=10, pady=6)
        self.app_info = ttk.Label(app, wraplength=WRAP, justify="left")
        self.app_info.pack(anchor="w", padx=8, pady=(6, 4))
        self.app_btn = ttk.Button(app)
        self.app_btn.pack(anchor="w", padx=8, pady=(0, 8))

        cable = ttk.LabelFrame(f, text="VB-CABLE")
        cable.pack(fill="x", padx=10, pady=6)
        ttk.Label(cable, wraplength=WRAP, justify="left",
                  text=("VB-CABLE is a separate driver from VB-Audio, so Bleepline can't remove it for you. To "
                        "uninstall it, run VBCABLE_Setup_x64.exe from its download as administrator, click "
                        "Remove Driver, then reboot.")).pack(anchor="w", padx=8, pady=6)

    def _refresh_manage(self):
        for w in self.storage_grid.winfo_children():
            w.destroy()
        items = maintenance.storage_items()
        for r, (key, label, size) in enumerate(items):
            ttk.Label(self.storage_grid, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Label(self.storage_grid, text=maintenance.human_size(size), foreground=GREY).grid(
                row=r, column=1, sticky="e", padx=10)
            ttk.Button(self.storage_grid, text="Reset" if key == "settings" else "Remove",
                       command=lambda k=key, lbl=label: self._remove_item(k, lbl)).grid(row=r, column=2, pady=2)
        if not items:
            ttk.Label(self.storage_grid, text="Nothing downloaded.", foreground=GREY).grid(row=0, column=0, sticky="w")
        self.storage_total.config(text=f"Total: {maintenance.human_size(sum(s for _, _, s in items))}")
        self.remove_all_btn.state(["!disabled"] if items else ["disabled"])

        if maintenance.is_installed_copy():
            self.app_info.config(text=f"{APP_NAME} {VERSION} is installed in {APP_DIR}. Uninstalling removes it from "
                                      "Apps & features, deletes its shortcuts, and lets you choose whether to "
                                      "remove the downloads above and the OBS plugin too.")
            self.app_btn.config(text=f"Uninstall {APP_NAME}...", command=self._uninstall)
        elif IS_FROZEN:
            self.app_info.config(text=f"This is the portable version in {APP_DIR}. To remove it, click \"Remove "
                                      "everything\" above, close Bleepline and delete that folder.")
            self.app_btn.config(text="Open the app folder",
                                command=lambda: subprocess.Popen(["explorer", str(APP_DIR)]))
        else:
            self.app_info.config(text=f"Running from source in {APP_DIR}.")
            self.app_btn.config(text="Open the app folder",
                                command=lambda: subprocess.Popen(["explorer", str(APP_DIR)]))

    def _can_remove(self):
        if self.engine is not None or self._starting or self._cleaning or self._busy:
            messagebox.showinfo(APP_NAME, "Stop filtering and wait for any download or cleaning job to finish first.")
            return False
        self._live_models.clear()  # release model files so Windows lets us delete them
        gc.collect()
        return True

    def _after_removal(self):
        self._refresh_models()
        self._refresh_setup_status()
        self._update_mode_status()
        self._refresh_lists()
        self._refresh_sounds()
        self._refresh_manage()

    def _remove_item(self, key, label):
        if not self._can_remove():
            return
        verb = "Reset" if key == "settings" else "Remove"
        if not messagebox.askyesno(APP_NAME, f"{verb} {label}?"):
            return
        try:
            maintenance.remove_item(key)
            if key == "settings":
                self._wiped = True
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not remove {label}:\n{exc}")
        self._after_removal()

    def _remove_everything(self):
        if not self._can_remove():
            return
        total = maintenance.human_size(sum(s for _, _, s in maintenance.storage_items()))
        if not messagebox.askyesno(APP_NAME, f"Remove all speech models, the GPU runtime, the OBS plugin, word lists, "
                                             f"sounds and your settings ({total})? You can download them again."):
            return
        try:
            maintenance.remove_all_downloads()
            self._wiped = True
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Some files could not be removed:\n{exc}")
        self._after_removal()

    def _uninstall(self):
        if not messagebox.askyesno(APP_NAME, f"Uninstall {APP_NAME}? You'll choose what else to remove next."):
            return
        self._collect_settings()
        if not winsetup.run_uninstaller():  # it closes this window itself
            messagebox.showerror(APP_NAME, "The uninstaller wasn't found. Use Settings > Apps instead.")

    # =====================================================================
    # Live engine control
    # =====================================================================

    def _readiness(self):
        if st.missing_packages():
            return False, "Install the Python packages first (Setup tab)."
        if not self.live_model_var.get():
            return False, "Download a speech model first (Models tab)."
        mode = self.mode_var.get()
        if mode == "stream" and not self._cable_playback():
            return False, "This mode needs VB-CABLE (Setup tab)."
        if mode == "listen" and not self._cable_capture():
            return False, "This mode needs VB-CABLE (Setup tab)."
        return True, ""

    def _toggle(self):
        if self.engine is not None:
            self._stop()
        else:
            self._start()

    def _collect_settings(self):
        s = self.settings
        s.mode = self.mode_var.get()
        s.delay_ms = round(self.delay_var.get() / 10) * 10
        s.live_model = self.live_model_var.get() or s.live_model
        s.file_model = self.file_model_var.get() or s.file_model
        s.auto_start = self.auto_start_var.get()
        s.desktop_device = self.desktop_var.get() or s.desktop_device
        s.monitor_device = self.monitor_var.get() or s.monitor_device
        s.input_device = self.input_var.get() or s.input_device
        s.output_device = self.output_var.get() or s.output_device
        s.builtin_lists = [key for key, var in self._builtin_vars.items() if var.get()]
        s.disabled_lists = self._disabled_lists()
        s.catch_censored = self.censored_var.get()
        s.language = next((code for code, name in LANGUAGES if name == self.language_var.get()), "auto")
        s.min_word_prob = round(float(self.sens_var.get()), 2)
        s.early_mute = self.early_var.get()
        s.mute_pad_before_ms = int(self.pad_before_var.get())
        s.mute_pad_after_ms = int(self.pad_after_var.get())
        s.mute_mode = self.censor_var.get()
        s.beep_hz = int(self.beep_var.get())
        s.censor_volume = round(self.volume_var.get() / 100, 2)
        s.censor_sound = self.sound_var.get() or None
        s.start_minimized = self.minimized_var.get()
        s.asr_device = self.asr_device_var.get()
        if s.mute_mode == "sound" and not s.censor_sound:
            s.mute_mode = "beep"  # "my own sound" picked but no sound added yet
        s.save()

    def _route(self):
        """(input, output, capture) for the current audio mode."""
        s = self.settings
        if s.mode == "stream":
            return s.desktop_device, self._cable_playback(), "loopback"
        if s.mode == "listen":
            return self._cable_capture(), s.monitor_device, "device"
        return s.input_device, s.output_device, "device"

    def _warn_language_mismatch(self, model, log_widget):
        lang = self.settings.language
        if model.endswith(".en") and lang not in ("auto", "en"):
            self._append(log_widget, f"Note: {model} only understands English, but the language is set to "
                                     f"{dict(LANGUAGES).get(lang, lang)}. Use a multilingual model (Models tab).")

    def _start(self):
        if self.engine is not None or self._starting:
            return
        ready, reason = self._readiness()
        if not ready:
            self._log(reason)
            self._select(self.setup_tab)
            return
        self._wiped = False
        self._collect_settings()
        s = self.settings
        mode = s.mode
        inp, out, capture = self._route() if mode != "obs" else (None, None, None)
        if mode != "obs" and (not inp or not out):
            self._log("Pick the devices for this mode first.")
            return
        if mode == "obs" and not st.obs_plugin_installed():
            self._log("Note: the OBS plugin isn't installed yet, so OBS can't connect (Setup tab).")
        self._warn_language_mismatch(s.live_model, self.log_text)
        matcher = self._build_matcher()

        self._starting = True
        self.toggle_btn.config(state="disabled")
        self._set_status("Loading speech model...", AMBER)
        censor = (s.mute_mode, s.beep_hz, s.censor_volume, s.censor_sound)

        def worker():
            try:
                from .detector import load_model

                key = (s.live_model, s.asr_device)
                bundle = self._live_models.get(key)
                if bundle is None:
                    bundle = self._live_models[key] = load_model(s.live_model, s.asr_device)
                if mode == "obs":
                    from .link_server import LinkServer

                    obj = LinkServer(
                        bundle[0], s.mute_pad_before_ms, s.mute_pad_after_ms,
                        on_catch=lambda src, w, late: self._events.put(("catch", (w, late, src))),
                        on_event=lambda t: self._events.put(("log", t)),
                        on_stats=lambda src, ms: self._events.put(("stats", ms)),
                        matcher=matcher, min_prob=s.min_word_prob, language=s.language, censor=censor,
                        early_mute=s.early_mute,
                    )
                else:
                    from .audio_engine import AudioEngine

                    obj = AudioEngine(
                        input_device=inp, output_device=out, capture=capture, delay_ms=s.delay_ms,
                        mute_pad_before_ms=s.mute_pad_before_ms, mute_pad_after_ms=s.mute_pad_after_ms,
                        mute_mode=s.mute_mode, beep_hz=s.beep_hz, censor_volume=s.censor_volume,
                        censor_sound=s.censor_sound, matcher=matcher, min_prob=s.min_word_prob,
                        language=s.language, model_bundle=bundle, early_mute=s.early_mute,
                        on_catch=lambda w, late: self._events.put(("catch", (w, late, None))),
                        on_level=lambda lvl: self._events.put(("level", lvl)),
                        on_error=lambda err: self._events.put(("error", err)),
                        on_stats=lambda ms: self._events.put(("stats", ms)),
                    )
                self._events.put(("built", (obj, bundle)))
            except Exception as exc:
                self._events.put(("start_failed", str(exc)))

        # Model loading is slow, so it runs in the background. Streams must be opened on the
        # Tk thread though: PortAudio's Windows backend fails to start them from other threads.
        threading.Thread(target=worker, daemon=True).start()

    def _on_built(self, obj, bundle):
        try:
            obj.start()
        except Exception as exc:
            self._on_start_failed(str(exc))
            return
        self._starting = False
        self.engine = obj
        self.toggle_btn.config(text="Stop filtering", state="normal")
        self._set_status("Filtering", GREEN)
        self.options_note.config(text="Filtering is running: option changes apply after you stop and start it.")
        _, device, note = bundle
        self._live_device = device  # what it actually runs on (a GPU choice can fall back to the CPU)
        self.stats_label.config(text=f"{self.settings.live_model} on {DEVICE_NAMES[device]} - idle")
        for w in self._locked_widgets:
            w.state(["disabled"])
        s = self.settings
        if s.mode == "obs":
            self._log("Waiting for OBS: add the Bleepline filter to an audio source in OBS.")
        else:
            inp, out, _ = self._route()
            self._log(f"Filtering {inp}  ->  {out}  ({s.delay_ms} ms delay)")
        self._log(f"Speech model {s.live_model} on {DEVICE_NAMES[device]}, censor: {s.mute_mode}"
                  + (", early muting on" if s.early_mute else ""))
        self._slow_checks = 0
        for problem in self._windows_problems:
            self._log(f"Warning: {problem}")
        if not s.live_model.endswith(".en") and s.language == "auto":
            self._log("Tip: set a fixed language in Options. Auto-detect has to guess the language from about a "
                      "second of audio, which is slower and often wrong.")
        if device == "cpu" and s.asr_device != "cpu":
            self._log(f"GPU not available{f' ({note})' if note else ''}, running on the CPU instead.")

    def _on_start_failed(self, err):
        self._starting = False
        self.toggle_btn.config(text="Start filtering", state="normal")
        self._set_status("Stopped", RED)
        self._log(f"Could not start: {err}")

    def _stop(self):
        if self.engine is not None:
            self.engine.stop()
            self.engine = None
        self.toggle_btn.config(text="Start filtering")
        self._set_status("Stopped", RED)
        self.stats_label.config(text="")
        self.obs_sources.config(text="")
        self.options_note.config(text="")
        self.level_bar["value"] = 0
        for w in self._locked_widgets:
            w.state(["!disabled"])
        for combo in (self.desktop_combo, self.monitor_combo, self.input_combo, self.output_combo,
                      self.live_model_combo):
            combo.state(["readonly"])
        self._mode_changed()

    # =====================================================================
    # Event pump / logging
    # =====================================================================

    def _poll(self):
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "catch":
                    word, late, src = payload
                    where = f" in {src}" if src else ""
                    if late > 0:
                        self._log(f"muted \"{word}\"{where} - caught {late} ms late, its start may have been "
                                  "heard: raise the delay")
                    else:
                        self._log(f"muted \"{word}\"{where}")
                elif kind == "level":
                    db = 20 * math.log10(max(payload, 1e-6))
                    self.level_bar["value"] = max(0.0, min(100.0, (db + 60) * 100 / 60))  # -60..0 dBFS
                elif kind == "stats" and self.engine is not None:
                    self.stats_label.config(text=f"{self.settings.live_model} on {DEVICE_NAMES[self._live_device]} "
                                                 f"- {payload:.0f} ms per check")
                    self._maybe_warn_slow(payload)
                elif kind == "log":
                    self._log(payload)
                elif kind == "error":
                    if time.time() - self._last_error_at > 5:
                        self._last_error_at = time.time()
                        self._log(f"audio warning: {payload}")
                elif kind == "built":
                    self._on_built(*payload)
                elif kind == "start_failed":
                    self._on_start_failed(payload)
                elif kind == "setup_log":
                    self._setup_log(payload)
                elif kind == "progress":
                    done, total = payload
                    if total:
                        for bar, text in ((self.progress, self.progress_label),
                                          (self.model_progress, self.model_progress_label)):
                            bar["value"] = done * 100 / total
                            text.config(text=f"{done / 1e6:.0f} / {total / 1e6:.0f} MB")
                elif kind == "task_done":
                    self._task_done(*payload)
                elif kind == "clean_progress":
                    frac, stage = payload
                    self.clean_progress["value"] = frac * 100
                    self.clean_stage.config(text=stage)
                elif kind == "clean_log":
                    self._append(self.clean_log, payload)
                elif kind == "clean_done":
                    self._clean_finished(*payload)
                elif kind == "clean_failed":
                    self._clean_finished(error=payload)
                elif kind == "update":
                    self._update_result(*payload)
                elif kind == "update_failed":
                    error, manual = payload
                    if manual:
                        self.update_status.config(text=f"Couldn't reach GitHub ({error[:60]}).")
        except queue.Empty:
            pass
        if self.settings.mode == "obs" and self.engine is not None and time.time() - self._last_sources_at > 1:
            self._last_sources_at = time.time()
            names = self.engine.source_names()
            self.obs_sources.config(text="Connected OBS sources: " + (", ".join(names) if names else "none yet"))
        self.after(100, self._poll)

    def _maybe_warn_slow(self, ms: float):
        """Once per session: if checks stay slow (GPU busy with a game), say what delay would be safe."""
        if self._slow_checks < 0:
            return  # already said
        mode = self.mode_var.get()
        delay = round(self.delay_var.get() / 10) * 10 if mode != "obs" else 500
        # reaction ~ wait for the next check (1.25x) + the check itself + the word being spoken (~400 ms)
        safe = int(math.ceil((2.25 * ms + 400) / 50) * 50)
        self._slow_checks = self._slow_checks + 1 if safe > delay else 0
        if self._slow_checks == SLOW_CHECKS:
            self._slow_checks = -1
            where = "the delay in the Bleepline filter in OBS" if mode == "obs" else "the Delay slider"
            busy = {"cuda": "the GPU", "split": "the CPU or GPU"}.get(self._live_device, "the CPU")
            self._log(f"Checks are taking {ms:.0f} ms ({busy} is busy, probably with a game). At {delay} ms delay "
                      f"some words may get through: raise {where} to about {min(safe, 1500)} ms, use a smaller "
                      "model, or set a fixed language in Options.")

    @staticmethod
    def _make_log(parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=6, pady=6)
        text = tk.Text(frame, height=8, state="disabled", wrap="word", font=("Consolas", 9))
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        return text

    @staticmethod
    def _append(widget, text):
        widget.config(state="normal")
        widget.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        widget.see("end")
        widget.config(state="disabled")

    def _log(self, text):
        self._append(self.log_text, text)

    def _setup_log(self, text):
        self._append(self.setup_log_text, text)

    def _on_close(self):
        self._stop()
        if not self._wiped:
            self._collect_settings()
        self.destroy()
