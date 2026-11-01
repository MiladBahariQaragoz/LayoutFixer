"""Reads the physical keyboards and passes keys on through a virtual one.

The keyboards are grabbed so Tab can be swallowed while a suggestion is showing. Every other key is
forwarded unchanged. If this process dies for any reason, the kernel releases the grab and the
keyboards go straight back to working normally.
"""

from __future__ import annotations

import select
import sys
import threading
import time

from .engine import SHIFT_KEYS, SHORTCUT_MODIFIERS, Fixer, Key
from .xkb import Layout

VIRTUAL_NAME = "LayoutFixer virtual keyboard"
KEY_BACKSPACE = 14
KEY_LEFTSHIFT = 42
KEY_A = 30
KEY_Z = 44
_RELEASE, _PRESS, _REPEAT = 0, 1, 2
_RESCAN_SECONDS = 2.0
_REGISTER_SECONDS = 0.3
_KEY_GAP_SECONDS = 0.002
_LAYOUT_SETTLE_SECONDS = 0.03


class KeyRouter:
    """Decides, for each key event, what the engine sees and what reaches the applications."""

    def __init__(self, fixer: Fixer) -> None:
        self.fixer = fixer
        self.caps_on = False
        self._shift: set[int] = set()
        self._shortcut: set[int] = set()
        self._swallowed: set[int] = set()

    def process(self, code: int, value: int) -> bool:
        """Returns True when the event should be forwarded."""
        self._track_modifiers(code, value)
        if code in self._swallowed:
            if value == _RELEASE:
                self._swallowed.discard(code)
            return False
        if value == _REPEAT:
            # Apps repeat held keys on their own schedule on Wayland, so the buffer can no longer
            # match the screen. Forget it rather than risk deleting the wrong amount later.
            self.fixer.reset()
        elif value == _PRESS:
            try:
                swallow = self.fixer.handle_key(code, bool(self._shift), bool(self._shortcut), self.caps_on)
            except Exception as error:  # A bug here must never cost the user a keystroke.
                print(f"layoutfixer: {error!r}", file=sys.stderr)
                self.fixer.reset()
                swallow = False
            if swallow:
                self._swallowed.add(code)
                return False
        return True

    def _track_modifiers(self, code: int, value: int) -> None:
        group = self._shift if code in SHIFT_KEYS else self._shortcut if code in SHORTCUT_MODIFIERS else None
        if group is None:
            return
        if value == _RELEASE:
            group.discard(code)
        else:
            group.add(code)


def is_keyboard(keys: set[int], has_pointer: bool, name: str) -> bool:
    """A real typing keyboard. Combined keyboard-and-touchpad nodes can't be forwarded key-only."""
    return name != VIRTUAL_NAME and not has_pointer and KEY_A in keys and KEY_Z in keys


def needs_click_watch(keys: set[int], name: str) -> bool:
    btn_left, btn_touch = 0x110, 0x14a
    return name != VIRTUAL_NAME and (btn_left in keys or btn_touch in keys)


