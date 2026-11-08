"""Views: how each broadcast, edited video and Short TouchStream put on YouTube is doing

Every video the hub made (a broadcast's YouTube video on each channel, and the edits and Shorts published from it)
has its view, like and comment counts read from YouTube (videos.list: one quota unit for up to 50 videos), and,
while it's live, how many are watching (the hub keeps the most it saw: the peak). Fresh ones are read more often:
every minute while live, every 15 minutes for a week, every 6 hours for three months, then once a day. It only
reads videos the hub made, with the same YouTube permission it schedules with.
"""
import json
import threading
import time
from collections import defaultdict

from .youtube import YouTubeError

LOOP_EVERY = 60
BATCH = 50   # videos.list takes up to 50 ids


def due(row, live, now):
    """Whether a video's numbers are due for reading again."""
    if row is None:
        return True
    age = now - row['updated_at']
    if live:
        return age >= 55
    made = row['made_at'] or row['updated_at']
    if now - made < 7 * 86400:
        return age >= 15 * 60
    if now - made < 90 * 86400:
        return age >= 6 * 3600
    return age >= 86400


class Stats:
    def __init__(self, db, youtube):
        self.db = db
        self.youtube = youtube
        self.lock = threading.Lock()

    def videos(self):
        """Every video the hub made: [{video_id, channel_id, event_id, kind, made_at, live}]."""
        out = []
        for t in self.db.all("SELECT t.broadcast_id, t.channel_id, t.event_id, e.state, e.start_at FROM event_targets t "
                             "JOIN events e ON e.id = t.event_id WHERE t.broadcast_id IS NOT NULL "
                             "AND e.state IN ('live', 'ended')"):
            out.append({'video_id': t['broadcast_id'], 'channel_id': t['channel_id'], 'event_id': t['event_id'],
                        'kind': 'broadcast', 'made_at': t['start_at'], 'live': t['state'] == 'live'})
        for table, kind in (('edits', 'edit'), ('shorts', 'short')):
            for row in self.db.all(f"SELECT event_id, published FROM {table} WHERE published IS NOT NULL"):
                for p in json.loads(row['published'] or '[]'):
                    if p.get('video_id'):
                        out.append({'video_id': p['video_id'], 'channel_id': p.get('channel_id'),
                                    'event_id': row['event_id'], 'kind': kind, 'made_at': p.get('at'), 'live': False})
        return out

    def refresh(self, force=False):
        """Read the numbers of the videos that are due (all of them, with force)."""
        now = time.time()
        known = {r['video_id']: r for r in self.db.all('SELECT * FROM video_stats')}
        by_channel = defaultdict(list)
        for v in self.videos():
            if force or due(known.get(v['video_id']), v['live'], now):
                by_channel[v['channel_id']].append(v)
        read = 0
        for channel_id, videos in by_channel.items():
            channel = self.youtube.channel(channel_id) if channel_id else None
            if not channel:
                continue
            for i in range(0, len(videos), BATCH):
                batch = {v['video_id']: v for v in videos[i:i + BATCH]}
                try:
                    data = self.youtube.video_stats(channel, list(batch))
                except YouTubeError as e:
                    print(f"Reading views from YouTube ({channel['title']}): {e}")
                    break
                for item in data:
                    self._save(item, batch.get(item.get('id')), known.get(item.get('id')), now)
                    read += 1
        return read

    def _save(self, item, video, before, now):
        if not video:
            return
        s = item.get('statistics') or {}
        live = item.get('liveStreamingDetails') or {}
        watching = int(live['concurrentViewers']) if live.get('concurrentViewers') and not live.get('actualEndTime') else None
        peak = max(watching or 0, (before or {}).get('peak_viewers') or 0) or None
        self.db.run('INSERT INTO video_stats (video_id, channel_id, event_id, kind, views, likes, comments, watching, '
                    'peak_viewers, made_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                    'ON CONFLICT(video_id) DO UPDATE SET views = excluded.views, likes = excluded.likes, '
                    'comments = excluded.comments, watching = excluded.watching, peak_viewers = excluded.peak_viewers, '
                    'updated_at = excluded.updated_at',
                    video['video_id'], video['channel_id'], video['event_id'], video['kind'],
                    _int(s.get('viewCount')), _int(s.get('likeCount')), _int(s.get('commentCount')), watching, peak,
                    video['made_at'], now)

    def for_video(self, video_id):
        row = self.db.one('SELECT views, likes, comments, watching, peak_viewers, updated_at FROM video_stats '
                          'WHERE video_id = ?', video_id)
        return dict(row) if row else None

    def for_event(self, event_id):
        """A broadcast's numbers: its own videos' views, its edits' and its Shorts', all of them together,
        and while it's live how many are watching."""
        rows = self.db.all('SELECT kind, views, likes, comments, watching, peak_viewers FROM video_stats WHERE event_id = ?',
                           event_id)
        if not rows:
            return None
        sum_of = lambda kind, field: sum(r[field] or 0 for r in rows if r['kind'] == kind)
        watching = [r['watching'] for r in rows if r['kind'] == 'broadcast' and r['watching'] is not None]
        return {'views': sum(r['views'] or 0 for r in rows), 'live_views': sum_of('broadcast', 'views'),
                'edit_views': sum_of('edit', 'views'), 'shorts_views': sum_of('short', 'views'),
                'likes': sum(r['likes'] or 0 for r in rows), 'comments': sum(r['comments'] or 0 for r in rows),
                'watching': sum(watching) if watching else None,
                'peak_viewers': sum(r['peak_viewers'] or 0 for r in rows if r['kind'] == 'broadcast') or None}

    def watching_on(self, spoke_id):
        """How many are watching what a spoke has live right now (for its screen), or None."""
        row = self.db.one("SELECT SUM(v.watching) AS n FROM video_stats v JOIN events e ON e.id = v.event_id "
                          "WHERE e.spoke_id = ? AND e.state = 'live' AND v.kind = 'broadcast'", spoke_id)
        return row['n'] if row and row['n'] is not None else None

    def summary(self, days=30):
        """Views of everything made in the last `days` days, for the home page."""
        row = self.db.one('SELECT SUM(views) AS views, COUNT(*) AS videos FROM video_stats WHERE made_at > ?',
                          time.time() - days * 86400)
        return {'days': days, 'views': row['views'] or 0, 'videos': row['videos'] or 0}

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        time.sleep(20)
        while True:
            try:
                with self.lock:
                    self.refresh()
            except Exception as e:  # never let the loop die
                print(f'Reading views: {e}')
            time.sleep(LOOP_EVERY)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
