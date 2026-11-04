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
- The TouchStream mark (`brand/touchstream_mark.py`): an ember play shape sending out arcs of signal, in the Cinderwire mark's chipped-shard style. It's on the dashboard, favicon, Mac app icon, boot splash, holding screen and touchscreen. "Made by Cinderwire" sits on the sign-in page and in Settings.

## Next

1. **Installers without a terminal**: a signed Mac .pkg and a Windows installer, plus a Linux systemd test on a real
   machine.
2. **Record on the spoke without a hub**: a Record button on the touchscreen. The QR code opens a small page served by the spoke to trim and download.
   - Trims are keyframe-accurate (every 2 s) with no re-encode, so the Pi stays fast.
   - Fades, cuts in the middle and publishing stay on the hub.
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

## Shorts and reels (long road, all on the hub)

- **Pick a moment** from the transcript or search. The cut follows sentence boundaries.
- **Framing**, two choices:
  - *Fill*: a vertical 9:16 crop that follows the speaker with face and body tracking, smoothed so it doesn't jitter.
  - *Fit*: the whole 16:9 picture in the middle, with the top and bottom filled by a blurred, enlarged copy of the same video.
- **Captions burned in**, in 4–5 fixed styles only, so nothing off the system looks bad.
  - Each style sets font, colour, outline or box, position and word highlighting.
  - Users pick a style and an accent colour, not free-form settings.
- **Smart silence cutting** (optional): remove pauses longer than a threshold, with short crossfades.
- Export the file, or upload it as a YouTube Short with its own title.

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
- Make the GHCR image public.
- Revisit the license (MIT for now; the goal is to keep the Cinderwire branding and stop rebranded forks).
- Real-world tests: a real HDMI source and audio (including 50 fps and 29.97 sources), a real YouTube channel, and the QR download on Android and iPhone.
