# Connecting YouTube

The hub creates your YouTube broadcasts itself: title, description, thumbnail, visibility, and the stream it goes
live on. It signs in to YouTube with Google's device sign-in, so connecting a channel takes a minute and nothing to set
up. Your hub then talks to Google directly; nothing goes through Cinderwire.

## Before you start

- The channel must be able to **live stream**. Turn it on at [youtube.com/features](https://www.youtube.com/features).
  The first time, YouTube can take up to 24 hours to allow it.
- Custom thumbnails need a channel with **intermediate features** (a verified phone number), on the same page.

## Connect channels

On the **Schedule** page (or **Settings > YouTube**), choose **Connect channel**. The hub shows a code. On any phone or
computer:
1. Open [google.com/device](https://www.google.com/device) and enter the code.
2. Pick the channel. A Brand Account works too.

Connect as many channels as you like. Each broadcast picks which ones it goes out on.

The sign-in is TouchStream's own Google client, the same for every hub. The hub reads its ID and secret from
cinderwire.co once a day (nothing about you is sent), keeps them, and uses them only to sign in to Google. Google's
consent screen names TouchStream and asks for one permission, "Manage your YouTube account".

## Your own Google client (optional)

For your own app name on Google's consent screen, or a YouTube quota of your own, use an OAuth client of your own.
It takes about 10 minutes, once:

1. Open [Google Cloud Console](https://console.cloud.google.com) and create a project, for example "TouchStream".
2. In **APIs & Services > Library**, find **YouTube Data API v3** and **Enable** it.
3. Open **Google Auth Platform**:
   - set up the app as **External**, with your email as the support contact;
   - under **Audience**, choose **Publish app**. While it's in testing, sign-ins expire after 7 days. You don't need
     Google to verify it: you and anyone you invite can sign in past the "unverified app" warning.
4. Under **Clients**, create a client of type **TVs and Limited Input devices**. Copy its **Client ID** and
   **Client secret**.
5. In the dashboard, open **Settings > YouTube > Use your own Google OAuth client**, paste them, and **Save**.

New channels then connect with your client; each channel keeps the client it was connected with. **Use TouchStream's
instead** goes back (channels connected with yours then need connecting again). `TOUCHSTREAM_GOOGLE_CLIENT_ID` and
`TOUCHSTREAM_GOOGLE_CLIENT_SECRET` set a client from the environment, and `TOUCHSTREAM_SHARED_CLIENT=off` stops the
hub reading TouchStream's.

## What TouchStream does on YouTube, and when

Each broadcast you schedule becomes a broadcast on every channel you pick. TouchStream sets its **title**,
**description**, **start and end time**, **privacy** (public, unlisted or private), **made for kids**, **latency**
(normal, low or ultra-low) and **thumbnail**: the broadcast's own, or else the default thumbnail of the channel's brand
(Settings > Brands). It also turns on **auto start** (it goes live when the spoke's video arrives), **auto stop** (it
ends when the video stops) and **DVR** (viewers can rewind while it's live), and binds it to the spoke's stream key on
that channel: one key per spoke per channel, made the first time and reused.

### Before it starts

Everything you change in the dashboard goes to YouTube:

| Change | On YouTube |
|---|---|
| Title, description, times, privacy, made for kids | Updated on the broadcast |
| Thumbnail | Replaced |
| The spoke | The broadcast is bound to the other spoke's stream: its watch link stays the same |
| Add a channel | A broadcast is made on that channel |
| Remove a channel, or delete the broadcast | It's deleted from that channel |
| Latency | **Not** changed: YouTube sets it when the broadcast is made. Change it in YouTube Studio, or delete the broadcast and schedule it again |

A broadcast whose start time has passed but that hasn't gone live yet is edited like a live one (below): YouTube only
takes new times while they're still in the future.

### While it's live

| Change | On YouTube |
|---|---|
| Title, description, privacy, made for kids | Changed on the live video; viewers see the new title within a minute or so |
| Thumbnail | Replaced |
| Times, latency, DVR | Not possible: YouTube locks them once a broadcast starts |
| The spoke | Not possible: end it and start again from the other spoke |
| Add a channel | Doesn't take (YouTube won't make a broadcast that starts in the past): add channels before it starts |
| Remove a channel | The hub stops following it there; the video stays on that channel |
| End | End it in the dashboard or on the spoke's screen; YouTube ends the broadcast when the video stops |
| Delete it in the hub | It ends, and the video stays on YouTube |

### After it's over

The broadcast is now the channel's video. Its **title, description, privacy, made for kids and thumbnail** can still be
changed from the dashboard (on the video). Deleting the broadcast in the hub, or removing a channel from it, **never**
deletes the video on YouTube: do that in YouTube Studio if you want it gone.

### Live chat

YouTube's API has no way to turn live chat on or off, so TouchStream can't. Turn it off for a broadcast in YouTube
Studio's Live Control Room (Edit, then Customization, then Live chat). Your channel's default chat setting may not
apply to broadcasts made through the API: check the first one. Broadcasts marked **made for kids** have chat and
comments off, by YouTube's rules.

### Edited videos and Shorts

**Publishing an edit** uploads a new video to each channel you pick, with its title, description and privacy (public,
unlisted or private), made for kids (from the broadcast), and a thumbnail: the broadcast's, or else the brand's. You can
also have the original livestream made private or unlisted. YouTube can't swap a video's footage, so publishing an edit
again uploads another new video, and can hide the earlier one. Captions: the hub makes caption files re-timed to the
edit (SRT or VTT); upload them in YouTube Studio.

**Publishing a Short** uploads it with its title, description (#Shorts is added if it's not there), privacy and made
for kids. Its captions are burned in, and YouTube picks a Short's thumbnail from the video.

Views, likes and comments of both show in the hub. Changing their title or thumbnail after publishing is done in
YouTube Studio for now.

### What YouTube Studio does that TouchStream doesn't (yet)

Live chat on or off and its moderation, comment settings, premieres, end screens and cards, playlists, tags, category
and language (TouchStream keeps whatever a video has; uploads get YouTube's defaults), scheduled publishing of uploads,
captions upload, age restrictions, and ads (monetization). Some of these are on the roadmap.

### Limits worth knowing

- **Thumbnails** are JPG or PNG, up to 2 MB (1280×720 is ideal), and need a channel with intermediate features (a
  verified phone number).
- **Ultra-low latency** doesn't support captions or resolutions above 1080p.
- **The daily allowance**: every hub using TouchStream's Google sign-in shares one YouTube quota. Scheduling and going
  live use little of it; uploads use a lot. A Google client of your own has its own allowance.

## Good to know

- **Each spoke gets its own stream key on each channel**, made the first time it goes live there. You never copy a
  stream key by hand.
- **YouTube limits uploads** per Google project each day. Live broadcasts don't count; publishing edited videos does.
  If TouchStream's shared allowance runs short, your own Google client has its own.
- **Deleting a broadcast that has aired** never deletes it on YouTube: that would delete the channel's video. Deleting
  one that hasn't aired removes it from YouTube too.
- **Disconnecting a channel** (Settings > YouTube) deletes its sign-in from the hub and gives the hub's access back
  to Google. You can also take it back yourself at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
- **Your channel's data stays on your hub.** It talks to Google directly; see the
  [privacy policy](https://cinderwire.co/touchstream/privacy).
- **A channel you can't connect** (someone else's, say): add its stream key to a spoke instead, with **Add stream
  key** on the spoke's page. It goes out whenever that spoke is live. The server address can be any RTMP(S) server.
