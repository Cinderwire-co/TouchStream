"""The hub's media library and brands

Media: videos, images and sound uploaded to the hub (an intro, a slide, a logo, music), kept in
<data>/media and usable in any edit: a video or image as a clip on the timeline, sound as music
under it, an image as a watermark.

Brands: a look for a channel or a ministry, as many as you like. Each has an intro and an outro (a
video, or an image shown for a few seconds) and a watermark image in a corner. Picking a brand in
the editor puts its intro and outro on the timeline and its watermark over the recording. A YouTube
channel can have a brand, so the editor picks it for that channel's broadcasts.
"""
import json
import re
import subprocess
import time
from pathlib import Path

from . import settings

KINDS = {'video': ('.mp4', '.mov', '.m4v', '.webm', '.mkv'), 'image': ('.png', '.jpg', '.jpeg', '.webp'),
         'audio': ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.flac')}
MAX_BYTES = 4 * 1024 ** 3
CORNERS = ('top_left', 'top_right', 'bottom_left', 'bottom_right')
BRAND_FIELDS = ('name', 'intro', 'outro', 'watermark', 'thumbnail', 'watermark_corner', 'watermark_size', 'watermark_opacity',
                'intro_seconds', 'outro_seconds')
THUMBNAIL_MAX = 2 * 1024 * 1024   # YouTube's limit for a thumbnail


def folder():
    path = settings.DATA_DIR / 'media'
    path.mkdir(parents=True, exist_ok=True)
    return path


def kind_of(name):
    suffix = Path(name).suffix.lower()
    return next((k for k, suffixes in KINDS.items() if suffix in suffixes), None)


def probe(path):
    """{duration, width, height, audio} of a media file."""
    out = subprocess.run([settings.FFPROBE_BIN, '-v', 'error', '-show_entries',
                          'format=duration:stream=codec_type,width,height', '-of', 'json', str(path)],
                         capture_output=True, text=True)
    data = json.loads(out.stdout or '{}')
    streams = data.get('streams') or []
    video = next((s for s in streams if s.get('codec_type') == 'video'), {})
    try:
        duration = float((data.get('format') or {}).get('duration') or 0)
    except ValueError:
        duration = 0.0
    return {'duration': duration, 'width': video.get('width'), 'height': video.get('height'),
            'audio': any(s.get('codec_type') == 'audio' for s in streams)}


class Media:
    def __init__(self, db):
        self.db = db

    def get(self, media_id):
        row = self.db.one('SELECT * FROM media WHERE id = ?', media_id)
        if row:
            row['has_audio'] = bool(row['has_audio'])
        return row

    def path(self, media_id):
        row = self.get(media_id)
        path = folder() / row['file'] if row else None
        return path if path and path.is_file() else None

    def all(self):
        return [self.get(r['id']) for r in self.db.all('SELECT id FROM media ORDER BY created_at DESC')]

    async def add(self, name, chunks):
        """Store an upload (an async iterator of bytes). Returns its row, or raises ValueError."""
        kind = kind_of(name)
        if not kind:
            raise ValueError('Use a video (MP4, MOV), an image (PNG, JPG) or sound (MP3, M4A, WAV)')
        safe = re.sub(r'[^A-Za-z0-9._ -]', '', Path(name).name).strip() or f'media{Path(name).suffix}'
        target = folder() / f'{int(time.time() * 1000)}-{safe}'
        size = 0
        with open(target, 'wb') as f:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_BYTES:
                    f.close()
                    target.unlink(missing_ok=True)
                    raise ValueError('That file is over 4 GB')
                f.write(chunk)
        info = probe(target)
        if kind != 'image' and not info['duration']:
            target.unlink(missing_ok=True)
            raise ValueError("That file couldn't be read as video or sound")
        media_id = self.db.run('INSERT INTO media (name, kind, file, duration, width, height, has_audio, created_at) '
                               'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', Path(name).stem[:80], kind, target.name,
                               info['duration'] or None, info['width'], info['height'], int(info['audio']), time.time())
        return self.get(media_id)

    def rename(self, media_id, name):
        self.db.run('UPDATE media SET name = ? WHERE id = ?', name[:80], media_id)

    def delete(self, media_id):
        path = self.path(media_id)
        self.db.run('DELETE FROM media WHERE id = ?', media_id)
        if path:
            path.unlink(missing_ok=True)

    def poster(self, media_id):
        """A still of a video (cached), for the timeline and the library."""
        row, path = self.get(media_id), self.path(media_id)
        if not row or not path:
            return None
        if row['kind'] == 'image':
            return path
        if row['kind'] != 'video':
            return None
        target = folder() / f".poster-{row['id']}.jpg"
        if not target.exists():
            subprocess.run([settings.FFMPEG_BIN, '-v', 'error', '-y', '-ss', str(min(1.0, (row['duration'] or 0) / 2)),
                            '-i', str(path), '-frames:v', '1', '-vf', 'scale=320:-2', str(target)])
        return target if target.exists() else None

    # ---- Brands ----
    def brands(self):
        brands = self.db.all('SELECT * FROM brands ORDER BY name')
        channels = self.db.all('SELECT id, title, brand_id FROM youtube_channels')
        for b in brands:
            b['channel_ids'] = [c['id'] for c in channels if c['brand_id'] == b['id']]
        return brands

    def brand(self, brand_id):
        return next((b for b in self.brands() if b['id'] == brand_id), None)

    def save_brand(self, brand_id, data):
        """Create (brand_id None) or change a brand. Returns it, or raises ValueError."""
        fields = {}
        if 'name' in data or brand_id is None:
            fields['name'] = str(data.get('name') or '').strip()[:60]
            if not fields['name']:
                raise ValueError('Give the brand a name')
        for slot in ('intro', 'outro', 'watermark', 'thumbnail'):
            if slot in data:
                media = self.get(int(data[slot])) if data[slot] else None
                if data[slot] and not media:
                    raise ValueError(f'That {slot} file is gone')
                if media and slot == 'watermark' and media['kind'] != 'image':
                    raise ValueError('The watermark needs to be an image (a PNG with a transparent background is best)')
                if media and slot == 'thumbnail' and (media['kind'] != 'image' or Path(media['file']).suffix.lower() not in ('.jpg', '.jpeg', '.png')):
                    raise ValueError('The thumbnail needs to be a JPG or PNG image (1280×720 is ideal)')
                if media and slot == 'thumbnail' and (folder() / media['file']).stat().st_size > THUMBNAIL_MAX:
                    raise ValueError('YouTube takes thumbnails up to 2 MB: use a smaller JPG')
                if media and slot not in ('watermark', 'thumbnail') and media['kind'] == 'audio':
                    raise ValueError(f'The {slot} needs to be a video or an image')
                fields[slot] = media['id'] if media else None
        if 'watermark_corner' in data:
            fields['watermark_corner'] = data['watermark_corner'] if data['watermark_corner'] in CORNERS else 'bottom_right'
        for key, low, high in (('watermark_size', 4, 40), ('watermark_opacity', 10, 100),
                               ('intro_seconds', 1, 30), ('outro_seconds', 1, 30)):
            if key in data:
                fields[key] = max(low, min(high, int(data[key])))
        if brand_id is None:
            brand_id = self.db.run('INSERT INTO brands (name, created_at) VALUES (?, ?)', fields.pop('name'), time.time())
        if fields:
            self.db.run(f"UPDATE brands SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", *fields.values(), brand_id)
        if 'channel_ids' in data:
            self.db.run('UPDATE youtube_channels SET brand_id = NULL WHERE brand_id = ?', brand_id)
            for channel_id in data['channel_ids'] or []:
                self.db.run('UPDATE youtube_channels SET brand_id = ? WHERE id = ?', brand_id, int(channel_id))
        return self.brand(brand_id)

    def channel_thumbnail(self, channel_row_id):
        """The default thumbnail of a channel's brand, as (bytes, content type), or None."""
        row = self.db.one('SELECT m.file FROM youtube_channels c JOIN brands b ON b.id = c.brand_id '
                          'JOIN media m ON m.id = b.thumbnail WHERE c.id = ?', channel_row_id)
        path = folder() / row['file'] if row else None
        if not path or not path.exists():
            return None
        return path.read_bytes(), 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'

    def delete_brand(self, brand_id):
        self.db.run('UPDATE youtube_channels SET brand_id = NULL WHERE brand_id = ?', brand_id)
        self.db.run('DELETE FROM brands WHERE id = ?', brand_id)
