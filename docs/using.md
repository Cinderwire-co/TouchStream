# Using TouchStream

The dashboard has four places:
- **Home**: everything at a glance.
- **Schedule**: every broadcast, coming up and aired.
- **Spokes**: each spoke's picture and controls.
- **Settings**.

Open it from any browser at the hub's address, on the hub computer or anywhere on your network.

## Home

- How many spokes are online, and whether any is live.
- The next broadcast.
- The hub's internet speed (**Test speed**) and free storage.
- For each spoke: its temperature, free space on its card, the recordings on it, and its own internet speed.

Upload speed is what matters for live video. Aim for about 1.5 times what you stream: a spoke sends 8 Mbps by default.

## Scheduling

**Schedule broadcast** opens the broadcast editor:

- **Details**: title, description, thumbnail (1280 × 720), visibility, latency, and whether it's made for kids.
- **When**: the date, start and end time.
  - **Once** or **Repeats**. A repeating broadcast picks:
    - the days of the week;
    - every week, every other week, or certain weeks of the month (1st, 2nd, 3rd, 4th, last);
    - the months: none picked means every month;
    - optionally a last date.
  - It shows the next few dates, so you can check them.
  - **Add the date to each title** gives each broadcast its own title, like "Sunday Service · November 8, 2026".
- **Where**: the spoke, the YouTube channels, and **Start automatically**. With it on, the hub puts the spoke live at
  the start time and ends it at the end time. With it off, you press **Start now**.

A repeating broadcast creates each date's broadcast, on YouTube too, a week before it airs. Dates further out are
listed as **Planned**.

**Changing or deleting one date of a series** asks: **only this one**, or **all upcoming**. A date you change on its
own keeps its changes when you later change the series, for a guest speaker's title, say. Deleting just one date
skips it.

## Going live

Scheduled broadcasts go live by themselves. To go live now, open a spoke and choose **Live**. **Preview** shows the
picture in the dashboard without going live. The hub also records anything that goes live without a schedule, as its
own broadcast.

## After a broadcast

Aired broadcasts are under **Schedule > Past**, each with:

- **Download**: the hub's recording, the spoke's recording and the edited video.
- **Edit**: a linear editor (see below). Edits are always made from the original recording.
  - **Save draft** keeps your work for later. Leaving with unsaved changes asks first.
  - **Render** makes the edited video. Rendering again replaces it.
  - **Publish** (in Export) uploads it to YouTube as a new video, and can make the original live video private or
    unlisted. YouTube can't swap a video's footage, so publishing again uploads another one and can hide the earlier
    upload.
- **Transcript**: speech to text, made on the hub. Correct anything it misheard, then download captions (.srt, .vtt)
  for YouTube Studio. Captions for the edited video follow its cuts.
- **Shorts**: vertical videos of one moment, for YouTube Shorts, Instagram Reels and TikTok (see below).

On the hub's computer, the folder button opens the broadcast's folder. Each broadcast gets one, like
`2026-11-08 Sunday Service`, with:
- its recordings and edited video, with the title, date and thumbnail built into each file;
- the thumbnail;
- the captions and transcript;
- an info file.

## The editor

Properties on the left, the picture beside them, the timeline underneath, with these tracks:
- **Color**: one adjustment over the whole recording.
- **Titles**.
- **Video**, and its **Audio**.
- **Music**.
- **Words**: the transcript.
- **Shorts**.

- **Cut**: the cut tool (B) splits a clip where you click; S cuts every track at the playhead.
- **Delete** takes a clip out and leaves a gap. Click the gap and press Delete to close it, or Shift+Delete to do both
  at once. I and O mark a range to delete the same way.
- **Trim and move**: drag a clip's edges to trim it in place, or drag the clip to move it. A clip's picture and sound
  move together; hold Option to change just one (for an L-cut), or select one and **Unlink sound**.
- **Transitions**: click a cut and pick dissolve, dip to black or white, wipe, slide or circle, and how long. It
  plays across the cut, so nothing moves.
