"""Device configuration, persisted at ~/stream-config.json"""
import json
import os
import platform
import threading
from pathlib import Path

CONFIG_PATH = str(Path.home() / 'stream-config.json')

CAPTURE_WIDTH = 1920
CAPTURE_HEIGHT = 1080

DEFAULT_CONFIG = {
    'device_name': platform.node(),
    'location': '',
    'framerate': '30',   # 24 | 25 | 30 | 50, set from the hub; sources are asked for it via the EDID
    # Set by pairing (touchscreen) or adoption (hub on the LAN)
    'hub_url': None,
    'spoke_id': None,
    'hub_token': None,
}

_lock = threading.Lock()


def ensure_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG.copy())


def load_config():
    ensure_config()
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
    except Exception:
        cfg = DEFAULT_CONFIG.copy()
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    return cfg


def save_config(cfg):
    # Write-then-rename so a crash or power cut never leaves a half-written file
    with _lock:
        try:
            tmp = CONFIG_PATH + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except Exception as e:
            print("Failed to save config:", e)
