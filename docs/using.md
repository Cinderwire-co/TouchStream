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
- **Edit**: trim the start and end, cut out sections and add fades.
  - Edits are always made from the original recording.
  - **Save draft** keeps your work for later. Leaving with unsaved changes asks first.
  - **Render** makes the edited video. Rendering again replaces it.
  - **Publish** uploads it to YouTube as a new video, and can make the original live video private or unlisted.
    YouTube can't swap a video's footage, so publishing again uploads another one and can hide the earlier upload.
- **Transcript**: speech to text, made on the hub. Correct anything it misheard, then download captions (.srt, .vtt)
  for YouTube Studio. Captions for the edited video follow its cuts.

On the hub's computer, the folder button opens the broadcast's folder. Each broadcast gets one, like
`2026-11-08 Sunday Service`, with:
- its recordings and edited video, with the title, date and thumbnail built into each file;
- the thumbnail;
- the captions and transcript;
- an info file.

## The assistant

With an OpenRouter key (asked for when you set up the hub, or in Settings > Assistant), a button at the bottom right
of every page opens the assistant. Ask in plain words: "schedule our Sunday service every week at 10", "how are the
spokes?", or, in the editor, "cut the first ten minutes". It uses the page you're on, and asks before deleting,
publishing, going live or ending.

It uses [OpenRouter](https://openrouter.ai), an AI service you pay for as you use it. Claude Haiku 5.5 is the
default model: a typical request costs well under a cent. Without a key, the assistant doesn't appear and nothing is
sent anywhere.

## Settings

- **YouTube**: connect your channels ([how](youtube.md)).
- **Assistant**: your OpenRouter key and model, or turn it off.
- **Recordings folder**: where each broadcast's folder goes.
- **Old recordings**: delete recordings after a number of days, or to keep the folder under a size.
  - It deletes the oldest first, and shows what would go before saving.
  - Edited videos, thumbnails and transcripts are always kept.
  - Broadcasts that never went to YouTube are kept unless you allow it, since the hub may have the only copy.
- **Transcripts**: transcribe every broadcast automatically, how accurate (and slow) to be, the language, and names
  it should spell right.
- **How spokes reach this hub**: the address spokes send video to, if it differs from the dashboard's.

On the **Spokes** page:
- **Settings for all** sets the frame rate, quality and local copy on every spoke at once, and on spokes you add
  later.
- Each spoke's own **Settings** can still change it on its own.
