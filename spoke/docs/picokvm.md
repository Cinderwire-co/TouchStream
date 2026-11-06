# The Luckfox PicoKVM, inside

What's in the box, what its own software does, and how the spoke will use it. From Luckfox's published
sources ([luckfox-eng29](https://github.com/luckfox-eng29): `kvm`, `kvm_video`, `kvm_display`, `kvm_system`) and the
device tree in its system update (Sys 0.1.8). All of it still to be checked on the real box.

## The hardware, as Linux sees it

| Part | Device | Notes |
|---|---|---|
| HDMI in: Toshiba TC358743 (I2C 0x0f, 4 CSI-2 lanes) | `/dev/video0`, `/dev/v4l-subdev2` | Frames as YUYV into DMA buffers; the subdevice has the timings, source-change events and the EDID |
| HDMI sound: the TC358743's I2S into the RV1106's (capture only) | ALSA card `rockchip,hdmiin` | The TC358743 drives the bit and frame clocks |
| H.264/H.265 encoder | Rockchip's MPI (`librockit.so` in `/oem/usr/lib`) | 1080p60 is within its limit |
| Screen: Sitronix ST7789V, 240×240 | `/dev/fb0`, 16-bit | Luckfox's UI turns it 180° in software |
| Touch: Hynitron CST816S | `/dev/input/event0` | |
| eMMC, microSD | `/dev/mmcblk0`, `/dev/mmcblk1` | `root=` in `/proc/cmdline` says which it booted from |

Also on board: Ethernet (`eth0`, 10/100), ffmpeg 4.4 (with RTMP and RTMPS), `arecord`, `v4l2-ctl`, `media-ctl`,
`evtest` and an SSH server.

## Its own software

- Buildroot, Linux 5.10.160, with A/B partitions: `env`, `idblock`, `uboot_a`/`_b`, `misc`, `security`,
  `boot_a`/`_b`, `system_a`/`_b`, `userdata` (`p1` to `p11` on the card).
- `/etc/init.d/S21appinit` runs `/oem/usr/bin/RkLunch.sh`, which starts the apps in `/userdata/picokvm/bin`:
  - `kvm_app`, in Go, a fork of JetKVM (GPL-2.0): the web page and WebRTC. The others connect to it over sockets.
  - `kvm_video`, in C (GPL-2.0): HDMI capture into the hardware encoder, then one encoded frame per packet on
    `/var/run/kvm_video.sock`, with JSON controls on `/var/run/kvm_ctrl.sock` (start, stop, quality, codec, EDID,
    snapshots).
  - `kvm_audio` (no source published): Opus in RTP, 20 ms at 48 kHz, on `/var/run/kvm_audio.sock`.
  - `kvm_display`, in C with LVGL (GPL-2.0): the screen.
- Updates go through Rockchip's `rk_ota`, with `update_system.tar` (`uboot.img`, `boot.img`, `system.img`).

## What the spoke does with it

The spoke takes the place of all four apps: one Go program, plus a small capture helper.

- **Video**: `kvm_video` won't do as it is: it's made for remote desktops, so its bitrate tops out at 2 Mbps for
  1080p, its keyframes are every 60 frames, and its frames carry no timestamps. Instead, `touchstream-capture`
  (`spoke/capture/`), a helper of our own in C (MIT) against Rockchip's MPI: V4L2 capture from `/dev/video0` into
  DMA buffers (from the 66 MB kept for the video hardware), then H.264 from the hardware encoder at a constant
  bitrate, Main profile with no B-frames, a keyframe every 2 seconds with its SPS and PPS, on its stdout. On its
  stderr, a JSON line each time what's coming in changes. It gives the source an EDID asking for 1080p at the hub's
  frame rate, with 2-channel PCM sound, as an HDMI sink (`edid-decode` passes all five rates). `kvm_video` showed
  the devices and the sequence of calls; its GPL code isn't copied.
- **Sound, and the stream**: for each signal, the box's ffmpeg takes the helper's H.264 and the sound from the
  `hdmiin` card (ALSA: `hw:CARD=rockchiphdmiin`, to check on the box) as AAC, and makes the MPEG-TS the rest of the
  spoke uses (`media.Capture`). Its clocks carry on from the last one's, so the stream never runs backwards when the
  cable comes and goes. (`kvm_audio`'s Opus would need converting for YouTube anyway, and its source isn't
  published.) In the emulator, `SOURCE=hdmi` runs all of this with a stand-in helper playing the clip's H.264.
- **Screen**: drawn as in the emulator, then written to `/dev/fb0` (in its own layout, asked of it: RGB565), turned
  180°, with the backlight at Luckfox's level; touches from `/dev/input/event0`, turned the same way
  (`spoke/internal/panel`).
- **ffmpeg** on the box makes the phone copies and trims, and the direct stream to YouTube.

## The image

- Luckfox's microSD image (`tools/dev firmware` unpacks it into `.dev/picokvm/`) has no MBR or GPT: the partitions
  are in the U-Boot environment at its start (`env`, 32K, CRC-checked), as
  `blkdevparts=mmcblk1:32K(env),512K@32K(idblock),512K(uboot_a),512K(uboot_b),256K(misc),1M(security),32M(boot_a),32M(boot_b),640M(system_a),640M(system_b),-(userdata)`,
  with `root=/dev/mmcblk1p9` (`system_a`). The idblock (`RKNS`) is where the boot ROM looks, at 32K.
- So the spoke's image is made from theirs, with no SDK build (`tools/dev image`, with
  `tools/picokvm_image.py`): the spoke goes in both system partitions, and Luckfox's launcher
  (`/oem/usr/bin/RkLunch.sh`, once the drivers and network are up) starts `/usr/bin/touchstream-start` in place of
  their apps. That grows the card's last partition's filesystem to fill the card, once, and keeps the spoke running,
  logging to syslog. Until the capture helper is built, the spoke starts with no signal, so it still pairs and shows
  up. Checked in the emulator: the other partitions are byte for byte Luckfox's, and the spoke pairs itself from a
  card the installer wrote. Later, built with
  [Luckfox's SDK](https://github.com/LuckfoxTECH/luckfox-pico) once the PicoKVM's board config is published.
- The installer's settings block goes in a gap, not a partition of its own: the partitions are numbered, and
  Luckfox's scripts use the numbers (`mmcblk1p9`), so a new one would move the rest. `32M@2944K(boot_a)` leaves 96K
  free after `security`, and the block goes at 2880K (a 64 KiB boundary, in the first 64 MiB, where the installer
  looks); the partitions after it move down 96K. It starts `TOUCHSTREAM-PROVISION-V1` and a newline; the spoke reads
  it from `/dev/mmcblk1`.
- Licenses: Rockchip's media libraries are proprietary, and shipped as part of the SDK; the GPL parts (the kernel,
  BusyBox) need their sources offered, as a link to the SDK revision the image is built from.

## Checked in the emulator, on the box's own system

The emulator runs on the PicoKVM's root filesystem (see the spoke's README), so these are checked against the
box's own ffmpeg 4.4:

