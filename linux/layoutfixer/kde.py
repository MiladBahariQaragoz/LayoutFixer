"""Everything that talks to Plasma over D-Bus: layouts, notifications, focus and the lock screen."""

from __future__ import annotations

import html
import sys
from pathlib import Path

from PyQt6.QtCore import QMetaType, QObject, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusInterface, QDBusMessage

from .layouts import LayoutSet, read_kxkbrc
from .xkb import Layout

SERVICE = "io.github.layoutfixer"
_KWIN_SCRIPT = "layoutfixer-focus"
_FOCUS_SCRIPT_PATH = Path(__file__).resolve().parent / "kwin" / "focus.js"
_LAYOUTS = ("org.kde.keyboard", "/Layouts", "org.kde.KeyboardLayouts")
_NOTIFICATIONS = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications")
_SCREENSAVER = ("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver")
_SUGGESTION_TIMEOUT_MS = 10_000


def _call(target: tuple[str, str, str], method: str, *arguments) -> QDBusMessage:
    message = QDBusMessage.createMethodCall(*target, method)
    if arguments:
        message.setArguments(list(arguments))
    return QDBusConnection.sessionBus().call(message)


def _first_result(reply: QDBusMessage):
    values = reply.arguments()
    return values[0] if reply.type() == QDBusMessage.MessageType.ReplyMessage and values else None


class KeyboardLayouts(QObject):
    """KDE's layout list and active layout. Safe to read from the input thread."""

    changed = pyqtSignal()

    def __init__(self, latin_choice: str) -> None:
        super().__init__()
        self.latin_choice = latin_choice
        self.layouts = LayoutSet([])
        self._index = 0
        self.reload()
        bus = QDBusConnection.sessionBus()
        bus.connect(*_LAYOUTS, "layoutChanged", self._on_layout_changed)
        bus.connect(*_LAYOUTS, "layoutListChanged", self.reload)

    @pyqtSlot()
    def reload(self) -> None:
        self.layouts = LayoutSet(read_kxkbrc(), self.latin_choice)
        index = _first_result(_call(_LAYOUTS, "getLayout"))
        self._index = int(index) if index is not None else 0
        self.changed.emit()

    def set_latin_choice(self, choice: str) -> None:
        self.latin_choice = choice
        self.reload()

    @pyqtSlot("uint")
    def _on_layout_changed(self, index: int) -> None:
        self._index = int(index)

    def current(self) -> Layout | None:
        layouts = self.layouts.layouts
        return layouts[self._index] if self._index < len(layouts) else None

    def other(self, current: Layout) -> Layout | None:
        return self.layouts.other(current)

    def switch_to(self, index: int) -> bool:
        """Blocking on purpose: retyping must not start until KWin has changed layout."""
        result = _first_result(_call(_LAYOUTS, "setLayout", QDBusArgument(index, QMetaType.Type.UInt.value)))
        if result:
            self._index = index
        return bool(result)


class Suggestions(QObject):
    """Shows suggestions as a Plasma notification.

    Wayland doesn't let an app place a window at the text cursor or show one without risking focus.
    A notification never takes focus, follows the Plasma theme, and is marked transient so typed
    text doesn't stay in the notification history.
    """

    show_requested = pyqtSignal(str, str)
    hide_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._notification_id = 0
        self.show_requested.connect(self._show)
        self.hide_requested.connect(self._hide)

    @pyqtSlot(str, str)
    def _show(self, text: str, target_name: str) -> None:
        reply = _call(
            _NOTIFICATIONS,
            "Notify",
            "LayoutFixer",
            QDBusArgument(self._notification_id, QMetaType.Type.UInt.value),
            "input-keyboard",
            f"Press Tab to switch to {target_name}",
            html.escape(text),
            QDBusArgument([], QMetaType.Type.QStringList.value),
            {"transient": True, "desktop-entry": "layoutfixer"},
            _SUGGESTION_TIMEOUT_MS,
        )
        result = _first_result(reply)
        if result is not None:
            self._notification_id = int(result)

    @pyqtSlot()
    def _hide(self) -> None:
        if self._notification_id:
            _call(_NOTIFICATIONS, "CloseNotification", QDBusArgument(self._notification_id, QMetaType.Type.UInt.value))
            self._notification_id = 0


@pyqtClassInfo("D-Bus Interface", "io.github.layoutfixer.Focus")
class FocusWatcher(QObject):
    """Receives focus changes from a small KWin script, and lock screen changes from Plasma."""

    def __init__(self, on_focus_changed, on_lock_changed) -> None:
        super().__init__()
        self._on_focus_changed = on_focus_changed
        self._on_lock_changed = on_lock_changed

    @pyqtSlot(str, str)
    def changed(self, resource_class: str, desktop_file: str) -> None:
        self._on_focus_changed(resource_class, desktop_file)

    @pyqtSlot(bool)
    def lock_changed(self, locked: bool) -> None:
        self._on_lock_changed(locked)

    def start(self) -> bool:
        bus = QDBusConnection.sessionBus()
        bus.registerObject("/Focus", self, QDBusConnection.RegisterOption.ExportAllSlots)
        bus.connect(*_SCREENSAVER, "ActiveChanged", self.lock_changed)
        if _first_result(_call(_SCREENSAVER, "GetActive")):
            self._on_lock_changed(True)
        scripting = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
        scripting.call("unloadScript", _KWIN_SCRIPT)
        reply = scripting.call("loadScript", str(_FOCUS_SCRIPT_PATH), _KWIN_SCRIPT)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or int(reply.arguments()[0]) < 0:
            print(f"layoutfixer: could not load the KWin focus script: {reply.errorMessage()}", file=sys.stderr)
            return False
        scripting.call("start")
        return True

    def stop(self) -> None:
        bus = QDBusConnection.sessionBus()
        QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus).call("unloadScript", _KWIN_SCRIPT)
        bus.unregisterObject("/Focus")


def claim_single_instance() -> bool:
    return QDBusConnection.sessionBus().registerService(SERVICE)
