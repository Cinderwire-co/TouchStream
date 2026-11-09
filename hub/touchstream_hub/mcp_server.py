"""The hub as an MCP server (at /mcp), so AI agents can run it: Claude, ChatGPT, n8n, local models

A few plain tools rather than one per API route: look at the hub, list and schedule broadcasts
(once or repeating), change or delete them, go live and end, read a transcript, edit the video
and publish it, and make vertical shorts. Each goes through the same code as the dashboard, so the same rules apply.
Agents connect with an API token from Settings > API access; a 'read' token can only use the
tools that look.

Times are the hub computer's local time, as ISO 8601 ("2026-11-08T10:00"); an offset works too.
"""
import asyncio
import contextvars
import json
import re
from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import app as hub_app, auth
from .editor import keep_ranges
from .shorts import moment

DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october',
          'november', 'december']
token_scope = contextvars.ContextVar('token_scope', default='read')

INSTRUCTIONS = """TouchStream runs live broadcasts: spokes (small capture devices by each camera) send video to this
hub, which streams it to YouTube on a schedule, records it, and keeps a folder per broadcast.

Times are the hub computer's local time, ISO 8601 ("2026-11-08T10:00"). Start with hub_overview to see the spokes,
channels and what's next; spoke and channel ids come from there. Broadcast ids come from list_broadcasts: a number
for a broadcast, or "series:<id>:<date>" for a planned date of a repeating one.

How things ripple:
- Scheduling, changing or deleting an upcoming broadcast changes it on YouTube too. Deleting an aired one never
  deletes YouTube's video; with delete_recordings it deletes the hub's recordings, edit and transcript.
- A repeating broadcast makes each date's broadcast a week ahead; later dates are planned. Changing one date
  (apply_to "this") detaches it from the series; "all_upcoming" changes the series and every date not changed
  on its own.
- A broadcast has one edit, always cut from an original recording; editing again replaces it. It can have a brand
  (intro, outro, watermark), a color look, transitions on its cuts and titles. When they're in the video editor, use
  edit_video with render=False so the change shows on their timeline. publish_video
  uploads it as a new YouTube video (YouTube can't swap footage) and can make the live video private. YouTube
  allows about 6 uploads a day.
- make_short cuts a vertical short (up to 3 minutes) from one moment: find the moment with get_transcript.
  Its start and end widen to whole sentences. A broadcast can
  have many shorts; publish_short uploads one as a YouTube Short.
- Scheduled broadcasts go live and end by themselves; go_live and end_live are for doing it now.
Ask before deleting things or publishing, unless you were told to."""

server = MCPServer(name='TouchStream Hub', instructions=INSTRUCTIONS, website_url='https://cinderwire.co')
READ = ToolAnnotations(readOnlyHint=True)
CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


COLOR_PRESETS = {'natural': {}, 'warm': {'warmth': 35, 'saturation': 10}, 'cool': {'warmth': -30},
                 'vivid': {'saturation': 35, 'contrast': 15}, 'black_and_white': {'saturation': -100, 'contrast': 12},
                 'faded': {'contrast': -25, 'saturation': -25, 'brightness': 8}}     # as the editor's presets


class TitleIn(BaseModel):
    text: str = Field(description='The main line, e.g. a name')
    sub: str | None = Field(None, description='A second line, e.g. a role or a place')
    style: Literal['lower_third', 'title', 'caption', 'corner'] = 'lower_third'
    at: float | str = Field(description='When it appears, in the edited video (seconds or m:ss)')
    seconds: float = Field(5, description='How long it shows')


# ---- Helpers ----
def _change():
    if token_scope.get() == 'read':
        raise ToolError("This hub's API token is read-only: ask for one with 'control' access to change things")


