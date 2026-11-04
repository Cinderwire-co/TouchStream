"""Full-screen panels shown over the preview: menu, Wi-Fi list, password entry"""
import os
import threading
import time

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.scrollview import ScrollView

from .. import VERSION, hub, recorder, wifi
from ..config import load_config, save_config
from ..system import device_ip, network_summary
from . import theme
from .keyboard import Keyboard
from .widgets import CwButton, IconButton, LockIcon, SignalBars, eyebrow, paint, text

HEADER = 44
PAD = 16


def in_background(work, done):
    """Run work() on a thread, then done(result) on the UI thread."""
    def run():
        result = work()
        Clock.schedule_once(lambda dt: done(result))
    threading.Thread(target=run, daemon=True).start()


class Panel(FloatLayout):
    """A full-screen sheet with a header. Swallows touches so the preview never sees them."""

    def __init__(self, nav, title, back=True, **kwargs):
        super().__init__(**kwargs)
        self.nav = nav
        paint(self, theme.INK)
        self.title = text(title, bold=True, size=18, size_hint=(None, None))
        self.nav_button = IconButton('back' if back else 'close', on_press=nav.pop)
        self.back = back
        self.add_widget(self.title)
        self.add_widget(self.nav_button)
        self.bind(pos=self._layout_header, size=self._layout_header)

    def _layout_header(self, *_):
        top = self.top - HEADER
        if self.back:
            self.nav_button.pos = (self.x + 2, top + 2)
            self.title.pos, self.title.size = (self.x + 46, top), (self.width - 100, HEADER)
        else:
            self.nav_button.pos = (self.right - 46, top + 2)
            self.title.pos, self.title.size = (self.x + PAD, top), (self.width - 70, HEADER)

    def on_open(self):
        pass

    def on_touch_down(self, touch):
        super().on_touch_down(touch)
        return True

    def on_touch_move(self, touch):
        super().on_touch_move(touch)
        return True

    def on_touch_up(self, touch):
        super().on_touch_up(touch)
        return True


