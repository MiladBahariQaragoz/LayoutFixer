"""Command line entry point. `--test` and `--doctor` work without touching the keyboard."""

from __future__ import annotations

import argparse
import sys

from .config import LATIN_CHOICES, config_path, load
from .dictionaries import Dictionaries
from .layouts import LayoutSet, read_kxkbrc


def _test(text: str, latin: str) -> int:
    layouts = LayoutSet(read_kxkbrc(), latin)
    if not layouts.usable:
        print(layouts.warning())
        return 1
    print("layouts:", ", ".join(f"{layout.name}({layout.variant})" for layout in layouts.layouts))
    dictionaries = Dictionaries()
    persian = any("؀" <= char <= "ۿ" for char in text)
    source = next(layout for layout in layouts.layouts if layout.language == ("fa" if persian else "latin"))
    target = layouts.other(source)
    latin_side = target if persian else source
    persian_side = source if persian else target
    reverse: dict[str, tuple[int, bool]] = {}
    for key, char in source.actual.items():
        reverse.setdefault(char, key)
    for word in text.split():
        converted = "".join(
            target.character(*reverse[char]) or char if char in reverse else char for char in word
        )
        arguments = (word, source.language, converted, latin_side.dictionaries)
        bridge = latin_side.bridge_characters(persian_side)
        fix = dictionaries.is_mistake(*arguments, bridge=bridge)
        short = dictionaries.is_mistake(*arguments, bridge=bridge, min_letters=1)
        print(f"{word} -> {converted}  {'FIX' if fix else 'fix-if-next-is-wrong' if short else 'ok'}")
    return 0


def _doctor() -> int:
    from .app import missing_permissions

    config = load()
    layouts = LayoutSet(read_kxkbrc(), config.latin)
    print("config:", config_path(), f"(latin = {config.latin})")
    print("layouts:", ", ".join(f"{layout.name}({layout.variant})" for layout in layouts.layouts) or "none found")
    print("layout setup:", layouts.warning() or "OK")
    problems = missing_permissions()
    print("keyboard access:", "; ".join(problems) if problems else "OK")
    return 1 if problems or not layouts.usable else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="layoutfixer", description="Fix wrong-layout typing on KDE Plasma.")
    parser.add_argument("--test", metavar="TEXT", help="check detection on some text and exit")
    parser.add_argument("--doctor", action="store_true", help="check the setup and exit")
    parser.add_argument("--latin", choices=LATIN_CHOICES, help="override the QWERTY/QWERTZ setting for --test")
    args = parser.parse_args()
    if args.test is not None:
        return _test(args.test, args.latin or load().latin)
    if args.doctor:
        return _doctor()
    from .app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
