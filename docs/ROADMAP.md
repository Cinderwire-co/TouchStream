# TouchStream roadmap

Working notes on what's planned, in rough order. Two rules hold for everything here:

- **Simple to deploy.** Anyone should get from download to streaming without a terminal.
- **Simple to use.** Add features without adding clutter: each thing has one home in the UI.

## Done recently

- Spoke Recordings menu: rolling delete when the card fills, and a QR code that downloads a recording to a phone. The spoke makes a regular MP4 first, because phones can't play a streamed one.
- Hub shows its real LAN address (not "localhost"). If the hub computer's IP changes, it tells its spokes the new address.
- Recordings folder: one folder per broadcast, named for people, with the details embedded in each MP4. There's a native folder picker, and a Show in Finder / File Explorer button when you're on the hub's own computer.
- Recurring broadcasts: pick the days of the week (at least one), every week, every other week or certain weeks of the month (1st-4th, last), and the months (none means every month), with an optional end date and the date added to each title. Change one date on its own or all upcoming; delete one date or end the series.
- Home page: spokes online, the next broadcast, each spoke's temperature, storage and recordings on its card, and internet speed tests for the hub and each spoke.
- The dashboard draws its own dropdowns, date and time pickers and dialogs (no browser ones). The hub's address is a small button in the top bar.
- One edit per broadcast, always cut from the original, with drafts. Leaving the editor (or a transcript) with unsaved changes asks first.
- Old recordings (Settings): delete broadcasts' recordings after N days and/or to keep the folder under N GB, oldest first. Edited videos, thumbnails and transcripts are never deleted, and record-only broadcasts are kept unless you allow it. It previews what would go and asks before deleting.
- Settings for all spokes (Spokes page): frame rate, quality and local copy on every spoke and on new ones; each spoke can still change its own.
- Hub installers for Mac, Windows and Linux, plus Docker and Coolify, and new docs (README, hub, spoke, YouTube,
  using it).
  - Mac: a real install took about 12 s.
  - Windows: tested on a real Windows 11 PC.
    - Fixes it needed: the account name over SSH, ImageMagick's cut-down FFmpeg on the PATH, Public Wi-Fi.
    - Firewall rules name the hub's own programs, so Windows never pops up asking (where Cancel would block
      spokes).
  - Linux: tested in a fresh Debian container (its systemd service not yet tested).
- API tokens (Settings > API access: read, control or admin), the API described at `/api/openapi.json`, and an
  MCP server at `/mcp`.
  - It has eleven tools, with the "how things ripple" brief as its instructions.
  - Tested end to end with the MCP SDK's client, including scheduling a repeating broadcast. See `docs/api.md`.
- The assistant: a chat button on every page, using OpenRouter (default model Claude Haiku 5.5).
  - It has the same tools and brief as MCP agents, plus which page you're on.
  - Deleting, publishing, going live and ending ask first.
  - Its key is offered at setup, with Skip; without one it's off and nothing is sent.
  - Tested with a scripted OpenRouter; not yet with a real key.
- Record on the spoke without a hub: a Record button on the touchscreen (two taps to stop). The QR code opens a page
  served by the spoke to watch, trim and download, with keyframe trims and no re-encode (tested on the dev Pi).
  Fixed on the way: a capture restart mid-recording wrote over the session's first part.
- The editor, as a linear editor: tracks for video, its linked sound (unlink for L-cuts), music and titles, plus a
  color adjustment layer.
  - Clips sit where they're put: deleting leaves a gap until it's closed; trims and moves happen in place.
  - A cut tool, transitions on cuts (FFmpeg xfade, centered on the cut, so timing never changes), fade handles, undo.
  - A media library (intros, slides, logos, music) and brands in Settings (an intro, an outro, a watermark; per
    channel).
  - The assistant's `edit_video` does all of it, and on the editor page leaves a draft that appears on the timeline.
  - Dashboard files load from versioned addresses, so an update always shows.
- Shorts (hub): pick a moment by its sentences, up to 3 minutes.
  - Framing follows the speaker (YuNet face detection on ONNX Runtime, linked into tracks, a camera-operator path
    with a dead zone, easing and cuts after cutaways), or shows the whole picture over a blur.
  - Five fixed caption styles drawn with Pillow (so any FFmpeg works) with the spoken word lit, and six accents.
  - Pauses become jump cuts; it publishes as a YouTube Short; MCP tools `make_short` and `publish_short`.
  - Transcripts now keep each word's timing.