class MenuPanel(Panel):
    ROWS = ('Name', 'Hub', 'Network', 'IP address', 'Location', 'Version')

    def __init__(self, nav, **kwargs):
        super().__init__(nav, 'TouchStream', back=False, **kwargs)
        self.rows = {}
        for name in self.ROWS:
            label = eyebrow(name, size=9, size_hint=(None, None))
            value = text(size=14, size_hint=(None, None))
            self.add_widget(label)
            self.add_widget(value)
            self.rows[name] = (label, value)
        self.wifi_button = CwButton('Wi-Fi', on_press=lambda: nav.push(WifiPanel(nav)),
                                    size_hint=(None, None))
        self.hub_button = CwButton('Hub', on_press=lambda: nav.push(HubPanel(nav)),
                                   size_hint=(None, None))
        self.recordings_button = CwButton('Recordings', on_press=lambda: nav.push(RecordingsPanel(nav)),
                                          size_hint=(None, None))
        self.bars_button = CwButton('', on_press=self._toggle_bars, size_hint=(None, None))
        self.reboot_button = CwButton('Reboot', kind='ghost', on_press=self._reboot,
                                      size_hint=(None, None))
        self.buttons = (self.wifi_button, self.hub_button, self.recordings_button, self.bars_button,
                        self.reboot_button)
        for button in self.buttons:
            self.add_widget(button)
        # Where TouchStream comes from
        self.credit_mark = Image(source=theme.CINDERWIRE, size_hint=(None, None), size=(16, 16), opacity=0.8)
        self.credit = text('Made by Cinderwire · MIT License', size=10, color=theme.SMOKE, size_hint=(None, None))
        self.add_widget(self.credit_mark)
        self.add_widget(self.credit)
        self._confirm = None
        self.bind(pos=self._layout, size=self._layout)

    def _layout(self, *_):
        col_w = self.width * 0.55
        y = self.top - HEADER - 6
        for name in self.ROWS:
            label, value = self.rows[name]
            y -= 14
            label.pos, label.size = (self.x + PAD, y), (col_w - PAD, 14)
            y -= 22
            value.pos, value.size = (self.x + PAD, y), (col_w - PAD, 22)
            y -= 6
        bx, bw = self.x + col_w + 8, self.width - col_w - 8 - PAD
        for i, button in enumerate(self.buttons):
            button.pos, button.size = (bx, self.top - HEADER - 48 - i * 48), (bw, 40)
        self.credit_mark.pos = (bx + 2, self.y + 11)
        self.credit.pos, self.credit.size = (bx + 24, self.y + 8), (bw - 24, 22)

    def on_open(self):
        cfg = load_config()
        self.rows['Name'][1].text = cfg.get('device_name') or 'TouchStream'
        url, _ = hub.credentials()
        self.rows['Hub'][1].text = url.split('://', 1)[-1] if url else 'Not paired'
        self.hub_button.text = 'Hub' if url else 'Connect to hub'
        self.rows['Location'][1].text = cfg.get('location') or 'Not set'
        self.rows['Version'][1].text = VERSION
        self.bars_button.text = 'Test pattern: on' if cfg.get('test_pattern') else 'Test pattern: off'
        self.rows['Network'][1].text = 'Checking…'
        self.rows['IP address'][1].text = ''
        in_background(network_summary, self._show_network)

    def _show_network(self, net):
        self.rows['Network'][1].text = net['name'] if net['kind'] != 'wifi' else f"Wi-Fi · {net['name']}"
        self.rows['IP address'][1].text = net['ip'] or 'None'

    def _toggle_bars(self):
        # Timestamped so the hub and the spoke agree on the latest change
        cfg = load_config()
        cfg.update(test_pattern=not cfg.get('test_pattern'), test_pattern_at=time.time())
        save_config(cfg)
        self.on_open()

    def _reboot(self):
        # Two taps: the first arms it for 3 seconds
        if self._confirm:
            os.system('systemctl reboot')
            return
        self.reboot_button.text = 'Tap to confirm'
        self._confirm = Clock.schedule_once(self._disarm, 3)

    def _disarm(self, *_):
        self._confirm = None
        self.reboot_button.text = 'Reboot'


class _NetworkRow(ButtonBehavior, BoxLayout):
    """[signal] SSID ................ TAG [lock]"""

    def __init__(self, net, on_choose, **kwargs):
        super().__init__(size_hint_y=None, height=46, padding=(PAD, 0), spacing=12, **kwargs)
        self.net = net
        self._bg = paint(self, (0, 0, 0, 0))
        centered = {'center_y': 0.5}
        tag = 'Connected' if net.in_use else 'Saved' if net.saved else ''
        self.add_widget(SignalBars(strength=net.signal, pos_hint=centered))
        self.add_widget(text(net.ssid, size=15))
        if tag:
            self.add_widget(eyebrow(tag, color=theme.EMBER if net.in_use else theme.SMOKE, size=9,
                                    halign='right', size_hint_x=None, width=72))
        self.add_widget(LockIcon(pos_hint=centered, opacity=1 if net.secured else 0))
        with self.canvas.after:
            Color(*theme.LINE_SOFT)
            self._rule = Rectangle()
        self.bind(pos=self._place_rule, size=self._place_rule)
        self.bind(on_release=lambda *_: on_choose(net))

    def _place_rule(self, *_):
        self._rule.pos, self._rule.size = (self.x + PAD, self.y), (self.width - 2 * PAD, 1)

    def on_state(self, _, state):
        self._bg.rgba = theme.LINE_SOFT if state == 'down' else (0, 0, 0, 0)


