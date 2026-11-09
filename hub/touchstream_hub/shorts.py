"""Shorts and reels (hub only): one moment of a broadcast as a vertical 9:16 video

A short is a stretch of a broadcast's recording, picked from its transcript so it starts and ends
on whole sentences, up to three minutes long. It's made vertical in one of two ways:

- 'fill': a 9:16 window that follows the speaker (framing.py), showing the whole picture instead
  while nobody's in it (a slide, a wide shot);
- 'fit': the whole picture across the middle, over a blurred, enlarged copy of itself.

It can have captions burned in, in one of a few fixed styles with an accent color (captions.py),
and the pauses taken out (quiet stretches over 0.6 s become jump cuts). Shorts render on the hub
one at a time, straight into the broadcast's folder, and upload to YouTube as Shorts: YouTube
treats any vertical video up to three minutes long as one.
"""
import json
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import settings
from .youtube import YouTubeError

try:
    from . import captions, framing
    AVAILABLE = framing.AVAILABLE
except ImportError:          # needs the transcribe extra (numpy, ONNX Runtime, Pillow)
    captions = framing = None
    AVAILABLE = False

MAX_LENGTH = 180        # seconds: YouTube's limit for a Short
MIN_LENGTH = 3
WIDTH, HEIGHT = 1080, 1920
PAUSE = 0.6             # seconds of quiet that count as a pause
PAD = 0.15              # seconds kept either side of a pause, so no word is clipped
LEAD, TAIL = 0.15, 0.4  # seconds before a short's first sentence and after its last
FRAMINGS = ('fill', 'fit')


def words_between(segments, start, end):
    """Timed words [{start, end, text}] of a transcript between two times. Lines transcribed before
    the hub kept word timings, or corrected since, have their words spread over the line by length."""
    words = []
    for s in segments:
        if s['end'] <= start or s['start'] >= end:
            continue
        if s.get('words'):
            words += [{'start': a, 'end': b, 'text': t} for a, b, t in s['words']]
            continue
        parts = s['text'].split()
        per = (s['end'] - s['start']) / max(1, sum(len(p) + 1 for p in parts))
        t = s['start']
        for p in parts:
            words.append({'start': t, 'end': t + (len(p) + 1) * per, 'text': p})
            t += (len(p) + 1) * per
    return [w for w in words if start <= (w['start'] + w['end']) / 2 < end]


def sentences(segments):
    """The transcript as sentences [{start, end, text}] to pick a short's moment from, so it starts
    and ends on whole ones. A long pause also ends one, for speech Whisper didn't punctuate."""
    out, cur = [], []
    for w in words_between(segments, 0, float('inf')):
        if cur and w['start'] - cur[-1]['end'] > 2:
            out.append(cur)
            cur = []
        cur.append(w)
        if w['text'].endswith(('.', '?', '!')):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{'start': round(c[0]['start'], 2), 'end': round(c[-1]['end'], 2), 'text': ' '.join(w['text'] for w in c)}
            for c in out]


def moment(sentences, start, end):
    """(start, end) widened to the sentences they fall in, with a breath either side."""
    for s in sentences:
        if s['start'] <= start < s['end']:
            start = s['start']
        if s['start'] < end <= s['end']:
            end = s['end']
    return max(0.0, start - LEAD), end + TAIL


def place(words, ranges):
    """Words timed in the recording, re-timed for a short made of these (start, end) sections of it."""
    out, offset = [], 0.0
    for a, b in ranges:
        out += [{'text': w['text'], 'start': offset + max(w['start'], a) - a, 'end': offset + min(w['end'], b) - a}
                for w in words if a <= (w['start'] + w['end']) / 2 < b]
        offset += b - a
    return out


def quiet(source, start, end):
    """Quiet stretches [(start, end)] of the recording between two times. 'Quiet' is set from the
    recording's own level, well under its speech, so room noise and hum still count as quiet."""
    span = ['-ss', f'{start:.3f}', '-to', f'{end:.3f}', '-i', str(source), '-vn']
    level = subprocess.run([settings.FFMPEG_BIN, '-hide_banner', '-nostats', *span, '-af', 'volumedetect', '-f', 'null', '-'],
                           capture_output=True, text=True).stderr
    m = re.search(r'mean_volume: (-?[\d.]+) dB', level)
    threshold = min(-28.0, max(-55.0, float(m.group(1)) - 16)) if m else -40.0
    found = subprocess.run([settings.FFMPEG_BIN, '-hide_banner', '-nostats', *span, '-af',
                            f'silencedetect=noise={threshold:.1f}dB:d={PAUSE}', '-f', 'null', '-'],
                           capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r'silence_start: (-?[\d.]+)', found)]
    ends = [float(x) for x in re.findall(r'silence_end: (-?[\d.]+)', found)]
    return [(start + a, start + (ends[i] if i < len(ends) else end - start)) for i, a in enumerate(starts)]


