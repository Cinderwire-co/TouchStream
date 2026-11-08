# The hub

The hub runs your schedule, puts spokes live, sends their video on to YouTube, records everything and serves the
dashboard. Run it on a computer that stays on: a Mac or PC in the building, a small Linux box, or a server.

**Pick the computer:**
- It should stay on and awake. On a Mac or PC the hub keeps it from sleeping on its own, but closing a laptop's lid
  still sleeps it.
- It needs enough **upload** speed: about 1.5 times the bitrate of every spoke that streams at once (8 Mbps each by
  default). The Home page measures it.
- It needs room for recordings: about 3.6 GB per hour at 8 Mbps. Settings > Old recordings can clear space
  automatically.

## On a Mac, Windows or Linux computer

Run one command. It installs everything the hub needs, starts it now and every time the computer starts, and opens
the dashboard. Nothing else is needed first, Docker included. Run it again any time to update; your settings and
recordings stay.

**Mac** (macOS 12 or later), in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/macos.sh | bash
```

**Windows 10 or 11**, in PowerShell (not as administrator):

```powershell
irm https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/windows.ps1 | iex
```

Windows asks once for permission to let spokes through the firewall. That works even on a network Windows calls
**Public**, which it often does for home and church Wi-Fi. On a Public network, only devices on the same local
network can reach the hub.

**Linux** (Ubuntu, Debian, Fedora, Arch and others with systemd):

```bash
curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/linux.sh | bash
```

It may ask for your password, to install FFmpeg and open the firewall if ufw or firewalld is on. The hub starts with
the computer, before anyone signs in.

**What gets installed:**
- The hub, with its own Python. It uses [uv](https://docs.astral.sh/uv/), so nothing system-wide changes.
- [MediaMTX](https://github.com/bluenviron/mediamtx), the video server spokes send to, checked against its
  published checksum.
- FFmpeg, unless you already have it: from Homebrew on a Mac, the [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
  build on Windows, and your package manager on Linux.

The dashboard is at `http://localhost:8080`, or the next free port if something else has 8080 (the installer tells
you). There's a **TouchStream Hub** shortcut in Applications, the Start menu or your app menu.

|                  | Mac | Windows | Linux |
|---|---|---|---|
| Recordings (default) | `~/Movies/TouchStream` | `Videos\TouchStream` | `~/Videos/TouchStream` |
| Settings and working files | `~/Library/Application Support/TouchStream Hub` | `%LOCALAPPDATA%\TouchStream Hub` | `~/.local/share/touchstream-hub` |
| Log | `~/Library/Logs/TouchStream Hub.log` | `%LOCALAPPDATA%\TouchStream Hub\hub.log` | `journalctl --user -u touchstream-hub` |
| Runs as | a login item | a sign-in task, "TouchStream Hub" | the `touchstream-hub` user service |

Change the recordings folder in Settings. On the hub's own computer there's a folder picker and a **Show in Finder**
(or File Explorer) button.

**To remove the hub** (your settings and recordings stay):

```bash
curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/macos.sh | bash -s -- --uninstall
curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/linux.sh | bash -s -- --uninstall
```

```powershell
$env:TOUCHSTREAM_UNINSTALL = 1; irm https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/windows.ps1 | iex
```

## On a server

### Coolify

1. New resource > **Public repository** `https://github.com/Cinderwire-co/TouchStream`, build pack **Docker Compose**.
2. Base directory `/hub`, Docker Compose location `/docker-compose.coolify.yml`.
3. Give the hub a domain. Coolify adds HTTPS and generates the admin password: find it under the app's Environment
   Variables as `SERVICE_PASSWORD_ADMIN`.
4. Spokes send video to the same domain on **UDP 8890**:
   - Open UDP 8890 in the server's firewall.
   - The domain's DNS must point straight at the server. A proxied (orange-cloud) Cloudflare record won't carry
     video.

