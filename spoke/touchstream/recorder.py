"""Local safety recordings: sessions on the spoke, uploaded to the hub as they're written

    ~/recordings/<session>/part-00000.ts ...   5-minute MPEG-TS segments
    ~/recordings/<session>/.uploaded           parts the hub has confirmed
    ~/recordings/<session>/.complete           recording finished
    ~/recordings/<session>/.merged             hub has joined the parts into one MP4

Parts upload while the stream is still running (all but the one being written),
so little is left to send when it ends. When the card runs low, the oldest
recordings are deleted to make room: ones the hub already has first, then ones
that only exist here. The recording in progress is never touched.

Recordings can also be downloaded straight to a phone: the touchscreen shows a
QR code with a short-lived link to the spoke's own web server. Phones need a
regular MP4 (they can't play the TS segments, or an MP4 streamed as it's made),
so opening a recording makes one in ~/.cache/touchstream/downloads first: about
a minute per hour of video. It's deleted when the link expires.
"""
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import hub

RECORDINGS_DIR = Path(os.environ.get('TOUCHSTREAM_RECORDINGS', Path.home() / 'recordings'))
MIN_FREE_BYTES = 4 * 1024 ** 3   # keep at least 4 GB free (about 45 minutes at 12 Mbps)
SCAN_SECONDS = 10
SHARE_SECONDS = 15 * 60          # how long a download link from the touchscreen works
SHARE_DIR = Path.home() / '.cache' / 'touchstream' / 'downloads'

_shares = {}                     # token -> (session name, expiry)
_prepared = {}                   # session name -> {'size': bytes it was made from, 'state', 'error'}


def sessions():
    """Recordings on this spoke, newest first."""
    out = []
    if not RECORDINGS_DIR.exists():
        return out
    for path in RECORDINGS_DIR.iterdir():
        parts = sorted(path.glob('part-*.ts')) if path.is_dir() else []
        if not parts:
            continue
        try:
            start = time.mktime(time.strptime(path.name, '%Y%m%d-%H%M%S'))
        except ValueError:
            continue
        complete = (path / '.complete').exists()
        end = (path / '.complete').stat().st_mtime if complete else max(p.stat().st_mtime for p in parts)
        out.append({'name': path.name, 'start': start, 'duration': max(0, end - start),
                    'size': sum(p.stat().st_size for p in parts), 'parts': [str(p) for p in parts],
                    'recording': not complete, 'on_hub': (path / '.merged').exists()})
    return sorted(out, key=lambda s: s['start'], reverse=True)


def delete_session(name):
    path = RECORDINGS_DIR / name
    if path.parent == RECORDINGS_DIR and (path / '.complete').exists():
        shutil.rmtree(path, ignore_errors=True)
        _share_file(name).unlink(missing_ok=True)
        _prepared.pop(name, None)
        return True
    return False


def share(name):
    """A download link token for one recording, valid for 15 minutes. Starts
    making the MP4 the link serves; share_state() says when it's ready."""
    now = time.time()
    for token in [t for t, (_, exp) in _shares.items() if exp < now]:
        del _shares[token]
    clean_shares()
    token = secrets.token_urlsafe(12)
    _shares[token] = (name, now + SHARE_SECONDS)
    session = next((s for s in sessions() if s['name'] == name), None)
    job = _prepared.get(name)
    if session and not (job and job['size'] == session['size'] and job['state'] != 'failed'):
        # New, or a recording still in progress that has grown since
        _prepared[name] = {'size': session['size'], 'state': 'preparing', 'error': None}
        threading.Thread(target=_prepare, args=(session,), daemon=True).start()
    return token


def _share_file(name):
    return SHARE_DIR / f'{name}.mp4'


def _prepare(session):
    job, target = _prepared[session['name']], _share_file(session['name'])
    partial = target.with_suffix('.part')
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    if free_bytes() < session['size'] + 512 * 1024 ** 2:
        job.update(state='failed', error='Not enough space on the card to prepare it')
        return
    # aac_adtstoasc: MP4 stores AAC without the per-frame headers TS carries
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                             '-i', 'concat:' + '|'.join(session['parts']), '-c', 'copy',
                             '-bsf:a', 'aac_adtstoasc', '-movflags', '+faststart', '-f', 'mp4', str(partial)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Preparing {session['name']} for download failed: {result.stderr.strip()}")
        partial.unlink(missing_ok=True)
        job.update(state='failed', error="Couldn't prepare this recording")
        return
    partial.replace(target)
    job['state'] = 'ready'


def share_state(name):
    """('preparing' | 'ready' | 'failed', error message) for a recording being shared."""
    job = _prepared.get(name)
    if job and job['state'] == 'ready' and not _share_file(name).exists():
        job.update(state='failed', error='The download copy was removed')
    return (job['state'], job['error']) if job else ('failed', None)