def keep_ranges(start, end, silences, pause=PAUSE, pad=PAD):
    """The sections of [start, end] left once these quiet stretches (those at least `pause` long) are taken out,
    keeping `pad` seconds of each either side."""
    ranges, cursor = [], start
    for a, b in sorted(silences):
        if b - a < pause:
            continue
        if a + pad > cursor:
            ranges.append((cursor, a + pad))
        cursor = max(cursor, b - pad)
    if end > cursor:
        ranges.append((cursor, end))
    return [(a, b) for a, b in ranges if b - a >= 0.1]


def render_command(source, info, ranges, spec, output, crop=None, captions_list=None, embed=((), ()), whole=(), brand=None):
    """FFmpeg command for a short. Runs in the folder holding the crop commands and captions.
    crop: (crop width, first x) for 'fill', following crop.cmd; whole: [(start, end)] of the short where
    'fill' shows the whole picture instead (nobody's in it). brand: {watermark (path), corner, size, opacity} or
    None: the watermark goes at the top, on the brand's side (YouTube's buttons cover the bottom of a Short).
    Its input comes after the captions', so embed's inputs come after both."""
    width, height, fps = info
    base = ranges[0][0]
    rel = [(a - base, b - base) for a, b in ranges]
    cmd = [settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-y', '-nostats', '-progress', 'pipe:1',
           '-ss', f'{base:.3f}', '-to', f'{ranges[-1][1]:.3f}', '-i', str(source)]
    if captions_list:
        cmd += ['-f', 'concat', '-safe', '0', '-i', captions_list.name]
    total = sum(b - a for a, b in ranges)
    if brand:
        cmd += ['-loop', '1', '-framerate', f'{min(60.0, fps):.3f}', '-t', f'{total:.3f}', '-i', brand['watermark']]
    # Video: keep the frames inside the sections, each moved back by the time removed before it
    if len(rel) > 1:
        inside = '+'.join(f'gte(t,{a:.3f})*lt(t,{b:.3f})' for a, b in rel)
        removed, offsets = 0.0, []
        for i, (a, b) in enumerate(rel):
            removed += a - (rel[i - 1][1] if i else 0)
            offsets.append(f'gte(T,{a:.3f})*lt(T,{b:.3f})*{removed:.3f}')
        video = f"[0:v]select='{inside}',setpts='(T-({'+'.join(offsets)}))/TB'"
    else:
        video = '[0:v]setpts=PTS-STARTPTS'
    fit = ['[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,'
           f'scale={WIDTH}:{HEIGHT},eq=brightness=-0.08[bg]', f'[b]scale={WIDTH}:-2[fg]']
    fill = (f"sendcmd=f=crop.cmd,crop@follow=w={crop[0]}:h={height}:x={crop[1]}:y=0,"
            f"scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1") if crop else None
    if spec['framing'] == 'fill' and not whole:
        graph = [f'{video},{fill}[framed]']
    elif spec['framing'] == 'fill':
        # Following the speaker, and the whole picture where nobody's in it
        graph = [f'{video},split=3[a][b][c]', *fit, '[bg][fg]overlay=0:(H-h)/2,setsar=1[whole]', f'[c]{fill}[follow]',
                 "[follow][whole]overlay=0:0:enable='" + '+'.join(f'between(t,{a:.3f},{b:.3f})' for a, b in whole) + "'[framed]"]
    else:
        graph = [f'{video},split[a][b]', *fit, '[bg][fg]overlay=0:(H-h)/2,setsar=1[framed]']
    if captions_list:
        style = captions.STYLES[spec['style']]
        middle = style['y'] if spec['framing'] == 'fill' else max(style['y'], 0.76)   # under the picture
        top = round(middle * HEIGHT - captions.band_height(style) / 2)
        graph.append(f'[framed][1:v]overlay=0:{top}:eof_action=pass:format=auto[captioned]')
        last = '[captioned]'
    else:
        last = '[framed]'
    if brand:
        n = 2 if captions_list else 1
        margin = round(WIDTH * 0.05)
        x = margin if brand['corner'].endswith('left') else f'main_w-overlay_w-{margin}'
        graph += [f"[{n}:v]format=rgba,scale={round(WIDTH * min(45, brand['size'] * 2) / 100 / 2) * 2}:-2,"
                  f"colorchannelmixer=aa={brand['opacity'] / 100:.2f}[wm]",
                  f'{last}[wm]overlay={x}:{round(HEIGHT * 0.06)}:format=auto[branded]']
        last = '[branded]'
    # fps would otherwise pad the end out to the recording's own length, past the removed pauses
    graph.append(f'{last}fps={min(60.0, fps):.3f},trim=end={total:.3f},format=yuv420p[vout]')
    # Audio: the same sections, with tiny fades at each join so the cuts don't click
    if len(rel) > 1:
        graph.append(f"[0:a]asplit={len(rel)}{''.join(f'[s{i}]' for i in range(len(rel)))}")
        for i, (a, b) in enumerate(rel):
            graph.append(f'[s{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,'
                         f'afade=t=in:d=0.02,afade=t=out:st={max(0.0, b - a - 0.02):.3f}:d=0.02[p{i}]')
        graph.append(f"{''.join(f'[p{i}]' for i in range(len(rel)))}concat=n={len(rel)}:v=0:a=1[aout]")
    else:
        graph.append('[0:a]asetpts=PTS-STARTPTS,afade=t=in:d=0.05[aout]')
    inputs, extra = embed
    cmd += [*inputs, '-filter_complex', ';'.join(graph), '-map', '[vout]', '-map', '[aout]', *extra,
            '-c:v:0', 'libx264', '-preset:v:0', 'veryfast', '-crf:v:0', '20', '-profile:v:0', 'high',
            '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-movflags', '+faststart', '-f', 'mp4', str(output)]
    return cmd, total


