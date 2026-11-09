"""Recurring broadcasts: "every Sunday at 10 am", one real broadcast per date

A series is a rule ("Sundays", "1st and 3rd Wednesday", "weekdays, September to
May") plus everything a broadcast has (title, description, channels,
thumbnail...). Each date's broadcast is created, on YouTube too, once it is less
than AHEAD_DAYS away: YouTube and the spokes only ever see ordinary broadcasts,
and nothing is made on YouTube months in advance. Dates further out are listed
as planned (see planned()).

Every broadcast of a series remembers its date. Changing one broadcast on its own
detaches it: it keeps its own title, time and so on. Changing the series updates
every upcoming broadcast that isn't detached. Deleting one date skips it.

Times follow the series' time zone, so 10 am stays 10 am across daylight saving.
"""
import json
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import settings
from .broadcasts import THUMBNAIL_TYPES

AHEAD_DAYS = 8          # create each date's broadcast this far ahead (spokes keep 7 days)
PLAN_DAYS = 56          # list dates this far ahead as planned
PLAN_MAX = 8            # at most this many planned dates per series
PLAN_LOOK = 400         # how far to look for a series' next date
CHECK_SECONDS = 60
WEEKS = ('every', 'other')   # or a list of the weeks of the month: 1-4, and -1 for the last
FIELDS = ('title', 'add_date', 'description', 'privacy', 'latency', 'made_for_kids', 'auto_start',
          'repeat', 'first_date', 'start_minutes', 'length_minutes', 'until_date', 'timezone', 'edit_profile_id')


def zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None   # unknown: the hub computer's own time zone


def rule_of(series):
    """{'days': weekdays 0-6 (Monday first, at least one), 'weeks': 'every' | 'other' | [1-4, -1],
    'months': 1-12 (none: every month)}"""
    try:
        return json.loads(series['repeat'])
    except ValueError:   # an early test series: weekly on its first date's weekday
        return {'days': [date.fromisoformat(series['first_date']).weekday()], 'weeks': 'every', 'months': []}


def matches(series, d):
    """Whether the series broadcasts on date d (a datetime.date)."""
    first = date.fromisoformat(series['first_date'])
    if d < first or (series['until_date'] and d > date.fromisoformat(series['until_date'])):
        return False
    rule = rule_of(series)
    if d.weekday() not in rule['days'] or (rule['months'] and d.month not in rule['months']):
        return False
    weeks = rule['weeks']
    if weeks == 'other':
        return (d - first).days // 7 % 2 == 0     # weeks counted from the first date
    if isinstance(weeks, list):
        last = (d + timedelta(days=7)).month != d.month
        return (d.day - 1) // 7 + 1 in weeks or (-1 in weeks and last)
    return True


