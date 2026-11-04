"""TouchStream Spoke kiosk app: Kivy UI + GStreamer capture + hub link"""
import os
import threading
import time

os.environ.setdefault('KIVY_NO_ARGS', '1')
os.environ.setdefault('KIVY_NO_FILELOG', '1')  # logs go to the journal

import kivy
kivy.require('2.1.0')
from kivy.config import Config
# Take touch only from the compositor (calibrated by libinput). Kivy's default
# probesysfs provider would also read the raw touchscreen and double every tap.
for _option in Config.options('input'):
    if Config.get('input', _option) != 'mouse':
        Config.remove_option('input', _option)

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics.texture import Texture
from kivy.uix.floatlayout import FloatLayout

from . import VERSION, api, edid, fallback, media, recorder
from .config import ensure_config, load_config, save_config
from .hub import HubLink, credentials
from .system import CpuUsage, cpu_temp, network_summary, set_display_power, speed_test
from .ui import theme
from .ui.panels import MenuPanel
from .ui.preview import PreviewView

DEBUG = os.environ.get('TOUCHSTREAM_DEBUG') == '1'  # logs taps; never on in production

SCREEN_SLEEP_TIMEOUT = 3 * 3600  # 3 hours in seconds
PANEL_TIMEOUT = 120              # menus close themselves after 2 idle minutes
SIGNAL_PROBE_INTERVAL = 3        # seconds between HDMI checks while there's no picture
SOURCE_SWITCH_INTERVAL = 3       # minimum seconds between capture source changes
HDMI_RETRY_AFTER = 30            # back off when HDMI is detected but gives no frames
UPLINK_SETTLE = 2                # seconds a new capture pipeline runs before the hub link connects


