"""Dark / light look: the Sun Valley ttk theme, matching colours for plain tk widgets, and the Windows title bar."""
import ctypes
import tkinter as tk
import winreg
from tkinter import ttk

PALETTES = {
    "dark": {"red": "#f87171", "green": "#4ade80", "amber": "#fbbf24", "muted": "#9ca3af", "accent": "#a78bfa",
             "text_bg": "#232323", "text_fg": "#e5e7eb", "select": "#5b4bb7", "header": "#1c1c1c"},
    "light": {"red": "#c0392b", "green": "#1e8449", "amber": "#b9770e", "muted": "#666666", "accent": "#7c3aed",
              "text_bg": "#ffffff", "text_fg": "#1f2937", "select": "#c4b5fd", "header": "#fafafa"},
}
COLOR_KEYS = ("red", "green", "amber", "muted", "accent")


def windows_prefers_light() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return False


def resolve(mode: str) -> str:
    """'dark', 'light' or 'system' -> 'dark' or 'light'."""
    if mode == "system":
        return "light" if windows_prefers_light() else "dark"
    return mode if mode in PALETTES else "dark"


def set_title_bar(root: tk.Tk, dark: bool) -> None:
    """Windows 10/11 immersive dark mode title bar (no effect elsewhere)."""
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # 20 on current builds, 19 on early Windows 10 builds
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), 4) == 0:
                break
    except Exception:
        pass


def style_text(widget: tk.Text, palette: dict) -> None:
    widget.configure(background=palette["text_bg"], foreground=palette["text_fg"],
                     insertbackground=palette["text_fg"], selectbackground=palette["select"],
                     selectforeground=palette["text_fg"], relief="flat", borderwidth=0,
                     highlightthickness=1, highlightbackground=palette["select"], highlightcolor=palette["accent"])


def _header_styles(palette: dict) -> dict:
    # Same as the theme's own background: after a live theme switch, ttk labels paint the theme's plain label
    # background whatever their style says, so a differently coloured header would show boxes behind text.
    bg = palette["header"]
    return {
        "Header.TFrame": {"configure": {"background": bg}},
        "Header.TLabel": {"configure": {"background": bg}},
        "HeaderMuted.TLabel": {"configure": {"background": bg, "foreground": palette["muted"]}},
        "Title.TLabel": {"configure": {"background": bg, "font": ("Segoe UI Semibold", 15)}},
        "Pill.TLabel": {"configure": {"background": bg, "font": ("Segoe UI Semibold", 10)}},
        "Update.TLabel": {"configure": {"background": bg, "foreground": palette["accent"],
                                        "font": ("Segoe UI Semibold", 10, "underline")}},
    }


def apply(root: tk.Tk, mode: str, previous: dict | None = None) -> dict:
    """Switch the whole window to `mode`; recolours existing widgets. Returns the new palette."""
    name = resolve(mode)
    palette = PALETTES[name]
    style = ttk.Style(root)
    try:
        import sv_ttk

        sv_ttk.get_theme()  # loads both Sun Valley themes
        # Register the header styles inside each theme before switching, so the switch redraws them right
        # away (styles configured after the switch leave labels painted with the plain label background).
        for theme_name, colors in PALETTES.items():
            style.theme_settings(f"sun-valley-{theme_name}", _header_styles(colors))
        sv_ttk.set_theme(name)
    except ImportError:  # running from source without requirements installed: plain Windows look
        for widget_style, options in _header_styles(palette).items():
            style.configure(widget_style, **options["configure"])
    swap = {previous[k].lower(): palette[k] for k in COLOR_KEYS} if previous else {}

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Text):
                style_text(child, palette)
            elif isinstance(child, tk.Canvas):  # scrollable tab bodies
                child.configure(background=palette["header"])
            elif isinstance(child, ttk.Label):
                color = str(child.cget("foreground")).lower()
                if color in swap:
                    child.configure(foreground=swap[color])
            walk(child)

    walk(root)
    set_title_bar(root, name == "dark")
    return palette
