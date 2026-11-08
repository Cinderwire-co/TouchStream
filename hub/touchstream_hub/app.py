"""TouchStream Hub web app: dashboard API, spoke API and the MediaMTX auth hook"""
import asyncio
import hashlib
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import threading
import time
import zoneinfo
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import VERSION, agent_guide, auth, desktop, discovery, platforms, recordings, settings, updates
from .assistant import Assistant, AssistantError
from .broadcasts import THUMBNAIL_MAX, THUMBNAIL_TYPES, Broadcasts
from .db import Database
from .editor import COLOR, PEAKS_PER_SECOND, Editor, filmstrip, normalize, placements, probe_duration, waveform
from .media import Media
from .library import Library, safe_name
from .mediamtx import MediaMTX
from .restream import Restreamer
from .retention import Retention
from .series import WEEKS, Series, zone
from .shorts import AVAILABLE as SHORTS_AVAILABLE, Shorts, sentences
from .speedtest import SpeedTest
from .transcribe import MODELS, Transcriber, remap, to_srt, to_txt, to_vtt
from .youtube import YouTube, YouTubeError
from .stats import Stats
from .updates import UpdateError, Updates

STATIC = Path(__file__).parent / 'static'
ONLINE_FOR = 15            # seconds since a spoke's last heartbeat to count it online
PAIRING_MINUTES = 10
LOOPBACK = ('127.0.0.1', '::1')
PUBLISH_PATH = re.compile(r'^(spoke|preview)/([A-Za-z0-9]+)$')
SCHEDULE_DAYS = 7          # how far ahead spokes keep a copy of their schedule
FRAME_RATES = ('24', '25', '30', '50', '60')   # any spoke's; each says which it can capture (spoke_rates)


class Hub:
    """Everything the routes share, created at startup."""
    db: Database
    sessions: auth.Sessions
    tokens: auth.Tokens
    access: auth.AccessRequests
    mtx: MediaMTX
    restreamer: Restreamer
    youtube: YouTube
    broadcasts: Broadcasts
    series: Series
    speedtest: SpeedTest
    assistant: Assistant
    library: Library
    retention: Retention
    editor: Editor
    media: Media
    transcriber: Transcriber
    shorts: Shorts
    discovery: discovery.Discovery
    updates: Updates
    stats: Stats
    http: httpx.AsyncClient
    pairing: dict         # code -> expiry time
    pair_failures: dict   # client ip -> [failure times]


hub = Hub()


@asynccontextmanager
async def lifespan(app):
    settings.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    hub.db = Database(settings.DB_PATH)
    if settings.ADMIN_PASSWORD:
        stored = hub.db.get_setting('admin_password')
        if not stored or not auth.check_password(settings.ADMIN_PASSWORD, stored):
            hub.db.set_setting('admin_password', auth.hash_password(settings.ADMIN_PASSWORD))
    hub.sessions = auth.Sessions(hub.db)
    hub.tokens = auth.Tokens(hub.db)
    hub.access = auth.AccessRequests(hub.db, hub.tokens)
    hub.mtx = MediaMTX()
    hub.mtx.start()
    hub.youtube = YouTube(hub.db)
    hub.broadcasts = Broadcasts(hub.db, hub.youtube)
    hub.broadcasts.start()
    hub.series = Series(hub.db, hub.broadcasts)
    hub.series.start()
    hub.restreamer = Restreamer(hub.db, hub.mtx, hub.broadcasts)
    hub.restreamer.start()
    hub.library = Library(hub.db)
    hub.library.start()
    hub.retention = Retention(hub.db, hub.library)
    hub.retention.start()
    hub.media = Media(hub.db)
    hub.editor = Editor(hub.db, hub.youtube, hub.library, hub.media)
    hub.editor.start()
    hub.transcriber = Transcriber(hub.db, hub.library)
    hub.transcriber.start()
    hub.shorts = Shorts(hub.db, hub.youtube, hub.library, hub.transcriber)
    hub.shorts.start()
    hub.discovery = discovery.Discovery(hub.db)
    hub.discovery.start()
    hub.speedtest = SpeedTest(hub.db)
    hub.assistant = Assistant(hub.db)
    hub.updates = Updates(hub.db)
    hub.updates.start(busy=busy_with)
    hub.stats = Stats(hub.db, hub.youtube)
    hub.stats.start()
    hub.http = httpx.AsyncClient(timeout=10)
    hub.pairing, hub.pair_failures = {}, {}
    print(f"TouchStream Hub {VERSION} - data in {settings.DATA_DIR}")
    shorts_task = asyncio.create_task(make_shorts_by_themselves())
    async with mcp_server.server.session_manager.run():   # the MCP endpoint, for AI agents
        yield
    shorts_task.cancel()
    hub.restreamer.stop_all()
    hub.mtx.stop()
    await hub.http.aclose()


app = FastAPI(title='TouchStream Hub', version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url='/api/openapi.json')

# ---- The API description (/api/openapi.json), for scripts and AI agents ----
API_ABOUT = """Everything the TouchStream dashboard does, for scripts and AI agents. Send an API token from
Settings > API access as `Authorization: Bearer ts_...`; an agent can ask for one instead (`POST /api/access`, then
someone approves it in the dashboard: see /agent). A 'read' token can GET; 'control' can also schedule,
go live, edit and publish; 'admin' can also change settings.

How things ripple:
- A broadcast owns its recordings, edited video, transcript and thumbnail. Deleting an aired one with
  recordings_too=true deletes those from the hub; YouTube's copy is never deleted.
- Scheduling, changing or deleting an upcoming broadcast changes it on YouTube too.
- A series (recurring broadcast) makes each date's broadcast a week ahead; later dates are planned (GET
  /api/events, 'planned'). PATCH /api/series changes all upcoming dates not changed on their own; PATCH
  /api/events/{id} changes one date and detaches it from the series.
- A broadcast has one edit, always cut from an original recording; rendering again replaces it. Publishing
  uploads a new YouTube video (YouTube can't replace footage) and can make the live video private.
- A broadcast can have any number of shorts: vertical videos of one moment, up to 3 minutes, published to
  YouTube as Shorts.
- Spokes keep a week of their schedule and stream straight to YouTube if the hub is down."""
HIDDEN = re.compile(r'^/$|^/hls/|^/api/(spoke/|internal/|setup|login|logout|tokens|access-requests|password|reveal|recordings-folder'
                    r'|recordings/filmstrip|recordings/waveform|youtube/connect|assistant|shorts/style|titles/preview'
                    r'|events/[^/]+/editor/status|events/[^/]+/framing|events/[^/]+/caption-pages|shorts/caption)')
TAGS = [(r'^/api/events/[^/]+/(editor|edit|edits)|^/api/edits|^/api/(media|brands)', 'Editing'), (r'^/api/events/[^/]+/transcript', 'Transcripts'),
        (r'^/api/events/[^/]+/shorts|^/api/shorts', 'Shorts'),
        (r'^/api/(events|series)', 'Broadcasts'), (r'^/api/(spokes|spoke-defaults|discovered|pairing|destinations)', 'Spokes'),
        (r'^/api/(recordings|retention)', 'Recordings'), (r'^/api/youtube', 'YouTube'), (r'^/api/access', 'Getting access'),
        (r'.', 'Hub')]


def describe_api():
    if not app.openapi_schema:
        schema = get_openapi(title='TouchStream Hub API', version=VERSION, description=API_ABOUT, routes=app.routes)
        schema['paths'] = {path: ops for path, ops in schema['paths'].items() if not HIDDEN.match(path)}
        for path, ops in schema['paths'].items():
            tag = next(name for pattern, name in TAGS if re.match(pattern, path))
            for op in ops.values():
                op['tags'], op['security'] = [tag], [] if tag == 'Getting access' else [{'token': []}]
        schema.setdefault('components', {})['securitySchemes'] = {
            'token': {'type': 'http', 'scheme': 'bearer', 'description': 'An API token from Settings > API access'}}
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = describe_api
def dashboard_version():
    """Changes whenever a dashboard file does, so the page asks for its scripts at new addresses."""
    newest = max(f.stat().st_mtime_ns for f in STATIC.rglob('*') if f.is_file())
    return f'{VERSION}-{newest:x}'[-16:]


@app.get('/static/v/{version}/{path:path}', include_in_schema=False)
def versioned_file(version: str, path: str):
    """A dashboard file at its versioned address (see index): never stale, so cached for good."""
    target = (STATIC / path).resolve()
    if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, headers={'Cache-Control': 'public, max-age=31536000, immutable'})


class DashboardFiles(StaticFiles):
    """The dashboard's files. Browsers check for a newer copy every time (a quick 304 when there isn't
    one), so an updated hub shows its new dashboard on the next reload."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers['Cache-Control'] = 'no-cache'
        return response


app.mount('/static', DashboardFiles(directory=STATIC), name='static')


# ---- Auth helpers ----
# What an API token needs: GETs need 'read', changes 'control', and these 'admin'
ADMIN_ONLY = re.compile(r'^/api/(password|settings|retention$|youtube/(client|connect|channels)|recordings-folder|reveal|spokes$)')


def signed_in(request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    return bool(token and hub.sessions.valid(token))


def admin(request: Request):
    """The dashboard's session cookie, or an API token (Authorization: Bearer ts_...) allowed to do this."""
    if signed_in(request):
        return
    bearer = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    row = hub.tokens.check(bearer) if bearer else None
    if not row:
        raise HTTPException(401, 'Sign in required, or send an API token as "Authorization: Bearer ts_..." '
                                 '(an agent can ask for one: see /agent)')
    need = 'read' if request.method in ('GET', 'HEAD') else 'admin' if ADMIN_ONLY.match(request.url.path) else 'control'
    if auth.SCOPES.index(row['scope']) < auth.SCOPES.index(need):
        raise HTTPException(403, f"This API token is for {row['scope']} only; this needs {need}")


def session_only(request: Request):
    """Only from the dashboard: API tokens can't make or see tokens."""
    if not signed_in(request):
        raise HTTPException(401, 'Sign in to the dashboard to manage API tokens')


def spoke_auth(request: Request):
    spoke = hub.db.spoke(request.headers.get('X-Spoke-Id', ''))
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if not spoke or spoke['removed_at'] or not secrets.compare_digest(token, spoke['token']):
        raise HTTPException(401, 'Unknown spoke')   # (a removed one drops back to unpaired)
    return spoke


def set_session(request, response):
    response.set_cookie(auth.SESSION_COOKIE, hub.sessions.issue(), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite='lax', secure=request.url.scheme == 'https')


def public_host(request):
    """Where spokes should send SRT: the configured address, else the host they reached us at."""
    return (hub.db.get_setting('public_host') or settings.PUBLIC_HOST
            or request.headers.get('host', '').rsplit(':', 1)[0].strip('[]'))


def hub_ip():
    """This computer's LAN address, preferring the one spokes already reach it on."""
    spoke_ips = [s['last_ip'] for s in hub.db.spokes() if s['last_ip'] and _private(s['last_ip'])]
    return discovery.lan_ip(spoke_ips + [s['ip'] for s in hub.discovery.found.values()])


