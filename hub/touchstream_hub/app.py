"""TouchStream Hub web app: dashboard API, spoke API and the MediaMTX auth hook"""
import asyncio
import ipaddress
import json
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import VERSION, auth, desktop, discovery, platforms, recordings, settings
from .assistant import Assistant, AssistantError
from .broadcasts import THUMBNAIL_MAX, THUMBNAIL_TYPES, Broadcasts
from .db import Database
from .editor import Editor, filmstrip, keep_ranges, probe_duration
from .library import Library, safe_name
from .mediamtx import MediaMTX
from .restream import Restreamer
from .retention import Retention
from .series import WEEKS, Series, zone
from .speedtest import SpeedTest
from .transcribe import MODELS, Transcriber, remap, to_srt, to_txt, to_vtt
from .youtube import YouTube, YouTubeError

STATIC = Path(__file__).parent / 'static'
ONLINE_FOR = 15            # seconds since a spoke's last heartbeat to count it online
PAIRING_MINUTES = 10
LOOPBACK = ('127.0.0.1', '::1')
PUBLISH_PATH = re.compile(r'^(spoke|preview)/([A-Za-z0-9]+)$')
SCHEDULE_DAYS = 7          # how far ahead spokes keep a copy of their schedule
FRAME_RATES = ('24', '25', '30', '50')


class Hub:
    """Everything the routes share, created at startup."""
    db: Database
    sessions: auth.Sessions
    tokens: auth.Tokens
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
    transcriber: Transcriber
    discovery: discovery.Discovery
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
    hub.editor = Editor(hub.db, hub.youtube, hub.library)
    hub.editor.start()
    hub.transcriber = Transcriber(hub.db, hub.library)
    hub.transcriber.start()
    hub.discovery = discovery.Discovery(hub.db)
    hub.discovery.start()
    hub.speedtest = SpeedTest(hub.db)
    hub.assistant = Assistant(hub.db)
    hub.http = httpx.AsyncClient(timeout=10)
    hub.pairing, hub.pair_failures = {}, {}
    print(f"TouchStream Hub {VERSION} - data in {settings.DATA_DIR}")
    async with mcp_server.server.session_manager.run():   # the MCP endpoint, for AI agents
        yield
    hub.restreamer.stop_all()
    hub.mtx.stop()
    await hub.http.aclose()


app = FastAPI(title='TouchStream Hub', version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url='/api/openapi.json')

# ---- The API description (/api/openapi.json), for scripts and AI agents ----
API_ABOUT = """Everything the TouchStream dashboard does, for scripts and AI agents. Send an API token from
Settings > API access as `Authorization: Bearer ts_...`. A 'read' token can GET; 'control' can also schedule,
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
- Spokes keep a week of their schedule and stream straight to YouTube if the hub is down."""
HIDDEN = re.compile(r'^/$|^/hls/|^/api/(spoke/|internal/|setup|login|logout|tokens|password|reveal|recordings-folder'
                    r'|recordings/filmstrip|youtube/connect|assistant)')
TAGS = [(r'^/api/events/[^/]+/(editor|edit|edits)|^/api/edits', 'Editing'), (r'^/api/events/[^/]+/transcript', 'Transcripts'),
        (r'^/api/(events|series)', 'Broadcasts'), (r'^/api/(spokes|spoke-defaults|discovered|pairing|destinations)', 'Spokes'),
        (r'^/api/(recordings|retention)', 'Recordings'), (r'^/api/youtube', 'YouTube'), (r'.', 'Hub')]


def describe_api():
    if not app.openapi_schema:
        schema = get_openapi(title='TouchStream Hub API', version=VERSION, description=API_ABOUT, routes=app.routes)
        schema['paths'] = {path: ops for path, ops in schema['paths'].items() if not HIDDEN.match(path)}
        for path, ops in schema['paths'].items():
            tag = next(name for pattern, name in TAGS if re.match(pattern, path))
            for op in ops.values():
                op['tags'], op['security'] = [tag], [{'token': []}]
        schema.setdefault('components', {})['securitySchemes'] = {
            'token': {'type': 'http', 'scheme': 'bearer', 'description': 'An API token from Settings > API access'}}
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = describe_api
app.mount('/static', StaticFiles(directory=STATIC), name='static')


