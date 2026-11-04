"""Post-broadcast editing (hub only): trim, fades, cut out sections, render, publish

An edit keeps the part of a recording between `in` and `out`, minus any `cuts`.
It renders to a new MP4 (fades need re-encoding, so the hub does it with FFmpeg,
one job at a time). Publishing uploads it to YouTube as a new video on the
broadcast's channels and can hide the original live video, since YouTube
can't replace a live video's footage in place.

A broadcast has one edit. It is always cut from an original recording, and
rendering again replaces the edited video (once the new one is ready). Its list
of YouTube uploads carries over, so publishing again can hide the earlier upload.
"""
import hashlib
import json
import queue
import subprocess
import tempfile
import threading
import time

from . import settings
from .youtube import YouTubeError

FILMSTRIP_TILES = 40
TILE_WIDTH = 192


def probe_duration(path):
    out = subprocess.run([settings.FFPROBE_BIN, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def filmstrip(path):
    """A strip of evenly spaced frames for the editor's timeline (cached)."""
    stat = path.stat()
    key = hashlib.sha1(f'{path}:{stat.st_size}:{stat.st_mtime}'.encode()).hexdigest()[:16]
    target = settings.DATA_DIR / 'cache' / f'filmstrip-{key}.jpg'
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(path)
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(FILMSTRIP_TILES):
            # Seeking before -i jumps straight to the nearest keyframe: fast on long recordings
            t = duration * (i + 0.5) / FILMSTRIP_TILES
            subprocess.run([settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-ss', f'{t:.2f}', '-i', str(path),
                            '-frames:v', '1', '-vf', f'scale={TILE_WIDTH}:-2', '-q:v', '5', f'{tmp}/f{i:03d}.jpg'])
        subprocess.run([settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-y', '-i', f'{tmp}/f%03d.jpg',
                        '-vf', f'tile={FILMSTRIP_TILES}x1', '-frames:v', '1', '-q:v', '5', str(target)], check=True)
    return target


def keep_ranges(spec, duration):
    """The (start, end) sections an edit keeps, in order."""
    start = max(0.0, float(spec.get('in') or 0))
    end = min(duration, float(spec.get('out') or duration))
    keep, cursor = [], start
    for a, b in sorted((float(a), float(b)) for a, b in spec.get('cuts', []) if float(b) > float(a)):
        a, b = max(a, start), min(b, end)
        if b <= a or b <= cursor:      # outside the trimmed span, or already cut
            continue
        if a > cursor:
            keep.append((cursor, a))
        cursor = max(cursor, b)
    if end > cursor:
        keep.append((cursor, end))
    return [(a, b) for a, b in keep if b - a >= 0.5]


def render_command(source, spec, ranges, output, extra_inputs=(), extra_output=()):
    """FFmpeg command: one fast-seeked input per kept section, joined, with fades.
    Extra inputs follow the sections (input len(ranges) onwards); extra output options
    can add streams after the video (v:0) and audio, e.g. a cover picture."""
    fade_in, fade_out = float(spec.get('fade_in') or 0), float(spec.get('fade_out') or 0)
    dip = float(spec.get('dip') or 0) if len(ranges) > 1 else 0
    cmd = [settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-y', '-nostats', '-progress', 'pipe:1']
    graph, joined = [], ''
    for i, (a, b) in enumerate(ranges):
        cmd += ['-ss', f'{a:.3f}', '-to', f'{b:.3f}', '-i', str(source)]
        v, au = f'[{i}:v]setpts=PTS-STARTPTS', f'[{i}:a]asetpts=PTS-STARTPTS'
        length = b - a
        if dip:
            # A short dip to black (and silence) at each cut
            d = min(dip, length / 2)
            if i > 0:
                v += f',fade=t=in:st=0:d={d}'
                au += f',afade=t=in:st=0:d={d}'
            if i < len(ranges) - 1:
                v += f',fade=t=out:st={length - d:.3f}:d={d}'
                au += f',afade=t=out:st={length - d:.3f}:d={d}'
        graph += [f'{v}[v{i}]', f'{au}[a{i}]']
        joined += f'[v{i}][a{i}]'
    total = sum(b - a for a, b in ranges)
    graph.append(f'{joined}concat=n={len(ranges)}:v=1:a=1[vc][ac]')
    vf, af = [], []
    if fade_in:
        vf.append(f'fade=t=in:st=0:d={fade_in}')
        af.append(f'afade=t=in:st=0:d={fade_in}')
    if fade_out:
        vf.append(f'fade=t=out:st={max(0, total - fade_out):.3f}:d={fade_out}')
        af.append(f'afade=t=out:st={max(0, total - fade_out):.3f}:d={fade_out}')
    graph += [f"[vc]{','.join(vf) or 'null'}[vout]", f"[ac]{','.join(af) or 'anull'}[aout]"]
    cmd += [*extra_inputs, '-filter_complex', ';'.join(graph), '-map', '[vout]', '-map', '[aout]', *extra_output,
            '-c:v:0', 'libx264', '-preset:v:0', 'veryfast', '-crf:v:0', '19', '-profile:v:0', 'high',
            '-pix_fmt:v:0', 'yuv420p', '-g:v:0', '60', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
            '-movflags', '+faststart', '-f', 'mp4', str(output)]
    return cmd, total


class Editor:
    def __init__(self, db, youtube, library):
        self.db = db
        self.youtube = youtube
        self.library = library
        self.jobs = queue.Queue()

    def start(self):
        # Renders interrupted by a restart start over
        for edit in self.db.all("SELECT id FROM edits WHERE status IN ('queued', 'rendering')"):
            self.jobs.put(('render', edit['id']))
        for edit in self.db.all("SELECT id FROM edits WHERE status = 'publishing'"):
            self.db.run("UPDATE edits SET status = 'done', error = 'Publishing was interrupted; try again' WHERE id = ?",
                        edit['id'])
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            kind, edit_id, *args = self.jobs.get()
            try:
                if kind == 'render':
                    self._render(edit_id)
                else:
                    self._publish(edit_id, *args)
            except Exception as e:
                print(f"Edit {edit_id} {kind} failed: {e}")
                self.db.run("UPDATE edits SET status = ?, error = ? WHERE id = ?",
                            'failed' if kind == 'render' else 'done', str(e)[:500], edit_id)

    def edit(self, edit_id):
        edit = self.db.one('SELECT * FROM edits WHERE id = ?', edit_id)
        if edit:
            edit['spec'] = json.loads(edit['spec'])
            edit['published'] = json.loads(edit['published'] or '[]')
        return edit

    def current(self, event_id):
        """The broadcast's edit (the latest, if an older hub left more than one)."""
        row = self.db.one('SELECT id FROM edits WHERE event_id = ? ORDER BY id DESC LIMIT 1', event_id)
        return self.edit(row['id']) if row else None

    def create(self, event, source_path, spec):
        """Render the broadcast's edit, replacing the one it has (when the new render is ready)."""
        source = self.library.resolve(source_path)
        if not source:
            raise ValueError('That recording is not available')
        duration = probe_duration(source)
        ranges = keep_ranges(spec, duration)
        if not ranges:
            raise ValueError('Nothing left to keep: check the start, end and cuts')
        length = sum(b - a for a, b in ranges)
        edit = self.current(event['id'])
        if edit and edit['status'] in ('queued', 'rendering', 'publishing'):
            raise ValueError('Wait for the edit to finish rendering or publishing first')
        if edit:
            self.db.run("UPDATE edits SET source = ?, spec = ?, status = 'queued', progress = 0, duration = ?, "
                        'error = NULL, created_at = ? WHERE id = ?', source_path, json.dumps(spec), length,
                        time.time(), edit['id'])
            edit_id = edit['id']
        else:
            edit_id = self.db.run('INSERT INTO edits (event_id, source, spec, status, duration, created_at) '
                                  "VALUES (?, ?, ?, 'queued', ?, ?)", event['id'], source_path, json.dumps(spec),
                                  length, time.time())
        self.db.run('UPDATE events SET edit_draft = NULL WHERE id = ?', event['id'])
        self.jobs.put(('render', edit_id))
        return edit_id

    def _render(self, edit_id):
        edit = self.edit(edit_id)
        event = self.db.one('SELECT * FROM events WHERE id = ?', edit['event_id'])
        source = self.library.resolve(edit['source'])
        if not source or not event:
            raise ValueError('The recording or broadcast is gone')
        # Straight into the broadcast's folder, with its details and thumbnail embedded;
        # a render again replaces the edited video in place
        previous = self.library.resolve(edit['output']) if edit['output'] else None
        if previous:
            output = previous
        else:
            number, output = self.library.edit_target(event)
        partial = output.with_name(f'.{output.name}.part')
        ranges = keep_ranges(edit['spec'], probe_duration(source))
        cmd, total = render_command(source, edit['spec'], ranges, partial, *self.library.embed_args(event, len(ranges)))
        self.db.run("UPDATE edits SET status = 'rendering', progress = 0, error = NULL WHERE id = ?", edit_id)
        print(f"Rendering edit {edit_id}: {len(ranges)} section(s), {total:.0f} s")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if line.startswith('out_time_us=') and line.strip()[12:].isdigit():
                done = int(line.strip()[12:]) / 1e6
                self.db.run('UPDATE edits SET progress = ? WHERE id = ?', min(0.99, done / total), edit_id)
        if proc.wait() != 0:
            partial.unlink(missing_ok=True)
            raise RuntimeError(proc.stderr.read().strip().splitlines()[-1] if proc.stderr else 'FFmpeg failed')
        partial.replace(output)
        ref = edit['output'] if previous else f"library/{self.library.add(event, 'edit', number, output)}"
        self.db.run("UPDATE edits SET status = 'done', progress = 1, output = ? WHERE id = ?", ref, edit_id)
        self._drop_older(edit_id, event['id'])
        self.library.write_extras(event['id'])
        print(f"Edit {edit_id} rendered")

    def _drop_older(self, edit_id, event_id):
        """One edit per broadcast: remove ones an older hub kept, keeping their upload history."""
        published = self.edit(edit_id)['published']
        for old in self.db.all('SELECT id FROM edits WHERE event_id = ? AND id != ?', event_id, edit_id):
            old = self.edit(old['id'])
            published = old['published'] + published
            if old['output']:
                self.library.delete(old['output'])
            self.db.run('DELETE FROM edits WHERE id = ?', old['id'])
        self.db.run('UPDATE edits SET published = ? WHERE id = ?', json.dumps(published), edit_id)

    def delete(self, edit_id):
        edit = self.edit(edit_id)
        if edit and edit['output']:
            self.library.delete(edit['output'])
        self.db.run('DELETE FROM edits WHERE id = ?', edit_id)

    # ---- Publishing to YouTube ----
    def publish(self, edit_id, request):
        self.db.run("UPDATE edits SET status = 'publishing', progress = 0, error = NULL WHERE id = ?", edit_id)
        self.jobs.put(('publish', edit_id, request))

    def _publish(self, edit_id, request):
        edit = self.edit(edit_id)
        event = self.db.one('SELECT * FROM events WHERE id = ?', edit['event_id'])
        path = self.library.resolve(edit['output'])
        thumb = self.library.thumbnail(event)
        originals = {t['channel_id']: t['broadcast_id'] for t in
                     self.db.all('SELECT channel_id, broadcast_id FROM event_targets WHERE event_id = ?', event['id'])}
        results, errors = edit['published'], []
        earlier = [p for p in results if not p.get('hidden')]
        channels = request['channel_ids']
        for n, channel_id in enumerate(channels):
            channel = self.youtube.channel(channel_id)
            try:
                def progress(fraction, n=n):
                    self.db.run('UPDATE edits SET progress = ? WHERE id = ?', (n + fraction) / len(channels), edit_id)
                video_id = self.youtube.upload_video(channel, path, {
                    'title': request['title'], 'description': request['description'],
                    'privacy': request['privacy'], 'made_for_kids': bool(event['made_for_kids'])}, progress)
                if thumb and thumb.exists():
                    self.youtube.set_thumbnail(channel, video_id, thumb.read_bytes(),
                                               'image/png' if thumb.suffix == '.png' else 'image/jpeg')
                hidden = None
                if request.get('hide_original') in ('private', 'unlisted') and originals.get(channel_id):
                    self.youtube.set_privacy(channel, originals[channel_id], request['hide_original'])
                    hidden = request['hide_original']
                # YouTube can't swap a video's footage: an earlier upload of this edit is hidden instead
                if request.get('hide_previous') in ('private', 'unlisted'):
                    for p in earlier:
                        if p['channel_id'] == channel_id:
                            self.youtube.set_privacy(channel, p['video_id'], request['hide_previous'])
                            p['hidden'] = request['hide_previous']
                results.append({'channel_id': channel_id, 'channel': channel['title'], 'video_id': video_id,
                                'url': f'https://youtu.be/{video_id}', 'original_hidden': hidden,
                                'at': time.time()})
            except YouTubeError as e:
                errors.append(f"{channel['title'] if channel else channel_id}: {e}")
        self.db.run("UPDATE edits SET status = 'done', progress = 1, published = ?, error = ? WHERE id = ?",
                    json.dumps(results), '; '.join(errors) or None, edit_id)
