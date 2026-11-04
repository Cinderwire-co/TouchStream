"""What a hub running on someone's own Mac or PC can do on that computer

Show a folder in Finder or File Explorer, and ask for a folder with the system's
own picker (only for a browser on the same computer, and never from Docker), and
keep the computer from falling asleep before a scheduled broadcast.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from . import settings

PLATFORM = 'mac' if sys.platform == 'darwin' else 'windows' if sys.platform == 'win32' else 'linux'
PICK_PROMPT = 'Choose where TouchStream saves recordings'
_quiet = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
if sys.platform == 'win32':
    _quiet['creationflags'] = subprocess.CREATE_NO_WINDOW


def run_windowless():
    """Windows, when the hub runs in the background (pythonw, from the installer's sign-in task):
    log to a file, as there's no console, and keep the programs it starts (MediaMTX, FFmpeg)
    from opening console windows of their own."""
    if sys.platform != 'win32':
        return
    if sys.stdout is None:
        log = open(settings.DATA_DIR / 'hub.log', 'a', buffering=1, encoding='utf-8')
        sys.stdout = sys.stderr = log
    popen_init = subprocess.Popen.__init__

    def windowless(self, *args, **kwargs):
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
        popen_init(self, *args, **kwargs)
    subprocess.Popen.__init__ = windowless


def keep_awake():
    """Stop the computer idle-sleeping while the hub runs: a sleeping hub misses its
    broadcasts. The display can still sleep, and closing a laptop's lid still sleeps it."""
    if settings.IN_DOCKER or os.environ.get('TOUCHSTREAM_KEEP_AWAKE') == '0':
        return
    if sys.platform == 'darwin':
        subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())], **_quiet)   # ends with the hub
    elif sys.platform == 'win32':
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


def is_local(client_ip, own_ips=()):
    """Whether the browser making a request is on the computer the hub runs on."""
    return not settings.IN_DOCKER and bool(client_ip) and client_ip in ('127.0.0.1', '::1', *own_ips)


def reveal(path):
    """Open a folder, or show a file selected in its folder."""
    path = Path(path)
    if sys.platform == 'darwin':
        cmd = ['open', '-R', str(path)] if path.is_file() else ['open', str(path)]
    elif sys.platform == 'win32':
        cmd = ['explorer', f'/select,{path}'] if path.is_file() else ['explorer', str(path)]
    else:
        cmd = ['xdg-open', str(path if path.is_dir() else path.parent)]
    subprocess.Popen(cmd, **_quiet)


def choose_folder(current):
    """The system's folder picker, starting at `current`: the chosen folder, or None."""
    current = Path(current)
    try:
        if sys.platform == 'darwin':
            start = f' default location (POSIX file {json.dumps(str(current))})' if current.is_dir() else ''
            lines = ['tell current application to activate',
                     f'set chosen to choose folder with prompt {json.dumps(PICK_PROMPT)}{start}',
                     'POSIX path of chosen']
            cmd = ['osascript'] + [arg for line in lines for arg in ('-e', line)]
        elif sys.platform == 'win32':
            quoted = str(current).replace("'", "''")
            cmd = ['powershell', '-NoProfile', '-STA', '-Command',
                   'Add-Type -AssemblyName System.Windows.Forms;'
                   '$d = New-Object System.Windows.Forms.FolderBrowserDialog;'
                   f"$d.Description = '{PICK_PROMPT}'; $d.SelectedPath = '{quoted}'; $d.ShowNewFolderButton = $true;"
                   '$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true};'
                   "if ($d.ShowDialog($owner) -eq 'OK') { $d.SelectedPath }"]
        else:
            cmd = ['zenity', '--file-selection', '--directory', f'--title={PICK_PROMPT}', f'--filename={current}/']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                **({'creationflags': _quiet['creationflags']} if 'creationflags' in _quiet else {}))
    except (OSError, subprocess.TimeoutExpired):
        return None
    chosen = result.stdout.strip()
    return chosen.rstrip('/\\') or chosen if result.returncode == 0 and chosen else None
