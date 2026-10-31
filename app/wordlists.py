"""
Banned-word lists and matching. Standard library only.

Sources: the built-in English list, "your words", imported files and downloaded
language lists, minus an allow list. Entry syntax, one per line:
    word        exact word (case and accents ignored)
    word*       anything starting with it: fuck* also catches fucker, fucking
    two words   a phrase, matched as consecutive words (up to 4)
    # comment   ignored
"""
import re
import unicodedata
import urllib.request
from pathlib import Path

from .paths import CUSTOM_WORDS_PATH, DATA_DIR

WORDLISTS_DIR = DATA_DIR / "wordlists"
ALLOWED_PATH = DATA_DIR / "allowed_words.txt"
MAX_PHRASE = 4
PARTIAL_MIN = 3  # shortest beginning of a banned word that counts as "starts like" one, for early muting
#                  (2 would flag everyday words like "he"/"go"/"as" as the start of hell/goddamn/ass)

LDNOOBW_URL = ("https://raw.githubusercontent.com/LDNOOBW/"
               "List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/{}")
LDNOOBW_CREDIT = "Language lists: LDNOOBW project (github.com/LDNOOBW), CC BY 4.0"
# LDNOOBW list code -> (language name, Whisper language code)
LANGUAGE_LISTS = {
    "ar": ("Arabic", "ar"), "cs": ("Czech", "cs"), "da": ("Danish", "da"), "de": ("German", "de"),
    "en": ("English (extra)", "en"), "eo": ("Esperanto", None), "es": ("Spanish", "es"),
    "fa": ("Persian", "fa"), "fi": ("Finnish", "fi"), "fil": ("Filipino", "tl"), "fr": ("French", "fr"),
    "hi": ("Hindi", "hi"), "hu": ("Hungarian", "hu"), "it": ("Italian", "it"), "ja": ("Japanese", "ja"),
    "ko": ("Korean", "ko"), "nl": ("Dutch", "nl"), "no": ("Norwegian", "no"), "pl": ("Polish", "pl"),
    "pt": ("Portuguese", "pt"), "ru": ("Russian", "ru"), "sv": ("Swedish", "sv"), "th": ("Thai", "th"),
    "tr": ("Turkish", "tr"), "zh": ("Chinese", "zh"),
}
_CENSORED = re.compile(r"\w\*|\*\w")  # how Whisper sometimes writes swear words itself: f**k, **ck
_LEET = str.maketrans({"$": "s", "@": "a", "!": "i", "+": "t", "0": "o", "1": "i", "3": "e", "4": "a",
                       "5": "s", "7": "t"})


def normalize(word: str) -> str:
    """Lower-case, strip surrounding punctuation and apostrophes; keep '*' (wildcards / censored words)."""
    w = unicodedata.normalize("NFKC", word).casefold()
    return "".join(ch for ch in w if ch.isalnum() or ch == "*")


def fold(word: str) -> str:
    """Drop accents so 'kurvá' matches 'kurva'."""
    return "".join(c for c in unicodedata.normalize("NFD", word) if unicodedata.category(c) != "Mn")


def entry_token(token: str) -> str:
    """A list entry's word in matchable form. Leetspeak spellings (sh!+, a$$) are decoded, since speech is
    never transcribed that way, and dropped if what's left is too short to be safe ('s.o.b.' -> 'sob')."""
    if all(c.isalpha() or c in "'-" for c in token.rstrip("*")):
        return fold(normalize(token))
    decoded = fold(normalize(token.translate(_LEET)))
    return decoded if len(decoded.replace("*", "")) >= 4 else ""


