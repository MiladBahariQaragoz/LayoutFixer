"""The desktop's keyboard layouts, and how the QWERTY/QWERTZ setting applies to them."""

from __future__ import annotations

import configparser
import os
from pathlib import Path

from .xkb import Layout

QWERTY_LAYOUTS = {"us", "gb", "ca", "au", "ie"}
QWERTZ_LAYOUTS = {"de", "at", "ch"}
_FAMILIES = {"qwerty": QWERTY_LAYOUTS, "qwertz": QWERTZ_LAYOUTS}
_READING_LAYOUT = {"qwerty": "us", "qwertz": "de"}


def read_kxkbrc(path: Path | None = None) -> list[tuple[str, str]]:
    """Layouts and variants from KDE's config. KDE's D-Bus list leaves the variants out."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path or Path(base) / "kxkbrc", encoding="utf-8")
    names = parser.get("Layout", "LayoutList", fallback="").split(",")
    variants = parser.get("Layout", "VariantList", fallback="").split(",")
    pairs = []
    for position, name in enumerate(names):
        if name.strip():
            variant = variants[position].strip() if position < len(variants) else ""
            pairs.append((name.strip(), variant))
    return pairs


class LayoutSet:
    """The configured layouts, and which one a wrong-layout word converts to."""

    def __init__(self, pairs: list[tuple[str, str]], latin_choice: str = "auto") -> None:
        self.latin_choice = latin_choice
        self.layouts = [Layout(index, name, variant) for index, (name, variant) in enumerate(pairs)]
        self._latin = self._pick_latin()
        self._persian = next((layout for layout in self.layouts if layout.language == "fa"), None)
        if self._latin and latin_choice in _FAMILIES and self._latin.name not in _FAMILIES[latin_choice]:
            self._latin = self._latin.with_reading(_READING_LAYOUT[latin_choice])
            self.layouts[self._latin.index] = self._latin

    def _pick_latin(self) -> Layout | None:
        latin = [layout for layout in self.layouts if layout.language == "latin"]
        family = _FAMILIES.get(self.latin_choice, set())
        return next((layout for layout in latin if layout.name in family), latin[0] if latin else None)

    @property
    def usable(self) -> bool:
        return self._latin is not None and self._persian is not None

    def other(self, current: Layout) -> Layout | None:
        if current.language == "fa":
            return self._latin
        return self._persian if current.language == "latin" else None

    def warning(self) -> str | None:
        """Explain a setup that can't work as the user expects, for the tray and the log."""
        if not self.usable:
            return "Add both a Latin and a Persian layout in System Settings → Keyboard."
        family = _FAMILIES.get(self.latin_choice)
        if family and self._latin.name not in family:
            return (
                f"Set to {self.latin_choice.upper()}, but KDE's layout is '{self._latin.name}'. Fixes come out "
                f"right, but the next words follow '{self._latin.name}'. Add a matching layout in KDE."
            )
        return None
