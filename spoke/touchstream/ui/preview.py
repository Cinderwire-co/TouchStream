"""Main screen: live preview with status bars; tap anywhere for the menu

    +-----------------------------------------------+
    | * STANDBY                     ETH 192.168.1.5 |  top bar
    |▌▌                                             |
    |▌▌            480x270 preview                  |
    |▌▌                                        (●) |  Record, while there's a picture
    | Studio A               TAP FOR MENU  52° · 7% |  bottom bar
    +-----------------------------------------------+

Record starts a recording on the spoke at once; stopping takes a second tap, so a stray
touch never ends one.
"""
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image

from . import theme
from .widgets import RecordButton, StatusPill, StereoMeter, eyebrow, paint, text

TOP_BAR = 26
BOTTOM_BAR = 26


class PreviewView(FloatLayout):
    def __init__(self, texture, on_tap, on_record, **kwargs):
        super().__init__(**kwargs)
        self.on_tap = on_tap
        self.on_record = on_record
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
        self.record = RecordButton(opacity=0, disabled=True)
        self.record.bind(on_release=lambda *_: self._record_tapped())
        self.record_hint = eyebrow('', color=theme.ASH, size=9, halign='center', size_hint=(None, None), opacity=0)
        paint(self.record_hint, (0, 0, 0, 0.75), radius=8)
        self.add_widget(self.record)
        self.add_widget(self.record_hint)
        self._armed = None

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
        self.record.pos = (x + w - 60, y + BOTTOM_BAR + 8)
        self.record_hint.pos, self.record_hint.size = (x + w - 200, y + BOTTOM_BAR + 22), (132, 24)

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

    def set_record(self, available, recording):
        """Show Record while there's something to record, and whether it's recording."""
        self.record.opacity = 1 if available else 0
        self.record.disabled = not available
        self.record.recording = recording
        if not recording and self._armed:
            self._disarm()

    def _record_tapped(self):
        if not self.record.recording:
            self.on_record()
            self.record.recording = True
        elif self._armed:
            self._disarm()
            self.on_record()
            self.record.recording = False
        else:
            self.record_hint.text, self.record_hint.opacity = 'Tap again to stop'.upper(), 1
            self._armed = Clock.schedule_once(lambda dt: self._disarm(), 4)

    def _disarm(self):
        if self._armed:
            self._armed.cancel()
        self._armed = None
        self.record_hint.text, self.record_hint.opacity = '', 0

    def on_touch_down(self, touch):
        if not self.record.disabled and self.record.collide_point(*touch.pos):
            return self.record.on_touch_down(touch)
        if self.collide_point(*touch.pos):
            self.on_tap()
            return True
        return False
