"""Main screen: live preview with status bars; tap anywhere for the menu

    +-----------------------------------------------+
    | * STANDBY                     ETH 192.168.1.5 |  top bar
    |▌▌                                             |
    |▌▌            480x270 preview                  |
    |▌▌                                             |
    | Studio A               TAP FOR MENU  52° · 7% |  bottom bar
    +-----------------------------------------------+
"""
from kivy.animation import Animation
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image

from . import theme
from .widgets import StatusPill, StereoMeter, eyebrow, paint, text

TOP_BAR = 26
BOTTOM_BAR = 26


class PreviewView(FloatLayout):
    def __init__(self, texture, on_tap, **kwargs):
        super().__init__(**kwargs)
        self.on_tap = on_tap
        paint(self, theme.INK)

        self.video = Image(texture=texture, fit_mode='contain', size_hint=(None, None), opacity=0)
        self.add_widget(self.video)

        # No-signal placeholder: the TouchStream mark, breathing like the boot splash
        self.mark = Image(source=theme.MARK, size_hint=(None, None), size=(64, 64))
        self.placeholder_title = eyebrow('No signal', size=11, color=theme.ASH, halign='center',
                                         size_hint=(None, None))
        self.placeholder_hint = text('Connect an HDMI source', size=12, color=theme.SMOKE,
                                     halign='center', size_hint=(None, None))
        for w in (self.mark, self.placeholder_title, self.placeholder_hint):
            self.add_widget(w)
        self._breathe = (Animation(opacity=0.45, d=1.2, t='in_out_sine')
                         + Animation(opacity=1, d=1.2, t='in_out_sine'))
        self._breathe.repeat = True
        self._breathe.start(self.mark)

        self.meter = StereoMeter(size_hint=(None, None))
        self.status = StatusPill(size_hint=(None, None))
        self.network = eyebrow(halign='right', size_hint=(None, None))
        self.device_name = text(font=theme.SANS_SEMI, size=13, size_hint=(None, None))
        self.hint = eyebrow('Tap for menu', color=theme.FAINT, size=9, halign='center',
                            size_hint=(None, None))
        self.metrics = eyebrow(color=theme.SMOKE, halign='right', size_hint=(None, None))
        for w in (self.meter, self.status, self.network, self.device_name, self.hint, self.metrics):
            self.add_widget(w)

        self.has_signal = False
        self.bind(pos=self._layout, size=self._layout)

    def _layout(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        video_h = h - TOP_BAR - BOTTOM_BAR
        self.video.pos, self.video.size = (x, y + BOTTOM_BAR), (w, video_h)

        cx, cy = x + w / 2, y + BOTTOM_BAR + video_h / 2
        self.mark.pos = (cx - 32, cy - 10)
        self.placeholder_title.pos, self.placeholder_title.size = (x, cy - 36), (w, 18)
        self.placeholder_hint.pos, self.placeholder_hint.size = (x, cy - 56), (w, 18)

        self.meter.pos, self.meter.size = (x + 4, y + BOTTOM_BAR + 6), (8, video_h - 12)

        top_y = y + h - TOP_BAR
        self.status.pos, self.status.size = (x + 12, top_y), (w * 0.5, TOP_BAR)
        self.network.pos, self.network.size = (x + w * 0.4, top_y), (w * 0.6 - 12, TOP_BAR)

        self.device_name.pos, self.device_name.size = (x + 12, y), (w * 0.38, BOTTOM_BAR)
        self.hint.pos, self.hint.size = (x + w * 0.35, y), (w * 0.3, BOTTOM_BAR)
        self.metrics.pos, self.metrics.size = (x + w * 0.65, y), (w * 0.35 - 12, BOTTOM_BAR)

    def set_signal(self, has_signal):
        if has_signal == self.has_signal:
            return
        self.has_signal = has_signal
        self.video.opacity = 1 if has_signal else 0
        for w in (self.mark, self.placeholder_title, self.placeholder_hint):
            w.opacity = 0 if has_signal else 1
        if has_signal:
            self._breathe.cancel(self.mark)
        else:
            self._breathe.start(self.mark)

    def set_status(self, label, rgba):
        self.status.set(label, rgba)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.on_tap()
            return True
        return False
