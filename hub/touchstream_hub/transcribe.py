"""Transcripts: speech-to-text on the hub with Whisper (faster-whisper), editable and exportable

Runs on the hub's own CPU (or GPU) for free; nothing leaves the hub. After a
broadcast ends the hub transcribes its recording (preferring the spoke's copy,
which has no network gaps). Transcripts can be corrected in the dashboard and
downloaded as SRT/VTT/TXT, with timings shifted to match any edited version.

Models download on first use into <data>/models (base about 150 MB, small 500 MB).
"""
import json
import queue
import subprocess
import tempfile
import threading
import time
import wave

from . import settings
from .editor import probe_duration

try:
    import numpy
    from faster_whisper import WhisperModel
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

MODELS = ('tiny', 'base', 'small', 'medium')
AUTO_DELAY = 180        # seconds after a broadcast ends, so the spoke's copy can finish uploading
CAPTION_SECONDS = 6     # longest caption on screen
CAPTION_CHARS = 84      # about two lines


def load_wav(path):
    """16-bit mono WAV -> float samples. Handing Whisper samples (not a file) skips its PyAV
    decoder, which breaks whenever PyAV changes its API."""
    with wave.open(path, 'rb') as w:
        pcm = w.readframes(w.getnframes())
    return numpy.frombuffer(pcm, dtype=numpy.int16).astype(numpy.float32) / 32768.0


def captions_from_words(words):
    """Group timed words into caption-sized lines: at most ~6 s and two lines,
    breaking early at the end of a sentence. Each line keeps its words' timings (for shorts)."""
    out, cur = [], []

    def line():
        return {'start': round(cur[0].start, 2), 'end': round(cur[-1].end, 2),
                'text': ''.join(x.word for x in cur).strip(),
                'words': [[round(x.start, 2), round(x.end, 2), x.word.strip()] for x in cur if x.word.strip()]}
    for w in words:
        cur.append(w)
        text = ''.join(x.word for x in cur).strip()
        long = cur[-1].end - cur[0].start >= CAPTION_SECONDS or len(text) >= CAPTION_CHARS
        sentence_end = text.endswith(('.', '?', '!')) and cur[-1].end - cur[0].start >= 1.5
        if long or sentence_end:
            out.append(line())
            cur = []
    if cur:
        out.append(line())
    return out


