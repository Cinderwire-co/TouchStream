"""Discovery: UDP beacon + HTTP API (GET /info, POST /adopt, remote management)

Also the page a phone opens from a recording's QR code (web/recording.html): /r/<link> to
watch it, trim it and download it, all from the spoke itself.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import APP_DIR, ASSETS_DIR, VERSION, recorder
from .config import load_config
from .hub import save_pairing
from .system import device_ip

DISCOVERY_PORT = 6077
DISCOVERY_UDP_PORT = 9999
PAGE = os.path.join(os.path.dirname(__file__), 'web', 'recording.html')
BRAND = {'touchstream_mark.png': ('brand', 'image/png'), 'Bricolage-Regular.ttf': ('fonts', 'font/ttf'),
         'Bricolage-SemiBold.ttf': ('fonts', 'font/ttf'), 'Bricolage-Bold.ttf': ('fonts', 'font/ttf'),
         'JetBrainsMono-Medium.ttf': ('fonts', 'font/ttf')}


def update_and_restart():
    """git pull the checkout, then restart the app in place."""
    try:
        print("Running git pull...")
        result = subprocess.run(['git', 'pull', '--ff-only'], cwd=APP_DIR,
                                check=False, capture_output=True, text=True)
        print(f"Git output: {result.stdout}")
        if result.returncode != 0:
            print(f"Git error: {result.stderr}")
        print("Restarting application...")
        os.execv(sys.executable, ['python3'] + sys.argv)
    except Exception as e:
        print(f"Update failed: {e}")


class DiscoveryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

    def do_GET(self):
        if self.path.startswith('/download/'):
            self._download(self.path.split('/')[2])
            return

        if self.path.startswith('/r/'):
            self._recording_page(self.path.split('?')[0].split('/')[2:])
            return

        if self.path.startswith('/brand/') and self.path[7:] in BRAND:
            folder, kind = BRAND[self.path[7:]]
            with open(os.path.join(ASSETS_DIR, folder, self.path[7:]), 'rb') as f:
                self._send_bytes(f.read(), kind, cache=True)
            return

        if self.path == '/info':
            cfg = load_config()
            data = {
                'device_id': cfg.get('spoke_id') or 'unadopted',
                'device_name': cfg.get('device_name'),
                'ip': device_ip() or 'Unknown',
                'model': 'raspberry-pi',
                'status': 'ready',
                'version': VERSION,
                'paired': bool(cfg.get('hub_token')),
            }
            self._send_json(data)
            return

        # logind lets the kiosk's local session power off/reboot without sudo
        if self.path == '/shutdown':
            self._send_json({'status': 'shutdown_initiated'})
            threading.Thread(target=lambda: (time.sleep(1), os.system('systemctl poweroff'))).start()
            return

        if self.path == '/reboot':
            self._send_json({'status': 'reboot_initiated'})
            threading.Thread(target=lambda: (time.sleep(1), os.system('systemctl reboot'))).start()
            return

        if self.path == '/update':
            self._send_json({'status': 'updating'})
            threading.Thread(target=lambda: (time.sleep(1), update_and_restart())).start()
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path.startswith('/r/') and self.path.endswith('/clip'):
            # Trim a shared recording: {start, end} in seconds
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length) or b'{}')
                self._send_json({'id': recorder.clip(self.path.split('/')[2], body['start'], body['end'])})
            except (ValueError, KeyError, TypeError) as e:
                self._send_json({'error': str(e) or 'Pick a start and an end'}, 400)
            return

        if self.path == '/adopt':
            # A hub on the LAN adopting this spoke: hands over its address and credentials.
            # Only an unpaired spoke accepts; otherwise only the current hub may re-adopt it.
            length = int(self.headers.get('Content-Length', 0))
            try:
                payload = json.loads(self.rfile.read(length))
                cfg = load_config()
                current = cfg.get('hub_token')
                given = self.headers.get('Authorization', '').removeprefix('Bearer ')
                if current and given != current:
                    self._send_json({'status': 'error', 'error': 'Already paired with a hub'}, 403)
                    return
                save_pairing(payload['hub_url'], payload['spoke_id'], payload['token'])
                print(f"Adopted by hub at {payload['hub_url']}")
                self._send_json({'status': 'ok', 'saved': True})
            except Exception as e:
                self._send_json({'status': 'error', 'error': str(e)}, 400)
            return

        self.send_response(404)
        self.end_headers()

    def _recording_page(self, parts):
        """/r/<link>: the page; /info, /video.mp4, /clip/<id> and /clip/<id>/file: what it uses."""
        token, rest = parts[0], parts[1:]
        if not rest:
            with open(PAGE, 'rb') as f:
                self._send_bytes(f.read(), 'text/html; charset=utf-8')
            return
        session = recorder.shared(token)
        if not session:
            self._expired()
        elif rest == ['info']:
            state, error = recorder.share_state(session['name'])
            t = time.localtime(session['start'])
            self._send_json({'title': time.strftime('%A %b ', t) + str(t.tm_mday) + time.strftime(' · %I:%M %p', t).replace(' 0', ' '),
                             'where': load_config().get('device_name') or '', 'duration': session['duration'],
                             'size': session['size'], 'state': state, 'error': error})
        elif rest == ['video.mp4'] and (shared := recorder.shared_file(token, wait=0)):
            self._send_file(shared[1], None)
        elif len(rest) == 2 and rest[0] == 'clip' and (state := recorder.clip_state(token, rest[1])):
            self._send_json(state)
        elif len(rest) == 3 and rest[0] == 'clip' and rest[2] == 'file' and (found := recorder.clip_file(token, rest[1])):
            start = time.localtime(found[0]['start'] + recorder.clip_state(token, rest[1])['start'])
            self._send_file(found[1], time.strftime('TouchStream %Y-%m-%d %H.%M.%S clip.mp4', start))
        else:
            self._expired()

    def _expired(self):
        self._send_bytes(b'This link has expired. Show the QR code again on the spoke.', 'text/plain', code=404)

    def _download(self, token):
        """A recording for a phone that scanned the touchscreen's QR code, as an
        attachment so phones save it instead of playing it."""
        shared = recorder.shared_file(token)
        if not shared:
            self._expired()
            return
        session, path = shared
        self._send_file(path, time.strftime('TouchStream %Y-%m-%d %H.%M.mp4', time.localtime(session['start'])))

    def _send_file(self, path, name):
        """A video, as an attachment called `name` (so phones save it), or to play in the page when
        name is None. Ranges let a player seek and an interrupted download resume."""
        with open(path, 'rb') as f:
            size = os.fstat(f.fileno()).st_size
            start, end = 0, size - 1
            ranged = self.headers.get('Range', '').removeprefix('bytes=')
            if ranged and ',' not in ranged:
                first, _, last = ranged.partition('-')
                if first:
                    start, end = int(first), min(int(last), size - 1) if last else size - 1
                elif last:
                    start = max(0, size - int(last))
            if start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            self.send_response(206 if ranged else 200)
            self.send_header('Content-Type', 'application/octet-stream' if name else 'video/mp4')
            if name:
                self.send_header('Content-Disposition', f'attachment; filename="{name}"')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if ranged:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0 and (chunk := f.read(min(left, 256 * 1024))):
                    self.wfile.write(chunk)
                    left -= len(chunk)
                if name and start == 0 and left == 0:
                    print(f"{name} downloaded to {self.client_address[0]}")
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_bytes(self, body, kind, code=200, cache=False):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        if cache:
            self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_discovery_server():
    try:
        # Threaded: a phone downloading a recording mustn't hold up the hub's requests
        server = ThreadingHTTPServer(('0.0.0.0', DISCOVERY_PORT), DiscoveryHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"Discovery HTTP server running on port {DISCOVERY_PORT}")
    except Exception as e:
        print("Failed to start discovery HTTP server:", e)


def udp_beacon_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            cfg = load_config()  # Reload to get updates
            payload = json.dumps({
                'device_name': cfg.get('device_name'),
                'device_id': cfg.get('spoke_id') or 'unadopted'
            })
            sock.sendto(payload.encode('utf-8'), ('<broadcast>', DISCOVERY_UDP_PORT))
        except Exception:
            pass
        time.sleep(5)


def start_beacon():
    threading.Thread(target=udp_beacon_thread, daemon=True).start()
