"""Shorts made by themselves after a broadcast: the assistant's best moments become Shorts, once, as drafts"""
import asyncio
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from touchstream_hub import app as hub_app
from touchstream_hub.assistant import AssistantError
from touchstream_hub.db import Database

SEGMENTS = [{'start': 0, 'end': 4, 'text': 'Welcome, everyone.', 'words': []},
            {'start': 4, 'end': 30, 'text': 'Waiting is how we grow.', 'words': []}]


class AutoShorts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        db = Database(self.tmp / 'hub.db')
        now = time.time()
        db.run("INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES ('sp1', 't', 'k', 'Sanctuary', ?)", now)
        self.event = db.run("INSERT INTO events (spoke_id, title, start_at, ended_at, state, created_at) "
                            "VALUES ('sp1', 'Sunday Service', ?, ?, 'ended', ?)", now - 7200, now - 3600, now)
        db.run("INSERT INTO transcripts (event_id, source, status, segments, created_at) VALUES (?, 'library/1', 'done', ?, ?)",
               self.event, json.dumps(SEGMENTS), now)
        self.made = []
        self.moments = [{'start': 4, 'end': 30, 'title': 'How we grow', 'why': ''},
                        {'start': 40, 'end': 70, 'title': 'Second', 'why': ''},
                        {'start': 80, 'end': 120, 'title': 'Third', 'why': ''},
                        {'start': 130, 'end': 160, 'title': 'Fourth', 'why': ''}]

        async def suggest(title, sentences):
            if isinstance(self.moments, Exception):
                raise self.moments
            return self.moments

        fake = SimpleNamespace(
            db=db,
            broadcasts=SimpleNamespace(event=lambda i: db.one('SELECT * FROM events WHERE id = ?', i)),
            transcriber=SimpleNamespace(transcript=lambda i: {**db.one('SELECT * FROM transcripts WHERE event_id = ?', i),
                                                              'segments': SEGMENTS}),
            assistant=SimpleNamespace(suggest_moments=suggest, config=lambda: {'model': 'test-model'}, enabled=lambda: True),
            shorts=SimpleNamespace(for_event=lambda i: [], create=lambda event, title, spec: self.made.append((title, spec))))
        self.db = db
        patch = mock.patch.object(hub_app, 'hub', fake)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_makes_the_best_three_once(self):
        asyncio.run(hub_app.shorts_for(self.event))
        self.assertEqual([t for t, _ in self.made], ['How we grow', 'Second', 'Third'])
        title, spec = self.made[0]
        self.assertEqual((spec['source'], spec['start'], spec['end']), ('library/1', 4, 30))
        self.assertEqual((spec['framing'], spec['style'], spec['trim_pauses']), ('fill', 'bold', True))
        row = self.db.one('SELECT auto_shorts_at, suggestions FROM transcripts WHERE event_id = ?', self.event)
        self.assertIsNotNone(row['auto_shorts_at'])
        self.assertEqual(len(json.loads(row['suggestions'])['moments']), 4)

    def test_how_many_comes_from_settings(self):
        self.db.set_setting('shorts_auto_count', 1)
        asyncio.run(hub_app.shorts_for(self.event))
        self.assertEqual(len(self.made), 1)

    def test_the_assistant_failing_is_tried_once_and_left(self):
        self.moments = AssistantError('out of credit')
        asyncio.run(hub_app.shorts_for(self.event))
        self.assertEqual(self.made, [])
        self.assertIsNotNone(self.db.one('SELECT auto_shorts_at FROM transcripts WHERE event_id = ?', self.event)['auto_shorts_at'])


if __name__ == '__main__':
    unittest.main()
