"""SQLite storage: settings, spokes and their destinations"""
import json
import sqlite3
import threading

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spokes (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,              -- authenticates the spoke's API calls
    stream_key TEXT NOT NULL,         -- authenticates its SRT publish (sent in the clear)
    name TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'off', -- off | preview | live
    bitrate_kbps INTEGER NOT NULL DEFAULT 8000,
    record_local INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_seen REAL,
    status TEXT NOT NULL DEFAULT '{}',
    commands TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS destinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spoke_id TEXT NOT NULL REFERENCES spokes(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    stream_key TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    transcode_kbps INTEGER NOT NULL DEFAULT 0  -- 0 = pass the spoke's stream through untouched
);
CREATE TABLE IF NOT EXISTS youtube_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    thumbnail TEXT NOT NULL DEFAULT '',
    refresh_token TEXT NOT NULL,
    access_token TEXT,
    token_expires REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL
);
-- A scheduled broadcast: one spoke, one or more channels
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spoke_id TEXT NOT NULL REFERENCES spokes(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    start_at REAL NOT NULL,
    end_at REAL,
    privacy TEXT NOT NULL DEFAULT 'public',     -- public | unlisted | private
    latency TEXT NOT NULL DEFAULT 'normal',     -- normal | low | ultraLow
    made_for_kids INTEGER NOT NULL DEFAULT 0,
    auto_start INTEGER NOT NULL DEFAULT 1,      -- the hub puts the spoke live at start_at
    thumbnail TEXT,                             -- file name in the data folder
    state TEXT NOT NULL DEFAULT 'scheduled',    -- scheduled | live | ended
    created_at REAL NOT NULL
);
-- An edited version of a broadcast's recording (rendered on the hub)
CREATE TABLE IF NOT EXISTS edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    source TEXT NOT NULL,                       -- recording it was cut from (relative path)
    spec TEXT NOT NULL,                         -- {in, out, cuts, fade_in, fade_out, dip}
    status TEXT NOT NULL DEFAULT 'queued',      -- queued | rendering | done | failed | publishing
    progress REAL NOT NULL DEFAULT 0,
    duration REAL,
    output TEXT,
    error TEXT,
    published TEXT,                             -- JSON list of YouTube uploads
    created_at REAL NOT NULL
);
-- Speech-to-text of a broadcast's recording (Whisper, on the hub)
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',      -- queued | running | done | failed
    progress REAL NOT NULL DEFAULT 0,
    model TEXT,
    language TEXT,
    segments TEXT,                              -- JSON [{start, end, text, words: [[start, end, word]]}]
    edited INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL
);
-- A vertical short of one moment of a broadcast (shorts.py)
CREATE TABLE IF NOT EXISTS shorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    spec TEXT NOT NULL,                         -- {source, start, end, framing, style, accent, trim_pauses}
    status TEXT NOT NULL DEFAULT 'queued',      -- queued | analyzing | rendering | done | failed | publishing
    progress REAL NOT NULL DEFAULT 0,
    duration REAL,
    output TEXT,
    error TEXT,
    published TEXT,                             -- JSON list of YouTube uploads
    created_at REAL NOT NULL
);
-- Videos, images and sound uploaded to the hub for edits: intros, slides, logos, music (media.py)
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                         -- video | image | audio
    file TEXT NOT NULL,                         -- name in <data>/media
    duration REAL,
    width INTEGER,
    height INTEGER,
    has_audio INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
