"""The recordings folder: a folder per broadcast with everything from it, named for people

    <recordings folder>/
        2026-11-04 Sunday Service/
            Sunday Service - hub recording.mp4      title, description, date and thumbnail inside
            Sunday Service - spoke recording.mp4
            Sunday Service - edited.mp4
            Sunday Service - short.mp4              vertical, for YouTube Shorts and reels
            Sunday Service - thumbnail.jpg
            Sunday Service.srt                      captions, and the same as plain text
            Sunday Service - transcript.txt
            Sunday Service - info.txt               when, where, which channels, links

Streams record into the hub's working folder while they're live (recordings.py).
Once a broadcast is over and a copy of it is complete, the copy is filed here:
rewritten as a regular MP4 with the broadcast's details embedded (no re-encoding)
and the working copy removed. Edits render straight into the folder.

The folder is whatever the user picks in Settings. Changing it only affects
what's filed afterwards; files already filed stay where they are and keep working.
"""
import glob
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import VERSION, recordings, settings
from .transcribe import to_srt, to_txt

FILE_EVERY = 30     # seconds between looks for copies ready to file
SETTLE = 60         # seconds after a broadcast ends before its copies are filed
KINDS = {'hub': 'hub recording', 'spoke': 'spoke recording', 'edit': 'edited', 'short': 'short'}
ABOUT = {'hub': 'recorded by the hub as it arrived', 'spoke': 'recorded on the spoke (no network gaps)',
         'edit': 'edited on the hub', 'short': 'a vertical short, made on the hub'}
