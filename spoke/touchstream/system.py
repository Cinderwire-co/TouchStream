"""Device helpers: network status, CPU metrics, display power, internet speed"""
import http.client
import os
import socket
import subprocess
import time

SPEED_TEST = 'speed.cloudflare.com'


def _run(cmd, timeout=10):
    """Run a command and return stdout ('' on any failure)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ''


def device_ip():
    """The device's own IPv4 address on the LAN, or None."""
    addrs = _run(['hostname', '-I']).split()
    for addr in addrs:
        if '.' in addr and not addr.startswith('169.254.'):
            return addr
    # Fallback when hostname -I is unavailable: route lookup (sends no packets)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def network_summary():
    """Return {'kind': 'ethernet'|'wifi'|None, 'name': str, 'ip': str|None}."""
    out = _run(['nmcli', '-t', '-f', 'TYPE,STATE,CONNECTION', 'device'])
    active = {}
    for line in out.splitlines():
        parts = line.split(':', 2)
        if len(parts) == 3 and parts[1] == 'connected' and parts[0] in ('ethernet', 'wifi'):
            active.setdefault(parts[0], parts[2].replace('\\:', ':'))
    ip = device_ip()
    if 'ethernet' in active:
        return {'kind': 'ethernet', 'name': 'Ethernet', 'ip': ip}
    if 'wifi' in active:
        return {'kind': 'wifi', 'name': active['wifi'], 'ip': ip}
    return {'kind': None, 'name': 'Offline', 'ip': None}


def cpu_temp():
    """CPU temperature in Celsius, or None."""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


class CpuUsage:
    """CPU usage percentage between successive calls to sample()."""

    def __init__(self):
        self._last = None

    def sample(self):
        try:
            with open('/proc/stat') as f:
                fields = f.readline().split()
            idle = int(fields[4])
            total = sum(int(x) for x in fields[1:8])
        except Exception:
            return None
        last, self._last = self._last, (idle, total)
        if not last or total == last[1]:
            return None
        return 100.0 * (1.0 - (idle - last[0]) / (total - last[1]))


def set_display_power(on):
    """Turn every display on or off through the compositor's output management."""
    for line in _run(['wlr-randr']).splitlines():
        if line and not line.startswith(' '):
            _run(['wlr-randr', '--output', line.split()[0], '--on' if on else '--off'])


def speed_test(down_bytes=25_000_000, up_bytes=8_000_000):
    """Internet speed against Cloudflare's speed test: {'down_mbps', 'up_mbps', 'latency_ms', 'at'}.
    Upload matters most: a live stream needs it to be comfortably above the bitrate."""
    conn = http.client.HTTPSConnection(SPEED_TEST, timeout=30)
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
