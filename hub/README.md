# TouchStream Hub

The hub runs your schedule, puts spokes live, sends their video on to YouTube, records every broadcast and serves
the dashboard. It's a small web app (FastAPI, with a Preact dashboard and no build step) plus
[MediaMTX](https://github.com/bluenviron/mediamtx), which spokes send video to, and FFmpeg.

Install it on a Mac, Windows or Linux computer with one command, or run it with Docker or Coolify on a server:
see [docs/hub.md](../docs/hub.md).

For development: `tools/dev up` from the top folder runs the hub from this checkout (on ports of its own, so it
runs beside an installed hub), a stand-in YouTube and two emulated spokes; `tools/dev restart hub` picks up a change.
Run `tests/check.sh` before committing: it runs the unit tests and checks that every dashboard module parses.