- The TouchStream mark (`brand/touchstream_mark.py`): an ember play shape sending out arcs of signal, in the Cinderwire mark's chipped-shard style. It's on the dashboard, favicon, Mac app icon, boot splash, holding screen and touchscreen. "Made by Cinderwire" sits on the sign-in page and in Settings.

## Next

1. **Installers without a terminal**: a signed Mac .pkg and a Windows installer, plus a Linux systemd test on a real
   machine.
2. **Search across everything** (below).
3. **Fan control on the spoke** (needs the right fan; see "Parts list"):
   - Today the fan is wired to 5 V and runs flat out all the time; nothing can slow it.
   - With a fan that has a control wire (or a 2-wire fan plus a small transistor) on GPIO 12 or 13 (pins 32/33, clear of the screen's header), the installer adds the `pwm-gpio-fan` overlay. The fan then stays off when cool and steps up through four speeds as the CPU warms (about 55 °C to 75 °C).
   - The hub shows the fan's speed next to the temperature.
   - Not undervolting or overclocking: neither changes the fan speed. At about 15% CPU and 56 °C while streaming, the Pi has plenty of headroom. Video encoding runs on its hardware encoder, and the 1080p50 limit comes from the HDMI board's camera link, so a faster CPU wouldn't help.

## The agent API and the in-hub assistant

**One API for everything**: done (tokens, OpenAPI, MCP; see `docs/api.md`). Still to come:
- typed request bodies in the OpenAPI description, so tools that generate clients from it get every field;
- thumbnail upload through MCP;
- OAuth sign-in for the MCP endpoint, so Claude Desktop and claude.ai connectors can add the hub directly.

**The in-hub assistant**: done (OpenRouter). Still to come: streaming replies as they're written, and a local model as an option. The original plan:

- It works on whatever the page shows: "cut the first 10 minutes", "schedule this for Sunday 10 am on both channels".
- Uses OpenRouter, or any OpenAI-compatible endpoint, including a local one (Ollama, LM Studio). The provider is chosen in Settings.
- Gets lean tools, the same ones as the API, plus a short system brief of how the parts relate (below), so it doesn't have to rediscover the rules each time.

**How things ripple** (the brief the assistant gets, and the rules any client must keep):

- A broadcast owns its recordings, edits, transcript and thumbnail. Deleting it with "recordings too" deletes them all; without, the files stay in the recordings folder.
- Publishing an edit uploads a new YouTube video. It can make the original livestream private or unlisted. It never deletes it: deleting an aired broadcast on YouTube deletes the channel's video.
- A broadcast has one edit, always cut from an original copy (hub or spoke). Rendering again replaces the edited file. Publishing again uploads another new video (YouTube can't swap footage) and can make the earlier upload private. Captions for an edit are re-timed to its cuts.
- Unsaved work in the editor is kept as a draft on the hub; rendering clears it.
- A recurring series makes each date's broadcast (on YouTube too) 8 days ahead; later dates are only planned. Changing the series updates every upcoming broadcast not changed on its own. Changing one date detaches it from the series. Deleting one date skips it. Ending the series deletes its upcoming broadcasts; aired ones stay.
- Changing a title renames the broadcast's folder and files. The MP4s keep the details they were filed with.
- A scheduled broadcast that's edited updates YouTube. Deleting one that hasn't aired removes it from YouTube.
- Spokes keep a week of their schedule and stream straight to YouTube if the hub is down. The hub takes over again when it's back.

## Search across everything

- Each transcript gets a short summary and chapter list, written by the assistant's model when one is set up.
- Transcripts are split into passages and embedded. A local embedding model works offline; sqlite-vec keeps it all in the hub's one database.
- One search box across all broadcasts. Results jump to the moment in the recording, and the editor opens there.

## The editor: still to come

- Drag media from the library straight onto the timeline; volume for each sound clip; music that ducks under speech.
- A second video track for picture-in-picture and logos that come and go; keyframes for moves and zooms.
- Transitions previewed in the browser (today: dips show, dissolves and wipes show as a plain cut until rendered).
- Brands with their own title colors and font, and brands on shorts (watermark, end card).
- Export presets (1080p, 720p for smaller uploads, audio only for podcasts).

## The spoke: still to come

- A confidence monitor on the Pi's own HDMI port (the code is there behind `clean_feed` in the config, off by
  default). It's a frame or two behind the source, so it's for watching, not for working a mouse; for that, use an HDMI
  splitter in front of the spoke (see docs/spoke.md). Before it's switched on: a setting, and test it after a source
  switch, since the display output and the hardware converter must be let go of cleanly every time.
- Headphones on the 3.5 mm jack to hear what's being streamed (it's output only: no input).
- A USB audio adapter as the sound source, for a feed straight from the soundboard.
- HDMI sound without soldering. The TC358743 boards send sound over separate I2S pins, never the ribbon, and the Pi's
  clock pin (12) sits under the 3.5" screen. Options: a board with an audio plug (JST-GH 5-pin to Dupont) plus a DSI
  screen that leaves the GPIO free; or a USB 3.0 capture card with HDMI loop-out (sound over USB, zero-lag loop-out).
