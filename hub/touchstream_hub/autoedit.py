"""Edits made by themselves: once a broadcast is transcribed, its edit profile cuts it, renders it and leaves it to
look over (or publishes it)

An edit profile (Settings > Editing; a broadcast or a series picks one) says what to keep:
- keep: 'service', the whole thing with the dead air before the first words and after the last trimmed off, or
  'message', just the preaching or talk, which the assistant finds in the transcript;
- cut_music: the songs taken out (the assistant knows them by their lyrics);
- trim_pauses: long pauses taken out (off for most sermons, where a pause is part of the delivery; Shorts have
  their own setting);
- instructions: what this church keeps and cuts, in its own words, for the assistant;
- brand: the channel's brand goes on (its intro, outro, watermark and title color);
- after: 'review' (it waits on the broadcast to be looked over) or 'publish' (it goes to YouTube by itself, with
  the profile's visibility, and the live video made private or unlisted if asked).

It learns. When someone renders an edit of a broadcast that has a profile (or publishes one it made), what they kept
and cut, with the words where the cuts fall, becomes an example the assistant reads next time: the newest EXAMPLES
of them. So correcting an edit it made teaches it. Without the assistant it can only trim the dead air before the
first words and after the last.
"""
import asyncio
import json
import time

from .assistant import AssistantError
from .editor import probe_duration
from .shorts import keep_ranges, quiet, sentences

KEEPS = ('service', 'message')
AFTER = ('review', 'publish')
PRIVACY = ('public', 'unlisted', 'private')
HIDE = ('private', 'unlisted', 'keep')
EXAMPLES = 8            # examples kept for each profile, newest first
LEAD, TAIL = 1.0, 2.0   # seconds kept before the first words and after the last
LONG_PAUSE = 1.5        # with trim_pauses, the pauses taken out (shorter ones belong to the speech)
PAUSE_PAD = 0.35        # seconds kept either side of one
GAP = 8.0               # stretches further apart than this are separate parts: a dissolve between them, not a cut
FADE = 0.5              # seconds of those dissolves


def clock(t):
    t = int(t)
    return f'{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}'


def merge(ranges, gap=0.5):
    """[(start, end)] sorted, with ones that overlap or nearly touch joined."""
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def kept_ranges(spec, duration):
    """What of the recording an edit keeps, [(start, end)] in the recording's time: its own clips on the video
    track (or, in the simple form, in to out less the cuts)."""
    if 'video' in spec:
        return merge((float(c['start']), float(c['end'])) for c in spec['video'] if not c.get('media'))
    cursor, out = float(spec.get('in') or 0), []
    for a, b in sorted(spec.get('cuts') or []):
        if a > cursor:
            out.append((cursor, a))
        cursor = max(cursor, b)
    end = float(spec['out']) if spec.get('out') else duration
    return merge(out + [(cursor, end)] if end > cursor else out)


def summary(said, kept, duration):
    """An edit in words, for the assistant: each stretch kept, from which words to which, and each cut and how it
    began. said: the transcript's sentences."""
    def first(a, b):
        s = next((s for s in said if a - 0.5 <= s['start'] < b), None)
        return s['text'][:100] if s else ''

    def last(a, b):
        s = next((s for s in reversed(said) if a < s['end'] <= b + 0.5), None)
        return s['text'][-100:] if s else ''

    def cut(a, b):
        words = first(a, b)
        return f'Cut {clock(a)} to {clock(b)}' + (f' (it began: "{words}")' if words else '')

    lines, cursor = [], 0.0
    for a, b in kept:
        if a - cursor > 5:
            lines.append(cut(cursor, a))
        lines.append(f'Kept {clock(a)} to {clock(b)}, from "{first(a, b) or "..."}" to "{last(a, b) or "..."}"')
        cursor = b
    if duration - cursor > 5:
        lines.append(cut(cursor, duration))
    return '\n'.join(lines)


