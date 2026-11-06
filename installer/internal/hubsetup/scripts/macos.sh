#!/bin/bash
# TouchStream Hub for macOS
#
#   curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/macos.sh | bash
#
# Installs the hub (with uv, which brings its own Python), MediaMTX (its official
# release, checksum-verified) and FFmpeg (from Homebrew, unless it's already here);
# starts the hub now and at every login; adds "TouchStream Hub" to Applications;
# and opens the dashboard. Run it again to update: settings and recordings stay.
#
#   ... | bash -s -- --uninstall    remove the hub (keeps your settings and recordings)
#
#   TOUCHSTREAM_SOURCE   install the hub from a folder or URL instead of GitHub
#   TOUCHSTREAM_PORT     dashboard port (default: 8080, or the next free one)
#   TOUCHSTREAM_NO_OPEN  don't open the dashboard at the end (the installer app sets it)
#
# Copyright (c) 2025 Will Reeves and TouchStream Contributors
# Licensed under the MIT License - see LICENSE file for details

set -euo pipefail

BRANCH="${TOUCHSTREAM_BRANCH:-main}"
SOURCE="${TOUCHSTREAM_SOURCE:-https://github.com/Cinderwire-co/TouchStream/archive/refs/heads/$BRANCH.zip#subdirectory=hub}"
MEDIAMTX_VERSION=1.21.3
LABEL="co.cinderwire.touchstream-hub"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="$HOME/Applications/TouchStream Hub.app"
DATA="$HOME/Library/Application Support/TouchStream Hub"
LOG="$HOME/Library/Logs/TouchStream Hub.log"

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
listening() { lsof -nP -i"$1" -sTCP:LISTEN >/dev/null 2>&1 || { [ "${1#UDP}" != "$1" ] && lsof -nP -i"$1" >/dev/null 2>&1; }; }

[ "$(uname -s)" = Darwin ] || die "This installer is for macOS."

UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"

# ---- Uninstall ----
if [ "${1:-}" = "--uninstall" ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -rf "$AGENT" "$APP"
    [ -n "$UV" ] && "$UV" tool uninstall touchstream-hub >/dev/null 2>&1 || true
    say "TouchStream Hub removed. Your settings are still in $DATA and your recordings where you chose."
    exit 0
fi

# ---- Dashboard port: the one from before, else 8080 or the next free one ----
PORT="${TOUCHSTREAM_PORT:-}"
if [ -z "$PORT" ] && [ -f "$AGENT" ]; then
    PORT="$(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:TOUCHSTREAM_PORT' "$AGENT" 2>/dev/null || true)"
fi
if [ -z "$PORT" ]; then
    for p in $(seq 8080 8099); do listening "TCP:$p" || { PORT=$p; break; }; done
    [ -n "$PORT" ] || die "No free port between 8080 and 8099; set TOUCHSTREAM_PORT."
fi

# ---- uv (installs the hub and its own Python, nothing system-wide) ----
if [ -z "$UV" ]; then
    say "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
    UV="$HOME/.local/bin/uv"
fi

# ---- FFmpeg: use the one that's here, else Homebrew's ----
FFMPEG="$(command -v ffmpeg || true)"
for f in /opt/homebrew/bin/ffmpeg /usr/local/bin/ffmpeg; do [ -z "$FFMPEG" ] && [ -x "$f" ] && FFMPEG="$f"; done
if [ -z "$FFMPEG" ]; then
    BREW="$(command -v brew || true)"
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -z "$BREW" ] && [ -x "$b" ] && BREW="$b"; done
    if [ -z "$BREW" ]; then
        say "Installing Homebrew, for FFmpeg (it will ask for your Mac password)"
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b"; done
    fi
    say "Installing FFmpeg"
    "$BREW" install --quiet ffmpeg
    FFMPEG="$("$BREW" --prefix)/bin/ffmpeg"
fi

# ---- MediaMTX: the official release, where the hub looks for it ----
MTX="$DATA/bin/mediamtx"
if ! "$MTX" --version 2>/dev/null | grep -qx "v$MEDIAMTX_VERSION"; then
    say "Installing MediaMTX $MEDIAMTX_VERSION"
    case "$(uname -m)" in arm64) arch=arm64 ;; x86_64) arch=amd64 ;; *) die "Unsupported Mac: $(uname -m)" ;; esac
    base="https://github.com/bluenviron/mediamtx/releases/download/v$MEDIAMTX_VERSION"
    file="mediamtx_v${MEDIAMTX_VERSION}_darwin_$arch.tar.gz"
    tmp="$(mktemp -d)"
    (cd "$tmp" && curl -fsSLO "$base/$file" \
        && curl -fsSL "$base/checksums.sha256" | grep " \*$file\$" | shasum -a 256 -c - >/dev/null \
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

# ---- Start now and at every login, restart if it stops ----
say "Starting the hub on port $PORT"
mkdir -p "$(dirname "$AGENT")" "$(dirname "$LOG")"
cat > "$AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key><array><string>$HUB_BIN</string></array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$(dirname "$FFMPEG"):/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>TOUCHSTREAM_PORT</key><string>$PORT</string>
        <key>TOUCHSTREAM_FFMPEG</key><string>$FFMPEG</string>
        <key>TOUCHSTREAM_MEDIAMTX</key><string>$MTX</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for _ in 1 2 3 4 5; do listening "TCP:$PORT" || break; sleep 1; done   # the old hub letting go
launchctl bootstrap "gui/$(id -u)" "$AGENT"

# ---- "TouchStream Hub" in Applications: opens the dashboard ----
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/MacOS/TouchStream Hub" <<EOF
#!/bin/bash
launchctl kickstart "gui/\$(id -u)/$LABEL" 2>/dev/null
open "http://localhost:$PORT"
EOF
chmod +x "$APP/Contents/MacOS/TouchStream Hub"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>TouchStream Hub</string>
    <key>CFBundleIdentifier</key><string>$LABEL.launcher</string>
    <key>CFBundleExecutable</key><string>TouchStream Hub</string>
    <key>CFBundleIconFile</key><string>icon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
</dict>
</plist>
EOF
ICON="$PACKAGE/static/img/touchstream-icon.png"    # 1024 px, from brand/touchstream_mark.py
if [ -f "$ICON" ]; then
    ICONSET="$(mktemp -d)/icon.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 128 256 512; do
        sips -z $size $size "$ICON" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
        sips -z $((size * 2)) $((size * 2)) "$ICON" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns" 2>/dev/null || true
    rm -rf "$(dirname "$ICONSET")"
fi

# ---- Done ----
say "Waiting for the hub"
for _ in $(seq 1 60); do curl -s -m 2 "http://localhost:$PORT/api/state" >/dev/null && break; sleep 1; done
curl -s -m 2 "http://localhost:$PORT/api/state" >/dev/null || die "The hub didn't start; see $LOG"
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "this Mac's IP")"
cat <<EOF

  TouchStream Hub is running.

    Dashboard:           http://localhost:$PORT   (or "TouchStream Hub" in Applications)
    Address for spokes:  $IP:$PORT

  If macOS asks whether to accept incoming connections, choose Allow: spokes
  send video to this Mac.

EOF
for port in UDP:8890 TCP:1935; do
    listening "$port" && ! lsof -nP -i"$port" | grep -q mediamtx \
        && echo "  Note: something else on this Mac is using $port, which the hub needs for video."
done
[ -n "${TOUCHSTREAM_NO_OPEN:-}" ] || open "http://localhost:$PORT"   # (the installer app carries on itself)