def parse_lines(text: str) -> list[str]:
    """One entry per line; commas and semicolons also separate entries (CSV-style exports)."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries += [part.strip() for part in re.split(r"[;,\t]", line) if part.strip()]
    return entries


class WordMatcher:
    def __init__(self, words, allowed=(), catch_censored=True):
        self.exact: set[str] = set()
        self.prefixes: list[str] = []
        self.accented_prefixes: list[str] = []  # 'píč*' keeps its accents, so it can't catch English 'pick'
        self.phrases: dict[tuple, str] = {}
        for entry in words:
            tokens = entry.split()
            parts = [entry_token(t) for t in tokens]
            if not all(p.replace("*", "") for p in parts):
                continue  # an empty or dropped token would turn a phrase into something it isn't
            if 1 < len(parts) <= MAX_PHRASE:
                self.phrases[tuple(p.replace("*", "") for p in parts)] = entry.strip()
            elif len(parts) == 1:
                p = parts[0]
                if p.endswith("*") and len(p) > 1:
                    raw_stem = normalize(tokens[0]).rstrip("*")
                    if raw_stem != fold(raw_stem):
                        self.accented_prefixes.append(raw_stem)
                    else:
                        self.prefixes.append(p.rstrip("*"))
                else:
                    self.exact.add(p.replace("*", ""))
        self.allowed = {fold(normalize(a)).replace("*", "") for a in allowed}
        self.allowed.discard("")
        self.catch_censored = catch_censored
        self.max_phrase = max((len(k) for k in self.phrases), default=1)
        self.partials = {w[:k] for w in self.exact | set(self.prefixes) for k in range(PARTIAL_MIN, len(w))}
        self.accented_partials = {s[:k] for s in self.accented_prefixes for k in range(PARTIAL_MIN, len(s))}

    def is_partial(self, raw: str) -> bool:
        """True if this (possibly unfinished) word is the beginning of a banned word: 'fu', 'bulls', 'kur'."""
        raw_norm = normalize(raw).replace("*", "")
        n = fold(raw_norm)
        if len(n) < PARTIAL_MIN or n in self.allowed:
            return False
        return n in self.partials or raw_norm in self.accented_partials

    def match_word(self, raw: str) -> str | None:
        raw_norm = normalize(raw)
        n = fold(raw_norm)
        plain = n.replace("*", "")
        if not plain or plain in self.allowed:
            return None
        if plain in self.exact or any(plain.startswith(p) for p in self.prefixes):
            return plain
        raw_plain = raw_norm.replace("*", "")
        if any(raw_plain.startswith(p) for p in self.accented_prefixes):
            return raw_plain
        if self.catch_censored and _CENSORED.search(n):
            return n
        return None

    def find(self, words: list[str]) -> list[tuple[int, int, str]]:
        """(first index, end index exclusive, text) of every banned word or phrase in a word sequence."""
        hits = [(i, i + 1, t) for i, w in enumerate(words) if (t := self.match_word(w))]
        if self.phrases:
            folded = [fold(normalize(w)).replace("*", "") for w in words]
            for k in range(2, self.max_phrase + 1):
                for i in range(len(words) - k + 1):
                    phrase = self.phrases.get(tuple(folded[i:i + k]))
                    if phrase:
                        hits.append((i, i + k, phrase))
        return hits


# -- list storage ----------------------------------------------------------------

def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1250", "latin-1"):  # cp1250: older Central European exports
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def builtin_words() -> list[str]:
    import better_profanity

    path = Path(better_profanity.__file__).parent / "profanity_wordlist.txt"
    return parse_lines(_read_text(path)) if path.exists() else []


def custom_words() -> list[str]:
    return parse_lines(_read_text(CUSTOM_WORDS_PATH)) if CUSTOM_WORDS_PATH.exists() else []


def allowed_words() -> list[str]:
    return parse_lines(_read_text(ALLOWED_PATH)) if ALLOWED_PATH.exists() else []


def save_allowed(words: list[str]) -> None:
    ALLOWED_PATH.write_text("\n".join(words), encoding="utf-8")


def imported_lists() -> list[tuple[str, int]]:
    """(file name, number of entries) for every list in the word lists folder."""
    if not WORDLISTS_DIR.exists():
        return []
    return [(p.name, len(parse_lines(_read_text(p)))) for p in sorted(WORDLISTS_DIR.glob("*.txt"))]


def _unique_path(name: str) -> Path:
    WORDLISTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[<>:"/\\|?*]', "_", Path(name).stem).strip() or "list"
    path, n = WORDLISTS_DIR / f"{stem}.txt", 2
    while path.exists():
        path, n = WORDLISTS_DIR / f"{stem} ({n}).txt", n + 1
    return path


def import_file(source: Path) -> tuple[str, int]:
    entries = parse_lines(_read_text(Path(source)))
    if not entries:
        raise ValueError("No words found in that file (expected one word or phrase per line).")
    dest = _unique_path(Path(source).name)
    dest.write_text("\n".join(entries), encoding="utf-8")
    return dest.name, len(entries)


def download_language(code: str) -> tuple[str, int]:
    name, _ = LANGUAGE_LISTS[code]
    req = urllib.request.Request(LDNOOBW_URL.format(code), headers={"User-Agent": "Bleepline/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        entries = parse_lines(resp.read().decode("utf-8", "replace"))
    dest = WORDLISTS_DIR / f"{name} (LDNOOBW).txt"
    WORDLISTS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# {LDNOOBW_CREDIT}\n" + "\n".join(entries), encoding="utf-8")
    return dest.name, len(entries)


def remove_list(name: str) -> None:
    (WORDLISTS_DIR / name).unlink(missing_ok=True)


def export_words(dest: Path, words: list[str]) -> None:
    Path(dest).write_text("\n".join(words) + "\n", encoding="utf-8")


def sources(use_builtin: bool, disabled_lists, your_words) -> list[tuple[str, list[str]]]:
    out = [("Built-in English list", builtin_words())] if use_builtin else []
    out.append(("Your words", list(your_words)))
    for name, _ in imported_lists():
        if name not in disabled_lists:
            out.append((name, parse_lines(_read_text(WORDLISTS_DIR / name))))
    return out


def build_matcher(use_builtin=True, disabled_lists=(), your_words=None, catch_censored=True) -> WordMatcher:
    your_words = custom_words() if your_words is None else your_words
    words = [w for _, entries in sources(use_builtin, disabled_lists, your_words) for w in entries]
    return WordMatcher(words, allowed_words(), catch_censored)


def explain(text: str, use_builtin=True, disabled_lists=(), your_words=None, catch_censored=True) -> str:
    """Human answer for the 'test a word' box."""
    your_words = custom_words() if your_words is None else your_words
    allowed = allowed_words()
    words = text.split()
    if not words:
        return ""
    if WordMatcher([], allowed).allowed & {fold(normalize(w)).replace("*", "") for w in words}:
        return f"\"{text}\" is on your allow list, so it is never muted."
    hits = [name for name, entries in sources(use_builtin, disabled_lists, your_words)
            if WordMatcher(entries, allowed, catch_censored=False).find(words)]
    if hits:
        return f"\"{text}\" would be muted (found in: {', '.join(hits)})."
    if catch_censored and WordMatcher([], allowed, catch_censored=True).find(words):
        return f"\"{text}\" would be muted (looks like a word the speech model censored with *)."
    return f"\"{text}\" is not on any enabled list."
