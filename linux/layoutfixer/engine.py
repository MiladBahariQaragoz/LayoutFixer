"""Tracks what you type, spots wrong-layout words, and fixes them on Tab.

Same behaviour as Fixer in the Mac and Windows versions. This module has no dependency on the
desktop or on input devices: everything it needs goes through `Backend`, which keeps it testable.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .dictionaries import Dictionaries
from .xkb import Layout

KEY_ESC = 1
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_ENTER = 28
KEY_SPACE = 57
KEY_KPENTER = 96
_NAVIGATION_KEYS = {102, 103, 104, 105, 106, 107, 108, 109, 110, 111}  # Home..Delete
_RESET_KEYS = {KEY_ENTER, KEY_KPENTER, KEY_ESC} | _NAVIGATION_KEYS

SHIFT_KEYS = {42, 54}
SHORTCUT_MODIFIERS = {29, 97, 56, 100, 125, 126}  # Ctrl, Alt, AltGr, Meta
CAPS_LOCK = 58

SUGGESTION_SECONDS = 10.0
MAX_RUN_WORDS = 12


@dataclass(frozen=True)
class Key:
    code: int
    shift: bool
    typed: str


class Backend(Protocol):
    def current_layout(self) -> Layout | None: ...

    def other_layout(self, current: Layout) -> Layout | None: ...

    def show_suggestion(self, text: str, target: Layout) -> None: ...

    def hide_suggestion(self) -> None: ...

    def retype(self, deletes: int, keys: list[Key], target: Layout) -> None: ...


class Fixer:
    def __init__(self, dictionaries: Dictionaries, backend: Backend, clock=time.monotonic) -> None:
        self.dictionaries = dictionaries
        self.backend = backend
        self.clock = clock
        self._lock = threading.RLock()
        self._enabled = True
        self._suspended = False
        self._word: list[Key] = []
        # The word finished just before `_word`, if only a single space separates them.
        self._previous: list[Key] = []
        # Keystrokes from the first wrong word up to now, while a suggestion is showing.
        self._run: list[Key] = []
        self._run_layout: int | None = None
        self._run_words = 0
        self._expires_at: float | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
            self.reset()

    def set_suspended(self, suspended: bool) -> None:
        """Stop looking at keys while the lock screen or an excluded app has focus."""
        with self._lock:
            self._suspended = suspended
            self.reset()

    def reset(self) -> None:
        """Forget every typed key. Also called on clicks, focus changes and shortcuts."""
        with self._lock:
            showing = bool(self._run)
            self._word.clear()
            self._previous.clear()
            self._run.clear()
            self._run_layout = None
            self._run_words = 0
            self._expires_at = None
            if showing:
                self.backend.hide_suggestion()

    def tick(self) -> None:
        """Called regularly by the input loop so suggestions expire."""
        with self._lock:
            if self._expires_at is not None and self.clock() >= self._expires_at:
                self.reset()

    def handle_key(self, code: int, shift: bool, shortcut_held: bool, caps_on: bool = False) -> bool:
        """Process a key press. Returns True when the key must not reach the application."""
        with self._lock:
            if not self._enabled or self._suspended:
                return False
            if code in SHIFT_KEYS or code in SHORTCUT_MODIFIERS or code == CAPS_LOCK:
                return False
            if shortcut_held or caps_on:
                self.reset()
                return False

            if code == KEY_TAB:
                if self._run:
                    self._apply()
                    return True
                self.reset()
                return False
            if code in _RESET_KEYS:
                self.reset()
                return False
            if code == KEY_BACKSPACE:
                self._backspace()
                return False
            if code == KEY_SPACE:
                space = Key(code, shift, " ")
                if self._run:
                    self._run.append(space)
                self._finish_word(space)
                return False

            current = self.backend.current_layout()
            typed = current.on_screen(code, shift) if current else None
            if not typed:
                self.reset()
                return False
            key = Key(code, shift, typed)
            self._word.append(key)
            if self._run:
                self._run.append(key)
                self._refresh()
            return False

    def _backspace(self) -> None:
        if self._word:
            self._word.pop()
        else:
            # Deleting into the previous word: what it holds no longer matches the screen, and
            # including it in a later fix would delete text that isn't part of the mistake.
            self._previous.clear()
        if self._run:
            self._run.pop()
            if self._run:
                self._refresh()
            else:
                self.reset()

    def _finish_word(self, space: Key) -> None:
        finished = list(self._word)
        try:
            current = self.backend.current_layout()
            if not self._word or current is None:
                return
            if self._run:
                # Already suggesting: keep extending, unless you switched layouts.
                self._run_words += 1
                if current.index != self._run_layout or self._run_words > MAX_RUN_WORDS:
                    self.reset()
                else:
                    self._refresh()
                return
            other = self.backend.other_layout(current)
            if other is None:
                return
            if self._looks_wrong(self._word, current, other):
                # A short word just before (like هد -> "in") is usually part of the same mistake.
                before = self._previous + [space] if self._looks_wrong(self._previous, current, other, 1) else []
                self._run = before + self._word + [space]
                self._run_layout = current.index
                self._run_words = 1
                self._refresh()
        finally:
            self._word = []
            self._previous = finished

    def _looks_wrong(self, keys: list[Key], current: Layout, other: Layout, min_letters: int = 3) -> bool:
        if not keys:
            return False
        typed = "".join(key.typed for key in keys)
        latin = other if current.language == "fa" else current
        persian = current if current.language == "fa" else other
        return self.dictionaries.is_mistake(
            typed,
            current.language or "",
            _convert(keys, other),
            latin.dictionaries,
            bridge=latin.bridge_characters(persian),
            min_letters=min_letters,
        )

    def _target(self) -> Layout | None:
        current = self.backend.current_layout()
        return self.backend.other_layout(current) if current else None

    def _refresh(self) -> None:
        target = self._target()
        if target is None:
            return
        self.backend.show_suggestion(_convert(self._run, target).strip(), target)
        self._expires_at = self.clock() + SUGGESTION_SECONDS

    def _apply(self) -> None:
        target = self._target()
        if target is None:
            self.reset()
            return
        keys = list(self._run)
        self.reset()
        self.backend.retype(len(keys), keys, target)


def _convert(keys: list[Key], layout: Layout) -> str:
    return "".join(
        " " if key.code == KEY_SPACE else layout.character(key.code, key.shift) or key.typed for key in keys
    )
