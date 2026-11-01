"""Key-to-character lookup using libxkbcommon, the library KWin itself uses for keymaps.

Each layout is compiled once into a table of {(evdev code, shift): character}. This is what makes
the conversion exact for any layout pair, including a German QWERTZ keyboard paired with Persian.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass, field

# evdev key codes are offset by 8 from XKB keycodes.
_XKB_OFFSET = 8
_MAX_KEY = 248


class _RuleNames(ctypes.Structure):
    _fields_ = [
        ("rules", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p),
        ("options", ctypes.c_char_p),
    ]


def _load_library() -> ctypes.CDLL:
    path = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
    lib = ctypes.CDLL(path)
    pointer = ctypes.c_void_p
    lib.xkb_context_new.restype = pointer
    lib.xkb_context_new.argtypes = [ctypes.c_int]
    lib.xkb_context_unref.argtypes = [pointer]
    lib.xkb_keymap_new_from_names.restype = pointer
    lib.xkb_keymap_new_from_names.argtypes = [pointer, ctypes.POINTER(_RuleNames), ctypes.c_int]
    lib.xkb_keymap_unref.argtypes = [pointer]
    lib.xkb_keymap_mod_get_index.restype = ctypes.c_uint32
    lib.xkb_keymap_mod_get_index.argtypes = [pointer, ctypes.c_char_p]
    lib.xkb_state_new.restype = pointer
    lib.xkb_state_new.argtypes = [pointer]
    lib.xkb_state_unref.argtypes = [pointer]
    lib.xkb_state_update_mask.restype = ctypes.c_int
    lib.xkb_state_update_mask.argtypes = [pointer] + [ctypes.c_uint32] * 6
    lib.xkb_state_key_get_utf8.restype = ctypes.c_int
    lib.xkb_state_key_get_utf8.argtypes = [pointer, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    return lib


def compile_table(layout: str, variant: str = "") -> dict[tuple[int, bool], str]:
    """Return the characters a layout produces, without Shift and with it."""
    lib = _load_library()
    context = lib.xkb_context_new(0)
    if not context:
        raise RuntimeError("could not create an XKB context")
    names = _RuleNames(None, None, layout.encode(), variant.encode(), None)
    keymap = lib.xkb_keymap_new_from_names(context, ctypes.byref(names), 0)
    if not keymap:
        lib.xkb_context_unref(context)
        raise RuntimeError(f"unknown keyboard layout: {layout}({variant})")
    state = lib.xkb_state_new(keymap)
    shift_mask = 1 << lib.xkb_keymap_mod_get_index(keymap, b"Shift")
    table: dict[tuple[int, bool], str] = {}
    buffer = ctypes.create_string_buffer(16)
    try:
        for shift in (False, True):
            lib.xkb_state_update_mask(state, shift_mask if shift else 0, 0, 0, 0, 0, 0)
            for code in range(1, _MAX_KEY):
                size = lib.xkb_state_key_get_utf8(state, code + _XKB_OFFSET, buffer, len(buffer))
                if size <= 0 or size >= len(buffer):
                    continue
                text = buffer.value.decode("utf-8", "replace")
                if text and all(ch.isprintable() or ch == "‌" for ch in text):
                    table[(code, shift)] = text
    finally:
        lib.xkb_state_unref(state)
        lib.xkb_keymap_unref(keymap)
        lib.xkb_context_unref(context)
    return table


def language_of(layout: str) -> str | None:
    """Group a layout into the two sides LayoutFixer fixes between."""
    if layout == "ir":
        return "fa"
    return "latin" if layout in LATIN_DICTIONARIES else None


# Which word lists count as "a real word" when a Latin layout is active. People on a German
# keyboard usually write both German and English, so both have to be left alone.
LATIN_DICTIONARIES: dict[str, tuple[str, ...]] = {
    "us": ("en",),
    "gb": ("en",),
    "ca": ("en",),
    "au": ("en",),
    "ie": ("en",),
    "de": ("en", "de"),
    "at": ("en", "de"),
    "ch": ("en", "de"),
}


@dataclass
class Layout:
    """One keyboard layout from the desktop's configured list."""

    index: int
    name: str
    variant: str = ""
    display_name: str = ""
    # How wrong-layout keys are read. Differs from what the desktop layout produces only when the
    # config forces QWERTY or QWERTZ; see `with_reading`.
    table: dict[tuple[int, bool], str] = field(default_factory=dict, repr=False)
    # What the desktop layout really produces for each key.
    actual: dict[tuple[int, bool], str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.actual:
            self.actual = compile_table(self.name, self.variant)
        if not self.table:
            self.table = self.actual
        if not self.display_name:
            self.display_name = "فارسی" if self.name == "ir" else self.name

    def with_reading(self, reading_layout: str) -> "Layout":
        """Read keys as `reading_layout`, while the desktop keeps using this layout."""
        return Layout(
            self.index,
            self.name,
            self.variant,
            self.display_name,
            table=compile_table(reading_layout),
            actual=self.actual,
        )

    @property
    def language(self) -> str | None:
        return language_of(self.name)

    @property
    def dictionaries(self) -> tuple[str, ...]:
        return LATIN_DICTIONARIES.get(self.name, ())

    def character(self, code: int, shift: bool) -> str | None:
        """The character a key means when converting a wrong-layout word to this layout."""
        return self.table.get((code, shift))

    def on_screen(self, code: int, shift: bool) -> str | None:
        """The character a key really types while this layout is active."""
        return self.actual.get((code, shift))

    def output_key(self, code: int, shift: bool) -> tuple[int, bool]:
        """The key to press so the desktop layout types what `character` promised."""
        wanted = self.character(code, shift)
        if wanted is None or self.actual.get((code, shift)) == wanted:
            return code, shift
        for key, char in self.actual.items():
            if char == wanted:
                return key
        return code, shift

    def bridge_characters(self, other: "Layout") -> frozenset[str]:
        """Punctuation here that is a letter on the other layout (`,` is و on Persian keys)."""
        bridge = set()
        for (code, shift), char in self.actual.items():
            twin = other.character(code, shift)
            if twin and twin.isalpha() and not char.isalpha():
                bridge.add(char)
        return frozenset(bridge)
