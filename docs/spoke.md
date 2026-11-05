# Spokes

A spoke sits next to your camera or video switcher. It takes the HDMI output, shows the picture on its touchscreen,
and sends the video to the hub. It also records its own copy while you're live.

## What you need

- **Raspberry Pi 4 Model B** (2 GB or more), its official USB-C power supply, and a case with a fan
- **HDMI-to-CSI capture board** built on the Toshiba TC358743 chip (for example Geekworm C779 or Auvidea B101), on the
  Pi's camera connector
- **3.5" SPI touchscreen**, MHS35 type (480 × 320, ILI9486 with XPT2046 touch), on the Pi's GPIO header
- **microSD card**, 64 GB or more: the spoke's own recordings live on it
- An Ethernet cable if you can. Wi-Fi works, but a wired connection is steadier for live video.

A full parts list with links is coming before the first release.

The source should send **1080p** at 24, 25, 30 (29.97 is fine) or 50 frames per second. 1080p60 is too fast for the
Pi 4's capture link.

## Install

1. With [Raspberry Pi Imager](https://www.raspberrypi.com/software/), write **Raspberry Pi OS Lite (64-bit)** to the
   card. In its settings:
   - give the spoke a name, such as `main-hall`;
   - set a username and password;
   - add your Wi-Fi (unless you're using Ethernet);
   - turn on SSH.
2. Fit the capture board and the screen, put the card in, and power it on.
3. From another computer, connect to it (`ssh yourname@main-hall.local`) and run:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/spoke/install.sh | sudo bash
   ```

   It takes about 10 minutes, then the spoke restarts into TouchStream: the boot screen, then the picture from HDMI.

4. **Pair it with your hub.**
   - If the spoke and the hub are on the same network: in the dashboard, choose **Add spoke** and **Adopt** it.
   - Otherwise: **Add spoke** shows a code. On the spoke's screen, tap anywhere, then **Hub**, and enter the hub's
     address and the code. The address is under the network icon at the top of the dashboard.

## The touchscreen

The screen shows the live picture with the audio levels, whether the spoke is live, its network, temperature and
CPU. With no HDMI signal it shows a "Waiting for source" screen. While you're live, viewers see that screen too, so
they never get a frozen picture.

The round **Record** button at the bottom right records on the spoke, with or without a hub: for a talk, a rehearsal
or anything you didn't schedule. It shows while there's a picture. The top bar counts the time while it records. To
stop, tap it twice, so a stray touch never ends a recording.

Tap anywhere else for the menu:

- **Wi-Fi**: pick a network and type its password.
- **Hub**: the hub it's paired with, or pair it with one.
- **Recordings**: what's on the card. Tap one for a QR code, and scan it with a phone on the same network (see
  below).
- **Test pattern**: color bars with a running clock, to test the stream without a source.
- **Reboot**.

## A screen that shows the source

To work a computer or switcher with a second screen on it, put an **HDMI splitter** (1 in, 2 out) in front of the
spoke: one output to the spoke, the other to the screen. The screen gets the source directly, with no delay and at
its full refresh rate. Pick a splitter that sends the spoke's EDID (or lets you choose 1080p), so the source sends a
picture the spoke can capture.

Don't use the Pi's own micro-HDMI ports for this. Everything there has been through the capture first, so it's a
frame or two behind, and it's too slow to work a mouse by.

## Sound

The spoke takes the sound that comes over HDMI with the picture. If the source sends its sound elsewhere (or sends
none), the stream carries silence and the picture is unaffected. The 3.5 mm jack is output only, so it can't take a
microphone or a mixer feed.

The ribbon cable carries only the picture. The sound leaves the capture board on a row of pins (often bare holes to
solder) and takes four wires to the Pi's GPIO header:

| Capture board | Pi pin    | (GPIO)            |
|---------------|-----------|-------------------|
| SCK           | 12        | GPIO18, PCM clock |
| WFS           | 35        | GPIO19, PCM frame |
| SD            | 38        | GPIO20, PCM data  |
| GND           | 39        | ground            |

Leave the board's other pins (3V3, RST, OSCK, IR, INT) unconnected. The touchscreen covers pins 1 to 26, pin 12
included, so solder that wire to pin 12 on the underside of the Pi, or fit a GPIO splitter board between the Pi and
the screen. Pins 35, 38 and 39 stay uncovered.

## Upside down

Mounted the other way up? **Settings › Flip screen** (tap it twice) turns the screen and its touch input around, and
the spoke restarts to do it. The Pi has no orientation sensor, so it's a setting rather than automatic.

## Its own recordings

With **Keep a copy on the spoke** on (in the spoke's settings in the dashboard), the spoke records while it's live. It
uploads each part to the hub as it goes, so little is left to send at the end. If the internet drops, the spoke's copy
has everything, and it catches up once the connection is back.

When the card fills, the spoke deletes its oldest recordings: ones the hub already has first, and never the one being
recorded. The Home page shows each spoke's free space and any recordings the hub doesn't have yet.

Recordings made with the Record button upload to the hub too, when the spoke is paired with one.

## Recordings on your phone

Scanning a recording's QR code opens a page on the phone, served by the spoke itself, so it works on the venue's
network with no internet:

- watch the recording;
- trim it: drag the two handles, or play to the spot and tap **Start here** or **End here**;
- **Download clip** saves just that part; **Download the whole recording** saves everything.

The spoke first makes a phone-friendly copy, which takes about a minute per hour of video; the QR code appears once
it's ready. A clip is copied out without re-encoding, so it's quick and keeps the full quality. It can start up to 2
seconds before the start you pick, at the nearest keyframe. For fades, cuts in the middle or YouTube, use the hub's
editor. The link stops working 30 minutes after it was last used.

## When the hub is down

Every spoke keeps a week of its own schedule. If it can't reach the hub at a broadcast's start time, it goes live
straight to YouTube by itself, and records as usual. When the hub is back, it takes over again and collects the
recording.

## Settings and updates

In the dashboard, open a spoke, then **Settings**:
- name and location;
- frame rate and quality;
- whether it keeps its own copy;
- **Restart**, and **Update software**, which fetches the latest TouchStream and restarts.

To change several spokes at once, use **Settings for all** on the Spokes page. You can also update a spoke by running
the install command on it again.