class WifiPanel(Panel):
    def __init__(self, nav, **kwargs):
        super().__init__(nav, 'Wi-Fi', **kwargs)
        self.refresh = IconButton('refresh', on_press=self.scan)
        self.status = eyebrow('', size=9, size_hint=(None, None))
        self.list = GridLayout(cols=1, size_hint_y=None)
        self.list.bind(minimum_height=self.list.setter('height'))
        self.scroll = ScrollView(size_hint=(None, None), do_scroll_x=False, bar_width=3,
                                 bar_color=theme.FAINT, bar_inactive_color=theme.LINE)
        self.scroll.add_widget(self.list)
        self.message = text('', size=14, color=theme.SMOKE, halign='center', size_hint=(None, None))
        for w in (self.refresh, self.status, self.scroll, self.message):
            self.add_widget(w)
        self.bind(pos=self._layout, size=self._layout)

    def _layout(self, *_):
        top = self.top - HEADER
        self.refresh.pos = (self.right - 46, top + 2)
        self.status.pos, self.status.size = (self.x + PAD, top - 18), (self.width - 2 * PAD, 14)
        self.scroll.pos, self.scroll.size = (self.x, self.y), (self.width, top - 24 - self.y)
        self.message.pos, self.message.size = (self.x, self.center_y - 20), (self.width, 30)

    def on_open(self):
        self.scan()

    def scan(self):
        self.list.clear_widgets()
        self.message.text = 'Scanning…'
        self.status.text = ''
        in_background(lambda: (wifi.scan(), wifi.current()), self._show)

    def _show(self, result):
        networks, connected = result
        self.status.text = f'CONNECTED · {connected}' if connected else 'NOT CONNECTED'
        self.status.color = theme.EMBER if connected else theme.SMOKE
        self.message.text = '' if networks else 'No networks found'
        for net in networks:
            self.list.add_widget(_NetworkRow(net, self._choose))
        self.scroll.scroll_y = 1

    def _choose(self, net):
        if net.in_use:
            return
        if net.saved or not net.secured:
            self.message.text = f'Connecting to {net.ssid}…'
            self.list.clear_widgets()
            in_background(lambda: wifi.connect(net.ssid), self._connected)
        else:
            self.nav.push(PasswordPanel(self.nav, net, on_done=self.scan))

    def _connected(self, result):
        ok, message = result
        self.scan()
        if not ok:
            self.message.text = message


class EntryPanel(Panel):
    """Type one value with the on-screen keyboard, then submit it.

    submit(value) runs on a background thread and returns (ok, message);
    done(value) runs on the UI thread after a success.
    """

    def __init__(self, nav, title, placeholder, hint, submit, done, enter_label='Connect',
                 secret=False, layout='lower', min_length=1, too_short='', max_length=63,
                 initial='', **kwargs):
        super().__init__(nav, title, **kwargs)
        self.placeholder, self.hint = placeholder, hint
        self.submit, self.done = submit, done
        self.secret, self.visible = secret, not secret
        self.min_length, self.too_short, self.max_length = min_length, too_short, max_length
        self.value = initial
        self.busy = False

        self.field = FloatLayout(size_hint=(None, None))
        self._field_bg = paint(self.field, theme.CHARCOAL, radius=10)
        self.shown = text('', font=theme.MONO, size=16, size_hint=(None, None))
        self.field.add_widget(self.shown)
        self.show = None
        if secret:
            self.show = CwButton('Show', kind='ghost', font_size=12, on_press=self._toggle_visible,
                                 size_hint=(None, None))
            self.field.add_widget(self.show)
        self.message = text(hint, size=12, color=theme.SMOKE, size_hint=(None, None))
        self.keyboard = Keyboard(enter_label=enter_label, size_hint=(None, None))
        self.keyboard.layout = layout
        self.keyboard.bind(on_key=lambda _, key: self._key(key))
        for w in (self.field, self.message, self.keyboard):
            self.add_widget(w)
        self.bind(pos=self._layout, size=self._layout)
        self._render()

    def _layout(self, *_):
        top = self.top - HEADER
        self.field.pos, self.field.size = (self.x + PAD, top - 40), (self.width - 2 * PAD, 40)
        reserve = 84 if self.show else 24
        self.shown.pos, self.shown.size = (self.field.x + 12, self.field.y), (self.field.width - reserve, 40)
        if self.show:
            self.show.pos, self.show.size = (self.field.right - 68, self.field.y + 4), (64, 32)
        self.message.pos, self.message.size = (self.x + PAD, top - 64), (self.width - 2 * PAD, 20)
        self.keyboard.pos, self.keyboard.size = (self.x + 6, self.y + 6), (self.width - 12, top - 72 - self.y)

    def _render(self):
        if not self.value:
            self.shown.text, self.shown.color = self.placeholder, theme.FAINT
        else:
            shown = self.value if self.visible else '•' * len(self.value)
            # Keep the end of a long value in view
            self.shown.text, self.shown.color = shown[-26:], theme.ASH

    def _toggle_visible(self):
        self.visible = not self.visible
        self.show.text = 'Hide' if self.visible else 'Show'
        self._render()

    def say(self, message, color=theme.SMOKE):
        self.message.text, self.message.color = message, color

    def _key(self, key):
        if self.busy:
            return
        if key == 'backspace':
            self.value = self.value[:-1]
        elif key == 'enter':
            self._submit()
            return
        elif len(self.value) < self.max_length:
            self.value += key
        self._render()

    def _submit(self):
        if len(self.value) < self.min_length:
            self.say(self.too_short or self.hint, theme.BAD)
            return
        self.busy = True
        self.say('Connecting…', theme.EMBER)
        value = self.value
        in_background(lambda: self.submit(value), lambda result: self._finished(value, result))

    def _finished(self, value, result):
        ok, message = result
        self.busy = False
        if ok:
            self.done(value)
        else:
            self.say(message, theme.BAD)