class TouchStreamRoot(FloatLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tex = Texture.create(size=(media.PREVIEW_WIDTH, media.PREVIEW_HEIGHT), colorfmt='rgb')
        self.tex.flip_vertical()
        self.preview = PreviewView(self.tex, on_tap=lambda: self.push(MenuPanel(self)))
        self.add_widget(self.preview)
        self.stack = []

        self.frames_since_last_check = 0
        self.last_fps_time = time.time()
        self.fps = 0
        self.has_signal = False          # an HDMI source is connected and sending
        self.signal_size = None          # its (width, height, fps) from the last probe
        self._probe_result = None
        self._last_probe = 0
        self._probing = False
        self._last_switch = 0
        self._hdmi_failed_until = 0
        self.cpu = CpuUsage()
        self.cpu_percent = None
        self.speed = None            # last internet speed test, run when the hub asks
        self.network = network_summary()
        self.last_activity_time = time.time()
        self.screen_asleep = False

        # What the hub asked for, and what we're doing about it
        self.mode = 'off'
        self.encode = None        # None, or {'bitrate_kbps', 'record_dir'} for the capture pipeline
        self.record_dir = None    # current local recording session
        self.uplink = media.Uplink()
        self.direct = fallback.DirectStream()   # straight to YouTube when the hub is unreachable
        self._direct_manual = False
        self.uploader = recorder.Uploader()
        self.hub = HubLink(self.hub_status, lambda d: Clock.schedule_once(lambda dt: self.apply_desired(d)))

        self.capture = None
        self.audio = None
        self._pending_frame = None
        self._frame_lock = threading.Lock()
        self._pending_audio_levels = None

        if media.GST_OK:
            try:
                self.capture = media.CapturePipeline(self.on_gst_frame)
                self.capture.start(source='hdmi')
            except Exception as e:
                print("Failed to start capture:", e)
            # Audio monitor (level metering only, no playback)
            try:
                self.audio = media.AudioMonitor(self.on_audio_level)
                self.audio.start()
            except Exception as e:
                print("Failed to start AudioMonitor:", e)

        threading.Thread(target=self._poll_network, daemon=True).start()
        recorder.finish_stale_sessions()
        self.hub.start()
        self.uploader.start()
        Clock.schedule_interval(self.update_display, 1.0 / 30.0)
        Clock.schedule_interval(self.heartbeat, 1.0)
        self.heartbeat(0)

    # ---- Panel navigation ----
    def push(self, panel):
        self.stack.append(panel)
        self.add_widget(panel)
        panel.on_open()

    def pop(self):
        if self.stack:
            self.remove_widget(self.stack.pop())

    def pop_all(self):
        while self.stack:
            self.pop()

    # ---- Media callbacks (GStreamer threads) ----
    def on_gst_frame(self, data, w, h):
        with self._frame_lock:
            self._pending_frame = data
        self.frames_since_last_check += 1

    def on_audio_level(self, left, right):
        self._pending_audio_levels = (left, right)

    def update_display(self, dt):
        """Main thread: upload the newest frame and audio levels"""
        with self._frame_lock:
            frame, self._pending_frame = self._pending_frame, None
        if frame is not None and not self.stack:
            try:
                self.tex.blit_buffer(frame, colorfmt='rgb', bufferfmt='ubyte')
                self.preview.video.canvas.ask_update()
            except Exception as e:
                print(f"Texture update failed: {e}")
        levels, self._pending_audio_levels = self._pending_audio_levels, None
        if levels is not None:
            self.preview.meter.set_levels(*levels)

    # ---- Hub ----
    def hub_status(self):
        """Reported to the hub with every heartbeat (runs on the heartbeat thread)."""
        net = self.network
        cfg = load_config()
        return {
            'version': VERSION, 'signal': self.has_signal,
            'signal_label': f'{self.signal_size[1]}p{self.signal_size[2]:.2f}'.rstrip('0').rstrip('.')
                            if self.has_signal and self.signal_size else None,
            'signal_fps': round(self.signal_size[2], 3) if self.has_signal and self.signal_size else None,
            'framerate': cfg.get('framerate', edid.DEFAULT_RATE),
            'source': self.capture.source if self.capture and self.capture.running else None,
            'test_pattern': bool(cfg.get('test_pattern')), 'test_pattern_at': cfg.get('test_pattern_at', 0),
            'temp': cpu_temp(), 'cpu': self.cpu_percent, 'mode': self.mode,
            'network': f"Wi-Fi · {net['name']}" if net['kind'] == 'wifi' else net['name'],
            'uplink': self.uplink.state(), 'uplink_error': self.uplink.error,
            'recording': bool(self.record_dir), 'uploading': self.uploader.status,
            'direct': self.direct.active, 'direct_state': self.direct.state(),
            'storage': recorder.storage(), 'speed': self.speed,
        }

    def apply_desired(self, desired):
        """Main thread: make the spoke match what the hub asked for (None = unpaired)."""
        desired = desired or {'mode': 'off', 'stream': None, 'record_local': False, 'commands': []}
        cfg = load_config()
        if desired.get('name') and (desired['name'], desired.get('location', '')) != (cfg['device_name'], cfg['location']):
            cfg.update(device_name=desired['name'], location=desired.get('location', ''))
            save_config(cfg)
        # Test pattern can be switched from the hub or the spoke: the latest change wins
        rate = str(desired.get('framerate') or cfg.get('framerate') or edid.DEFAULT_RATE)
        if rate in edid.RATES and rate != cfg.get('framerate'):
            # Ask sources for the new rate; HDMI drops and comes back at it, and the
            # source switching below picks it up. Generated sources restart at the new rate.
            cfg['framerate'] = rate
            save_config(cfg)
            print(f"Frame rate set to 1080p{rate}")
            threading.Thread(target=edid.apply, args=(rate,), daemon=True).start()
            if self.capture and self.capture.running and self.capture.source != 'hdmi':
                self._restart_capture(self.capture.source)
        if desired.get('test_pattern_at', 0) > cfg.get('test_pattern_at', 0):
            cfg.update(test_pattern=bool(desired['test_pattern']), test_pattern_at=desired['test_pattern_at'])
            save_config(cfg)

        if desired.get('schedule') is not None:
            fallback.save(desired)
        if self.direct.active:
            print("Hub is back; it takes over from direct streaming")
            self.direct.disconnect()

        self.mode = desired['mode']
        stream = desired.get('stream') if self.mode != 'off' else None
        self._set_encode(stream['bitrate_kbps'] if stream else None,
                         record=bool(desired.get('record_local')) and self.mode == 'live')

        target = ({k: stream[k] for k in ('host', 'port', 'streamid', 'latency_ms')} if stream else None)
        if target != self.uplink.target:
            if target:
                self.uplink.connect(target, delay=UPLINK_SETTLE)
            else:
                self.uplink.disconnect()

        for command in desired.get('commands', []):
            print(f"Hub command: {command}")
            if command == 'reboot':
                os.system('systemctl reboot')
            elif command == 'update':
                threading.Thread(target=api.update_and_restart, daemon=True).start()
            elif command == 'speedtest' and not (self.speed or {}).get('running'):
                threading.Thread(target=self._speed_test, daemon=True).start()

    def _speed_test(self):
        """Measure this spoke's internet speed for the hub (heartbeat thread reads self.speed)."""
        last = self.speed or {}
        self.speed = {**last, 'running': True}
        try:
            self.speed = speed_test()
        except Exception as e:
            self.speed = {**last, 'running': False, 'error': f'Speed test failed: {e}'}
        print(f"Speed test: {self.speed}")

    def _set_encode(self, bitrate_kbps, record):
        """Encode at this bitrate (None = preview only), recording locally if asked."""
        if record and not self.record_dir:
            self.record_dir = recorder.new_session()
            print(f"Recording locally to {self.record_dir}")
        encode = {'bitrate_kbps': bitrate_kbps, 'record_dir': self.record_dir if record else None} if bitrate_kbps else None
        if encode != self.encode:
            self.encode = encode
            if self.capture and self.capture.running:
                self._restart_capture(self.capture.source)
        if not record and self.record_dir:
            # The pipeline restart above closed the last segment
            recorder.finish_session(self.record_dir)
            print(f"Local recording finished: {self.record_dir}")
            self.record_dir = None

    def _run_fallback(self, now):
        """Hub unreachable: run this spoke's own copy of its schedule, straight to YouTube."""
        if not credentials()[0] or now - self.hub.last_ok < fallback.HUB_TIMEOUT:
            return
        cache = fallback.load()
        keys = cache.get('direct_destinations') or []
        event = fallback.due_event(cache.get('schedule'), now)
        if event:
            targets = event['targets'] + keys
        elif self.mode == 'live' and (not self.direct.active or self._direct_manual):
            targets = keys        # was live when the hub went away: keep the stream keys going
        else:
            targets = []
        if targets and targets != self.direct.targets:
            print(f"Hub unreachable: streaming directly ({event['title'] if event else 'continuing live'})")
            self._direct_manual = event is None
            self.mode = 'live'
            self.uplink.disconnect()
            self._set_encode(cache.get('bitrate_kbps') or 8000, record=bool(cache.get('record_local_pref')))
            self.direct.connect(targets, delay=UPLINK_SETTLE)
        elif not targets and self.direct.active:
            print("Scheduled broadcast over; stopping the direct stream")
            self.direct.disconnect()
            self.mode = 'off'
            self._set_encode(None, record=False)

    # ---- Status ----
    def _poll_network(self):
        while True:
            time.sleep(5)
            self.network = network_summary()

    def _status(self):
        if self.direct.active:
            return 'Live · direct' + (' · Rec' if self.record_dir else ''), theme.EMBER
        if not credentials()[0]:
            return 'Not paired', theme.SMOKE
        if not self.hub.connected:
            return 'Hub offline', theme.BAD
        if self.mode == 'live':
            label = 'Live' if self.uplink.state() == 'sending' else 'Live · reconnecting'
            return label + (' · Rec' if self.record_dir else ''), theme.EMBER
        if self.mode == 'preview':
            return 'Preview', theme.ASH
        return 'Standby', theme.OK

    def heartbeat(self, dt):
        now = time.time()
        elapsed = now - self.last_fps_time
        if elapsed >= 1.0:
            self.fps = self.frames_since_last_check / elapsed
            self.frames_since_last_check = 0
            self.last_fps_time = now
        self.preview.set_signal(self.fps >= 1)
        self._update_signal(now)
        self._choose_source(now)
        self._run_fallback(now)
        self.uplink.check()
        self.direct.check()

        self.preview.set_status(*self._status())
        net = self.network
        if net['kind'] == 'ethernet':
            self.preview.network.text = f"ETH  {net['ip'] or ''}"
        elif net['kind'] == 'wifi':
            self.preview.network.text = f"WI-FI  {net['name']}"
        else:
            self.preview.network.text = 'OFFLINE'
        self.preview.network.color = theme.SMOKE if net['kind'] else theme.BAD
        self.preview.device_name.text = load_config().get('device_name') or 'TouchStream'

        temp, self.cpu_percent = cpu_temp(), self.cpu.sample()
        self.preview.metrics.text = '  ·  '.join(filter(None, [
            f'{temp:.0f}°C' if temp is not None else None,
            f'{self.cpu_percent:.0f}%' if self.cpu_percent is not None else None,
            f'{self.fps:.0f} FPS' if self.has_signal else None,
        ]))

        if self.stack and now - self.last_activity_time > PANEL_TIMEOUT:
            self.pop_all()
        self._check_screen_sleep()

    # ---- Capture source: HDMI, the "Waiting for source" slate, or test bars ----
    def _update_signal(self, now):
        capture = self.capture
        hdmi_running = capture and capture.running and capture.source == 'hdmi'
        if hdmi_running and now - capture.started_at > SIGNAL_PROBE_INTERVAL:
            # Frames are the most reliable signal check while HDMI capture runs
            if self.fps < 1:
                if self.has_signal:
                    print("HDMI signal lost")
                elif self._probe_result:
                    # A source is detected but capture gives no frames (e.g. an unsupported
                    # resolution): back off instead of flapping between sources
                    print(f"HDMI detected but no frames; retrying in {HDMI_RETRY_AFTER}s")
                    self._hdmi_failed_until = now + HDMI_RETRY_AFTER
                self._probe_result = None   # needs a fresh probe before trying HDMI again
            self.has_signal = self.fps >= 1
        elif not hdmi_running:
            self.has_signal = bool(self._probe_result) and now >= self._hdmi_failed_until
        if not (hdmi_running and self.fps >= 1):
            self._probe_signal(now)

    def _probe_signal(self, now):
        if self._probing or now - self._last_probe < SIGNAL_PROBE_INTERVAL:
            return
        self._last_probe = now
        self._probing = True

        def probe():
            try:
                self._probe_result = media.probe_signal()
                if self._probe_result:
                    self.signal_size = self._probe_result
            finally:
                self._probing = False
        threading.Thread(target=probe, daemon=True).start()

    def _choose_source(self, now):
        if not self.capture:
            return
        if load_config().get('test_pattern'):
            target = 'bars'
        elif self.has_signal:
            target = 'hdmi'
        elif self.mode != 'off':
            target = 'slate'          # keep the stream up with the holding screen
        else:
            target = None             # idle without a source: the screen shows its own placeholder
        current = self.capture.source if self.capture.running else None
        if target == current or now - self._last_switch < SOURCE_SWITCH_INTERVAL:
            return
        self._last_switch = now
        print(f"Switching capture source: {current} -> {target}")
        if target is None:
            self.capture.stop()
            return
        if target == 'hdmi':
            media.lock_signal()
        self._restart_capture(target)

    def _restart_capture(self, source):
        if source == 'hdmi' and self.signal_size:
            rate = media.rate_fraction(self.signal_size[2])      # exactly what the source sends
        else:
            rate = (int(load_config().get('framerate') or edid.DEFAULT_RATE), 1)
        self.capture.restart(self.encode, source, rate)
        # Audio levels only mean something from a real source; the slate and bars are silent
        Clock.schedule_once(lambda _: setattr(self.preview.meter, 'opacity', 1 if source == 'hdmi' else 0))
        if self.encode and self.uplink.target:
            # A new pipeline is a new MPEG-TS stream (new track IDs): reconnect so the
            # hub reads it from the start instead of discarding it
            self.uplink.connect(self.uplink.target, delay=UPLINK_SETTLE)
        if self.encode and self.direct.active:
            self.direct.connect(self.direct.targets, delay=UPLINK_SETTLE)

    # ---- Screen sleep ----
    def on_touch_down(self, touch):
        if DEBUG:
            panel = type(self.stack[-1]).__name__ if self.stack else 'PreviewView'
            print(f"tap {touch.device} at {touch.x:.0f},{self.height - touch.y:.0f} on {panel}")
        self._record_activity()
        if self.screen_asleep:
            self._wake_screen()
            return True
        return super().on_touch_down(touch)

    def _record_activity(self):
        self.last_activity_time = time.time()

    def _check_screen_sleep(self):
        if self.screen_asleep:
            return
        if self.mode != 'off':
            self._record_activity()
            return
        if time.time() - self.last_activity_time >= SCREEN_SLEEP_TIMEOUT:
            self.screen_asleep = True
            print("Screen sleeping due to inactivity")
            set_display_power(False)

    def _wake_screen(self):
        self.screen_asleep = False
        self._record_activity()
        print("Screen waking up")
        set_display_power(True)


class TouchStreamApp(App):
    title = 'TouchStream'

    def build(self):
        theme.register_fonts()
        Window.borderless = True
        Window.fullscreen = 'auto'
        Window.show_cursor = False
        Window.clearcolor = theme.INK

        # Run GLib main loop in thread for GStreamer bus messages
        from gi.repository import GLib
        threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
        return TouchStreamRoot()

    def on_stop(self):
        root = self.root
        for part in (root.capture, root.audio, root.uplink, root.direct):
            if part:
                part.stop()
        if root.record_dir:
            recorder.finish_session(root.record_dir)


def main():
    ensure_config()
    api.start_discovery_server()
    api.start_beacon()
    if media.GST_OK:
        media.Gst.init(None)
    TouchStreamApp().run()