class KeyboardBridge(threading.Thread):
    def __init__(self, on_fatal=None) -> None:
        super().__init__(name="layoutfixer-input", daemon=True)
        self.fixer: Fixer | None = None
        self.router: KeyRouter | None = None
        self.on_fatal = on_fatal
        self._stop_requested = threading.Event()
        self._keyboards: dict[str, object] = {}
        self._pointers: dict[str, object] = {}
        self._ignored: set[str] = set()
        self._ui = None

    def attach(self, fixer: Fixer) -> None:
        self.fixer = fixer
        self.router = KeyRouter(fixer)

    def stop(self) -> None:
        self._stop_requested.set()
        if self.is_alive():
            self.join(timeout=2)

    def retype(self, deletes: int, keys: list[Key], target: Layout, switch_layout) -> None:
        """Replace the wrong text. Runs on this thread, so keys typed meanwhile wait their turn."""
        if not switch_layout(target.index):
            print("layoutfixer: KDE refused the layout switch, leaving the text alone", file=sys.stderr)
            return
        for _ in range(deletes):
            self._tap(KEY_BACKSPACE, False)
        time.sleep(_LAYOUT_SETTLE_SECONDS)
        for key in keys:
            code, shift = target.output_key(key.code, key.shift)
            self._tap(code, shift)

    def _tap(self, code: int, shift: bool) -> None:
        from evdev import ecodes

        if shift:
            self._ui.write(ecodes.EV_KEY, KEY_LEFTSHIFT, _PRESS)
        self._ui.write(ecodes.EV_KEY, code, _PRESS)
        self._ui.syn()
        self._ui.write(ecodes.EV_KEY, code, _RELEASE)
        if shift:
            self._ui.write(ecodes.EV_KEY, KEY_LEFTSHIFT, _RELEASE)
        self._ui.syn()
        time.sleep(_KEY_GAP_SECONDS)

    def run(self) -> None:
        try:
            from evdev import UInput, ecodes

            # Everything keyboards can send, but no mouse buttons: those would make KDE treat
            # the virtual device as a pointer. Fixed up front so hot-plugged keyboards just work.
            codes = list(range(1, 0x100)) + list(range(0x160, 0x2c0))
            leds = [ecodes.LED_NUML, ecodes.LED_CAPSL, ecodes.LED_SCROLLL]
            self._ui = UInput({ecodes.EV_KEY: codes, ecodes.EV_LED: leds}, name=VIRTUAL_NAME)
            time.sleep(_REGISTER_SECONDS)
            self._rescan()
            self._loop(ecodes)
        except Exception as error:
            print(f"layoutfixer: input stopped: {error!r}", file=sys.stderr)
            if self.on_fatal:
                self.on_fatal(str(error))
        finally:
            self._close()

    def _loop(self, ecodes) -> None:
        next_scan = time.monotonic() + _RESCAN_SECONDS
        while not self._stop_requested.is_set():
            ready, _, _ = select.select([*self._keyboards.values(), *self._pointers.values(), self._ui.fd], [], [], 0.25)
            for device in ready:
                if isinstance(device, int):
                    self._read_leds(ecodes)
                elif device.path in self._keyboards:
                    self._read_keyboard(device, ecodes)
                else:
                    self._read_pointer(device, ecodes)
            self.fixer.tick()
            if time.monotonic() >= next_scan:
                self._rescan()
                next_scan = time.monotonic() + _RESCAN_SECONDS

    def _read_keyboard(self, device, ecodes) -> None:
        try:
            events = list(device.read())
        except BlockingIOError:
            return
        except OSError:
            self._drop(device, self._keyboards)
            return
        for event in events:
            if event.type == ecodes.EV_KEY and self.router.process(event.code, event.value):
                self._ui.write(ecodes.EV_KEY, event.code, event.value)
                self._ui.syn()

    def _read_pointer(self, device, ecodes) -> None:
        try:
            events = list(device.read())
        except BlockingIOError:
            return
        except OSError:
            self._drop(device, self._pointers)
            return
        if any(event.type == ecodes.EV_KEY and event.value == _PRESS for event in events):
            self.fixer.reset()

    def _read_leds(self, ecodes) -> None:
        """The desktop sets LEDs on the virtual keyboard. Pass them to the real ones."""
        event = self._ui.read_one()
        while event is not None:
            if event.type == ecodes.EV_LED:
                if event.code == ecodes.LED_CAPSL:
                    self.router.caps_on = bool(event.value)
                for keyboard in self._keyboards.values():
                    keyboard.set_led(event.code, event.value)
            event = self._ui.read_one()

    def _rescan(self) -> None:
        from evdev import InputDevice, ecodes, list_devices

        present = list_devices()
        # Event numbers get reused, so a keyboard plugged in later may take an ignored device's path.
        self._ignored.intersection_update(present)
        for path in present:
            if path in self._keyboards or path in self._pointers or path in self._ignored:
                continue
            try:
                device = InputDevice(path)
                capabilities = device.capabilities()
                keys = set(capabilities.get(ecodes.EV_KEY, []))
                pointer = ecodes.EV_REL in capabilities or ecodes.EV_ABS in capabilities
                if is_keyboard(keys, pointer, device.name):
                    # Grabbing while a key is held would leave it stuck down: try again next scan.
                    if device.active_keys():
                        device.close()
                        continue
                    if ecodes.LED_CAPSL in device.leds():
                        self.router.caps_on = True
                    device.grab()
                    self._keyboards[path] = device
                elif needs_click_watch(keys, device.name):
                    self._pointers[path] = device
                else:
                    self._ignored.add(path)
                    device.close()
            except OSError as error:
                print(f"layoutfixer: skipping {path}: {error}", file=sys.stderr)
                self._ignored.add(path)

    def _drop(self, device, group: dict) -> None:
        group.pop(device.path, None)
        try:
            device.close()
        except OSError:
            pass

    def _close(self) -> None:
        for device in self._keyboards.values():
            try:
                device.ungrab()
                device.close()
            except OSError:
                pass
        for device in self._pointers.values():
            try:
                device.close()
            except OSError:
                pass
        self._keyboards.clear()
        self._pointers.clear()
        if self._ui is not None:
            self._ui.close()
