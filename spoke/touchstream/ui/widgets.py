"""Small Cinderwire-styled widgets for the 480x320 touchscreen"""
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from . import theme


def paint(widget, rgba, radius=0):
    """Give `widget` a (rounded) background; returns the Color to recolor it later."""
    with widget.canvas.before:
        color = Color(*rgba)
        rect = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
    widget.bind(pos=lambda w, v: setattr(rect, 'pos', v),
                size=lambda w, v: setattr(rect, 'size', v))
    return color


def text(value='', font=theme.SANS, size=14, color=theme.ASH, bold=False,
         halign='left', valign='middle', **kwargs):
    """A Label whose alignment works inside its own box."""
    label = Label(text=value, font_name=font, font_size=size, color=color, bold=bold,
                  halign=halign, valign=valign, shorten=True, shorten_from='right', **kwargs)
    label.bind(size=lambda w, v: setattr(w, 'text_size', v))
    return label


def eyebrow(value='', color=theme.SMOKE, size=10, **kwargs):
    """Mono uppercase label."""
    return text(value.upper(), font=theme.MONO, size=size, color=color, bold=True, **kwargs)


class CwButton(ButtonBehavior, Label):
    KINDS = {
        'primary': (theme.EMBER, theme.EMBER_DEEP, theme.INK),
        'secondary': (theme.CHARCOAL, theme.CHAR2, theme.ASH),
        'ghost': ((0, 0, 0, 0), theme.LINE, theme.ASH),
    }

    def __init__(self, label, kind='secondary', on_press=None, font_size=15, **kwargs):
        self._bg_up, self._bg_down, fg = self.KINDS[kind]
        super().__init__(text=label, font_name=theme.SANS_SEMI, font_size=font_size,
                         color=fg, halign='center', valign='middle', **kwargs)
        self.bind(size=lambda w, v: setattr(w, 'text_size', v))
        self._bg = paint(self, self._bg_up, radius=12)
        if kind == 'ghost':
            with self.canvas.before:
                Color(*theme.LINE)
                border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, 12), width=1)
            self.bind(pos=lambda w, v: setattr(border, 'rounded_rectangle', (*w.pos, *w.size, 12)),
                      size=lambda w, v: setattr(border, 'rounded_rectangle', (*w.pos, *w.size, 12)))
        if on_press:
            self.bind(on_release=lambda *_: on_press())

    def on_state(self, _, state):
        self._bg.rgba = self._bg_down if state == 'down' else self._bg_up


class IconButton(ButtonBehavior, Widget):
    """40x40 touch target drawing a simple line icon: back, close or refresh."""
    icon = StringProperty('close')

    def __init__(self, icon, on_press=None, **kwargs):
        kwargs.setdefault('size_hint', (None, None))
        kwargs.setdefault('size', (44, 40))
        super().__init__(icon=icon, **kwargs)
        self.bind(pos=self._draw, size=self._draw, state=self._draw)
        if on_press:
            self.bind(on_release=lambda *_: on_press())
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        cx, cy = self.center
        with self.canvas:
            if self.state == 'down':
                Color(*theme.LINE)
                Ellipse(pos=(cx - 18, cy - 18), size=(36, 36))
            Color(*theme.ASH)
            if self.icon == 'back':
                Line(points=[cx + 4, cy + 8, cx - 4, cy, cx + 4, cy - 8], width=1.6, cap='round', joint='round')
            elif self.icon == 'close':
                Line(points=[cx - 7, cy - 7, cx + 7, cy + 7], width=1.6, cap='round')
                Line(points=[cx - 7, cy + 7, cx + 7, cy - 7], width=1.6, cap='round')
            elif self.icon == 'refresh':
                # Three-quarter circle (Kivy angles: 0 = top, clockwise) ending at the top
                # with a chevron pointing clockwise
                Line(circle=(cx, cy, 8, 90, 360), width=1.6, cap='round')
                Line(points=[cx - 3, cy + 12, cx + 1, cy + 8, cx - 3, cy + 4], width=1.6,
                     cap='round', joint='round')


