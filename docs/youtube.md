# Connecting YouTube

The hub creates your YouTube broadcasts itself: title, description, thumbnail, visibility, and the stream it goes
live on. To do that it signs in to YouTube with a Google "OAuth client" that belongs to you. Your hub then talks to
Google directly; nothing goes through Cinderwire. You set the client up once, and it takes about 10 minutes.

## Before you start

- The channel must be able to **live stream**. Turn it on at [youtube.com/features](https://www.youtube.com/features).
  The first time, YouTube can take up to 24 hours to allow it.
- Custom thumbnails need a channel with **intermediate features** (a verified phone number), on the same page.

## 1. Make the OAuth client

1. Open [Google Cloud Console](https://console.cloud.google.com) and create a project, for example "TouchStream".
2. In **APIs & Services > Library**, find **YouTube Data API v3** and **Enable** it.
3. Open **Google Auth Platform**:
   - set up the app as **External**, with your email as the support contact;
   - under **Audience**, choose **Publish app**. While it's in testing, sign-ins expire after 7 days. You don't need
     Google to verify it: you and anyone you invite can sign in past the "unverified app" warning.
4. Under **Clients**, create a client of type **TVs and Limited Input devices**. Copy its **Client ID** and
   **Client secret**.

## 2. Add it to the hub

In the dashboard, open **Settings > YouTube**, paste the client ID and secret, and **Save**.

## 3. Connect channels

On the **Schedule** page, choose **Connect channel**. The hub shows a code. On any phone or computer:
1. Open [google.com/device](https://www.google.com/device) and enter the code.
2. Pick the channel. A Brand Account works too.

Connect as many channels as you like. Each broadcast picks which ones it goes out on.

## Good to know

- **Each spoke gets its own stream key on each channel**, made the first time it goes live there. You never copy a
  stream key by hand.
- **YouTube limits uploads**: about 6 videos a day per Google project. Live broadcasts don't count; only publishing
  edited videos does.
- **Deleting a broadcast that has aired** never deletes it on YouTube: that would delete the channel's video. Deleting
  one that hasn't aired removes it from YouTube too.
- **A channel you can't connect** (someone else's, say): add its stream key to a spoke instead, with **Add stream
  key** on the spoke's page. It goes out whenever that spoke is live. The server address can be any RTMP(S) server.
