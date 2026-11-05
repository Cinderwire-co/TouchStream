"""Burned-in captions for shorts, in a few fixed styles

Each style sets the font, size, case, colors, outline or box, where it sits, how many words show
at once and whether the word being spoken lights up. People choose a style and an accent color,
nothing more, so every short looks finished.

Captions are drawn here as transparent images, one for each change on screen, and FFmpeg lays
them over the video. That works with any FFmpeg build (many, Homebrew's among them, can't draw
text themselves).
"""
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path(__file__).parent / 'static' / 'fonts'
WIDTH = 1080            # the short's width
MARGIN = 90
WHITE, INK = (255, 255, 255, 255), (14, 14, 14, 255)

# highlight: 'word' (the spoken word in the accent color), 'pill' (a block of accent color behind
# it), 'all' (every word in the accent color) or None. gap: extra space between words.
# y: where the middle of the caption sits, down the frame.
STYLES = {
    'bold': {'label': 'Bold', 'font': 'Bricolage-Bold.ttf', 'size': 86, 'upper': True, 'words': 3, 'lines': 2,
             'stroke': 10, 'highlight': 'word', 'y': 0.66},
    'marker': {'label': 'Marker', 'font': 'Bricolage-Bold.ttf', 'size': 72, 'upper': False, 'words': 4, 'lines': 2,
               'stroke': 8, 'highlight': 'pill', 'gap': 16, 'y': 0.68},
    'boxed': {'label': 'Boxed', 'font': 'Bricolage-Bold.ttf', 'size': 58, 'upper': False, 'words': 8, 'lines': 2,
              'box': True, 'highlight': 'word', 'y': 0.74},
    'pop': {'label': 'Pop', 'font': 'Bricolage-Bold.ttf', 'size': 128, 'upper': True, 'words': 1, 'lines': 1,
            'stroke': 12, 'highlight': 'all', 'y': 0.6},
    'clean': {'label': 'Clean', 'font': 'Bricolage-SemiBold.ttf', 'size': 54, 'upper': False, 'words': 10, 'lines': 2,
              'shadow': True, 'highlight': None, 'y': 0.8},
}
ACCENTS = {'ember': '#FE5C09', 'gold': '#FACC15', 'lime': '#A3E635', 'sky': '#38BDF8', 'pink': '#F472B6',
           'white': '#FFFFFF'}
SAMPLE = 'Every voice in this room matters'


def _rgba(value):
    value = value.lstrip('#')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def _on(color):
    """Ink or white: whichever reads on top of this color."""
    r, g, b, _ = color
    return INK if 0.299 * r + 0.587 * g + 0.114 * b > 150 else WHITE


def _font(style):
    return _load(style['font'], style['size'])


