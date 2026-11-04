#!/bin/bash
# TouchStream Hub for Linux (Ubuntu, Debian, Fedora, Arch and others with systemd)
#
#   curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/linux.sh | bash
#
# Installs the hub (with uv, which brings its own Python), MediaMTX (its official
# release, checksum-verified) and FFmpeg (from your package manager, unless it's already
# here); runs the hub as a service that starts with the computer, even before anyone
# signs in; opens the firewall for spokes if ufw or firewalld is on; and adds
# "TouchStream Hub" to the desktop's app menu. Run it again to update: settings and
# recordings stay. Prefer Docker for servers you manage with Coolify (see the README).
#
#   ... | bash -s -- --uninstall    remove the hub (keeps your settings and recordings)
#
#   TOUCHSTREAM_SOURCE   install the hub from a folder or URL instead of GitHub
#   TOUCHSTREAM_PORT     dashboard port (default: 8080, or the next free one)
#
# Copyright (c) 2025 Will Reeves and TouchStream Contributors
# Licensed under the MIT License - see LICENSE file for details

set -euo pipefail

BRANCH="${TOUCHSTREAM_BRANCH:-main}"
SOURCE="${TOUCHSTREAM_SOURCE:-https://github.com/Cinderwire-co/TouchStream/archive/refs/heads/$BRANCH.zip#subdirectory=hub}"
MEDIAMTX_VERSION=1.21.3
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/touchstream-hub"      # the hub's own data folder (settings.py)
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/touchstream-hub.service"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/touchstream-hub.desktop"

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
listening() { ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; }
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

[ "$(uname -s)" = Linux ] || die "This installer is for Linux."
command -v curl >/dev/null || die "Install curl first."
SYSTEMD=1; systemctl --user show-environment >/dev/null 2>&1 || SYSTEMD=0

UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"

# ---- Uninstall ----
if [ "${1:-}" = "--uninstall" ]; then
    [ "$SYSTEMD" = 1 ] && systemctl --user disable --now touchstream-hub 2>/dev/null || true
    rm -f "$UNIT" "$DESKTOP"
    [ -n "$UV" ] && "$UV" tool uninstall touchstream-hub >/dev/null 2>&1 || true
    say "TouchStream Hub removed. Your settings are still in $DATA and your recordings where you chose."
    exit 0
fi

# ---- Dashboard port: the one from before, else 8080 or the next free one ----
PORT="${TOUCHSTREAM_PORT:-}"
[ -z "$PORT" ] && [ -f "$UNIT" ] && PORT="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' "$UNIT")"
[ "$SYSTEMD" = 1 ] && systemctl --user stop touchstream-hub 2>/dev/null || true
if [ -z "$PORT" ]; then
    for p in $(seq 8080 8099); do listening "$p" || { PORT=$p; break; }; done
    [ -n "$PORT" ] || die "No free port between 8080 and 8099; set TOUCHSTREAM_PORT."
fi

# ---- uv (installs the hub and its own Python, nothing system-wide) ----
if [ -z "$UV" ]; then
    say "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
    UV="$HOME/.local/bin/uv"
fi

# ---- FFmpeg: use the one that's here, else the package manager's ----
if ! command -v ffmpeg >/dev/null; then
    say "Installing FFmpeg (it may ask for your password)"
    if command -v apt-get >/dev/null; then $SUDO apt-get update -qq && $SUDO apt-get install -y -qq ffmpeg >/dev/null
    elif command -v dnf >/dev/null; then $SUDO dnf install -y -q ffmpeg || $SUDO dnf install -y -q ffmpeg-free
    elif command -v pacman >/dev/null; then $SUDO pacman -S --noconfirm --needed ffmpeg
    elif command -v zypper >/dev/null; then $SUDO zypper -q install -y ffmpeg
    else die "Install FFmpeg with your package manager, then run this again."
    fi
fi

