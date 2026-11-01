#!/usr/bin/env bash
# Installs LayoutFixer for the current user, plus one system udev rule for keyboard access.
# Usage: linux/install.sh [--uninstall]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
share="${XDG_DATA_HOME:-$HOME/.local/share}/layoutfixer"
launcher="$HOME/.local/bin/layoutfixer"
rule="/etc/udev/rules.d/70-layoutfixer.rules"
autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/layoutfixer.desktop"

reload_udev() {
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=input --subsystem-match=misc
}

if [[ "${1:-}" == "--uninstall" ]]; then
    pkill -f "python3 -m layoutfixer" 2>/dev/null || true
    rm -rf -- "$share"
    rm -f -- "$launcher" "$autostart"
    if [[ -e "$rule" ]]; then
        sudo rm -f -- "$rule"
        reload_udev
    fi
    echo "LayoutFixer removed. Your settings stay in ~/.config/layoutfixer."
    exit 0
fi

missing=()
python3 -c "import evdev" 2>/dev/null || missing+=(python-evdev)
python3 -c "import PyQt6.QtDBus" 2>/dev/null || missing+=(python-pyqt6)
if ((${#missing[@]})); then
    if command -v pacman >/dev/null; then
        sudo pacman -S --needed "${missing[@]}"
    else
        echo "Install these with your package manager first: ${missing[*]}" >&2
        exit 1
    fi
fi

rm -rf -- "$share"
mkdir -p "$share/data" "$(dirname "$launcher")"
cp -r "$here/layoutfixer" "$share/"
find "$share" -type d -name __pycache__ -prune -exec rm -rf {} +
cp "$repo/Resources/en_50k.txt" "$repo/Resources/fa_50k.txt" "$here/data/de_50k.txt" "$repo/windows/data/words.txt" "$share/data/"

cat >"$launcher" <<EOF
#!/usr/bin/env bash
cd "$share" && exec python3 -m layoutfixer "\$@"
EOF
chmod +x "$launcher"

echo "Installing the udev rule for keyboard access (asks for your password):"
sudo install -m 0644 "$here/70-layoutfixer.rules" "$rule"
reload_udev

echo
"$launcher" --doctor || true
echo
echo "Installed. Start it with: layoutfixer"