def _private(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def is_local(request):
    """Whether the browser is on the hub's own computer (so it can open Finder/File Explorer)."""
    return desktop.is_local(request.client.host if request.client else None, discovery.own_ips())


def new_spoke(name):
    spoke_id = secrets.token_hex(4)
    spoke = {'id': spoke_id, 'token': secrets.token_urlsafe(32), 'stream_key': secrets.token_urlsafe(16)}
    hub.db.run('INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES (?, ?, ?, ?, ?)',
               spoke_id, spoke['token'], spoke['stream_key'], name[:60] or 'TouchStream', time.time())
    for field, value in (hub.db.get_setting('spoke_defaults') or {}).items():   # settings for all spokes
        hub.db.run(f'UPDATE spokes SET {field} = ? WHERE id = ?', value, spoke_id)
    return spoke


# ---- Pages ----
@app.get('/')
def index():
    # The scripts and styles load from an address with the dashboard's version in it, and the modules they
    # import resolve under it too: after an update the browser fetches them anew, however it caches
    base = f'/static/v/{dashboard_version()}/'
    page = (STATIC / 'index.html').read_text().replace('/static/styles.css', base + 'styles.css').replace(
        '/static/app.js', base + 'app.js')
    return Response(page, media_type='text/html', headers={'Cache-Control': 'no-cache'})


@app.get('/api/state')
def state(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    signed = bool(token and hub.sessions.valid(token))
    return {'version': VERSION, 'setup_needed': not hub.db.get_setting('admin_password'), 'signed_in': signed,
            'local': is_local(request), 'platform': desktop.PLATFORM,
            # The assistant: on with an OpenRouter key; 'asked' once the key was offered at setup (or skipped)
            **({'ai': {'enabled': hub.assistant.enabled(), 'asked': bool(hub.db.get_setting('ai_asked'))},
                'update': update_notice()} if signed else {})}


@app.post('/api/setup')
def setup(request: Request, response: Response, password: str = Body(embed=True)):
    if hub.db.get_setting('admin_password'):
        raise HTTPException(409, 'Already set up')
    if len(password) < 8:
        raise HTTPException(400, 'Use at least 8 characters')
    hub.db.set_setting('admin_password', auth.hash_password(password))
    set_session(request, response)
    return {'ok': True}


@app.post('/api/login')
def login(request: Request, response: Response, password: str = Body(embed=True)):
    stored = hub.db.get_setting('admin_password')
    if not stored or not auth.check_password(password, stored):
        time.sleep(1)  # slow down guessing
        raise HTTPException(401, 'Wrong password')
    set_session(request, response)
    return {'ok': True}


@app.post('/api/logout')
def logout(response: Response):
    response.delete_cookie(auth.SESSION_COOKIE)
    return {'ok': True}


# ---- Dashboard ----
def spoke_view(spoke):
    online = bool(spoke['last_seen'] and time.time() - spoke['last_seen'] < ONLINE_FOR)
    path = f"{'spoke' if spoke['mode'] == 'live' else 'preview'}/{spoke['id']}"
    dests = []
    for d in hub.db.destinations(spoke['id']):
        dests.append({'id': d['id'], 'platform': d['platform'], 'name': d['name'], 'url': d['url'],
                      'enabled': bool(d['enabled']), 'transcode_kbps': d['transcode_kbps'],
                      'key_hint': ('••••' + d['stream_key'][-4:]) if d['stream_key'] else '',
                      'channel_id': d['channel_id'], **hub.restreamer.state(d['id'])})
    return {
        'id': spoke['id'], 'name': spoke['name'], 'location': spoke['location'], 'mode': spoke['mode'],
        'bitrate_kbps': spoke['bitrate_kbps'], 'record_local': spoke['record_local'],
        'test_pattern': spoke['test_pattern'], 'framerate': spoke['framerate'], 'time_zone': spoke['time_zone'],
        'online': online, 'last_seen': spoke['last_seen'], 'status': spoke['status'],
        'receiving': hub.mtx.ready(path) if spoke['mode'] != 'off' else False,
        'preview_path': path, 'destinations': dests,
        'obs_url': f"rtmp://<hub>:{settings.RTMP_PORT}/spoke/{spoke['id']}",
        'update': hub.updates.spoke_view(spoke, online),
    }


@app.get('/api/dashboard', dependencies=[Depends(admin)])
def dashboard():
    return {'spokes': [spoke_view(s) for s in hub.db.spokes()],
            'discovered': hub.discovery.unadopted(), 'discovery': hub.discovery.listening,
            'mediamtx': hub.mtx.available, 'platforms': platforms.PLATFORMS,
            'events': [event_view(e) for e in hub.db.all(
                "SELECT * FROM events WHERE state != 'ended' ORDER BY start_at")],
            'channels': channel_list(), 'youtube': hub.youtube.configured(),
            'hub': {'ip': hub_ip(), 'port': settings.HTTP_PORT, 'docker': settings.IN_DOCKER,
                    'public_host': hub.db.get_setting('public_host') or settings.PUBLIC_HOST}}


@app.get('/api/hub', dependencies=[Depends(admin)])
def hub_info():
    """This hub's address for spokes (top bar)."""
    return {'ip': hub_ip(), 'port': settings.HTTP_PORT, 'docker': settings.IN_DOCKER,
            'public_host': hub.db.get_setting('public_host') or settings.PUBLIC_HOST}


_recordings_size = {'at': 0}


@app.get('/api/overview', dependencies=[Depends(admin)])
def overview():
    """The home page: the hub's storage and internet speed, and the latest broadcasts."""
    root = hub.library.root()
    root.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(root)
    if time.time() - _recordings_size['at'] > 60:
        files = [Path(r['path']) for r in hub.db.all('SELECT path FROM library')]
        _recordings_size.update(at=time.time(), bytes=sum(f.stat().st_size for f in files if f.exists()),
                                broadcasts=hub.db.one('SELECT COUNT(DISTINCT event_id) AS n FROM library')['n'])
    recent = hub.db.all("SELECT * FROM events WHERE state = 'ended' ORDER BY start_at DESC LIMIT 4")
    return {'storage': {'free': disk.free, 'total': disk.total, 'folder': str(root),
                        'recordings': _recordings_size['bytes'], 'broadcasts': _recordings_size['broadcasts']},
            'speed': hub.speedtest.state(), 'views': hub.stats.summary(),
            'recent': [event_view(e, hub.library.event_copies(e)) for e in recent]}


@app.post('/api/speedtest', dependencies=[Depends(admin)])
def run_speedtest():
    hub.speedtest.start()
    return hub.speedtest.state()


@app.post('/api/pairing', dependencies=[Depends(admin)])
def start_pairing():
    now = time.time()
    hub.pairing = {c: exp for c, exp in hub.pairing.items() if exp > now}
    code = f'{secrets.randbelow(1_000_000):06d}'
    hub.pairing[code] = now + PAIRING_MINUTES * 60
    return {'code': code, 'expires_at': hub.pairing[code]}


@app.post('/api/spokes', dependencies=[Depends(admin)])
def make_spoke(name: str = Body(embed=True)):
    """Register a spoke before it's switched on: the installer writes these credentials onto its card, so it's
    paired from its first boot. (If the hub's address changes, it tells the spoke when it next sees it.)"""
    spoke = new_spoke(name.strip() or 'TouchStream')
    return {'spoke_id': spoke['id'], 'token': spoke['token'],
            'hub_url': f'http://{hub_ip() or "localhost"}:{settings.HTTP_PORT}'}


@app.post('/api/discovered/{ip}/adopt', dependencies=[Depends(admin)])
def adopt_discovered(ip: str):
    found = next((s for s in hub.discovery.unadopted() if s['ip'] == ip), None)
    if not found:
        raise HTTPException(404, 'That spoke is no longer on the network')
    spoke = new_spoke(found['name'])
    hub_url = f'http://{discovery.local_address_towards(ip)}:{settings.HTTP_PORT}'
    try:
        discovery.adopt(ip, {'hub_url': hub_url, 'spoke_id': spoke['id'], 'token': spoke['token']})
    except Exception as e:
        hub.db.run('DELETE FROM spokes WHERE id = ?', spoke['id'])
        raise HTTPException(502, f"The spoke didn't respond ({e})")
    hub.discovery.found.pop(ip, None)
    return {'id': spoke['id']}


SPOKE_SHARED = ('framerate', 'bitrate_kbps', 'record_local')   # what "settings for all spokes" sets


def _spoke_fields(changes, fields=('name', 'location', 'time_zone', *SPOKE_SHARED)):
    casts = {'name': str, 'location': str, 'bitrate_kbps': int, 'record_local': bool, 'framerate': str, 'time_zone': str}
    out = {}
    for field in fields:
        if field in changes:
            value = casts[field](changes[field])
            if field == 'bitrate_kbps' and not 1000 <= value <= 25000:
                raise HTTPException(400, 'Bitrate must be 1000-25000 kbps')
            if field == 'framerate' and value not in FRAME_RATES:
                raise HTTPException(400, f"Frame rate must be one of {', '.join(FRAME_RATES)}")
            if field == 'time_zone' and value and value not in zoneinfo.available_timezones():
                raise HTTPException(400, f'{value} isn\'t a time zone (one like America/Chicago, or none: automatic)')
            out[field] = int(value) if isinstance(value, bool) else value
    return out


def spoke_rates(spoke):
    """The frame rates a spoke says it can capture (in its heartbeat); all of them if it hasn't said."""
    rates = (spoke.get('status') or {}).get('framerates')
    return [r for r in FRAME_RATES if r in rates] if isinstance(rates, list) and rates else list(FRAME_RATES)


@app.patch('/api/spokes/{spoke_id}', dependencies=[Depends(admin)])
def update_spoke(spoke_id: str, changes: dict = Body()):
    spoke = hub.db.spoke(spoke_id)
    if not spoke:
        raise HTTPException(404)
    fields = _spoke_fields(changes)
    if fields.get('framerate') and fields['framerate'] not in spoke_rates(spoke):
        raise HTTPException(400, f"This spoke can capture 1080p at {', '.join(spoke_rates(spoke))} fps")
    for field, value in fields.items():
        hub.db.run(f'UPDATE spokes SET {field} = ? WHERE id = ?', value, spoke_id)
    return spoke_view(hub.db.spoke(spoke_id))


@app.get('/api/spoke-defaults', dependencies=[Depends(admin)])
def get_spoke_defaults():
    """Settings for all spokes, and how many spokes have their own value of each."""
    spokes = hub.db.spokes()
    defaults = {'framerate': '30', 'bitrate_kbps': 8000, 'record_local': 1, **(hub.db.get_setting('spoke_defaults') or {})}
    return {**{k: bool(v) if k == 'record_local' else v for k, v in defaults.items()},
            'differ': {k: sum(1 for s in spokes if int(s[k]) != int(defaults[k])) if k != 'framerate'
                       else sum(1 for s in spokes if s[k] != defaults[k]) for k in SPOKE_SHARED},
            'spokes': len(spokes)}


@app.put('/api/spoke-defaults', dependencies=[Depends(admin)])
def set_spoke_defaults(changes: dict = Body()):
    """Set these on every spoke (each can still be changed on its own afterwards) and on new ones."""
    fields = _spoke_fields(changes, SPOKE_SHARED)
    hub.db.set_setting('spoke_defaults', {**(hub.db.get_setting('spoke_defaults') or {}), **fields})
    for field, value in fields.items():
        for spoke in hub.db.spokes():
            # A spoke that can't capture this frame rate keeps its own
            if field != 'framerate' or value in spoke_rates(spoke):
                hub.db.run(f'UPDATE spokes SET {field} = ? WHERE id = ?', value, spoke['id'])
    return get_spoke_defaults()


@app.post('/api/spokes/{spoke_id}/mode', dependencies=[Depends(admin)])
def set_mode(spoke_id: str, mode: str = Body(embed=True)):
    """Body: {"mode": "off" | "preview" | "live"}. Going live without a scheduled broadcast makes one
    (ad hoc) so its recordings have a home; scheduled broadcasts go live by themselves."""
    if mode not in ('off', 'preview', 'live'):
        raise HTTPException(400, 'Mode is off, preview or live')
    spoke = hub.db.spoke(spoke_id)
    if not spoke:
        raise HTTPException(404)
    change_mode(spoke, mode)
    return spoke_view(hub.db.spoke(spoke_id))


def change_mode(spoke, mode):
    with hub.broadcasts.lock:
        if mode == 'live' and not hub.broadcasts.live_event(spoke['id']):
            hub.broadcasts.start_adhoc(spoke)
        elif mode != 'live' and spoke['mode'] == 'live':
            hub.broadcasts.end_spoke(spoke['id'])
        hub.db.run('UPDATE spokes SET mode = ? WHERE id = ?', mode, spoke['id'])


@app.post('/api/spokes/{spoke_id}/command', dependencies=[Depends(admin)])
def send_command(spoke_id: str, command: str = Body(embed=True)):
    spoke = hub.db.spoke(spoke_id)
    if not spoke:
        raise HTTPException(404)
    if command not in ('reboot', 'speedtest'):
        raise HTTPException(400, 'Unknown command')
    hub.db.run('UPDATE spokes SET commands = ? WHERE id = ?',
               json.dumps(spoke['commands'] + [command]), spoke_id)
    return {'ok': True}


@app.delete('/api/spokes/{spoke_id}', dependencies=[Depends(admin)])
def forget_spoke(spoke_id: str):
    """Remove a spoke from the hub. The device is forgotten (it drops back to unpaired on its next heartbeat),
    but its broadcasts, recordings, edits and shorts all stay. Its upcoming broadcasts stay on the schedule,
    marked as needing a spoke, until they're moved to another one."""
    spoke = hub.db.spoke(spoke_id)
    if not spoke or spoke['removed_at']:
        raise HTTPException(404)
    if not spoke['last_seen'] and not hub.db.one(
            'SELECT 1 FROM events WHERE spoke_id = ? UNION SELECT 1 FROM series WHERE spoke_id = ?', spoke_id, spoke_id):
        hub.db.run('DELETE FROM spokes WHERE id = ?', spoke_id)   # never connected, nothing to keep
        return {'ok': True}
    change_mode(spoke, 'off')   # ends a live broadcast
    hub.db.run("UPDATE spokes SET removed_at = ?, mode = 'off', token = ? WHERE id = ?",
               time.time(), secrets.token_urlsafe(24), spoke_id)
    hub.db.run('UPDATE destinations SET enabled = 0 WHERE spoke_id = ? AND channel_id IS NULL', spoke_id)
    return {'ok': True}

@app.post('/api/spokes/{spoke_id}/test_pattern', dependencies=[Depends(admin)])
def set_test_pattern(spoke_id: str, on: bool = Body(embed=True)):
    # Timestamped: the spoke's own menu can switch it too, and the latest change wins
    hub.db.run('UPDATE spokes SET test_pattern = ?, test_pattern_at = ? WHERE id = ?', int(on), time.time(), spoke_id)
    return {'ok': True}


# ---- YouTube ----
def channel_list():
    return [{'id': c['id'], 'channel_id': c['channel_id'], 'title': c['title'], 'thumbnail': channel_picture_url(c),
             'error': c['error']} for c in hub.db.all('SELECT * FROM youtube_channels ORDER BY created_at')]


def channel_picture_url(channel, row_id=None):
    """Where the dashboard gets a channel's picture: from the hub (channel_picture), not from YouTube."""
    return f"/api/youtube/channels/{row_id or channel['id']}/picture" if channel['thumbnail'] else ''


@app.get('/api/youtube/channels/{channel_row}/picture', dependencies=[Depends(admin)])
def channel_picture(channel_row: int):
    """A channel's YouTube picture, kept by the hub. Google's image servers can refuse it to a page on another
    site (the dashboard: a broken image), and this way the dashboard never calls Google. Kept under a name from
    its address, so a new picture is fetched anew."""
    row = hub.db.one('SELECT thumbnail FROM youtube_channels WHERE id = ?', channel_row)
    if not row or not row['thumbnail']:
        raise HTTPException(404)
    path = settings.DATA_DIR / 'channels' / (hashlib.sha256(row['thumbnail'].encode()).hexdigest()[:24] + '.img')
    if not path.exists():
        try:
            resp = httpx.get(row['thumbnail'], timeout=10, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(404, "YouTube didn't give the channel's picture")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    data = path.read_bytes()
    kind = 'image/png' if data[:4] == b'\x89PNG' else 'image/webp' if data[8:12] == b'WEBP' else 'image/jpeg'
    return Response(data, media_type=kind, headers={'Cache-Control': 'private, max-age=86400'})


@app.get('/api/youtube', dependencies=[Depends(admin)])
def youtube_state():
    client_id, _ = hub.youtube.client()
    return {'configured': hub.youtube.configured(), 'from_env': hub.youtube.client_from_env(),
            'client_id': client_id, 'channels': channel_list()}


@app.put('/api/youtube/client', dependencies=[Depends(admin)])
def set_youtube_client(data: dict = Body()):
    client_id, client_secret = str(data.get('client_id', '')).strip(), str(data.get('client_secret', '')).strip()
    if not client_id.endswith('.apps.googleusercontent.com'):
        raise HTTPException(400, 'That doesn\'t look like a Google OAuth client ID')
    stored = hub.db.get_setting('youtube_client') or {}
    # An empty secret in an edit keeps the saved one
    hub.db.set_setting('youtube_client', {'client_id': client_id,
                                          'client_secret': client_secret or stored.get('client_secret', '')})
    return {'ok': True}


@app.post('/api/youtube/connect', dependencies=[Depends(admin)])
def youtube_connect():
    if not hub.youtube.configured():
        raise HTTPException(400, 'Add your Google OAuth client in Settings first')
    try:
        return hub.youtube.start_connect()
    except (YouTubeError, httpx.HTTPError) as e:
        raise HTTPException(502, f'Google: {e}')


@app.get('/api/youtube/connect/{flow_id}', dependencies=[Depends(admin)])
def youtube_connect_poll(flow_id: str):
    try:
        return hub.youtube.poll_connect(flow_id)
    except (YouTubeError, httpx.HTTPError) as e:
        return {'status': 'error', 'error': str(e)}


@app.delete('/api/youtube/channels/{channel_row}', dependencies=[Depends(admin)])
def youtube_disconnect(channel_row: int):
    hub.youtube.disconnect(channel_row)
    return {'ok': True}


# ---- Scheduled broadcasts ----
def event_view(event, copies=None):
    targets = []
    for t in hub.broadcasts.targets(event['id']):
        bid = t['broadcast_id']
        targets.append({'channel_id': t['channel_id'], 'channel': t['channel_title'],
                        'destination_id': t['destination_id'],
                        'channel_thumbnail': channel_picture_url({'thumbnail': t['channel_thumbnail']}, t['channel_id']),
                        'broadcast_id': bid, 'error': t['error'],
                        'watch_url': f'https://youtu.be/{bid}' if bid else None,
                        'studio_url': f'https://studio.youtube.com/video/{bid}/livestreaming' if bid else None,
                        'stats': hub.stats.for_video(bid) if bid else None})
    series = hub.series.get(event['series_id']) if event['series_id'] else None
    spoke = hub.db.spoke(event['spoke_id'])
    # Whether the spoke has it in the schedule it keeps, to go straight to YouTube if the hub is down
    # (None: its spoke doesn't say, or it isn't in the week the spoke keeps)
    kept = (spoke.get('status') or {}).get('schedule_kept') if spoke else None
    on_spoke = event['id'] in kept if isinstance(kept, list) and event['state'] in ('scheduled', 'live') \
        and event['start_at'] < time.time() + SCHEDULE_DAYS * 86400 else None
    return {**event, 'made_for_kids': bool(event['made_for_kids']), 'auto_start': bool(event['auto_start']),
            'on_spoke': on_spoke,
            'spoke_online': bool(spoke and spoke['last_seen'] and time.time() - spoke['last_seen'] < ONLINE_FOR),
            'spoke_name': spoke['name'] if spoke else None, 'spoke_removed': bool(not spoke or spoke['removed_at']),
            'adhoc': bool(event['adhoc']), 'detached': bool(event['detached']),
            'series': hub.series.view(series) if series else None, 'targets': targets, 'recordings': copies or [],
            'edits': [edit_view(hub.editor.edit(e['id'])) for e in
                      hub.db.all('SELECT id FROM edits WHERE event_id = ? ORDER BY id', event['id'])],
            'transcript': hub.db.one('SELECT status, progress, edited, language FROM transcripts WHERE event_id = ?',
                                     event['id']),
            'shorts': hub.db.one('SELECT COUNT(*) AS n FROM shorts WHERE event_id = ?', event['id'])['n'],
            'stats': hub.stats.for_event(event['id']),
            'thumbnail_url': f"/api/events/{event['id']}/thumbnail?v={event['thumbnail']}" if event['thumbnail'] else None}


def _event_fields(data, partial=False, timing=True):
    f = {}
    if 'title' in data or not partial:
        # YouTube rejects < and > in titles and descriptions
        f['title'] = re.sub(r'[<>]', '', str(data.get('title', ''))).strip()[:100]
        if not f['title']:
            raise HTTPException(400, 'Give the broadcast a title')
    if 'description' in data or not partial:
        f['description'] = re.sub(r'[<>]', '', str(data.get('description', '')))[:5000]
    if timing and ('start_at' in data or not partial):
        try:
            f['start_at'] = float(data['start_at'])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, 'Pick a start time')
    if timing and ('end_at' in data or not partial):
        f['end_at'] = float(data['end_at']) if data.get('end_at') else None
        if f['end_at'] and f['end_at'] <= f.get('start_at', 0):
            raise HTTPException(400, 'The end time must be after the start')
    for field, allowed, default in (('privacy', ('public', 'unlisted', 'private'), 'public'),
                                    ('latency', ('normal', 'low', 'ultraLow'), 'normal')):
        if field in data or not partial:
            f[field] = data.get(field, default)
            if f[field] not in allowed:
                raise HTTPException(400, f'{field} must be one of {", ".join(allowed)}')
    for field, default in (('made_for_kids', False), ('auto_start', True)):
        if field in data or not partial:
            f[field] = int(bool(data.get(field, default)))
    return f


def _channel_ids(data):
    ids = [int(i) for i in data.get('channel_ids', [])]
    known = {c['id'] for c in hub.db.all('SELECT id FROM youtube_channels')}
    if not set(ids) <= known:
        raise HTTPException(400, 'Unknown channel')
    return ids


@app.get('/api/events', dependencies=[Depends(admin)])
def list_events(spoke_id: str = '', days: int = 90):
    """Broadcasts (upcoming, live and the last `days` of past ones) with their recordings."""
    since = time.time() - days * 86400
    rows = hub.db.all("SELECT * FROM events WHERE (? = '' OR spoke_id = ?) AND (state != 'ended' OR start_at > ?) "
                      'ORDER BY start_at', spoke_id, spoke_id, since)
    copies, other = {}, []
    for sid in {e['spoke_id'] for e in rows} | ({spoke_id} if spoke_id else {s['id'] for s in hub.db.spokes(removed=True)}):
        grouped, unmatched = hub.library.copies(sid, [e for e in rows if e['spoke_id'] == sid
                                                      and e['state'] != 'scheduled'])
        copies.update(grouped)
        other += [{**c, 'spoke_id': sid} for c in unmatched]
    return {'events': [event_view(e, copies.get(e['id'])) for e in rows],
            'planned': hub.series.planned(spoke_id),
            'other_recordings': sorted(other, key=lambda c: c['start'], reverse=True)}


@app.get('/api/events/{event_id}', dependencies=[Depends(admin)])
def get_event(event_id: int):
    """One broadcast, with its recordings."""
    event = hub.broadcasts.event(event_id)
    if not event:
        raise HTTPException(404, 'That broadcast is gone')
    return event_view(event, _event_copies(event))


@app.post('/api/events', dependencies=[Depends(admin)])
def create_event(data: dict = Body()):
    """Schedule a one-off broadcast. Body: spoke_id, title, description, start_at and end_at (epoch seconds;
    end_at optional), privacy (public | unlisted | private), latency (normal | low | ultraLow), made_for_kids,
    auto_start (go live at start_at), channel_ids (connected YouTube channels; none = record only).
    For a repeating one, POST /api/series."""
    spoke_id = str(data.get('spoke_id', ''))
    spoke = hub.db.spoke(spoke_id)
    if not spoke or spoke['removed_at']:
        raise HTTPException(400, 'Choose a spoke')
    fields = {**_event_fields(data), 'spoke_id': spoke_id}
    with hub.broadcasts.lock:
        event_id = hub.broadcasts.create(fields, _channel_ids(data))
    return event_view(hub.broadcasts.event(event_id))


@app.patch('/api/events/{event_id}', dependencies=[Depends(admin)])
def update_event(event_id: int, data: dict = Body()):
    """Change a broadcast: any of the fields POST /api/events takes, plus channel_ids. On a date of a series
    this changes that date only (and detaches it); PATCH /api/series/{id} changes all upcoming."""
    before = hub.broadcasts.event(event_id)
    if not before:
        raise HTTPException(404)
    moving = 'spoke_id' in data and str(data['spoke_id']) != before['spoke_id']
    if moving:
        spoke = hub.db.spoke(str(data['spoke_id']))
        if not spoke or spoke['removed_at']:
            raise HTTPException(400, 'Choose a spoke')
        if before['state'] != 'scheduled':
            raise HTTPException(400, 'Only an upcoming broadcast can move to another spoke')
    with hub.broadcasts.lock:
        if moving:
            hub.broadcasts.move(event_id, spoke)
        hub.broadcasts.update(event_id, _event_fields(data, partial=True),
                              _channel_ids(data) if 'channel_ids' in data else None)
    if before['series_id']:
        # Changed on its own: changes to the series leave it alone from now on
        hub.db.run('UPDATE events SET detached = 1 WHERE id = ?', event_id)
    hub.library.sync_names(event_id)   # renames its folder and files if the title changed
    return event_view(hub.broadcasts.event(event_id))


@app.put('/api/events/{event_id}/thumbnail', dependencies=[Depends(admin)])
async def upload_thumbnail(event_id: int, request: Request):
    content_type = request.headers.get('content-type', '').split(';')[0]
    if content_type not in THUMBNAIL_TYPES:
        raise HTTPException(400, 'Use a JPG or PNG image')
    image = await request.body()
    if len(image) > THUMBNAIL_MAX:
        raise HTTPException(400, 'Thumbnails must be under 2 MB (1280×720 is ideal)')
    if not hub.broadcasts.event(event_id):
        raise HTTPException(404)
    await asyncio.to_thread(hub.broadcasts.set_thumbnail, event_id, image, content_type)
    hub.db.run('UPDATE events SET detached = 1 WHERE id = ? AND series_id IS NOT NULL', event_id)
    await asyncio.to_thread(hub.library.write_extras, event_id)
    return event_view(hub.broadcasts.event(event_id))


@app.get('/api/events/{event_id}/thumbnail', dependencies=[Depends(admin)])
def get_thumbnail(event_id: int):
    path = hub.broadcasts.thumbnail_path(hub.broadcasts.event(event_id))
    if not path or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.post('/api/events/{event_id}/start', dependencies=[Depends(admin)])
def start_event(event_id: int):
    """Go live now with a scheduled broadcast."""
    event = hub.broadcasts.event(event_id)
    if not event or event['state'] != 'scheduled':
        raise HTTPException(400, 'Only a scheduled broadcast can be started')
    spoke = hub.db.spoke(event['spoke_id'])
    if not spoke or spoke['removed_at']:
        raise HTTPException(400, 'Its spoke was removed: move it to another spoke first')
    if event['start_at'] > time.time():
        hub.db.run('UPDATE events SET start_at = ? WHERE id = ?', time.time(), event_id)
    hub.broadcasts.go_live(event_id)
    return event_view(hub.broadcasts.event(event_id))


@app.post('/api/events/{event_id}/end', dependencies=[Depends(admin)])
def end_event(event_id: int):
    """End a live broadcast."""
    event = hub.broadcasts.event(event_id)
    if not event or event['state'] != 'live':
        raise HTTPException(400, 'That broadcast is not live')
    hub.broadcasts.end(event_id)
    return event_view(hub.broadcasts.event(event_id))


@app.delete('/api/events/{event_id}', dependencies=[Depends(admin)])
def delete_event(event_id: int, recordings_too: bool = False):
    event = hub.broadcasts.event(event_id)
    if event and recordings_too:
        recordings.delete_copies(event['spoke_id'], [c for c in hub.library.event_copies(event) if not c.get('filed')])
        for edit in hub.db.all('SELECT output FROM edits WHERE event_id = ? AND output IS NOT NULL', event_id):
            hub.library.delete(edit['output'])
        hub.library.delete_event_files(event)
    if event and event['series_id'] and event['state'] == 'scheduled':
        hub.series.skip(event['series_id'], event['occurrence'])   # so it isn't made again
    with hub.broadcasts.lock:
        hub.broadcasts.delete(event_id)
    return {'ok': True}


# ---- Recurring broadcasts ----
def _series_fields(data, partial=False):
    f = _event_fields(data, partial, timing=False)
    if 'add_date' in data or not partial:
        f['add_date'] = int(bool(data.get('add_date', True)))
    if 'repeat' in data or not partial:
        f['repeat'] = _rule(data.get('repeat'))
    for field in ('first_date', 'until_date'):
        if field in data or not partial:
            try:
                f[field] = date.fromisoformat(data[field]).isoformat() if data.get(field) else None
            except (TypeError, ValueError):
                raise HTTPException(400, 'Dates look like 2026-11-08')
    if not partial and not f['first_date']:
        raise HTTPException(400, 'Pick the first date')
    if 'start_minutes' in data or not partial:
        try:
            f['start_minutes'] = int(data['start_minutes'])
            assert 0 <= f['start_minutes'] < 1440
        except (KeyError, TypeError, ValueError, AssertionError):
            raise HTTPException(400, 'Pick a start time')
    if 'length_minutes' in data or not partial:
        f['length_minutes'] = int(data['length_minutes']) if data.get('length_minutes') else None
        if f['length_minutes'] is not None and not 1 <= f['length_minutes'] <= 1440:
            raise HTTPException(400, 'A broadcast can run up to 24 hours')
    if 'timezone' in data or not partial:
        f['timezone'] = str(data.get('timezone') or '') if zone(data.get('timezone')) else ''
    return f


def _rule(value):
    """A series' rule as stored: which days of the week, which weeks, which months."""
    rule = value if isinstance(value, dict) else {}
    try:
        days = sorted({int(d) for d in rule.get('days', []) if 0 <= int(d) <= 6})
        months = sorted({int(m) for m in rule.get('months', []) if 1 <= int(m) <= 12})
        weeks = rule.get('weeks', 'every')
        if isinstance(weeks, list):
            weeks = sorted({int(w) for w in weeks if int(w) in (1, 2, 3, 4, -1)})
    except (TypeError, ValueError):
        raise HTTPException(400, "That repeat rule isn't readable")
    if not days:
        raise HTTPException(400, 'Pick at least one day of the week')
    if weeks == [] or (not isinstance(weeks, list) and weeks not in WEEKS):
        raise HTTPException(400, 'Pick which weeks of the month')
    return json.dumps({'days': days, 'weeks': weeks, 'months': [] if len(months) == 12 else months})


def _series(series_id):
    s = hub.series.get(series_id)
    if not s:
        raise HTTPException(404, 'That series is gone')
    return s


@app.post('/api/series', dependencies=[Depends(admin)])
def create_series(data: dict = Body()):
    """Start a recurring broadcast. With from_event, an existing broadcast becomes its first date."""
    spoke_id = str(data.get('spoke_id', ''))
    spoke = hub.db.spoke(spoke_id)
    if not spoke or spoke['removed_at']:
        raise HTTPException(400, 'Choose a spoke')
    fields = {**_series_fields(data), 'spoke_id': spoke_id}
    if fields['until_date'] and fields['until_date'] < fields['first_date']:
        raise HTTPException(400, 'The last date must be after the first')
    from_event = int(data['from_event']) if data.get('from_event') else None
    if from_event:
        event = hub.broadcasts.event(from_event)
        if not event or event['state'] != 'scheduled' or event['series_id']:
            raise HTTPException(400, 'Only an upcoming broadcast can start a series')
    series_id = hub.series.create(fields, _channel_ids(data), from_event)
    return hub.series.view(hub.series.get(series_id))


@app.patch('/api/series/{series_id}', dependencies=[Depends(admin)])
def update_series(series_id: int, data: dict = Body()):
    """Change the series and all its upcoming broadcasts (except ones changed on their own;
    `attach` brings one of those back in line)."""
    _series(series_id)
    hub.series.update(series_id, _series_fields(data, partial=True),
                      _channel_ids(data) if 'channel_ids' in data else None,
                      attach=int(data['attach']) if data.get('attach') else None)
    for event in hub.series.upcoming(series_id):
        hub.library.sync_names(event['id'])
    return hub.series.view(hub.series.get(series_id))


@app.put('/api/series/{series_id}/thumbnail', dependencies=[Depends(admin)])
async def upload_series_thumbnail(series_id: int, request: Request):
    content_type = request.headers.get('content-type', '').split(';')[0]
    if content_type not in THUMBNAIL_TYPES:
        raise HTTPException(400, 'Use a JPG or PNG image')
    image = await request.body()
    if len(image) > THUMBNAIL_MAX:
        raise HTTPException(400, 'Thumbnails must be under 2 MB (1280×720 is ideal)')
    _series(series_id)
    await asyncio.to_thread(hub.series.set_thumbnail, series_id, image, content_type)
    return hub.series.view(hub.series.get(series_id))


@app.get('/api/series/{series_id}/thumbnail', dependencies=[Depends(admin)])
def get_series_thumbnail(series_id: int):
    path = hub.series.thumbnail_path(_series(series_id))
    if not path or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.post('/api/series/{series_id}/dates/{day}', dependencies=[Depends(admin)])
def create_series_date(series_id: int, day: str, data: dict = Body(default={})):
    """Make one planned date's broadcast now: as the series has it, or changed on its own (with fields)."""
    _series(series_id)
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        raise HTTPException(400, 'Dates look like 2026-11-08')
    fields = _event_fields(data) if data else None
    event_id = hub.series.create_date(series_id, day, fields, _channel_ids(data) if 'channel_ids' in data else None)
    return event_view(hub.broadcasts.event(event_id))


@app.post('/api/series/{series_id}/skip', dependencies=[Depends(admin)])
def skip_series_date(series_id: int, day: str = Body(embed=True, alias='date')):
    _series(series_id)
    hub.series.skip(series_id, day)
    return {'ok': True}


@app.delete('/api/series/{series_id}', dependencies=[Depends(admin)])
def delete_series(series_id: int, keep: int = 0):
    """End a series: its upcoming broadcasts are deleted (`keep` one stays, on its own)."""
    _series(series_id)
    hub.series.delete(series_id, keep or None)
    return {'ok': True}



# ---- Editing (hub only) ----
def with_views(published):
    """Published uploads, each with its numbers from YouTube (stats.py)."""
    return [{**p, 'stats': hub.stats.for_video(p['video_id']) if p.get('video_id') else None} for p in published or []]


def edit_view(edit):
    return {**{k: edit[k] for k in ('id', 'event_id', 'source', 'spec', 'status', 'progress', 'duration',
                                    'output', 'error', 'created_at')}, 'published': with_views(edit['published'])}


def _event_copies(event):
    return [c for c in hub.library.event_copies(event) if c['path']]


@app.get('/api/events/{event_id}/editor', dependencies=[Depends(admin)])
def editor_source(event_id: int):
    event = hub.broadcasts.event(event_id)
    if not event:
        raise HTTPException(404)
    copies = [{**c, 'duration': probe_duration(hub.library.resolve(c['path']))} for c in _event_copies(event)]
    transcript = hub.transcriber.transcript(event_id)
    done = transcript and transcript['status'] == 'done'
    return {'event': event_view(event, copies), 'copies': copies, 'channels': channel_list(),
            'edit': edit_view(hub.editor.current(event_id)) if hub.editor.current(event_id) else None,
            'draft': json.loads(event['edit_draft']) if event['edit_draft'] else None,
            # For the transcript and shorts tracks
            'sentences': sentences(transcript['segments']) if done else None,
            'transcript_status': transcript['status'] if transcript else None,
            'suggestions': json.loads(transcript['suggestions']) if done and transcript.get('suggestions') else None,
            'shorts': [short_view(s) for s in hub.shorts.for_event(event_id)], 'shorts_options': hub.shorts.options(),
            'ai': hub.assistant.enabled(), 'media': [media_view(m) for m in hub.media.all()], 'brands': hub.media.brands(),
            'channel_brand': next((r['brand_id'] for r in hub.db.all(
                'SELECT c.brand_id FROM event_targets t JOIN youtube_channels c ON c.id = t.channel_id '
                'WHERE t.event_id = ? AND c.brand_id IS NOT NULL', event_id)), None)}


@app.get('/api/events/{event_id}/editor/status', dependencies=[Depends(admin)])
def editor_status(event_id: int):
    """What the editor polls while something renders: its edit and shorts."""
    if not hub.broadcasts.event(event_id):
        raise HTTPException(404)
    edit = hub.editor.current(event_id)
    return {'edit': edit_view(edit) if edit else None, 'shorts': [short_view(s) for s in hub.shorts.for_event(event_id)]}


AUTO_SHORTS = 3          # Shorts the hub makes of a broadcast by itself, unless Settings says otherwise
SHORT_LOOK = {'framing': 'fill', 'style': 'bold', 'accent': 'ember', 'trim_pauses': True}   # (as the editor's)


async def make_shorts_by_themselves():
    """Once a broadcast is transcribed, the assistant picks its best moments and the hub makes Shorts of them,
    the editor's way: vertical, following the speaker, captions, no pauses. They're drafts to look over: nothing is
    published by itself. Needs the assistant (an OpenRouter key); Settings > Recordings turns it off."""
    await asyncio.sleep(90)
    while True:
        try:
            if hub.db.get_setting('shorts_auto', True) and hub.assistant.enabled() and SHORTS_AVAILABLE:
                for row in hub.db.all("SELECT t.event_id FROM transcripts t JOIN events e ON e.id = t.event_id "
                                      "WHERE t.status = 'done' AND t.auto_shorts_at IS NULL AND e.state = 'ended' "
                                      "AND COALESCE(e.ended_at, e.end_at, e.start_at) > ?", time.time() - 3 * 86400):
                    await shorts_for(row['event_id'])
        except Exception as e:  # never let the loop die
            print(f'Making Shorts: {e}')
        await asyncio.sleep(120)


async def shorts_for(event_id):
    hub.db.run('UPDATE transcripts SET auto_shorts_at = ? WHERE event_id = ?', time.time(), event_id)   # once, come what may
    event, transcript = hub.broadcasts.event(event_id), hub.transcriber.transcript(event_id)
    if not event or not transcript or hub.shorts.for_event(event_id):   # (some made by hand already: leave it be)
        return
    try:
        moments = await hub.assistant.suggest_moments(event['title'], sentences(transcript['segments']))
    except AssistantError as e:
        print(f"Shorts of {event['title']}: {e}")
        return
    hub.db.run('UPDATE transcripts SET suggestions = ? WHERE event_id = ?', json.dumps(
        {'moments': moments, 'model': hub.assistant.config()['model'], 'made_at': time.time()}), event_id)
    count = int(hub.db.get_setting('shorts_auto_count', AUTO_SHORTS))
    for m in moments[:count]:
        try:
            hub.shorts.create(event, (m['title'] or event['title'])[:100],
                              {'source': transcript['source'], 'start': m['start'], 'end': m['end'], **SHORT_LOOK})
        except ValueError as e:
            print(f"A Short of {event['title']}: {e}")
    made = min(count, len(moments))
    if made:
        print(f"Made {made} Short{'s' if made > 1 else ''} of {event['title']}")


@app.post('/api/events/{event_id}/suggestions', dependencies=[Depends(admin)])
async def suggest_shorts(event_id: int):
    """Ask the assistant's model for moments that would make good shorts (needs an OpenRouter key)."""
    event = hub.broadcasts.event(event_id)
    transcript = hub.transcriber.transcript(event_id) if event else None
    if not transcript or transcript['status'] != 'done':
        raise HTTPException(400, 'Transcribe the broadcast first')
    try:
        moments = await hub.assistant.suggest_moments(event['title'], sentences(transcript['segments']))
    except AssistantError as e:
        raise HTTPException(400, str(e))
    found = {'moments': moments, 'model': hub.assistant.config()['model'], 'made_at': time.time()}
    hub.db.run('UPDATE transcripts SET suggestions = ? WHERE event_id = ?', json.dumps(found), event_id)
    return found


@app.put('/api/events/{event_id}/edit-draft', dependencies=[Depends(admin)])
def save_edit_draft(event_id: int, data: dict = Body()):
    """Keep the editor's unsaved work on a broadcast's edit, to pick up later."""
    if not hub.broadcasts.event(event_id):
        raise HTTPException(404)
    draft = {'source': str(data.get('source', '')), 'spec': _edit_spec(data), 'saved_at': time.time(),
             'by': str(data.get('by') or '')}
    hub.db.run('UPDATE events SET edit_draft = ? WHERE id = ?', json.dumps(draft), event_id)
    return draft


@app.delete('/api/events/{event_id}/edit-draft', dependencies=[Depends(admin)])
def discard_edit_draft(event_id: int):
    hub.db.run('UPDATE events SET edit_draft = NULL WHERE id = ?', event_id)
    return {'ok': True}


@app.get('/api/recordings/waveform/{relative:path}', dependencies=[Depends(admin)])
def recording_waveform(relative: str):
    """The recording's sound level, one byte (0-255) per 1/40 s, for the editor's audio track."""
    path = hub.library.resolve(relative)
    if not path:
        raise HTTPException(404)
    return FileResponse(waveform(path), media_type='application/octet-stream',
                        headers={'X-Peaks-Per-Second': str(PEAKS_PER_SECOND), 'Cache-Control': 'max-age=3600'})


@app.get('/api/recordings/filmstrip/{relative:path}', dependencies=[Depends(admin)])
def recording_filmstrip(relative: str):
    path = hub.library.resolve(relative)
    if not path:
        raise HTTPException(404)
    return FileResponse(filmstrip(path), media_type='image/jpeg')


@app.post('/api/events/{event_id}/edits', dependencies=[Depends(admin)])
def create_edit(event_id: int, data: dict = Body()):
    """Render the broadcast's edit, replacing any earlier one. Body: source (a recording's path from GET
    /api/events/{id}/editor), then either
    - clips: [{start, end, color: {brightness, contrast, saturation, warmth (-100..100)}}] in the recording's seconds,
      in order; transitions: one per join, [{type: cut | dissolve | dip_black | dip_white | wipe | slide | circle,
      duration}]; titles: [{text, sub, style: lower_third | title | caption | corner, start, end}] in the edited
      video's seconds; or, simpler,
    - in and out (seconds; out optional), cuts ([[start, end], ...] to remove), dip (seconds of black at each cut);
    plus fade_in, fade_out (seconds, up to 10)."""
    event = hub.broadcasts.event(event_id)
    if not event or data.get('source') not in [c['path'] for c in _event_copies(event)]:
        raise HTTPException(400, 'Pick one of this broadcast\'s recordings')
    try:
        edit_id = hub.editor.create(event, data['source'], _edit_spec(data))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return edit_view(hub.editor.edit(edit_id))


def _edit_spec(data):
    """A clean edit from the editor (tracks), or the simple in/out/cuts form."""
    def clip(c, video):
        out = {'at': float(c.get('at') or 0), 'start': float(c.get('start') or 0), 'end': float(c['end'])}
        if c.get('media'):
            out['media'] = int(c['media'])
        if c.get('link'):
            out['link'] = str(c['link'])[:40]       # the editor's own: which audio goes with which video
        if video and c.get('transition'):
            out['transition'] = {'type': str(c['transition'].get('type') or 'cut'),
                                 'duration': float(c['transition'].get('duration') or 0)}
        return out
    try:
        fades = {'fade_in': min(10.0, float(data.get('fade_in') or 0)), 'fade_out': min(10.0, float(data.get('fade_out') or 0))}
        if 'video' in data or 'audio' in data:
            return {'video': [clip(c, True) for c in data.get('video') or []][:1000],
                    'audio': [clip(c, False) for c in data.get('audio') or []][:1000],
                    'music': [{**clip(c, False), 'volume': float(c.get('volume', 0.3))} for c in data.get('music') or []][:50],
                    'titles': [{'text': str(t.get('text', ''))[:120], 'sub': str(t.get('sub', ''))[:120],
                                'style': str(t.get('style') or 'lower_third'), 'start': float(t['start']), 'end': float(t['end'])}
                               for t in data.get('titles') or []][:100],
                    'color': {k: float((data.get('color') or {}).get(k) or 0) for k in COLOR},
                    'brand': int(data['brand']) if data.get('brand') else None, **fades}
        return {'in': float(data.get('in') or 0), 'out': float(data['out']) if data.get('out') else None,
                'cuts': [[float(a), float(b)] for a, b in data.get('cuts', [])],
                'dip': min(2.0, float(data.get('dip') or 0)), **fades}
    except (TypeError, ValueError, KeyError, AttributeError):
        raise HTTPException(400, 'That edit has a time that isn\'t a number')


# ---- The media library and brands (media.py) ----
def media_view(m):
    return {**m, 'url': f"/api/media/{m['id']}/file", 'poster': f"/api/media/{m['id']}/poster" if m['kind'] != 'audio' else None}


@app.get('/api/media', dependencies=[Depends(admin)])
def list_media():
    """Videos, images and sound uploaded for edits, and the brands made from them."""
    return {'media': [media_view(m) for m in hub.media.all()], 'brands': hub.media.brands()}


@app.post('/api/media', dependencies=[Depends(admin)])
async def upload_media(request: Request):
    """Upload a video, image or sound file: the body is the file; X-File-Name says what it's called."""
    try:
        item = await hub.media.add(request.headers.get('x-file-name', 'upload'), request.stream())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return media_view(item)


@app.patch('/api/media/{media_id}', dependencies=[Depends(admin)])
def rename_media(media_id: int, name: str = Body(embed=True)):
    if not hub.media.get(media_id):
        raise HTTPException(404)
    hub.media.rename(media_id, str(name).strip() or 'Untitled')
    return media_view(hub.media.get(media_id))


@app.delete('/api/media/{media_id}', dependencies=[Depends(admin)])
def delete_media(media_id: int):
    """Delete a library file. Edits that used it leave it out next time they're rendered."""
    hub.media.delete(media_id)
    return {'ok': True}


@app.get('/api/media/{media_id}/file', dependencies=[Depends(admin)])
def media_file(media_id: int):
    path = hub.media.path(media_id)
    if not path:
        raise HTTPException(404)
    return FileResponse(path)


@app.get('/api/media/{media_id}/poster', dependencies=[Depends(admin)])
def media_poster(media_id: int):
    path = hub.media.poster(media_id)
    if not path:
        raise HTTPException(404)
    return FileResponse(path, headers={'Cache-Control': 'max-age=86400'})


@app.post('/api/brands', dependencies=[Depends(admin)])
def create_brand(data: dict = Body()):
    """A brand: name, intro and outro (media ids: a video, or an image shown intro_seconds), watermark (an image's
    media id), watermark_corner (top_left | top_right | bottom_left | bottom_right), watermark_size (% of the width),
    watermark_opacity (%), channel_ids (YouTube channels whose broadcasts use it)."""
    try:
        return hub.media.save_brand(None, data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch('/api/brands/{brand_id}', dependencies=[Depends(admin)])
def change_brand(brand_id: int, data: dict = Body()):
    if not hub.media.brand(brand_id):
        raise HTTPException(404)
    try:
        return hub.media.save_brand(brand_id, data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete('/api/brands/{brand_id}', dependencies=[Depends(admin)])
def delete_brand(brand_id: int):
    hub.media.delete_brand(brand_id)
    return {'ok': True}


@app.get('/api/edits/{edit_id}', dependencies=[Depends(admin)])
def get_edit(edit_id: int):
    edit = hub.editor.edit(edit_id)
    if not edit:
        raise HTTPException(404)
    return edit_view(edit)


@app.post('/api/edits/{edit_id}/publish', dependencies=[Depends(admin)])
def publish_edit(edit_id: int, data: dict = Body()):
    """Upload the rendered edit to YouTube as a new video. Body: channel_ids, title, description, privacy,
    hide_original (private | unlisted | keep: the live video), hide_previous (the same, for an earlier upload
    of this edit). YouTube allows about 6 uploads a day per Google project."""
    edit = hub.editor.edit(edit_id)
    if not edit or edit['status'] != 'done' or not edit['output']:
        raise HTTPException(400, 'Render the edit before publishing it')
    known = {c['id'] for c in hub.db.all('SELECT id FROM youtube_channels')}
    channel_ids = [int(c) for c in data.get('channel_ids', [])]
    if not channel_ids or not set(channel_ids) <= known:
        raise HTTPException(400, 'Choose at least one connected channel')
    title = re.sub(r'[<>]', '', str(data.get('title', ''))).strip()[:100]
    if not title:
        raise HTTPException(400, 'Give the video a title')
    if data.get('privacy') not in ('public', 'unlisted', 'private'):
        raise HTTPException(400, 'Choose who can see the video')
    hub.editor.publish(edit_id, {
        'channel_ids': channel_ids, 'title': title,
        'description': re.sub(r'[<>]', '', str(data.get('description', '')))[:5000],
        'privacy': data['privacy'], 'hide_original': data.get('hide_original') or 'keep',
        'hide_previous': data.get('hide_previous') or 'keep'})
    return edit_view(hub.editor.edit(edit_id))


@app.delete('/api/edits/{edit_id}', dependencies=[Depends(admin)])
def delete_edit(edit_id: int):
    hub.editor.delete(edit_id)
    return {'ok': True}


# ---- Transcripts ----
@app.get('/api/events/{event_id}/transcript', dependencies=[Depends(admin)])
def get_transcript(event_id: int):
    event = hub.broadcasts.event(event_id)
    if not event:
        raise HTTPException(404)
    copies = _event_copies(event)
    return {'transcript': hub.transcriber.transcript(event_id), 'options': hub.transcriber.options(),
            'copies': copies, 'event': event_view(event, copies)}


@app.post('/api/events/{event_id}/transcript', dependencies=[Depends(admin)])
def start_transcript(event_id: int, data: dict = Body(default={})):
    if not hub.transcriber.options()['available']:
        raise HTTPException(400, 'Transcription is not installed on this hub')
    event = hub.broadcasts.event(event_id)
    copies = _event_copies(event) if event else []
    chosen = next((c for c in copies if c['path'] == data.get('source')), None) or hub.transcriber.best_copy(copies)
    if not chosen:
        raise HTTPException(400, 'There is no finished recording to transcribe yet')
    hub.transcriber.request(event_id, chosen['path'])
    return {'ok': True}


@app.put('/api/events/{event_id}/transcript', dependencies=[Depends(admin)])
def save_transcript(event_id: int, segments: list = Body(embed=True)):
    if not hub.transcriber.transcript(event_id):
        raise HTTPException(404)
    hub.transcriber.save(event_id, segments)
    hub.library.write_extras(event_id)
    return {'ok': True}


@app.get('/api/events/{event_id}/transcript.{fmt}', dependencies=[Depends(admin)])
def download_transcript(event_id: int, fmt: str, edit: int = 0):
    transcript = hub.transcriber.transcript(event_id)
    writers = {'srt': (to_srt, 'application/x-subrip'), 'vtt': (to_vtt, 'text/vtt'), 'txt': (to_txt, 'text/plain')}
    if not transcript or transcript['status'] != 'done' or fmt not in writers:
        raise HTTPException(404)
    segments, name = transcript['segments'], safe_name(hub.broadcasts.event(event_id)['title'])
    if edit:
        # Captions for an edited version: timings follow its cuts
        version = hub.editor.edit(edit)
        if not version or version['event_id'] != event_id:
            raise HTTPException(404)
        source = hub.library.resolve(version['source'])
        segments = remap(segments, placements(normalize(version['spec'], probe_duration(source), hub.editor.resolve)))
        name += ' - edited'
    write, media_type = writers[fmt]
    return Response(write(segments), media_type=media_type,
                    headers={'Content-Disposition': f'attachment; filename="{name}.{fmt}"'})



# ---- Shorts (hub only) ----
def short_view(short):
    return {**{k: short[k] for k in ('id', 'event_id', 'title', 'spec', 'status', 'progress', 'duration', 'output',
                                     'error', 'created_at')}, 'published': with_views(short['published'])}


def _short(short_id):
    short = hub.shorts.short(short_id)
    if not short:
        raise HTTPException(404)
    return short


def _short_title(data, event):
    return re.sub(r'[<>]', '', str(data.get('title') or event['title'])).strip()[:100] or event['title']


def _short_source(event, data):
    copies = _event_copies(event)
    if data.get('source') not in [c['path'] for c in copies]:
        raise HTTPException(400, "Pick one of this broadcast's recordings")


@app.get('/api/events/{event_id}/shorts', dependencies=[Depends(admin)])
def list_shorts(event_id: int):
    """The broadcast's shorts, plus what's needed to make one: its recordings (with their length), its
    transcript's sentences to pick a moment from, and the framings, caption styles and accents."""
    event = hub.broadcasts.event(event_id)
    if not event:
        raise HTTPException(404)
    copies = [{**c, 'duration': probe_duration(hub.library.resolve(c['path']))} for c in _event_copies(event)]
    transcript = hub.transcriber.transcript(event_id)
    found = sentences(transcript['segments']) if transcript and transcript['status'] == 'done' else None
    return {'event': event_view(event, copies), 'copies': copies, 'sentences': found,
            'transcript_status': transcript['status'] if transcript else None,
            'shorts': [short_view(s) for s in hub.shorts.for_event(event_id)], 'channels': channel_list(),
            'options': hub.shorts.options()}


@app.post('/api/events/{event_id}/shorts', dependencies=[Depends(admin)])
def create_short(event_id: int, data: dict = Body()):
    """Make a short. Body: title, source (a recording's path from GET /api/events/{id}/shorts), start and end
    (seconds in that recording, up to 3 minutes apart; use the start of a sentence and the end of one from GET
    /api/events/{id}/shorts, so it begins and ends on whole sentences), framing (fill: follows the speaker | fit: the whole picture over a blurred copy),
    style (a caption style, or none), accent (a color name), trim_pauses (take out pauses over 0.6 s)."""
    event = hub.broadcasts.event(event_id)
    if not event:
        raise HTTPException(404)
    _short_source(event, data)
    try:
        short_id = hub.shorts.create(event, _short_title(data, event), data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return short_view(hub.shorts.short(short_id))


@app.get('/api/titles/preview.png', dependencies=[Depends(admin)])
def title_preview(style: str = 'lower_third', text: str = '', sub: str = '', width: int = 1920, height: int = 1080):
    """A title exactly as the edit will show it, for the editor's preview."""
    from . import titles
    out = io.BytesIO()
    titles.draw({'style': style, 'text': text, 'sub': sub}, max(320, min(3840, width)), max(180, min(2160, height))).save(out, 'PNG')
    return Response(out.getvalue(), media_type='image/png', headers={'Cache-Control': 'max-age=86400'})


@app.post('/api/events/{event_id}/framing', dependencies=[Depends(admin)])
async def short_framing(event_id: int, data: dict = Body()):
    """How a 'fill' short of a moment will be framed, to preview it: {width, path [[t, center]], whole [[start,
    end]]}. Body: source, start, end (seconds in the recording, up to 3 minutes apart)."""
    event = hub.broadcasts.event(event_id)
    if not event or not hub.shorts.options()['available']:
        raise HTTPException(404)
    _short_source(event, data)
    start, end = float(data['start']), float(data['end'])
    if not 0 < end - start <= 181:
        raise HTTPException(400, 'Pick a moment up to 3 minutes long')
    return await asyncio.to_thread(hub.shorts.framing_plan, hub.library.resolve(data['source']), start, end)


@app.post('/api/events/{event_id}/caption-pages', dependencies=[Depends(admin)])
def short_caption_pages(event_id: int, data: dict = Body()):
    """What a short's captions will show, for the editor's preview: [{start, end, words: [{start, end, text}]}] in
    the recording's time. Body: start, end, style."""
    from . import captions, shorts
    transcript = hub.transcriber.transcript(event_id)
    style = captions.STYLES.get(data.get('style'))
    if not transcript or transcript['status'] != 'done' or not style:
        return []
    words = shorts.words_between(transcript['segments'], float(data['start']), float(data['end']))
    return [{'start': p[0]['start'], 'end': p[-1]['end'], 'words': p} for p in captions.pages(words, style)]


@app.get('/api/shorts/caption.png', dependencies=[Depends(admin)])
def short_caption(style: str, words: str, active: int = -1, accent: str = 'ember'):
    """One caption exactly as a short shows it (a band the short's width), for the editor's preview."""
    from . import captions
    if style not in captions.STYLES or accent not in captions.ACCENTS:
        raise HTTPException(404)
    out = io.BytesIO()
    captions.draw(words.split('\n')[:12], active if active >= 0 else None, captions.STYLES[style],
                  captions.ACCENTS[accent]).save(out, 'PNG')
    return Response(out.getvalue(), media_type='image/png', headers={'Cache-Control': 'max-age=86400'})


@app.get('/api/shorts/style.png', dependencies=[Depends(admin)])
def short_style_sample(style: str, accent: str = 'ember'):
    """A sample of a caption style, for the dashboard's picker."""
    from . import captions   # here: it needs Pillow, which comes with the transcribe extra
    if style not in captions.STYLES or accent not in captions.ACCENTS:
        raise HTTPException(404)
    out = io.BytesIO()
    captions.sample(style, captions.ACCENTS[accent]).save(out, 'PNG')
    return Response(out.getvalue(), media_type='image/png', headers={'Cache-Control': 'max-age=86400'})


@app.get('/api/shorts/{short_id}', dependencies=[Depends(admin)])
def get_short(short_id: int):
    return short_view(_short(short_id))


@app.put('/api/shorts/{short_id}', dependencies=[Depends(admin)])
def change_short(short_id: int, data: dict = Body()):
    """Change a short. With only a title it's renamed; with anything else it's made again (the same body as
    making one), and the new video replaces the old when it's ready."""
    short = _short(short_id)
    event = hub.broadcasts.event(short['event_id'])
    if set(data) <= {'title'}:
        hub.shorts.rename(short_id, _short_title(data, event))
        return short_view(hub.shorts.short(short_id))
    _short_source(event, data)
    try:
        hub.shorts.change(short_id, _short_title(data, event), data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return short_view(hub.shorts.short(short_id))


@app.post('/api/shorts/{short_id}/publish', dependencies=[Depends(admin)])
def publish_short(short_id: int, data: dict = Body()):
    """Upload a short to YouTube as a Short. Body: channel_ids, title, description, privacy (#Shorts is
    added to the description). YouTube allows about 6 uploads a day per Google project."""
    short = _short(short_id)
    if short['status'] != 'done' or not short['output']:
        raise HTTPException(400, 'Wait for the short to finish first')
    known = {c['id'] for c in hub.db.all('SELECT id FROM youtube_channels')}
    channel_ids = [int(c) for c in data.get('channel_ids', [])]
    if not channel_ids or not set(channel_ids) <= known:
        raise HTTPException(400, 'Choose at least one connected channel')
    title = re.sub(r'[<>]', '', str(data.get('title', ''))).strip()[:100]
    if not title:
        raise HTTPException(400, 'Give the short a title')
    if data.get('privacy') not in ('public', 'unlisted', 'private'):
        raise HTTPException(400, 'Choose who can see the short')
    hub.shorts.publish(short_id, {'channel_ids': channel_ids, 'title': title, 'privacy': data['privacy'],
                                  'description': re.sub(r'[<>]', '', str(data.get('description', '')))[:4900]})
    return short_view(hub.shorts.short(short_id))


@app.delete('/api/shorts/{short_id}', dependencies=[Depends(admin)])
def delete_short(short_id: int):
    """Delete a short and its video file (anything already on YouTube stays there)."""
    _short(short_id)
    try:
        hub.shorts.delete(short_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {'ok': True}

def _destination_fields(data, partial=False):
    fields = {}
    for field, cast in (('platform', str), ('name', str), ('url', str), ('stream_key', str),
                        ('enabled', bool), ('transcode_kbps', int)):
        if field in data:
            fields[field] = cast(data[field]).strip() if cast is str else cast(data[field])
    if 'url' in fields and not re.match(r'^(rtmps?|srt)://', fields['url']):
        raise HTTPException(400, 'Server must start with rtmp://, rtmps:// or srt://')
    if not partial:
        if fields.get('platform') not in platforms.BY_ID:
            raise HTTPException(400, 'Unknown platform')
        if not fields.get('url'):
            raise HTTPException(400, 'Server is required')
        fields.setdefault('name', platforms.BY_ID[fields['platform']]['name'])
    if partial and not fields.get('stream_key', 'kept'):
        fields.pop('stream_key')   # an empty key in an edit means "keep the current one"
    return fields


@app.post('/api/spokes/{spoke_id}/destinations', dependencies=[Depends(admin)])
def add_destination(spoke_id: str, data: dict = Body()):
    if not hub.db.spoke(spoke_id):
        raise HTTPException(404)
    f = _destination_fields(data)
    hub.db.run('INSERT INTO destinations (spoke_id, platform, name, url, stream_key, enabled, transcode_kbps) '
               'VALUES (?, ?, ?, ?, ?, ?, ?)', spoke_id, f['platform'], f['name'], f['url'],
               f.get('stream_key', ''), int(f.get('enabled', True)), f.get('transcode_kbps', 0))
    return {'ok': True}


@app.patch('/api/destinations/{dest_id}', dependencies=[Depends(admin)])
def update_destination(dest_id: int, data: dict = Body()):
    for field, value in _destination_fields(data, partial=True).items():
        hub.db.run(f'UPDATE destinations SET {field} = ? WHERE id = ?',
                   int(value) if isinstance(value, bool) else value, dest_id)
    return {'ok': True}


@app.delete('/api/destinations/{dest_id}', dependencies=[Depends(admin)])
def delete_destination(dest_id: int):
    hub.db.run('DELETE FROM destinations WHERE id = ?', dest_id)
    return {'ok': True}


# ---- Recordings ----
@app.get('/api/recordings/file/{relative:path}', dependencies=[Depends(admin)])
def download_recording(relative: str):
    path = hub.library.resolve(relative)
    if not path:
        raise HTTPException(404)
    return FileResponse(path, filename=path.name)


@app.delete('/api/recordings/file/{relative:path}', dependencies=[Depends(admin)])
def delete_recording(relative: str):
    if not hub.library.delete(relative):
        raise HTTPException(404)
    return {'ok': True}


@app.post('/api/reveal', dependencies=[Depends(admin)])
def reveal(request: Request, data: dict = Body(default={})):
    """Show the recordings folder, a broadcast's folder or one file in Finder / File Explorer."""
    if not is_local(request):
        raise HTTPException(403, "Only from a browser on the hub's own computer")
    if data.get('event_id'):
        event = hub.broadcasts.event(int(data['event_id']))
        path = Path(event['folder']) if event and event['folder'] else None
    elif data.get('file'):
        path = hub.library.resolve(str(data['file']))
    else:
        path = hub.library.root()
        path.mkdir(parents=True, exist_ok=True)
    if not path or not path.exists():
        raise HTTPException(404, 'That folder is gone; it may have been moved or deleted')
    desktop.reveal(path)
    return {'ok': True}


@app.post('/api/recordings-folder/choose', dependencies=[Depends(admin)])
def choose_recordings_folder(request: Request):
    """Ask for a folder with the computer's own picker; saved by PATCH /api/settings."""
    if not is_local(request):
        raise HTTPException(403, "Only from a browser on the hub's own computer")
    return {'path': desktop.choose_folder(hub.library.root())}


# ---- Settings ----
@app.get('/api/settings', dependencies=[Depends(admin)])
def get_settings(request: Request):
    ip = hub_ip()
    host = request.headers.get('host', '').rsplit(':', 1)[0].strip('[]')
    return {'public_host': hub.db.get_setting('public_host') or '',
            'detected_host': ip or (host if host not in ('localhost', *LOOPBACK) else ''),
            'srt_port': settings.SRT_PORT, 'rtmp_port': settings.RTMP_PORT, 'version': VERSION,
            'data_dir': str(settings.DATA_DIR), 'install': hub.updates.kind, 'password_from_env': bool(settings.ADMIN_PASSWORD),
            'recordings_folder': str(hub.library.root()), 'recordings_folder_default': str(settings.RECORDINGS_FOLDER),
            'local': is_local(request), 'platform': desktop.PLATFORM, 'docker': settings.IN_DOCKER,
            'transcription': {**hub.transcriber.options(), 'models': MODELS},
            'shorts_auto': {'on': bool(hub.db.get_setting('shorts_auto', True)),
                            'count': int(hub.db.get_setting('shorts_auto_count', AUTO_SHORTS)),
                            'assistant': hub.assistant.enabled(), 'available': SHORTS_AVAILABLE}}


@app.patch('/api/settings', dependencies=[Depends(admin)])
def update_settings(data: dict = Body()):
    if 'public_host' in data:
        hub.db.set_setting('public_host', str(data['public_host']).strip())
    if 'recordings_folder' in data:
        folder = str(data['recordings_folder'] or '').strip()
        if folder:
            path = Path(folder).expanduser()
            if not path.is_absolute():
                raise HTTPException(400, 'Give the full path of the folder')
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / '.touchstream-write-test'
                probe.write_text('ok')
                probe.unlink()
            except OSError as e:
                raise HTTPException(400, f"Can't save recordings there: {e.strerror or e}")
            folder = str(path)
        hub.db.set_setting('recordings_folder', folder)   # empty: back to the default
    if 'transcribe_enabled' in data:
        hub.db.set_setting('transcribe_enabled', bool(data['transcribe_enabled']))
    if data.get('transcribe_model') in MODELS:
        hub.db.set_setting('transcribe_model', data['transcribe_model'])
    if 'transcribe_language' in data:
        hub.db.set_setting('transcribe_language', str(data['transcribe_language'])[:8])
    if 'transcribe_vocabulary' in data:
        hub.db.set_setting('transcribe_vocabulary', str(data['transcribe_vocabulary'])[:500])
    if 'shorts_auto' in data:
        hub.db.set_setting('shorts_auto', bool(data['shorts_auto']))
    if 'shorts_auto_count' in data:
        try:
            hub.db.set_setting('shorts_auto_count', max(1, min(6, int(data['shorts_auto_count']))))
        except (TypeError, ValueError):
            raise HTTPException(400, 'How many Shorts: 1 to 6')
    return {'ok': True}


def _retention_view(rules):
    plan = hub.retention.plan(rules)
    return {**rules, 'total': plan['total'], 'would_delete': plan['bytes'], 'broadcasts': plan['broadcasts'],
            'kept_unpublished': plan['kept_unpublished'], 'over': plan['over']}


def _retention_rules(data):
    rules = hub.retention.rules()
    for field in ('days', 'max_gb'):
        if field in data:
            try:
                rules[field] = int(data[field]) if data[field] else None
            except (TypeError, ValueError):
                raise HTTPException(400, f'{field} must be a whole number')
            if rules[field] is not None and rules[field] < 1:
                raise HTTPException(400, f'{field} must be at least 1')
    if 'unpublished' in data:
        rules['unpublished'] = bool(data['unpublished'])
    return rules


@app.get('/api/retention', dependencies=[Depends(admin)])
def get_retention():
    """The old-recordings rules, and what they'd delete now."""
    return _retention_view(hub.retention.rules())


@app.post('/api/retention/preview', dependencies=[Depends(admin)])
def preview_retention(data: dict = Body()):
    return _retention_view(_retention_rules(data))


@app.put('/api/retention', dependencies=[Depends(admin)])
def set_retention(data: dict = Body()):
    """Save the rules and apply them now."""
    hub.db.set_setting('retention', _retention_rules(data))
    deleted = hub.retention.apply()
    return {**_retention_view(hub.retention.rules()), 'deleted': deleted}


# ---- The assistant (OpenRouter), from the dashboard only: it spends the hub owner's credit ----
@app.get('/api/assistant', dependencies=[Depends(session_only)])
def assistant_settings():
    return hub.assistant.view()


@app.put('/api/assistant', dependencies=[Depends(session_only)])
async def set_assistant(data: dict = Body()):
    """Turn the assistant on with an OpenRouter key (checked with OpenRouter first), or change its model."""
    config = hub.assistant.config()
    key = str(data.get('api_key') or '').strip()
    if key:
        try:
            await hub.assistant.check_key(key)
        except AssistantError as e:
            raise HTTPException(400, str(e))
        config['api_key'] = key
    if data.get('model'):
        config['model'] = str(data['model']).strip()[:100]
    hub.db.set_setting('ai', config)
    hub.db.set_setting('ai_asked', True)
    return hub.assistant.view()


@app.delete('/api/assistant', dependencies=[Depends(session_only)])
def turn_off_assistant():
    """Remove the key: the assistant and other AI features go away until a key is added again."""
    hub.db.set_setting('ai', {k: v for k, v in hub.assistant.config().items() if k != 'api_key'})
    hub.db.set_setting('ai_asked', True)
    return hub.assistant.view()


@app.post('/api/assistant/skip', dependencies=[Depends(session_only)])
def skip_assistant():
    hub.db.set_setting('ai_asked', True)
    return {'ok': True}


@app.post('/api/assistant/chat', dependencies=[Depends(session_only)])
async def assistant_chat(data: dict = Body()):
    """One turn of the conversation; the dashboard keeps the messages and sends them back each time."""
    messages = data.get('messages')
    if not isinstance(messages, list) or len(messages) > 200:
        raise HTTPException(400, 'messages must be the conversation so far')
    try:
        return await hub.assistant.chat(messages, str(data.get('page') or ''), data.get('approve'))
    except AssistantError as e:
        raise HTTPException(400, str(e))


# ---- API tokens (Settings > API access) ----
@app.get('/api/tokens', dependencies=[Depends(session_only)])
def list_tokens():
    return hub.tokens.list()


@app.post('/api/tokens', dependencies=[Depends(session_only)])
def create_token(data: dict = Body()):
    """Make a token; it's returned only this once."""
    name = str(data.get('name') or '').strip()[:60]
    scope = data.get('scope')
    if not name:
        raise HTTPException(400, 'Name the token after what will use it')
    if scope not in auth.SCOPES:
        raise HTTPException(400, f'scope is one of {", ".join(auth.SCOPES)}')
    return {'token': hub.tokens.create(name, scope), 'tokens': hub.tokens.list()}


@app.delete('/api/tokens/{token_id}', dependencies=[Depends(session_only)])
def revoke_token(token_id: int):
    hub.tokens.revoke(token_id)
    return {'ok': True}


# ---- Agents asking for a token (auth.AccessRequests): see /agent ----
@app.get('/agent', include_in_schema=False)
@app.get('/llms.txt', include_in_schema=False)
def agent_page(request: Request):
    """What an AI agent reads first: what the hub does, how to ask for a token, and how to use it."""
    return Response(agent_guide.guide(str(request.base_url)), media_type='text/markdown; charset=utf-8')


def access_reply(row, base):
    reply = {'id': row['id'], 'status': row['status'], 'name': row['name'], 'scope': row['scope'],
             'code': row['code'], 'expires_at': row['expires_at']}
    if row['status'] == 'pending':
        reply['tell_your_person'] = (f'Approve TouchStream access for "{row["name"]}" in the dashboard ({base}). '
                                     f'Its code is {row["code"]}.')
        reply['poll'] = f'{base}/api/access/{row["id"]}?wait=25'
    return reply


@app.post('/api/access')
def ask_for_access(request: Request, data: dict = Body()):
    """Ask for an API token, with none (an AI agent, say). Someone approves it in the dashboard, where it shows
    with the same `code` as here, and then `GET /api/access/{id}` has the token, once. See /agent."""
    name = str(data.get('name') or '').strip()[:60] or 'An AI agent'
    scope = data.get('scope') or 'control'
    if scope not in auth.SCOPES:
        raise HTTPException(400, f'scope is one of {", ".join(auth.SCOPES)}')
    try:
        row = hub.access.ask(name, scope, request.client.host if request.client else None)
    except auth.TooManyRequests:
        raise HTTPException(429, 'Too many requests are waiting: approve or deny them in the dashboard first')
    return access_reply(row, str(request.base_url).rstrip('/'))


@app.get('/api/access/{request_id}')
async def access_status(request: Request, request_id: str, wait: int = 0):
    """How a request is going: pending, approved (with the token, this once), collected, denied or expired.
    `wait` (up to 30 seconds) waits for it to change first."""
    base = str(request.base_url).rstrip('/')
    deadline = time.time() + max(0, min(wait, 30))
    while True:
        row = await asyncio.to_thread(hub.access.get, request_id)
        if not row:
            raise HTTPException(404, 'No such request: ask again')
        if row['status'] == 'approved':
            token = await asyncio.to_thread(hub.access.collect, request_id)
            if token:
                return {**access_reply(row, base), 'token': token, 'header': f'Authorization: Bearer {token}',
                        'mcp': f'{base}/mcp', 'openapi': f'{base}/api/openapi.json', 'guide': f'{base}/agent'}
            row = await asyncio.to_thread(hub.access.get, request_id)
        if row['status'] != 'pending' or time.time() >= deadline:
            return access_reply(row, base)
        await asyncio.sleep(1)
        await asyncio.to_thread(hub.access.expire)


@app.get('/api/access-requests', dependencies=[Depends(session_only)])
def access_requests():
    """The requests waiting for the dashboard to answer."""
    return hub.access.waiting()


@app.post('/api/access-requests/{request_id}/approve', dependencies=[Depends(session_only)])
def approve_access(request_id: str, scope: str = Body(embed=True)):
    if scope not in auth.SCOPES:
        raise HTTPException(400, f'scope is one of {", ".join(auth.SCOPES)}')
    if not hub.access.approve(request_id, scope):
        raise HTTPException(409, 'That request was answered already, or it lapsed')
    return {'ok': True}


@app.post('/api/access-requests/{request_id}/deny', dependencies=[Depends(session_only)])
def deny_access(request_id: str):
    hub.access.deny(request_id)
    return {'ok': True}


@app.post('/api/password', dependencies=[Depends(session_only)])
def change_password(request: Request, response: Response, current: str = Body(), new: str = Body()):
    if not auth.check_password(current, hub.db.get_setting('admin_password')):
        raise HTTPException(401, 'Current password is wrong')
    if len(new) < 8:
        raise HTTPException(400, 'Use at least 8 characters')
    hub.db.set_setting('admin_password', auth.hash_password(new))
    set_session(request, response)   # re-sign: the password change voided old sessions
    return {'ok': True}


# ---- Live preview: proxy MediaMTX's HLS so everything goes through one port ----
@app.get('/hls/{relative:path}', dependencies=[Depends(admin)])
async def hls(relative: str, request: Request):
    if not re.match(r'^(spoke|preview)/[A-Za-z0-9]+/', relative):
        raise HTTPException(404)
    url = f'http://127.0.0.1:{settings.MTX_HLS_PORT}/{relative}'
    # follow_redirects: MediaMTX answers a fresh playlist request with a cookie-check
    # redirect; resolving it here keeps browsers' players out of it
    upstream = await hub.http.send(hub.http.build_request('GET', url, params=request.query_params),
                                   stream=True, follow_redirects=True)
    headers = {'Cache-Control': upstream.headers.get('cache-control', 'no-cache')}
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers,
                             media_type=upstream.headers.get('content-type'),
                             background=BackgroundTask(upstream.aclose))


# ---- Updates (updates.py) ----
def busy_with():
    """What would make updating now a bad idea: '' when nothing would. A broadcast live or due within the
    hour, a spoke streaming, or work under way (renders, uploads to YouTube, transcripts)."""
    now = time.time()
    live = hub.db.one("SELECT title FROM events WHERE state = 'live' LIMIT 1")
    if live:
        return f"{live['title']} is live"
    soon = hub.db.one("SELECT title FROM events WHERE state = 'scheduled' AND start_at < ? ORDER BY start_at LIMIT 1",
                      now + updates.QUIET_FOR)
    if soon:
        return f"{soon['title']} starts within the hour"
    if hub.db.one("SELECT id FROM spokes WHERE mode != 'off' AND removed_at IS NULL LIMIT 1"):
        return 'a spoke is streaming'
    if hub.db.one("SELECT id FROM edits WHERE status IN ('queued', 'rendering', 'publishing') LIMIT 1") or \
            hub.db.one("SELECT id FROM shorts WHERE status IN ('queued', 'analyzing', 'rendering', 'publishing') LIMIT 1"):
        return 'a video is being made or published'
    if hub.db.one("SELECT event_id FROM transcripts WHERE status IN ('queued', 'running') LIMIT 1"):
        return 'a transcript is being made'
    return ''


def spoke_idle(spoke, status):
    """Whether a spoke can be updated now: not streaming or recording, and nothing of its due within the hour."""
    if spoke['mode'] != 'off' or status.get('mode') not in (None, 'off') or status.get('recording') \
            or status.get('direct') or status.get('uploading'):
        return False
    return not hub.db.one("SELECT id FROM events WHERE spoke_id = ? AND (state = 'live' OR (state = 'scheduled' "
                          "AND start_at < ?)) LIMIT 1", spoke['id'], time.time() + updates.QUIET_FOR)


def update_notice():
    """For the banner on every page: an update to install by hand, one waiting, or one just done."""
    info = hub.updates.info()
    behind = [s['name'] for s in hub.db.spokes() if hub.updates.spoke_view(s, True)['state'] in ('available', 'failed')]
    return {'version': info['version'], 'latest': info['latest'], 'available': info['available'], 'auto': info['auto'],
            'can_update_self': info['can_update_self'], 'applying': info['applying'], 'requested': info['requested'],
            'waiting_for': info['waiting_for'], 'updated': info['updated'], 'spokes_behind': behind}


@app.get('/api/updates', dependencies=[Depends(admin)])
def get_updates():
    spokes = [{'id': s['id'], 'name': s['name'], 'online': v['online'], 'update': v['update']}
              for s in hub.db.spokes() for v in [spoke_view(s)]]
    return {**hub.updates.info(), 'spokes': spokes}


@app.post('/api/updates/check', dependencies=[Depends(admin)])
def check_updates():
    hub.updates.check()
    return get_updates()


@app.put('/api/updates/auto', dependencies=[Depends(admin)])
def set_updates_auto(auto: bool = Body(embed=True)):
    hub.updates.set_auto(auto)
    return get_updates()


@app.post('/api/updates/apply', dependencies=[Depends(admin)])
def apply_update():
    """Update the hub now, or as soon as nothing's going on (then its spokes follow)."""
    try:
        hub.updates.request()
    except UpdateError as e:
        raise HTTPException(409, str(e))
    return get_updates()


@app.post('/api/spokes/{spoke_id}/update', dependencies=[Depends(admin)])
def update_spoke(spoke_id: str):
    """Bring a spoke up to the hub's version, as soon as it's idle."""
    if not hub.db.spoke(spoke_id):
        raise HTTPException(404, 'No such spoke')
    hub.updates.request_spoke(spoke_id)
    return get_updates()


@app.get('/api/spoke/update/{version}')
def spoke_update_file(version: str, spoke: dict = Depends(spoke_auth)):
    """The spoke of the hub's version, for a spoke that was offered it."""
    bundle = hub.updates.bundle() if version == hub.updates.version else None
    if not bundle:
        raise HTTPException(404, 'This hub has no spoke of that version')
    return FileResponse(bundle['path'], media_type='application/gzip')


# ---- Spoke API ----
@app.post('/api/spoke/pair')
def pair(request: Request, data: dict = Body()):
    ip = request.client.host
    now = time.time()
    recent = [t for t in hub.pair_failures.get(ip, []) if now - t < 600]
    if len(recent) >= 10:
        raise HTTPException(429, 'Too many attempts; try again in 10 minutes')
    code = str(data.get('code', '')).strip()
    if hub.pairing.get(code, 0) < now:
        hub.pair_failures[ip] = recent + [now]
        raise HTTPException(403, 'That code is wrong or expired')
    del hub.pairing[code]
    spoke = new_spoke(str(data.get('name') or 'TouchStream'))
    return {'spoke_id': spoke['id'], 'token': spoke['token']}


@app.post('/api/spoke/heartbeat')
def heartbeat(request: Request, status: dict = Body(), spoke: dict = Depends(spoke_auth)):
    hub.db.run('UPDATE spokes SET last_seen = ?, last_ip = ?, status = ?, commands = ? WHERE id = ?', time.time(),
               request.client.host if request.client else None, json.dumps(status)[:8000], '[]', spoke['id'])
    # Test pattern can be switched from either side: the latest change wins
    if float(status.get('test_pattern_at') or 0) > spoke['test_pattern_at']:
        spoke['test_pattern'], spoke['test_pattern_at'] = bool(status.get('test_pattern')), float(status['test_pattern_at'])
        hub.db.run('UPDATE spokes SET test_pattern = ?, test_pattern_at = ? WHERE id = ?',
                   int(spoke['test_pattern']), spoke['test_pattern_at'], spoke['id'])
    stream = None
    if spoke['mode'] != 'off':
        path = f"{'spoke' if spoke['mode'] == 'live' else 'preview'}/{spoke['id']}"
        stream = {'host': public_host(request), 'port': settings.SRT_PORT, 'latency_ms': 1000,
                  'streamid': f"publish:{path}:{spoke['id']}:{spoke['stream_key']}",
                  'bitrate_kbps': spoke['bitrate_kbps']}
    return {'name': spoke['name'], 'location': spoke['location'], 'mode': spoke['mode'],
            'record_local': spoke['record_local'] and spoke['mode'] == 'live',
            'stream': stream, 'commands': spoke['commands'],
            'test_pattern': spoke['test_pattern'], 'test_pattern_at': spoke['test_pattern_at'],
            # For running on its own if the hub goes down
            'bitrate_kbps': spoke['bitrate_kbps'], 'record_local_pref': spoke['record_local'],
            'framerate': spoke['framerate'], 'time_zone': spoke['time_zone'],
            'update': hub.updates.spoke_offer(spoke, status, spoke_idle(spoke, status)),
            'viewers': hub.stats.watching_on(spoke['id']),   # watching on YouTube, while it's live: for its screen
            **spoke_schedule(spoke)}


def spoke_schedule(spoke):
    """The spoke's own copy of what it should broadcast, so it can go direct if the hub is down."""
    def target(dest):
        return {'url': dest['url'], 'key': dest['stream_key'], 'name': dest['name']}
    events = []
    for e in hub.db.all("SELECT * FROM events WHERE spoke_id = ? AND state IN ('scheduled', 'live') "
                        "AND start_at < ? ORDER BY start_at", spoke['id'], time.time() + SCHEDULE_DAYS * 86400):
        dests = [hub.db.one('SELECT * FROM destinations WHERE id = ?', t['destination_id'])
                 for t in hub.broadcasts.targets(e['id']) if t['destination_id'] and t['broadcast_id']]
        events.append({'id': e['id'], 'title': e['title'], 'start_at': e['start_at'], 'end_at': e['end_at'],
                       'auto_start': bool(e['auto_start']), 'state': e['state'],
                       'targets': [target(d) for d in dests if d]})
    keyed = [target(d) for d in hub.db.destinations(spoke['id']) if not d['channel_id'] and d['enabled']]
    return {'schedule': events, 'direct_destinations': keyed}


@app.post('/api/spoke/mode')
def spoke_mode(mode: str = Body(embed=True), spoke: dict = Depends(spoke_auth)):
    """The spoke's own Go live and End buttons: {"mode": "live" | "off"}, as from the dashboard."""
    if mode not in ('live', 'off'):
        raise HTTPException(400, 'Mode is live or off')
    change_mode(spoke, mode)
    return {'mode': mode}


@app.put('/api/spoke/recordings/{session}/{name}')
async def upload_part(session: str, name: str, request: Request, spoke: dict = Depends(spoke_auth)):
    target = recordings.upload_path(spoke['id'], session, name)
    if not target:
        raise HTTPException(400, 'Bad file name')
    recordings.note_session(spoke['id'], session, started_at=request.headers.get('X-Session-Start'))
    size = int(request.headers.get('content-length', 0))
    if target.exists() and target.stat().st_size == size:
        return {'ok': True, 'skipped': True}
    partial = target.with_suffix('.part')
    with open(partial, 'wb') as f:
        async for chunk in request.stream():
            f.write(chunk)
    if partial.stat().st_size != size:
        partial.unlink()
        raise HTTPException(400, 'Upload was cut short')
    os.replace(partial, target)
    return {'ok': True}


@app.post('/api/spoke/recordings/{session}/complete')
def complete_session(session: str, times: dict = Body(default={}), spoke: dict = Depends(spoke_auth)):
    recordings.note_session(spoke['id'], session, started_at=times.get('started_at'), ended_at=times.get('ended_at'))
    threading.Thread(target=recordings.merge_session, args=(spoke['id'], session), daemon=True).start()
    return {'ok': True}


# ---- MediaMTX asks us who may publish and read ----
@app.post('/api/internal/mediamtx-auth')
async def mediamtx_auth(request: Request):
    if request.client.host not in LOOPBACK:
        raise HTTPException(403)
    req = await request.json()
    action, path = req.get('action'), req.get('path', '')
    if action in ('read', 'playback'):
        # Only the hub itself reads: restreamers (RTSP) and the dashboard preview proxy (HLS)
        if req.get('ip') in LOOPBACK:
            return {'ok': True}
    elif action == 'publish':
        match = PUBLISH_PATH.match(path)
        spoke = hub.db.spoke(match.group(2)) if match else None
        if spoke and req.get('user') == spoke['id'] and secrets.compare_digest(
                req.get('password') or '', spoke['stream_key']):
            return {'ok': True}
    return JSONResponse({'ok': False}, status_code=401)


# ---- MCP, for AI agents (after the routes: its tools call them) ----
from . import mcp_server  # noqa: E402

mcp_server.mount(app)
