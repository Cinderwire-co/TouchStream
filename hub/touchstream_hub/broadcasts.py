"""Scheduled broadcasts: create them on YouTube, keep them in sync, run them on time

An event is one spoke going live at a time, on zero or more connected YouTube
channels. For each channel the hub creates (once per spoke) a reusable YouTube
stream, stored as a destination, and a broadcast bound to it with auto start/stop,
so YouTube starts when video arrives and ends when it stops.

Channel destinations only receive video while an event targeting them is live;
stream-key destinations follow their on/off switch whenever the spoke is live.
"""
import threading
import time
from datetime import datetime

from . import settings
from .youtube import YouTubeError

LEAD_SECONDS = 10           # go live this early so YouTube has video at the start time
TICK_SECONDS = 5
THUMBNAIL_TYPES = {'image/jpeg': 'jpg', 'image/png': 'png'}
THUMBNAIL_MAX = 2 * 1024 * 1024   # YouTube's limit


class Broadcasts:
    def __init__(self, db, youtube):
        self.db = db
        self.youtube = youtube
        self.lock = threading.Lock()
        # a channel's brand thumbnail (media.py channel_thumbnail): for broadcasts that have none of their own
        self.thumbnail_for = lambda channel_row_id: None

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as e:
                print(f"Scheduler error: {e}")
            time.sleep(TICK_SECONDS)

    # ---- Queries ----
    def event(self, event_id):
        return self.db.one('SELECT * FROM events WHERE id = ?', event_id)

    def targets(self, event_id):
        return self.db.all('SELECT t.*, c.title AS channel_title, c.thumbnail AS channel_thumbnail '
                           'FROM event_targets t JOIN youtube_channels c ON c.id = t.channel_id '
                           'WHERE t.event_id = ? ORDER BY t.id', event_id)

    def live_event(self, spoke_id):
        return self.db.one("SELECT * FROM events WHERE spoke_id = ? AND state = 'live' ORDER BY start_at",
                           spoke_id)

    def streaming_destination_ids(self, spoke_id):
        """Channel destinations that should receive video right now (those of the live event)."""
        live = self.live_event(spoke_id)
        if not live:
            return set()
        return {t['destination_id'] for t in self.targets(live['id']) if t['destination_id']}

    def thumbnail_path(self, event):
        return settings.DATA_DIR / 'thumbnails' / event['thumbnail'] if event and event['thumbnail'] else None

    # ---- Changes ----
    def create(self, fields, channel_ids):
        event_id = self.db.run(
            'INSERT INTO events (spoke_id, title, description, start_at, end_at, privacy, latency, '
            'made_for_kids, auto_start, series_id, occurrence, detached, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            fields['spoke_id'], fields['title'], fields['description'], fields['start_at'], fields['end_at'],
            fields['privacy'], fields['latency'], int(fields['made_for_kids']), int(fields['auto_start']),
            fields.get('series_id'), fields.get('occurrence'), int(fields.get('detached', 0)), time.time())
        for channel_id in channel_ids:
            self.db.run('INSERT INTO event_targets (event_id, channel_id) VALUES (?, ?)', event_id, channel_id)
        self.sync(event_id)
        return event_id

    def update(self, event_id, fields, channel_ids=None):
        for field in ('title', 'description', 'start_at', 'end_at', 'privacy', 'latency',
                      'made_for_kids', 'auto_start'):
            if field in fields:
                self.db.run(f'UPDATE events SET {field} = ? WHERE id = ?', fields[field], event_id)
        if channel_ids is not None:
            current = {t['channel_id']: t for t in self.targets(event_id)}
            upcoming = (self.event(event_id) or {}).get('state') == 'scheduled'
            for channel_id, target in current.items():
                if channel_id not in channel_ids:
                    # Only an upcoming broadcast leaves YouTube: once it has aired, it's the channel's video, which stays
                    if upcoming:
                        self._delete_broadcast(target)
                    self.db.run('DELETE FROM event_targets WHERE id = ?', target['id'])
            for channel_id in channel_ids:
                if channel_id not in current:
                    self.db.run('INSERT INTO event_targets (event_id, channel_id) VALUES (?, ?)',
                                event_id, channel_id)
        self.sync(event_id, update_existing=True)

    def move(self, event_id, spoke):
        """An upcoming broadcast to another spoke: each YouTube broadcast is bound to that spoke's stream
        on its channel, so its watch link stays the same."""
        self.db.run('UPDATE events SET spoke_id = ? WHERE id = ?', spoke['id'], event_id)
        for target in self.targets(event_id):
            def work(channel, target=target):
                dest = self._destination_for(spoke, channel)
                if target['broadcast_id']:
                    self.youtube.bind_broadcast(channel, target['broadcast_id'], dest['yt_stream_id'])
                self.db.run('UPDATE event_targets SET destination_id = ? WHERE id = ?', dest['id'], target['id'])
            self._try(target, work)
        self.sync(event_id)

    def set_thumbnail(self, event_id, image, content_type):
        event = self.event(event_id)
        folder = settings.DATA_DIR / 'thumbnails'
        folder.mkdir(exist_ok=True)
        name = f'event-{event_id}-{int(time.time())}.{THUMBNAIL_TYPES[content_type]}'
        (folder / name).write_bytes(image)
        if event['thumbnail']:
            (folder / event['thumbnail']).unlink(missing_ok=True)
        self.db.run('UPDATE events SET thumbnail = ? WHERE id = ?', name, event_id)
        for target in self.targets(event_id):
            if target['broadcast_id']:
                self._try(target, lambda c: self.youtube.set_thumbnail(c, target['broadcast_id'], image, content_type))

    def delete(self, event_id):
        event = self.event(event_id)
        if not event:
            return
        if event['state'] == 'live':
            self.end(event_id)
        # Only an upcoming broadcast is removed from YouTube; after it has aired,
        # deleting it there would delete the channel's video
        if event['state'] == 'scheduled':
            for target in self.targets(event_id):
                self._delete_broadcast(target)
        path = self.thumbnail_path(event)
        if path:
            path.unlink(missing_ok=True)
        self.db.run('DELETE FROM events WHERE id = ?', event_id)

    def go_live(self, event_id):
        event = self.event(event_id)
        print(f"Broadcast '{event['title']}' starting on spoke {event['spoke_id']}")
        # A scheduled broadcast takes over from a manual go-live on the same spoke
        for adhoc in self.db.all("SELECT id FROM events WHERE spoke_id = ? AND state = 'live' AND adhoc = 1",
                                 event['spoke_id']):
            self._mark_ended(adhoc['id'])
        self.db.run("UPDATE events SET state = 'live', started_at = COALESCE(started_at, ?) WHERE id = ?",
                    time.time(), event_id)
        self.db.run("UPDATE spokes SET mode = 'live' WHERE id = ?", event['spoke_id'])

    def start_adhoc(self, spoke):
        """Going live by hand still makes a broadcast, so its recordings have a home."""
        now = time.time()
        t = datetime.fromtimestamp(now)   # (no %-d / %-I: they don't exist on Windows)
        title = f"{spoke['name']} live · {t:%b} {t.day}, {t.hour % 12 or 12}:{t:%M} {t:%p}"
        return self.db.run(
            "INSERT INTO events (spoke_id, title, start_at, auto_start, adhoc, state, started_at, created_at) "
            "VALUES (?, ?, ?, 0, 1, 'live', ?, ?)", spoke['id'], title, now, now, now)

    def end(self, event_id):
        event = self.event(event_id)
        print(f"Broadcast '{event['title']}' ending")
        self._mark_ended(event_id)
        # Stopping the video lets YouTube end the broadcast (auto stop)
        self.db.run("UPDATE spokes SET mode = 'off' WHERE id = ? AND mode = 'live'", event['spoke_id'])

    def end_spoke(self, spoke_id):
        """The spoke stopped being live (from the dashboard): close its live broadcasts."""
        for event in self.db.all("SELECT id FROM events WHERE spoke_id = ? AND state = 'live'", spoke_id):
            self._mark_ended(event['id'])

    def _mark_ended(self, event_id):
        self.db.run("UPDATE events SET state = 'ended', ended_at = COALESCE(ended_at, ?) WHERE id = ?",
                    time.time(), event_id)

    # ---- YouTube sync ----
    def sync(self, event_id, update_existing=False):
        """Create missing YouTube streams/broadcasts for an event; push edits to existing ones."""
        event = self.event(event_id)
        spoke = self.db.spoke(event['spoke_id'])
        image = None
        path = self.thumbnail_path(event)
        if path and path.exists():
            image = (path.read_bytes(), 'image/png' if path.suffix == '.png' else 'image/jpeg')
        for target in self.targets(event_id):
            def work(channel, target=target):
                dest_id = target['destination_id'] or self._destination_for(spoke, channel)['id']
                if not target['broadcast_id']:
                    dest = self.db.one('SELECT * FROM destinations WHERE id = ?', dest_id)
                    broadcast_id = self.youtube.create_broadcast(channel, event, dest['yt_stream_id'])
                    self.db.run('UPDATE event_targets SET destination_id = ?, broadcast_id = ? WHERE id = ?',
                                dest_id, broadcast_id, target['id'])
                    picture = image or self.thumbnail_for(channel['id'])
                    if picture:
                        self.youtube.set_thumbnail(channel, broadcast_id, *picture)
                elif update_existing:
                    self._push_edit(channel, target['broadcast_id'], event)
            self._try(target, work)

    def _push_edit(self, channel, broadcast_id, event):
        """An edit to YouTube. An upcoming broadcast takes it whole (times included) as a broadcast update; one that's
        live, over, or past its start time can't (YouTube wants a start time in the future there), so its title,
        description, privacy and made-for-kids change on its video instead, and its times stay as they were."""
        if event['state'] == 'scheduled' and event['start_at'] > time.time() + 120:
            self.youtube.update_broadcast(channel, broadcast_id, event)
        else:
            self.youtube.update_video(channel, broadcast_id, event)

    def _destination_for(self, spoke, channel):
        """The spoke's reusable YouTube stream on this channel, created the first time."""
        dest = self.db.one('SELECT * FROM destinations WHERE spoke_id = ? AND channel_id = ?',
                           spoke['id'], channel['id'])
        if dest:
            return dest
        stream_id, url, key = self.youtube.create_stream(channel, f"TouchStream · {spoke['name']}")
        dest_id = self.db.run(
            'INSERT INTO destinations (spoke_id, platform, name, url, stream_key, enabled, transcode_kbps, '
            "channel_id, yt_stream_id) VALUES (?, 'youtube', ?, ?, ?, 1, 0, ?, ?)",
            spoke['id'], channel['title'], url, key, channel['id'], stream_id)
        return self.db.one('SELECT * FROM destinations WHERE id = ?', dest_id)

    def _delete_broadcast(self, target):
        if target['broadcast_id']:
            self._try(target, lambda c: self.youtube.delete_broadcast(c, target['broadcast_id']))

    def _try(self, target, work):
        """Run a YouTube call for one target, recording any error on it instead of raising."""
        channel = self.youtube.channel(target['channel_id'])
        try:
            work(channel)
            self.db.run('UPDATE event_targets SET error = NULL WHERE id = ?', target['id'])
        except (YouTubeError, KeyError) as e:
            print(f"YouTube error for channel {channel['title'] if channel else target['channel_id']}: {e}")
            self.db.run('UPDATE event_targets SET error = ? WHERE id = ?', str(e), target['id'])

    # ---- The clock ----
    def tick(self):
        now = time.time()
        for event in self.db.all("SELECT * FROM events WHERE state = 'scheduled' AND auto_start = 1 "
                                 "AND start_at - ? <= ?", LEAD_SECONDS, now):
            spoke = self.db.spoke(event['spoke_id'])
            if event['end_at'] and event['end_at'] <= now:
                self.db.run("UPDATE events SET state = 'ended' WHERE id = ?", event['id'])   # missed entirely
            elif spoke and not spoke['removed_at']:   # (a removed spoke's broadcast waits to be moved)
                self.go_live(event['id'])
        for event in self.db.all("SELECT * FROM events WHERE state = 'live'"):
            spoke = self.db.spoke(event['spoke_id'])
            if event['end_at'] and now >= event['end_at']:
                self.end(event['id'])
            elif spoke and spoke['mode'] != 'live' and now - event['start_at'] > LEAD_SECONDS * 2:
                # Stopped some other way (e.g. the spoke was removed)
                self._mark_ended(event['id'])
