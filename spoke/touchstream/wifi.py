"""Wi-Fi setup through NetworkManager (nmcli)

The kiosk runs in a local, active session and the app user is in the netdev
group, so NetworkManager's polkit rule lets it manage connections without sudo.
"""
import subprocess
from dataclasses import dataclass

IFACE = 'wlan0'


@dataclass
class Network:
    ssid: str
    signal: int        # 0-100
    secured: bool
    in_use: bool
    saved: bool = False


def _nmcli(*args, timeout=30):
    return subprocess.run(['nmcli', *args], capture_output=True, text=True, timeout=timeout)


def _split_terse(line):
    """Split one line of `nmcli -t` output on unescaped colons."""
    fields, cur, escaped = [], [], False
    for ch in line:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == ':':
            fields.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append(''.join(cur))
    return fields


def saved_ssids():
    """SSIDs of Wi-Fi connection profiles NetworkManager already has."""
    out = _nmcli('-t', '-f', 'NAME,TYPE', 'connection', 'show').stdout
    names = set()
    for line in out.splitlines():
        name, kind = (_split_terse(line) + ['', ''])[:2]
        if kind == '802-11-wireless':
            names.add(name)
    return names


def scan(rescan=True):
    """Visible networks, strongest first, one entry per SSID (hidden ones skipped)."""
    _nmcli('radio', 'wifi', 'on')
    out = _nmcli('-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list',
                 'ifname', IFACE, '--rescan', 'yes' if rescan else 'no').stdout
    saved = saved_ssids()
    best = {}
    for line in out.splitlines():
        fields = _split_terse(line)
        if len(fields) < 4 or not fields[1]:
            continue
        in_use, ssid, signal, security = fields[:4]
        net = Network(ssid=ssid, signal=int(signal or 0),
                      secured=bool(security.strip()) and security.strip() != '--',
                      in_use=in_use.strip() == '*', saved=ssid in saved)
        prev = best.get(ssid)
        if not prev or net.in_use or (net.signal > prev.signal and not prev.in_use):
            best[ssid] = net
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal))


def current():
    """SSID of the connected Wi-Fi network, or None."""
    out = _nmcli('-t', '-f', 'TYPE,STATE,CONNECTION', 'device').stdout
    for line in out.splitlines():
        kind, state, name = (_split_terse(line) + ['', '', ''])[:3]
        if kind == 'wifi' and state == 'connected':
            return name
    return None


def connect(ssid, password=None):
    """Join a network. Returns (ok, message)."""
    try:
        if password is None and ssid in saved_ssids():
            result = _nmcli('--wait', '45', 'connection', 'up', 'id', ssid, timeout=60)
        else:
            existed = ssid in saved_ssids()
            args = ['--wait', '45', 'device', 'wifi', 'connect', ssid, 'ifname', IFACE]
            if password:
                args += ['password', password]
            result = _nmcli(*args, timeout=60)
            if result.returncode != 0 and not existed:
                # Don't leave a profile with a wrong password behind
                _nmcli('connection', 'delete', 'id', ssid)
    except subprocess.TimeoutExpired:
        return False, 'Timed out connecting'
    if result.returncode == 0:
        return True, f'Connected to {ssid}'
    err = (result.stderr or result.stdout).strip()
    if 'Secrets were required' in err or 'password' in err.lower():
        return False, 'Wrong password'
    return False, err.split('\n')[-1].replace('Error: ', '') or 'Could not connect'


def forget(ssid):
    _nmcli('connection', 'delete', 'id', ssid)
