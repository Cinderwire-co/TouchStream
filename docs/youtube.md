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
