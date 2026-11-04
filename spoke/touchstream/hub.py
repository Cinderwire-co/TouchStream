"""Link to a TouchStream hub: pairing, heartbeats, and the state the hub asks for

The spoke always calls the hub (never the other way round), so it works behind
any home or venue router. Every few seconds it reports its status and gets back
what to do: off / preview / live, bitrate, local recording, and one-off commands.
If the hub can't be reached the spoke keeps doing what it was doing, so a
stream or local recording in progress isn't interrupted by a blip.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request

from . import VERSION
from .config import load_config, save_config

HEARTBEAT_SECONDS = 3


def normalize_url(address):
    """'hub.example.com' -> https://..., '192.168.1.20:8080' or 'name.local' -> http://..."""
    address = address.strip().rstrip('/')
    if '://' in address:
        return address
    host = address.split('/')[0]
    local = (re.match(r'^[\d.]+(:\d+)?$', host) or ':' in host or host.split(':')[0].endswith('.local')
             or host.startswith('localhost'))
    return ('http://' if local else 'https://') + address


def request(method, url, body=None, headers=None, data=None, timeout=10):
    payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, method=method, data=payload,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def credentials():
    """(hub_url, auth headers) when paired, else (None, None)."""
    cfg = load_config()
    if not (cfg.get('hub_url') and cfg.get('spoke_id') and cfg.get('hub_token')):
        return None, None
    return cfg['hub_url'], {'X-Spoke-Id': cfg['spoke_id'], 'Authorization': f"Bearer {cfg['hub_token']}"}


def save_pairing(hub_url, spoke_id, token):
    cfg = load_config()
    cfg.update(hub_url=hub_url, spoke_id=spoke_id, hub_token=token)
    save_config(cfg)


def pair(address, code):
    """Pair with a hub using the code shown in its dashboard. Returns (ok, message)."""
    url = normalize_url(address)
    try:
        resp = request('POST', f'{url}/api/spoke/pair',
                       {'code': code, 'name': load_config().get('device_name'), 'version': VERSION})
    except urllib.error.HTTPError as e:
        try:
            return False, json.load(e).get('detail', f'Hub said {e.code}')
        except Exception:
            return False, f'Hub said {e.code}'
    except Exception as e:
        return False, f"Can't reach {url} ({getattr(e, 'reason', e)})"
    save_pairing(url, resp['spoke_id'], resp['token'])
    return True, 'Connected'


def unpair():
    cfg = load_config()
    for key in ('hub_url', 'spoke_id', 'hub_token'):
        cfg[key] = None
    save_config(cfg)


class HubLink:
    def __init__(self, get_status, on_desired):
        self.get_status = get_status      # () -> dict reported to the hub
        self.on_desired = on_desired      # (dict | None) -> None, called on the heartbeat thread
        self.connected = False
        self.last_error = None
        self.last_ok = time.time()        # the fallback counts from boot if the hub is never reached

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            url, headers = credentials()
            if url:
                self._beat(url, headers)
            else:
                self.connected = False
            time.sleep(HEARTBEAT_SECONDS)

    def _beat(self, url, headers):
        try:
            desired = request('POST', f'{url}/api/spoke/heartbeat', self.get_status(), headers=headers, timeout=8)
        except urllib.error.HTTPError as e:
            self.connected = False
            self.last_error = f'Hub said {e.code}'
            if e.code == 401:
                # The hub removed this spoke: go back to unpaired
                print("Hub no longer knows this spoke; unpairing")
                unpair()
                self.on_desired(None)
            return
        except Exception as e:
            self.connected = False
            self.last_error = str(getattr(e, 'reason', e))
            return
        self.connected = True
        self.last_error = None
        self.last_ok = time.time()
        self.on_desired(desired)
