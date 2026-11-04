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

Tap anywhere for the menu:

- **Wi-Fi**: pick a network and type its password.
- **Hub**: the hub it's paired with, or pair it with one.
- **Recordings**: what's on the card. Tap one for a QR code: scan it with a phone on the same network to download it.
  The spoke first makes a phone-friendly copy, which takes about a minute per hour of video.
- **Test pattern**: colour bars with a running clock, to test the stream without a source.
- **Reboot**.

## Its own recordings

With **Keep a copy on the spoke** on (in the spoke's settings in the dashboard), the spoke records while it's live. It
uploads each part to the hub as it goes, so little is left to send at the end. If the internet drops, the spoke's copy
has everything, and it catches up once the connection is back.

When the card fills, the spoke deletes its oldest recordings: ones the hub already has first, and never the one being
recorded. The Home page shows each spoke's free space and any recordings the hub doesn't have yet.

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
