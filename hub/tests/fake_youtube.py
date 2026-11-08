#!/usr/bin/env python3
"""
A stand-in for Google's OAuth device flow and the YouTube Data API, for testing
the hub's YouTube features without a real channel.

    python3 tests/fake_youtube.py [port] [ingest_url]

Run the hub with
    TOUCHSTREAM_GOOGLE_OAUTH_BASE=http://127.0.0.1:<port>
    TOUCHSTREAM_YOUTUBE_API_BASE=http://127.0.0.1:<port>
and any client id ending in .apps.googleusercontent.com. Streams it creates
point at ingest_url (default rtmp://127.0.0.1:19350/live2), so a local RTMP
listener can check that video really arrives. GET /calls lists what the hub did.
"""
import itertools
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
INGEST = sys.argv[2] if len(sys.argv) > 2 else 'rtmp://127.0.0.1:19350/live2'
ids = itertools.count(1)
calls = []
polls = {}
broadcasts = {}
uploads = {}      # upload session -> {'meta', 'size', 'received'}
videos = {}       # video id -> status


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.headers.get('Content-Type', '').startswith('application/json'):
            return json.loads(raw or b'{}')
        if self.headers.get('Content-Type', '').startswith('application/x-www-form-urlencoded'):
            return {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        return {'bytes': len(raw), 'type': self.headers.get('Content-Type')}

    def _route(self, method):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = self._body() if method in ('POST', 'PUT') else {}
        if isinstance(body, dict) and 'bytes' in body and url.path.startswith('/upload-session/'):
            body = {'bytes': body['bytes']}
        calls.append({'method': method, 'path': url.path, 'query': query, 'body': body})
        p = url.path
        if p == '/calls':
            return self._send(calls)
        if p == '/device/code':
            code = f'dev{next(ids)}'
            polls[code] = 0
            return self._send({'device_code': code, 'user_code': 'TSHB-7Q4K', 'expires_in': 1800, 'interval': 1,
                               'verification_url': 'https://www.google.com/device'})
        if p == '/token':
            if body.get('grant_type', '').endswith('device_code'):
                polls[body['device_code']] = polls.get(body['device_code'], 0) + 1
                if polls[body['device_code']] < 2:
                    return self._send({'error': 'authorization_pending'}, 428)
                return self._send({'access_token': f'at{next(ids)}', 'refresh_token': 'rt-test',
                                   'expires_in': 3600})
            return self._send({'access_token': f'at{next(ids)}', 'expires_in': 3600})
        if p == '/revoke':
            return self._send({})
        if not self.headers.get('Authorization', '').startswith('Bearer at'):
            return self._send({'error': {'message': 'Invalid credentials'}}, 401)
        if p == '/youtube/v3/channels':
            return self._send({'items': [{'id': 'UCfake', 'snippet': {
                'title': 'Cinderwire Test Channel', 'thumbnails': {'default': {'url': ''}}}}]})
        if p == '/youtube/v3/liveStreams' and method == 'POST':
            n = next(ids)
            return self._send({'id': f'stream{n}', 'cdn': {'ingestionInfo': {
                'ingestionAddress': INGEST, 'streamName': f'key{n}'}}})
        if p == '/youtube/v3/liveBroadcasts' and method == 'POST':
            bid = f'bcast{next(ids)}'
            broadcasts[bid] = {'status': 'ready', **body}
            return self._send({'id': bid, **body})
        if p == '/youtube/v3/liveBroadcasts/bind':
            return self._send({'id': query['id']})
        if p == '/youtube/v3/liveBroadcasts' and method == 'PUT':
            broadcasts.get(body.get('id'), {}).update(body)
            return self._send(body)
        if p == '/youtube/v3/liveBroadcasts' and method == 'DELETE':
            broadcasts.pop(query.get('id'), None)
            self.send_response(204)
            self.end_headers()
            return
        if p == '/youtube/v3/liveBroadcasts' and method == 'GET':
            return self._send({'items': [{'id': b, 'status': {'lifeCycleStatus': v['status']}}
                                         for b, v in broadcasts.items()]})
        if p == '/upload/youtube/v3/videos' and query.get('uploadType') == 'resumable':
            sid = f'up{next(ids)}'
            uploads[sid] = {'meta': body, 'size': int(self.headers['X-Upload-Content-Length']), 'received': 0}
            return self._send({}, headers={'Location': f'http://127.0.0.1:{PORT}/upload-session/{sid}'})
        if p.startswith('/upload-session/') and method == 'PUT':
            up = uploads[p.rsplit('/', 1)[1]]
            first, last = self.headers['Content-Range'].split(' ')[1].split('/')[0].split('-')
            up['received'] = int(last) + 1
            if up['received'] < up['size']:
                return self._send({}, 308, {'Range': f"bytes=0-{last}"})
            vid = f'video{next(ids)}'
            videos[vid] = {'privacyStatus': up['meta']['status']['privacyStatus'], 'uploadStatus': 'uploaded'}
            return self._send({'id': vid, 'snippet': up['meta']['snippet']})
        if p == '/youtube/v3/videos' and method == 'GET':
            vid = query['id']
            status = videos.get(vid) or {'privacyStatus': 'public', 'uploadStatus': 'processed', 'embeddable': True,
                                         'license': 'youtube', 'publicStatsViewable': True,
                                         'selfDeclaredMadeForKids': False}
            return self._send({'items': [{'id': vid, 'status': status}]})
        if p == '/youtube/v3/videos' and method == 'PUT':
            videos[body['id']] = body['status']
            return self._send(body)
        if p == '/upload/youtube/v3/thumbnails/set':
            return self._send({'items': [{'default': {'url': 'x'}}]})
        return self._send({'error': {'message': f'Not faked: {method} {p}'}}, 404)

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def do_PUT(self):
        self._route('PUT')

    def do_DELETE(self):
        self._route('DELETE')


if __name__ == '__main__':
    print(f'Fake YouTube on http://127.0.0.1:{PORT}, ingest {INGEST}')
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
