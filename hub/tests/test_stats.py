"""Views: which videos get read and when, the numbers kept, and a broadcast's totals"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from touchstream_hub.db import Database
from touchstream_hub.stats import Stats, due
from touchstream_hub.youtube import YouTubeError


class FakeYouTube:
    def __init__(self):
        self.asked = []
        self.answers = {}   # video id -> item
        self.fail = False

    def channel(self, row_id):
        return {'id': row_id, 'title': 'Test Channel'}

    def video_stats(self, channel, ids):
        if self.fail:
            raise YouTubeError('quota')
        self.asked.append(list(ids))
        return [self.answers[i] for i in ids if i in self.answers]


def item(vid, views, likes=0, comments=0, watching=None):
    out = {'id': vid, 'statistics': {'viewCount': str(views), 'likeCount': str(likes), 'commentCount': str(comments)}}
    if watching is not None:
        out['liveStreamingDetails'] = {'concurrentViewers': str(watching)}
    return out


class Views(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        self.yt = FakeYouTube()
        self.stats = Stats(self.db, self.yt)
        now = time.time()
        self.channel = self.db.run("INSERT INTO youtube_channels (channel_id, title, refresh_token, created_at) "
                                   "VALUES ('UC1', 'Test Channel', 'rt', ?)", now)
        self.db.run("INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES ('sp1', 't', 'k', 'Sanctuary', ?)", now)
        self.event = self.db.run("INSERT INTO events (spoke_id, title, start_at, state, created_at) "
                                 "VALUES ('sp1', 'Sunday', ?, 'live', ?)", now - 600, now)
        self.db.run('INSERT INTO event_targets (event_id, channel_id, broadcast_id) VALUES (?, ?, ?)',
                    self.event, self.channel, 'live1')
        self.db.run("INSERT INTO edits (event_id, source, spec, status, published, created_at) VALUES (?, 'x', '{}', 'done', ?, ?)",
                    self.event, json.dumps([{'channel_id': self.channel, 'video_id': 'edit1', 'at': now}]), now)
        self.db.run("INSERT INTO shorts (event_id, title, spec, status, published, created_at) VALUES (?, 'a', '{}', 'done', ?, ?)",
                    self.event, json.dumps([{'channel_id': self.channel, 'video_id': 'short1', 'at': now}]), now)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_reads_every_video_the_hub_made_in_one_call(self):
        self.yt.answers = {'live1': item('live1', 120, 9, 2, watching=31), 'edit1': item('edit1', 50, 4),
                           'short1': item('short1', 900, 60, 5)}
        self.assertEqual(self.stats.refresh(), 3)
        self.assertEqual(len(self.yt.asked), 1)
        self.assertEqual(sorted(self.yt.asked[0]), ['edit1', 'live1', 'short1'])
        totals = self.stats.for_event(self.event)
        self.assertEqual((totals['views'], totals['live_views'], totals['edit_views'], totals['shorts_views']), (1070, 120, 50, 900))
        self.assertEqual((totals['likes'], totals['comments'], totals['watching'], totals['peak_viewers']), (73, 7, 31, 31))

    def test_keeps_the_peak_while_live(self):
        self.yt.answers = {'live1': item('live1', 10, watching=40)}
        self.stats.refresh(force=True)
        self.yt.answers = {'live1': item('live1', 15, watching=25)}
        self.stats.refresh(force=True)
        v = self.stats.for_video('live1')
        self.assertEqual((v['watching'], v['peak_viewers'], v['views']), (25, 40, 15))
        self.assertEqual(self.stats.watching_on('sp1'), 25)
        self.assertIsNone(self.stats.watching_on('nobody'))

    def test_once_read_a_video_waits_its_turn(self):
        self.yt.answers = {'live1': item('live1', 1), 'edit1': item('edit1', 1), 'short1': item('short1', 1)}
        self.stats.refresh()
        self.db.run("UPDATE events SET state = 'ended'")
        self.assertEqual(self.stats.refresh(), 0, 'nothing is due a minute later')
        now = time.time()
        self.assertTrue(due(None, False, now))
        self.assertTrue(due({'updated_at': now - 60, 'made_at': now}, True, now))
        self.assertFalse(due({'updated_at': now - 60, 'made_at': now}, False, now))
        self.assertTrue(due({'updated_at': now - 16 * 60, 'made_at': now - 86400}, False, now))
        self.assertFalse(due({'updated_at': now - 16 * 60, 'made_at': now - 30 * 86400}, False, now))
        self.assertTrue(due({'updated_at': now - 25 * 3600, 'made_at': now - 400 * 86400}, False, now))

    def test_youtube_saying_no_leaves_the_numbers_as_they_were(self):
        self.yt.answers = {'live1': item('live1', 7)}
        self.stats.refresh()
        self.yt.fail = True
        self.stats.refresh(force=True)
        self.assertEqual(self.stats.for_video('live1')['views'], 7)

    def test_summary_counts_the_last_month(self):
        self.yt.answers = {'live1': item('live1', 5), 'edit1': item('edit1', 6), 'short1': item('short1', 7)}
        self.stats.refresh()
        self.assertEqual(self.stats.summary(), {'days': 30, 'views': 18, 'videos': 3})


if __name__ == '__main__':
    unittest.main()
