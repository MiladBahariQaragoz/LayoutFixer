"""Decides whether a word was typed on the wrong layout, in either direction.

A suggestion is only made when the other reading is a real word, so names, brands and typos
("github", "teh") are left alone. Same rules as the Mac and Windows versions, extended so a Latin
layout can stand for more than one language.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Iterable
from pathlib import Path

# Only the most common Persian words count as a real reading; the long tail of the list is noisy.
PERSIAN_LIMIT = 30000

_PACKAGE = Path(__file__).resolve().parent
_SEARCH_PATHS = [
    _PACKAGE.parent / "data",
    _PACKAGE.parent.parent / "Resources",
    _PACKAGE.parent.parent / "windows" / "data",
]


def find_word_list(name: str) -> Path | None:
    override = os.environ.get("LAYOUTFIXER_DATA")
    for directory in ([Path(override)] if override else []) + _SEARCH_PATHS:
        if (directory / name).is_file():
            return directory / name
    return None


def normalize(text: str) -> str:
    text = text.lower().replace("ي", "ی").replace("ك", "ک")
    start, end = 0, len(text)

    def trims(char: str) -> bool:
        return char.isspace() or unicodedata.category(char)[0] in "PS"

    while start < end and trims(text[start]):
        start += 1
    while end > start and trims(text[end - 1]):
        end -= 1
    return text[start:end]


def _load_ranks(name: str) -> dict[str, int]:
    path = find_word_list(name)
    ranks: dict[str, int] = {}
    if path is None:
        return ranks
    with path.open(encoding="utf-8") as lines:
        for rank, line in enumerate(lines):
            ranks.setdefault(normalize(line.split(" ")[0]), rank)
    return ranks


class Dictionaries:
    def __init__(self, languages: Iterable[str] = ("en", "de")) -> None:
        self.persian = _load_ranks("fa_50k.txt")
        self.ranks = {language: _load_ranks(f"{language}_50k.txt") for language in languages}
        self.known = {language: set(ranks) for language, ranks in self.ranks.items()}
        webster = find_word_list("words.txt")
        if webster and "en" in self.known:
            with webster.open(encoding="utf-8") as lines:
                self.known["en"].update(line.strip().lower() for line in lines)

    def _persian_rank(self, text: str) -> int | None:
        rank = self.persian.get(normalize(text))
        return rank if rank is not None and rank < PERSIAN_LIMIT else None

    def _latin_rank(self, word: str, languages: Iterable[str]) -> int | None:
        found = [self.ranks[lang][word] for lang in languages if word in self.ranks.get(lang, {})]
        return min(found) if found else None

    def _is_known(self, word: str, languages: Iterable[str]) -> bool:
        return any(word in self.known.get(language, ()) for language in languages)

    def is_mistake(
        self,
        typed: str,
        typed_language: str,
        converted: str,
        languages: Iterable[str],
        bridge: Iterable[str] = (),
        min_letters: int = 3,
    ) -> bool:
        """True when the word was typed on the wrong layout.

        `languages` are the word lists of the Latin side. `bridge` is the punctuation on the Latin
        side that is a letter on Persian keys. `min_letters` guards against short words, which are
        often ambiguous (شد is "an" on English keys).
        """
        if typed_language == "fa":
            # Typed on Persian keys: the Latin reading must be a common word, and more common
            # than the Persian word itself if the typed text is real Persian.
            # A Persian letter that reads as punctuation means this isn't a Latin word: without
            # this check, trimming the punctuation turns وعده (",uni") into the word "uni".
            if any(a.isalpha() and not b.isalpha() for a, b in zip(typed, converted)):
                return False
            reading = normalize(converted)
            if len(reading) < min_letters or not all(_is_latin_letter(c) for c in reading):
                return False
            latin = self._latin_rank(reading, languages)
            if latin is None:
                return False
            persian = self._persian_rank(typed)
            return persian is None or latin < persian

        # Typed on Latin keys: never touch a real word, and only suggest Persian when the Persian
        # reading is a real Persian word.
        word = normalize(typed)
        if len(word) < min_letters or not any(c.isalpha() for c in word):
            return False
        if not all(c.isalpha() or c in bridge for c in word):
            return False
        if self._is_known(word, languages):
            return False
        # don't, it's, ...
        if "'" in word and self._is_known(word.split("'")[0], languages):
            return False
        return self._persian_rank(converted) is not None


def _is_latin_letter(char: str) -> bool:
    return char.isalpha() and "LATIN" in unicodedata.name(char, "")
