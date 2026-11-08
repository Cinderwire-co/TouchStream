"""Updates: the hub and its spokes keep themselves up to date from TouchStream's GitHub releases

The hub looks for a new release every few hours. With "Install updates automatically" on (Settings >
Updates, the default), it updates itself once nothing is live, about to go live, or being worked on:
it runs its own install script from the new release, the same as running it again by hand, as a job
of its own (launchd, systemd or Task Scheduler), so the hub stopping doesn't stop it. Then each spoke,
once it's idle, is offered the spoke of the hub's own version (spoke/internal/update: the spoke checks
it was signed by TouchStream's release key, and goes back to the version before if the new one won't
start). With it off, the dashboard says an update is ready and nothing changes until you choose Update.

A hub in Docker (or Coolify) can't replace itself: it says when there's a new image to pull instead.

TOUCHSTREAM_UPDATES=off turns all of it off (tests, and developers' hubs). TOUCHSTREAM_RELEASES and
TOUCHSTREAM_RELEASES_API point it at another copy of the releases (tests).
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from . import VERSION, settings

REPO = 'https://github.com/Cinderwire-co/TouchStream'
RELEASES = os.environ.get('TOUCHSTREAM_RELEASES', REPO).rstrip('/')
RELEASES_API = os.environ.get('TOUCHSTREAM_RELEASES_API',
                              'https://api.github.com/repos/Cinderwire-co/TouchStream/releases/latest')
RAW = 'https://raw.githubusercontent.com/Cinderwire-co/TouchStream'
CHECK_EVERY = 6 * 3600
QUIET_FOR = 3600         # nothing may be due to go live for this long before an update starts
APPLY_TIMEOUT = 20 * 60  # an update that hasn't brought the new version up by then has failed
SPOKE_BUNDLE = 'touchstream-spoke-{v}-picokvm.tar.gz'
SPOKE_SIGNATURE = 'touchstream-spoke-{v}-picokvm.json'
ENABLED = os.environ.get('TOUCHSTREAM_UPDATES', 'on').lower() not in ('off', '0', 'no', 'false')
JOB = 'co.cinderwire.touchstream-hub-update'


def parse(version):
    """'0.3.0' or 'v0.3.0' -> (0, 3, 0); None for anything else (developers' builds: '0.1.0-dev')."""
    m = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(version or '').strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(a, b):
    """Whether version a is a release newer than b (False if either isn't a release)."""
    pa, pb = parse(a), parse(b)
    return bool(pa and pb and pa > pb)


def install_kind():
    """How this hub was installed, which says how it can update itself: macos, windows or linux (the
    install scripts), docker (pull the new image), or source (a checkout: a developer's, by hand)."""
    kind = os.environ.get('TOUCHSTREAM_INSTALL', '').lower()
    if kind:
        return kind
    if Path('/.dockerenv').exists():
        return 'docker'
    here = Path(__file__).resolve().as_posix()
    if '/uv/tools/touchstream-hub/' in here:   # put there by an install script (uv tool install)
        return {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform, 'linux')
    return 'source'


class Updates:
    def __init__(self, db, version=VERSION):
        self.db = db
        self.version = version
        self.kind = install_kind()
        self.http = httpx.Client(timeout=30, follow_redirects=True)
        self.lock = threading.Lock()
        self.folder = settings.DATA_DIR / 'updates'
        self.busy = None   # a function: what's keeping the hub busy right now ('' when nothing is)

    # ---- What the dashboard shows ----
    def auto(self):
        return bool(self.db.get_setting('updates_auto', True))

    def set_auto(self, on):
        self.db.set_setting('updates_auto', bool(on))

    def state(self):
        return self.db.get_setting('update_state') or {}

    def _save(self, **changes):
        with self.lock:
            state = {**self.state(), **changes}
            self.db.set_setting('update_state', state)
            return state

    def can_update_self(self):
        return self.kind in ('macos', 'linux', 'windows')

    def info(self):
        state = self.state()
        latest = state.get('latest')
        waiting = self.busy() if self.busy and latest and newer(latest, self.version) else ''
        return {'version': self.version, 'install': self.kind, 'can_update_self': self.can_update_self(),
                'enabled': ENABLED, 'auto': self.auto(), 'latest': latest,
                'available': bool(latest and newer(latest, self.version)),
                'notes': state.get('notes') or '', 'notes_url': state.get('url'), 'checked_at': state.get('checked_at'),
                'error': state.get('error'), 'applying': state.get('applying'), 'requested': bool(state.get('requested')),
                'updated': state.get('updated'), 'waiting_for': waiting,
                'docker_image': 'ghcr.io/cinderwire-co/touchstream-hub' if self.kind == 'docker' else None}

    # ---- Looking for a new release ----
    def check(self):
        """Ask GitHub for the latest release. Returns the state saved."""
        try:
            resp = self.http.get(RELEASES_API, headers={'Accept': 'application/vnd.github+json'})
            if resp.status_code == 404:   # no release yet
                return self._save(checked_at=time.time(), error=None)
            resp.raise_for_status()
            release = resp.json()
        except httpx.HTTPError as e:
            return self._save(checked_at=time.time(), error=f"Couldn't reach GitHub to look for updates: {e}")
        except ValueError:
            return self._save(checked_at=time.time(), error="GitHub's answer about updates didn't make sense; trying again later")
        tag = str(release.get('tag_name') or '')
        if not parse(tag):
            return self._save(checked_at=time.time(), error=None)
        return self._save(checked_at=time.time(), error=None, latest=tag.lstrip('v'),
                          notes=str(release.get('body') or '')[:4000], url=release.get('html_url'))

    # ---- Updating the hub ----
    def request(self):
        """Update now (Update on the dashboard): straight away if nothing's going on, else as soon as it isn't."""
        info = self.info()
        if not info['available']:
            raise UpdateError("There's no update to install")
        if not self.can_update_self():
            raise UpdateError(self._by_hand())
        self._save(requested=True)
        if not (self.busy and self.busy()):
            self.apply()
        return self.info()

    def apply(self):
        """Run the new release's install script as a job of its own: it stops this hub and starts the new one."""
        latest = self.state().get('latest')
        if not latest or not newer(latest, self.version) or not self.can_update_self():
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        log = self.folder / 'update.log'
        port = str(settings.HTTP_PORT)
        env = {'TOUCHSTREAM_SOURCE': f'{RELEASES}/archive/refs/tags/v{latest}.zip#subdirectory=hub',
               'TOUCHSTREAM_NO_OPEN': '1', 'TOUCHSTREAM_PORT': port, 'TOUCHSTREAM_BRANCH': f'v{latest}'}
        script = f'{RAW}/v{latest}/hub/install/' + {'macos': 'macos.sh', 'linux': 'linux.sh', 'windows': 'windows.ps1'}[self.kind]
        self._save(applying={'version': latest, 'from': self.version, 'at': time.time()}, requested=False, error=None)
        print(f'Updating to TouchStream {latest}: {log}')
        try:
            if self.kind == 'windows':
                self._run_windows(script, env, log)
            else:
                self._run_unix(script, env, log)
        except (OSError, subprocess.SubprocessError) as e:
            self._save(applying=None, error=f"The update couldn't start: {e}")

    def _run_unix(self, script, env, log):
        exports = ' '.join(f'{k}={shlex.quote(v)}' for k, v in env.items())
        command = f'{exports} bash -c "$(curl -fsSL {shlex.quote(script)})" >> {shlex.quote(str(log))} 2>&1'
        if self.kind == 'macos':
            # A launchd job of its own: the install script stops the hub's job, which takes its children with it
            plist = self.folder / f'{JOB}.plist'
            plist.write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>{JOB}</string>
    <key>ProgramArguments</key><array><string>/bin/bash</string><string>-c</string><string>{_xml(command)}</string></array>
    <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin</string></dict>
    <key>RunAtLoad</key><true/>
</dict></plist>
''')
            domain = f'gui/{os.getuid()}'
            subprocess.run(['launchctl', 'bootout', f'{domain}/{JOB}'], capture_output=True)
            subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True, capture_output=True)
        elif subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True).returncode == 0:
            # A unit of its own: stopping the hub's unit stops everything in it
            subprocess.run(['systemd-run', '--user', '--collect', f'--unit=touchstream-hub-update-{int(time.time())}',
                            '/bin/bash', '-c', command], check=True, capture_output=True)
        else:
            subprocess.Popen(['/bin/bash', '-c', command], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _run_windows(self, script, env, log):
        # A scheduled task of its own: stopping the hub's task ends what it started
        runner = self.folder / 'update.ps1'
        lines = [f"$env:{k} = '{v}'" for k, v in env.items()]
        lines.append(f"irm '{script}' | iex *>> '{log}'")
        runner.write_text('\r\n'.join(lines) + '\r\n', encoding='utf-8')
        task = 'TouchStream Hub Update'
        ps = (f"$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "
              f"'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{runner}\"'; "
              f"$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited; "
              f"Register-ScheduledTask -TaskName '{task}' -Action $a -Principal $p -Force | Out-Null; "
              f"Start-ScheduledTask -TaskName '{task}'")
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps], check=True, capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def _by_hand(self):
        if self.kind == 'docker':
            return ('This hub runs in Docker: pull the new image (ghcr.io/cinderwire-co/touchstream-hub) and start it '
                    'again. On Coolify, choose Redeploy.')
        return 'This hub runs from source: pull the new version and start it again.'

    def started(self):
        """At startup: an update that was underway either worked (this is the new version) or didn't."""
        applying = self.state().get('applying')
        if not applying:
            return
        if applying.get('version') == self.version:
            print(f"Updated to TouchStream {self.version}")
            self._save(applying=None, updated={'from': applying.get('from'), 'to': self.version, 'at': time.time()})
        elif time.time() - applying.get('at', 0) > APPLY_TIMEOUT:
            self._save(applying=None, error=f"The update to {applying.get('version')} didn't finish: "
                                            f"see {self.folder / 'update.log'}")

    # ---- The spokes ----
    def bundle(self):
        """The spoke of this hub's version, fetched from its release once: {path, sha256, size, signature},
        or None if there isn't one (a developer's hub, or no release for this version)."""
        if not ENABLED or not parse(self.version):
            return None
        name = SPOKE_BUNDLE.format(v=self.version)
        path, meta_path = self.folder / name, self.folder / SPOKE_SIGNATURE.format(v=self.version)
        try:
            meta = json.loads(meta_path.read_text())
            if path.stat().st_size == meta['size']:
                return {**meta, 'path': path}
        except (OSError, ValueError, KeyError):
            pass
        failed = self.state().get('bundle_failed') or {}
        if failed.get('version') == self.version and time.time() - failed.get('at', 0) < 3600:
            return None   # tried within the hour
        base = f'{RELEASES}/releases/download/v{self.version}'
        try:
            meta = self.http.get(f'{base}/{SPOKE_SIGNATURE.format(v=self.version)}').raise_for_status().json()
            if meta.get('version') != self.version:
                raise ValueError(f"the release's spoke says it's {meta.get('version')}")
            self.folder.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix('.part')
            sha = hashlib.sha256()
            with self.http.stream('GET', f'{base}/{name}') as resp, open(part, 'wb') as f:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(1 << 16):
                    sha.update(chunk)
                    f.write(chunk)
            if sha.hexdigest() != meta['sha256'] or part.stat().st_size != meta['size']:
                part.unlink(missing_ok=True)
                raise ValueError("the spoke didn't arrive whole")
            part.replace(path)
            meta_path.write_text(json.dumps(meta))
            for old in self.folder.glob('touchstream-spoke-*'):
                if self.version not in old.name:
                    old.unlink(missing_ok=True)
            return {**meta, 'path': path}
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            print(f"Couldn't fetch the spoke for {self.version}: {e}")
            self._save(bundle_failed={'version': self.version, 'at': time.time(), 'error': str(e)})
            return None

    def requested_spokes(self):
        return set(self.db.get_setting('spoke_update_requests') or [])

    def request_spoke(self, spoke_id, on=True):
        ids = self.requested_spokes()
        ids = ids | {spoke_id} if on else ids - {spoke_id}
        self.db.set_setting('spoke_update_requests', sorted(ids))

    def spoke_view(self, spoke, online):
        """What the dashboard says about a spoke's version."""
        status = spoke.get('status') or {}
        version = status.get('version')
        out = {'version': version, 'hub_version': self.version, 'state': 'current', 'progress': None,
               'error': status.get('update_error'), 'failed': status.get('update_failed'),
               'requested': spoke['id'] in self.requested_spokes()}
        if status.get('update'):
            out.update(state='updating', progress=(status['update'] or {}).get('progress'))
        elif not parse(version):
            out['state'] = 'developer' if version else 'unknown'
        elif newer(self.version, version):
            out['state'] = 'failed' if status.get('update_failed') == self.version else 'available'
        elif newer(version, self.version):
            out['state'] = 'ahead'
        if not online and out['state'] == 'updating':
            out['state'] = 'restarting'
        return out

    def spoke_offer(self, spoke, status, idle):
        """The update for a spoke's heartbeat, or None. Offered only when it's behind the hub, its update is
        wanted (automatic updates, or Update chosen for it), it hasn't already failed by itself, and it's idle."""
        version = status.get('version')
        if not ENABLED or not parse(version) or not newer(self.version, version) or status.get('update'):
            return None
        requested = spoke['id'] in self.requested_spokes()
        if not (self.auto() or requested):
            return None
        if status.get('update_failed') == self.version and not requested:
            return None
        if not idle:
            return None
        bundle = self.bundle()
        if not bundle:
            return None
        if requested:
            self.request_spoke(spoke['id'], False)
        return {'version': self.version, 'url': f'/api/spoke/update/{self.version}', 'sha256': bundle['sha256'],
                'size': bundle['size'], 'signature': bundle['signature']}

    # ---- The loop ----
    def start(self, busy):
        self.busy = busy
        self.started()
        if ENABLED:
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        time.sleep(60)
        while True:
            try:
                if time.time() - (self.state().get('checked_at') or 0) > CHECK_EVERY:
                    self.check()
                state = self.state()
                latest = state.get('latest')
                wanted = self.auto() or state.get('requested')
                if latest and newer(latest, self.version) and wanted and self.can_update_self() \
                        and not state.get('applying') and not self.busy():
                    self.apply()
                elif state.get('applying') and time.time() - state['applying'].get('at', 0) > APPLY_TIMEOUT:
                    self.started()
            except Exception as e:  # never let the loop die
                print(f'Updates: {e}')
            time.sleep(60)


class UpdateError(Exception):
    pass


def _xml(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