- Straight to YouTube (the tee muxer to RTMP), phone copies and clips, and AAC from PCM at 128 kbps.
- Two things 4.4 does differently, now handled: it gives the tee muxer no `total_size` in its progress (the spoke
  watches its output clock instead), and it can't start part way through a stream (each ffmpeg the spoke starts
  is fed from a keyframe, with the stream's tables first).
- Memory, with everything at once (live straight to an RTMP server, recording, a phone copy and a clip): under
  70 MB in all, under emulation (which adds to it), of the 170 MB or so the box has for programs (its 256 MB,
  less 66 MB kept for the video hardware, `rk_dma_heap_cma=66M`).
- The spoke's image: its start script runs the spoke, which pairs itself from a card the installer wrote.
- The way in for HDMI, with a stand-in for the capture helper: a recording has the picture and the sound, and its
  clocks only go forward when the cable's pulled and put back.
- The screen and touch drivers, with a file for the framebuffer (it matches the screen page) and a pipe for the touch
  (taps in the panel's own layout, turned round, open the recordings).

## On the box, in order

1. **Does it boot from the card?** Its eMMC comes with a factory test image. Write Luckfox's microSD image
   (`Luckfox-PicoKVM-Lite_MicroSD_Sys0.1.8_App0.1.4.img`, from the
   [Google Drive folder](https://drive.google.com/drive/folders/1c_1EdF3fCocWYCSY2KlshBpRdEznSKzy) on their
   [flashing page](https://wiki.luckfox.com/Luckfox-PicoKVM/Flash-image/): Drive refuses scripted downloads, so
   use a browser) to a card, boot it, and check `/proc/cmdline` for `root=/dev/mmcblk1`. If it won't, the installer
   writes the eMMC over USB instead (hold BOOT while plugging in).
2. **HDMI sound**: `arecord -l`, then 10 seconds of a known tone from a computer
   (`arecord -D hw:N -f S16_LE -r 48000 -c 2 tone.wav`): check its pitch and length. If it's fast, compare the rate
   ALSA was asked for with the TC358743's audio settings, as it drives the clocks.
3. **Capture**: `v4l2-ctl -d /dev/v4l-subdev2 --query-dv-timings` at 1080p30 and 1080p60, and `media-ctl -p`.
4. **The encoder**: `touchstream-capture` (built by `spoke/capture/build`, in the image) at 1080p60 and 8 Mbps;
   whether encoding a frame at a time keeps up at 60; CPU with the spoke running. It compiles with Luckfox's
   toolchain and loads against the box's own libraries, which is as far as it goes without the box.
5. **Screen and touch**: the spoke's screen on the panel, the right way up, and `evtest /dev/input/event0` at each
   corner (that the touch is turned as the picture is).
6. **ffmpeg**: what AAC at 128 kbps costs in CPU; RTMPS to YouTube.
7. **The image**: build it, write it with the installer, and check it pairs itself at its first start.
