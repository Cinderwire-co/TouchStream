"""Direct-to-YouTube fallback: what to do when the hub can't be reached

Every heartbeat the hub sends this spoke its upcoming broadcasts with the YouTube
stream address and key for each, plus its own stream-key destinations. That copy
is kept on disk. If the hub stays unreachable, the spoke runs its own schedule:
it starts a due broadcast (or keeps a live one going) by streaming straight to
YouTube, and stops at the end time. When the hub is back it takes over again;
it uses the same stream key, so YouTube sees a reconnect, not a new stream.
"""
import json
import os
import time
from pathlib import Path

from .media import Gst, UPLINK_PORT, _set_null

CACHE_PATH = Path.home() / 'touchstream-schedule.json'
HUB_TIMEOUT = 30              # seconds without the hub before the spoke acts on its own
LEAD_SECONDS = 10             # start this early, like the hub does
OPEN_ENDED_HOURS = 4          # how long a broadcast without an end time may run


def save(desired):
    """Keep the hub's latest schedule and settings for when it's unreachable."""
    keep = {k: desired.get(k) for k in ('schedule', 'direct_destinations', 'bitrate_kbps', 'record_local_pref')}
    keep['saved_at'] = time.time()
    tmp = str(CACHE_PATH) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(keep, f)
    os.chmod(tmp, 0o600)   # holds stream keys
    os.replace(tmp, CACHE_PATH)


def load():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def due_event(schedule, now=None):
    """The broadcast that should be on air right now, if any."""
    now = now or time.time()
    for event in schedule or []:
        end = event.get('end_at') or event['start_at'] + OPEN_ENDED_HOURS * 3600
        if event.get('auto_start', True) and event['start_at'] - LEAD_SECONDS <= now < end:
            return event
    return None


class DirectStream:
    """Relays the local MPEG-TS stream to one or more RTMP(S) servers (YouTube ingest)."""

    def __init__(self):
        self.pipeline = None
        self.targets = None
        self.error = None
        self.started_at = None
        self.connect_at = 0

    def connect(self, targets, delay=0):
        self._stop_pipeline()
        self.targets = targets
        self.connect_at = time.time() + delay

    def disconnect(self):
        self._stop_pipeline()
        self.targets = None

    def stop(self):
        self.disconnect()

    def _start_pipeline(self):
        sinks = '  '.join(
            f't. ! queue ! rtmp2sink async-connect=true location="{t["url"].rstrip("/")}/{t["key"]}"'
            for t in self.targets)
        desc = (f'udpsrc port={UPLINK_PORT} buffer-size=4194304 '
                'caps="video/mpegts,systemstream=(boolean)true,packetsize=(int)188" ! tsdemux name=d  '
                'd. ! queue ! h264parse ! video/x-h264,stream-format=avc ! fm.  '
                'd. ! queue ! aacparse ! fm.  '
                f'flvmux name=fm streamable=true ! tee name=t  {sinks}')
        self.pipeline = Gst.parse_launch(desc)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', self._on_error)
        self.error = None
        self.started_at = time.time()
        self.pipeline.set_state(Gst.State.PLAYING)
        print(f"Direct to {', '.join(t.get('name') or 'destination' for t in self.targets)} started")

    def _on_error(self, bus, message):
        err, _ = message.parse_error()
        self.error = err.message
        print(f"Direct stream error: {err.message}")

    def _stop_pipeline(self):
        if self.pipeline:
            _set_null(self.pipeline, 'Direct stream')
            self.pipeline = None

    def check(self):
        """Called every second: connect when due, and retry 5 s after an error."""
        if self.pipeline and self.error and time.time() - self.started_at > 5:
            self._stop_pipeline()
        if self.targets and not self.pipeline and time.time() >= self.connect_at:
            self._start_pipeline()

    @property
    def active(self):
        return self.targets is not None

    def state(self):
        if not self.targets:
            return 'off'
        return 'retrying' if self.error or not self.pipeline else 'sending'
