"""Finds spokes on the local network from their UDP beacons, and this hub's own address

Spokes broadcast {"device_name", "device_id"} to port 9999 every 5 seconds.
This only works when the hub shares the spokes' network (a local install, or
Docker with host networking); otherwise pair spokes with a code instead.

Paired spokes keep beaconing, which is how the hub finds one that has lost it:
if this computer's address changed (a new DHCP lease), the hub tells the spoke
where it is now.
"""
import ipaddress
import json
import socket
import threading
import time
import urllib.request

from . import settings

SEEN_FOR = 20       # seconds a beacon keeps a spoke in the list
RELINK_AFTER = 30   # seconds without a heartbeat before a beaconing spoke is told where the hub is
RELINK_EVERY = 300  # ...and at most this often


class Discovery:
    def __init__(self, db):
        self.db = db
        self.found = {}   # ip -> {'name', 'device_id', 'seen'}
        self.listening = False

    def start(self):
        if settings.DISCOVERY:
            threading.Thread(target=self._listen, daemon=True).start()
            threading.Thread(target=self._relink, daemon=True).start()

    def _listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('', settings.BEACON_PORT))
        except OSError as e:
            print(f"Spoke discovery disabled: can't listen on UDP {settings.BEACON_PORT} ({e})")
            return
        self.listening = True
        print(f"Listening for spokes on UDP {settings.BEACON_PORT}")
        while True:
            data, (ip, _) = sock.recvfrom(2048)
            try:
                beacon = json.loads(data)
            except ValueError:
                continue
            self.found[ip] = {'ip': ip, 'name': beacon.get('device_name') or 'TouchStream',
                              'device_id': beacon.get('device_id'), 'seen': time.time()}

    def unadopted(self):
        now = time.time()
        return [s for s in self.found.values()
                if now - s['seen'] < SEEN_FOR and s['device_id'] in (None, 'unadopted')]

    def _relink(self):
        """Give paired spokes that beacon but don't check in this hub's current address."""
        told = {}
        while True:
            time.sleep(10)
            now = time.time()
            for beacon in list(self.found.values()):
                spoke = self.db.spoke(beacon['device_id'] or '')
                if (not spoke or now - beacon['seen'] > SEEN_FOR or now - (spoke['last_seen'] or 0) < RELINK_AFTER
                        or now - told.get(spoke['id'], 0) < RELINK_EVERY):
                    continue
                told[spoke['id']] = now
                try:
                    hub_url = f"http://{local_address_towards(beacon['ip'])}:{settings.HTTP_PORT}"
                    adopt(beacon['ip'], {'hub_url': hub_url, 'spoke_id': spoke['id'], 'token': spoke['token']},
                          token=spoke['token'])
                    print(f"Told spoke {spoke['name']} at {beacon['ip']} the hub is at {hub_url}")
                except Exception as e:
                    print(f"Couldn't reach spoke {spoke['name']} at {beacon['ip']}: {e}")


def lan_ip(towards=()):
    """This computer's address on the local network: the one it reaches spokes from,
    else the one its default route uses. None in Docker, which only sees its own network."""
    if settings.IN_DOCKER:
        return None
    for target in (*towards, '8.8.8.8', '192.168.0.1', '10.0.0.1'):
        try:
            ip = local_address_towards(target)
        except OSError:
            continue
        if not ipaddress.ip_address(ip).is_loopback and ip != '0.0.0.0':
            return ip
    return None


_own = (0, set())


def own_ips():
    """Addresses a browser on this computer might reach the hub at, besides loopback."""
    global _own
    if time.time() - _own[0] > 60:   # the host name lookup can be slow; once a minute is plenty
        ips = set()
        try:
            ips.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        except OSError:
            pass
        ip = lan_ip()
        _own = (time.time(), ips | ({ip} if ip else set()))
    return _own[1]


def local_address_towards(ip):
    """This machine's IP on the route to `ip` (no packets are sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((ip, settings.SPOKE_API_PORT))
        return sock.getsockname()[0]
    finally:
        sock.close()


def adopt(ip, payload, token=None):
    """Hand a spoke its hub address and credentials over its local API. A paired
    spoke only accepts this from its own hub, which proves it with the spoke's token."""
    headers = {'Content-Type': 'application/json', **({'Authorization': f'Bearer {token}'} if token else {})}
    req = urllib.request.Request(f'http://{ip}:{settings.SPOKE_API_PORT}/adopt',
                                 data=json.dumps(payload).encode(), method='POST', headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)