def shared_file(token, wait=600):
    """(session, MP4 path) for a download link, once its MP4 is ready; None if it expired or failed."""
    entry = _shares.get(token)
    if not entry or entry[1] < time.time():
        return None
    deadline = time.time() + wait
    while share_state(entry[0])[0] == 'preparing' and time.time() < deadline:
        time.sleep(0.5)
    session = next((s for s in sessions() if s['name'] == entry[0]), None)
    if not session or share_state(entry[0])[0] != 'ready':
        return None
    return session, _share_file(entry[0])


def clean_shares():
    """Delete download copies no live link points at. A phone still downloading
    one keeps its open file, so this never cuts a download short."""
    if not SHARE_DIR.exists():
        return
    now = time.time()
    live = {name for name, exp in _shares.values() if exp >= now}
    for path in SHARE_DIR.iterdir():
        name = path.name.split('.')[0]
        if name not in live and (_prepared.get(name) or {}).get('state') != 'preparing':
            path.unlink(missing_ok=True)
            _prepared.pop(name, None)


_storage = {'at': 0}


def storage():
    """The card's space and the recordings on it, for the hub (worked out at most every 30 s)."""
    if time.time() - _storage['at'] > 30:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        disk = shutil.disk_usage(RECORDINGS_DIR)
        found = sessions()
        _storage.update(at=time.time(), free=disk.free, total=disk.total, recordings=len(found),
                        recordings_bytes=sum(r['size'] for r in found),
                        only_here=sum(1 for r in found if not r['on_hub'] and not r['recording']))
    return {k: v for k, v in _storage.items() if k != 'at'}


def free_bytes():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(RECORDINGS_DIR).free


def new_session():
    path = RECORDINGS_DIR / time.strftime('%Y%m%d-%H%M%S')
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def finish_session(path):
    if path and os.path.isdir(path):
        Path(path, '.complete').touch()


def finish_stale_sessions():
    """At startup: sessions left open by a crash or power cut are as complete as they'll get."""
    if RECORDINGS_DIR.exists():
        for session in RECORDINGS_DIR.iterdir():
            if session.is_dir() and not (session / '.complete').exists():
                finish_session(session)


class Uploader:
    def __init__(self):
        self.status = None   # e.g. '2 of 5' while uploading, for the hub dashboard

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                url, headers = hub.credentials()
                if url and RECORDINGS_DIR.exists():
                    for session in sorted(p for p in RECORDINGS_DIR.iterdir() if p.is_dir()):
                        self._upload_session(url, headers, session)
                self._prune()
                clean_shares()
            except Exception as e:
                print(f"Upload error: {e}")
            self.status = None
            time.sleep(SCAN_SECONDS)

    def _upload_session(self, url, headers, session):
        if (session / '.merged').exists():
            return
        done_file = session / '.uploaded'
        uploaded = set(done_file.read_text().split()) if done_file.exists() else set()
        complete = (session / '.complete').exists()
        parts = sorted(session.glob('part-*.ts'))
        if not complete:
            parts = parts[:-1]   # the newest part is still being written
        todo = [p for p in parts if p.name not in uploaded]
        # Session names are the spoke's local start time; send the exact moment so the
        # hub can match this copy to its broadcast whatever its own time zone
        started_at = time.mktime(time.strptime(session.name, '%Y%m%d-%H%M%S'))
        for i, part in enumerate(todo):
            self.status = f'{len(uploaded) + 1} of {len(uploaded) + len(todo) - i}'
            with open(part, 'rb') as f:
                hub.request('PUT', f'{url}/api/spoke/recordings/{session.name}/{part.name}', data=f,
                            headers={**headers, 'Content-Type': 'video/mp2t', 'X-Session-Start': str(started_at),
                                     'Content-Length': str(part.stat().st_size)}, timeout=60)
            uploaded.add(part.name)
            with open(done_file, 'a') as f:
                f.write(part.name + '\n')
        if complete and len(uploaded) >= len(parts):
            ended_at = (session / '.complete').stat().st_mtime
            hub.request('POST', f'{url}/api/spoke/recordings/{session.name}/complete',
                        {'started_at': started_at, 'ended_at': ended_at}, headers=headers)
            (session / '.merged').touch()
            print(f"Recording {session.name} uploaded to the hub")

    def _prune(self):
        """Delete the oldest finished recordings while the card is low on space:
        ones the hub already has first, then ones only on this spoke."""
        if free_bytes() >= MIN_FREE_BYTES:
            return
        finished = [s for s in sessions() if not s['recording']]
        for session in sorted(finished, key=lambda s: (not s['on_hub'], s['start'])):
            if free_bytes() >= MIN_FREE_BYTES:
                return
            print(f"Low on space: deleting recording {session['name']}"
                  f"{'' if session['on_hub'] else ' (not on the hub)'}")
            delete_session(session['name'])
