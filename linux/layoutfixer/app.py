"""The tray app: wires the input loop, the engine and Plasma together."""

from __future__ import annotations

import glob
import os
import shutil
import signal
import sys
from pathlib import Path

from .config import LATIN_CHOICES, config_path, load, save_latin
from .dictionaries import Dictionaries
from .engine import Fixer, Key
from .keyboard import KeyboardBridge
from .xkb import Layout

_AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "layoutfixer.desktop"
_CHOICE_LABELS = {"auto": "Same as KDE", "qwerty": "QWERTY", "qwertz": "QWERTZ"}


def missing_permissions() -> list[str]:
    """What stops the daemon from running, in terms the user can act on."""
    problems = []
    if not os.access("/dev/uinput", os.R_OK | os.W_OK):
        problems.append("/dev/uinput isn't writable")
    if not any(os.access(path, os.R_OK | os.W_OK) for path in glob.glob("/dev/input/event*")):
        problems.append("no keyboard under /dev/input is readable")
    return problems


class DesktopBackend:
    """Connects the engine to KDE and to the virtual keyboard."""

    def __init__(self, layouts, suggestions, bridge: KeyboardBridge) -> None:
        self.layouts = layouts
        self.suggestions = suggestions
        self.bridge = bridge

    def current_layout(self) -> Layout | None:
        return self.layouts.current()

    def other_layout(self, current: Layout) -> Layout | None:
        return self.layouts.other(current)

    def show_suggestion(self, text: str, target: Layout) -> None:
        self.suggestions.show_requested.emit(text, target.display_name)

    def hide_suggestion(self) -> None:
        self.suggestions.hide_requested.emit()

    def retype(self, deletes: int, keys: list[Key], target: Layout) -> None:
        self.bridge.retype(deletes, keys, target, self.layouts.switch_to)


class FocusPolicy:
    """Keeps the engine off while the screen is locked or an excluded app has focus."""

    def __init__(self, fixer: Fixer, excluded_apps) -> None:
        self.fixer = fixer
        self.excluded = {app.lower() for app in excluded_apps}
        self.locked = False
        self.excluded_focused = False

    def focus_changed(self, resource_class: str, desktop_file: str) -> None:
        self.excluded_focused = bool({resource_class.lower(), desktop_file.lower()} & self.excluded)
        self.fixer.set_suspended(self.locked or self.excluded_focused)

    def lock_changed(self, locked: bool) -> None:
        self.locked = locked
        self.fixer.set_suspended(self.locked or self.excluded_focused)


def _set_autostart(enabled: bool) -> None:
    if not enabled:
        _AUTOSTART.unlink(missing_ok=True)
        return
    launcher = shutil.which("layoutfixer") or f"{sys.executable} -m layoutfixer"
    _AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    _AUTOSTART.write_text(
        "[Desktop Entry]\nType=Application\nName=LayoutFixer\n"
        f"Exec={launcher}\nIcon=input-keyboard\nX-KDE-autostart-phase=2\n",
        encoding="utf-8",
    )


def _build_tray(app, fixer: Fixer, layouts, on_quit):
    from PyQt6.QtGui import QAction, QActionGroup, QIcon
    from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

    menu = QMenu()
    menu.addAction("LayoutFixer").setEnabled(False)
    warning = menu.addAction("")
    warning.setEnabled(False)

    def refresh_warning() -> None:
        text = layouts.layouts.warning()
        warning.setText(text or "")
        warning.setVisible(bool(text))

    layouts.changed.connect(refresh_warning)
    refresh_warning()
    menu.addSeparator()

    enabled = QAction("Enabled", menu, checkable=True, checked=True)
    enabled.toggled.connect(fixer.set_enabled)
    menu.addAction(enabled)

    keyboard = menu.addMenu("Latin keyboard")
    group = QActionGroup(keyboard)
    for choice in LATIN_CHOICES:
        action = QAction(_CHOICE_LABELS[choice], keyboard, checkable=True, checked=choice == layouts.latin_choice)

        def choose(checked: bool, value: str = choice) -> None:
            if checked and value != layouts.latin_choice:
                save_latin(value)
                layouts.set_latin_choice(value)
                fixer.reset()

        action.toggled.connect(choose)
        group.addAction(action)
        keyboard.addAction(action)

    autostart = QAction("Start at login", menu, checkable=True, checked=_AUTOSTART.exists())
    autostart.toggled.connect(_set_autostart)
    menu.addAction(autostart)
    menu.addSeparator()
    menu.addAction("Quit LayoutFixer").triggered.connect(on_quit)

    tray = QSystemTrayIcon(QIcon.fromTheme("input-keyboard"), app)
    tray.setToolTip("LayoutFixer")
    tray.setContextMenu(menu)
    tray.show()
    return tray, menu


def run() -> int:
    problems = missing_permissions()
    if problems:
        print("layoutfixer can't read your keyboard yet: " + "; ".join(problems) + ".", file=sys.stderr)
        print("Run linux/install.sh first. See linux/README.md.", file=sys.stderr)
        return 1

    from PyQt6.QtCore import QMetaObject, Qt, QTimer
    from PyQt6.QtWidgets import QApplication

    from . import kde

    app = QApplication(sys.argv)
    app.setApplicationName("LayoutFixer")
    app.setDesktopFileName("layoutfixer")
    app.setQuitOnLastWindowClosed(False)

    if not kde.claim_single_instance():
        print("layoutfixer is already running", file=sys.stderr)
        return 0

    config = load()
    print(f"layoutfixer: settings in {config_path()}", file=sys.stderr)
    layouts = kde.KeyboardLayouts(config.latin)
    message = layouts.layouts.warning()
    if message:
        print(f"layoutfixer: {message}", file=sys.stderr)

    # Called from the input thread, which has no event loop of its own.
    bridge = KeyboardBridge(
        on_fatal=lambda error: QMetaObject.invokeMethod(app, "quit", Qt.ConnectionType.QueuedConnection)
    )
    fixer = Fixer(Dictionaries(), DesktopBackend(layouts, kde.Suggestions(), bridge))
    bridge.attach(fixer)

    policy = FocusPolicy(fixer, config.excluded_apps)
    focus = kde.FocusWatcher(policy.focus_changed, policy.lock_changed)
    focus.start()

    def quit_app(*_) -> None:
        app.quit()

    signal.signal(signal.SIGINT, quit_app)
    signal.signal(signal.SIGTERM, quit_app)
    # Lets Python run the signal handlers while Qt's event loop is in charge.
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(250)

    tray, _menu = _build_tray(app, fixer, layouts, quit_app)
    bridge.start()
    try:
        return app.exec()
    finally:
        bridge.stop()
        focus.stop()
        tray.hide()
