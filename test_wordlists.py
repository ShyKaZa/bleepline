"""Word matching, list import/download and the 'test a word' explanations (temp folder only)."""
import tempfile
from pathlib import Path

from app import wordlists as wl
from app.wordlists import WordMatcher


def test_matching_rules():
    m = WordMatcher(["Kurva", "fuck*", "son of a bitch", "# comment"], allowed=["fuckingham"])
    assert m.match_word(" kurvá,") == "kurva"  # case, accents, punctuation
    assert m.match_word("Fucking!") and m.match_word("fucker")  # wildcard
    assert m.match_word("fuckingham") is None  # allow list wins
    assert m.match_word("duck") is None and m.match_word("hello") is None
    assert m.match_word("f**k") and m.match_word("**ck")  # censored by the speech model
    assert WordMatcher([], catch_censored=False).match_word("f**k") is None
    words = "you son of a bitch".split()
    assert (1, 5, "son of a bitch") in m.find(words)
    cz = WordMatcher(["píč*", "kurv*", "čurák*", "píča"])
    assert all(cz.match_word(w) for w in ("píčo", "Píčus,", "kurvo", "KURVY", "čuráku", "pica"))
    assert not any(cz.match_word(w) for w in ("pick", "picture", "picnic", "curak"))  # accented stems keep accents
    early = WordMatcher(["fuck", "bullshit", "kurv*"], allowed=["bull"])
    assert all(early.is_partial(w) for w in ("Fuc", "bullsh", "kur"))
    assert not any(early.is_partial(w) for w in ("fu", "ku", "fun", "bull", "kurva", "pizza"))  # short/other/allowed/full
    print("PASS matching: case/accents, wildcard, accented wildcard, allow list, censored, phrases")


def test_builtin_lists():
    m = WordMatcher([w for k in wl.DEFAULT_BUILTIN for w in wl.builtin_words(k)])
    swears = ("fuck", "Fucking,", "motherfucker", "shit", "bullshit", "bitches", "asshole", "cunt", "dick", "porn",
              "blowjob", "faggot", "retarded", "bitch's", "fuckin'")
    assert not [w for w in swears if not m.match_word(w)], [w for w in swears if not m.match_word(w)]
    assert (1, 3, "blow job") in m.find("a blow job".split())
    # everyday game and stream talk that the old default (better_profanity's text-chat list) muted
    harmless = ("duck", "hello", "class", "assume", "assassin", "shot", "a", "sh", "shi", "sht", "i", "he", "kill",
                "sniper", "god", "stupid", "drunk", "weed", "fat", "ugly", "gay", "lesbian", "murder", "pawn",
                "strip", "Dickens", "cocktail", "cockpit", "analysis", "cumulative", "shell", "he'll", "who're",
                "we'll", "she'd", "hell", "damn", "ass")
    assert not [w for w in harmless if m.match_word(w)], [w for w in harmless if m.match_word(w)]
    mild = WordMatcher(wl.builtin_words("mild"))
    assert all(mild.match_word(w) for w in ("hell", "Damn!", "crap", "ass")) and not mild.match_word("he'll")
    assert (0, 3, "oh my god") in mild.find("oh my god".split())
    strict = WordMatcher(wl.builtin_words("strict"))
    assert strict.match_word("kill") and len(wl.builtin_words("strict")) > 800  # the old list, still available
    assert WordMatcher(["sh!+"]).match_word("shit") and not WordMatcher(["s.o.b."]).match_word("sob")
    sizes = {k: len(wl.builtin_words(k)) for k in wl.BUILTIN_LISTS}
    print(f"PASS built-in lists {sizes}: swears caught, everyday words and contractions left alone")


def test_storage_import_download_explain():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        wl.WORDLISTS_DIR, wl.ALLOWED_PATH, wl.CUSTOM_WORDS_PATH = tmp / "wordlists", tmp / "allowed.txt", tmp / "custom.txt"
        src = tmp / "moje slova.txt"
        src.write_bytes("zatraceně\nkrucinál, sakra\n".encode("cp1250"))  # an old Windows-1250 export
        name, count = wl.import_file(src)
        assert count == 3 and wl.imported_lists() == [(name, 3)], wl.imported_lists()

        name_cs, count_cs = wl.download_language("cs")
        assert count_cs > 20, count_cs

        wl.save_allowed(["hell"])
        wl.CUSTOM_WORDS_PATH.write_text("frack*\nhell", encoding="utf-8")
        m = wl.build_matcher()
        assert m.match_word("fracking") and m.match_word("Krucinál") and m.match_word("shit")
        assert m.match_word("hell") is None
        assert "disabled" not in wl.explain("sakra")
        assert "moje slova" in wl.explain("sakra")
        assert "allow list" in wl.explain("hell")
        assert "not on any" in wl.explain("pizza")
        assert "switched off" in wl.explain("crap") and "Mild words" in wl.explain("crap")
        assert "Mild words" in wl.explain("crap", builtin_lists=["mild"]) and "would be muted" in wl.explain(
            "crap", builtin_lists=["mild"])
        m2 = wl.build_matcher(disabled_lists=[name])
        assert m2.match_word("sakra") is None
        wl.remove_list(name)
        assert [n for n, _ in wl.imported_lists()] == [name_cs]
        print(f"PASS import (cp1250), LDNOOBW download ({name_cs}: {count_cs}), allow list, disable, explain")


if __name__ == "__main__":
    test_matching_rules()
    test_builtin_lists()
    test_storage_import_download_explain()
