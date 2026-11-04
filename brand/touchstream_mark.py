#!/usr/bin/env python3
"""
The TouchStream mark: an ember play shape sending out two arcs of signal, both
broken into chipped shards like the Cinderwire mark they come from.

    python3 brand/touchstream_mark.py      (needs Pillow)

Writes the mark (and the app icon: the mark on an ink tile) wherever it's used:

  brand/touchstream-mark.svg                          the master
  hub/touchstream_hub/static/img/touchstream-mark.svg dashboard
  hub/touchstream_hub/static/img/touchstream-icon.png favicon, Mac app icon (1024 px)
  hub/touchstream_hub/static/img/touchstream.ico      Windows Start menu shortcut
  brand/banner.png                                    the top of the README
  spoke/assets/brand/touchstream_mark.png             touchscreen, boot splash, holding screen
                                                      (then run spoke/tools/render-brand-assets.py)

Copyright (c) 2025 Will Reeves and TouchStream Contributors
Licensed under the MIT License - see LICENSE file for details
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
INK, ASH, EMBER = '#0e0e0e', '#f2f2f3', '#fe5c09'
SIZE = 512
MARGIN = 34
CENTER = (176, 256)        # where the arcs radiate from (before centring)


def polar(r, deg):
    a = math.radians(deg)
    return CENTER[0] + r * math.cos(a), CENTER[1] + r * math.sin(a)


def shard(r_in, r_out, inner, outer, notch=None, step=13):
    """One chipped piece of an arc: the outer edge from outer[0] to outer[1] degrees, back
    along the inner edge from inner[1] to inner[0]. Ends lean (inner and outer angles differ),
    corners are bevelled, and `notch` (degrees) cuts a crack partway in from the outside."""
    def edge(r, a0, a1, wobble):
        n = max(2, round(abs(a1 - a0) / step))
        pts = []
        for i in range(n + 1):
            a = a0 + (a1 - a0) * i / n
            # Straight facets with a slight, fixed unevenness: chipped, not wobbly
            pts.append(polar(r + (wobble if 0 < i < n and i % 2 else 0), a))
        return pts
    out = edge(r_out, *outer, 2.5)
    if notch is not None:
        k = min(range(1, len(out) - 1), key=lambda i: abs(math.degrees(math.atan2(out[i][1] - CENTER[1], out[i][0] - CENTER[0])) - notch))
        out[k:k + 1] = [polar(r_out, notch - 1.6), polar(r_out - 19, notch + 0.4), polar(r_out, notch + 1.8)]
    inn = edge(r_in, *inner, -2)[::-1]
    # Bevel the four corners a little, unevenly
    pts = [*out, *inn]
    bevel = {0: 7, len(out) - 1: 5, len(out): 6, len(pts) - 1: 8}
    shaped = []
    for i, p in enumerate(pts):
        if i in bevel:
            prev, nxt = pts[i - 1], pts[(i + 1) % len(pts)]
            d = bevel[i]
            shaped += [_toward(p, prev, d), _toward(p, nxt, d)]
        else:
            shaped.append(p)
    return shaped


def _toward(p, q, d):
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy) or 1
    return p[0] + dx * d / length, p[1] + dy * d / length


def play():
    """The ember core: a play triangle with chipped corners and one slight kink in its back."""
    tl, tip, bl = (104, 158), (272, 252), (110, 354)
    left_kink = (98, 262)
    pts = []
    for corner, prev, nxt, a, b in ((tl, left_kink, tip, 16, 20), (tip, tl, bl, 17, 13), (bl, tip, left_kink, 22, 15)):
        pts += [_toward(corner, prev, a), _toward(corner, nxt, b)]
        if corner == bl:
            pts.append(left_kink)
    return pts


def shapes():
    ash = [
        # Inner arc: two pieces
        shard(122, 170, inner=(-57, 3.5), outer=(-61, 7)),
        shard(122, 170, inner=(8.6, 56), outer=(10.8, 59)),
        # Outer arc: three pieces, a crack partway into the middle one
        shard(198, 250, inner=(-49, -19), outer=(-53, -16.4)),
        shard(198, 250, inner=(-15.8, 18.8), outer=(-13.8, 22.6), notch=4),
        shard(198, 250, inner=(22, 49), outer=(25.2, 52)),
    ]
    return ash, [play()]


def fitted():
    """Centre the mark in the square with a margin, scaled to fill it."""
    ash, ember = shapes()
    xs = [x for poly in ash + ember for x, _ in poly]
    ys = [y for poly in ash + ember for _, y in poly]
    scale = (SIZE - 2 * MARGIN) / max(max(xs) - min(xs), max(ys) - min(ys))
    ox = (SIZE - (max(xs) - min(xs)) * scale) / 2 - min(xs) * scale
    oy = (SIZE - (max(ys) - min(ys)) * scale) / 2 - min(ys) * scale
    fit = lambda polys: [[(x * scale + ox, y * scale + oy) for x, y in poly] for poly in polys]
    return fit(ash), fit(ember)


def svg():
    ash, ember = fitted()
    path = lambda poly: 'M' + ' '.join(f'{x:.1f} {y:.1f}' for x, y in poly) + 'Z'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">'
            f'<title>TouchStream</title>'
            f'<path fill="{ASH}" d="{"".join(path(p) for p in ash)}"/>'
            f'<path fill="{EMBER}" d="{"".join(path(p) for p in ember)}"/></svg>\n')


def png(size, tile=False):
    """The mark at `size` px, drawn 4x larger and scaled down for smooth edges.
    With `tile`, on a rounded ink square (the app icon)."""
    big = size * 4
    img = Image.new('RGBA', (big, big))
    draw = ImageDraw.Draw(img)
    s = big / SIZE
    inset = 0
    if tile:
        # macOS-style icon: an 824/1024 tile, the mark inside it
        pad = big * 100 / 1024
        draw.rounded_rectangle((pad, pad, big - pad, big - pad), radius=big * 0.18, fill=INK)
        inset, s = pad + big * 0.09, (big - 2 * (pad + big * 0.09)) / SIZE
    ash, ember = fitted()
    for polys, colour in ((ash, ASH), (ember, EMBER)):
        for poly in polys:
            draw.polygon([(inset + x * s, inset + y * s) for x, y in poly], fill=colour)
    return img.resize((size, size), Image.LANCZOS)


def banner(width=1600, height=560):
    """The README's banner: the mark with its ember glow, the wordmark, the tagline, made by Cinderwire."""
    fonts = ROOT / 'spoke' / 'assets' / 'fonts'
    img = Image.new('RGBA', (width, height), INK)
    glow = Image.new('RGBA', (width, height))
    ImageDraw.Draw(glow).ellipse((70, 80, 470, 480), fill=(254, 92, 9, 70))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))
    img.alpha_composite(png(340), (120, (height - 340) // 2))
    draw = ImageDraw.Draw(img)
    face = lambda name, size: ImageFont.truetype(str(fonts / name), size)
    x = 540
    eyebrow = face('JetBrainsMono-Medium.ttf', 22)
    for i, ch in enumerate('BY CINDERWIRE'):
        draw.text((x + i * 19, 150), ch, font=eyebrow, fill=EMBER)
    draw.text((x - 6, 178), 'TouchStream', font=face('Bricolage-Bold.ttf', 132), fill=ASH)
    tagline = face('Bricolage-Regular.ttf', 34)
    for i, line in enumerate(('Schedule, stream and record live video', 'from every room, on autopilot.')):
        draw.text((x, 348 + i * 46), line, font=tagline, fill='#8e8e93')
    return img.convert('RGB')


if __name__ == '__main__':
    mark = svg()
    (ROOT / 'brand' / 'touchstream-mark.svg').write_text(mark)
    static = ROOT / 'hub' / 'touchstream_hub' / 'static' / 'img'
    (static / 'touchstream-mark.svg').write_text(mark)
    png(1024, tile=True).save(static / 'touchstream-icon.png')
    png(256, tile=True).save(static / 'touchstream.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    png(512).save(ROOT / 'spoke' / 'assets' / 'brand' / 'touchstream_mark.png')
    banner().save(ROOT / 'brand' / 'banner.png', optimize=True)
    print('Wrote the TouchStream mark and icon')
