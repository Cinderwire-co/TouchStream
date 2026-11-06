# The TouchStream spoke

The spoke runs on an off-the-shelf HDMI capture box, the **Luckfox PicoKVM** (RV1106, 256 MB, hardware H.264, a
1.54" touchscreen, Ethernet). There's nothing to assemble or solder: plug in HDMI, Ethernet and power, and the hub
adopts it. It's a single static Go binary, small enough for the box. See [docs/spoke.md](../docs/spoke.md) for using it.

| | Status |
|---|---|
| Hub link: pairing, adoption, heartbeat, frame rates it can capture | Working |
| SRT stream to the hub (live and preview) | Working |
| Its own recordings, uploaded to the hub | Working |
| The screen: status, Recordings, Go live / End, Record | Working in the emulator |
| Recordings on the screen, a QR code each, the phone page to watch, trim and save | Working in the emulator |
| HDMI capture and the hardware encoder (1080p at up to 60 fps) | Waiting for the box |
| HDMI sound | Waiting for the box (the driver plays it 1.29× fast) |
| ffmpeg on the box, for the phone copies and trims | Waiting for the box |
| The screen on the real panel and its touch | Waiting for the box |
| Straight to YouTube: with no hub (stream keys from the setup page), or when the hub is down | Working in the emulator |
| The setup page (QR code on the screen): stream keys, resolution, frame rate, quality, name, hub | Working in the emulator |
| Paired from its first start by the installer's settings on the card | Working in the emulator |

## The emulator

Nothing emulates the RV1106 itself, so the emulator runs the real ARM binary in an ARMv7 Linux
container held to the box's limits (one core, 256 MB). A ready-made H.264 + AAC clip, replayed in
real time, stands in for the HDMI input and the hardware encoder; on the box the encoder costs
almost no CPU, so the rest of the spoke sees the same load.

You need Go and Docker Desktop.

```bash
tools/dev emulate                      # build and start it (makes the test clip the first time)
tools/dev hubless                      # a second one beside it, with no hub, on port 6078
tools/dev pair host.docker.internal:8091 CODE  # pair it with a hub on this computer (CODE from Add spoke)
tools/dev logs                         # follow its log
# (host.docker.internal is this computer as the container sees it: it keeps working when its IP changes)
tools/dev test                         # run the tests
tools/dev stop
```

`tools/dev hubless` runs a second emulator beside it with no hub, on port 6078, to try setup and going live
straight to YouTube.

Its screen is at **http://localhost:6077/screen**, or at your computer's address from a phone on
the same network. Tap it as you would the box: Record, and the middle of the home screen for the
recordings (drag to scroll, tap one for its QR code). The page also shows the link the QR code opens,
pulls the HDMI cable out and plugs it back in, and links to the hub dashboard, where you can take it
live or rename it and watch the screen follow.

The emulator keeps its settings and recordings in `.emulator/`, like the card in the box.
