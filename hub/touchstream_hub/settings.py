"""Hub settings, all overridable with environment variables

TOUCHSTREAM_DATA            where the database and working files live
TOUCHSTREAM_RECORDINGS      the recordings folder (also settable in the dashboard)
TOUCHSTREAM_PORT            web UI / API port (8080)
TOUCHSTREAM_SRT_PORT        SRT ingest port spokes publish to (8890/udp)
TOUCHSTREAM_RTMP_PORT       RTMP ingest port for other encoders, e.g. OBS (1935)
TOUCHSTREAM_PUBLIC_HOST     host name spokes use for SRT (default: the host they reached the hub at)
TOUCHSTREAM_ADMIN_PASSWORD  set the admin password instead of the first-run screen
TOUCHSTREAM_DISCOVERY       0 to stop listening for spokes on the local network
TOUCHSTREAM_KEEP_AWAKE      0 to let the computer sleep while the hub runs (Mac/Windows)
TOUCHSTREAM_MEDIAMTX        path to the mediamtx binary
TOUCHSTREAM_FFMPEG          path to the ffmpeg binary
TOUCHSTREAM_FFPROBE         path to the ffprobe binary (default: beside ffmpeg)
"""
import os
import shutil
import sys
from pathlib import Path


def _data_dir():
    if os.environ.get('TOUCHSTREAM_DATA'):
        return Path(os.environ['TOUCHSTREAM_DATA'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'TouchStream Hub'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'TouchStream Hub'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'touchstream-hub'


IN_DOCKER = os.path.exists('/.dockerenv')


def _recordings_folder():
    """Where finished broadcasts are filed, one folder each (see library.py)."""
    if os.environ.get('TOUCHSTREAM_RECORDINGS'):
        return Path(os.environ['TOUCHSTREAM_RECORDINGS'])
    if IN_DOCKER:
        return DATA_DIR / 'Recordings'
    videos = Path.home() / ('Movies' if sys.platform == 'darwin' else 'Videos')
    if videos.is_dir() or sys.platform in ('darwin', 'win32'):
        return videos / 'TouchStream'
    return DATA_DIR / 'Recordings'


DATA_DIR = _data_dir()
RECORDINGS_DIR = DATA_DIR / 'recordings'      # working files: streams being recorded, spoke uploads
RECORDINGS_FOLDER = _recordings_folder()      # default; the dashboard can change it
DB_PATH = DATA_DIR / 'hub.db'

HTTP_HOST = os.environ.get('TOUCHSTREAM_HOST', '0.0.0.0')
HTTP_PORT = int(os.environ.get('TOUCHSTREAM_PORT', 8080))
SRT_PORT = int(os.environ.get('TOUCHSTREAM_SRT_PORT', 8890))
RTMP_PORT = int(os.environ.get('TOUCHSTREAM_RTMP_PORT', 1935))
PUBLIC_HOST = os.environ.get('TOUCHSTREAM_PUBLIC_HOST', '')
ADMIN_PASSWORD = os.environ.get('TOUCHSTREAM_ADMIN_PASSWORD', '')
DISCOVERY = os.environ.get('TOUCHSTREAM_DISCOVERY', '1') != '0'

_exe = '.exe' if sys.platform == 'win32' else ''
# The installers put the programs the hub needs in its own bin folder, unless they're already installed
_installed = lambda name: str(DATA_DIR / 'bin' / f'{name}{_exe}')
MEDIAMTX_BIN = os.environ.get('TOUCHSTREAM_MEDIAMTX') or shutil.which('mediamtx') or _installed('mediamtx')
# The installer's FFmpeg first (Windows): one on the PATH may be another app's cut-down copy
FFMPEG_BIN = (os.environ.get('TOUCHSTREAM_FFMPEG')
              or (_installed('ffmpeg') if os.path.exists(_installed('ffmpeg')) else None)
              or shutil.which('ffmpeg') or 'ffmpeg')
# ffprobe ships with FFmpeg; look beside it first
_beside = os.path.join(os.path.dirname(FFMPEG_BIN), f'ffprobe{_exe}')
FFPROBE_BIN = (os.environ.get('TOUCHSTREAM_FFPROBE') or (_beside if os.path.exists(_beside) else None)
               or shutil.which('ffprobe') or 'ffprobe')

# MediaMTX's own ports; loopback only, the hub fronts everything
MTX_API_PORT = 9997
MTX_RTSP_PORT = 8554
MTX_HLS_PORT = 8888

# Spoke discovery beacons (UDP broadcast) and the spoke's local API
BEACON_PORT = 9999
SPOKE_API_PORT = 6077