- **Fades**: drag the round handles at the ends of the video track.
- **Color**: click the Color track for presets (Warm, Cool, Vivid, Black & white, Faded) and sliders.
- **Titles** (T): a lower third, a title, a caption or a corner tag, shown exactly as rendered. Drag to move and
  trim.
- **Media**: upload intros, slides, logos and music to the hub's library, then **Insert here** (it moves the rest
  along) or **Add as music** (it fades in and out under everything).
- **Brand**: pick one in Properties to put its intro and outro on the timeline and its watermark over the recording.
- ⌘Z undoes anything. The **Keyboard shortcuts** under the timeline lists the rest.
- The **assistant** works here too: "put the Sunday brand on it, make it warm and add a lower third for Pastor Sam at
  30 seconds" changes your timeline, ready for you to check and render.

## Brands

**Settings > Brands**: as many as you like, one per channel or ministry. Each has:
- an intro and an outro: a video, or an image shown for a few seconds;
- a watermark: an image in a corner, its size and opacity;
- the YouTube channels it's for. The editor starts their broadcasts with it.

## Shorts

**Shorts** on an aired broadcast (or **Make a short** on its transcript) opens the editor's Shorts tab:

- **The moment**: click the sentence it starts with, then the one it ends with. It's picked by sentences, so a short
  never starts or ends mid-word. Up to three minutes. **Play the moment** to check it.
- **Framing**:
  - **Follow speaker**: a vertical window that finds the speaker's face and moves with them. It holds still while
    they stay near the middle, eases over when they walk, and holds its place during cutaways like slides.
  - **Whole picture**: the full picture across the middle, over a blurred copy of itself.
- **Captions**, burned in: Bold, Marker, Boxed, Pop or Clean, in one of six accent colors, or none. The word being
  spoken lights up.
- **Take out pauses**: pauses over about half a second become quick jump cuts.

**Make short** renders it on the hub; the dashboard preview is a rough look until then. Each short is saved in the
broadcast's folder, and a broadcast can have as many as you like. **Publish** uploads one to YouTube as a Short;
**Change** makes it again with new settings. Download it for Reels or TikTok.

Shorts need the broadcast's transcript for the sentences and captions. Without one, you can still pick the start and
end in seconds.

## The assistant

With an OpenRouter key (asked for when you set up the hub, or in Settings > Assistant), a button at the bottom right
of every page opens the assistant. Ask in plain words: "schedule our Sunday service every week at 10", "how are the
spokes?", in the editor "cut the first ten minutes", or "make a short of the part about forgiveness". It uses the
page you're on, and asks before deleting, publishing, going live or ending.

It uses [OpenRouter](https://openrouter.ai), an AI service you pay for as you use it. Claude Haiku 5.5 is the
default model: a typical request costs well under a cent. Without a key, the assistant doesn't appear and nothing is
sent anywhere.

## Settings

Settings is in sections, chosen on the left (along the top on a phone):

- **General**: updates for the hub and its spokes ([how they work](hub.md#updates)), the dashboard's password, and
  this hub's version and data folder.
- **YouTube**: your channels, and the Google OAuth client they sign in with ([how](youtube.md)).
- **Recordings**: the folder, old recordings, and transcripts (below).
- **Brands**: intros, outros and watermarks ([Brands](#brands)).
- **Assistant and API**: your OpenRouter key and model, or turn it off; tokens for AI agents and scripts
  ([API](api.md)).
- **Network**: how spokes and other encoders reach the hub.

In more detail:

- **Recordings folder**: where each broadcast's folder goes.
- **Old recordings**: delete recordings after a number of days, or to keep the folder under a size.
  - It deletes the oldest first, and shows what would go before saving.
  - Edited videos, shorts, thumbnails and transcripts are always kept.
  - Broadcasts that never went to YouTube are kept unless you allow it, since the hub may have the only copy.
- **Transcripts**: transcribe every broadcast automatically, how accurate (and slow) to be, the language, and names
  it should spell right.
- **How spokes reach this hub**: the address spokes send video to, if it differs from the dashboard's.

On the **Spokes** page:
- **Settings for all** sets the frame rate and quality on every spoke at once, and on spokes you add
  later.
- Each spoke's own **Settings** can still change it on its own.
