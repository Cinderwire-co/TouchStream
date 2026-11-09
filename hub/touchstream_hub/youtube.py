"""YouTube: connect channels, create stream keys, schedule broadcasts, set thumbnails

Channels are connected with Google's device sign-in ("TVs and Limited Input
devices" OAuth client): the hub shows a code, you enter it at google.com/device.
That needs no redirect URL, so it works the same for a hub on Coolify, on a LAN
IP or on localhost. Connect as many channels (Google accounts or Brand Accounts)
as you like; each keeps its own refresh token.

The OAuth client: TouchStream's own, shared by every hub, which the hub reads from cinderwire.co
(its ID and secret, nothing else: Google treats a device client's secret as not secret, since it
ships inside the app; it's kept out of the repository and can be changed without a release). Or the
owner's own client (Settings > YouTube), or TOUCHSTREAM_GOOGLE_CLIENT_ID / _SECRET, which come first.
A channel keeps the client it was connected with: its tokens only work with that one.
TOUCHSTREAM_SHARED_CLIENT=off leaves the shared client out; TOUCHSTREAM_SHARED_CLIENT_URL,
TOUCHSTREAM_GOOGLE_OAUTH_BASE and TOUCHSTREAM_YOUTUBE_API_BASE exist so tests can point at fakes.
"""
import os
import secrets
import time
from datetime import datetime, timezone

import httpx

from . import VERSION

OAUTH_BASE = os.environ.get('TOUCHSTREAM_GOOGLE_OAUTH_BASE', 'https://oauth2.googleapis.com')
API_BASE = os.environ.get('TOUCHSTREAM_YOUTUBE_API_BASE', 'https://www.googleapis.com')
SCOPE = 'https://www.googleapis.com/auth/youtube'
DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'
SHARED_URL = os.environ.get('TOUCHSTREAM_SHARED_CLIENT_URL', 'https://cinderwire.co/api/touchstream/google-client')
SHARED = os.environ.get('TOUCHSTREAM_SHARED_CLIENT', 'on').lower() not in ('off', '0', 'no', 'false')
SHARED_EVERY = 86400   # read again daily (it can change without a release)
SHARED_RETRY = 600     # ...or after 10 minutes, if cinderwire.co couldn't be reached


