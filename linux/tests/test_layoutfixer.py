"""Run from linux/: python3 -m unittest discover tests"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layoutfixer import config
from layoutfixer.dictionaries import Dictionaries, normalize
from layoutfixer.engine import KEY_BACKSPACE, KEY_ESC, KEY_SPACE, KEY_TAB, SUGGESTION_SECONDS, Fixer
from layoutfixer.keyboard import KeyRouter, is_keyboard
from layoutfixer.layouts import LayoutSet, read_kxkbrc

DICTIONARIES = Dictionaries()
GERMAN_PERSIAN = [("de", ""), ("ir", "winkeys")]


class FakeBackend:
    def __init__(self, layouts: LayoutSet, active: int = 0) -> None:
        self.layouts = layouts
        self.active = active
        self.shown: list[str] = []
        self.hidden = 0
        self.fixes: list[tuple[int, str, str]] = []

    def current_layout(self):
        return self.layouts.layouts[self.active]

    def other_layout(self, current):
        return self.layouts.other(current)

    def show_suggestion(self, text, target):
        self.shown.append(text)

    def hide_suggestion(self):
        self.hidden += 1

    def retype(self, deletes, keys, target):
        output = "".join(target.on_screen(*target.output_key(key.code, key.shift)) for key in keys)
        self.fixes.append((deletes, output, target.name))


class Typist:
    """Presses the physical keys that produce some text on the active layout."""

    def __init__(self, fixer: Fixer, backend: FakeBackend) -> None:
        self.fixer = fixer
        self.backend = backend

    def type(self, text: str) -> None:
        layout = self.backend.current_layout()
        keys = {}
        for key, char in layout.actual.items():
            keys.setdefault(char, key)
        for char in text:
            if char == " ":
                self.fixer.handle_key(KEY_SPACE, False, False)
            else:
                code, shift = keys[char]
                self.fixer.handle_key(code, shift, False)


def make(pairs=None, active=0, latin="auto"):
    layouts = LayoutSet(pairs or GERMAN_PERSIAN, latin)
    backend = FakeBackend(layouts, active)
    fixer = Fixer(DICTIONARIES, backend)
    return fixer, backend, Typist(fixer, backend)


class DetectionTest(unittest.TestCase):
    def test_english_typed_on_persian_keys_is_flagged(self):
        fixer, backend, typist = make(active=1)
        typist.type("صشدف ")
        self.assertEqual(backend.shown, ["want"])

    def test_persian_typed_on_german_keys_is_flagged(self):
        fixer, backend, typist = make(active=0)
        typist.type("sghl ")
        self.assertEqual(backend.shown, ["سلام"])

    def test_german_and_english_words_are_left_alone(self):
        fixer, backend, typist = make(active=0)
        typist.type("ihn öde lege want github ")
        self.assertEqual(backend.shown, [])

    def test_real_persian_is_left_alone(self):
        fixer, backend, typist = make(active=1)
        typist.type("سلام کتاب ")
        self.assertEqual(backend.shown, [])

    def test_persian_letter_on_a_punctuation_key_is_not_trimmed_away(self):
        self.assertFalse(DICTIONARIES.is_mistake("وعده", "fa", ",uni", ("en", "de")))

    def test_normalize_unifies_arabic_letters(self):
        self.assertEqual(normalize("كي،"), "کی")


class FixTest(unittest.TestCase):
    def test_tab_retypes_the_whole_run_and_is_swallowed(self):
        fixer, backend, typist = make(active=1)
        typist.type("صشدف لقشذ ")
        self.assertTrue(fixer.handle_key(KEY_TAB, False, False))
        self.assertEqual(backend.fixes, [(10, "want grab ", "de")])

    def test_tab_passes_through_without_a_suggestion(self):
        fixer, backend, typist = make(active=1)
        self.assertFalse(fixer.handle_key(KEY_TAB, False, False))

    def test_short_word_before_a_mistake_joins_the_fix(self):
        fixer, backend, typist = make(active=1)
        typist.type("هد صشدف ")
        fixer.handle_key(KEY_TAB, False, False)
        self.assertEqual(backend.fixes[0][1], "in want ")

    def test_backspace_into_previous_word_cannot_delete_too_much(self):
        fixer, backend, typist = make(active=1)
        typist.type("هد ")
        for _ in range(3):
            fixer.handle_key(KEY_BACKSPACE, False, False)
        typist.type("صشدف ")
        fixer.handle_key(KEY_TAB, False, False)
        self.assertEqual(backend.fixes, [(5, "want ", "de")])

    def test_escape_dismisses(self):
        fixer, backend, typist = make(active=1)
        typist.type("صشدف ")
        fixer.handle_key(KEY_ESC, False, False)
        self.assertEqual(backend.hidden, 1)
        self.assertFalse(fixer.handle_key(KEY_TAB, False, False))

    def test_shortcut_clears_the_buffer(self):
        fixer, backend, typist = make(active=1)
        typist.type("صشد")
        fixer.handle_key(33, False, True)
        typist.type("ف ")
        self.assertEqual(backend.shown, [])

    def test_suggestion_expires(self):
        now = [0.0]
        layouts = LayoutSet(GERMAN_PERSIAN)
        backend = FakeBackend(layouts, 1)
        fixer = Fixer(DICTIONARIES, backend, clock=lambda: now[0])
        Typist(fixer, backend).type("صشدف ")
        now[0] = SUGGESTION_SECONDS + 1
        fixer.tick()
        self.assertEqual(backend.hidden, 1)

    def test_suspended_engine_sees_nothing(self):
        fixer, backend, typist = make(active=1)
        fixer.set_suspended(True)
        typist.type("صشدف ")
        self.assertEqual(backend.shown, [])


class LatinChoiceTest(unittest.TestCase):
    def test_auto_follows_the_desktop_layout(self):
        fixer, backend, typist = make(active=1, latin="auto")
        typist.type("غثس ")  # physical y-e-s: "zes" on QWERTZ, not a word
        self.assertEqual(backend.shown, [])

    def test_qwerty_reads_the_y_key_as_y(self):
        fixer, backend, typist = make(active=1, latin="qwerty")
        typist.type("غثس ")
        self.assertEqual(backend.shown, ["yes"])

    def test_qwerty_on_a_german_desktop_still_types_the_promised_text(self):
        fixer, backend, typist = make(active=1, latin="qwerty")
        typist.type("غثس ")
        fixer.handle_key(KEY_TAB, False, False)
        self.assertEqual(backend.fixes, [(4, "yes ", "de")])

    def test_qwertz_reads_the_z_key_as_y_on_a_us_desktop(self):
        fixer, backend, typist = make([("us", ""), ("ir", "winkeys")], active=1, latin="qwertz")
        typist.type("ظثس ")  # physical z-e-s: "yes" on QWERTZ
        fixer.handle_key(KEY_TAB, False, False)
        self.assertEqual(backend.fixes, [(4, "yes ", "us")])

    def test_matching_desktop_layout_is_preferred_and_needs_no_warning(self):
        layouts = LayoutSet([("de", ""), ("us", ""), ("ir", "winkeys")], "qwerty")
        self.assertEqual(layouts.other(layouts.layouts[2]).name, "us")
        self.assertIsNone(layouts.warning())

    def test_mismatch_warns(self):
        self.assertIn("QWERTY", LayoutSet(GERMAN_PERSIAN, "qwerty").warning())

    def test_missing_persian_warns(self):
        self.assertFalse(LayoutSet([("de", "")]).usable)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "layoutfixer" / "config.ini"

    def test_defaults(self):
        loaded = config.load(self.path)
        self.assertEqual(loaded.latin, "auto")
        self.assertIn("org.kde.konsole", loaded.excluded_apps)

    def test_save_keeps_other_settings(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[apps]\nexcluded = foo, Bar\n", encoding="utf-8")
        config.save_latin("qwertz", self.path)
        loaded = config.load(self.path)
        self.assertEqual(loaded.latin, "qwertz")
        self.assertEqual(loaded.excluded_apps, ("foo", "bar"))

    def test_invalid_choice_falls_back_to_auto(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[keyboard]\nlatin = dvorak\n", encoding="utf-8")
        self.assertEqual(config.load(self.path).latin, "auto")

    def test_reads_kxkbrc_variants(self):
        path = Path(self.directory.name) / "kxkbrc"
        path.write_text("[Layout]\nLayoutList=de,ir\nVariantList=,winkeys\nUse=true\n", encoding="utf-8")
        self.assertEqual(read_kxkbrc(path), GERMAN_PERSIAN)


class KeyRouterTest(unittest.TestCase):
    def setUp(self):
        self.fixer, self.backend, self.typist = make(active=1)
        self.router = KeyRouter(self.fixer)

    def test_tab_press_repeat_and_release_are_all_dropped_after_a_fix(self):
        self.typist.type("صشدف ")
        self.assertFalse(self.router.process(KEY_TAB, 1))
        self.assertFalse(self.router.process(KEY_TAB, 2))
        self.assertFalse(self.router.process(KEY_TAB, 0))
        self.assertTrue(self.router.process(KEY_TAB, 1))

    def test_ordinary_keys_are_forwarded(self):
        self.assertTrue(self.router.process(30, 1))
        self.assertTrue(self.router.process(30, 0))

    def test_key_repeat_forgets_the_buffer(self):
        self.typist.type("صشدف ")
        self.assertTrue(self.router.process(30, 2))
        self.assertEqual(self.backend.hidden, 1)

    def test_engine_failure_still_forwards_the_key(self):
        self.fixer.handle_key = lambda *_: 1 / 0
        self.assertTrue(self.router.process(30, 1))

    def test_shortcut_modifier_is_tracked_until_release(self):
        self.router.process(29, 1)
        self.typist.type("صشد")
        self.router.process(33, 1)
        self.router.process(29, 0)
        self.typist.type("ف ")
        self.assertEqual(self.backend.shown, [])


class DeviceFilterTest(unittest.TestCase):
    def test_only_plain_keyboards_are_grabbed(self):
        self.assertTrue(is_keyboard({30, 44}, False, "Some Keyboard"))
        self.assertFalse(is_keyboard({30, 44}, True, "Keyboard with touchpad"))
        self.assertFalse(is_keyboard({115, 114}, False, "Consumer Control"))
        self.assertFalse(is_keyboard({30, 44}, False, "LayoutFixer virtual keyboard"))


if __name__ == "__main__":
    unittest.main()
