"""YouTube channels: disconnecting one gives the hub's access back to Google, and forgets it either way"""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from touchstream_hub.db import Database
from touchstream_hub.youtube import OAUTH_BASE, YouTube


class Disconnect(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        self.youtube = YouTube(self.db)
        self.row = self.db.run('INSERT INTO youtube_channels (channel_id, title, thumbnail, refresh_token, access_token, '
                               'token_expires, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                               'UCfake', 'Test Channel', '', 'rt-test', 'at-test', time.time() + 3600, time.time())
        self.requests = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def answer(self, handler):
        self.youtube.http = httpx.Client(transport=httpx.MockTransport(handler))

    def test_revokes_the_refresh_token_at_google(self):
        def handler(request):
            self.requests.append(request)
            return httpx.Response(200, json={})
        self.answer(handler)
        self.youtube.disconnect(self.row)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(str(self.requests[0].url), f'{OAUTH_BASE}/revoke')
        self.assertEqual(self.requests[0].content, b'token=rt-test')
        self.assertIsNone(self.youtube.channel(self.row))

    def test_forgets_the_channel_when_google_cant_be_reached(self):
        def handler(request):
            raise httpx.ConnectError('offline', request=request)
        self.answer(handler)
        self.youtube.disconnect(self.row)
        self.assertIsNone(self.youtube.channel(self.row))


if __name__ == '__main__':
    unittest.main()