class YouTubeError(Exception):
    pass


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class YouTube:
    def __init__(self, db):
        self.db = db
        self.flows = {}   # flow id -> device-flow state while someone is signing in
        self.http = httpx.Client(timeout=30)

    # ---- OAuth client: the environment's, your own (Settings), or TouchStream's shared one ----
    def local_clients(self):
        """The clients set on this hub, as (id, secret): the environment's, then your own (Settings)."""
        env = (os.environ.get('TOUCHSTREAM_GOOGLE_CLIENT_ID', ''), os.environ.get('TOUCHSTREAM_GOOGLE_CLIENT_SECRET', ''))
        stored = self.db.get_setting('youtube_client') or {}
        own = (stored.get('client_id', ''), stored.get('client_secret', ''))
        return [c for c in (env, own) if all(c)]

    def client(self, client_id=None):
        """The client new channels connect with (the hub's own, else TouchStream's shared one), or, with client_id,
        the one a channel was connected with. Reads the shared one from cinderwire.co only when it's needed."""
        local = self.local_clients()
        if client_id:
            found = next((c for c in local if c[0] == client_id), None)
            shared = None if found else self.shared()
            return found or (shared if shared and shared[0] == client_id else ('', ''))
        return local[0] if local else (self.shared() or ('', ''))

    def shared(self, again=False):
        """TouchStream's shared client, read from cinderwire.co and kept for a day; None without it."""
        if not SHARED:
            return None
        kept = self.db.get_setting('youtube_shared_client') or {}
        if again or time.time() - kept.get('read_at', 0) > (SHARED_EVERY if kept.get('client_id') else SHARED_RETRY):
            try:
                resp = self.http.get(SHARED_URL, timeout=5, headers={'User-Agent': f'TouchStream-Hub/{VERSION}'})
                data = resp.json() if resp.status_code == 200 else {}
                if data.get('client_id') and data.get('client_secret'):
                    kept = {'client_id': data['client_id'], 'client_secret': data['client_secret']}
            except (httpx.HTTPError, ValueError):
                pass   # keep what it had (without one, it asks again in 10 minutes)
            kept['read_at'] = time.time()
            self.db.set_setting('youtube_shared_client', kept)
        return (kept['client_id'], kept['client_secret']) if kept.get('client_id') else None

    def which(self):
        """Which client new channels connect with: 'env', 'own' or 'shared' ('' for none)."""
        client_id, _ = self.client()
        if not client_id:
            return ''
        if client_id == os.environ.get('TOUCHSTREAM_GOOGLE_CLIENT_ID'):
            return 'env'
        return 'own' if client_id == (self.db.get_setting('youtube_client') or {}).get('client_id') else 'shared'

    def configured(self):
        return all(self.client())

    def client_from_env(self):
        return bool(os.environ.get('TOUCHSTREAM_GOOGLE_CLIENT_ID'))

    # ---- Connecting a channel (device flow) ----
    def start_connect(self):
        client_id, client_secret = self.client()
        resp = self.http.post(f'{OAUTH_BASE}/device/code', data={'client_id': client_id, 'scope': SCOPE})
        data = self._json(resp)
        flow_id = secrets.token_urlsafe(8)
        self.flows[flow_id] = {'device_code': data['device_code'], 'interval': data.get('interval', 5),
                               'client': (client_id, client_secret),
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
        client_id, client_secret = flow['client']
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
        return {'status': 'connected', 'channel': self._save_channel(data, client_id)}

    def _save_channel(self, tokens, client_id=None):
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
                        'token_expires = ?, client_id = ?, error = NULL WHERE id = ?',
                        snippet['title'], thumb, refresh, access, expires, client_id, existing['id'])
            row_id = existing['id']
        else:
            row_id = self.db.run('INSERT INTO youtube_channels (channel_id, title, thumbnail, refresh_token, '
                                 'access_token, token_expires, client_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                                 items[0]['id'], snippet['title'], thumb, refresh, access, expires, client_id, time.time())
        return {'id': row_id, 'title': snippet['title'], 'thumbnail': thumb}

    def disconnect(self, row_id):
        """Forget a channel, and ask Google to take back the hub's access to it (the same as removing
        it at myaccount.google.com/permissions). The hub forgets it even if Google can't be reached."""
        channel = self.channel(row_id)
        if not channel:
            return
        if channel['refresh_token']:
            try:
                self.http.post(f'{OAUTH_BASE}/revoke', data={'token': channel['refresh_token']}, timeout=10)
            except httpx.HTTPError:
                pass
        self.db.run('DELETE FROM youtube_channels WHERE id = ?', row_id)

    # ---- Authenticated API calls ----
    def _token(self, channel):
        if channel['access_token'] and channel['token_expires'] > time.time() + 60:
            return channel['access_token']
        data = self._refresh(channel)
        if data.get('error') == 'invalid_client' and channel.get('client_id') == (self.shared() or ('',))[0]:
            self.shared(again=True)   # its secret changed: read it again, and try once more
            data = self._refresh(channel)
        if 'access_token' not in data:
            message = 'Sign-in expired: reconnect this channel'
            self.db.run('UPDATE youtube_channels SET error = ? WHERE id = ?', message, channel['id'])
            raise YouTubeError(message)
        channel['access_token'] = data['access_token']
        channel['token_expires'] = time.time() + data.get('expires_in', 3600)
        self.db.run('UPDATE youtube_channels SET access_token = ?, token_expires = ?, error = NULL WHERE id = ?',
                    channel['access_token'], channel['token_expires'], channel['id'])
        return channel['access_token']

    def _refresh(self, channel):
        client_id, client_secret = self.client(channel.get('client_id'))
        resp = self.http.post(f'{OAUTH_BASE}/token', data={
            'client_id': client_id, 'client_secret': client_secret,
            'refresh_token': channel['refresh_token'], 'grant_type': 'refresh_token'})
        try:
            return resp.json()
        except ValueError:
            return {}

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

    def video_stats(self, channel, video_ids):
        """View, like and comment counts, and live viewers, of up to 50 videos (one quota unit)."""
        data = self._call(channel, 'GET', 'videos', params={'part': 'statistics,liveStreamingDetails',
                                                             'id': ','.join(video_ids[:50]), 'maxResults': 50})
        return data.get('items', [])

    def broadcast_states(self, channel, broadcast_ids):
        """broadcast id -> lifeCycleStatus (created, ready, testing, live, complete, revoked)."""
        data = self._call(channel, 'GET', 'liveBroadcasts',
                          params={'part': 'status', 'id': ','.join(broadcast_ids), 'maxResults': 50})
        return {item['id']: item['status']['lifeCycleStatus'] for item in data.get('items', [])}
