"""Run the hub: `touchstream-hub [--port N]` or `python -m touchstream_hub [--port N]`"""
import argparse
import sys

import uvicorn

from . import desktop, settings


def main():
    parser = argparse.ArgumentParser(prog='touchstream-hub', description='TouchStream Hub')
    parser.add_argument('--port', type=int, help=f'dashboard port (default {settings.HTTP_PORT}, or TOUCHSTREAM_PORT)')
    args = parser.parse_args()
    if args.port:
        settings.HTTP_PORT = args.port
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    desktop.run_windowless()
    # Line-buffered so logs show up promptly in Docker, Coolify and log files
    sys.stdout.reconfigure(line_buffering=True)
    desktop.keep_awake()
    # Trust X-Forwarded-* so cookies and links are right behind Coolify/Traefik or any proxy
    uvicorn.run('touchstream_hub.app:app', host=settings.HTTP_HOST, port=settings.HTTP_PORT,
                proxy_headers=True, forwarded_allow_ips='*', log_level='warning')


if __name__ == '__main__':
    main()
