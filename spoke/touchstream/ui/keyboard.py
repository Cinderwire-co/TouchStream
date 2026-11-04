"""Compact on-screen keyboard sized for the 480x320 touchscreen

Covers every printable ASCII character, which is what WPA passphrases allow.
Emits on_key(key) where key is a character, 'backspace' or 'enter'.
"""
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from . import theme

# Each row is a list of (key, width in key units); a row spans 10 units
_LETTERS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
LAYOUTS = {
    'lower': [[(c, 1) for c in _LETTERS[0]],
              [(c, 1) for c in _LETTERS[1]],
              [('shift', 1.5)] + [(c, 1) for c in _LETTERS[2]] + [('backspace', 1.5)],
              [('sym1', 2), ('space', 5), ('enter', 3)]],
    'upper': [[(c.upper(), 1) for c in _LETTERS[0]],
              [(c.upper(), 1) for c in _LETTERS[1]],
              [('shift', 1.5)] + [(c.upper(), 1) for c in _LETTERS[2]] + [('backspace', 1.5)],
              [('sym1', 2), ('space', 5), ('enter', 3)]],
    'sym1': [[(c, 1) for c in '1234567890'],
             [(c, 1) for c in '-/:;()$&@"'],
             [('sym2', 1.5)] + [(c, 1) for c in ".,?!'_+"] + [('backspace', 1.5)],
             [('lower', 2), ('space', 5), ('enter', 3)]],
    'sym2': [[(c, 1) for c in '[]{}#%^*=~'],
             [(c, 1) for c in '\\|<>`.,?!\''],
             [('sym1', 1.5)] + [(c, 1) for c in '-/:;@&$'] + [('backspace', 1.5)],
             [('lower', 2), ('space', 5), ('enter', 3)]],
}
LABELS = {'shift': '⇧', 'backspace': '←', 'space': 'space', 'sym1': '123',
          'sym2': '#+=', 'lower': 'abc'}
GLYPHS = ('⇧', '←')  # only JetBrains Mono has these
GAP = 4


class _Key(ButtonBehavior, Label):
    def __init__(self, key, label, accent=False, lit=False, **kwargs):
        self.key = key
        self._up = theme.EMBER if accent else theme.FAINT if lit else theme.CHARCOAL
        self._down = theme.EMBER_DEEP if accent else theme.FAINT
        small = len(label) > 1
        font = theme.MONO if label in GLYPHS else theme.SANS_SEMI if small else theme.SANS
        super().__init__(text=label, color=theme.INK if accent else theme.ASH,
                         font_name=font, font_size=13 if small else 18, **kwargs)
        with self.canvas.before:
            self._bg = Color(*self._up)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[7])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size

    def on_state(self, _, state):
        self._bg.rgba = self._down if state == 'down' else self._up


class Keyboard(Widget):
    __events__ = ('on_key',)

    def __init__(self, enter_label='Connect', **kwargs):
        super().__init__(**kwargs)
        self.enter_label = enter_label
        self.layout = 'lower'
        self.bind(pos=self._build, size=self._build)

    def on_key(self, key):
        pass

    def _press(self, key):
        if key == 'shift':
            self.layout = 'upper' if self.layout == 'lower' else 'lower'
            self._build()
        elif key in LAYOUTS:
            self.layout = key
            self._build()
        else:
            self.dispatch('on_key', ' ' if key == 'space' else key)
            if self.layout == 'upper':
                # Shift applies to one letter, like a phone keyboard
                self.layout = 'lower'
                self._build()

    def _build(self, *_):
        self.clear_widgets()
        rows = LAYOUTS[self.layout]
        unit = (self.width - GAP * 9) / 10
        row_h = (self.height - GAP * (len(rows) - 1)) / len(rows)
        for r, row in enumerate(rows):
            # A key w units wide also covers the w-1 gaps inside it
            units = sum(w for _, w in row)
            span = units * unit + GAP * (units - 1)
            x = self.x + (self.width - span) / 2
            y = self.top - (r + 1) * row_h - r * GAP
            for key, w in row:
                label = self.enter_label if key == 'enter' else LABELS.get(key, key)
                widget = _Key(key, label, accent=(key == 'enter'),
                              lit=(key == 'shift' and self.layout == 'upper'),
                              size_hint=(None, None),
                              pos=(x, y), size=(w * unit + GAP * (w - 1), row_h))
                widget.bind(on_release=lambda k: self._press(k.key))
                self.add_widget(widget)
                x += w * unit + GAP * w
