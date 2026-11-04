#!/bin/bash
# TouchStream Spoke installer
#
# Turns a fresh Raspberry Pi OS (Trixie, 64-bit) install into a TouchStream
# spoke. Safe to re-run: it updates the code and re-applies the configuration.
#
#   curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/spoke/install.sh | sudo bash
#   or, from a checkout:  sudo bash spoke/install.sh
#
# Environment overrides:
#   TOUCHSTREAM_USER          account that runs the spoke (default: the sudo user, else UID 1000)
#   TOUCHSTREAM_BRANCH        git branch to install (default: main)
#   TOUCHSTREAM_WIFI_COUNTRY  Wi-Fi regulatory country if none is set yet (default: US)
#   TOUCHSTREAM_NO_REBOOT=1   don't reboot at the end
#
# Copyright (c) 2025 Will Reeves and TouchStream Contributors
# Licensed under the MIT License - see LICENSE file for details

set -euo pipefail

REPO_URL="https://github.com/Cinderwire-co/TouchStream.git"
BRANCH="${TOUCHSTREAM_BRANCH:-main}"
REPO_DIR="/opt/touchstream"   # monorepo checkout; the spoke lives in its spoke/ folder
BOOT_DIR="/boot/firmware"

PACKAGES=(
    python3-kivy python3-gi python3-gst-1.0
    gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-alsa
    v4l-utils ffmpeg git cage grim wlr-randr plymouth
    python3-qrcode  # download recordings to a phone from the touchscreen
    python3-evdev   # tools/virtual-touch.py (remote testing)
)

log() { echo -e "\033[1;33m==>\033[0m $*"; }
die() { echo -e "\033[1;31mError:\033[0m $*" >&2; exit 1; }

# ---- Preflight ----
if [ "$(id -u)" -ne 0 ]; then
    [ -f "$0" ] && exec sudo -E bash "$0" "$@"
    die "Run as root, e.g. curl -fsSL <url> | sudo bash"
fi
grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null || die "This isn't a Raspberry Pi"
. /etc/os-release
[ "${VERSION_CODENAME:-}" = "trixie" ] || die "Needs Raspberry Pi OS Trixie (found ${PRETTY_NAME:-unknown})"
[ -f "$BOOT_DIR/config.txt" ] || die "$BOOT_DIR/config.txt not found"

APP_USER="${TOUCHSTREAM_USER:-${SUDO_USER:-$(id -nu 1000 2>/dev/null || true)}}"
[ -n "$APP_USER" ] && [ "$APP_USER" != "root" ] || die "No user account to run TouchStream (set TOUCHSTREAM_USER)"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
log "Installing TouchStream for user '$APP_USER'"

# ---- Packages ----
log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq "${PACKAGES[@]}"

# ---- Code ----
# Run in place when launched from a checkout; otherwise clone/update /opt/touchstream.
# INSTALL_DIR is the spoke folder, which the services run from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/touchstream-spoke.py" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
    log "Using checkout at $INSTALL_DIR"
else
    INSTALL_DIR="$REPO_DIR/spoke"
    if [ -d "$REPO_DIR/.git" ]; then
        log "Updating $REPO_DIR"
        sudo -u "$APP_USER" git -C "$REPO_DIR" pull --ff-only
    else
        log "Cloning $REPO_URL ($BRANCH) into $REPO_DIR"
        install -d -o "$APP_USER" -g "$APP_USER" "$REPO_DIR"
        sudo -u "$APP_USER" git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
    fi
fi

# ---- Boot configuration ----
log "Configuring $BOOT_DIR/config.txt"
sed -i '/^# --- TouchStream ---$/,/^# --- end TouchStream ---$/d' "$BOOT_DIR/config.txt"
printf '\n' >> "$BOOT_DIR/config.txt"
cat "$INSTALL_DIR/system/boot-config.txt" >> "$BOOT_DIR/config.txt"

log "Configuring quiet boot in $BOOT_DIR/cmdline.txt"
for arg in quiet splash loglevel=3 logo.nologo vt.global_cursor_default=0 plymouth.ignore-serial-consoles; do
    grep -qw -- "$arg" "$BOOT_DIR/cmdline.txt" || sed -i "1s/\$/ $arg/" "$BOOT_DIR/cmdline.txt"
done

# ---- Wi-Fi ----
# The radio stays blocked until a regulatory country is set
if [ -z "$(raspi-config nonint get_wifi_country 2>/dev/null)" ]; then
    log "Setting Wi-Fi country to ${TOUCHSTREAM_WIFI_COUNTRY:-US}"
    raspi-config nonint do_wifi_country "${TOUCHSTREAM_WIFI_COUNTRY:-US}"
fi
usermod -aG netdev,video,input,audio,render "$APP_USER"

# ---- Touchscreen calibration (keep an existing one) ----
if [ ! -f /etc/udev/rules.d/99-touchstream-touch.rules ]; then
    log "Installing default touch calibration"
    install -m 644 "$INSTALL_DIR/system/99-touchstream-touch.rules" /etc/udev/rules.d/
fi

# ---- Boot splash ----
log "Installing the boot splash"
rm -rf /usr/share/plymouth/themes/cinderwire /usr/share/plymouth/themes/touchstream   # (cinderwire: its old name)
cp -r "$INSTALL_DIR/system/plymouth/touchstream" /usr/share/plymouth/themes/
# Load the screen driver from the initramfs so the splash shows from early boot
for mod in spi_bcm2835 ili9486; do
    grep -qx "$mod" /etc/initramfs-tools/modules || echo "$mod" >> /etc/initramfs-tools/modules
done
plymouth-set-default-theme -R touchstream

# ---- Invisible cursor for the kiosk ----
# cage always loads the theme named "default", so the kiosk's XCURSOR_PATH points
# at a directory where "default" is this blank theme
rm -rf /usr/share/icons/touchstream-blank
CURSOR_DIR=/usr/share/touchstream/cursors/default
install -d "$CURSOR_DIR/cursors"
install -m 644 "$INSTALL_DIR/system/cursor/index.theme" "$CURSOR_DIR/"
for name in default left_ptr arrow pointer hand1 hand2 text xterm watch wait progress crosshair; do
    install -m 644 "$INSTALL_DIR/system/cursor/blank" "$CURSOR_DIR/cursors/$name"
done

# ---- Services ----
log "Installing services"
for unit in touchstream.service touchstream-edid.service touchstream-reboot.service touchstream-reboot.timer; do
    sed -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" -e "s|@USER@|$APP_USER|g" -e "s|@HOME@|$APP_HOME|g" \
        "$INSTALL_DIR/system/$unit" > "/etc/systemd/system/$unit"
done
# Drop the nightly reboot cron job from older installs; the timer replaces it
(crontab -l 2>/dev/null | grep -v "TouchStream nightly reboot" || true) | crontab -

# Kiosk replaces the desktop: no display manager, the app owns tty1
systemctl disable lightdm.service 2>/dev/null || true
rm -f "/home/$APP_USER/.config/autostart/touchstream.desktop"
systemctl daemon-reload
systemctl enable touchstream-edid.service touchstream-reboot.timer
systemctl enable --force touchstream.service
systemctl set-default graphical.target

log "TouchStream installed in $INSTALL_DIR"
if [ "${TOUCHSTREAM_NO_REBOOT:-0}" != "1" ]; then
    log "Rebooting in 5 seconds (Ctrl+C to cancel)"
    sleep 5
    systemctl reboot
fi
