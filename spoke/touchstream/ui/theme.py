"""Cinderwire brand tokens (Ink, Ash and Ember; Bricolage Grotesque + JetBrains Mono)"""
import os

from kivy.core.text import LabelBase

from .. import ASSETS_DIR


def _hex(value, alpha=1.0):
    value = value.lstrip('#')
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (alpha,)


INK = _hex('#0E0E0E')
CHAR2 = _hex('#1A1A1A')
CHARCOAL = _hex('#272727')
ASH = _hex('#F2F2F3')
SMOKE = _hex('#8E8E93')
FAINT = _hex('#5E5E63')
LINE = _hex('#F2F2F3', 0.14)
LINE_SOFT = _hex('#F2F2F3', 0.08)
EMBER = _hex('#FE5C09')
EMBER_HI = _hex('#FF7A2F')
EMBER_DEEP = _hex('#B83F00')
EMBER_SOFT = _hex('#FE5C09', 0.15)
# Status colors: real status only, never decoration
OK = _hex('#4ADE80')
BAD = _hex('#F87171')
REC = _hex('#EF4444')     # the Record button

SANS = 'Bricolage'        # regular + bold
SANS_SEMI = 'BricolageSemi'
MONO = 'Mono'             # regular + medium (as bold)

MARK = os.path.join(ASSETS_DIR, 'brand', 'touchstream_mark.png')
CINDERWIRE = os.path.join(ASSETS_DIR, 'brand', 'cinderwire_mark.png')   # the "Made by Cinderwire" credit


def register_fonts():
    fonts = os.path.join(ASSETS_DIR, 'fonts')
    LabelBase.register(name=SANS, fn_regular=os.path.join(fonts, 'Bricolage-Regular.ttf'),
                       fn_bold=os.path.join(fonts, 'Bricolage-Bold.ttf'))
    LabelBase.register(name=SANS_SEMI, fn_regular=os.path.join(fonts, 'Bricolage-SemiBold.ttf'))
    LabelBase.register(name=MONO, fn_regular=os.path.join(fonts, 'JetBrainsMono-Regular.ttf'),
                       fn_bold=os.path.join(fonts, 'JetBrainsMono-Medium.ttf'))