@lru_cache(maxsize=16)
def _load(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def _word(text, style):
    text = text.strip()
    if style['upper']:
        # Big uppercase captions read better without trailing commas and full stops
        text = text.rstrip(',.;:').upper()
    return text


def _lines(words, style, font):
    """Words wrapped into lines that fit the width."""
    space = font.getlength(' ') + style.get('gap', 0)
    lines, line, used = [], [], 0
    for i, w in enumerate(words):
        width = font.getlength(w)
        if line and used + space + width > WIDTH - 2 * MARGIN:
            lines.append(line)
            line, used = [], 0
        line.append((i, w, width))
        used += (space if used else 0) + width
    if line:
        lines.append(line)
    return lines


def fits(texts, style):
    return len(_lines([_word(t, style) for t in texts], style, _font(style))) <= style['lines']


def band_height(style):
    return round(style['size'] * 1.3 * style['lines'] + style['size'])


def draw(texts, active, style, accent):
    """One caption as a transparent image WIDTH wide: `texts` on screen, `active` the index of the
    word being spoken (or None)."""
    font, accent = _font(style), _rgba(accent)
    words = [_word(t, style) for t in texts]
    lines = _lines(words, style, font)
    size, space = style['size'], font.getlength(' ') + style.get('gap', 0)
    step = round(size * 1.3)
    height = band_height(style)
    image = Image.new('RGBA', (WIDTH, height), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    top = (height - step * len(lines)) / 2
    placed = []          # (index, word, x, baseline)
    for n, line in enumerate(lines):
        x = (WIDTH - sum(w for _, _, w in line) - space * (len(line) - 1)) / 2
        for i, word, width in line:
            placed.append((i, word, x, width, top + n * step + size * 0.95))
            x += width + space
    if style.get('box'):
        left = min(p[2] for p in placed) - 30
        right = max(p[2] + p[3] for p in placed) + 30
        pen.rounded_rectangle((left, top - size * 0.2, right, top + step * len(lines) + size * 0.05), radius=26,
                              fill=(14, 14, 14, 214))
    if style.get('shadow'):
        shade = Image.new('RGBA', image.size, (0, 0, 0, 0))
        shade_pen = ImageDraw.Draw(shade)
        for _, word, x, _, base in placed:
            shade_pen.text((x + 2, base + 4), word, font=font, anchor='ls', fill=(0, 0, 0, 200))
        image = Image.alpha_composite(image, shade.filter(ImageFilter.GaussianBlur(6)))
        pen = ImageDraw.Draw(image)
    for i, word, x, width, base in placed:
        color = WHITE
        if style['highlight'] == 'all' or (style['highlight'] == 'word' and i == active):
            color = accent
        if style['highlight'] == 'pill' and i == active:
            pen.rounded_rectangle((x - 14, base - size * 0.86, x + width + 14, base + size * 0.26), radius=16, fill=accent)
            color = _on(accent)
        stroke = style.get('stroke', 0) if not (style['highlight'] == 'pill' and i == active) else 0
        pen.text((x, base), word, font=font, anchor='ls', fill=color, stroke_width=stroke, stroke_fill=INK)
    return image


def pages(words, style):
    """Timed words [{start, end, text}] grouped into what's on screen together: a few words at a
    time, breaking at the end of a sentence and at pauses."""
    out, page = [], []
    for w in words:
        if page and (len(page) >= style['words'] or w['start'] - page[-1]['end'] > 0.6
                     or not fits([p['text'] for p in page + [w]], style)):
            out.append(page)
            page = []
        page.append(w)
        if w['text'].strip().endswith(('.', '?', '!')):
            out.append(page)
            page = []
    if page:
        out.append(page)
    return out


def frames(words, style_name, accent, folder):
    """Draw the captions for timed words into `folder`, and write the FFmpeg concat list that shows
    each at its time (blank between). Returns the list's path, or None with no words."""
    style = STYLES[style_name]
    shown = []                                  # (image path, start, end)
    for p, page in enumerate(pages(words, style)):
        steps = range(len(page)) if style['highlight'] in ('word', 'pill') else [None]
        for i in steps:
            start = page[i if i is not None else 0]['start']
            end = page[i + 1]['start'] if i is not None and i + 1 < len(page) else page[-1]['end']
            path = folder / f'cap-{p:04d}-{i or 0:02d}.png'
            draw([w['text'] for w in page], i, style, accent).save(path)
            shown.append((path, start, end))
    if not shown:
        return None
    blank = folder / 'blank.png'
    Image.new('RGBA', (WIDTH, band_height(style)), (0, 0, 0, 0)).save(blank)
    listing, clock = ['ffconcat version 1.0'], 0.0
    for n, (path, start, end) in enumerate(shown):
        # Close gaps shorter than a quarter second, so captions don't flicker between words
        if n + 1 < len(shown) and shown[n + 1][1] - end < 0.25:
            end = shown[n + 1][1]
        if start > clock:
            listing += [f"file '{blank.name}'", f'duration {start - clock:.3f}']
            clock = start
        if end > clock:
            listing += [f"file '{path.name}'", f'duration {end - clock:.3f}']
            clock = end
    listing += [f"file '{blank.name}'", 'duration 1', f"file '{blank.name}'"]   # the last entry's duration needs a next one
    target = folder / 'captions.ffconcat'
    target.write_text('\n'.join(listing) + '\n')
    return target


def sample(style_name, accent):
    """A preview of a style for the dashboard's picker."""
    words = SAMPLE.split()
    style = STYLES[style_name]
    shown = words[:style['words']] if style['words'] < len(words) else words
    image = draw(shown, 1 if style['highlight'] in ('word', 'pill') and len(shown) > 1 else 0, style, accent)
    left, top, right, bottom = image.getbbox()
    return image.crop((max(0, left - 12), max(0, top - 12), min(image.width, right + 12), min(image.height, bottom + 12)))
