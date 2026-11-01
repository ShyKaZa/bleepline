"""Optional check for a newer Bleepline on GitHub. Standard library only.

It's one HTTPS request to GitHub's public releases API, at most once a day, when switched on: no account, no
identifiers, nothing about what you hear. The installer asks whether to turn it on; the portable version starts
with it off.
"""
import json
import re
import urllib.request
import winreg

from .version import APP_NAME, PROJECT_URL, VERSION

CHECK_EVERY_SEC = 24 * 3600
REGISTRY_KEY = rf"Software\{APP_NAME}"  # the installer writes CheckForUpdates here
_REPO = re.sub(r"^https://github\.com/", "", PROJECT_URL).strip("/")


def version_tuple(text: str) -> tuple[int, ...]:
    """'v1.10.2' -> (1, 10, 2); anything unparsable sorts lowest."""
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3]) or (0,)


def is_newer(version: str, than: str = VERSION) -> bool:
    return version_tuple(version) > version_tuple(than)


def installer_choice() -> bool | None:
    """What was picked in the installer (True/False), or None for the portable version and older installs."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            return bool(winreg.QueryValueEx(key, "CheckForUpdates")[0])
    except OSError:
        return None


def latest_release(timeout: float = 10) -> tuple[str, str]:
    """(version, release page URL) of the newest published release. Raises on network errors."""
    req = urllib.request.Request(f"https://api.github.com/repos/{_REPO}/releases/latest",
                                 headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return data["tag_name"].lstrip("v"), data["html_url"]
