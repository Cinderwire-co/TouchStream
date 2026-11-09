"""YouTube channels: TouchStream's shared Google sign-in (read from cinderwire.co), your own client first, each channel
keeping the client it connected with; disconnecting one gives the hub's access back to Google, and forgets it either way"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from touchstream_hub.db import Database
from touchstream_hub import youtube as yt
from touchstream_hub.youtube import OAUTH_BASE, SHARED_URL, YouTube


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


SHARED_ID = 'shared-123.apps.googleusercontent.com'
OWN_ID = 'own-456.apps.googleusercontent.com'


class SharedClient(unittest.TestCase):
    """A hub with no Google client of its own uses TouchStream's, from cinderwire.co"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        self.youtube = YouTube(self.db)
        self.secret = 'shared-secret'
        self.reads = 0
        self.posts = []
        self.reachable = True

        def handler(request):
            if str(request.url) == SHARED_URL:
                self.reads += 1
                if not self.reachable:
                    raise httpx.ConnectError('offline', request=request)
                return httpx.Response(200, json={'client_id': SHARED_ID, 'client_secret': self.secret})
            form = dict(x.split('=', 1) for x in request.content.decode().split('&'))
            self.posts.append((request.url.path, form))
            if request.url.path == '/device/code':
                return httpx.Response(200, json={'device_code': 'dc', 'user_code': 'ABCD-EFGH', 'verification_url': 'https://www.google.com/device'})
            if request.url.path == '/token':
                if form['client_id'] == SHARED_ID and form['client_secret'] != self.secret:
                    return httpx.Response(401, json={'error': 'invalid_client'})
                return httpx.Response(200, json={'access_token': f"at-{form['client_id'][:6]}", 'expires_in': 3600})
            return httpx.Response(404)
        self.youtube.http = httpx.Client(transport=httpx.MockTransport(handler))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def channel(self, client_id):
        row = self.db.run('INSERT INTO youtube_channels (channel_id, title, refresh_token, token_expires, client_id, created_at) '
                          'VALUES (?, ?, ?, 0, ?, ?)', f'UC{client_id[:4]}', 'Test', 'rt', client_id, time.time())
        return self.youtube.channel(row)

    def test_connects_with_touchstreams_client_read_once(self):
        self.assertEqual(self.youtube.which(), 'shared')
        flow = self.youtube.start_connect()
        self.assertEqual(flow['user_code'], 'ABCD-EFGH')
        self.assertEqual(self.posts[0], ('/device/code', {'client_id': SHARED_ID, 'scope': 'https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube'}))
        self.youtube.configured(); self.youtube.client()
        self.assertEqual(self.reads, 1, 'kept for a day, not read every time')

    def test_your_own_client_comes_first_and_needs_no_cinderwire(self):
        self.db.set_setting('youtube_client', {'client_id': OWN_ID, 'client_secret': 'own-secret'})
        self.assertEqual(self.youtube.which(), 'own')
        self.assertEqual(self.reads, 0)

    def test_a_channel_keeps_the_client_it_connected_with(self):
        shared_channel = self.channel(SHARED_ID)
        self.db.set_setting('youtube_client', {'client_id': OWN_ID, 'client_secret': 'own-secret'})
        own_channel = self.channel(OWN_ID)
        self.assertEqual(self.youtube._token(shared_channel), 'at-shared')
        self.assertEqual(self.youtube._token(own_channel), 'at-own-45')
        self.assertEqual([f['client_id'] for _, f in self.posts], [SHARED_ID, OWN_ID])

    def test_a_changed_shared_secret_is_read_again(self):
        self.youtube.client()                        # the hub has the old secret
        self.secret = 'rotated'
        channel = self.channel(SHARED_ID)
        self.assertEqual(self.youtube._token(channel), 'at-shared')
        self.assertEqual([f['client_secret'] for _, f in self.posts], ['shared-secret', 'rotated'])

    def test_without_cinderwire_it_tries_again_later(self):
        self.reachable = False
        self.assertFalse(self.youtube.configured())
        self.assertFalse(self.youtube.configured())
        self.assertEqual(self.reads, 1, 'not asked again straight away')
        kept = self.db.get_setting('youtube_shared_client')
        self.db.set_setting('youtube_shared_client', {**kept, 'read_at': time.time() - yt.SHARED_RETRY - 1})
        self.reachable = True
        self.assertTrue(self.youtube.configured())
        self.assertEqual(self.reads, 2)



class EditingBroadcasts(unittest.TestCase):
    """An edit reaches YouTube the way YouTube takes it: an upcoming broadcast's as a broadcast update (its times
    included); a live or finished one's on its video, keeping the video's other details"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        self.youtube = YouTube(self.db)
        self.sent = []

        def handler(request):
            body = json.loads(request.content) if request.content else None
            self.sent.append((request.method, request.url.path, dict(request.url.params), body))
            if request.method == 'GET' and request.url.path.endswith('/videos'):
                return httpx.Response(200, json={'items': [{'id': 'b1',
                    'snippet': {'title': 'Old', 'categoryId': '29', 'tags': ['church'], 'liveBroadcastContent': 'live'},
                    'status': {'privacyStatus': 'public', 'uploadStatus': 'processed', 'embeddable': False, 'license': 'youtube',
                               'madeForKids': False, 'selfDeclaredMadeForKids': False}}]})
            return httpx.Response(200, json={})
        self.youtube.http = httpx.Client(transport=httpx.MockTransport(handler))
        self.youtube.channel = lambda row_id: {'id': row_id, 'access_token': 'at', 'token_expires': time.time() + 3600, 'client_id': None}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def event(self, state, start_in):
        return {'title': 'Sunday Service (edited)', 'description': 'New words', 'privacy': 'unlisted', 'made_for_kids': 0,
                'latency': 'normal', 'state': state, 'start_at': time.time() + start_in, 'end_at': None}

    def push(self, event):
        from touchstream_hub.broadcasts import Broadcasts
        Broadcasts(self.db, self.youtube)._push_edit({'id': 1, 'access_token': 'at', 'token_expires': time.time() + 3600}, 'b1', event)

    def test_an_upcoming_broadcast_is_updated_with_its_times(self):
        self.push(self.event('scheduled', 3600))
        method, path, params, body = self.sent[-1]
        self.assertEqual((method, path, params['part']), ('PUT', '/youtube/v3/liveBroadcasts', 'snippet,status'))
        self.assertEqual(body['snippet']['title'], 'Sunday Service (edited)')
        self.assertIn('scheduledStartTime', body['snippet'])

    def test_a_live_broadcast_is_edited_on_its_video_keeping_its_details(self):
        self.push(self.event('live', -600))
        method, path, params, body = self.sent[-1]
        self.assertEqual((method, path, params['part']), ('PUT', '/youtube/v3/videos', 'snippet,status'))
        self.assertEqual(body['snippet'], {'title': 'Sunday Service (edited)', 'description': 'New words', 'categoryId': '29', 'tags': ['church']})
        self.assertEqual(body['status']['privacyStatus'], 'unlisted')
        self.assertFalse(body['status']['embeddable'], 'its other settings are kept')
        self.assertNotIn('uploadStatus', body['status'])
        self.assertNotIn('madeForKids', body['status'])

    def test_one_past_its_start_time_is_edited_on_its_video_too(self):
        self.push(self.event('scheduled', -30))
        self.assertEqual(self.sent[-1][1], '/youtube/v3/videos')
        self.push(self.event('ended', -7200))
        self.assertEqual(self.sent[-1][1], '/youtube/v3/videos')


if __name__ == '__main__':
    unittest.main()