def PasswordPanel(nav, net, on_done):
    def done(_):
        nav.pop()
        on_done()
    return EntryPanel(nav, net.ssid, 'Password', 'Enter the network password',
                      submit=lambda pw: wifi.connect(net.ssid, pw), done=done, secret=True,
                      min_length=8, too_short='Wi-Fi passwords are at least 8 characters')


class HubPanel(Panel):
    """Pair with a hub (address, then the code from its dashboard), or show the current one."""

    def __init__(self, nav, **kwargs):
        super().__init__(nav, 'Hub', **kwargs)
        self.info = text('', size=14, halign='left', valign='top', size_hint=(None, None))
        self.info.shorten = False
        self.action = CwButton('', size_hint=(None, None), on_press=self._act)
        self.add_widget(self.info)
        self.add_widget(self.action)
        self._confirm = None
        self.bind(pos=self._layout, size=self._layout)

    def _layout(self, *_):
        top = self.top - HEADER
        self.info.pos, self.info.size = (self.x + PAD, top - 150), (self.width - 2 * PAD, 140)
        self.action.pos, self.action.size = (self.x + PAD, self.y + PAD), (self.width - 2 * PAD, 48)

    def on_open(self):
        url, _ = hub.credentials()
        if url:
            self.info.text = (f"Paired with\n[b]{url.split('://', 1)[-1]}[/b]\n\n"
                              "Go live, preview and settings are controlled from the hub's dashboard.")
            self.action.text = 'Disconnect from hub'
        else:
            self.info.text = ("Pair this spoke with your TouchStream Hub.\n\n"
                              "In the hub's dashboard choose [b]Add spoke[/b]: it shows the address "
                              "to type here and a 6-digit code.")
            self.action.text = 'Connect to hub'
        self.info.markup = True

    def _act(self):
        if hub.credentials()[0]:
            # Two taps: the first arms it for 3 seconds
            if self._confirm:
                hub.unpair()
                self.on_open()
                return
            self.action.text = 'Tap again to disconnect'
            self._confirm = Clock.schedule_once(lambda dt: self._disarm(), 3)
            return
        self.nav.push(EntryPanel(
            self.nav, 'Hub address', 'hub.example.com', 'Type the address shown in the hub',
            submit=lambda address: (True, ''), done=self._ask_code, enter_label='Next',
            initial=load_config().get('last_hub_address') or ''))

    def _disarm(self):
        self._confirm = None
        self.on_open()

    def _ask_code(self, address):
        cfg = load_config()
        cfg['last_hub_address'] = address
        save_config(cfg)

        def done(_):
            self.nav.pop()   # code entry
            self.nav.pop()   # address entry
            self.on_open()
        self.nav.push(EntryPanel(self.nav, 'Pairing code', '000000', f'The 6-digit code from {address}',
                                 submit=lambda code: hub.pair(address, code), done=done,
                                 layout='sym1', min_length=6, max_length=6))


