# LayoutFixer for Linux (KDE Plasma on Wayland)

The Linux version of LayoutFixer: same idea and same rules, living in the Plasma system tray. Type a
word on the wrong layout, press <kbd>Tab</kbd> when the notification appears, and the text is retyped
and your layout switched.

Developed on Plasma 6.7 with `de` and `ir(winkeys)` layouts. Other desktops are not supported yet.

## Install

From a clone of this repository:

```bash
linux/install.sh
layoutfixer
```

The script installs to `~/.local`, installs `python-evdev` and `python-pyqt6` on Arch (other distros
are told what to install), and adds one udev rule for keyboard access. It asks for your password for
the system parts. You need a Latin layout and a Persian layout in **System Settings → Keyboard**.

`linux/install.sh --uninstall` removes everything except your settings.

## Settings

Right-click the tray icon for **Enabled**, **Latin keyboard** and **Start at login**. Settings live in
`~/.config/layoutfixer/config.ini`:

```ini
[keyboard]
# auto: read keys the way KDE's Latin layout does.
# qwerty or qwertz: read keys that way, whatever KDE's layout is.
latin = auto

[apps]
# Replaces the built-in list. Use the app IDs that KDE reports.
excluded = org.kde.konsole, kitty, org.keepassxc.keepassxc
```

**QWERTY or QWERTZ** only matters for the keys that differ, like <kbd>Y</kbd> and <kbd>Z</kbd>: it
decides which word those keys mean when you were typing Latin text on the Persian layout. If KDE has
a layout that matches your choice, LayoutFixer switches to it. If not, the fixed text still comes out
right, but what you type next follows KDE's layout, and the tray menu tells you to add a matching one.

On a German layout, both German and English words count as real words and are left alone.

## Things to know

- **Keyboard access.** On Wayland, apps can't normally see keystrokes. The udev rule lifts that for
  the logged-in session, so any program you run could read your keyboard too. That's a real
  security trade-off: read `70-layoutfixer.rules` and decide if it's acceptable.
- **Passwords.** Unlike the Mac and Windows versions, LayoutFixer can't tell a password field from any
  other field here, so a password that is also a dictionary word could appear in a suggestion. It
  turns itself off on the lock screen and in terminals, password managers and authentication dialogs
  (the excluded list above). Password fields in browsers are not covered.
- **Terminals are excluded by default**, because of `sudo` prompts. To test, use another app like Kate.
- **The suggestion is a notification**, not a popup at the cursor. Wayland doesn't let apps find the
  text cursor or place windows freely. **Do Not Disturb** hides it, though Tab still works.
- **Keyboards are grabbed** so Tab can be swallowed, and every other key is forwarded through a virtual
  keyboard. If LayoutFixer crashes or is killed, the kernel gives your keyboards straight back.
- **Combined devices.** A keyboard that shares one input node with a touchpad is left alone and can't
  be used with LayoutFixer.

Like the other versions, it keeps only the last few words in memory, clears them on clicks, shortcuts
and focus changes, and writes nothing to disk or the network.

## How it differs from the other versions

| | Mac | Windows | Linux |
|---|---|---|---|
| Watches typing | event tap | low-level keyboard hook | evdev |
| Suggestion | popup at the cursor | popup at the cursor | Plasma notification |
| Switches layout | input source API | window message | KWin over D-Bus |
| Retypes by | Unicode events | Unicode events | replaying the physical keys |
| Permission | Accessibility | none | udev rule |

## Code

```
layoutfixer/engine.py         tracks typing, decides when to suggest, retypes on Tab
layoutfixer/dictionaries.py   the "real word" rules
layoutfixer/xkb.py            key-to-character tables from libxkbcommon
layoutfixer/layouts.py        KDE's layout list and the QWERTY/QWERTZ setting
layoutfixer/config.py         config.ini
layoutfixer/keyboard.py       evdev grab, virtual keyboard, key routing
layoutfixer/kde.py            D-Bus: layouts, notifications, focus, lock screen
layoutfixer/kwin/focus.js     small KWin script that reports the focused app
layoutfixer/app.py            tray icon, wires it all together
data/de_50k.txt               German word list; the others come from ../Resources and ../windows/data
```

## Develop

```bash
cd linux
python3 -m unittest discover tests
python3 -m layoutfixer --test "صشدف لقشذ"             # check detection, no keyboard access needed
python3 -m layoutfixer --test "غثس" --latin qwerty    # try a QWERTY/QWERTZ setting
python3 -m layoutfixer --doctor                       # check your setup
```