A server isn't on the spokes' network, so pair spokes with a code (Add spoke) rather than finding them.

### Docker

```bash
git clone https://github.com/Cinderwire-co/TouchStream && cd TouchStream/hub
docker compose up -d
```

Then open `http://<server>:8080`. Open UDP 8890 for spokes. Recordings are kept in the `touchstream-data` volume.
To see them as files, mount a folder at `/data/Recordings` (see `docker-compose.yml`). On Linux,
`network_mode: host` also lets the hub find spokes on its network.

To update: `git pull && docker compose up -d --build`, or with the published image, pull
`ghcr.io/cinderwire-co/touchstream-hub:latest` and start it again (on Coolify, choose **Redeploy**).

## Updates

The hub looks for a new TouchStream release on GitHub every few hours. **Settings > General > Updates** shows the
version you have, what's new, and each spoke's version.

- **Install updates automatically** (on to begin with): the hub updates itself once nothing is live, due to go live
  within the hour, or being worked on (a render, an upload to YouTube, a transcript). It runs its own install
  script from the new release, the same as running it again by hand: settings and recordings stay, and the
  dashboard reloads itself when it's back, a minute later. Then each spoke updates as soon as it's idle (see
  [the spoke guide](spoke.md#updates)).
- **Off**: a bar at the top of every page says when an update is ready. **Update now** installs it, straight away
  or as soon as nothing is going on; **Later** hides it until the next one. Spokes wait for **Update** in Settings.
- **In Docker or Coolify**, the hub can't replace itself: it tells you when there's a new image to pull. Its spokes
  still update themselves.
- `TOUCHSTREAM_UPDATES=off` turns all of it off.

## Ports

| Port | What for |
|---|---|
| 8080/tcp (or the one the installer chose) | The dashboard, and spokes checking in |
| 8890/udp | Video from spokes (SRT) |
| 1935/tcp | Video from other encoders: OBS, vMix, an ATEM (RTMP, optional) |
| 9999/udp | Finding spokes on the local network |

## Settings for the technically minded

Each of these is an environment variable. On a Mac, set them in `~/Library/LaunchAgents/co.cinderwire.touchstream-hub.plist`. On
Linux, use `systemctl --user edit touchstream-hub`. In Docker, put them in the compose file.

| Variable | Does |
|---|---|
| `TOUCHSTREAM_PORT` | Dashboard port (8080); also `touchstream-hub --port N` |
| `TOUCHSTREAM_PUBLIC_HOST` | The address spokes send video to, when it isn't the one they reach the dashboard at (also in Settings) |
| `TOUCHSTREAM_ADMIN_PASSWORD` | Set the password here instead of on the first visit |
| `TOUCHSTREAM_RECORDINGS` | The recordings folder (also in Settings) |
| `TOUCHSTREAM_DATA` | Where the database and working files live |
| `TOUCHSTREAM_SRT_PORT`, `TOUCHSTREAM_RTMP_PORT` | Video ports (8890, 1935) |
| `TOUCHSTREAM_MTX_API_PORT`, `TOUCHSTREAM_MTX_RTSP_PORT`, `TOUCHSTREAM_MTX_HLS_PORT` | MediaMTX's own ports, on this computer only (9997, 8554, 8888): change them and the ones above to run a second hub on the same computer |
| `TOUCHSTREAM_DISCOVERY` | `0` stops looking for spokes on the network |
| `TOUCHSTREAM_KEEP_AWAKE` | `0` lets the computer sleep while the hub runs |
| `TOUCHSTREAM_GOOGLE_CLIENT_ID`, `TOUCHSTREAM_GOOGLE_CLIENT_SECRET` | The YouTube OAuth client, instead of Settings |
| `TOUCHSTREAM_MEDIAMTX`, `TOUCHSTREAM_FFMPEG`, `TOUCHSTREAM_FFPROBE` | Paths to those programs |
