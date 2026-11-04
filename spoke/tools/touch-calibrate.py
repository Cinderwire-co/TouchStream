#!/usr/bin/env python3
"""
TouchStream touchscreen calibration (MHS35 / ADS7846 resistive panel)

Shows five targets, reads raw ADS7846 coordinates straight from evdev, and fits
the libinput calibration matrix that maps them onto the screen.

    python3 touch-calibrate.py            # print the matrix
    python3 touch-calibrate.py --install  # also write the udev rule (sudo)

Copyright (c) 2025 Will Reeves and TouchStream Contributors
Licensed under the MIT License - see LICENSE file for details
"""
import fcntl
import glob
import os
import queue
import statistics
import struct
import subprocess
import sys
import threading

os.environ.setdefault('KIVY_NO_ARGS', '1')
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line
from kivy.uix.label import Label
from kivy.uix.widget import Widget

DEVICE_NAME = 'ADS7846 Touchscreen'
UDEV_RULE = '/etc/udev/rules.d/99-touchstream-touch.rules'

# Target positions as screen fractions, y measured from the top (libinput convention)
TARGETS = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9), (0.5, 0.5)]

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, BTN_TOUCH = 0, 1, 330
EVENT = struct.Struct('llHHi')

INK = (14 / 255, 14 / 255, 14 / 255, 1)
ASH = (242 / 255, 242 / 255, 243 / 255, 1)
EMBER = (254 / 255, 92 / 255, 9 / 255, 1)


def find_device():
    for path in glob.glob('/sys/class/input/event*'):
        with open(path + '/device/name') as f:
            if f.read().strip() == DEVICE_NAME:
                return '/dev/input/' + os.path.basename(path)
    raise SystemExit(f'{DEVICE_NAME} not found')


def abs_range(fd, axis):
    """Return (min, max) of an absolute axis via EVIOCGABS."""
    buf = bytearray(24)
    fcntl.ioctl(fd, 0x80000000 | (24 << 16) | (ord('E') << 8) | (0x40 + axis), buf)
    _, lo, hi, _, _, _ = struct.unpack('6i', buf)
    return lo, hi


def read_taps(dev, out):
    """Push the median raw (x, y) of each completed press onto `out`."""
    with open(dev, 'rb') as fd:
        x = y = None
        samples, down = [], False
        while True:
            _, _, typ, code, val = EVENT.unpack(fd.read(EVENT.size))
            if typ == EV_ABS and code == ABS_X:
                x = val
            elif typ == EV_ABS and code == ABS_Y:
                y = val
            elif typ == EV_KEY and code == BTN_TOUCH:
                down = bool(val)
                if not down and len(samples) >= 3:
                    # Drop the first samples: resistive panels settle after contact
                    settled = samples[len(samples) // 3:]
                    out.put((statistics.median(s[0] for s in settled),
                             statistics.median(s[1] for s in settled)))
                samples = []
            elif typ == EV_SYN and down and x is not None:
                samples.append((x, y))


def solve3(m, v):
    """Solve a 3x3 linear system by Gaussian elimination."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(3):
            if r != col:
                f = a[r][col] / a[col][col]
                a[r] = [a[r][k] - f * a[col][k] for k in range(4)]
    return [a[i][3] / a[i][i] for i in range(3)]


def fit_matrix(raw_norm, screen):
    """Least-squares affine fit: screen = [a b c; d e f] . [rx ry 1]."""
    ata = [[0.0] * 3 for _ in range(3)]
    atx, aty = [0.0] * 3, [0.0] * 3
    for (rx, ry), (sx, sy) in zip(raw_norm, screen):
        row = (rx, ry, 1.0)
        for i in range(3):
            for j in range(3):
                ata[i][j] += row[i] * row[j]
            atx[i] += row[i] * sx
            aty[i] += row[i] * sy
    return solve3(ata, atx) + solve3(ata, aty)


def install_rule(matrix):
    values = ' '.join(f'{v:.6f}' for v in matrix)
    rule = (f'# Written by touch-calibrate.py\n'
            f'ACTION=="add|change", KERNEL=="event*", ATTRS{{name}}=="{DEVICE_NAME}", '
            f'ENV{{LIBINPUT_CALIBRATION_MATRIX}}="{values}"\n')
    subprocess.run(['sudo', 'tee', UDEV_RULE], input=rule, text=True,
                   stdout=subprocess.DEVNULL, check=True)
    subprocess.run(['sudo', 'udevadm', 'control', '--reload'], check=True)
    print(f'Wrote {UDEV_RULE} (takes effect when the display server restarts)')


class CalibrationScreen(Widget):
    def __init__(self, ranges, taps, **kwargs):
        super().__init__(**kwargs)
        self.ranges, self.taps = ranges, taps
        self.raw = []
        self.label = Label(color=ASH, font_size='16sp', halign='center')
        self.add_widget(self.label)
        self.bind(size=lambda *_: self.redraw())
        Clock.schedule_interval(self.poll, 0.05)

    def redraw(self):
        self.canvas.before.clear()
        self.label.size = self.size
        self.label.text_size = self.size
        self.label.valign = 'middle'
        if len(self.raw) >= len(TARGETS):
            return
        tx, ty = TARGETS[len(self.raw)]
        cx, cy = tx * self.width, (1 - ty) * self.height
        with self.canvas.before:
            Color(*EMBER)
            Line(points=[cx - 14, cy, cx + 14, cy], width=1.5)
            Line(points=[cx, cy - 14, cx, cy + 14], width=1.5)
            Line(circle=(cx, cy, 8), width=1.5)
        self.label.text = f'Tap the center of the target\n{len(self.raw) + 1} of {len(TARGETS)}'

    def poll(self, dt):
        try:
            self.raw.append(self.taps.get_nowait())
        except queue.Empty:
            return
        if len(self.raw) < len(TARGETS):
            self.redraw()
            return
        (x0, x1), (y0, y1) = self.ranges
        norm = [((x - x0) / (x1 - x0), (y - y0) / (y1 - y0)) for x, y in self.raw]
        matrix = fit_matrix(norm, TARGETS)
        print('raw taps:', [(round(x), round(y)) for x, y in self.raw])
        print('LIBINPUT_CALIBRATION_MATRIX="' + ' '.join(f'{v:.6f}' for v in matrix) + '"')
        if '--install' in sys.argv:
            install_rule(matrix)
        self.canvas.before.clear()
        self.label.text = 'Calibration saved'
        Clock.schedule_once(lambda *_: App.get_running_app().stop(), 2)
        return False


class CalibrateApp(App):
    def build(self):
        Window.clearcolor = INK
        Window.fullscreen = 'auto'
        Window.show_cursor = False
        dev = find_device()
        with open(dev, 'rb') as fd:
            ranges = (abs_range(fd, ABS_X), abs_range(fd, ABS_Y))
        taps = queue.Queue()
        threading.Thread(target=read_taps, args=(dev, taps), daemon=True).start()
        return CalibrationScreen(ranges, taps)


if __name__ == '__main__':
    CalibrateApp().run()
