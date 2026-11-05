"""Recordings on the spoke: sharing one to a phone and trimming a clip out of it (needs FFmpeg)"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from touchstream import recorder   # noqa: E402


@unittest.skipUnless(shutil.which('ffmpeg'), 'needs FFmpeg')
class Clips(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = recorder.RECORDINGS_DIR, recorder.SHARE_DIR
        recorder.RECORDINGS_DIR, recorder.SHARE_DIR = self.tmp / 'recordings', self.tmp / 'downloads'
        for state in (recorder._shares, recorder._prepared, recorder._clips):   # links from another test
            state.clear()
        # A finished 20 s recording in two parts, started 20 s ago, with a keyframe every 2 s
        self.name = time.strftime('%Y%m%d-%H%M%S', time.localtime(time.time() - 20))
        session = recorder.RECORDINGS_DIR / self.name
        session.mkdir(parents=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=30:d=20', '-f', 'lavfi',
                        '-i', 'sine=d=20', '-c:v', 'libx264', '-g', '60', '-c:a', 'aac', '-f', 'segment', '-segment_time', '10',
                        '-segment_format', 'mpegts', str(session / 'part-%05d.ts')], check=True)
        (session / '.complete').touch()

    def tearDown(self):
        recorder.RECORDINGS_DIR, recorder.SHARE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait(self, check):
        for _ in range(100):
            if check():
                return
            time.sleep(0.1)
        self.fail('timed out')

    def test_share_then_clip(self):
        token = recorder.share(self.name)
        self.wait(lambda: recorder.share_state(self.name)[0] == 'ready')
        self.assertEqual(recorder.shared(token)['name'], self.name)
        clip_id = recorder.clip(token, 5.5, 12)
        self.wait(lambda: recorder.clip_state(token, clip_id)['state'] == 'ready')
        _, path = recorder.clip_file(token, clip_id)
        length = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                       str(path)], capture_output=True, text=True).stdout)
        self.assertAlmostEqual(length, 8.0, delta=0.6)    # from the keyframe at 4 s, to 12 s

    def test_expired_links_and_short_clips_are_refused(self):
        token = recorder.share(self.name)
        self.wait(lambda: recorder.share_state(self.name)[0] == 'ready')
        with self.assertRaises(ValueError):
            recorder.clip(token, 3, 3.5)
        recorder._shares[token] = (self.name, time.time() - 1)
        self.assertIsNone(recorder.shared(token))
        with self.assertRaises(ValueError):
            recorder.clip(token, 0, 5)


if __name__ == '__main__':
    unittest.main()
