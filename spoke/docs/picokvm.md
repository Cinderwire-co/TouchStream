# The Luckfox PicoKVM, inside

What's in the box, what its own software does, and how the spoke will use it. From Luckfox's published
sources ([luckfox-eng29](https://github.com/luckfox-eng29): `kvm`, `kvm_video`, `kvm_display`, `kvm_system`) and the
device tree in its system update (Sys 0.1.8), then checked on the real box (see the end).

## The hardware, as Linux sees it

| Part | Device | Notes |
|---|---|---|
| HDMI in: Toshiba TC358743 (I2C 0x0f, 4 CSI-2 lanes) | `/dev/video0`, `/dev/v4l-subdev2` | Frames as YUYV into DMA buffers; the subdevice has the timings, source-change events and the EDID |
| HDMI sound: the TC358743's I2S into the RV1106's (capture only) | ALSA card 0, the only one: `rockchiphdmiin` on Luckfox's microSD system (Sys 0.1.8), `tc358743` on the factory eMMC system | The TC358743 drives the bit and frame clocks, from its reference clock: the RV1106's `mclk_ref_mipi0` (see the image) |
| H.264/H.265 encoder | Rockchip's MPI (`librockit.so` in `/oem/usr/lib`) | 1080p60 is within its limit |
| Screen: Sitronix ST7789V, 240×240 | `/dev/fb0`, 16-bit | Luckfox's UI turns it 180° in software |
| Touch: Hynitron CST816S | `/dev/input/event0` | |
| eMMC (8 GB), microSD | `/dev/mmcblk0`, `/dev/mmcblk1` | It starts from a card with a system on it, even with Luckfox's on the eMMC; `storagemedia=` in `/proc/cmdline` says which |

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
- The box comes with a newer system on its eMMC than the microSD image (built February 2026): its sound card is
  named `tc358743`, its device tree gives the HDMI input 2 CSI-2 lanes (the microSD's has 4, which 1080p60 needs),
  and its root password is `luckfox` with SSH and telnet open.

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
  sound card (ALSA: `hw:0`, the box's only one) as AAC, and makes the MPEG-TS the rest of the
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
- The HDMI chip's reference clock goes to 27 MHz, in the device tree in both boot partitions. Luckfox's has the
  RV1106 make 26 MHz, and the TC358743's driver only sets the chip's audio clock up right for 27 MHz (anything
  else gets its 42 MHz setting): the sound came out 1.58 times too fast (a 1 kHz tone at 1,584 Hz, 10 seconds of
  it in 15.9). 27 MHz is 594 MHz divided by 22, and the video link then comes out at exactly the 594 Mbps a lane
  the device tree asks for (572 at 26 MHz). The boot partitions are FIT images whose parts are checked by
  SHA-256 but not signed, so the device tree's hash is updated with it.
- The screen is the spoke's from the start: `fbcon=map:1` on the kernel's command line (`sys_bootargs`) keeps the
  kernel's console, and its boot logo and cursor, off it, and `/etc/init.d/S00touchstream-splash` puts TouchStream's
  splash on it (`touchstream-spoke splash`) until the spoke's up. (The FIT's resource image has Rockchip's logos too,
  for a bigger display: the ST7789V never shows them.)
- **The installer's card puts the spoke on the box itself** (`spoke/internal/install`). Started from the card, the
  start script runs `touchstream-spoke install`: the card's partitions go onto the eMMC at the same places, each
  checked (the eMMC's own idblock and U-Boot stay: the card's are from Luckfox's image for the Lite, which has no
  eMMC); the eMMC's data partition is made afresh with the spoke's settings (its pairing); then the eMMC's
  environment (`mmcblk0`), last. The card's idblock is replaced with `TOUCHSTREAM-INSTALLED`, and the box starts
  again, from the eMMC, whose start script makes that card one for recordings (`touchstream-spoke card`: one exFAT
  partition from 16 MiB, "TouchStream") and mounts it over `recordings`. Other cards are left as they are (one
  that's exFAT or FAT already is used as it is). `install -keep-card` leaves the card able to start the box, to try
  the eMMC with it out; `/userdata/touchstream/stay-on-card` on a card keeps it running from the card.
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

## Checked on the box (2026-11-07)

1. **It starts from the card**, with Luckfox's factory system still on its eMMC (`storagemedia=sd`,
   `root=/dev/mmcblk1p9`), written by the installer, and pairs itself with the hub at its first start.
2. **HDMI sound**: from a Mac, 1 kHz on the left and 1.5 kHz on the right, recorded with
   `arecord -D hw:0 -f S16_LE -r 48000 -c 2`: at 26 MHz, 1,584 and 2,376 Hz, and 10 seconds took 15.9; at 27 MHz
   (the image), 1,000.0 and 1,500.0 Hz, in 10.05 seconds, left and right the right way round.
3. **Capture** at 1080p60 from a Mac: `touchstream-capture` asks the driver how big its buffers must be
   (`VIDIOC_QUERYBUF`: more than the picture; a buffer of just the picture is refused, "Invalid argument"), and
   keeps its own copy of stdout for the H.264, as Rockchip's library prints notes on stdout (they go to stderr).
4. **The encoder** keeps up at 1080p60: 593 frames in 10 seconds, Main profile, no B-frames, with the helper at 3%
   of the CPU.
5. **CPU**, with 1080p60 coming in and the stream going to the hub: the helper 3%, ffmpeg (AAC, and the MPEG-TS)
   27%, the spoke 17%, about 55% in all. The spoke drew its screen 20 times a second, at 71% of the CPU; now each
   shape is rasterized over only its own box, letters are kept once shaped, the panel's pixels are packed row by
   row, and it draws 4 times a second while nothing on it moves (20 while something does, and at once on a touch).
   (Found with Go's profiler: `go build -tags pprof` serves it on port 6060.)
6. **Installed on the eMMC** from the card (`install -keep-card`, 2026-11-07): the eMMC's environment and data
   partition checked; starting from it with the card out, next. Luckfox's factory eMMC is backed up in
   `.dev/backup/picokvm-emmc-factory` (its first 1347 MiB, MD5-checked against the box, and its data partition's
   files).
7. **Still to check**: touch at each corner, RTMPS straight to YouTube, the HDMI input's 1080p30 and other rates,
   and memory with everything running.