def dates(series, start, end):
    """Dates (ISO strings) the series broadcasts on between two dates, inclusive, minus skipped ones."""
    skips = set(json.loads(series['skips']))
    d, out = max(start, date.fromisoformat(series['first_date'])), []
    while d <= end:
        if matches(series, d) and d.isoformat() not in skips:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def times(series, day):
    """(start, end) epoch seconds of the series' broadcast on a date (end None without a set length)."""
    y, m, d = map(int, day.split('-'))
    minutes = series['start_minutes']
    start = datetime(y, m, d, minutes // 60, minutes % 60, tzinfo=zone(series['timezone'])).timestamp()
    return start, start + series['length_minutes'] * 60 if series['length_minutes'] else None


def title(series, day):
    """The broadcast's title: the series title, with the date added if the series asks for it."""
    if not series['add_date']:
        return series['title']
    d = date.fromisoformat(day)
    return f"{series['title']} · {d:%B} {d.day}, {d.year}"[:100]


def today(series):
    return datetime.now(zone(series['timezone'])).date()


class Series:
    def __init__(self, db, broadcasts):
        self.db = db
        self.broadcasts = broadcasts

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                for s in self.all():
                    self.create_due(s['id'])
            except Exception as e:
                print(f"Series error: {e}")
            time.sleep(CHECK_SECONDS)

    # ---- Queries ----
    def get(self, series_id):
        return self.db.one('SELECT * FROM series WHERE id = ?', series_id)

    def all(self):
        return self.db.all('SELECT * FROM series ORDER BY id')

    def view(self, s):
        return {**{k: s[k] for k in ('id', 'spoke_id', *FIELDS)}, 'repeat': rule_of(s), 'add_date': bool(s['add_date']),
                'made_for_kids': bool(s['made_for_kids']), 'auto_start': bool(s['auto_start']),
                'channel_ids': json.loads(s['channel_ids']), 'skips': json.loads(s['skips']),
                'thumbnail_url': f"/api/series/{s['id']}/thumbnail?v={s['thumbnail']}" if s['thumbnail'] else None}

    def upcoming(self, series_id):
        """Its broadcasts that haven't started yet."""
        return self.db.all("SELECT * FROM events WHERE series_id = ? AND state = 'scheduled' ORDER BY start_at", series_id)

    def planned(self, spoke_id=''):
        """Dates beyond the ones already created, for the agenda: [{series_id, date, start_at, end_at, title, spoke_id}]."""
        out = []
        for s in self.all():
            if spoke_id and s['spoke_id'] != spoke_id:
                continue
            made = {e['occurrence'] for e in self.db.all('SELECT occurrence FROM events WHERE series_id = ?', s['id'])}
            upcoming = self.db.one("SELECT 1 FROM events WHERE series_id = ? AND state = 'scheduled'", s['id'])
            now, shown = time.time(), 0
            # The next PLAN_DAYS, or at least the next date however far off (e.g. a summer-only series)
            for day in dates(s, today(s), today(s) + timedelta(days=PLAN_LOOK)):
                start, end = times(s, day)
                if day in made or start < now:
                    continue
                if start > now + PLAN_DAYS * 86400 and (shown or upcoming):
                    break
                shown += 1
                out.append({'series_id': s['id'], 'date': day, 'start_at': start, 'end_at': end,
                            'title': title(s, day), 'spoke_id': s['spoke_id'], 'series': self.view(s)})
                if sum(1 for p in out if p['series_id'] == s['id']) >= PLAN_MAX:
                    break
        return sorted(out, key=lambda p: p['start_at'])

    # ---- Broadcasts of a series ----
    def event_fields(self, s, day):
        start, end = times(s, day)
        return {'spoke_id': s['spoke_id'], 'title': title(s, day), 'description': s['description'],
                'start_at': start, 'end_at': end, 'privacy': s['privacy'], 'latency': s['latency'],
                'made_for_kids': s['made_for_kids'], 'auto_start': s['auto_start'], 'edit_profile_id': s['edit_profile_id']}

    def create_date(self, series_id, day, fields=None, channel_ids=None):
        """Create the broadcast for one date now; with `fields`, as a broadcast changed on its own."""
        s = self.get(series_id)
        existing = self.db.one('SELECT id FROM events WHERE series_id = ? AND occurrence = ?', series_id, day)
        if existing:
            return existing['id']
        with self.broadcasts.lock:
            event_id = self.broadcasts.create({**self.event_fields(s, day), **(fields or {}),
                                               'series_id': series_id, 'occurrence': day, 'detached': int(bool(fields))},
                                              json.loads(s['channel_ids']) if channel_ids is None else channel_ids)
        if s['thumbnail'] and not fields:
            self._copy_thumbnail(s, event_id)
        return event_id

    def create_due(self, series_id):
        """Create the broadcasts of the dates now within AHEAD_DAYS."""
        s = self.get(series_id)
        if not s:
            return
        now = time.time()
        made = {e['occurrence'] for e in self.db.all('SELECT occurrence FROM events WHERE series_id = ?', series_id)}
        for day in dates(s, today(s) - timedelta(days=1), today(s) + timedelta(days=AHEAD_DAYS)):
            start, _ = times(s, day)
            if day not in made and now < start < now + AHEAD_DAYS * 86400:
                self.create_date(series_id, day)

    def _copy_thumbnail(self, s, event_id):
        path = self.thumbnail_path(s)
        if path and path.exists():
            self.broadcasts.set_thumbnail(event_id, path.read_bytes(), 'image/png' if path.suffix == '.png' else 'image/jpeg')

    def thumbnail_path(self, s):
        return settings.DATA_DIR / 'thumbnails' / s['thumbnail'] if s and s['thumbnail'] else None

    # ---- Changes ----
    def create(self, fields, channel_ids, from_event=None):
        """A new series. `from_event`: an existing broadcast becomes its first date."""
        series_id = self.db.run(
            f"INSERT INTO series (spoke_id, {', '.join(FIELDS)}, channel_ids, created_at) "
            f"VALUES (?, {', '.join('?' for _ in FIELDS)}, ?, ?)",
            fields['spoke_id'], *(fields.get(f) if f == 'edit_profile_id' else fields[f] for f in FIELDS),   # (optional)
            json.dumps(channel_ids), time.time())
        if from_event:
            self.db.run('UPDATE events SET series_id = ?, occurrence = ?, detached = 0 WHERE id = ?',
                        series_id, fields['first_date'], from_event)
            event = self.broadcasts.event(from_event)
            if event['thumbnail']:
                self._set_series_thumbnail(series_id, self.broadcasts.thumbnail_path(event))
            self._apply(self.get(series_id), [event])
        self.create_due(series_id)
        return series_id

    def update(self, series_id, fields, channel_ids=None, attach=None):
        """Change the series and every upcoming broadcast of it not changed on its own
        (and `attach`, a broadcast of it to bring back in line)."""
        for f, value in fields.items():
            if f in FIELDS:
                self.db.run(f'UPDATE series SET {f} = ? WHERE id = ?', value, series_id)
        if channel_ids is not None:
            self.db.run('UPDATE series SET channel_ids = ? WHERE id = ?', json.dumps(channel_ids), series_id)
        if attach:
            self.db.run('UPDATE events SET detached = 0 WHERE id = ? AND series_id = ?', attach, series_id)
        s = self.get(series_id)
        self._apply(s, [e for e in self.upcoming(series_id) if not e['detached']], channel_ids)
        self.create_due(series_id)

    def _apply(self, s, events, channel_ids=None):
        for event in events:
            day = event['occurrence']
            if not matches(s, date.fromisoformat(day)) or day in json.loads(s['skips']):
                with self.broadcasts.lock:     # the new rule doesn't include this date
                    self.broadcasts.delete(event['id'])
                continue
            fields = self.event_fields(s, day)
            fields.pop('spoke_id')
            with self.broadcasts.lock:
                self.broadcasts.update(event['id'], fields, channel_ids)

    def set_thumbnail(self, series_id, image, content_type):
        folder = settings.DATA_DIR / 'thumbnails'
        folder.mkdir(exist_ok=True)
        path = folder / f'series-{series_id}-{int(time.time())}.{THUMBNAIL_TYPES[content_type]}'
        path.write_bytes(image)
        self._set_series_thumbnail(series_id, path)
        for event in self.upcoming(series_id):
            if not event['detached']:
                self.broadcasts.set_thumbnail(event['id'], image, content_type)

    def _set_series_thumbnail(self, series_id, path):
        """Use `path` (in the thumbnails folder) as the series' picture; copied if it belongs to a broadcast."""
        s = self.get(series_id)
        if path.name.startswith('event-'):
            copy = path.with_name(f'series-{series_id}-{int(time.time())}{path.suffix}')
            copy.write_bytes(path.read_bytes())
            path = copy
        old = self.thumbnail_path(s)
        if old and old != path:
            old.unlink(missing_ok=True)
        self.db.run('UPDATE series SET thumbnail = ? WHERE id = ?', path.name, series_id)

    def skip(self, series_id, day):
        s = self.get(series_id)
        skips = sorted(set(json.loads(s['skips'])) | {day})
        self.db.run('UPDATE series SET skips = ? WHERE id = ?', json.dumps(skips), series_id)

    def delete(self, series_id, keep=None):
        """End a series: its upcoming broadcasts are deleted (except `keep`, which stays on its own).
        Broadcasts that already aired stay, as ordinary ones."""
        for event in self.upcoming(series_id):
            if event['id'] != keep:
                with self.broadcasts.lock:
                    self.broadcasts.delete(event['id'])
        path = self.thumbnail_path(self.get(series_id))
        if path:
            path.unlink(missing_ok=True)
        self.db.run('UPDATE events SET series_id = NULL WHERE series_id = ?', series_id)
        self.db.run('DELETE FROM series WHERE id = ?', series_id)