WINDOWS_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def safe_name(title):
    """A broadcast title as a file name that works on Mac, Windows and Linux."""
    name = re.sub(r'(\d):(\d)', r'\1.\2', title)          # 4:30 PM -> 4.30 PM
    name = re.sub(r'\s*:\s*', ' - ', name)
    name = re.sub(r'[\\/]', '-', name)
    name = re.sub(r'[*?"<>|\x00-\x1f]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()[:80].strip().rstrip('.')
    if name.split('.')[0].upper() in WINDOWS_RESERVED:
        name = f'_{name}'
    return name or 'Broadcast'


def _suffix(kind, number):
    return f" - {KINDS[kind]}{f' {number}' if number > 1 else ''}.mp4"


def file_name(title, kind, number=1):
    return safe_name(title) + _suffix(kind, number)


def _unique(path):
    """`path`, or `path (2)`, `path (3)`... whichever doesn't exist yet."""
    n, candidate = 1, path
    while candidate.exists():
        n += 1
        candidate = path.with_name(f'{path.name} ({n})')
    return candidate


def _when(ts):
    t = datetime.fromtimestamp(ts)
    return f"{t:%A} {t.day} {t:%B %Y}, {t:%I:%M %p}".replace(', 0', ', ')


def _length(seconds):
    m = round(seconds / 60)
    return f'{m // 60} h {m % 60} min' if m >= 60 else f'{m} min'


class Library:
    def __init__(self, db):
        self.db = db
        self.lock = threading.RLock()   # filing, renaming and deleting one at a time

    def start(self):
        # Copies a crash left mid-filing go back to be filed again
        for held in settings.RECORDINGS_DIR.glob('spoke/*/*/*.filing'):
            held.rename(held.with_suffix(''))
        for event in self.db.all('SELECT id FROM events WHERE folder IS NOT NULL'):
            self.sync_names(event['id'])   # in case the naming rules changed
        threading.Thread(target=self._loop, daemon=True).start()

    def root(self):
        return Path(self.db.get_setting('recordings_folder') or settings.RECORDINGS_FOLDER)

    def event(self, event_id):
        return self.db.one('SELECT * FROM events WHERE id = ?', event_id)

    # ---- What the UI sees ----
    def resolve(self, ref):
        """The file behind a reference from the UI: a filed one ('library/<id>') or a working copy."""
        if ref and ref.startswith('library/'):
            row = self.db.one('SELECT path FROM library WHERE id = ?', ref[8:]) if ref[8:].isdigit() else None
            path = Path(row['path']) if row else None
            return path if path and path.is_file() else None
        return recordings.resolve(ref)

    def delete(self, ref):
        """Delete one recording or edit, filed or not."""
        if not (ref and ref.startswith('library/')):
            return recordings.delete(ref)
        path = self.resolve(ref)
        row = self.db.one('SELECT event_id FROM library WHERE id = ?', ref[8:])
        if not path or not row:
            return False
        with self.lock:
            path.unlink()
            self.db.run('DELETE FROM library WHERE id = ?', ref[8:])
            self.write_extras(row['event_id'])
        return True

    def filed_copies(self, event_ids):
        """{event id: [copies]} of the recordings filed for these broadcasts (not edits or shorts)."""
        out = {}
        for i in range(0, len(event_ids), 500):
            ids = event_ids[i:i + 500]
            rows = self.db.all(f"SELECT * FROM library WHERE kind IN ('hub', 'spoke') AND event_id IN ({','.join('?' * len(ids))}) "
                               'ORDER BY kind, number', *ids)
            for row in rows:
                path = Path(row['path'])
                if path.is_file():   # else someone moved or deleted it
                    out.setdefault(row['event_id'], []).append({
                        'path': f"library/{row['id']}", 'name': path.name, 'source': row['kind'],
                        'size': path.stat().st_size, 'start': row['start'], 'end': row['end'],
                        'in_progress': False, 'filed': True})
        return out

    def copies(self, spoke_id, events):
        """({event id: [copies]}, [working copies matching no broadcast]) for one spoke's broadcasts."""
        grouped, unmatched = recordings.match(events, recordings.spoke_copies(spoke_id))
        for event_id, filed in self.filed_copies([e['id'] for e in events]).items():
            grouped[event_id] = filed + grouped.get(event_id, [])
        return grouped, unmatched

    def event_copies(self, event):
        events = self.db.all("SELECT * FROM events WHERE spoke_id = ? AND state != 'scheduled'", event['spoke_id'])
        return self.copies(event['spoke_id'], events)[0].get(event['id'], [])

    # ---- Filing ----
    def _loop(self):
        while True:
            time.sleep(FILE_EVERY)
            try:
                self.file_ready()
            except Exception as e:
                print(f"Filing recordings failed: {e}")

    def _in_use(self):
        """Working copies an edit, short or transcription is about to read or reading now."""
        rows = self.db.all("SELECT source FROM edits WHERE status IN ('queued', 'rendering') UNION "
                           "SELECT json_extract(spec, '$.source') FROM shorts WHERE status IN ('queued', 'analyzing', 'rendering') "
                           "UNION SELECT source FROM transcripts WHERE status IN ('queued', 'running')")
        return {r['source'] for r in rows}

    def file_ready(self, now=None):
        """File every complete copy of every broadcast that ended a minute or more ago."""
        now = now or time.time()
        busy = self._in_use()
        for spoke in self.db.spokes():
            events = self.db.all("SELECT * FROM events WHERE spoke_id = ? AND state != 'scheduled'", spoke['id'])
            grouped, _ = recordings.match(events, recordings.spoke_copies(spoke['id']), now)
            for event in events:
                ended = event['ended_at'] or event['end_at'] or event['start_at']
                copies = grouped.get(event['id'])
                if not copies or event['state'] != 'ended' or now - ended < SETTLE:
                    continue
                # The hub's copies of one broadcast become one file (a stream that dropped
                # and came back makes several); each spoke session is its own
                hub_copies = [c for c in copies if c['source'] == 'hub']
                if hub_copies and all(c['path'] and not c['in_progress'] and c['path'] not in busy for c in hub_copies):
                    self.file(event, 'hub', hub_copies)
                for c in copies:
                    if c['source'] == 'spoke' and c['path'] and not c['in_progress'] and c['path'] not in busy:
                        self.file(event, 'spoke', [c])

    def file(self, event, kind, copies):
        """Move working copies of a broadcast into its folder as one MP4 with its details embedded."""
        copies = sorted(copies, key=lambda c: c['start'])
        with self.lock:
            # Set the working files aside first: if one is open (Windows won't rename it),
            # try again next time; and nothing else picks them up meanwhile
            held = []
            for c in copies:
                path = recordings.resolve(c['path'])
                try:
                    if not path:
                        raise FileNotFoundError(c['path'])
                    held.append((path, path.rename(path.with_name(path.name + '.filing'))))
                except OSError:
                    for original, moved in held:
                        moved.rename(original)
                    return None
            event = self.event(event['id'])
            folder = self.folder(event)
            number = (self.db.one('SELECT MAX(number) AS n FROM library WHERE event_id = ? AND kind = ?',
                                  event['id'], kind)['n'] or 0) + 1
            target = folder / file_name(event['title'], kind, number)
            partial = target.with_name(f'.{target.name}.part')
            with tempfile.TemporaryDirectory() as tmp:
                if len(held) > 1:
                    listing = Path(tmp) / 'parts.txt'
                    listing.write_text(''.join("file '{}'\n".format(str(m).replace("'", "'\\''")) for _, m in held))
                    source = ['-f', 'concat', '-safe', '0', '-i', str(listing)]
                else:
                    source = ['-i', str(held[0][1])]
                inputs, embed = self.embed_args(event, 1)
                result = subprocess.run(
                    [settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-y', *source, *inputs,
                     '-map', '0:v:0', '-map', '0:a?', '-c', 'copy', *embed, '-movflags', '+faststart',
                     '-f', 'mp4', str(partial)], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Filing {kind} recording of broadcast {event['id']} failed: {result.stderr.strip()}")
                partial.unlink(missing_ok=True)
                for original, moved in held:
                    moved.rename(original)
                return None
            partial.replace(target)
            ref = f"library/{self.add(event, kind, number, target, copies[0]['start'], copies[-1]['end'])}"
            for c, (_, moved) in zip(copies, held):
                # Edits and transcripts made from the working copy now point at the filed one
                self.db.run('UPDATE edits SET source = ? WHERE source = ?', ref, c['path'])
                self.db.run('UPDATE transcripts SET source = ? WHERE source = ?', ref, c['path'])
                moved.unlink(missing_ok=True)
                if kind == 'spoke':
                    recordings.forget_session(event['spoke_id'], c)
            self.write_extras(event['id'])
            print(f"Filed {target}")
            return ref

    def add(self, event, kind, number, path, start=None, end=None):
        return self.db.run('INSERT INTO library (event_id, kind, number, path, start, end, created_at) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?)', event['id'], kind, number, str(path), start, end, time.time())

    def new_target(self, event, kind):
        """(number, path) for a new edit or short of a broadcast, in its folder."""
        with self.lock:
            event = self.event(event['id'])
            number = (self.db.one('SELECT MAX(number) AS n FROM library WHERE event_id = ? AND kind = ?',
                                  event['id'], kind)['n'] or 0) + 1
            return number, self.folder(event) / file_name(event['title'], kind, number)

    def folder(self, event):
        """The broadcast's folder in the recordings folder, made on first use."""
        if event['folder'] and Path(event['folder']).is_dir():
            return Path(event['folder'])
        start = event['started_at'] or event['start_at']
        path = _unique(self.root() / f"{time.strftime('%Y-%m-%d', time.localtime(start))} {safe_name(event['title'])}")
        path.mkdir(parents=True)
        self.db.run('UPDATE events SET folder = ? WHERE id = ?', str(path), event['id'])
        event['folder'] = str(path)
        return path

    # ---- What's embedded and written alongside ----
    def _targets(self, event_id):
        return self.db.all('SELECT t.broadcast_id, c.title FROM event_targets t '
                           'JOIN youtube_channels c ON c.id = t.channel_id WHERE t.event_id = ?', event_id)

    def _where(self, event):
        spoke = self.db.spoke(event['spoke_id'])
        if not spoke:
            return ''
        return f"{spoke['name']} ({spoke['location']})" if spoke['location'] else spoke['name']

    def thumbnail(self, event):
        path = settings.DATA_DIR / 'thumbnails' / event['thumbnail'] if event['thumbnail'] else None
        return path if path and path.is_file() else None

    def tags(self, event):
        """MP4 metadata: what Finder, File Explorer, QuickTime and VLC show."""
        start = event['started_at'] or event['start_at']
        where = self._where(event)
        targets = self._targets(event['id'])
        links = [f"https://youtu.be/{t['broadcast_id']}" for t in targets if t['broadcast_id']]
        return {'title': event['title'],
                'artist': ', '.join(t['title'] for t in targets) or where,
                'album': where,
                'date': time.strftime('%Y-%m-%d', time.localtime(start)),
                'creation_time': datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'description': event['description'], 'synopsis': event['description'],
                'comment': '\n\n'.join(filter(None, [event['description'], ' '.join(links)]))}

    def embed_args(self, event, next_input):
        """FFmpeg (inputs, output options) that put the broadcast's details in an MP4,
        with its thumbnail as the cover picture. The video must be output stream v:0."""
        thumb = self.thumbnail(event)
        inputs, out = (['-i', str(thumb)] if thumb else []), []
        if thumb:
            out += ['-map', f'{next_input}:v', '-c:v:1', 'copy', '-disposition:v:1', 'attached_pic']
        for key, value in self.tags(event).items():
            out += ['-metadata', f'{key}={value}']
        return inputs, out

    def write_extras(self, event_id):
        """Thumbnail, captions, transcript and info file beside the recordings, kept current."""
        event = self.event(event_id)
        if not event or not event['folder'] or not Path(event['folder']).is_dir():
            return
        folder, name = Path(event['folder']), safe_name(event['title'])
        with self.lock:
            thumb = self.thumbnail(event)
            for old in folder.glob(f'{glob.escape(name)} - thumbnail.*'):
                if not thumb or old.suffix != thumb.suffix:
                    old.unlink()
            if thumb:
                shutil.copyfile(thumb, folder / f'{name} - thumbnail{thumb.suffix}')
            transcript = self.db.one("SELECT segments FROM transcripts WHERE event_id = ? AND status = 'done'", event_id)
            if transcript and transcript['segments']:
                segments = json.loads(transcript['segments'])
                (folder / f'{name}.srt').write_text(to_srt(segments), encoding='utf-8')
                (folder / f'{name} - transcript.txt').write_text(to_txt(segments), encoding='utf-8')
            (folder / f'{name} - info.txt').write_text(self.info(event), encoding='utf-8')

    def info(self, event):
        start = event['started_at'] or event['start_at']
        end = event['ended_at'] or event['end_at']
        lines = [event['title'], _when(start) + (f" ({_length(end - start)})" if end else ''), '']
        if self._where(event):
            lines.append(f'Spoke:    {self._where(event)}')
        for t in self._targets(event['id']):
            link = f" - https://youtu.be/{t['broadcast_id']}" if t['broadcast_id'] else ''
            lines.append(f"YouTube:  {t['title']}{link}")
        if event['description']:
            lines += ['', event['description']]
        files = self.db.all('SELECT * FROM library WHERE event_id = ? ORDER BY kind, number', event['id'])
        present = [(Path(f['path']).name, ABOUT[f['kind']]) for f in files if Path(f['path']).is_file()]
        if present:
            width = max(len(n) for n, _ in present)
            lines += ['', 'Files:'] + [f'  {n.ljust(width)}   {about}' for n, about in present]
        lines += ['', f'TouchStream Hub {VERSION}']
        return '\n'.join(lines) + '\n'

    # ---- When a broadcast changes ----
    def sync_names(self, event_id):
        """Rename a broadcast's folder and files to match its title (after it changed)."""
        event = self.event(event_id)
        if not event or not event['folder'] or not Path(event['folder']).is_dir():
            return
        with self.lock:
            folder = Path(event['folder'])
            start = event['started_at'] or event['start_at']
            wanted = f"{time.strftime('%Y-%m-%d', time.localtime(start))} {safe_name(event['title'])}"
            rows = self.db.all('SELECT * FROM library WHERE event_id = ?', event_id)
            mine = [r for r in rows if Path(r['path']).parent == folder]
            # The name the files have now, from a file whose name we know the end of
            old_name = next((Path(r['path']).name.removesuffix(_suffix(r['kind'], r['number'])) for r in mine
                             if Path(r['path']).name.endswith(_suffix(r['kind'], r['number']))), None)
            if not re.fullmatch(re.escape(wanted) + r'( \(\d+\))?', folder.name):
                try:
                    folder = folder.rename(_unique(folder.with_name(wanted)))
                except OSError as e:   # e.g. a file open on Windows
                    print(f"Couldn't rename {folder}: {e}")
                    return
                self.db.run('UPDATE events SET folder = ? WHERE id = ?', str(folder), event_id)
            for row in mine:
                current = folder / Path(row['path']).name
                renamed = folder / file_name(event['title'], row['kind'], row['number'])
                try:
                    if current != renamed:
                        current.rename(renamed)
                except OSError as e:
                    print(f"Couldn't rename {current}: {e}")
                    renamed = current
                self.db.run('UPDATE library SET path = ? WHERE id = ?', str(renamed), row['id'])
            if old_name and old_name != safe_name(event['title']):
                for pattern in (' - thumbnail.*', '.srt', ' - transcript.txt', ' - info.txt'):
                    for old in folder.glob(glob.escape(old_name) + pattern):
                        old.unlink()
            self.write_extras(event_id)

    def delete_event_files(self, event):
        """Delete everything filed for a broadcast; its folder too if nothing else is in it."""
        with self.lock:
            for row in self.db.all('SELECT * FROM library WHERE event_id = ?', event['id']):
                Path(row['path']).unlink(missing_ok=True)
            self.db.run('DELETE FROM library WHERE event_id = ?', event['id'])
            if event['folder'] and Path(event['folder']).is_dir():
                folder, name = Path(event['folder']), safe_name(event['title'])
                for pattern in (' - thumbnail.*', '.srt', ' - transcript.txt', ' - info.txt'):
                    for old in folder.glob(glob.escape(name) + pattern):
                        old.unlink()
                try:
                    folder.rmdir()
                except OSError:
                    pass   # the user keeps something of their own in there