async def _call(fn, *args, **kwargs):
    """Run one of the hub's route functions off the event loop, turning its errors into tool errors."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except HTTPException as e:
        raise ToolError(str(e.detail))


def _when(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec='minutes') if t else None


def _epoch(text, name='start'):
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        raise ToolError(f'{name} must be an ISO 8601 time like 2026-11-08T10:00')


def _seconds(value, name):
    """12.5, "90", "1:30" or "1:02:03" -> seconds"""
    if value is None or isinstance(value, (int, float)):
        return value
    parts = str(value).strip().split(':')
    try:
        return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    except ValueError:
        raise ToolError(f'{name} must be seconds or h:mm:ss')


def _event(broadcast_id):
    event = hub_app.hub.broadcasts.event(int(broadcast_id)) if str(broadcast_id).isdigit() else None
    if not event:
        raise ToolError(f'No broadcast {broadcast_id}; list_broadcasts gives the ids')
    return event


def _planned(broadcast_id):
    """("series:3:2026-11-15") -> (3, "2026-11-15"), else None"""
    m = re.fullmatch(r'series:(\d+):(\d{4}-\d\d-\d\d)', str(broadcast_id))
    return (int(m.group(1)), m.group(2)) if m else None


def _brief(e, spokes):
    """A broadcast (or planned date) as an agent needs it."""
    series = e.get('series')
    item = {'id': e['id'], 'title': e['title'], 'start': _when(e.get('started_at') or e['start_at']),
            'end': _when(e.get('ended_at') or e.get('end_at')), 'state': e['state'],
            'spoke': spokes.get(e['spoke_id'], e['spoke_id']),
            'channels': [t['channel'] for t in e.get('targets', [])]}
    if series:
        rule = series['repeat']
        item['repeats'] = {'series_id': series['id'], 'days': [DAYS[d] for d in rule['days']], 'weeks': rule['weeks'],
                           'months': [MONTHS[m - 1] for m in rule['months']] or 'all', 'until': series['until_date'],
                           'changed_on_its_own': bool(e.get('detached'))}
    if e['state'] == 'ended':
        item['recordings'] = [c['source'] for c in e.get('recordings', []) if c.get('path')]
        item['edited'] = any(x['status'] == 'done' for x in e.get('edits', []))
        item['transcript'] = (e.get('transcript') or {}).get('status')
        item['watch'] = [t['watch_url'] for t in e.get('targets', []) if t.get('watch_url')]
    return item


def _spoke_names():
    return {s['id']: s['name'] for s in hub_app.hub.db.spokes(removed=True)}


class Repeat(BaseModel):
    """When a broadcast repeats."""
    days: list[Literal['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']] = Field(
        description='Days of the week it airs on (at least one)')
    weeks: Literal['every', 'other'] | list[Literal[1, 2, 3, 4, 'last']] = Field(
        'every', description='"every" week, every "other" week (counted from the first date), or weeks of the month '
                             'like [1, 3] or ["last"]')
    months: list[int] = Field(default_factory=list, description='Months it runs in, 1-12; empty means every month')
    until: str | None = Field(None, description='Last date, YYYY-MM-DD; none means it keeps going')
    add_date_to_title: bool = Field(True, description='Title each broadcast like "Sunday Service · November 8, 2026"')


def _rule(repeat):
    weeks = repeat.weeks if not isinstance(repeat.weeks, list) else [-1 if w == 'last' else w for w in repeat.weeks]
    return {'days': [DAYS.index(d) for d in repeat.days], 'weeks': weeks, 'months': repeat.months}


# ---- Looking ----
@server.tool(annotations=READ)
async def hub_overview() -> dict:
    """The hub at a glance: its spokes (id, online, live or not, temperature, storage), connected YouTube channels,
    what's live, the next broadcasts, and the hub's free space. Start here."""
    dash, info, events = await asyncio.gather(_call(hub_app.dashboard), _call(hub_app.overview),
                                              _call(hub_app.list_events, '', 0))
    spokes = {s['id']: s['name'] for s in dash['spokes']}
    upcoming = sorted(events['events'] + [{**p, 'id': f"series:{p['series_id']}:{p['date']}", 'state': 'planned'}
                                          for p in events['planned']], key=lambda e: e['start_at'])
    return {
        'spokes': [{'id': s['id'], 'name': s['name'], 'location': s['location'], 'online': s['online'], 'mode': s['mode'],
                    'temperature_c': (s['status'] or {}).get('temp'), 'free_bytes': ((s['status'] or {}).get('storage') or {}).get('free'),
                    'quality': f"1080p{s['framerate']} at {s['bitrate_kbps'] / 1000:g} Mbps"} for s in dash['spokes']],
        'channels': [{'id': c['id'], 'title': c['title']} for c in dash['channels']],
        'live': [_brief(e, spokes) for e in events['events'] if e['state'] == 'live'],
        'next': [_brief(e, spokes) for e in upcoming if e['state'] != 'live'][:5],
        'hub_free_bytes': info['storage']['free'], 'hub_time': _when(datetime.now().timestamp()),
    }


@server.tool(annotations=READ)
async def list_broadcasts(when: Literal['upcoming', 'past'] = 'upcoming', spoke_id: str | None = None,
                          limit: int = 20) -> list[dict]:
    """Broadcasts coming up (including planned dates of repeating ones) or that aired (newest first, the last 90 days),
    for one spoke or all."""
    events = await _call(hub_app.list_events, spoke_id or '', 90)
    spokes = _spoke_names()
    if when == 'past':
        items = [e for e in reversed(events['events']) if e['state'] == 'ended']
    else:
        items = sorted([e for e in events['events'] if e['state'] != 'ended']
                       + [{**p, 'id': f"series:{p['series_id']}:{p['date']}", 'state': 'planned'} for p in events['planned']],
                       key=lambda e: e['start_at'])
    return [_brief(e, spokes) for e in items[:max(1, min(limit, 100))]]


@server.tool(annotations=READ)
async def get_broadcast(broadcast_id: str) -> dict:
    """One broadcast in full: its details, YouTube links, recordings (with lengths, and the API paths to download each
    or just its sound as an MP3), edit and transcript."""
    event = _event(broadcast_id)
    data = await _call(hub_app.editor_source, event['id'])
    e = data['event']
    item = {**_brief(e, _spoke_names()), 'description': e['description'], 'privacy': e['privacy'],
            'latency': e['latency'], 'auto_start': e['auto_start'],
            'youtube': [{'channel': t['channel'], 'watch': t['watch_url'], 'error': t['error']} for t in e['targets']],
            'recordings': [{'kind': c['source'], 'seconds': round(c['duration']), 'download': f"/api/recordings/file/{c['path']}",
                            'audio': f"/api/recordings/audio/{c['path']}"} for c in data['copies']]}
    if data['edit']:
        edit = data['edit']
        item['edit'] = {'status': edit['status'], 'seconds': round(edit['duration'] or 0), 'cuts': edit['spec'],
                        'published': [{'channel': p['channel'], 'url': p['url']} for p in edit['published']], 'error': edit['error']}
    shorts = hub_app.hub.shorts.for_event(event['id'])
    if shorts:
        item['shorts'] = [{'short_id': x['id'], 'title': x['title'], 'status': x['status'], 'seconds': round(x['duration'] or 0),
                           'from': round(x['spec']['start']), 'published': [p['url'] for p in x['published']],
                           'error': x['error']} for x in shorts]
    return item


@server.tool(annotations=READ)
async def get_transcript(broadcast_id: str, search: str | None = None) -> dict:
    """What was said in a broadcast, line by line with times ("[0:12:05] ..."), to find where to cut or what to
    quote. search keeps only lines containing it."""
    event = _event(broadcast_id)
    transcript = await _call(hub_app.hub.transcriber.transcript, event['id'])
    if not transcript or transcript['status'] != 'done':
        return {'status': (transcript or {}).get('status', 'none'),
                'note': 'Not transcribed yet: the hub transcribes after a broadcast when that is switched on in Settings'}
    def stamp(s):
        s = int(s)
        return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'
    lines = [f"[{stamp(s['start'])}] {s['text'].strip()}" for s in transcript['segments']
             if not search or search.lower() in s['text'].lower()]
    return {'status': 'done', 'language': transcript['language'], 'lines': lines[:2000]}


# ---- Scheduling ----
@server.tool(annotations=CHANGE)
async def schedule_broadcast(title: str, start: str, spoke_id: str | None = None, end: str | None = None,
                             description: str = '', channel_ids: list[int] | None = None,
                             privacy: Literal['public', 'unlisted', 'private'] = 'public',
                             repeat: Repeat | None = None) -> dict:
    """Schedule a broadcast, once or repeating. It's created on YouTube too, and goes live and ends by itself.
    spoke_id defaults to the only spoke; channel_ids to every connected channel (none connected: record only)."""
    _change()
    spokes = hub_app.hub.db.spokes()
    if not spoke_id:
        if len(spokes) != 1:
            raise ToolError('Say which spoke: ' + ', '.join(f"{s['id']} ({s['name']})" for s in spokes))
        spoke_id = spokes[0]['id']
    begins = _epoch(start)
    ends = _epoch(end, 'end') if end else None
    if ends and ends < begins:
        raise ToolError('end is before start')
    if channel_ids is None:
        channel_ids = [c['id'] for c in hub_app.channel_list()]
    common = {'spoke_id': spoke_id, 'description': description, 'privacy': privacy, 'channel_ids': channel_ids}
    if not repeat:
        e = await _call(hub_app.create_event, {**common, 'title': title, 'start_at': begins, 'end_at': ends})
        return {'scheduled': _brief(e, _spoke_names())}
    first = datetime.fromtimestamp(begins)
    s = await _call(hub_app.create_series, {
        **common, 'title': title, 'add_date': repeat.add_date_to_title, 'repeat': _rule(repeat),
        'first_date': first.date().isoformat(), 'start_minutes': first.hour * 60 + first.minute,
        'length_minutes': round((ends - begins) / 60) if ends else None, 'until_date': repeat.until, 'timezone': ''})
    coming = await list_broadcasts('upcoming', spoke_id, 50)
    return {'series_id': s['id'], 'next': [b for b in coming if (b.get('repeats') or {}).get('series_id') == s['id']][:4]}


@server.tool(annotations=CHANGE)
async def update_broadcast(broadcast_id: str, title: str | None = None, description: str | None = None,
                           start: str | None = None, end: str | None = None,
                           privacy: Literal['public', 'unlisted', 'private'] | None = None,
                           channel_ids: list[int] | None = None,
                           apply_to: Literal['this', 'all_upcoming'] = 'this') -> dict:
    """Change an upcoming broadcast (YouTube follows). For a repeating one, apply_to "this" changes just this date;
    "all_upcoming" changes the series (title, description, time of day, privacy, channels) and every upcoming date
    not changed on its own."""
    _change()
    changes = {k: v for k, v in (('title', title), ('description', description), ('privacy', privacy),
                                 ('channel_ids', channel_ids)) if v is not None}
    planned = _planned(broadcast_id)
    event = None if planned else _event(broadcast_id)
    series = hub_app.hub.series.get(planned[0] if planned else event['series_id']) if planned or event['series_id'] else None
    if apply_to == 'all_upcoming':
        if not series:
            raise ToolError("That broadcast doesn't repeat: use apply_to 'this'")
        if start:
            t = datetime.fromtimestamp(_epoch(start))
            changes['start_minutes'] = t.hour * 60 + t.minute
            if end:
                changes['length_minutes'] = round((_epoch(end, 'end') - _epoch(start)) / 60)
        await _call(hub_app.update_series, series['id'], {**changes, **({'attach': event['id']} if event else {})})
        return {'updated': 'all upcoming', 'next': [b for b in await list_broadcasts('upcoming', series['spoke_id'], 50)
                                                    if (b.get('repeats') or {}).get('series_id') == series['id']][:4]}
    if start:
        changes['start_at'] = _epoch(start)
    if end:
        changes['end_at'] = _epoch(end, 'end')
    if planned:
        base = hub_app.hub.series.event_fields(series, planned[1])
        e = await _call(hub_app.create_series_date, series['id'], planned[1], {**base, **changes})
    else:
        e = await _call(hub_app.update_event, event['id'], changes)
    return {'updated': _brief(e, _spoke_names())}


@server.tool(annotations=DELETE)
async def delete_broadcast(broadcast_id: str, apply_to: Literal['this', 'all_upcoming'] = 'this',
                           delete_recordings: bool = False) -> dict:
    """Delete a broadcast. Upcoming: removed from YouTube too; apply_to "all_upcoming" ends a repeating series.
    Aired: YouTube's video is never touched; delete_recordings also deletes the hub's recordings, edit and transcript."""
    _change()
    planned = _planned(broadcast_id)
    if planned:
        if apply_to == 'all_upcoming':
            await _call(hub_app.delete_series, planned[0])
            return {'deleted': 'the series and its upcoming broadcasts'}
        await _call(hub_app.skip_series_date, planned[0], day=planned[1])
        return {'deleted': f'the {planned[1]} date of the series'}
    event = _event(broadcast_id)
    if apply_to == 'all_upcoming' and event['series_id']:
        await _call(hub_app.delete_series, event['series_id'])
        return {'deleted': 'the series and its upcoming broadcasts'}
    await _call(hub_app.delete_event, event['id'], recordings_too=delete_recordings)
    return {'deleted': event['title'], 'recordings_deleted': delete_recordings and event['state'] == 'ended'}


# ---- Live ----
@server.tool(annotations=CHANGE)
async def go_live(broadcast_id: str | None = None, spoke_id: str | None = None) -> dict:
    """Go live now: a scheduled broadcast (by id) early, or a spoke without one (the hub makes a broadcast for it)."""
    _change()
    if broadcast_id:
        e = await _call(hub_app.start_event, _event(broadcast_id)['id'])
        return {'live': _brief(e, _spoke_names())}
    if not spoke_id:
        raise ToolError('Give a broadcast_id or a spoke_id')
    s = await _call(hub_app.set_mode, spoke_id, 'live')
    return {'live': s['name']}


@server.tool(annotations=CHANGE)
async def end_live(broadcast_id: str | None = None, spoke_id: str | None = None) -> dict:
    """End a live broadcast (by id), or whatever a spoke is broadcasting."""
    _change()
    if broadcast_id:
        e = await _call(hub_app.end_event, _event(broadcast_id)['id'])
        return {'ended': _brief(e, _spoke_names())}
    if not spoke_id:
        raise ToolError('Give a broadcast_id or a spoke_id')
    s = await _call(hub_app.set_mode, spoke_id, 'off')
    return {'ended': s['name']}


# ---- After a broadcast ----
@server.tool(annotations=CHANGE)
async def edit_video(broadcast_id: str, start: float | str | None = None, end: float | str | None = None,
                     cut_out: list[list[float | str]] | None = None, fade_in: float = 1, fade_out: float = 2,
                     transition: Literal['cut', 'dissolve', 'dip_black', 'dip_white', 'wipe', 'slide', 'circle'] = 'cut',
                     color: Literal['natural', 'warm', 'cool', 'vivid', 'black_and_white', 'faded'] = 'natural',
                     brand: str | None = None, titles: list[TitleIn] | None = None, render: bool = True,
                     recording: Literal['spoke', 'hub'] | None = None) -> dict:
    """Make the broadcast's edited video from its original recording: keep start to end (seconds or h:mm:ss into
    the recording) minus each [from, to] in cut_out, with fades; a transition on every cut; a color look over the
    recording; a brand (by name: its intro and outro go at the ends, its watermark over the recording); and titles
    (each at a time in the edited video). Replaces the earlier edit. render=False puts it on the editor's timeline
    as a draft instead, for them to look at and render (do that when they're in the editor). Otherwise it renders in
    the background: get_broadcast shows when it's done. recording picks the copy (default: the spoke's)."""
    _change()
    event = _event(broadcast_id)
    data = await _call(hub_app.editor_source, event['id'])
    copies = data['copies']
    if not copies:
        raise ToolError('That broadcast has no recording to edit')
    copy = next((c for c in copies if c['source'] == (recording or 'spoke')), copies[0])
    ranges = keep_ranges({'in': _seconds(start, 'start') or 0, 'out': _seconds(end, 'end'),
                          'cuts': [[_seconds(a, 'cut_out'), _seconds(b, 'cut_out')] for a, b in (cut_out or [])]},
                         copy['duration'])
    if not ranges:
        raise ToolError('Nothing of the recording is left: check start, end and cut_out')
    look = None
    if brand:
        look = next((b for b in data['brands'] if b['name'].lower() == brand.lower()), None)
        if not look:
            raise ToolError(f"No brand called {brand}; there's " + (', '.join(b['name'] for b in data['brands']) or 'none yet'))
    media = {m['id']: m for m in data['media']}
    pieces = []                     # back to back: (media id or None, start, end)
    if look and look['intro'] in media:
        m = media[look['intro']]
        pieces.append((m['id'], 0.0, m['duration'] if m['kind'] == 'video' else float(look['intro_seconds'])))
    pieces += [(None, a, b) for a, b in ranges]
    if look and look['outro'] in media:
        m = media[look['outro']]
        pieces.append((m['id'], 0.0, m['duration'] if m['kind'] == 'video' else float(look['outro_seconds'])))
    video, audio, at = [], [], 0.0
    for n, (mid, a, b) in enumerate(pieces):
        link = f'a{n}'
        clip = {'at': at, 'start': a, 'end': b, 'link': link, **({'media': mid} if mid else {})}
        video.append({**clip, 'transition': {'type': transition, 'duration': 0.5} if transition != 'cut' else None})
        if not mid or media[mid]['has_audio']:
            audio.append(clip)
        at += b - a
    spec = {'source': copy['path'], 'video': video, 'audio': audio, 'music': [],
            'titles': [{'text': t.text, 'sub': t.sub or '', 'style': t.style, 'start': _seconds(t.at, 'at'),
                        'end': _seconds(t.at, 'at') + t.seconds} for t in titles or []],
            'color': COLOR_PRESETS[color], 'brand': look['id'] if look else None, 'fade_in': fade_in, 'fade_out': fade_out}
    if not render:
        await _call(hub_app.save_edit_draft, event['id'], {**spec, 'by': 'assistant'})
        return {'draft': True, 'seconds': round(at), 'note': 'On their timeline in the editor, ready to look at and render'}
    edit = await _call(hub_app.create_edit, event['id'], spec)
    return {'rendering': True, 'seconds': round(edit['duration']), 'from': f"the {copy['source']} recording"}


@server.tool(annotations=CHANGE)
async def publish_video(broadcast_id: str, title: str | None = None, description: str | None = None,
                        privacy: Literal['public', 'unlisted', 'private'] = 'public',
                        original_video: Literal['private', 'unlisted', 'keep'] = 'private',
                        channel_ids: list[int] | None = None) -> dict:
    """Upload the broadcast's edited video to YouTube as a new video (title and description default to the
    broadcast's), and make the original live video private, unlisted, or keep it. An earlier upload of this edit
    is hidden the same way."""
    _change()
    event = _event(broadcast_id)
    edit = hub_app.hub.editor.current(event['id'])
    if not edit or edit['status'] != 'done':
        raise ToolError('Edit the video first (edit_video), and wait for it to finish rendering')
    targets = hub_app.hub.broadcasts.targets(event['id'])
    body = {'channel_ids': channel_ids or [t['channel_id'] for t in targets], 'title': title or event['title'],
            'description': event['description'] if description is None else description, 'privacy': privacy,
            'hide_original': original_video, 'hide_previous': original_video}
    await _call(hub_app.publish_edit, edit['id'], body)
    return {'publishing': True, 'note': 'Uploading in the background; get_broadcast shows the YouTube link when done'}



# ---- Shorts ----
@server.tool(annotations=CHANGE)
async def make_short(broadcast_id: str, start: float | str, end: float | str, title: str | None = None,
                     framing: Literal['fill', 'fit'] = 'fill',
                     captions: Literal['bold', 'marker', 'boxed', 'pop', 'clean', 'none'] = 'bold',
                     accent: Literal['ember', 'gold', 'lime', 'sky', 'pink', 'white'] = 'ember',
                     remove_pauses: bool = False, recording: Literal['spoke', 'hub'] | None = None) -> dict:
    """Make a vertical 9:16 short of one moment of a broadcast: start and end are seconds or h:mm:ss into the
    recording (as get_transcript shows), up to 3 minutes apart, and widen to whole sentences. framing
    'fill' follows the speaker, 'fit' shows the whole picture over a blurred copy. captions is the burned-in
    caption style; remove_pauses takes out pauses over 0.6 s. It renders in the background: get_broadcast
    shows when it's done."""
    _change()
    event = _event(broadcast_id)
    data = await _call(hub_app.list_shorts, event['id'])
    if not data['options']['available']:
        raise ToolError("This hub can't make shorts: it needs the transcription add-on")
    copies = data['copies']
    if not copies:
        raise ToolError('That broadcast has no recording yet')
    copy = next((c for c in copies if c['source'] == (recording or 'spoke')), copies[0])
    # Whole sentences: from the start of the one it begins in to the end of the one it ends in
    a, b = moment(data['sentences'] or [], _seconds(start, 'start'), _seconds(end, 'end'))
    body = {'title': title, 'source': copy['path'], 'start': a, 'end': min(b, copy['duration']), 'framing': framing,
            'style': captions, 'accent': accent, 'trim_pauses': remove_pauses}
    short = await _call(hub_app.create_short, event['id'], body)
    return {'short_id': short['id'], 'rendering': True, 'seconds': round(short['duration']),
            'captions': captions if data['sentences'] else 'none (the broadcast has no transcript)'}