# ---- Recordings on this spoke ----
def _when(session):
    t = time.localtime(session['start'])
    return time.strftime('%a %b ', t) + str(t.tm_mday) + time.strftime(' · %I:%M %p', t).replace(' 0', ' ')


def _length(session):
    minutes = round(session['duration'] / 60)
    return f'{minutes // 60} h {minutes % 60:02d} min' if minutes >= 60 else f'{minutes} min'


def _size(n):
    return f'{n / 1e9:.1f} GB' if n >= 1e9 else f'{n / 1e6:.0f} MB'


class _RecordingRow(ButtonBehavior, BoxLayout):
    def __init__(self, session, on_choose, **kwargs):
        super().__init__(size_hint_y=None, height=50, padding=(PAD, 0), spacing=10, **kwargs)
        self._bg = paint(self, (0, 0, 0, 0))
        info = BoxLayout(orientation='vertical', padding=(0, 6))
        info.add_widget(text(_when(session), size=15))
        info.add_widget(text(f"{_length(session)} · {_size(session['size'])}", size=12, color=theme.SMOKE))
        self.add_widget(info)
        tag, color = (('Recording', theme.EMBER) if session['recording'] else ('On hub', theme.OK) if session['on_hub']
                      else ('Only here', theme.SMOKE))
        self.add_widget(eyebrow(tag, color=color, size=9, halign='right', size_hint_x=None, width=90))
        with self.canvas.after:
            Color(*theme.LINE_SOFT)
            self._rule = Rectangle()
        self.bind(pos=self._place_rule, size=self._place_rule)
        self.bind(on_release=lambda *_: on_choose(session))

    def _place_rule(self, *_):
        self._rule.pos, self._rule.size = (self.x + PAD, self.y), (self.width - 2 * PAD, 1)

    def on_state(self, _, state):
        self._bg.rgba = theme.LINE_SOFT if state == 'down' else (0, 0, 0, 0)


class RecordingsPanel(Panel):
    """What this spoke has recorded. The oldest are deleted automatically when the card fills."""

    def __init__(self, nav, **kwargs):
        super().__init__(nav, 'Recordings', **kwargs)
        self.free = eyebrow('', size=9, halign='right', size_hint=(None, None))
        self.list = GridLayout(cols=1, size_hint_y=None)
        self.list.bind(minimum_height=self.list.setter('height'))
        self.scroll = ScrollView(size_hint=(None, None), do_scroll_x=False, bar_width=3,
                                 bar_color=theme.FAINT, bar_inactive_color=theme.LINE)
        self.scroll.add_widget(self.list)
        self.message = text('', size=13, color=theme.SMOKE, halign='center', size_hint=(None, None))
        for w in (self.free, self.scroll, self.message):
            self.add_widget(w)
        self.bind(pos=self._layout, size=self._layout)

    def _layout(self, *_):
        top = self.top - HEADER
        self.free.pos, self.free.size = (self.right - 200 - PAD, top), (200, HEADER)
        self.scroll.pos, self.scroll.size = (self.x, self.y), (self.width, top - self.y)
        self.message.pos, self.message.size = (self.x + PAD, self.center_y - 30), (self.width - 2 * PAD, 60)
        self.message.text_size = self.message.size

    def on_open(self):
        self.list.clear_widgets()
        found = recorder.sessions()
        self.free.text = f'{_size(recorder.free_bytes())} free'.upper()
        self.message.text = '' if found else ('No recordings yet. While live, the spoke keeps its own copy here; '
                                              'the oldest are removed automatically when the card fills.')
        for session in found:
            self.list.add_widget(_RecordingRow(session, lambda s: self.nav.push(RecordingPanel(self.nav, s))))


