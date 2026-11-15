#!/bin/sh
# Installs YT Music Presence for the current user and starts it on every login.
# Usage: sh linux/install.sh        Uninstall: sh linux/install.sh --uninstall
set -e
DEST="$HOME/.local/share/ytmusic-presence"
UNIT="$HOME/.config/systemd/user/ytmusic-presence.service"
cd "$(dirname "$0")/.."

if [ "$1" = "--uninstall" ]; then
    systemctl --user disable --now ytmusic-presence.service 2>/dev/null || true
    rm -rf "$DEST" "$UNIT"
    systemctl --user daemon-reload
    echo "Uninstalled. (Your settings in ~/.config/ytmusic-presence were kept.)"
    exit 0
fi

mkdir -p "$DEST" "$(dirname "$UNIT")"
cp main.py media_linux.py requirements.txt "$DEST/"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet --upgrade -r "$DEST/requirements.txt"
cp linux/ytmusic-presence.service "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now ytmusic-presence.service
systemctl --user restart ytmusic-presence.service
echo "Installed and running. See what it's doing with:"
echo "  journalctl --user -u ytmusic-presence -f"
