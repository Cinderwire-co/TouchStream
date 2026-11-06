"""YouTube: connect channels, create stream keys, schedule broadcasts, set thumbnails

Channels are connected with Google's device sign-in ("TVs and Limited Input
devices" OAuth client): the hub shows a code, you enter it at google.com/device.
That needs no redirect URL, so it works the same for a hub on Coolify, on a LAN
IP or on localhost. Connect as many channels (Google accounts or Brand Accounts)
as you like; each keeps its own refresh token.

TOUCHSTREAM_GOOGLE_CLIENT_ID / TOUCHSTREAM_GOOGLE_CLIENT_SECRET can supply the
OAuth client instead of the Settings page. TOUCHSTREAM_GOOGLE_OAUTH_BASE and
TOUCHSTREAM_YOUTUBE_API_BASE exist so tests can point at a fake server.
"""
import os
import secrets
import time
from datetime import datetime, timezone

import httpx

OAUTH_BASE = os.environ.get('TOUCHSTREAM_GOOGLE_OAUTH_BASE', 'https://oauth2.googleapis.com')
API_BASE = os.environ.get('TOUCHSTREAM_YOUTUBE_API_BASE', 'https://www.googleapis.com')
SCOPE = 'https://www.googleapis.com/auth/youtube'
DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'