@server.tool(annotations=CHANGE)
async def publish_short(short_id: int, title: str | None = None, description: str | None = None,
                        privacy: Literal['public', 'unlisted', 'private'] = 'public',
                        channel_ids: list[int] | None = None) -> dict:
    """Upload a finished short to YouTube as a Short (on the broadcast's channels unless given; the title
    defaults to the short's)."""
    _change()
    short = hub_app.hub.shorts.short(short_id)
    if not short:
        raise ToolError(f'No short {short_id}; get_broadcast lists its shorts')
    targets = hub_app.hub.broadcasts.targets(short['event_id'])
    channels = channel_ids or [t['channel_id'] for t in targets] or [c['id'] for c in hub_app.channel_list()][:1]
    body = {'channel_ids': channels, 'title': title or short['title'], 'description': description or '',
            'privacy': privacy}
    await _call(hub_app.publish_short, short_id, body)
    return {'publishing': True, 'note': 'Uploading in the background; get_broadcast shows the link when done'}

# ---- Serving it ----
class _Auth:
    """Only with an API token; the token's access (read / control / admin) decides what the tools may do."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get('headers') or [])
        bearer = headers.get(b'authorization', b'').decode().removeprefix('Bearer ').strip()
        row = await asyncio.to_thread(hub_app.hub.tokens.check, bearer) if bearer else None
        if not row:
            body = json.dumps({'error': 'Send an API token from Settings > API access as "Authorization: Bearer ts_..."'}).encode()
            await send({'type': 'http.response.start', 'status': 401,
                        'headers': [(b'content-type', b'application/json'), (b'www-authenticate', b'Bearer')]})
            await send({'type': 'http.response.body', 'body': body})
            return
        token_scope.set(row['scope'] if row['scope'] in auth.SCOPES else 'read')
        await self.app(scope, receive, send)


def mount(app):
    """Serve the MCP endpoint at /mcp on the hub's own port. The hub's lifespan runs server.session_manager."""
    from starlette.routing import Route
    asgi = server.streamable_http_app(
        streamable_http_path='/mcp', stateless_http=True, json_response=True,
        # Every request needs a token, so the DNS-rebinding guard (meant for open local servers) isn't needed,
        # and it would refuse the LAN addresses and domains the hub is reached at
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    route = asgi.routes[0]
    app.router.routes.append(Route('/mcp', endpoint=_Auth(route.endpoint), methods=['GET', 'POST', 'DELETE']))
