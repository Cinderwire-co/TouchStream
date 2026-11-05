#!/usr/bin/env python3
"""
Render the spoke's branded images from the TouchStream mark and fonts (needs Pillow).
The mark itself comes from brand/touchstream_mark.py.

    python3 tools/render-brand-assets.py

  system/plymouth/touchstream/*.png   boot splash layers (480x320 screen)
  assets/slate/slate.png             1920x1080 "Waiting for source" holding screen
  assets/slate/core.png              its ember core, pulsed live by the capture pipeline

Copyright (c) 2025 Will Reeves and TouchStream Contributors
Licensed under the MIT License - see LICENSE file for details
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MARK = ROOT / 'assets' / 'brand' / 'touchstream_mark.png'
FONTS = ROOT / 'assets' / 'fonts'
INK, ASH, SMOKE, EMBER = (14, 14, 14), (242, 242, 243), (142, 142, 147), (254, 92, 9)

# Where the slate's core sits; touchstream/media.py overlays core.png here
SLATE_SIZE = (1920, 1080)
SLATE_MARK = 340
SLATE_CENTER = (960, 460)


def split_mark(size):
    """The mark as two layers: the ash shards and the ember core."""
    mark = Image.open(MARK).convert('RGBA').resize((size, size), Image.LANCZOS)
    ring, core = Image.new('RGBA', mark.size), Image.new('RGBA', mark.size)
    for y in range(size):
        for x in range(size):
            r, g, b, a = mark.getpixel((x, y))
            (core if r - b > 90 else ring).putpixel((x, y), (r, g, b, a))
    return ring, core


def core_offset(core):
    """How far the ember core's middle sits from the middle of the mark (it's left of center)."""
    left, top, right, bottom = core.getbbox()
    return (left + right - core.width) // 2, (top + bottom - core.height) // 2


def glow(size, radius, alpha, offset=(0, 0)):
    """A soft ember glow, centered `offset` from the middle of the image so that
    centring the image on the mark puts the glow behind the core."""
    dx, dy = offset
    img = Image.new('RGBA', (size + 2 * abs(dx), size + 2 * abs(dy)))
    cx, cy, r = img.width // 2 + dx, img.height // 2 + dy, size // 2 - radius
    ImageDraw.Draw(img).ellipse((cx - r, cy - r, cx + r, cy + r), fill=EMBER + (alpha,))
    return img.filter(ImageFilter.GaussianBlur(radius / 2))


def text(value, font, size, color, tracking=0):
    face = ImageFont.truetype(str(FONTS / font), size)
    width = int(sum(face.getlength(ch) + tracking for ch in value)) + 8
    img = Image.new('RGBA', (width, size * 2))
    draw, x = ImageDraw.Draw(img), 2
    for ch in value:
        draw.text((x, 2), ch, font=face, fill=color)
        x += face.getlength(ch) + tracking
    return img.crop(img.getbbox())


def plymouth():
    out = ROOT / 'system' / 'plymouth' / 'touchstream'
    ring, core = split_mark(132)
    ring.save(out / 'ring.png')
    core.save(out / 'core.png')
    glow(220, 70, 150, core_offset(core)).save(out / 'glow.png')
    text('TouchStream', 'Bricolage-Bold.ttf', 26, ASH).save(out / 'wordmark.png')
    text('BY CINDERWIRE', 'JetBrainsMono-Medium.ttf', 10, SMOKE, tracking=2).save(out / 'eyebrow.png')


def slate():
    out = ROOT / 'assets' / 'slate'
    out.mkdir(exist_ok=True)
    w, h = SLATE_SIZE
    cx, cy = SLATE_CENTER
    ring, core = split_mark(SLATE_MARK)
    img = Image.new('RGBA', SLATE_SIZE, INK + (255,))
    g = glow(SLATE_MARK + 260, 180, 90, core_offset(core))
    img.alpha_composite(g, (cx - g.width // 2, cy - g.height // 2))
    img.alpha_composite(ring, (cx - SLATE_MARK // 2, cy - SLATE_MARK // 2))
    # A dim core stays in the slate so the pulse never fades to a hole
    dim = core.copy()
    dim.putalpha(dim.getchannel('A').point(lambda v: v * 35 // 100))
    img.alpha_composite(dim, (cx - SLATE_MARK // 2, cy - SLATE_MARK // 2))
    word = text('TouchStream', 'Bricolage-Bold.ttf', 78, ASH)
    img.alpha_composite(word, (cx - word.width // 2, cy + SLATE_MARK // 2 + 70))
    eye = text('WAITING FOR SOURCE', 'JetBrainsMono-Medium.ttf', 26, SMOKE, tracking=7)
    img.alpha_composite(eye, (cx - eye.width // 2, cy + SLATE_MARK // 2 + 180))
    img.convert('RGB').save(out / 'slate.png')
    core.save(out / 'core.png')


if __name__ == '__main__':
    plymouth()
    slate()
    print('Rendered boot splash and slate assets')
