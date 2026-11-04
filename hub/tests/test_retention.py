"""Old recordings: what retention deletes, and what it must never touch"""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from touchstream_hub import retention, settings
from touchstream_hub.db import Database
from touchstream_hub.library import Library
from touchstream_hub.retention import Retention

DAY = 86400


class OldRecordings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = settings.DATA_DIR, settings.RECORDINGS_FOLDER, retention.GB
        settings.DATA_DIR, settings.RECORDINGS_FOLDER = self.tmp, self.tmp / 'Recordings'
        retention.GB = 1000                 # so a few small files can go over "1 GB"
        self.db = Database(self.tmp / 'hub.db')
        self.library = Library(self.db)
        self.retention = Retention(self.db, self.library)
        now = time.time()
        self.db.run("INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES ('s1', 't', 'k', 'Hall', ?)", now)
        self.db.run("INSERT INTO youtube_channels (channel_id, title, refresh_token, created_at) VALUES ('UC1', 'Ch', 'r', ?)", now)
        self.old_live = self.broadcast('Old, on YouTube', now - 100 * DAY, youtube=True, edit=True)
        self.old_record_only = self.broadcast('Old, record only', now - 90 * DAY)
        self.new_live = self.broadcast('New, on YouTube', now - DAY, youtube=True)

    def tearDown(self):
        settings.DATA_DIR, settings.RECORDINGS_FOLDER, retention.GB = self.saved
        shutil.rmtree(self.tmp)

    def broadcast(self, title, aired, youtube=False, edit=False):
        event_id = self.db.run("INSERT INTO events (spoke_id, title, start_at, started_at, ended_at, state, created_at) "
                               "VALUES ('s1', ?, ?, ?, ?, 'ended', ?)", title, aired, aired, aired + 3600, aired)
        if youtube:
            self.db.run('INSERT INTO event_targets (event_id, channel_id, broadcast_id) VALUES (?, 1, ?)', event_id, f'yt{event_id}')
        folder = self.tmp / 'Recordings' / title
        folder.mkdir(parents=True)
        self.db.run('UPDATE events SET folder = ? WHERE id = ?', str(folder), event_id)
        for kind in ('hub', 'edit') if edit else ('hub',):
            path = folder / f'{title} - {kind}.mp4'
            path.write_bytes(b'x' * 600)
            self.db.run('INSERT INTO library (event_id, kind, path, created_at) VALUES (?, ?, ?, ?)',
                        event_id, kind, str(path), aired)
        return event_id

    def left(self):
        return sorted((r['event_id'], r['kind']) for r in self.db.all('SELECT event_id, kind FROM library'))

    def test_after_n_days_keeps_edits_and_record_only_broadcasts(self):
        self.db.set_setting('retention', {'days': 30})
        self.assertEqual(self.retention.apply(), 1)
        self.assertEqual(self.left(), [(self.old_live, 'edit'), (self.old_record_only, 'hub'), (self.new_live, 'hub')])
        self.assertIsNotNone(self.db.one('SELECT recordings_removed_at FROM events WHERE id = ?', self.old_live)['recordings_removed_at'])

    def test_record_only_goes_too_when_allowed(self):
        self.db.set_setting('retention', {'days': 30, 'unpublished': True})
        self.retention.apply()
        self.assertEqual(self.left(), [(self.old_live, 'edit'), (self.new_live, 'hub')])

    def test_size_limit_deletes_oldest_first_and_spares_the_last_few_days(self):
        # 2400 bytes in all, limit 1 "GB" (1000 bytes here): both old recordings go, the new one stays
        plan = self.retention.plan({'days': None, 'max_gb': 1, 'unpublished': True})
        self.assertEqual([f['event_id'] for f in plan['delete']], [self.old_live, self.old_record_only])
        self.assertTrue(plan['over'])   # still over: the edit and the newest recording are never deleted for size

    def test_nothing_happens_without_rules(self):
        self.assertEqual(self.retention.apply(), 0)
        self.assertEqual(len(self.left()), 4)


if __name__ == '__main__':
    unittest.main()
