"""The recordings folder: names, and filing a finished broadcast's copies (needs FFmpeg)"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from touchstream_hub import settings
from touchstream_hub.db import Database
from touchstream_hub.editor import keep_ranges, render_command
from touchstream_hub.library import Library, file_name, safe_name


class Names(unittest.TestCase):
    def test_titles_become_safe_file_names(self):
        self.assertEqual(safe_name('Sunday Service: Week 3'), 'Sunday Service - Week 3')
        self.assertEqual(safe_name('Main Hall live · Nov 3, 4:39 PM'), 'Main Hall live · Nov 3, 4.39 PM')
        self.assertEqual(safe_name('Q&A / Live?'), 'Q&A - Live')
        self.assertEqual(safe_name('  Trailing dots...  '), 'Trailing dots')
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertEqual(safe_name('<>'), 'Broadcast')

    def test_numbered_files(self):
        self.assertEqual(file_name('Talk', 'hub'), 'Talk - hub recording.mp4')
        self.assertEqual(file_name('Talk', 'edit', 2), 'Talk - edited 2.mp4')


def make_clip(path, seconds=2):
    subprocess.run([settings.FFMPEG_BIN, '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=s=320x180:d={seconds}',
                    '-f', 'lavfi', '-i', f'sine=d={seconds}', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(path)],
                   check=True)


def probe(path):
    out = subprocess.run([settings.FFPROBE_BIN, '-v', 'error', '-show_entries',
                          'format_tags=title,date,comment:stream=codec_type:stream_disposition=attached_pic',
                          '-of', 'json', str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which(settings.FFMPEG_BIN), 'needs FFmpeg')
class Filing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = settings.DATA_DIR, settings.RECORDINGS_DIR, settings.RECORDINGS_FOLDER
        settings.DATA_DIR = self.tmp / 'data'
        settings.RECORDINGS_DIR = self.tmp / 'data' / 'recordings'
        settings.RECORDINGS_FOLDER = self.tmp / 'My Recordings'
        settings.DATA_DIR.mkdir()
        self.db = Database(settings.DATA_DIR / 'hub.db')
        self.library = Library(self.db)

        now = time.time()
        self.start, self.end = now - 1200, now - 600
        self.db.run("INSERT INTO spokes (id, token, stream_key, name, location, created_at) "
                    "VALUES ('s1', 't', 'k', 'Main Hall', 'Building A', ?)", now)
        (settings.DATA_DIR / 'thumbnails').mkdir()
        subprocess.run([settings.FFMPEG_BIN, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=320x180', '-frames:v', '1',
                        str(settings.DATA_DIR / 'thumbnails' / 'thumb.jpg')], check=True)
        self.event_id = self.db.run(
            "INSERT INTO events (spoke_id, title, description, start_at, end_at, started_at, ended_at, state, "
            "thumbnail, created_at) VALUES ('s1', 'Sunday Service: Week 3', 'Morning worship', ?, ?, ?, ?, 'ended', "
            "'thumb.jpg', ?)", self.start, self.end, self.start, self.end, now)

        # What the hub recorded (as MediaMTX names it) and what the spoke uploaded
        hub_dir = settings.RECORDINGS_DIR / 'spoke' / 's1' / 'hub'
        hub_dir.mkdir(parents=True)
        self.hub_copy = hub_dir / time.strftime('%Y-%m-%d_%H-%M-%S-000000.mp4', time.localtime(self.start + 5))
        make_clip(self.hub_copy)
        os.utime(self.hub_copy, (self.end, self.end))
        spoke_dir = settings.RECORDINGS_DIR / 'spoke' / 's1' / 'spoke'
        spoke_dir.mkdir()
        self.spoke_copy = spoke_dir / '20260101-100000.mp4'
        make_clip(self.spoke_copy)
        (spoke_dir / '20260101-100000.json').write_text(json.dumps({'started_at': self.start, 'ended_at': self.end}))

    def tearDown(self):
        settings.DATA_DIR, settings.RECORDINGS_DIR, settings.RECORDINGS_FOLDER = self.saved
        shutil.rmtree(self.tmp)

    def folder(self):
        return Path(self.db.one('SELECT folder FROM events WHERE id = ?', self.event_id)['folder'])

    def test_files_copies_into_a_named_folder_with_details(self):
        self.library.file_ready()
        folder = self.folder()
        date = time.strftime('%Y-%m-%d', time.localtime(self.start))
        self.assertEqual(folder, settings.RECORDINGS_FOLDER / f'{date} Sunday Service - Week 3')
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(names, ['Sunday Service - Week 3 - hub recording.mp4', 'Sunday Service - Week 3 - info.txt',
                                 'Sunday Service - Week 3 - spoke recording.mp4',
                                 'Sunday Service - Week 3 - thumbnail.jpg'])
        # Working copies are gone, and the filed ones are what the dashboard sees
        self.assertFalse(self.hub_copy.exists() or self.spoke_copy.exists())
        event = self.db.one('SELECT * FROM events WHERE id = ?', self.event_id)
        copies = self.library.event_copies(event)
        self.assertEqual(sorted(c['source'] for c in copies), ['hub', 'spoke'])
        self.assertTrue(all(self.library.resolve(c['path']) for c in copies))

        info = probe(folder / 'Sunday Service - Week 3 - hub recording.mp4')
        self.assertEqual(info['format']['tags']['title'], 'Sunday Service: Week 3')
        self.assertEqual(info['format']['tags']['date'], date)
        self.assertIn('Morning worship', info['format']['tags']['comment'])
        self.assertEqual(sum(s['disposition']['attached_pic'] for s in info['streams']), 1)   # the thumbnail
        about = (folder / 'Sunday Service - Week 3 - info.txt').read_text()
        self.assertIn('Main Hall (Building A)', about)
        self.assertIn('spoke recording.mp4', about)

    def test_rename_follows_the_title(self):
        self.library.file_ready()
        self.db.run("UPDATE events SET title = 'Easter Sunday' WHERE id = ?", self.event_id)
        self.library.sync_names(self.event_id)
        folder = self.folder()
        self.assertTrue(folder.name.endswith(' Easter Sunday'))
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ['Easter Sunday - hub recording.mp4', 'Easter Sunday - info.txt',
                          'Easter Sunday - spoke recording.mp4', 'Easter Sunday - thumbnail.jpg'])
        event = self.db.one('SELECT * FROM events WHERE id = ?', self.event_id)
        self.assertEqual(len([c for c in self.library.event_copies(event) if c['path']]), 2)

    def test_not_filed_while_in_use_or_too_soon(self):
        self.db.run("INSERT INTO transcripts (event_id, source, status, created_at) VALUES (?, ?, 'running', 0)",
                    self.event_id, self.hub_copy.relative_to(settings.RECORDINGS_DIR).as_posix())
        self.library.file_ready()
        self.assertTrue(self.hub_copy.exists())          # being transcribed: left alone
        self.assertFalse(self.spoke_copy.exists())       # filed
        self.db.run('UPDATE events SET ended_at = ? WHERE id = ?', time.time(), self.event_id)
        self.db.run("UPDATE transcripts SET status = 'done'")
        self.library.file_ready()
        self.assertTrue(self.hub_copy.exists())          # only just ended

    def test_edit_renders_with_details_and_cover(self):
        event = self.db.one('SELECT * FROM events WHERE id = ?', self.event_id)
        number, output = self.library.edit_target(event)
        spec = {'in': 0.2, 'out': 1.8, 'cuts': [[0.8, 1.0]], 'fade_in': 0.2}
        ranges = keep_ranges(spec, 2)
        cmd, _ = render_command(self.spoke_copy, spec, ranges, output, *self.library.embed_args(event, len(ranges)))
        subprocess.run(cmd, check=True, capture_output=True)
        self.assertEqual(output.name, 'Sunday Service - Week 3 - edited.mp4')
        info = probe(output)
        self.assertEqual(info['format']['tags']['title'], 'Sunday Service: Week 3')
        self.assertEqual([s['codec_type'] for s in info['streams']], ['video', 'audio', 'video'])
        self.assertEqual(info['streams'][2]['disposition']['attached_pic'], 1)

    def test_delete_removes_the_folder(self):
        self.library.file_ready()
        folder = self.folder()
        self.library.delete_event_files(self.db.one('SELECT * FROM events WHERE id = ?', self.event_id))
        self.assertFalse(folder.exists())


if __name__ == '__main__':
    unittest.main()