class Shorts:
    def __init__(self, db, youtube, library, transcriber):
        self.db = db
        self.youtube = youtube
        self.library = library
        self.transcriber = transcriber
        self.jobs = queue.Queue()
        self._plans = {}

    def start(self):
        for row in self.db.all("SELECT id FROM shorts WHERE status IN ('queued', 'analyzing', 'rendering')"):
            self.jobs.put(('render', row['id']))
        self.db.run("UPDATE shorts SET status = 'done', error = 'Publishing was interrupted; try again' "
                    "WHERE status = 'publishing'")
        threading.Thread(target=self._worker, daemon=True).start()

    def framing_plan(self, path, start, end):
        """framing.plan, kept for each moment asked about (the editor asks again as you play)."""
        key = (str(path), round(start, 1), round(end, 1))
        if key not in self._plans:
            self._plans = dict(list(self._plans.items())[-20:])
            self._plans[key] = framing.plan(path, start, end)
        return self._plans[key]

    def options(self):
        # Each style: [name, label, where its middle sits down the frame, its band's height as a share of it]
        return {'available': AVAILABLE, 'max_length': MAX_LENGTH,
                'styles': [[k, v['label'], v['y'], captions.band_height(v) / HEIGHT] for k, v in captions.STYLES.items()]
                if captions else [],
                'accents': list(captions.ACCENTS.items()) if captions else []}

    def short(self, short_id):
        row = self.db.one('SELECT * FROM shorts WHERE id = ?', short_id)
        if row:
            row['spec'] = json.loads(row['spec'])
            row['published'] = json.loads(row['published'] or '[]')
        return row

    def for_event(self, event_id):
        return [self.short(r['id']) for r in self.db.all('SELECT id FROM shorts WHERE event_id = ? ORDER BY id', event_id)]

    def check(self, spec):
        """A clean spec, or ValueError saying what's wrong."""
        try:
            start, end = float(spec['start']), float(spec['end'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Pick where the short starts and ends')
        if end - start < MIN_LENGTH:
            raise ValueError(f'A short needs at least {MIN_LENGTH} seconds')
        if end - start > MAX_LENGTH + 0.5:
            raise ValueError('A short can be up to 3 minutes long')
        style = str(spec.get('style') or 'none')     # (anything but a name is no style, not a crash)
        if style != 'none' and style not in captions.STYLES:
            raise ValueError('Pick one of the caption styles')
        accent = str(spec.get('accent') or 'ember')
        if accent not in captions.ACCENTS:
            raise ValueError('Pick one of the accent colors')
        return {'source': str(spec.get('source', '')), 'start': max(0.0, start), 'end': end,
                'framing': spec.get('framing') if spec.get('framing') in FRAMINGS else 'fill',
                'style': style, 'accent': accent, 'trim_pauses': bool(spec.get('trim_pauses')),
                'brand': int(spec['brand']) if str(spec.get('brand') or '').isdigit() else None}

    def channel_brand(self, event_id):
        """The brand of the broadcast's first channel that has one, or None."""
        row = self.db.one('SELECT c.brand_id FROM event_targets t JOIN youtube_channels c ON c.id = t.channel_id '
                          'WHERE t.event_id = ? AND c.brand_id IS NOT NULL ORDER BY t.id LIMIT 1', event_id)
        return row['brand_id'] if row else None

    def brand_look(self, brand_id):
        """A brand's watermark for render_command, or None."""
        from .media import folder
        row = self.db.one('SELECT b.watermark_corner, b.watermark_size, b.watermark_opacity, m.file FROM brands b '
                          'JOIN media m ON m.id = b.watermark WHERE b.id = ?', brand_id) if brand_id else None
        path = row and folder() / row['file']
        return {'watermark': str(path), 'corner': row['watermark_corner'], 'size': row['watermark_size'],
                'opacity': row['watermark_opacity']} if path and path.exists() else None

    def create(self, event, title, spec):
        if not AVAILABLE:
            raise ValueError('Shorts need the transcription add-on, which this hub doesn\'t have')
        # A short gets its channel's brand unless it says otherwise (brand: null for none)
        spec = self.check({**spec, 'brand': spec['brand'] if 'brand' in spec else self.channel_brand(event['id'])})
        short_id = self.db.run("INSERT INTO shorts (event_id, title, spec, status, duration, created_at) "
                               "VALUES (?, ?, ?, 'queued', ?, ?)", event['id'], title, json.dumps(spec),
                               spec['end'] - spec['start'], time.time())
        self.jobs.put(('render', short_id))
        return short_id

    def change(self, short_id, title, spec):
        """Make it again with new settings: the new video replaces the old one when it's ready."""
        short = self.short(short_id)
        if short['status'] in ('queued', 'analyzing', 'rendering', 'publishing'):
            raise ValueError('Wait for it to finish first')
        spec = self.check({**spec, 'brand': spec['brand'] if 'brand' in spec else short['spec'].get('brand')})
        self.db.run("UPDATE shorts SET title = ?, spec = ?, status = 'queued', progress = 0, error = NULL, "
                    'duration = ? WHERE id = ?', title, json.dumps(spec), spec['end'] - spec['start'], short_id)
        self.jobs.put(('render', short_id))

    def rename(self, short_id, title):
        self.db.run('UPDATE shorts SET title = ? WHERE id = ?', title, short_id)

    def delete(self, short_id):
        short = self.short(short_id)
        if short and short['status'] in ('queued', 'analyzing', 'rendering', 'publishing'):
            raise ValueError('Wait for it to finish first')   # its render would file the video afterwards
        if short and short['output']:
            self.library.delete(short['output'])
        self.db.run('DELETE FROM shorts WHERE id = ?', short_id)

    def _worker(self):
        while True:
            kind, short_id, *args = self.jobs.get()
            try:
                if kind == 'render':
                    self._render(short_id)
                else:
                    self._publish(short_id, *args)
            except Exception as e:
                print(f"Short {short_id} {kind} failed: {e}")
                self.db.run('UPDATE shorts SET status = ?, error = ? WHERE id = ?',
                            'failed' if kind == 'render' else 'done', str(e)[:500], short_id)

    def _render(self, short_id):
        short = self.short(short_id)
        if not short:
            return
        spec = short['spec']
        event = self.db.one('SELECT * FROM events WHERE id = ?', short['event_id'])
        source = self.library.resolve(spec['source'])
        if not source or not event:
            raise ValueError('The recording or broadcast is gone')
        self.db.run("UPDATE shorts SET status = 'analyzing', progress = 0, error = NULL WHERE id = ?", short_id)
        info = framing.video_info(source)
        start, end = spec['start'], spec['end']
        ranges = keep_ranges(start, end, quiet(source, start, end)) if spec['trim_pauses'] else [(start, end)]
        # Sections start and end on frames, so picture and sound stay together across cuts
        ranges = [(round(a * info[2]) / info[2], round(b * info[2]) / info[2]) for a, b in ranges]
        transcript = self.transcriber.transcript(event['id'])
        words = place(words_between(transcript['segments'], start, end), ranges) \
            if transcript and transcript['status'] == 'done' else []
        previous = self.library.resolve(short['output']) if short['output'] else None
        number, output = (None, previous) if previous else self.library.new_target(event, 'short')
        partial = output.with_name(f'.{output.name}.part')
        with tempfile.TemporaryDirectory() as tmp:
            folder, crop, whole = Path(tmp), None, ()
            if spec['framing'] == 'fill':
                crop_width = round(info[1] * 9 / 16 / 2) * 2
                # Where the speaker is, in the short's own time (pauses taken out)
                shifted, offset = [], 0.0
                samples = framing.sample(source, start, end)
                for a, b in ranges:
                    shifted += [(offset + t + start - a, found) for t, found in samples if a <= t + start < b]
                    offset += b - a
                points = framing.speaker(shifted)
                path = framing.follow(points, offset, crop_width / info[0])
                commands, first = framing.crop_commands(path, info[0], crop_width)
                (folder / 'crop.cmd').write_text(commands)
                crop = (crop_width, first)
                whole = framing.unseen(points, offset)
            listing = captions.frames(words, spec['style'], captions.ACCENTS[spec['accent']], folder) \
                if spec['style'] != 'none' and words else None
            brand = self.brand_look(spec.get('brand'))
            cmd, total = render_command(source, info, ranges, spec, partial, crop, listing,
                                        self.library.embed_args(event, 1 + bool(listing) + bool(brand)), whole, brand)
            self.db.run("UPDATE shorts SET status = 'rendering', duration = ? WHERE id = ?", total, short_id)
            print(f"Rendering short {short_id}: {total:.0f} s in {len(ranges)} section(s), {spec['framing']}")
            proc = subprocess.Popen(cmd, cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in proc.stdout:
                if line.startswith('out_time_us=') and line.strip()[12:].isdigit():
                    self.db.run('UPDATE shorts SET progress = ? WHERE id = ?',
                                min(0.99, int(line.strip()[12:]) / 1e6 / total), short_id)
            if proc.wait() != 0:
                partial.unlink(missing_ok=True)
                raise RuntimeError((proc.stderr.read().strip().splitlines() or ['FFmpeg failed'])[-1])
        partial.replace(output)
        ref = short['output'] if previous else f"library/{self.library.add(event, 'short', number, output)}"
        self.db.run("UPDATE shorts SET status = 'done', progress = 1, output = ? WHERE id = ?", ref, short_id)
        self.library.write_extras(event['id'])
        print(f"Short {short_id} rendered")

    # ---- Publishing to YouTube ----
    def publish(self, short_id, request):
        self.db.run("UPDATE shorts SET status = 'publishing', progress = 0, error = NULL WHERE id = ?", short_id)
        self.jobs.put(('publish', short_id, request))

    def _publish(self, short_id, request):
        short = self.short(short_id)
        event = self.db.one('SELECT * FROM events WHERE id = ?', short['event_id'])
        path = self.library.resolve(short['output'])
        description = request['description']
        if '#shorts' not in (request['title'] + description).lower():
            description = (description + '\n\n#Shorts').strip()
        results, errors = short['published'], []
        channels = request['channel_ids']
        for n, channel_id in enumerate(channels):
            channel = self.youtube.channel(channel_id)
            try:
                def progress(fraction, n=n):
                    self.db.run('UPDATE shorts SET progress = ? WHERE id = ?', (n + fraction) / len(channels), short_id)
                video_id = self.youtube.upload_video(channel, path, {
                    'title': request['title'], 'description': description, 'privacy': request['privacy'],
                    'made_for_kids': bool(event['made_for_kids'])}, progress)
                results.append({'channel_id': channel_id, 'channel': channel['title'], 'video_id': video_id,
                                'url': f'https://youtube.com/shorts/{video_id}', 'at': time.time()})
            except YouTubeError as e:
                errors.append(f"{channel['title'] if channel else channel_id}: {e}")
        self.db.run("UPDATE shorts SET status = 'done', progress = 1, published = ?, error = ? WHERE id = ?",
                    json.dumps(results), '; '.join(errors) or None, short_id)