def build_spec(ranges, brand=None, media=None):
    """An edit in the editor's tracks: these stretches of the recording back to back, the brand's intro and outro at
    the ends, a dissolve between stretches that are parts apart (a plain cut where only a pause came out)."""
    media = media or {}
    pieces = []                      # (media id or None, start, end)
    intro, outro = brand and media.get(brand.get('intro')), brand and media.get(brand.get('outro'))
    if intro:
        pieces.append((intro['id'], 0.0, intro['duration'] if intro['kind'] == 'video' else float(brand['intro_seconds'])))
    pieces += [(None, a, b) for a, b in ranges]
    if outro:
        pieces.append((outro['id'], 0.0, outro['duration'] if outro['kind'] == 'video' else float(brand['outro_seconds'])))
    video, audio, at, previous = [], [], 0.0, None
    for n, (mid, a, b) in enumerate(pieces):
        apart = n and (mid or previous[0] or a - previous[2] > GAP)
        clip = {'at': at, 'start': a, 'end': b, 'link': f'a{n}', **({'media': mid} if mid else {})}
        video.append({**clip, 'transition': {'type': 'dissolve', 'duration': FADE} if apart else None})
        if not mid or media[mid]['has_audio']:
            audio.append(clip)
        at += b - a
        previous = (mid, a, b)
    return {'video': video, 'audio': audio, 'music': [], 'titles': [],
            'color': {'brightness': 0, 'contrast': 0, 'saturation': 0, 'warmth': 0},
            'brand': brand['id'] if brand else None, 'fade_in': 1.0, 'fade_out': 2.0}