# ---- Auth helpers ----
# What an API token needs: GETs need 'read', changes 'control', and these 'admin'
ADMIN_ONLY = re.compile(r'^/api/(password|settings|retention$|youtube/(client|connect|channels)|recordings-folder|reveal)')


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
        raise HTTPException(401, 'Sign in required, or send an API token as "Authorization: Bearer ts_..."')
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
    if not spoke or not secrets.compare_digest(token, spoke['token']):
        raise HTTPException(401, 'Unknown spoke')
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
    return FileResponse(STATIC / 'index.html')


@app.get('/api/state')
def state(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    signed = bool(token and hub.sessions.valid(token))
    return {'version': VERSION, 'setup_needed': not hub.db.get_setting('admin_password'), 'signed_in': signed,
            'local': is_local(request), 'platform': desktop.PLATFORM,
            # The assistant: on with an OpenRouter key; 'asked' once the key was offered at setup (or skipped)
            **({'ai': {'enabled': hub.assistant.enabled(), 'asked': bool(hub.db.get_setting('ai_asked'))}} if signed else {})}


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
        'test_pattern': spoke['test_pattern'], 'framerate': spoke['framerate'],
        'online': bool(spoke['last_seen'] and time.time() - spoke['last_seen'] < ONLINE_FOR),
        'last_seen': spoke['last_seen'], 'status': spoke['status'],
        'receiving': hub.mtx.ready(path) if spoke['mode'] != 'off' else False,
        'preview_path': path, 'destinations': dests,
        'obs_url': f"rtmp://<hub>:{settings.RTMP_PORT}/spoke/{spoke['id']}",
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
            'speed': hub.speedtest.state(),
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


def _spoke_fields(changes, fields=('name', 'location', *SPOKE_SHARED)):
    casts = {'name': str, 'location': str, 'bitrate_kbps': int, 'record_local': bool, 'framerate': str}
    out = {}
    for field in fields:
        if field in changes:
            value = casts[field](changes[field])
            if field == 'bitrate_kbps' and not 1000 <= value <= 25000:
                raise HTTPException(400, 'Bitrate must be 1000-25000 kbps')
            if field == 'framerate' and value not in FRAME_RATES:
                raise HTTPException(400, 'Frame rate must be 24, 25, 30 or 50 (a Pi 4 can capture up to 1080p50)')
            out[field] = int(value) if isinstance(value, bool) else value
    return out


@app.patch('/api/spokes/{spoke_id}', dependencies=[Depends(admin)])
def update_spoke(spoke_id: str, changes: dict = Body()):
    if not hub.db.spoke(spoke_id):
        raise HTTPException(404)
    for field, value in _spoke_fields(changes).items():
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
        hub.db.run(f'UPDATE spokes SET {field} = ?', value)
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
    with hub.broadcasts.lock:
        if mode == 'live' and not hub.broadcasts.live_event(spoke_id):
            hub.broadcasts.start_adhoc(spoke)
        elif mode != 'live' and spoke['mode'] == 'live':
            hub.broadcasts.end_spoke(spoke_id)
        hub.db.run('UPDATE spokes SET mode = ? WHERE id = ?', mode, spoke_id)
    return spoke_view(hub.db.spoke(spoke_id))


@app.post('/api/spokes/{spoke_id}/command', dependencies=[Depends(admin)])
def send_command(spoke_id: str, command: str = Body(embed=True)):
    spoke = hub.db.spoke(spoke_id)
    if not spoke:
        raise HTTPException(404)
    if command not in ('reboot', 'update', 'speedtest'):
        raise HTTPException(400, 'Unknown command')
    hub.db.run('UPDATE spokes SET commands = ? WHERE id = ?',
               json.dumps(spoke['commands'] + [command]), spoke_id)
    return {'ok': True}


@app.delete('/api/spokes/{spoke_id}', dependencies=[Depends(admin)])
def forget_spoke(spoke_id: str):
    # Recordings stay on disk; the spoke drops back to unadopted on its next heartbeat
    hub.db.run('DELETE FROM spokes WHERE id = ?', spoke_id)
    return {'ok': True}

@app.post('/api/spokes/{spoke_id}/test_pattern', dependencies=[Depends(admin)])
def set_test_pattern(spoke_id: str, on: bool = Body(embed=True)):
    # Timestamped: the spoke's own menu can switch it too, and the latest change wins
    hub.db.run('UPDATE spokes SET test_pattern = ?, test_pattern_at = ? WHERE id = ?', int(on), time.time(), spoke_id)
    return {'ok': True}


# ---- YouTube ----
def channel_list():
    return [{'id': c['id'], 'channel_id': c['channel_id'], 'title': c['title'], 'thumbnail': c['thumbnail'],
             'error': c['error']} for c in hub.db.all('SELECT * FROM youtube_channels ORDER BY created_at')]


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
    hub.db.run('DELETE FROM youtube_channels WHERE id = ?', channel_row)
    return {'ok': True}


# ---- Scheduled broadcasts ----
def event_view(event, copies=None):
    targets = []
    for t in hub.broadcasts.targets(event['id']):
        bid = t['broadcast_id']
        targets.append({'channel_id': t['channel_id'], 'channel': t['channel_title'],
                        'destination_id': t['destination_id'],
                        'channel_thumbnail': t['channel_thumbnail'], 'broadcast_id': bid, 'error': t['error'],
                        'watch_url': f'https://youtu.be/{bid}' if bid else None,
                        'studio_url': f'https://studio.youtube.com/video/{bid}/livestreaming' if bid else None})
    series = hub.series.get(event['series_id']) if event['series_id'] else None
    return {**event, 'made_for_kids': bool(event['made_for_kids']), 'auto_start': bool(event['auto_start']),
            'adhoc': bool(event['adhoc']), 'detached': bool(event['detached']),
            'series': hub.series.view(series) if series else None, 'targets': targets, 'recordings': copies or [],
            'edits': [edit_view(hub.editor.edit(e['id'])) for e in
                      hub.db.all('SELECT id FROM edits WHERE event_id = ? ORDER BY id', event['id'])],
            'transcript': hub.db.one('SELECT status, progress, edited, language FROM transcripts WHERE event_id = ?',
                                     event['id']),
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
    for sid in {e['spoke_id'] for e in rows} | ({spoke_id} if spoke_id else {s['id'] for s in hub.db.spokes()}):
        grouped, unmatched = hub.library.copies(sid, [e for e in rows if e['spoke_id'] == sid
                                                      and e['state'] != 'scheduled'])
        copies.update(grouped)
        other += [{**c, 'spoke_id': sid} for c in unmatched]
    return {'events': [event_view(e, copies.get(e['id'])) for e in rows],
            'planned': hub.series.planned(spoke_id),
            'other_recordings': sorted(other, key=lambda c: c['start'], reverse=True)}


@app.post('/api/events', dependencies=[Depends(admin)])
def create_event(data: dict = Body()):
    """Schedule a one-off broadcast. Body: spoke_id, title, description, start_at and end_at (epoch seconds;
    end_at optional), privacy (public | unlisted | private), latency (normal | low | ultraLow), made_for_kids,
    auto_start (go live at start_at), channel_ids (connected YouTube channels; none = record only).
    For a repeating one, POST /api/series."""
    spoke_id = str(data.get('spoke_id', ''))
    if not hub.db.spoke(spoke_id):
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
    with hub.broadcasts.lock:
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
    if not hub.db.spoke(spoke_id):
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
def edit_view(edit):
    return {k: edit[k] for k in ('id', 'event_id', 'source', 'spec', 'status', 'progress', 'duration',
                                 'output', 'error', 'published', 'created_at')}


def _event_copies(event):
    return [c for c in hub.library.event_copies(event) if c['path']]


@app.get('/api/events/{event_id}/editor', dependencies=[Depends(admin)])
def editor_source(event_id: int):
    event = hub.broadcasts.event(event_id)
    if not event:
        raise HTTPException(404)
    copies = [{**c, 'duration': probe_duration(hub.library.resolve(c['path']))} for c in _event_copies(event)]
    return {'event': event_view(event, copies), 'copies': copies, 'channels': channel_list(),
            'edit': edit_view(hub.editor.current(event_id)) if hub.editor.current(event_id) else None,
            'draft': json.loads(event['edit_draft']) if event['edit_draft'] else None}


@app.put('/api/events/{event_id}/edit-draft', dependencies=[Depends(admin)])
def save_edit_draft(event_id: int, data: dict = Body()):
    """Keep the editor's unsaved work on a broadcast's edit, to pick up later."""
    if not hub.broadcasts.event(event_id):
        raise HTTPException(404)
    draft = {'source': str(data.get('source', '')), 'spec': _edit_spec(data), 'saved_at': time.time()}
    hub.db.run('UPDATE events SET edit_draft = ? WHERE id = ?', json.dumps(draft), event_id)
    return draft


@app.delete('/api/events/{event_id}/edit-draft', dependencies=[Depends(admin)])
def discard_edit_draft(event_id: int):
    hub.db.run('UPDATE events SET edit_draft = NULL WHERE id = ?', event_id)
    return {'ok': True}


@app.get('/api/recordings/filmstrip/{relative:path}', dependencies=[Depends(admin)])
def recording_filmstrip(relative: str):
    path = hub.library.resolve(relative)
    if not path:
        raise HTTPException(404)
    return FileResponse(filmstrip(path), media_type='image/jpeg')


@app.post('/api/events/{event_id}/edits', dependencies=[Depends(admin)])
def create_edit(event_id: int, data: dict = Body()):
    """Render the broadcast's edit, replacing any earlier one. Body: source (a recording's path from GET
    /api/events/{id}/editor), in and out (seconds; out optional), cuts ([[start, end], ...] to remove),
    fade_in, fade_out (seconds, up to 10), dip (seconds of black at each cut, up to 2)."""
    event = hub.broadcasts.event(event_id)
    if not event or data.get('source') not in [c['path'] for c in _event_copies(event)]:
        raise HTTPException(400, 'Pick one of this broadcast\'s recordings')
    try:
        edit_id = hub.editor.create(event, data['source'], _edit_spec(data))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return edit_view(hub.editor.edit(edit_id))


def _edit_spec(data):
    try:
        return {'in': float(data.get('in') or 0), 'out': float(data['out']) if data.get('out') else None,
                'cuts': [[float(a), float(b)] for a, b in data.get('cuts', [])],
                'fade_in': min(10.0, float(data.get('fade_in') or 0)), 'fade_out': min(10.0, float(data.get('fade_out') or 0)),
                'dip': min(2.0, float(data.get('dip') or 0))}
    except (TypeError, ValueError):
        raise HTTPException(400, 'That edit has a time that isn\'t a number')


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
        segments = remap(segments, keep_ranges(version['spec'], probe_duration(source)))
        name += ' - edited'
    write, media_type = writers[fmt]
    return Response(write(segments), media_type=media_type,
                    headers={'Content-Disposition': f'attachment; filename="{name}.{fmt}"'})


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
            'data_dir': str(settings.DATA_DIR), 'password_from_env': bool(settings.ADMIN_PASSWORD),
            'recordings_folder': str(hub.library.root()), 'recordings_folder_default': str(settings.RECORDINGS_FOLDER),
            'local': is_local(request), 'platform': desktop.PLATFORM, 'docker': settings.IN_DOCKER,
            'transcription': {**hub.transcriber.options(), 'models': MODELS}}


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
            'framerate': spoke['framerate'],
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
