#!/bin/sh
# Turn the touchscreen around, for a spoke mounted the other way up (Settings > Flip screen).
# The panel itself turns, in the boot config, so it draws in the right direction; and the touch
# calibration turns with it. Takes effect at the next boot. Run as root.
#
# Copyright (c) 2025 Will Reeves and TouchStream Contributors
# Licensed under the MIT License - see LICENSE file for details
set -eu

CONFIG=/boot/firmware/config.txt
RULE=/etc/udev/rules.d/99-touchstream-touch.rules

current=$(sed -n 's/^dtoverlay=piscreen,.*rotate=\([0-9]*\).*/\1/p' "$CONFIG")
case "$current" in
    180) rotate=0 ;;
    0) rotate=180 ;;
    *) echo "No rotate= on the touchscreen's line in $CONFIG" >&2; exit 1 ;;
esac
sed -i "s/^\(dtoverlay=piscreen,.*rotate=\)[0-9]*/\1$rotate/" "$CONFIG"

# A touch at (x, y) now lands at (1 - x, 1 - y): turn the calibration matrix the same way
if [ -f "$RULE" ]; then
    python3 - "$RULE" <<'EOF'
import re, sys
path = sys.argv[1]
rule = open(path).read()
def turn(m):
    a, b, c, d, e, f = (float(v) for v in m.group(1).split())
    return 'LIBINPUT_CALIBRATION_MATRIX}="' + ' '.join(f'{v:.6f}' for v in (-a, -b, 1 - c, -d, -e, 1 - f)) + '"'
open(path, 'w').write(re.sub(r'LIBINPUT_CALIBRATION_MATRIX}="([^"]+)"', turn, rule))
EOF
fi
echo "Touchscreen rotation $current -> $rotate (restart to apply)"
