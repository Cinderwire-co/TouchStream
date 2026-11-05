#!/bin/sh
# Start the kiosk: the spoke app full screen under the cage Wayland compositor (touchstream.service).
#
# When the touchscreen is its own display device (an SPI panel), cage drives only that, rendering on the
# GPU's render node, and leaves the Pi's HDMI ports free: the spoke sends a clean feed of its input to a
# TV or monitor plugged into one (media.py). With an HDMI touchscreen, cage drives HDMI as usual.
#
# Copyright (c) 2025 Will Reeves and TouchStream Contributors
# Licensed under the MIT License - see LICENSE file for details
panel=""
for card in /dev/dri/by-path/*-card; do
    [ -e "$card" ] || continue
    driver=$(basename "$(readlink -f "/sys/class/drm/$(basename "$(readlink -f "$card")")/device/driver")")
    case "$driver" in
        vc4*|v3d) ;;               # the Pi's HDMI and its GPU
        *) panel="$card" ;;        # e.g. ili9486, the 3.5" SPI touchscreen
    esac
done
render=$(ls /dev/dri/by-path/*v3d-render 2>/dev/null | head -n 1)
if [ -n "$panel" ] && [ -n "$render" ]; then
    export WLR_DRM_DEVICES="$panel" WLR_RENDER_DRM_DEVICE="$render"
fi
exec /usr/bin/cage -d -s -- /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/../touchstream-spoke.py"
