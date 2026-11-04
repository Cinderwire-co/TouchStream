"""The hub's internet speed, measured on request against Cloudflare's speed test

Upload is what matters most: the hub sends every live stream on to YouTube, so it
needs comfortably more upload than the spokes' bitrates added up. Spokes run the
same test (spoke/touchstream/system.py) when the dashboard asks them.
"""
import http.client
import os
import threading
import time

SERVER = 'speed.cloudflare.com'


def measure(down_bytes=25_000_000, up_bytes=8_000_000):
    """{'down_mbps', 'up_mbps', 'latency_ms', 'at'}"""
    conn = http.client.HTTPSConnection(SERVER, timeout=30)

    def timed(path, body=None):
        start = time.perf_counter()
        conn.request('POST' if body else 'GET', path, body=body, headers={'User-Agent': 'TouchStream'})
        resp = conn.getresponse()
        while resp.read(1 << 16):
            pass
        if resp.status != 200:
            raise OSError(f'speed test server answered {resp.status}')
        return time.perf_counter() - start
    try:
        timed('/__down?bytes=0')      # connect first, so the latency is the round trip alone
        latency = min(timed('/__down?bytes=0') for _ in range(4))
        down = timed(f'/__down?bytes={down_bytes}')
        up = timed('/__up', os.urandom(up_bytes))
    finally:
        conn.close()
    return {'down_mbps': round(down_bytes * 8 / down / 1e6, 1), 'up_mbps': round(up_bytes * 8 / up / 1e6, 1),
            'latency_ms': round(latency * 1000), 'at': time.time()}


class SpeedTest:
    def __init__(self, db):
        self.db = db
        self.running = False

    def state(self):
        return {**(self.db.get_setting('speedtest') or {}), 'running': self.running}

    def start(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.db.set_setting('speedtest', measure())
        except Exception as e:
            self.db.set_setting('speedtest', {**(self.db.get_setting('speedtest') or {}), 'error': f'Speed test failed: {e}'})
        finally:
            self.running = False
