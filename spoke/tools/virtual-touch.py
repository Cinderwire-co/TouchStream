#!/usr/bin/env python3
"""
Tap the TouchStream screen from software, for remote testing.

Creates a temporary virtual touchscreen with uinput and taps through it, so the
tap takes the same path as a real finger (libinput -> compositor -> app).
Run as root:

    sudo python3 virtual-touch.py X Y [X Y ...]   # screen pixels, origin top-left

Copyright (c) 2025 Will Reeves and TouchStream Contributors
Licensed under the MIT License - see LICENSE file for details
"""
import sys
import time

from evdev import AbsInfo, UInput, ecodes as e

WIDTH, HEIGHT = 480, 320


def main():
    args = [int(a) for a in sys.argv[1:]]
    if not args or len(args) % 2:
        raise SystemExit(__doc__)
    caps = {
        e.EV_KEY: [e.BTN_TOUCH],
        e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, WIDTH - 1, 0, 0, 0)),
                   (e.ABS_Y, AbsInfo(0, 0, HEIGHT - 1, 0, 0, 0))],
    }
    with UInput(caps, name='TouchStream Virtual Touch', input_props=[e.INPUT_PROP_DIRECT]) as ui:
        time.sleep(1.0)  # let udev and the compositor pick up the new device
        for x, y in zip(args[::2], args[1::2]):
            ui.write(e.EV_ABS, e.ABS_X, x)
            ui.write(e.EV_ABS, e.ABS_Y, y)
            ui.write(e.EV_KEY, e.BTN_TOUCH, 1)
            ui.syn()
            time.sleep(0.08)
            ui.write(e.EV_KEY, e.BTN_TOUCH, 0)
            ui.syn()
            time.sleep(0.4)
            print(f'tapped {x},{y}')


if __name__ == '__main__':
    main()