class YouTubeError(Exception):
    pass


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class YouTube:
    def __init__(self, db):
        self.db = db
        self.flows = {}   # flow id -> device-flow state while someone is signing in
        self.http = httpx.Client(timeout=30)

    # ---- OAuth client (from Settings, or the environment) ----
    def client(self):
        stored = self.db.get_setting('youtube_client') or {}
        return (os.environ.get('TOUCHSTREAM_GOOGLE_CLIENT_ID') or stored.get('client_id', ''),
                os.environ.get('TOUCHSTREAM_GOOGLE_CLIENT_SECRET') or stored.get('client_secret', ''))

    def configured(self):
        return all(self.client())

    def client_from_env(self):
        return bool(os.environ.get('TOUCHSTREAM_GOOGLE_CLIENT_ID'))

    # ---- Connecting a channel (device flow) ----
    def start_connect(self):
        client_id, _ = self.client()
        resp = self.http.post(f'{OAUTH_BASE}/device/code', data={'client_id': client_id, 'scope': SCOPE})
        data = self._json(resp)
        flow_id = secrets.token_urlsafe(8)
        self.flows[flow_id] = {'device_code': data['device_code'], 'interval': data.get('interval', 5),
                               'expires_at': time.time() + data.get('expires_in', 1800), 'next_poll': 0}
        return {'id': flow_id, 'user_code': data['user_code'],
                'verification_url': data.get('verification_url') or data.get('verification_uri'),
                'expires_at': self.flows[flow_id]['expires_at']}

    def poll_connect(self, flow_id):
        flow = self.flows.get(flow_id)
        if not flow or time.time() > flow['expires_at']:
            self.flows.pop(flow_id, None)
            return {'status': 'expired'}
        if time.time() < flow['next_poll']:
            return {'status': 'pending'}
        client_id, client_secret = self.client()
        resp = self.http.post(f'{OAUTH_BASE}/token', data={
            'client_id': client_id, 'client_secret': client_secret,
            'device_code': flow['device_code'], 'grant_type': DEVICE_GRANT})
        data = resp.json()
        error = data.get('error')
        if error in ('authorization_pending', 'slow_down'):
            flow['interval'] += 5 if error == 'slow_down' else 0
            flow['next_poll'] = time.time() + flow['interval']
            return {'status': 'pending'}
        self.flows.pop(flow_id, None)
        if error:
            return {'status': 'denied' if error == 'access_denied' else 'error',
                    'error': data.get('error_description', error)}
        return {'status': 'connected', 'channel': self._save_channel(data)}

    def _save_channel(self, tokens):
        access = tokens['access_token']
        resp = self.http.get(f'{API_BASE}/youtube/v3/channels', params={'part': 'snippet', 'mine': 'true'},
                             headers={'Authorization': f'Bearer {access}'})
        items = self._json(resp).get('items', [])
        if not items:
            raise YouTubeError("That Google account doesn't have a YouTube channel yet")
        snippet = items[0]['snippet']
        thumb = snippet.get('thumbnails', {}).get('default', {}).get('url', '')
        existing = self.db.one('SELECT id, refresh_token FROM youtube_channels WHERE channel_id = ?', items[0]['id'])
        # Google only returns a refresh token on the first consent; keep the old one otherwise
        refresh = tokens.get('refresh_token') or (existing or {}).get('refresh_token', '')
        expires = time.time() + tokens.get('expires_in', 3600)
        if existing:
            self.db.run('UPDATE youtube_channels SET title = ?, thumbnail = ?, refresh_token = ?, access_token = ?, '
                        'token_expires = ?, error = NULL WHERE id = ?',
                        snippet['title'], thumb, refresh, access, expires, existing['id'])
            row_id = existing['id']
        else:
            row_id = self.db.run('INSERT INTO youtube_channels (channel_id, title, thumbnail, refresh_token, '
                                 'access_token, token_expires, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                                 items[0]['id'], snippet['title'], thumb, refresh, access, expires, time.time())
        return {'id': row_id, 'title': snippet['title'], 'thumbnail': thumb}

    # ---- Authenticated API calls ----
    def _token(self, channel):
        if channel['access_token'] and channel['token_expires'] > time.time() + 60:
            return channel['access_token']
        client_id, client_secret = self.client()
        resp = self.http.post(f'{OAUTH_BASE}/token', data={
            'client_id': client_id, 'client_secret': client_secret,
            'refresh_token': channel['refresh_token'], 'grant_type': 'refresh_token'})
        data = resp.json()
        if 'access_token' not in data:
            message = 'Sign-in expired: reconnect this channel'
            self.db.run('UPDATE youtube_channels SET error = ? WHERE id = ?', message, channel['id'])
            raise YouTubeError(message)
        channel['access_token'] = data['access_token']
        channel['token_expires'] = time.time() + data.get('expires_in', 3600)
        self.db.run('UPDATE youtube_channels SET access_token = ?, token_expires = ?, error = NULL WHERE id = ?',
                    channel['access_token'], channel['token_expires'], channel['id'])
        return channel['access_token']

    def _call(self, channel, method, path, upload=False, **kwargs):
        base = f"{API_BASE}/{'upload/' if upload else ''}youtube/v3"
        headers = {'Authorization': f'Bearer {self._token(channel)}', **kwargs.pop('headers', {})}
        return self._json(self.http.request(method, f'{base}/{path}', headers=headers, **kwargs))

    @staticmethod
    def _json(resp):
        if resp.status_code == 204:
            return {}
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            err = data.get('error')
            message = (err.get('message') if isinstance(err, dict) else data.get('error_description') or err)
            raise YouTubeError(message or f'YouTube said {resp.status_code}')
        return data

    def channel(self, row_id):
        return self.db.one('SELECT * FROM youtube_channels WHERE id = ?', row_id)

    # ---- Streams (the reusable "stream key" a spoke sends to) ----
    def create_stream(self, channel, title):
        data = self._call(channel, 'POST', 'liveStreams', params={'part': 'snippet,cdn,contentDetails'}, json={
            'snippet': {'title': title},
            'cdn': {'frameRate': 'variable', 'ingestionType': 'rtmp', 'resolution': 'variable'},
            'contentDetails': {'isReusable': True},
        })
        info = data['cdn']['ingestionInfo']
        url = info.get('rtmpsIngestionAddress') or info['ingestionAddress']
        return data['id'], url, info['streamName']

    # ---- Broadcasts ----
    def create_broadcast(self, channel, event, stream_id):
        data = self._call(channel, 'POST', 'liveBroadcasts', params={'part': 'snippet,status,contentDetails'},
                          json=self._broadcast_body(event, content=True))
        self.bind_broadcast(channel, data['id'], stream_id)
        return data['id']

    def bind_broadcast(self, channel, broadcast_id, stream_id):
        """Point a broadcast at a stream (a spoke's): its watch link stays the same."""
        self._call(channel, 'POST', 'liveBroadcasts/bind',
                   params={'id': broadcast_id, 'streamId': stream_id, 'part': 'id,contentDetails'})

    def update_broadcast(self, channel, broadcast_id, event):
        self._call(channel, 'PUT', 'liveBroadcasts', params={'part': 'snippet,status'},
                   json={'id': broadcast_id, **self._broadcast_body(event, content=False)})

    @staticmethod
    def _broadcast_body(event, content):
        body = {
            'snippet': {'title': event['title'][:100], 'description': event['description'][:5000],
                        # YouTube rejects a start time in the past
                        'scheduledStartTime': iso(max(event['start_at'], time.time() + 30))},
            'status': {'privacyStatus': event['privacy'],
                       'selfDeclaredMadeForKids': bool(event['made_for_kids'])},
        }
        if content:
            body['contentDetails'] = {'enableAutoStart': True, 'enableAutoStop': True, 'enableDvr': True,
                                      'latencyPreference': event['latency'],
                                      'monitorStream': {'enableMonitorStream': False}}
        return body

    def set_thumbnail(self, channel, broadcast_id, image, content_type):
        self._call(channel, 'POST', 'thumbnails/set', upload=True, params={'videoId': broadcast_id},
                   content=image, headers={'Content-Type': content_type})

    # ---- Videos (publishing edits) ----
    def upload_video(self, channel, path, meta, progress=None, chunk=8 * 1024 * 1024):
        """Resumable upload of a finished edit as a new video. Returns the video id."""
        size = path.stat().st_size
        start = self.http.post(
            f'{API_BASE}/upload/youtube/v3/videos', params={'uploadType': 'resumable', 'part': 'snippet,status'},
            headers={'Authorization': f'Bearer {self._token(channel)}', 'X-Upload-Content-Type': 'video/mp4',
                     'X-Upload-Content-Length': str(size)},
            json={'snippet': {'title': meta['title'][:100], 'description': meta['description'][:5000]},
                  'status': {'privacyStatus': meta['privacy'], 'selfDeclaredMadeForKids': meta['made_for_kids']}})
        if start.status_code >= 400:
            self._json(start)
        session = start.headers['location']
        with open(path, 'rb') as f:
            offset, stalls = 0, 0
            while offset < size:
                f.seek(offset)
                data = f.read(chunk)
                end = offset + len(data) - 1
                resp = self.http.put(session, content=data, timeout=300, headers={
                    'Authorization': f'Bearer {self._token(channel)}', 'Content-Type': 'video/mp4',
                    'Content-Range': f'bytes {offset}-{end}/{size}'})
                if resp.status_code == 308:          # chunk received, keep going
                    received = resp.headers.get('range', '')
                    new_offset = int(received.split('-')[1]) + 1 if received else offset
                    stalls = stalls + 1 if new_offset == offset else 0
                    if stalls >= 3:
                        raise YouTubeError('YouTube stopped accepting the upload')
                    offset = new_offset
                else:
                    return self._json(resp)['id']
                if progress:
                    progress(offset / size)
        raise YouTubeError('Upload ended without a video id')

    def set_privacy(self, channel, video_id, privacy):
        """Change a video's privacy, keeping its other status settings (update replaces the whole part)."""
        items = self._call(channel, 'GET', 'videos', params={'part': 'status', 'id': video_id}).get('items', [])
        if not items:
            raise YouTubeError("Couldn't find the original live video")
        status = items[0]['status']
        status['privacyStatus'] = privacy
        status.pop('uploadStatus', None)
        status.pop('failureReason', None)
        status.pop('rejectionReason', None)
        self._call(channel, 'PUT', 'videos', params={'part': 'status'}, json={'id': video_id, 'status': status})

    def delete_broadcast(self, channel, broadcast_id):
        self._call(channel, 'DELETE', 'liveBroadcasts', params={'id': broadcast_id})

    def broadcast_states(self, channel, broadcast_ids):
        """broadcast id -> lifeCycleStatus (created, ready, testing, live, complete, revoked)."""
        data = self._call(channel, 'GET', 'liveBroadcasts',
                          params={'part': 'status', 'id': ','.join(broadcast_ids), 'maxResults': 50})
        return {item['id']: item['status']['lifeCycleStatus'] for item in data.get('items', [])}