-- A look for a channel: intro, outro and watermark (media.py)
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    intro INTEGER REFERENCES media(id) ON DELETE SET NULL,
    outro INTEGER REFERENCES media(id) ON DELETE SET NULL,
    intro_seconds INTEGER NOT NULL DEFAULT 5,   -- how long an image intro or outro shows
    outro_seconds INTEGER NOT NULL DEFAULT 5,
    watermark INTEGER REFERENCES media(id) ON DELETE SET NULL,
    watermark_corner TEXT NOT NULL DEFAULT 'bottom_right',
    watermark_size INTEGER NOT NULL DEFAULT 12, -- % of the video's width
    watermark_opacity INTEGER NOT NULL DEFAULT 80,
    created_at REAL NOT NULL
);
-- Files in the recordings folder: a broadcast's filed recordings, edits and shorts
CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                         -- hub | spoke | edit | short
    number INTEGER NOT NULL DEFAULT 1,          -- the 2nd spoke recording, edit 3...
    path TEXT NOT NULL,                         -- absolute
    start REAL,
    end REAL,
    created_at REAL NOT NULL
);
-- A recurring broadcast: a rule, plus what each of its broadcasts gets (see series.py)
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spoke_id TEXT NOT NULL REFERENCES spokes(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    add_date INTEGER NOT NULL DEFAULT 1,        -- put each broadcast's date in its title
    description TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'public',
    latency TEXT NOT NULL DEFAULT 'normal',
    made_for_kids INTEGER NOT NULL DEFAULT 0,
    auto_start INTEGER NOT NULL DEFAULT 1,
    channel_ids TEXT NOT NULL DEFAULT '[]',
    thumbnail TEXT,
    repeat TEXT NOT NULL,                       -- JSON {days, weeks, months}: see series.rule_of
    first_date TEXT NOT NULL,                   -- YYYY-MM-DD: none before it; every other week counts from it
    start_minutes INTEGER NOT NULL,             -- time of day, in the series' time zone
    length_minutes INTEGER,                     -- NULL: no set end
    until_date TEXT,                            -- last date; NULL: no end
    timezone TEXT NOT NULL,
    skips TEXT NOT NULL DEFAULT '[]',           -- dates left out
    created_at REAL NOT NULL
);
-- API tokens for scripts and AI agents (auth.Tokens); only a hash of each is kept
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,                        -- read | control | admin
    hash TEXT NOT NULL UNIQUE,
    hint TEXT NOT NULL,                         -- its last 4 characters, to tell tokens apart
    created_at REAL NOT NULL,
    last_used_at REAL
);
-- Agents asking for an API token, for the dashboard to approve (auth.AccessRequests)
CREATE TABLE IF NOT EXISTS access_requests (
    id TEXT PRIMARY KEY,                        -- long and random: the agent's proof it asked
    name TEXT NOT NULL,
    scope TEXT NOT NULL,                        -- asked for, then what was approved
    code TEXT NOT NULL,                         -- shown by the agent and the dashboard, to match
    ip TEXT,
    status TEXT NOT NULL,                       -- pending | approved | collected | denied | expired
    token_id INTEGER,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
-- How each video the hub put on YouTube is doing (stats.py): a broadcast's, an edit's or a Short's
CREATE TABLE IF NOT EXISTS video_stats (
    video_id TEXT PRIMARY KEY,
    channel_id INTEGER,
    event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                         -- broadcast | edit | short
    views INTEGER, likes INTEGER, comments INTEGER,
    watching INTEGER,                           -- watching now, while it's live
    peak_viewers INTEGER,                       -- the most the hub saw watching it live
    made_at REAL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS event_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    destination_id INTEGER REFERENCES destinations(id) ON DELETE SET NULL,
    broadcast_id TEXT,
    error TEXT
);
"""

# Columns added after the first release: (table, column, definition)
MIGRATIONS = [
    # When the hub made Shorts of this broadcast by itself (app.py make_shorts_by_themselves): once each
    ('transcripts', 'auto_shorts_at', 'REAL'),
    ('spokes', 'test_pattern', 'INTEGER NOT NULL DEFAULT 0'),
    ('spokes', 'test_pattern_at', 'REAL NOT NULL DEFAULT 0'),
    # Set when YouTube created this destination for a connected channel
    ('destinations', 'channel_id', 'INTEGER REFERENCES youtube_channels(id) ON DELETE CASCADE'),
    ('destinations', 'yt_stream_id', 'TEXT'),
    # When a broadcast actually went on and off air
    ('events', 'started_at', 'REAL'),
    ('events', 'ended_at', 'REAL'),
    # Created automatically when a spoke went live without a scheduled broadcast
    ('events', 'adhoc', 'INTEGER NOT NULL DEFAULT 0'),
    # 1080p frame rate the spoke asks its HDMI source for: 24 | 25 | 30 | 50
    ('spokes', 'framerate', "TEXT NOT NULL DEFAULT '30'"),
    ('spokes', 'removed_at', 'REAL'),   # removed from the hub: its broadcasts and recordings stay
    # The broadcast's folder in the recordings folder, once something is filed there
    ('events', 'folder', 'TEXT'),
    # Where the spoke's last heartbeat came from, to tell it the hub's new address if that changes
    ('spokes', 'last_ip', 'TEXT'),
    # The spoke's time zone (an IANA name); '' for where it finds itself to be, on the internet
    ('spokes', 'time_zone', "TEXT NOT NULL DEFAULT ''"),
    # Recurring broadcasts: the series a broadcast belongs to, its date in it, and whether it
    # was changed on its own (then changes to the series leave it alone)
    ('events', 'series_id', 'INTEGER REFERENCES series(id) ON DELETE SET NULL'),
    ('events', 'occurrence', 'TEXT'),
    ('events', 'detached', 'INTEGER NOT NULL DEFAULT 0'),
    # The editor's unsaved work on a broadcast's edit: {source, spec, saved_at}
    ('events', 'edit_draft', 'TEXT'),
    # When old-recordings cleanup (retention.py) deleted its recordings
    ('events', 'recordings_removed_at', 'REAL'),
    # Moments the assistant's model suggested for shorts: {moments: [{start, end, title, why}], model, made_at}
    ('transcripts', 'suggestions', 'TEXT'),
    # The brand the editor picks for this channel's broadcasts
    ('youtube_channels', 'brand_id', 'INTEGER REFERENCES brands(id) ON DELETE SET NULL'),
    # The OAuth client a channel was connected with (its tokens work only with it); NULL: the hub's first one
    ('youtube_channels', 'client_id', 'TEXT'),
    # A brand's default thumbnail: its channels' broadcasts without one of their own get it on YouTube
    ('brands', 'thumbnail', 'INTEGER REFERENCES media(id) ON DELETE SET NULL'),
]


class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            for table, column, definition in MIGRATIONS:
                columns = [row[1] for row in self.conn.execute(f'PRAGMA table_info({table})')]
                if column not in columns:
                    self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    def all(self, sql, *args):
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, args)]

    def one(self, sql, *args):
        rows = self.all(sql, *args)
        return rows[0] if rows else None

    def run(self, sql, *args):
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    # ---- settings ----
    def get_setting(self, key, default=None):
        row = self.one('SELECT value FROM settings WHERE key = ?', key)
        return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        self.run('INSERT INTO settings (key, value) VALUES (?, ?) '
                 'ON CONFLICT(key) DO UPDATE SET value = excluded.value', key, json.dumps(value))

    # ---- spokes ----
    def spoke(self, spoke_id):
        return _decode(self.one('SELECT * FROM spokes WHERE id = ?', spoke_id))

    def spokes(self, removed=False):
        """The hub's spokes; with removed=True, also ones removed from it (whose broadcasts and recordings stay)."""
        return [_decode(s) for s in self.all('SELECT * FROM spokes WHERE ? OR removed_at IS NULL ORDER BY created_at',
                                             int(removed))]

    def destinations(self, spoke_id=None):
        if spoke_id:
            return self.all('SELECT * FROM destinations WHERE spoke_id = ? ORDER BY id', spoke_id)
        return self.all('SELECT * FROM destinations ORDER BY id')


def _decode(spoke):
    if spoke:
        spoke['status'] = json.loads(spoke['status'])
        spoke['commands'] = json.loads(spoke['commands'])
        spoke['record_local'] = bool(spoke['record_local'])
        spoke['test_pattern'] = bool(spoke['test_pattern'])
    return spoke
