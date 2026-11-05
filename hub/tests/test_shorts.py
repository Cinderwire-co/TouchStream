"""Shorts: picking words and sections, captions, the speaker-following path, and a real render (needs FFmpeg)"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from touchstream_hub import settings, shorts
from touchstream_hub.db import Database
from touchstream_hub.library import Library

try:
    from touchstream_hub import captions, framing
except ImportError:          # without the transcribe extra
    captions = framing = None


class Sections(unittest.TestCase):
    def test_pauses_become_cuts_with_a_little_kept_either_side(self):
        ranges = shorts.keep_ranges(10, 30, [(12, 14), (20, 20.3), (25, 31)])
        self.assertEqual(ranges, [(10, 12.15), (13.85, 25.15)])   # the 0.3 s pause is too short to cut

    def test_words_are_retimed_into_the_short(self):
        words = [{'start': 10.0, 'end': 10.5, 'text': 'one'}, {'start': 13.0, 'end': 13.4, 'text': 'gone'},
                 {'start': 15.0, 'end': 15.5, 'text': 'two'}]
        placed = shorts.place(words, [(10, 12), (14, 16)])
        self.assertEqual([w['text'] for w in placed], ['one', 'two'])
        self.assertAlmostEqual(placed[1]['start'], 3.0)            # 2 s of the first section, then 1 s in

    def test_sentences_and_moments(self):
        segments = [{'start': 0, 'end': 3, 'text': 'Hello there. How are', 'words': [[0, .5, 'Hello'], [.6, 1, 'there.'],
                                                                                 [1.5, 2, 'How'], [2.1, 3, 'are']]},
                    {'start': 3, 'end': 4, 'text': 'you?', 'words': [[3, 3.5, 'you?']]}]
        found = shorts.sentences(segments)
        self.assertEqual([s['text'] for s in found], ['Hello there.', 'How are you?'])
        self.assertEqual(shorts.moment(found, 1.8, 3.2), (1.5 - shorts.LEAD, 3.5 + shorts.TAIL))

    def test_lines_without_word_timings_are_spread_by_length(self):
        segments = [{'start': 0, 'end': 4, 'text': 'aa bb'},
                    {'start': 4, 'end': 6, 'text': 'c d', 'words': [[4, 4.5, 'c'], [5, 5.5, 'd']]}]
        words = shorts.words_between(segments, 0, 10)
        self.assertEqual([w['text'] for w in words], ['aa', 'bb', 'c', 'd'])
        self.assertEqual((words[0]['start'], words[1]['end']), (0, 4))
        self.assertEqual(words[2]['start'], 4)


@unittest.skipUnless(captions, 'needs the transcribe extra')
class Captions(unittest.TestCase):
    def test_pages_break_at_sentences_pauses_and_the_word_limit(self):
        words = [{'start': i, 'end': i + 0.5, 'text': t} for i, t in enumerate(['One', 'two.', 'Three', 'four', 'five', 'six'])]
        words[5]['start'] = 7     # a pause before "six"
        pages = captions.pages(words, captions.STYLES['bold'])     # three words at most
        self.assertEqual([[w['text'] for w in p] for p in pages], [['One', 'two.'], ['Three', 'four', 'five'], ['six']])
        self.assertEqual(len(captions.pages([{'start': 0, 'end': 1, 'text': 'Yes.'}, {'start': 1, 'end': 2, 'text': 'No'}],
                                            captions.STYLES['bold'])), 2)   # a sentence ends the page, even at one word

    def test_every_style_draws(self):
        for name in captions.STYLES:
            image = captions.draw(captions.SAMPLE.split()[:3], 1, captions.STYLES[name], captions.ACCENTS['ember'])
            self.assertEqual(image.size[0], captions.WIDTH)
            self.assertIsNotNone(image.getbbox(), name)            # something was drawn
            self.assertLess(captions.sample(name, captions.ACCENTS['ember']).size[0], captions.WIDTH)   # cropped to the words


@unittest.skipUnless(framing, 'needs the transcribe extra')
class Following(unittest.TestCase):
    def test_holds_still_for_small_moves_and_follows_big_ones(self):
        width = 0.3
        still = framing.follow([(t / 4, 0.5 + 0.02 * (t % 2)) for t in range(40)], 10, width)
        self.assertLess(max(still) - min(still), 0.01)
        walk = framing.follow([(t / 4, 0.3 if t < 20 else 0.7) for t in range(40)], 10, width)
        self.assertAlmostEqual(walk[int(4 / framing.STEP)], 0.3, places=2)
        self.assertAlmostEqual(walk[-1], 0.7, delta=0.03 * width)   # it settles near, not exactly on
        steps = [abs(b - a) for a, b in zip(walk, walk[1:])]
        self.assertLess(max(steps), framing.MAX_SPEED * width * framing.STEP + 1e-6)   # eased, never a jump

    def test_cuts_back_to_a_speaker_after_a_cutaway(self):
        path = framing.follow([(0, 0.25), (0.25, 0.25), (3, 0.8)], 4, 0.3)
        self.assertAlmostEqual(path[int(3.1 / framing.STEP)], 0.8, places=2)

    def test_nobody_in_the_picture_means_the_whole_picture(self):
        points = [(t / 4, 0.5) for t in range(8)] + [(t / 4, 0.5) for t in range(24, 40)]   # gone from 2 s to 6 s
        self.assertEqual(framing.unseen(points, 10), [(2.0, 6.0)])
        self.assertEqual(framing.unseen([], 10), [(0.0, 10.0)])

    def test_no_faces_means_the_middle(self):
        self.assertEqual(set(framing.follow([], 1, 0.3)), {0.5})

    def test_the_most_seen_face_is_followed(self):
        samples = [(t / 4, [(0.9, 0.7, 0.4, 0.05)] + ([(0.9, 0.2, 0.4, 0.03)] if t == 5 else [])) for t in range(12)]
        self.assertEqual({round(x, 2) for _, x in framing.speaker(samples)}, {0.7})


def probe(path):
    out = subprocess.run([settings.FFPROBE_BIN, '-v', 'error', '-show_entries', 'stream=codec_type,width,height,duration',
                          '-of', 'json', str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)['streams']


@unittest.skipUnless(shutil.which(settings.FFMPEG_BIN) and shorts.AVAILABLE, 'needs FFmpeg and the transcribe extra')
class Rendering(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = settings.DATA_DIR, settings.RECORDINGS_DIR, settings.RECORDINGS_FOLDER
        settings.DATA_DIR = self.tmp / 'data'
        settings.RECORDINGS_DIR = self.tmp / 'data' / 'recordings'
        settings.RECORDINGS_FOLDER = self.tmp / 'Recordings'
        settings.DATA_DIR.mkdir()
        self.db = Database(settings.DATA_DIR / 'hub.db')
        self.library = Library(self.db)
        now = time.time()
        self.db.run("INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES ('s1', 't', 'k', 'Hall', ?)", now)
        self.event_id = self.db.run("INSERT INTO events (spoke_id, title, start_at, state, started_at, ended_at, created_at) "
                                    "VALUES ('s1', 'Talk', ?, 'ended', ?, ?, ?)", now - 600, now - 600, now - 580, now)
        # 12 s of picture, with a tone that stops for 1.5 s every 4 s
        self.source = self.tmp / 'talk.mp4'
        subprocess.run([settings.FFMPEG_BIN, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=1280x720:r=30:d=12',
                        '-f', 'lavfi', '-i', "aevalsrc='lt(mod(t,4),2.5)*0.5*sin(2*PI*440*t)':s=48000:d=12",
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(self.source)], check=True)
        row = self.library.add(self.db.one('SELECT * FROM events WHERE id = ?', self.event_id), 'hub', 1, self.source)
        self.ref = f'library/{row}'
        segments = [{'start': 0.2, 'end': 2.4, 'text': 'Welcome everyone.', 'words': [[0.2, 1, 'Welcome'], [1.1, 2.4, 'everyone.']]},
                    {'start': 4.1, 'end': 6.4, 'text': 'This is our talk today'}]
        self.db.run("INSERT INTO transcripts (event_id, source, status, segments, created_at) VALUES (?, ?, 'done', ?, ?)",
                    self.event_id, self.ref, json.dumps(segments), now)

        class Transcripts:
            def transcript(_, event_id):
                row = self.db.one('SELECT * FROM transcripts WHERE event_id = ?', event_id)
                return {**row, 'segments': json.loads(row['segments'])}
        self.shorts = shorts.Shorts(self.db, None, self.library, Transcripts())

    def tearDown(self):
        settings.DATA_DIR, settings.RECORDINGS_DIR, settings.RECORDINGS_FOLDER = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_the_pauses(self):
        found = shorts.quiet(self.source, 0, 12)
        self.assertEqual(len(found), 3)
        self.assertAlmostEqual(found[0][0], 2.5, delta=0.1)
        self.assertAlmostEqual(found[0][1], 4.0, delta=0.1)

    def make(self, **spec):
        event = self.db.one('SELECT * FROM events WHERE id = ?', self.event_id)
        short_id = self.shorts.create(event, 'Welcome', {'source': self.ref, 'start': 0, 'end': 12, **spec})
        self.shorts.jobs.get()
        self.shorts._render(short_id)
        short = self.shorts.short(short_id)
        self.assertEqual(short['status'], 'done', short['error'])
        return short, self.library.resolve(short['output'])

    def test_fill_with_captions_and_pauses_out(self):
        short, path = self.make(framing='fill', style='bold', trim_pauses=True)
        self.assertEqual(path.name, 'Talk - short.mp4')
        video = next(s for s in probe(path) if s['codec_type'] == 'video')
        self.assertEqual((video['width'], video['height']), (1080, 1920))
        self.assertAlmostEqual(float(video['duration']), 12 - 3 * 1.2, delta=0.3)   # three 1.5 s pauses, 0.3 s kept of each
        self.assertAlmostEqual(short['duration'], float(video['duration']), delta=0.3)

    def test_fit_and_made_again_in_place(self):
        short, path = self.make(framing='fit', style='none')
        self.shorts.change(short['id'], 'Welcome', {**short['spec'], 'style': 'marker', 'end': 8})
        self.shorts.jobs.get()
        self.shorts._render(short['id'])
        again = self.shorts.short(short['id'])
        self.assertEqual(again['output'], short['output'])
        self.assertAlmostEqual(float(probe(path)[0]['duration']), 8, delta=0.2)
        self.assertEqual(len(self.db.all("SELECT * FROM library WHERE kind = 'short'")), 1)

    def test_limits(self):
        event = self.db.one('SELECT * FROM events WHERE id = ?', self.event_id)
        queued = self.shorts.create(event, 'x', {'source': self.ref, 'start': 0, 'end': 10})
        with self.assertRaises(ValueError):
            self.shorts.delete(queued)                                   # not while it's being made
        for spec in ({'start': 0, 'end': 2}, {'start': 0, 'end': 200}, {'start': 0, 'end': 10, 'style': 'comic'}):
            with self.assertRaises(ValueError):
                self.shorts.create(event, 'x', {'source': self.ref, **spec})


if __name__ == '__main__':
    unittest.main()