class AutoEdits:
    def __init__(self, db, editor, media, transcriber, assistant):
        self.db, self.editor, self.media = db, editor, media
        self.transcriber, self.assistant = transcriber, assistant

    # ---- Profiles ----
    def profiles(self):
        counts = {r['profile_id']: r['n'] for r in
                  self.db.all('SELECT profile_id, count(*) AS n FROM edit_examples GROUP BY profile_id')}
        return [self._view(p, counts.get(p['id'], 0)) for p in self.db.all('SELECT * FROM edit_profiles ORDER BY name')]

    def profile(self, profile_id):
        return next((p for p in self.profiles() if p['id'] == profile_id), None) if profile_id else None

    @staticmethod
    def _view(p, examples):
        return {**p, **{k: bool(p[k]) for k in ('cut_music', 'trim_pauses', 'brand', 'learn')}, 'examples': examples}

    def save(self, profile_id, data):
        """Create (profile_id None) or change a profile. Returns it, or raises ValueError."""
        f = {}
        if 'name' in data or profile_id is None:
            f['name'] = str(data.get('name') or '').strip()[:60]
            if not f['name']:
                raise ValueError('Give the profile a name')
        for key, allowed in (('keep', KEEPS), ('after', AFTER), ('privacy', PRIVACY), ('hide_live', HIDE)):
            if key in data:
                if data[key] not in allowed:
                    raise ValueError(f'{key} must be one of {", ".join(allowed)}')
                f[key] = data[key]
        for key in ('cut_music', 'trim_pauses', 'brand', 'learn'):
            if key in data:
                f[key] = int(bool(data[key]))
        if 'instructions' in data:
            f['instructions'] = str(data['instructions'] or '').strip()[:2000]
        if profile_id is None:
            profile_id = self.db.run('INSERT INTO edit_profiles (name, created_at) VALUES (?, ?)', f.pop('name'), time.time())
        if f:
            self.db.run(f"UPDATE edit_profiles SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", *f.values(), profile_id)
        return self.profile(profile_id)

    def delete(self, profile_id):
        self.db.run('DELETE FROM edit_profiles WHERE id = ?', profile_id)   # broadcasts and series using it: none now

    def examples(self, profile_id):
        return self.db.all('SELECT * FROM edit_examples WHERE profile_id = ? ORDER BY created_at DESC', profile_id)

    def forget(self, example_id):
        self.db.run('DELETE FROM edit_examples WHERE id = ?', example_id)

    # ---- Learning ----
    def learn(self, event, source_path, spec):
        """An edit of a broadcast with a profile was rendered or published by someone: remember what they kept, to do
        it their way next time (the same broadcast's earlier example is replaced)."""
        profile = self.profile(event.get('edit_profile_id'))
        path = self.editor.library.resolve(source_path)
        if not profile or not profile['learn'] or not path:
            return
        duration = probe_duration(path)
        kept = merge(kept_ranges(spec, duration), GAP)
        if not kept:
            return
        transcript = self.transcriber.transcript(event['id'])
        said = sentences(transcript['segments']) if transcript and transcript['status'] == 'done' else []
        self.db.run('DELETE FROM edit_examples WHERE profile_id = ? AND event_id = ?', profile['id'], event['id'])
        self.db.run('INSERT INTO edit_examples (profile_id, event_id, title, summary, kept, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                    profile['id'], event['id'], event['title'], summary(said, kept, duration), json.dumps(kept), time.time())
        self.db.run('DELETE FROM edit_examples WHERE profile_id = ? AND id NOT IN (SELECT id FROM edit_examples '
                    'WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?)', profile['id'], profile['id'], EXAMPLES)

    # ---- Making one ----
    async def plan(self, event, profile):
        """(recording, [(start, end)] to keep, why) for a broadcast, its profile's way. Times are in the recording the
        transcript was made from."""
        transcript = self.transcriber.transcript(event['id'])
        if not transcript or transcript['status'] != 'done':
            raise ValueError('It needs its transcript first')
        path = self.editor.library.resolve(transcript['source'])
        if not path:
            raise ValueError('Its recording is gone')
        duration = probe_duration(path)
        said = sentences(transcript['segments'])
        if not said:
            raise ValueError('Nothing was said in it')
        wants_ai = profile['keep'] == 'message' or profile['cut_music'] or profile['instructions']
        ranges, why = None, 'From the first words to the last'
        if wants_ai and self.assistant.enabled():
            try:
                picked, why = await self.assistant.plan_edit(event['title'], said, profile, self.examples(profile['id']))
                ranges = [(max(0.0, said[a]['start'] - 0.4), min(duration, said[b]['end'] + 0.8)) for a, b in picked]
            except AssistantError as e:   # (still an edit, the simple way, rather than none)
                why += f": the assistant couldn't plan it ({e})"
        elif wants_ai:
            why += ': the rest of this profile needs the assistant (Settings > Assistant)'
        if not ranges:
            ranges = [(max(0.0, said[0]['start'] - LEAD), min(duration, said[-1]['end'] + TAIL))]
        ranges = merge(ranges)
        if profile['trim_pauses']:
            def without_pauses(a, b):
                long = [q for q in quiet(path, a, b) if q[1] - q[0] >= LONG_PAUSE]
                return keep_ranges(a, b, long, pause=LONG_PAUSE, pad=PAUSE_PAD)
            ranges = [r for a, b in ranges for r in await asyncio.to_thread(without_pauses, a, b)]
            why += ', with the long pauses taken out'
        return transcript['source'], ranges, why

    def channel_brand(self, event_id):
        row = self.db.one('SELECT b.* FROM event_targets t JOIN youtube_channels c ON c.id = t.channel_id '
                          'JOIN brands b ON b.id = c.brand_id WHERE t.event_id = ? ORDER BY t.id LIMIT 1', event_id)
        return self.media.brand(row['id']) if row else None

    async def make(self, event_id, again=False):
        """A broadcast's edit, made its profile's way and rendered: then it waits to be looked over, or publishes.
        Once per broadcast, and never over an edit someone made or is making by hand (again: asked for, over one a
        profile made)."""
        self.db.run('UPDATE events SET auto_edit_at = ? WHERE id = ?', time.time(), event_id)   # once, come what may
        event = self.db.one('SELECT * FROM events WHERE id = ?', event_id)
        profile = self.profile(event['edit_profile_id']) if event else None
        current = self.editor.current(event_id)
        if not profile or (current and not (again and current['auto'])) or (event['edit_draft'] and not again):
            return None
        source, ranges, why = await self.plan(event, profile)
        if not ranges:
            raise ValueError('Nothing was left to keep')
        brand = self.channel_brand(event_id) if profile['brand'] else None
        spec = build_spec(ranges, brand, {m['id']: m for m in self.media.all()})
        channels = [t['channel_id'] for t in self.db.all('SELECT channel_id FROM event_targets WHERE event_id = ?', event_id)]
        publish = {'channel_ids': channels, 'title': event['title'], 'description': event['description'],
                   'privacy': profile['privacy'], 'hide_original': profile['hide_live'], 'hide_previous': 'private'} \
            if profile['after'] == 'publish' and channels else None
        return self.editor.create(event, source, spec, auto={'profile_id': profile['id'], 'profile': profile['name'],
                                                             'why': why, 'publish': publish})
