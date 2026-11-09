"""Post-broadcast editing (hub only): clips, transitions, titles and color; render; publish

An edit is a list of clips: sections of one recording, in order, each with its own color
settings, joined by cuts or transitions (dissolve, dips, wipes), with titles over the top
and fades at the ends. It renders to a new MP4 with FFmpeg, one job at a time. Publishing uploads it to YouTube as a new video on the
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
from array import array
from pathlib import Path

from . import settings
from .youtube import YouTubeError

FILMSTRIP_TILES = 60
TILE_WIDTH = 192
PEAKS_PER_SECOND = 40     # the editor's audio waveform
PEAK_RATE = 4000          # samples a second the sound is read at for it


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
    key = hashlib.sha1(f'{path}:{stat.st_size}:{stat.st_mtime}:{FILMSTRIP_TILES}'.encode()).hexdigest()[:16]
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


def waveform(path):
    """The loudest point of each 1/40 s of a recording's sound, 0-255 (on a square-root scale, so
    speech shows up), for the editor's audio track (cached)."""
    stat = path.stat()
    key = hashlib.sha1(f'{path}:{stat.st_size}:{stat.st_mtime}'.encode()).hexdigest()[:16]
    target = settings.DATA_DIR / 'cache' / f'waveform-{key}.bin'
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([settings.FFMPEG_BIN, '-v', 'error', '-i', str(path), '-vn', '-ac', '1', '-ar', str(PEAK_RATE),
                             '-f', 's16le', '-'], stdout=subprocess.PIPE)
    step, peaks = PEAK_RATE // PEAKS_PER_SECOND, bytearray()
    while chunk := proc.stdout.read(step * 2 * 4000):
        samples = array('h', chunk[:len(chunk) // 2 * 2])
        for i in range(0, len(samples), step):
            part = samples[i:i + step]
            loudest = max(max(part), -min(part)) / 32768
            peaks.append(min(255, int(255 * loudest ** 0.5)))
    proc.wait()
    target.write_bytes(bytes(peaks))
    return target


TRANSITIONS = {'dissolve': 'fade', 'dip_black': 'fadeblack', 'dip_white': 'fadewhite', 'wipe': 'wipeleft',
               'slide': 'slideleft', 'circle': 'circleopen'}     # FFmpeg's xfade names
COLOR = ('brightness', 'contrast', 'saturation', 'warmth')       # each -100..100; 0 leaves the picture as it was


def video_info(path):
    """(width, height, frames per second) of a video file."""
    out = subprocess.run([settings.FFPROBE_BIN, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                          'stream=width,height,r_frame_rate', '-of', 'json', str(path)], capture_output=True, text=True)
    stream = (json.loads(out.stdout or '{}').get('streams') or [{}])[0]
    num, _, den = stream.get('r_frame_rate', '30/1').partition('/')
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return stream.get('width', 1920), stream.get('height', 1080), fps if 1 <= fps <= 120 else 30.0


def _legacy_ranges(spec, duration):
    """The sections an edit from before clips kept: in to out, minus the cuts."""
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


def _media_none(_):
    return None


def _clip(c, duration, media, video):
    """One clip of a track, checked: {at, start, end, media, transition}, or None if it's unusable."""
    m = media(c['media']) if c.get('media') else None
    if c.get('media') and (not m or (video and m['kind'] == 'audio') or (not video and m['kind'] == 'image')):
        return None                                     # gone from the library, or the wrong kind for the track
    limit = duration if not m else m['duration'] if m['kind'] != 'image' else 3600.0
    start = max(0.0, float(c.get('start') or 0))
    clip = {'at': max(0.0, float(c.get('at') or 0)), 'start': start, 'end': min(limit, float(c['end'])), 'media': m}
    if video:
        t = c.get('transition') or {}
        clip['transition'] = {'type': t.get('type') if t.get('type') in TRANSITIONS else 'cut',
                              'duration': float(t.get('duration') or 0.5)}
    return clip if clip['end'] - clip['start'] >= 0.2 else None


def _tidy(clips):
    """Clips in order along their track, none overlapping the one before."""
    out = []
    for c in sorted((c for c in clips if c), key=lambda c: c['at']):
        if out:
            prev_end = out[-1]['at'] + out[-1]['end'] - out[-1]['start']
            if c['at'] < prev_end - 0.01:
                cut = prev_end - c['at']
                c = {**c, 'at': prev_end, 'start': c['start'] + cut}
                if c['end'] - c['start'] < 0.2:
                    continue
        out.append(c)
    return out


def _end(c):
    return c['at'] + c['end'] - c['start']


def normalize(spec, duration, media=_media_none):
    """An edit as tracks: video [{at, start, end, media, transition}], audio [{at, start, end, media}],
    music [{at, start, end, media, volume}], titles [{start, end, text, sub, style}], plus color, brand,
    fade_in, fade_out and its total length. `at` is where a clip sits on the timeline; start and end are
    the part of its media used. media is None for the broadcast's recording, else the library item
    (media(id) gives {kind, path, duration, has_audio}). Gaps between clips are black and silent.
    Edits saved as back-to-back clips, or as in/out/cuts, become linked video and audio clips."""
    if 'video' in spec or 'audio' in spec:
        video = _tidy(_clip(c, duration, media, True) for c in spec.get('video') or [])
        audio = _tidy(_clip(c, duration, media, False) for c in spec.get('audio') or [])
    else:
        if spec.get('clips'):
            given = spec.get('transitions') or []
            seq = [(c, given[i] if i < len(given) else {}) for i, c in enumerate(spec['clips'])]
        else:
            dip = float(spec.get('dip') or 0)
            seq = [({'start': a, 'end': b}, {'type': 'dip_black', 'duration': 2 * dip} if dip else {})
                   for a, b in _legacy_ranges(spec, duration)]
        video, audio, at = [], [], 0.0
        for c, t in seq:
            v = _clip({**c, 'at': at, 'transition': t}, duration, media, True)
            if not v:
                continue
            video.append(v)
            m = v['media']
            if not m or m['has_audio']:
                audio.append({'at': at, 'start': v['start'], 'end': v['end'], 'media': m})
            at = _end(v)
    total = max([_end(c) for c in video + audio] or [0.0])
    # A transition plays across a cut between two touching clips, and is no longer than either of them
    for i, c in enumerate(video):
        nxt = video[i + 1] if i + 1 < len(video) else None
        if c['transition']['type'] == 'cut' or not nxt or abs(_end(c) - nxt['at']) > 0.02:
            c['transition'] = {'type': 'cut', 'duration': 0.0}
        else:
            c['transition']['duration'] = min(max(0.1, c['transition']['duration']), 3.0,
                                              c['end'] - c['start'], nxt['end'] - nxt['start'])
    music = []
    for item in spec.get('music') or []:
        m = media(item.get('media')) if item.get('media') else None
        if not m or not (m['kind'] == 'audio' or m['has_audio']):
            continue
        at, start = max(0.0, float(item.get('at') or 0)), max(0.0, float(item.get('start') or 0))
        end = min(float(item['end']), m['duration'] or 0, start + max(0.0, total - at))
        if end - start >= 0.5:
            music.append({'media': m, 'at': at, 'start': start, 'end': end,
                          'volume': max(0.0, min(2.0, float(item.get('volume', 0.3))))})
    titles = [{'text': str(t.get('text', ''))[:120], 'sub': str(t.get('sub', ''))[:120],
               'style': t.get('style') or 'lower_third', 'start': max(0.0, float(t['start'])),
               'end': min(total, float(t['end']))} for t in spec.get('titles') or [] if str(t.get('text', '')).strip()]
    color = {k: max(-100.0, min(100.0, float((spec.get('color') or {}).get(k) or 0))) for k in COLOR}
    return {'video': video, 'audio': audio, 'music': music, 'titles': [t for t in titles if t['end'] - t['start'] >= 0.5],
            'color': color, 'brand': spec.get('brand'), 'total': total,
            'fade_in': min(10.0, float(spec.get('fade_in') or 0)), 'fade_out': min(10.0, float(spec.get('fade_out') or 0))}


def placements(edit, track='audio'):
    """[(start, end, at)]: where the recording's clips on a track land in a normalized edit's video
    (the audio track by default: what's said, for captions)."""
    return [(c['start'], c['end'], c['at']) for c in edit[track] if not c['media']]


def keep_ranges(spec, duration):
    """The (start, end) sections of the recording on an edit's video track, in order."""
    return [(a, b) for a, b, _ in placements(normalize(spec, duration), 'video')]


def _color(c):
    """FFmpeg filters for the color adjustment, each followed by a comma ('' when it's untouched)."""
    out = []
    if c['brightness'] or c['contrast'] or c['saturation']:
        out.append(f"eq=brightness={c['brightness'] / 400:.3f}:contrast={1 + c['contrast'] / 200:.3f}"
                   f":saturation={1 + c['saturation'] / 100:.3f}")
    if c['warmth']:
        out.append(f"colortemperature=temperature={6500 - c['warmth'] * 30:.0f}")
    return ''.join(f'{f},' for f in out)


AUDIO = 'aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo'


def _track(items, total):
    """A track's clips with the gaps between them: [('clip', clip) | ('gap', seconds)] covering 0 to total."""
    out, t = [], 0.0
    for c in items:
        if c['at'] > t + 0.01:
            out.append(('gap', c['at'] - t))
        out.append(('clip', c))
        t = _end(c)
    if total > t + 0.01:
        out.append(('gap', total - t))
    return out


def render_command(source, spec, duration, output, title_images=(), embed=None, media=_media_none, brand=None):
    """FFmpeg command for an edit, built track by track: the video track's clips (the recording with the
    color adjustment, or a library video or image fitted to the recording's size) and black gaps, joined by
    cuts or transitions; the brand's watermark over the recording; titles; the audio track's clips and
    silent gaps; music mixed under it; fades at the ends. A transition is centered on its cut, using a little
    of each side's media beyond the cut (as much as there is), so nothing moves.
    title_images: a PNG per title, the video's size. brand: {watermark (path), corner, size, opacity} or None.
    embed(next input number): (inputs, output options) after everything else, e.g. a cover picture."""
    edit = normalize(spec, duration, media)
    total, titles = edit['total'], edit['titles']
    width, height, fps = video_info(source)
    fit = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,'
    look = _color(edit['color'])
    cmd = [settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-y', '-nostats', '-progress', 'pipe:1']
    graph, n = [], 0

    def room(c, side):
        """How far a clip's media goes past its edge."""
        m = c['media']
        if m and m['kind'] == 'image':
            return 3600.0
        return c['start'] if side == 'before' else (m['duration'] if m else duration) - c['end']

    def join(items, kind, crossfade):
        """Inputs and filters for one track; returns its output label."""
        nonlocal n
        before, after = [0.0] * len(items), [0.0] * len(items)
        fades = {}
        for i in range(len(items) - 1):
            t = crossfade(items[i], items[i + 1])
            if t:
                after[i] = min(t['duration'] / 2, room(items[i][1], 'after'))
                before[i + 1] = min(t['duration'] / 2, room(items[i + 1][1], 'before'))
                fades[i] = t
        labels = []
        for i, (what, c) in enumerate(items):
            label = f'{kind}{i}'
            if what == 'gap':
                source_args = ['-f', 'lavfi', '-t', f'{c:.3f}', '-i',
                               f'color=c=black:s={width}x{height}:r={fps:.3f}' if kind == 'v' else 'anullsrc=r=48000:cl=stereo']
                cmd.extend(source_args)
                graph.append(f'[{n}:{kind}]' + (f'format=yuv420p,settb=AVTB[{label}]' if kind == 'v' else f'{AUDIO}[{label}]'))
            else:
                m, span = c['media'], c['end'] - c['start'] + before[i] + after[i]
                if m and m['kind'] == 'image':
                    cmd.extend(['-loop', '1', '-framerate', f'{fps:.3f}', '-t', f'{span:.3f}', '-i', m['path']])
                    graph.append(f'[{n}:v]{fit}fps={fps:.3f},format=yuv420p,settb=AVTB[{label}]')
                else:
                    cmd.extend(['-ss', f"{c['start'] - before[i]:.3f}", '-to', f"{c['end'] + after[i]:.3f}",
                                '-i', m['path'] if m else str(source)])
                    graph.append(f'[{n}:v]setpts=PTS-STARTPTS,{fit if m else look}fps={fps:.3f},format=yuv420p,settb=AVTB[{label}]'
                                 if kind == 'v' else f'[{n}:a]asetpts=PTS-STARTPTS,{AUDIO}[{label}]')
            n += 1
            labels.append(label)
        out, length = f'[{labels[0]}]', (items[0][1] if items[0][0] == 'gap' else items[0][1]['end'] - items[0][1]['start']) + after[0]
        for i in range(1, len(items)):
            what, c = items[i]
            piece = (c if what == 'gap' else c['end'] - c['start']) + before[i] + after[i]
            overlap = after[i - 1] + before[i]
            joined = f'{kind}j{i}'
            if i - 1 not in fades or overlap < 0.04:
                graph.append(f"{out}[{labels[i]}]concat=n=2:v={1 if kind == 'v' else 0}:a={0 if kind == 'v' else 1}[{joined}]")
            elif kind == 'v':
                graph.append(f"{out}[{labels[i]}]xfade=transition={TRANSITIONS[fades[i - 1]['type']]}:duration={overlap:.3f}"
                             f':offset={length - overlap:.3f}[{joined}]')
            else:
                graph.append(f'{out}[{labels[i]}]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[{joined}]')
            out, length = f'[{joined}]', length + piece - overlap
        return out

    video_items = _track(edit['video'], total)
    video = join(video_items, 'v', lambda a, b: a[0] == 'clip' and b[0] == 'clip' and a[1]['transition']['type'] != 'cut'
                 and a[1]['transition'])
    # Sound crossfades where its cut lines up with a video transition
    dissolves = {round(_end(c), 2): c['transition'] for c in edit['video'] if c['transition']['type'] != 'cut'}
    audio = join(_track(edit['audio'], total), 'a',
                 lambda a, b: a[0] == 'clip' and b[0] == 'clip' and abs(_end(a[1]) - b[1]['at']) < 0.02
                 and dissolves.get(round(_end(a[1]), 2)))
    # The watermark, over the recording only (not an intro, outro or slide)
    spans = [(at, at + b - a) for a, b, at in placements(edit, 'video')]
    if brand and brand.get('watermark') and spans:
        margin = round(width * 0.03)
        x = margin if brand['corner'].endswith('left') else f'main_w-overlay_w-{margin}'
        y = margin if brand['corner'].startswith('top') else f'main_h-overlay_h-{margin}'
        cmd += ['-loop', '1', '-framerate', f'{fps:.3f}', '-t', f'{total:.3f}', '-i', brand['watermark']]
        graph += [f"[{n}:v]format=rgba,scale={round(width * brand['size'] / 100 / 2) * 2}:-2,"
                  f"colorchannelmixer=aa={brand['opacity'] / 100:.2f}[wm]",
                  f"{video}[wm]overlay={x}:{y}:format=auto:eof_action=pass:enable='"
                  + '+'.join(f'between(t,{a:.3f},{b:.3f})' for a, b in spans) + "'[vw]"]
        video, n = '[vw]', n + 1
    for k, (title, image) in enumerate(zip(titles, title_images)):
        shown = title['end'] - title['start']
        fade = min(0.4, shown / 3)
        cmd += ['-loop', '1', '-framerate', f'{fps:.3f}', '-t', f'{shown:.3f}', '-i', str(image)]
        graph += [f'[{n}:v]format=rgba,fade=t=in:st=0:d={fade:.2f}:alpha=1,'
                  f'fade=t=out:st={shown - fade:.3f}:d={fade:.2f}:alpha=1,setpts=PTS+{title["start"]:.3f}/TB[t{k}]',
                  f'{video}[t{k}]overlay=0:0:eof_action=pass:format=auto[vt{k}]']
        video, n = f'[vt{k}]', n + 1
    # Music under everything, faded in and out
    beds = []
    for k, item in enumerate(edit['music']):
        shown = item['end'] - item['start']
        fade = min(1.5, shown / 3)
        cmd += ['-ss', f"{item['start']:.3f}", '-t', f'{shown:.3f}', '-i', item['media']['path']]
        graph.append(f"[{n}:a]{AUDIO},volume={item['volume']:.2f},afade=t=in:d={fade:.2f},"
                     f"afade=t=out:st={shown - fade:.3f}:d={fade:.2f},adelay={round(item['at'] * 1000)}:all=1[m{k}]")
        beds.append(f'[m{k}]')
        n += 1
    if beds:
        graph.append(f"{audio}{''.join(beds)}amix=inputs={1 + len(beds)}:duration=first:normalize=0[mixed]")
        audio = '[mixed]'
    vf, af = [], []
    if edit['fade_in']:
        vf.append(f"fade=t=in:st=0:d={edit['fade_in']}")
        af.append(f"afade=t=in:st=0:d={edit['fade_in']}")
    if edit['fade_out']:
        vf.append(f"fade=t=out:st={max(0, total - edit['fade_out']):.3f}:d={edit['fade_out']}")
        af.append(f"afade=t=out:st={max(0, total - edit['fade_out']):.3f}:d={edit['fade_out']}")
    graph += [f"{video}{','.join(vf + ['format=yuv420p'])},trim=end={total:.3f}[vout]",
              f"{audio}{','.join(af) or 'anull'},atrim=end={total:.3f}[aout]"]
    inputs, extra = embed(n) if embed else ((), ())
    cmd += [*inputs, '-filter_complex', ';'.join(graph), '-map', '[vout]', '-map', '[aout]', *extra,
            '-c:v:0', 'libx264', '-preset:v:0', 'veryfast', '-crf:v:0', '19', '-profile:v:0', 'high',
            '-pix_fmt:v:0', 'yuv420p', '-g:v:0', '60', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
            '-movflags', '+faststart', '-f', 'mp4', str(output)]
    return cmd, total


class Editor:
    def __init__(self, db, youtube, library, media):
        self.db = db
        self.youtube = youtube
        self.library = library
        self.media = media
        self.jobs = queue.Queue()

    def resolve(self, media_id):
        """A library item for an edit: {kind, path, duration, has_audio}, or None if it's gone."""
        row = self.media.get(int(media_id)) if str(media_id).isdigit() else None
        path = self.media.path(row['id']) if row else None
        return {**row, 'path': str(path)} if path else None

    def title_color(self, brand_id):
        """A brand's color for titles, or None (TouchStream's)."""
        brand = self.media.brand(int(brand_id)) if str(brand_id or '').isdigit() else None
        return brand['title_color'] if brand else None

    def brand_look(self, brand_id):
        """A brand's watermark for render_command, or None."""
        brand = self.media.brand(int(brand_id)) if str(brand_id or '').isdigit() else None
        path = brand and brand['watermark'] and self.media.path(brand['watermark'])
        return {'watermark': str(path), 'corner': brand['watermark_corner'], 'size': brand['watermark_size'],
                'opacity': brand['watermark_opacity']} if path else None

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
            edit['auto'] = json.loads(edit['auto']) if edit['auto'] else None
        return edit

    def current(self, event_id):
        """The broadcast's edit (the latest, if an older hub left more than one)."""
        row = self.db.one('SELECT id FROM edits WHERE event_id = ? ORDER BY id DESC LIMIT 1', event_id)
        return self.edit(row['id']) if row else None

    def create(self, event, source_path, spec, auto=None):
        """Render the broadcast's edit, replacing the one it has (when the new render is ready). auto: made by its edit
        profile (autoedit.py): {profile_id, profile, why, publish (the request to publish with once it's rendered, or
        None to wait to be looked over)}; an edit someone renders has none."""
        source = self.library.resolve(source_path)
        if not source:
            raise ValueError('That recording is not available')
        duration = probe_duration(source)
        edit = normalize(spec, duration, self.resolve)
        if not edit['video'] and not edit['audio']:
            raise ValueError('The timeline is empty')
        length = edit['total']
        edit = self.current(event['id'])
        if edit and edit['status'] in ('queued', 'rendering', 'publishing'):
            raise ValueError('Wait for the edit to finish rendering or publishing first')
        if edit:
            self.db.run("UPDATE edits SET source = ?, spec = ?, status = 'queued', progress = 0, duration = ?, "
                        'error = NULL, auto = ?, created_at = ? WHERE id = ?', source_path, json.dumps(spec), length,
                        json.dumps(auto) if auto else None, time.time(), edit['id'])
            edit_id = edit['id']
        else:
            edit_id = self.db.run('INSERT INTO edits (event_id, source, spec, status, duration, auto, created_at) '
                                  "VALUES (?, ?, ?, 'queued', ?, ?, ?)", event['id'], source_path, json.dumps(spec),
                                  length, json.dumps(auto) if auto else None, time.time())
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
            number, output = self.library.new_target(event, 'edit')
        partial = output.with_name(f'.{output.name}.part')
        duration = probe_duration(source)
        normal = normalize(edit['spec'], duration, self.resolve)
        width, height, _ = video_info(source)
        with tempfile.TemporaryDirectory() as tmp:
            images, accent = [], self.title_color(normal['brand'])
            for k, title in enumerate(normal['titles']):
                from . import titles   # needs Pillow
                images.append(Path(tmp) / f'title-{k}.png')
                titles.draw(title, width, height, accent).save(images[-1])
            cmd, total = render_command(source, edit['spec'], duration, partial, images,
                                        lambda n: self.library.embed_args(event, n), self.resolve,
                                        self.brand_look(normal['brand']))
            self._run(cmd, total, edit_id, partial)
        partial.replace(output)
        ref = edit['output'] if previous else f"library/{self.library.add(event, 'edit', number, output)}"
        self.db.run("UPDATE edits SET status = 'done', progress = 1, output = ? WHERE id = ?", ref, edit_id)
        self._drop_older(edit_id, event['id'])
        self.library.write_extras(event['id'])
        print(f"Edit {edit_id} rendered")
        if edit['auto'] and edit['auto'].get('publish') and not edit['published']:   # its profile publishes by itself
            self.publish(edit_id, edit['auto']['publish'])

    def _run(self, cmd, total, edit_id, partial):
        self.db.run("UPDATE edits SET status = 'rendering', progress = 0, error = NULL WHERE id = ?", edit_id)
        print(f"Rendering edit {edit_id}: {total:.0f} s")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if line.startswith('out_time_us=') and line.strip()[12:].isdigit():
                done = int(line.strip()[12:]) / 1e6
                self.db.run('UPDATE edits SET progress = ? WHERE id = ?', min(0.99, done / total), edit_id)
        if proc.wait() != 0:
            partial.unlink(missing_ok=True)
            raise RuntimeError((proc.stderr.read().strip().splitlines() or ['FFmpeg failed'])[-1])

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
                elif brand_thumb := self.media.channel_thumbnail(channel_id):   # the channel's brand, without one of its own
                    self.youtube.set_thumbnail(channel, video_id, *brand_thumb)
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
