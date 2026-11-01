"""User settings, stored in ~/.config/layoutfixer/config.ini."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

LATIN_CHOICES = ("auto", "qwerty", "qwertz")

# Apps where passwords are commonly typed. On Wayland LayoutFixer can't see whether a text field
# is a password field, so these are skipped entirely. Terminals are here because of sudo prompts.
DEFAULT_EXCLUDED_APPS = (
    "org.kde.konsole",
    "org.kde.yakuake",
    "alacritty",
    "kitty",
    "foot",
    "org.wezfurlong.wezterm",
    "com.mitchellh.ghostty",
    "org.keepassxc.keepassxc",
    "bitwarden",
    "1password",
    "org.kde.polkit-kde-authentication-agent-1",
    "kwalletd6",
    "ksecretd",
    "pinentry-qt",
)


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "layoutfixer" / "config.ini"


@dataclass
class Config:
    latin: str = "auto"
    excluded_apps: tuple[str, ...] = field(default=DEFAULT_EXCLUDED_APPS)


def load(path: Path | None = None) -> Config:
    parser = configparser.ConfigParser()
    parser.read(path or config_path(), encoding="utf-8")
    latin = parser.get("keyboard", "latin", fallback="auto").strip().lower()
    if latin not in LATIN_CHOICES:
        print(f"layoutfixer: ignoring latin = {latin}; expected one of {', '.join(LATIN_CHOICES)}")
        latin = "auto"
    excluded = DEFAULT_EXCLUDED_APPS
    if parser.has_option("apps", "excluded"):
        raw = parser.get("apps", "excluded")
        excluded = tuple(item.strip().lower() for item in raw.replace("\n", ",").split(",") if item.strip())
    return Config(latin=latin, excluded_apps=excluded)


def save_latin(latin: str, path: Path | None = None) -> None:
    """Change only the keyboard choice, keeping the rest of the file."""
    target = path or config_path()
    parser = configparser.ConfigParser()
    parser.read(target, encoding="utf-8")
    if not parser.has_section("keyboard"):
        parser.add_section("keyboard")
    parser.set("keyboard", "latin", latin)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as output:
        parser.write(output)
