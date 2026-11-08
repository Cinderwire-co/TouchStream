# TouchStream roadmap

Working notes on what's planned, in rough order. Two rules hold for everything here:

- **Simple to deploy.** Anyone should get from download to streaming without a terminal.
- **Simple to use.** Add features without adding clutter: each thing has one home in the UI.

## Done recently

- Updates: the hub looks for new releases on GitHub and updates itself when nothing's live or due (or, with automatic updates off, says so on every page and waits for Update now); then it gives each spoke the matching version when it's idle. Spokes check each update was signed with TouchStream's release key, keep the version before, and go back to it if the new one can't reach the hub. A hub in Docker says when there's a new image instead. `tools/release` builds a release (the spoke's update, the card image, the Mac and Windows installers) and drafts it on GitHub.
- Settings in sections (General, YouTube, Recordings, Brands, Assistant and API, Network), each with its own link; YouTube channels can be connected from Settings; disconnecting one gives the hub's access back to Google. Past broadcasts have Edit, Download and a More menu instead of six buttons; the editor and transcript lead back to the broadcast's page.
- cinderwire.co/touchstream: TouchStream's page, with its privacy policy and terms (the YouTube API Services and Google's Limited Use wording, for Google's verification), not yet published.
- Every broadcast has its own page (click it on the schedule): its picture, or once it's aired its recording; when and where it goes; Start now or End, and Edit; its YouTube links; and its recordings, transcript and shorts. Scheduling says what it's doing as it saves (creating the broadcast on YouTube, the thumbnail, sending it to the spoke) and confirms when it's done. Spokes say which broadcasts they've kept, so the schedule shows when a spoke has its copy to stream straight to YouTube if the hub is down.
- The PicoKVM, on the desk: it starts from the installer's card and pairs itself; HDMI capture at 1080p60 through the hardware encoder; HDMI sound at the right pitch (the HDMI chip's reference clock, 26 to 27 MHz, in the image's device tree); TouchStream's splash from the first moment, with the kernel's console and logo kept off the screen; and the spoke's screen at a quarter of the CPU it took. On a Mac, the installer opens the card itself with an admin's password (macOS's `authopen`), as macOS refuses a removable card to a program run as admin.
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

1. **The TouchStream installer** for Mac and Windows (`installer/`, built and tested with a disk image for a card):
   it installs or finds the hub, asks about spokes, checks you have the right box, and writes and checks each
   spoke's microSD card, already paired with the hub. Still to do: sign and notarize it (an Apple Developer ID and
   a Windows code-signing certificate; the Mac's is done, `installer/tools/release-mac`), and write a real card once
   the image exists.
2. **The spoke on the real box** (a Luckfox PicoKVM arrives Nov 7): HDMI capture and its hardware encoder, HDMI
   sound, the screen on the panel, ffmpeg on board, and the image the installer writes (below).
3. **Straight to YouTube from the spoke** when the hub can't be reached (below).
4. **Search across everything** (below).

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
- Spokes keep a week of their schedule and stream straight to YouTube if the hub is down. The hub takes over again when it's back. (Coming back in the PicoKVM spoke: see below.)

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

The spoke is the Luckfox PicoKVM (in `spoke/`, in Go). The Raspberry Pi spoke is retired: it's in the history at
commit a12f6b2. The PicoKVM won on price (about $38 to $87 with its screen) and on being one box with nothing to build;
the Sipeed NanoKVM Pro (HDMI sound, loop-out) and GL.iNet Comet were over budget, and the original NanoKVM and JetKVM
have no HDMI sound.

Working in the emulator (`spoke/tools/dev emulate`: an ARMv7 container held to the box's one core and 256 MB,
replaying a clip as its HDMI input): the hub link and adoption, the SRT stream, recording and upload, the screen with
Go live, End and Record, and recordings to phones by QR code. Still to come:

- **Updates to the box's own system** (Linux, drivers, the start scripts): the spoke updates itself over the
  network now, but the system under it changes only with a new installer card. Next: the hub offers a system
  image too, and the box writes it to the A/B slot it isn't running and starts from that, falling back to the old
  slot if it doesn't come up.
- **On the box** (what's inside, and the checks in order: [spoke/docs/picokvm.md](../spoke/docs/picokvm.md)):
  HDMI capture and the RV1106's hardware H.264 encoder, at the hub's frame rate (it's sold as 1080p60), through a
  small capture helper of our own, as Luckfox's `kvm_video` tops out at 2 Mbps; the EDID that asks the source for
  that rate; the screen (`/dev/fb0`) and its touch (`/dev/input/event0`); ffmpeg on board (it has RTMPS), for phone
  copies, clips and the direct stream; its time zone, from the hub.
- **Spokes sold ready to go** (Will installs the software before selling them): no installer's card, so no name.
  Each gets one of its own until it's renamed in the dashboard: "Spoke" and the last four characters of its network
  address ("Spoke 4F2A"), shown on its screen too, so two new ones on a desk can be told apart when they're adopted.
- **The installer shows where the card goes**: a drawing of the PicoKVM (from photos of the real box: the front, the
  microSD slot close up, and the ports), with the card going into its slot and the power last, on the "card's
  ready" page.
- **Installed on the box itself, the card for recordings** (Will's call, 2026-11-07; built: `spoke/internal/install`). The box starts from a card with
  a system on it even with its eMMC's in place, so: the installer writes the card as now; at its first start, the
  spoke copies itself onto the eMMC (boot and system partitions, the environment with `mmcblk0`, the pairing), checks
  it, and only then wipes the card's start and formats it exFAT for recordings (readable on a Mac or PC when it's
  taken out; the kernel and `mkfs.exfat` are there). Then it starts again, from the eMMC. A power cut part way
  through leaves the card as it was, so it just starts over. With no card in, recordings go on the eMMC. Keep the
  eMMC's own idblock and U-Boot (they're made to start from it), and the card image's kernel and device tree (4 CSI
  lanes: the factory eMMC system's 2 can't carry 1080p60). This replaces Luckfox's software on the eMMC; their USB
  flashing tool and eMMC image (in `.dev/picokvm/eMMC`) put it back.