# ---- Export formats ----
def _stamp(t, sep):
    ms = int(round(t * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}'


def to_srt(segments):
    return '\n'.join(f"{i}\n{_stamp(s['start'], ',')} --> {_stamp(s['end'], ',')}\n{s['text'].strip()}\n"
                     for i, s in enumerate(segments, 1))


def to_vtt(segments):
    return 'WEBVTT\n\n' + '\n'.join(f"{_stamp(s['start'], '.')} --> {_stamp(s['end'], '.')}\n{s['text'].strip()}\n"
                                    for s in segments)


def to_txt(segments):
    return '\n'.join(s['text'].strip() for s in segments) + '\n'


def remap(segments, ranges):
    """Shift transcript timings onto an edit that keeps only `ranges` of the recording: (start, end)
    back to back, or (start, end, where it lands in the edit) when other clips sit between them."""
    out, offset = [], 0.0
    for a, b, *at in ranges:
        offset = at[0] if at else offset
        for s in segments:
            start, end = max(s['start'], a), min(s['end'], b)
            if end - start > 0.2:
                out.append({'start': offset + start - a, 'end': offset + end - a, 'text': s['text']})
        offset += b - a
    return out


class Transcriber:
    def __init__(self, db, library):
        self.db = db
        self.library = library
        self.jobs = queue.Queue()
        self._model = None
        self._model_name = None

    def options(self):
        return {'enabled': bool(self.db.get_setting('transcribe_enabled', True)),
                'model': self.db.get_setting('transcribe_model', 'base'),
                'language': self.db.get_setting('transcribe_language', ''),
                # Names Whisper should listen for (it otherwise hears "Cinder Wire")
                'vocabulary': self.db.get_setting('transcribe_vocabulary', 'TouchStream, Cinderwire'),
                'available': AVAILABLE}

    def start(self):
        if not AVAILABLE:
            print("Transcription unavailable (install touchstream-hub[transcribe])")
            return
        for row in self.db.all("SELECT event_id FROM transcripts WHERE status IN ('queued', 'running')"):
            self.jobs.put(row['event_id'])
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._auto, daemon=True).start()

    def transcript(self, event_id):
        row = self.db.one('SELECT * FROM transcripts WHERE event_id = ?', event_id)
        if row:
            row['segments'] = json.loads(row['segments'] or '[]')
            row['edited'] = bool(row['edited'])
        return row

    def request(self, event_id, source):
        current = self.transcript(event_id)
        if current and current['status'] in ('queued', 'running'):
            return
        self.db.run("INSERT INTO transcripts (event_id, source, status, progress, created_at) "
                    "VALUES (?, ?, 'queued', 0, ?) ON CONFLICT(event_id) DO UPDATE SET source = excluded.source, "
                    "status = 'queued', progress = 0, error = NULL", event_id, source, time.time())
        self.jobs.put(event_id)

    def save(self, event_id, segments):
        clean = []
        for s in segments:
            text = str(s.get('text', ''))[:2000]
            if not text.strip():
                continue
            line = {'start': float(s['start']), 'end': float(s['end']), 'text': text}
            # Word timings stay while the line still has the same words
            words = s.get('words') if isinstance(s.get('words'), list) else []
            if words and ' '.join(str(w[2]) for w in words) == ' '.join(text.split()):
                line['words'] = [[float(w[0]), float(w[1]), str(w[2])] for w in words]
            clean.append(line)
        self.db.run('UPDATE transcripts SET segments = ?, edited = 1, updated_at = ? WHERE event_id = ?',
                    json.dumps(clean), time.time(), event_id)

    @staticmethod
    def best_copy(copies):
        """The spoke's copy when it's complete (no network gaps), else the hub's."""
        done = [c for c in copies if c['path'] and not c['in_progress']]
        spoke = [c for c in done if c['source'] == 'spoke']
        return (spoke or done or [None])[0]

    def _auto(self):
        while True:
            time.sleep(60)
            try:
                if not self.options()['enabled']:
                    continue
                cutoff = time.time() - AUTO_DELAY
                for event in self.db.all("SELECT e.* FROM events e LEFT JOIN transcripts t ON t.event_id = e.id "
                                         "WHERE e.state = 'ended' AND t.id IS NULL "
                                         "AND COALESCE(e.ended_at, e.end_at, e.start_at) < ?", cutoff):
                    copy = self.best_copy(self.library.event_copies(event))
                    if copy:
                        self.request(event['id'], copy['path'])
            except Exception as e:
                print(f"Auto-transcribe error: {e}")

    def _worker(self):
        while True:
            event_id = self.jobs.get()
            try:
                self._run(event_id)
            except Exception as e:
                print(f"Transcribing broadcast {event_id} failed: {e}")
                self.db.run("UPDATE transcripts SET status = 'failed', error = ? WHERE event_id = ?",
                            str(e)[:500], event_id)

    def _load(self, name):
        if self._model_name != name:
            print(f"Loading Whisper '{name}' model (downloads on first use)")
            self._model = WhisperModel(name, device='auto', compute_type='int8',
                                       download_root=str(settings.DATA_DIR / 'models'))
            self._model_name = name
        return self._model

    def _run(self, event_id):
        row = self.transcript(event_id)
        if not row or row['status'] not in ('queued', 'running'):
            return
        source = self.library.resolve(row['source'])
        if not source:
            raise ValueError('The recording is gone')
        opts = self.options()
        self.db.run("UPDATE transcripts SET status = 'running', progress = 0, model = ? WHERE event_id = ?",
                    opts['model'], event_id)
        duration = probe_duration(source) or 1
        # (A folder, not an open temp file: Windows won't let FFmpeg write to an open file)
        with tempfile.TemporaryDirectory() as tmp:
            wav = f'{tmp}/audio.wav'
            # Whisper wants 16 kHz mono; extracting first is much faster than decoding video in Python
            subprocess.run([settings.FFMPEG_BIN, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                            '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', wav], check=True)
            model = self._load(opts['model'])
            started = time.time()
            # The word list goes in as an opening prompt: it fixes spelling without the
            # "hotwords" option's side effect of dropping timestamps
            segments, info = model.transcribe(
                load_wav(wav), language=opts['language'] or None, vad_filter=True, word_timestamps=True,
                initial_prompt=f"{opts['vocabulary']}." if opts['vocabulary'] else None)
            words = []
            for seg in segments:
                words += seg.words or []
                self.db.run('UPDATE transcripts SET progress = ? WHERE event_id = ?',
                            min(0.99, seg.end / duration), event_id)
            out = captions_from_words(words)
        self.db.run("UPDATE transcripts SET status = 'done', progress = 1, segments = ?, language = ?, edited = 0, "
                    'updated_at = ? WHERE event_id = ?', json.dumps(out), info.language, time.time(), event_id)
        print(f"Transcribed broadcast {event_id}: {len(out)} lines in {time.time() - started:.0f} s ({info.language})")
        self.library.write_extras(event_id)   # captions beside the recordings
