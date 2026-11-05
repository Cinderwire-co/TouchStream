# TouchStream Lite

The spoke for small, off-the-shelf HDMI capture boxes, starting with the **Luckfox PicoKVM**
(RV1106, 256 MB, hardware H.264, 1.54" touchscreen, Ethernet). There's nothing to assemble or
solder: plug HDMI and Ethernet in, and the hub adopts it like any other spoke.

It speaks the same protocol to the hub as the Pi spoke in [`../spoke`](../spoke): pairing, the
heartbeat, an SRT stream, and recordings uploaded in parts. So the hub, its dashboard, the editor
and shorts all work with it unchanged. It's a single static Go binary, small enough for the box.

| | Status |
|---|---|
| Hub link: pairing, adoption, heartbeat | Working |
| SRT stream to the hub (live and preview) | Working |
| Its own recordings, uploaded to the hub | Working |
| The 1.54" screen: status and Record | Working in the emulator |
| Recordings on the screen, a QR code each, the phone page to watch, trim and save | Working in the emulator |
| HDMI capture and the hardware encoder (1080p at up to 60 fps) | Waiting for the box |
| HDMI sound | Waiting for the box (the driver plays it 1.29× fast) |
| ffmpeg on the box, for the phone copies and trims | Waiting for the box |
| The screen on the real panel and its touch | Waiting for the box |

## The emulator

Nothing emulates the RV1106 itself, so the emulator runs the real ARM binary in an ARMv7 Linux
container held to the box's limits (one core, 256 MB). A ready-made H.264 + AAC clip, replayed in
real time, stands in for the HDMI input and the hardware encoder; on the box the encoder costs
almost no CPU, so the rest of the spoke sees the same load.

You need Go and Docker Desktop.

```bash
tools/dev emulate                      # build and start it (makes the test clip the first time)
tools/dev pair 192.168.1.20:8091 CODE  # pair it with a hub: CODE from the dashboard's Add spoke
tools/dev logs                         # follow its log
tools/dev test                         # run the tests
tools/dev stop
```

Its screen is at **http://localhost:6077/screen**, or at your computer's address from a phone on
the same network. Tap it as you would the box: Record, and the middle of the home screen for the
recordings (drag to scroll, tap one for its QR code). The page also shows the link the QR code opens,
pulls the HDMI cable out and plugs it back in, and links to the hub dashboard, where you can take it
live or rename it and watch the screen follow.

The emulator keeps its settings and recordings in `.emulator/`, like the card in the box.
