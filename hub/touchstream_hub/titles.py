"""Titles laid over an edited video: a lower third, a title, a caption or a corner tag

Each is drawn here as a transparent image the size of the video (so it looks the same at any
resolution) and FFmpeg fades it in and out over the picture. Drawing them here works with any
FFmpeg build; many, Homebrew's among them, can't draw text themselves.
"""
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path(__file__).parent / 'static' / 'fonts'
EMBER, INK, WHITE, SMOKE = (254, 92, 9, 255), (14, 14, 14, 255), (255, 255, 255, 255), (200, 200, 205, 255)
STYLES = {'lower_third': 'Lower third', 'title': 'Title', 'caption': 'Caption', 'corner': 'Corner tag'}
FADE = 0.4          # seconds each title fades in and out


@lru_cache(maxsize=32)
def _font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def _fit(text, name, size, width):
    """The font size (at most `size`) at which text fits the width."""
    while size > 12 and _font(name, size).getlength(text) > width:
        size -= 2
    return _font(name, size)


def color(hex_color, default=EMBER):
    """'#RRGGBB' as RGBA, or the default (TouchStream's ember) if it isn't one."""
    if isinstance(hex_color, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', hex_color):
        return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
    return default


def ink_on(rgba):
    """Dark or white text, whichever reads on that color."""
    return INK if 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2] > 150 else WHITE


def draw(title, width=1920, height=1080, accent=None):
    """A title {text, sub, style} as a transparent image the size of the video. accent: the brand's title color
    ('#RRGGBB') for the lower third's bar, the title's line under it and the corner tag; TouchStream's ember without."""
    accent = color(accent)
    u = height / 1080                               # everything is laid out for 1080p and scaled
    text, sub = str(title.get('text') or '').strip(), str(title.get('sub') or '').strip()
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    style = title.get('style') if title.get('style') in STYLES else 'lower_third'

    if style == 'lower_third':
        big = _fit(text, 'Bricolage-Bold.ttf', round(54 * u), width * 0.6)
        small = _fit(sub, 'Bricolage-Regular.ttf', round(32 * u), width * 0.6)
        x, bottom = round(96 * u), height - round(110 * u)
        inner = max(big.getlength(text), small.getlength(sub) if sub else 0)
        box_h = round((76 + (46 if sub else 0)) * u)
        top = bottom - box_h
        pen.rectangle((x, top, x + round(10 * u), bottom), fill=accent)
        pen.rectangle((x + round(10 * u), top, x + round(10 * u) + inner + round(56 * u), bottom), fill=(14, 14, 14, 225))
        pen.text((x + round(38 * u), top + round(18 * u)), text, font=big, fill=WHITE, anchor='lt')
        if sub:
            pen.text((x + round(38 * u), top + round(80 * u)), sub, font=small, fill=SMOKE, anchor='lt')
    elif style == 'title':
        big = _fit(text, 'Bricolage-Bold.ttf', round(110 * u), width * 0.85)
        small = _fit(sub, 'Bricolage-SemiBold.ttf', round(44 * u), width * 0.8)
        middle = height / 2 - (round(30 * u) if sub else 0)
        shade = Image.new('RGBA', image.size, (0, 0, 0, 0))
        ImageDraw.Draw(shade).text((width / 2, middle + 4 * u), text, font=big, fill=(0, 0, 0, 190), anchor='mm')
        image = Image.alpha_composite(image, shade.filter(ImageFilter.GaussianBlur(10 * u)))
        pen = ImageDraw.Draw(image)
        pen.text((width / 2, middle), text, font=big, fill=WHITE, anchor='mm')
        if sub:
            pen.text((width / 2, middle + round(92 * u)), sub, font=small, fill=accent, anchor='mm')
    elif style == 'caption':
        font = _fit(text, 'Bricolage-SemiBold.ttf', round(46 * u), width * 0.8)
        small = _font('Bricolage-Regular.ttf', round(30 * u))
        w = max(font.getlength(text), small.getlength(sub) if sub else 0) + round(64 * u)
        h = round((84 + (42 if sub else 0)) * u)
        top = height - round(90 * u) - h
        pen.rounded_rectangle(((width - w) / 2, top, (width + w) / 2, top + h), radius=round(18 * u), fill=(14, 14, 14, 215))
        pen.text((width / 2, top + round(42 * u)), text, font=font, fill=WHITE, anchor='mm')
        if sub:
            pen.text((width / 2, top + round(96 * u)), sub, font=small, fill=SMOKE, anchor='mm')
    else:   # corner
        font = _fit(text.upper(), 'Bricolage-Bold.ttf', round(30 * u), width * 0.4)
        w, h = font.getlength(text.upper()) + round(40 * u), round(52 * u)
        x, top = round(60 * u), round(54 * u)
        pen.rounded_rectangle((x, top, x + w, top + h), radius=round(10 * u), fill=accent)
        pen.text((x + w / 2, top + h / 2), text.upper(), font=font, fill=ink_on(accent), anchor='mm')
        if sub:
            pen.text((x + w + round(16 * u), top + h / 2), sub, font=_font('Bricolage-SemiBold.ttf', round(28 * u)),
                     fill=WHITE, anchor='lm', stroke_width=round(2 * u), stroke_fill=(0, 0, 0, 160))
    return image