def _qr_texture(url):
    """Dark-on-white QR code (scans far more reliably than an inverted one), pixel-crisp."""
    import qrcode
    from kivy.graphics.texture import Texture
    img = qrcode.make(url, border=2, box_size=4).get_image().convert('RGB')
    tex = Texture.create(size=img.size, colorfmt='rgb')
    tex.blit_buffer(img.tobytes(), colorfmt='rgb', bufferfmt='ubyte')
    tex.flip_vertical()
    tex.mag_filter = 'nearest'
    return tex


class RecordingPanel(Panel):
    """One recording: a QR code to download it to a phone, and delete."""

    def __init__(self, nav, session, **kwargs):
        super().__init__(nav, _when(session), **kwargs)
        from kivy.uix.image import Image
        self.session = session
        # The QR code appears once the phone-friendly MP4 is made (seconds; about a minute per hour)
        self.url = f'http://{device_ip()}:6077/download/{recorder.share(session["name"])}'
        self.qr_box = FloatLayout(size_hint=(None, None), opacity=0.12)   # a faint placeholder until ready
        paint(self.qr_box, (1, 1, 1, 1), radius=10)
        self.qr = Image(fit_mode='contain', size_hint=(None, None), opacity=0)
        self.qr_box.add_widget(self.qr)
        self.title2 = text('Preparing for your phone…', bold=True, size=16, size_hint=(None, None))
        self.info = text('The code will appear here in a moment.', size=12, color=theme.SMOKE, valign='top',
                         size_hint=(None, None))
        self.info.shorten = False
        self.meta = text(f"{_length(session)} · {_size(session['size'])}", font=theme.MONO, size=12,
                         size_hint=(None, None))
        self.delete = CwButton('Delete', kind='ghost', on_press=self._delete, size_hint=(None, None),
                               disabled=session['recording'])
        for w in (self.qr_box, self.title2, self.info, self.meta, self.delete):
            self.add_widget(w)
        self._confirm = None
        self.bind(pos=self._layout, size=self._layout)
        Clock.schedule_interval(self._check_ready, 0.5)

    def _check_ready(self, _dt):
        if not self.parent:
            return False   # left this screen
        state, error = recorder.share_state(self.session['name'])
        if state == 'preparing':
            return True
        if state == 'ready':
            self.qr.texture = _qr_texture(self.url)
            self.qr.opacity = self.qr_box.opacity = 1
            self.title2.text = 'Scan to download'
            self.info.text = ('Point your phone\'s camera at the code. The phone must be on the same network as '
                              'this spoke. The link works for 15 minutes.')
        else:
            self.title2.text = 'Can\'t download'
            self.info.text = error or 'Something went wrong preparing this recording.'
        return False

    def _layout(self, *_):
        top = self.top - HEADER
        side = min(top - self.y - 16, 236)
        self.qr_box.pos, self.qr_box.size = (self.x + PAD, top - side - 4), (side, side)
        self.qr.pos, self.qr.size = (self.qr_box.x + 6, self.qr_box.y + 6), (side - 12, side - 12)
        rx, rw = self.x + PAD + side + 16, self.width - side - 3 * PAD
        self.title2.pos, self.title2.size = (rx, top - 30), (rw, 24)
        self.info.pos, self.info.size = (rx, top - 128), (rw, 92)
        self.info.text_size = self.info.size
        self.meta.pos, self.meta.size = (rx, top - 156), (rw, 20)
        self.delete.pos, self.delete.size = (rx, self.y + PAD + 8), (rw, 40)

    def _delete(self):
        # Two taps: the first arms it for 3 seconds
        if self._confirm:
            recorder.delete_session(self.session['name'])
            self.nav.pop()
            self.nav.stack[-1].on_open()
            return
        self.delete.text = 'Tap again to delete'
        self._confirm = Clock.schedule_once(self._disarm, 3)

    def _disarm(self, *_):
        self._confirm = None
        self.delete.text = 'Delete'