class SignalBars(Widget):
    """Four ascending bars for Wi-Fi strength (0-100)."""
    strength = NumericProperty(0)

    def __init__(self, **kwargs):
        kwargs.setdefault('size_hint', (None, None))
        kwargs.setdefault('size', (18, 14))
        super().__init__(**kwargs)
        self.bind(pos=self._draw, size=self._draw, strength=self._draw)
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        lit = 1 + (self.strength > 30) + (self.strength > 55) + (self.strength > 75)
        bar_w = (self.width - 6) / 4
        with self.canvas:
            for i in range(4):
                Color(*(theme.ASH if i < lit else theme.FAINT))
                h = self.height * (i + 1) / 4
                Rectangle(pos=(self.x + i * (bar_w + 2), self.y), size=(bar_w, h))


class LockIcon(Widget):
    def __init__(self, **kwargs):
        kwargs.setdefault('size_hint', (None, None))
        kwargs.setdefault('size', (12, 14))
        super().__init__(**kwargs)
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        with self.canvas:
            Color(*theme.SMOKE)
            RoundedRectangle(pos=(self.x, self.y), size=(self.width, self.height * 0.55), radius=[2])
            Line(circle=(self.center_x, self.y + self.height * 0.6, self.width * 0.3, -90, 90), width=1.3)


class StatusPill(Widget):
    """Colored dot + mono label, e.g. a green STANDBY."""
    label = StringProperty('')
    live = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._text = eyebrow(size=10, color=theme.ASH)
        self.add_widget(self._text)
        with self.canvas:
            self._dot_color = Color(*theme.SMOKE)
            self._dot = Ellipse(size=(7, 7))
        self.bind(pos=self._layout, size=self._layout, label=self._layout)
        self._layout()

    def set(self, label, rgba):
        self.label = label
        self._dot_color.rgba = rgba

    def _layout(self, *_):
        self._dot.pos = (self.x, self.center_y - 3.5)
        self._text.text = self.label.upper()
        self._text.pos = (self.x + 13, self.y)
        self._text.size = (self.width - 13, self.height)


class StereoMeter(Widget):
    """Two thin vertical level bars (0-1) with a faint track."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.levels = [0.0, 0.0]
        with self.canvas:
            Color(*theme.LINE_SOFT)
            self._tracks = [Rectangle(), Rectangle()]
            self._colors, self._bars = [], []
            for _ in range(2):
                self._colors.append(Color(*theme.OK))
                self._bars.append(Rectangle())
        self.bind(pos=self._update, size=self._update)

    def set_levels(self, left, right):
        self.levels = [left, right]
        self._update()

    def _update(self, *_):
        bar_w = (self.width - 2) / 2
        for i, level in enumerate(self.levels):
            x = self.x + i * (bar_w + 2)
            self._tracks[i].pos = (x, self.y)
            self._tracks[i].size = (bar_w, self.height)
            self._bars[i].pos = (x, self.y)
            self._bars[i].size = (bar_w, self.height * level)
            self._colors[i].rgba = theme.BAD if level > 0.95 else theme.EMBER if level > 0.8 else theme.OK


class RecordButton(ButtonBehavior, Widget):
    """The round Record button: a red dot to start, a red square while recording."""
    recording = BooleanProperty(False)

    def __init__(self, **kwargs):
        kwargs.setdefault('size_hint', (None, None))
        kwargs.setdefault('size', (52, 52))
        super().__init__(**kwargs)
        self.bind(pos=self._draw, size=self._draw, state=self._draw, recording=self._draw)
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        cx, cy = self.center
        r = min(self.width, self.height) / 2 - 3
        with self.canvas:
            Color(0, 0, 0, 0.55 if self.state == 'normal' else 0.8)
            Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))
            Color(*theme.ASH[:3], 0.9)
            Line(circle=(cx, cy, r), width=1.6)
            Color(*theme.REC)
            if self.recording:
                RoundedRectangle(pos=(cx - 9, cy - 9), size=(18, 18), radius=[4])
            else:
                Ellipse(pos=(cx - 12, cy - 12), size=(24, 24))
