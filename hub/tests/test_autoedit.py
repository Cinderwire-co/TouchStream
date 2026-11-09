"""Edits made by themselves: a profile's plan (with and without the assistant), what it learns from people's edits,
and that it never touches an edit someone made"""
import asyncio
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from touchstream_hub import autoedit
from touchstream_hub.assistant import Assistant, AssistantError
from touchstream_hub.autoedit import AutoEdits, build_spec, kept_ranges, merge, summary
from touchstream_hub.db import Database

# A service: welcome, a song, the message, a closing song
WORDS = [(1.0, 'Good morning, church.'), (5.0, 'Let us stand and sing.'), (8.0, 'Amazing grace, how sweet the sound.'),
         (60.0, 'Turn with me to John chapter three.'), (64.0, 'For God so loved the world.'),
         (400.0, 'Let us pray.'), (404.0, 'Amen.'), (420.0, 'Be thou my vision, O Lord of my heart.')]
SEGMENTS = [{'start': t, 'end': t + 3, 'text': text, 'words': [[t + i * 0.5, t + i * 0.5 + 0.4, w] for i, w in enumerate(text.split())]}
            for t, text in WORDS]


class Pieces(unittest.TestCase):
    def test_kept_ranges_from_the_video_track_or_the_simple_form(self):
        spec = {'video': [{'start': 0, 'end': 5, 'media': 1}, {'start': 60, 'end': 200}, {'start': 200.2, 'end': 406}]}
        self.assertEqual(kept_ranges(spec, 500), [(60, 406)])
        self.assertEqual(kept_ranges({'in': 10, 'out': 100, 'cuts': [[20, 30]]}, 500), [(10, 20), (30, 100)])
        self.assertEqual(kept_ranges({'in': 10}, 50), [(10, 50)])

    def test_summary_says_what_was_kept_and_cut_in_their_words(self):
        said = [{'start': t, 'end': t + 3, 'text': text} for t, text in WORDS]
        text = summary(said, [(59.5, 407.0)], 430)
        self.assertIn('Cut 0:00:00 to 0:00:59 (it began: "Good morning, church.")', text)
        self.assertIn('Kept 0:00:59 to 0:06:47, from "Turn with me to John chapter three." to "Amen."', text)
        self.assertIn('Cut 0:06:47 to 0:07:10 (it began: "Be thou my vision', text)

    def test_spec_has_the_brand_at_the_ends_and_dissolves_only_between_parts(self):
        media = {1: {'id': 1, 'kind': 'image', 'duration': None, 'has_audio': 0}, 2: {'id': 2, 'kind': 'video', 'duration': 7.0, 'has_audio': 1}}
        brand = {'id': 9, 'intro': 1, 'outro': 2, 'intro_seconds': 5, 'outro_seconds': 5}
        spec = build_spec([(60, 120), (121, 200), (300, 400)], brand, media)
        video = spec['video']
        self.assertEqual([c.get('media') for c in video], [1, None, None, None, 2])
        self.assertEqual([bool(c['transition']) for c in video], [False, True, False, True, True])   # a pause cut is plain
        self.assertEqual(video[-1]['at'], 5 + 60 + 79 + 100)
        self.assertEqual([c.get('media') for c in spec['audio']], [None, None, None, 2])   # the image intro has no sound
        self.assertEqual(spec['brand'], 9)
        self.assertEqual(merge([(1, 2), (2.2, 3), (10, 11)]), [(1, 3), (10, 11)])


class Profiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        now = time.time()
        self.db.run("INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES ('sp1', 't', 'k', 'Sanctuary', ?)", now)
        self.event = self.db.run("INSERT INTO events (spoke_id, title, start_at, ended_at, state, created_at) "
                                 "VALUES ('sp1', 'Sunday Service', ?, ?, 'ended', ?)", now - 7200, now - 3600, now)
        self.db.run("INSERT INTO transcripts (event_id, source, status, segments, created_at) VALUES (?, 'library/1', 'done', ?, ?)",
                    self.event, json.dumps(SEGMENTS), now)
        self.created = []

        def create(event, source, spec, auto=None):
            self.created.append((source, spec, auto))
            return self.db.run("INSERT INTO edits (event_id, source, spec, status, auto, created_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                               event['id'], source, json.dumps(spec), json.dumps(auto) if auto else None, time.time())

        def current(event_id):
            row = self.db.one('SELECT * FROM edits WHERE event_id = ? ORDER BY id DESC LIMIT 1', event_id)
            return row and {**row, 'auto': json.loads(row['auto']) if row['auto'] else None}

        self.plans = []

        async def plan_edit(title, said, profile, examples=()):
            self.plans.append((profile, list(examples)))
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

        self.reply = ([(3, 6)], 'Kept the message, from the scripture reading to the amen')
        self.ai = True
        editor = SimpleNamespace(library=SimpleNamespace(resolve=lambda ref: self.tmp / 'service.mp4'), create=create, current=current)
        def transcript(event_id):
            row = self.db.one('SELECT * FROM transcripts WHERE event_id = ?', event_id)
            return row and {**row, 'segments': SEGMENTS}
        transcriber = SimpleNamespace(transcript=transcript)
        assistant = SimpleNamespace(enabled=lambda: self.ai, plan_edit=plan_edit)
        media = SimpleNamespace(all=lambda: [], brand=lambda i: None)
        self.auto = AutoEdits(self.db, editor, media, transcriber, assistant)
        patch = mock.patch.object(autoedit, 'probe_duration', lambda path: 430.0)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def profile(self, **f):
        p = self.auto.save(None, {'name': 'Sunday sermon', **f})
        self.db.run('UPDATE events SET edit_profile_id = ? WHERE id = ?', p['id'], self.event)
        return p

    def event_row(self):
        return self.db.one('SELECT * FROM events WHERE id = ?', self.event)

    def test_profiles_check_what_they_are_given(self):
        p = self.auto.save(None, {'name': 'Sermon', 'keep': 'message', 'cut_music': True})
        self.assertEqual((p['keep'], p['cut_music'], p['trim_pauses'], p['after'], p['learn']), ('message', True, False, 'review', True))
        for bad in ({'name': ''}, {'name': 'x', 'keep': 'songs'}, {'name': 'x', 'after': 'later'}):
            with self.assertRaises(ValueError):
                self.auto.save(None, bad)

    def test_just_the_message_the_assistants_way(self):
        p = self.profile(keep='message', cut_music=True)
        source, ranges, why = asyncio.run(self.auto.plan(self.event_row(), p))
        self.assertEqual(source, 'library/1')
        self.assertEqual(ranges, [(59.6, 405.2)])      # sentence 3's start to sentence 6's end (404.4), with a breath either side
        self.assertIn('scripture reading', why)

    def test_without_the_assistant_first_words_to_last_and_it_says_so(self):
        self.ai = False
        p = self.profile(keep='message')
        _, ranges, why = asyncio.run(self.auto.plan(self.event_row(), p))
        self.assertEqual(ranges, [(0.0, 426.4)])       # (1 s before the first words, 2 s after the last)
        self.assertIn('needs the assistant', why)
        self.ai, self.reply = True, AssistantError('OpenRouter is down')
        _, ranges, why = asyncio.run(self.auto.plan(self.event_row(), p))
        self.assertEqual(ranges, [(0.0, 426.4)])
        self.assertIn('OpenRouter is down', why)

    def test_long_pauses_come_out_only_when_asked(self):
        p = self.profile(keep='service', trim_pauses=True)
        with mock.patch.object(autoedit, 'quiet', lambda path, a, b: [(100.0, 100.8), (200.0, 204.0)]):
            _, ranges, why = asyncio.run(self.auto.plan(self.event_row(), p))
        self.assertEqual(ranges, [(0.0, 200.35), (203.65, 426.4)])   # the 0.8 s pause stays: it's the speaker's
        self.assertIn('long pauses', why)

    def test_makes_it_once_waiting_or_publishing_and_never_over_a_persons_edit(self):
        self.db.run("INSERT INTO youtube_channels (channel_id, title, refresh_token, created_at) VALUES ('UC1', 'Main', 'rt', ?)", time.time())
        self.db.run('INSERT INTO event_targets (event_id, channel_id) VALUES (?, 1)', self.event)
        self.profile(keep='message', after='publish', privacy='unlisted', hide_live='private')
        edit_id = asyncio.run(self.auto.make(self.event))
        self.assertTrue(edit_id)
        source, spec, auto = self.created[0]
        self.assertEqual(auto['publish'], {'channel_ids': [1], 'title': 'Sunday Service', 'description': '',
                                           'privacy': 'unlisted', 'hide_original': 'private', 'hide_previous': 'private'})
        self.assertEqual(kept_ranges(spec, 430), [(59.6, 405.2)])
        self.assertIsNone(asyncio.run(self.auto.make(self.event)))           # once
        self.assertEqual(len(self.created), 1)
        asyncio.run(self.auto.make(self.event, again=True))                   # asked for: over its own edit, yes
        self.assertEqual(len(self.created), 2)
        self.db.run('UPDATE edits SET auto = NULL')                            # now it's someone's
        self.assertIsNone(asyncio.run(self.auto.make(self.event, again=True)))
        self.assertEqual(len(self.created), 2)

    def test_learns_from_peoples_edits_and_reads_them_next_time(self):
        p = self.profile(keep='message')
        spec = {'video': [{'start': 0, 'end': 5, 'media': 1}, {'start': 59.5, 'end': 407.0}]}
        self.auto.learn(self.event_row(), 'library/1', spec)
        self.auto.learn(self.event_row(), 'library/1', spec)                 # the same broadcast again: replaced
        examples = self.auto.examples(p['id'])
        self.assertEqual(len(examples), 1)
        self.assertIn('from "Turn with me to John chapter three."', examples[0]['summary'])
        self.assertEqual(self.auto.profile(p['id'])['examples'], 1)
        asyncio.run(self.auto.plan(self.event_row(), p))
        self.assertEqual(self.plans[-1][1][0]['summary'], examples[0]['summary'])
        for n in range(autoedit.EXAMPLES + 2):                                 # only the newest are kept
            e = self.db.run("INSERT INTO events (spoke_id, title, start_at, state, edit_profile_id, created_at) "
                            "VALUES ('sp1', ?, 0, 'ended', ?, ?)", f'Week {n}', p['id'], time.time())
            self.auto.learn(self.db.one('SELECT * FROM events WHERE id = ?', e), 'library/1', spec)
        self.assertEqual(len(self.auto.examples(p['id'])), autoedit.EXAMPLES)
        self.auto.save(p['id'], {'learn': False})
        self.auto.learn(self.event_row(), 'library/1', {'video': [{'start': 0, 'end': 50}]})
        self.assertNotIn('Sunday Service', [x['title'] for x in self.auto.examples(p['id'])])


class Planning(unittest.TestCase):
    def test_the_assistant_gets_their_wishes_and_examples_and_its_reply_is_checked(self):
        assistant = Assistant(SimpleNamespace(get_setting=lambda key, default=None: {'api_key': 'k', 'model': 'm'}
                                              if key == 'assistant' else default))
        assistant.config = lambda: {'api_key': 'k', 'model': 'm'}
        sent = []

        async def complete(config, messages, tools=None):
            sent.append(messages)
            return {'content': 'Here: {"keep": [{"from": 6, "to": 3}, {"from": 50, "to": 60}], "why": "The message"}'}
        assistant._complete = complete
        said = [{'start': t, 'end': t + 3, 'text': text} for t, text in WORDS]
        profile = {'keep': 'message', 'cut_music': True, 'instructions': 'Cut the offering.'}
        picked, why = asyncio.run(assistant.plan_edit('Sunday', said, profile, [{'title': 'Last week', 'summary': 'Kept 0:21:00 to 1:02:00'}]))
        self.assertEqual(picked, [(3, 6)])            # put in order; one past the end dropped
        self.assertEqual(why, 'The message')
        system = sent[0][0]['content']
        for wanted in ('Just the message', 'Take out the songs', 'Cut the offering.', 'Example 1 (Last week)', 'Kept 0:21:00 to 1:02:00'):
            self.assertIn(wanted, system)
        self.assertIn('[3] (0:01:00) Turn with me', sent[0][1]['content'])


if __name__ == '__main__':
    unittest.main()
