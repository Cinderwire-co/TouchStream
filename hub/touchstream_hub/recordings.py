"""Recordings in progress: what the hub is recording, and the safety copies spokes upload

    recordings/spoke/<id>/hub/<start time>.mp4         recorded by the hub as it arrived
    recordings/spoke/<id>/spoke/<session>/part-N.ts    a spoke's upload in progress
    recordings/spoke/<id>/spoke/<session>.mp4          ...merged once the spoke finishes
    recordings/spoke/<id>/spoke/<session>.json         when that session started and ended

Every live stream is a broadcast; its copies are matched to it by spoke and time,
which also works for a stream the spoke ran on its own while the hub was down.
Once a broadcast is over, its complete copies move to the recordings folder
(library.py); copies that match no broadcast stay here.
"""
import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import settings

SAFE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
HUB_FILE = re.compile(r'^(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)-\d+(?:_([+-]\d{4}))?\.mp4$')
MATCH_SLACK = 120          # seconds a copy may start before or end after its broadcast


def spoke_dir(spoke_id):
    return settings.RECORDINGS_DIR / 'spoke' / spoke_id


def resolve(relative):
    """Map a path from the UI to a file inside the recordings folder, or None."""
    root = settings.RECORDINGS_DIR.resolve()
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def delete(relative):
    path = resolve(relative)
    if not path:
        return False
    path.unlink()
    return True


def _hub_start(name):
    m = HUB_FILE.match(name)
    if not m:
        return None
    if m.group(2):
        return datetime.strptime(m.group(1) + m.group(2), '%Y-%m-%d_%H-%M-%S%z').timestamp()
    return datetime.strptime(m.group(1), '%Y-%m-%d_%H-%M-%S').timestamp()


def _sidecar(spoke_id, session):
    return spoke_dir(spoke_id) / 'spoke' / f'{session}.json'


def note_session(spoke_id, session, **times):
    """Remember when a spoke's recording session started/ended (sent by the spoke)."""
    if not SAFE_NAME.match(session):
        return
    path = _sidecar(spoke_id, session)
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update({k: float(v) for k, v in times.items() if v})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _session_times(spoke_id, session, last_write):
    path = _sidecar(spoke_id, session)
    data = json.loads(path.read_text()) if path.exists() else {}
    start = data.get('started_at')
    if not start:
        try:   # older spokes: the session name is its local start time
            start = time.mktime(time.strptime(session, '%Y%m%d-%H%M%S'))
        except ValueError:
            start = last_write
    return start, data.get('ended_at') or last_write


def _copy(path, source, start, end, in_progress=False, size=None):
    return {'path': path.relative_to(settings.RECORDINGS_DIR).as_posix() if path.is_file() else None,
            'name': path.name, 'source': source, 'size': size if size is not None else path.stat().st_size,
            'start': start, 'end': end, 'in_progress': in_progress}


def spoke_copies(spoke_id):
    """Every recording file of one spoke, with the time span it covers."""
    base, now, out = spoke_dir(spoke_id), time.time(), []
    for f in sorted((base / 'hub').glob('*.mp4')):
        mtime = f.stat().st_mtime
        out.append(_copy(f, 'hub', _hub_start(f.name) or mtime, mtime, in_progress=now - mtime < 15))
    uploads = base / 'spoke'
    for f in sorted(uploads.glob('*.mp4')):
        out.append(_copy(f, 'spoke', *_session_times(spoke_id, f.stem, f.stat().st_mtime)))
    for session in sorted(p for p in uploads.glob('*') if p.is_dir()):
        parts = list(session.glob('*.ts'))
        if parts:
            last = max(p.stat().st_mtime for p in parts)
            out.append(_copy(session, 'spoke', *_session_times(spoke_id, session.name, last),
                             in_progress=True, size=sum(p.stat().st_size for p in parts)))
    return out


def match(events, copies, now=None):
    """Assign each copy to the broadcast it overlaps most: ({event id: [copies]}, [unmatched]).
    One that overlaps none still joins a broadcast it sits wholly within MATCH_SLACK of (clocks a little
    apart, a spoke that started early); one that only brushes past, like a recording made on the
    touchscreen just before going live, stays on its own."""
    now = now or time.time()
    windows = []
    for e in events:
        start = e.get('started_at') or e['start_at']
        end = e.get('ended_at') or (now if e['state'] == 'live' else e.get('end_at') or start + 4 * 3600)
        windows.append((e['id'], start, end))
    grouped, unmatched = {}, []
    for c in copies:
        best, best_overlap = None, 0
        for event_id, start, end in windows:
            overlap = min(end, c['end']) - max(start, c['start'])
            if overlap > best_overlap or (overlap >= 0 and best is None and c['start'] == c['end']):
                best, best_overlap = event_id, overlap
        if best is None:
            best = next((event_id for event_id, start, end in windows
                         if start - MATCH_SLACK <= c['start'] and c['end'] <= end + MATCH_SLACK), None)
        if best is None:
            unmatched.append(c)
        else:
            grouped.setdefault(best, []).append(c)
    return grouped, unmatched


def upload_path(spoke_id, session, name):
    if not (SAFE_NAME.match(session) and SAFE_NAME.match(name) and name.endswith('.ts')):
        return None
    folder = spoke_dir(spoke_id) / 'spoke' / session
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def merge_session(spoke_id, session):
    """Join a finished upload's parts into one MP4 without re-encoding."""
    if not SAFE_NAME.match(session):
        return False
    folder = spoke_dir(spoke_id) / 'spoke' / session
    parts = sorted(folder.glob('*.ts'))
    if not parts:
        return False
    target = folder.parent / f'{session}.mp4'
    listing = folder / 'parts.txt'
    listing.write_text(''.join(f"file '{p.name}'\n" for p in parts))
    result = subprocess.run([settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-y',
                             '-f', 'concat', '-safe', '0', '-i', str(listing),
                             '-c', 'copy', '-movflags', '+faststart', str(target)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Merging {spoke_id}/{session} failed: {result.stderr.strip()}")
        listing.unlink(missing_ok=True)
        return False
    shutil.rmtree(folder)
    return True


def forget_session(spoke_id, copy):
    """Drop what's remembered about a spoke copy that has been filed or deleted."""
    _sidecar(spoke_id, Path(copy['name']).stem).unlink(missing_ok=True)


def delete_copies(spoke_id, copies):
    """Delete a broadcast's recordings: hub files, the spoke's copy and any half-finished upload."""
    for c in copies:
        if c['path']:
            delete(c['path'])
        if c['source'] == 'spoke':
            if c['in_progress']:
                shutil.rmtree(spoke_dir(spoke_id) / 'spoke' / c['name'], ignore_errors=True)
            forget_session(spoke_id, c)
