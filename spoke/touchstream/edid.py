"""EDIDs for the TC358743: tell HDMI sources exactly which 1080p frame rate to send

The capture card shows sources an EDID (a monitor's "what I can display" list).
Each one here offers a single 1080p mode at the chosen rate, so cameras and
computers send that and nothing else, plus HDMI audio (2-channel PCM). A Pi 4's
2-lane capture link carries up to 1080p50; 1080p60 is too fast for it.

    python3 -m touchstream.edid --apply [--config PATH]   # load the configured rate (boot service)
"""
import json
import os
import subprocess
import sys

RATES = ('24', '25', '30', '50')
DEFAULT_RATE = '30'

# CEA-861 1080p timings: (pixel clock kHz, horizontal blanking, front porch, CEA video code)
TIMINGS = {
    '24': (74250, 830, 638, 32),
    '25': (74250, 720, 528, 33),
    '30': (74250, 280, 88, 34),
    '50': (148500, 720, 528, 31),
}


def _descriptor(tag, text):
    body = text.encode('ascii')[:13]
    body += b'\n' + b' ' * (12 - len(body)) if len(body) < 13 else b''
    return bytes([0, 0, 0, tag, 0]) + body


def _dtd(rate):
    clock_khz, h_blank, h_front, _ = TIMINGS[rate]
    h_active, h_sync = 1920, 44
    v_active, v_blank, v_front, v_sync = 1080, 45, 4, 5
    h_mm, v_mm = 800, 450
    clock = clock_khz // 10                       # stored in 10 kHz units
    return bytes([
        clock & 0xFF, clock >> 8,
        h_active & 0xFF, h_blank & 0xFF, (h_active >> 8) << 4 | (h_blank >> 8),
        v_active & 0xFF, v_blank & 0xFF, (v_active >> 8) << 4 | (v_blank >> 8),
        h_front & 0xFF, h_sync & 0xFF, (v_front & 0xF) << 4 | (v_sync & 0xF),
        (h_front >> 8 & 3) << 6 | (h_sync >> 8 & 3) << 4 | (v_front >> 4 & 3) << 2 | (v_sync >> 4 & 3),
        h_mm & 0xFF, v_mm & 0xFF, (h_mm >> 8) << 4 | (v_mm >> 8),
        0, 0,
        0x1E,                                     # progressive, digital separate sync, +H +V
    ])


def _checksum(block):
    return bytes(block) + bytes([(256 - sum(block) % 256) % 256])


def build(rate):
    """256-byte EDID (base block + CEA-861 extension) offering only 1080p at `rate`."""
    clock_khz = TIMINGS[rate][0]
    base = bytearray()
    base += bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
    base += bytes([0x0E, 0xF2])                   # manufacturer "CWR" (Cinderwire)
    base += int(rate).to_bytes(2, 'little')       # product code: the frame rate
    base += bytes(4)                              # serial
    base += bytes([1, 2026 - 1990])               # week, year
    base += bytes([1, 3])                         # EDID 1.3
    base += bytes([0x80, 80, 45, 0x78, 0x0A])     # digital input, 80x45 cm, gamma 2.2, preferred timing first
    base += bytes([0x0D, 0xC9, 0xA0, 0x57, 0x47, 0x98, 0x27, 0x12, 0x48, 0x4C])   # sRGB-ish chromaticity
    base += bytes([0x20, 0x00, 0x00])             # established timings: 640x480@60 only (HDMI requires it)
    base += bytes([0x01, 0x01] * 8)               # no standard timings
    base += _dtd(rate)
    base += _descriptor(0xFC, 'TouchStream')
    # Range covers the mandatory 640x480@60 fallback; not "continuous", so sources
    # still only pick the modes listed here
    base += bytes([0, 0, 0, 0xFD, 0, 23, 61, 15, 70, -(-clock_khz // 10000)]) + b'\x00\n' + b' ' * 6
    base += _descriptor(0xFF, f'TS 1080P{rate}')
    base += bytes([1])                            # one extension block
    base = _checksum(base)

    vic = TIMINGS[rate][3]
    blocks = bytes([0x40 | 2, vic, 1])            # video: 1080p@rate (the DTD is the native one), 640x480
    blocks += bytes([0x20 | 3, 0x09, 0x07, 0x07])   # audio: LPCM 2ch, 32/44.1/48 kHz, 16/20/24-bit
    blocks += bytes([0x80 | 3, 0x01, 0x00, 0x00])   # speakers: front left/right
    blocks += bytes([0x60 | 6, 0x03, 0x0C, 0x00, 0x10, 0x00, 0x00])   # HDMI vendor block (enables audio)
    # Video capability: RGB and YCbCr range selectable, everything underscanned (no edge cropping)
    blocks += bytes([0xE0 | 2, 0x00, 0xCF])
    ext = bytearray([0x02, 0x03, 4 + len(blocks), 0xF1])   # underscan, basic audio, YCbCr 4:4:4 + 4:2:2, 1 native DTD
    ext += blocks
    ext += bytes(127 - len(ext))
    return base + _checksum(ext)


def to_hex(edid):
    return '\n'.join(' '.join(f'{b:02X}' for b in edid[i:i + 16]) for i in range(0, len(edid), 16)) + '\n'


def subdev():
    for name in sorted(os.listdir('/sys/class/video4linux')):
        try:
            with open(f'/sys/class/video4linux/{name}/name') as f:
                if 'tc358743' in f.read():
                    return f'/dev/{name}'
        except OSError:
            pass
    return None


def apply(rate, cache_dir=None):
    """Load the EDID for `rate` into the capture card. Sources re-negotiate within a few seconds."""
    rate = rate if rate in RATES else DEFAULT_RATE
    device = subdev()
    if not device:
        return False
    cache_dir = cache_dir or os.path.join(os.path.expanduser('~'), '.cache', 'touchstream')
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f'edid-1080p{rate}.txt')
    with open(path, 'w') as f:
        f.write(to_hex(build(rate)))
    result = subprocess.run(['v4l2-ctl', '-d', device, f'--set-edid=file={path}'], capture_output=True, text=True)
    print(f"EDID set to 1080p{rate}" if result.returncode == 0 else f"Setting EDID failed: {result.stderr.strip()}")
    return result.returncode == 0


if __name__ == '__main__':
    if '--apply' in sys.argv:
        config = sys.argv[sys.argv.index('--config') + 1] if '--config' in sys.argv else os.path.expanduser('~/stream-config.json')
        try:
            with open(config) as f:
                rate = str(json.load(f).get('framerate') or DEFAULT_RATE)
        except (OSError, ValueError):
            rate = DEFAULT_RATE
        apply(rate, cache_dir='/run/touchstream')
    else:
        sys.stdout.write(to_hex(build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RATE)))