# ---- MediaMTX: the official release, where the hub looks for it ----
MTX="$DATA/bin/mediamtx"
if ! "$MTX" --version 2>/dev/null | grep -qx "v$MEDIAMTX_VERSION"; then
    say "Installing MediaMTX $MEDIAMTX_VERSION"
    case "$(uname -m)" in
        x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; armv7l) arch=armv7 ;;
        *) die "Unsupported processor: $(uname -m)" ;;
    esac
    base="https://github.com/bluenviron/mediamtx/releases/download/v$MEDIAMTX_VERSION"
    file="mediamtx_v${MEDIAMTX_VERSION}_linux_$arch.tar.gz"
    tmp="$(mktemp -d)"
    (cd "$tmp" && curl -fsSLO "$base/$file" \
        && curl -fsSL "$base/checksums.sha256" | grep " \*$file\$" | sed 's/ \*/  /' | sha256sum -c - >/dev/null \
        && tar -xzf "$file" mediamtx) || die "Couldn't download MediaMTX"
    mkdir -p "$DATA/bin"
    mv "$tmp/mediamtx" "$MTX"
    rm -rf "$tmp"
fi

# ---- The hub ----
say "Installing TouchStream Hub"
[ -d "$SOURCE" ] && SOURCE="file://$(cd "$SOURCE" && pwd)"
"$UV" tool install --quiet --force --python 3.12 "touchstream-hub[transcribe] @ $SOURCE"
HUB_BIN="$("$UV" tool dir --bin)/touchstream-hub"
PACKAGE="$("$UV" tool dir)/touchstream-hub/lib/python3.12/site-packages/touchstream_hub"

# ---- Firewall: let spokes in, if one is switched on ----
if command -v ufw >/dev/null && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
    say "Letting spokes through the firewall (ufw)"
    for rule in "$PORT/tcp" 8890/udp 1935/tcp 9999/udp; do $SUDO ufw allow "$rule" comment "TouchStream Hub" >/dev/null; done
elif command -v firewall-cmd >/dev/null && $SUDO firewall-cmd --state >/dev/null 2>&1; then
    say "Letting spokes through the firewall (firewalld)"
    for rule in "$PORT/tcp" 8890/udp 1935/tcp 9999/udp; do $SUDO firewall-cmd -q --permanent --add-port="$rule"; done
    $SUDO firewall-cmd -q --reload
fi

# ---- "TouchStream Hub" in the app menu, on desktops ----
mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=TouchStream Hub
Comment=Schedule, stream and record your TouchStream spokes
Exec=xdg-open http://localhost:$PORT/
Icon=$PACKAGE/static/img/touchstream-icon.png
Categories=AudioVideo;Video;
EOF

# ---- Start now and with the computer, restart if it stops ----
if [ "$SYSTEMD" = 0 ]; then
    say "No systemd session here (a container or WSL?): start the hub yourself with"
    echo "    $HUB_BIN --port $PORT"
    exit 0
fi
say "Starting the hub on port $PORT"
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=TouchStream Hub
After=network-online.target

[Service]
ExecStart=$HUB_BIN --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now touchstream-hub >/dev/null 2>&1
# Run from boot, before anyone signs in (a hub in a closet has nobody to sign in)
loginctl enable-linger "$USER" 2>/dev/null || $SUDO loginctl enable-linger "$USER" 2>/dev/null || true

# ---- Done ----
say "Waiting for the hub"
for _ in $(seq 1 60); do curl -s -m 2 "http://localhost:$PORT/api/state" >/dev/null && break; sleep 1; done
curl -s -m 2 "http://localhost:$PORT/api/state" >/dev/null || die "The hub didn't start; see: journalctl --user -u touchstream-hub"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP" ] || IP="this computer's IP"
cat <<EOF

  TouchStream Hub is running.

    Dashboard:           http://localhost:$PORT   (or "TouchStream Hub" in your apps)
    Address for spokes:  $IP:$PORT
    Logs:                journalctl --user -u touchstream-hub

EOF
command -v xdg-open >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && xdg-open "http://localhost:$PORT/" >/dev/null 2>&1 || true