- **The image the installer writes**: built with Luckfox's Buildroot SDK, with the spoke and ffmpeg, released on
  GitHub as `touchstream-spoke-picokvm-VERSION.img.xz` with a `.sha256` beside it. It needs a 64 KiB block for the
  installer to fill in: at a 64 KiB boundary in the image's first 64 MiB, starting `TOUCHSTREAM-PROVISION-V1` and a
  newline, then zeros. The installer writes the hub's address and the spoke's credentials there, and the spoke reads
  them at its first start (`spoke/internal/provision`, from `/dev/mmcblk1`, the card). It's made from Luckfox's own
  microSD image for now (`tools/picokvm_image.py`), and boots from the card with the eMMC in place (checked
  2026-11-07).
- Straight to YouTube (done in the emulator): with no hub, from stream keys added on the setup page; with a hub that's
  out of reach, from the schedule and keys it sends. Still to check on the box: ffmpeg's RTMPS (TLS) to YouTube, and
  the encoder following the setup page's resolution (720p means scaling in the encoder).
- **Wi-Fi.** It only has Ethernet. Plan on a pocket travel router as its Wi-Fi: over Ethernet (no software), or over
  one USB cable that powers the box too, with the box acting as a tethered phone (a USB network gadget, NCM, which
  Windows, macOS and Linux all take without drivers). The same USB network lets the box borrow the network of the
  computer it's plugged into, and reach a hub running on it directly. Also try its USB-C port as a host for a Wi-Fi
  stick (it's also the power input, so it may not work).
- Recordings go on the microSD card (not the 8 GB eMMC); 48 GB free holds about 12½ hours at 8 Mbps. Recommend a
  "high endurance" card in the parts list. (Phone trims now snap to the keyframes the recorder notes, so a clip begins
  exactly where its handle shows.)
- H.265 as an option for slow uploads (its encoder does both): about the same picture in 30–50% fewer bits. H.264
  stays the default: YouTube re-encodes everything anyway, and H.265 needs enhanced RTMP to reach YouTube and plays
  patchily in browsers (the dashboard preview, the phone page).
- Its pin out (I2C, two serial ports, 3.3 V) can't carry network or video, but could drive a tally light or a physical
  Record button.

## Shorts: still to come

- An optional manual nudge for the window when there's no face to follow (a wide shot, someone facing away).
- Faces too small to find in a very wide shot: look again on tiles of a sharper frame.
- Body tracking as well as faces, for a speaker who turns away for a while.
- Shorts from the edited video, and suggested moments (the assistant picking quotable passages from the transcript).
- Upload straight to Instagram and TikTok (today: download, then post).

## Later

- A UI pass as features land: keep four top-level places (Home, Schedule, Spokes, Settings), and keep each feature inside the broadcast or spoke it belongs to.
- Move a whole series to another spoke (one upcoming broadcast can move today: needed when a spoke is removed, since
  its series keeps making dates for it).
- Series titles: the date is added in English ("November 8, 2026"); follow the hub's language once there's a setting for it.

## Launch (after testing and the build are done)

- **A promo video** of TouchStream and its parts:
  - a 3D animation of the spoke (the PicoKVM box with its screen), rendered in Blender, which can be scripted end to
    end;
  - what it does, with animations of the dashboard;
  - the assistant and AI agents running it, the API, and YouTube.
  - Start from the product-video templates in the ops repo.

## The TouchStream app (an idea, 2026-11-07)

TouchStream on a Mac or PC should feel like an app, not a website: the installer grows into **TouchStream.app**, which
runs the hub in the background and opens the dashboard in its own window (no browser, no address or port to see), with
a menu-bar or tray icon, notifications (live, ended, an update ready), the recordings as files you can drag out and
open, and Add spoke writing the card right there. Phones and other computers still reach the dashboard on the network
(Hub address). The installer is already Wails (Go and a web page), so the window and the files come from there.

## Distribution

- **YouTube sign-in built in**: ship Cinderwire's Google OAuth client with the hub, so Connect channel works without
  making one (your own stays an option). Needs Google's verification to finish, a YouTube API quota increase (every
  hub would share one project's daily quota), and the owner's OK to publish its client secret (normal for TV and
  device sign-in clients, which Google doesn't treat as confidential).
- **Signed installers**: the Mac one with the Developer ID and notarized (`installer/tools/release-mac`; needs a
  notarytool keychain profile), the Windows one with a code-signing certificate (Microsoft's Trusted Signing, or an
  OV certificate), so neither warns on first open. 0.1.0 shipped unsigned.

## Before release

- Parts list with links: the PicoKVM (with its screen), a microSD card, a power supply, and a travel router for Wi-Fi.
- No default passwords anywhere.
- Revisit the license (MIT for now; the goal is to keep the Cinderwire branding and stop rebranded forks).
- Real-world tests: a real HDMI source and audio (including 50, 60 and 29.97 fps sources), a real YouTube channel, and the QR download on Android and iPhone.