- An off-the-shelf spoke, one box with nothing to assemble: no screen or HDMI out needed, run from the hub and a phone
  page, at about $40. Candidates:
  - Luckfox PicoKVM (about $38), the pick: RV1106 camera chip with a hardware H.264 encoder, 1080p60 capture,
    microSD, Ethernet, open Buildroot SDK. Its TC358743 has I2S wired inside, but HDMI sound isn't finished in its
    driver (a user measured the pitch 1.29× too fast, which looks like an audio clock setting we can fix with the open
    SDK). With 256 MB, one core and 100 Mbps it needs a lean native spoke (Go or C).
  - Over budget: the Sipeed NanoKVM Pro (about $69–119; HDMI sound, loop-out, gigabit, 1 GB) and the GL.iNet Comet
    (about $80; HDMI sound works, vendor firmware).
  - Not these: the original NanoKVM (SG2002) and JetKVM have no HDMI sound.
  Whichever box, it speaks the same hub protocol: capture, encode, SRT out, segment recording, upload, and the phone
  status/trim page. A PicoKVM is on order. Started in `lite/` (Go): the hub link, the SRT uplink, recording and
  upload, the 240×240 screen, and recordings to phones (a list on the screen, a QR code each, the Pi's phone page to
  watch, trim and save) work in an emulator (an ARMv7 container held to one core and 256 MB, replaying a clip as the
  HDMI input). Still to do on the box: HDMI capture and the hardware encoder (it's sold as 1080p60, which the Pi
  can't do: follow the hub's frame rate setting), HDMI sound, the screen on the real panel and its touch, ffmpeg on
  the box for the phone copies, and its time zone (from the hub).
- Wi-Fi for the PicoKVM, which only has Ethernet. Plan on a pocket travel router as its Wi-Fi: over Ethernet (no
  software), or over one USB cable that powers the box too, with the box acting as a tethered phone (a USB network
  gadget, NCM, which Windows, macOS and Linux all take without drivers). The same USB network also lets the box
  borrow the network of the computer it's plugged into, and reach a hub running on it directly. On the box, also try
  switching its USB-C port to host for a Wi-Fi stick (it's also the power input, so it may not work).
- The PicoKVM's pin out (I2C, two serial ports, 3.3 V) can't carry network or video, but could drive a tally light or
  a physical Record button.

## Shorts: still to come

- An optional manual nudge for the window when there's no face to follow (a wide shot, someone facing away).
- Faces too small to find in a very wide shot: look again on tiles of a sharper frame.
- Body tracking as well as faces, for a speaker who turns away for a while.
- Shorts from the edited video, and suggested moments (the assistant picking quotable passages from the transcript).
- Upload straight to Instagram and TikTok (today: download, then post).

## Later

- Efficiency on the spoke: zero-copy capture to the encoder, scaling the preview in hardware, and an optional WebRTC preview with lower latency.
- A UI pass as features land: keep four top-level places (Home, Schedule, Spokes, Settings), and keep each feature inside the broadcast or spoke it belongs to.
- Series titles: the date is added in English ("November 8, 2026"); follow the hub's language once there's a setting for it.

## Launch (after testing and the build are done)

- **A promo video** of TouchStream and its parts:
  - a 3D animation of the spoke, modelled from its real parts (Pi, capture board, screen, case) and rendered in
    Blender, which can be scripted end to end;
  - what it does, with animations of the dashboard;
  - the assistant and AI agents running it, the API, and YouTube.
  - Start from the product-video templates in the ops repo.

## Before release

- Parts list with links, including the HDMI capture board and a fan with a control wire (see fan control above).
- Remove the dev-only debug drop-in on the Pi (`touchstream.service.d/debug.conf`).
- No default passwords anywhere.
- Ship the SIL Open Font License with the Bricolage Grotesque and JetBrains Mono fonts (in `spoke/assets/fonts` and
  `lite/internal/screen/assets`); the OFL requires it to travel with the fonts.
- Make the GHCR image public.
- Revisit the license (MIT for now; the goal is to keep the Cinderwire branding and stop rebranded forks).
- Real-world tests: a real HDMI source and audio (including 50 fps and 29.97 sources), a real YouTube channel, and the QR download on Android and iPhone.
