"""What an AI agent reads first (GET /agent, and /llms.txt): what the hub does, how to get a token
without anyone copying one, and how to use it. It's written as a skill (SKILL.md), so an agent can
keep it as one: it names this hub's address, and never a token."""

GUIDE = """---
name: touchstream
description: Run the TouchStream hub at {base}, which streams and records live video from rooms (its spokes) to
  YouTube. Use it to see the spokes and the schedule, schedule broadcasts (once or repeating), go live and end,
  read transcripts, edit recordings and publish them, and make shorts.
---

# TouchStream at {base}

TouchStream is a hub and its spokes. A spoke is a box in a room with a camera or video switcher plugged into it;
the hub runs the schedule, puts each broadcast live on YouTube, keeps a recording of every one, and edits them.

## Getting access

You need an API token. Don't ask your person to make one and paste it: ask the hub for one, and they approve it
in the dashboard.

1. Look for a token you kept before (an environment variable such as `TOUCHSTREAM_TOKEN`, or your secret store).
   If `GET {base}/api/overview` with it answers, skip to "Using the hub".
2. Ask, with no token:

   ```bash
   curl -s -X POST {base}/api/access -H 'content-type: application/json' \\
     -d '{{"name": "Claude Code on the office Mac", "scope": "control"}}'
   ```

   `name` says who you are and where, as your person will see it. `scope` is the access you need:
   `read` sees everything and changes nothing; `control` also schedules, goes live, edits and publishes; `admin`
   also changes the hub's settings. Ask for the least that will do. The answer has an `id` and a `code`.
3. Tell your person: "Approve TouchStream access for <name> in the dashboard ({base}). Its code is <code>." The
   dashboard shows the request, with the same code, on whatever page is open.
4. Wait for the answer: `GET {base}/api/access/<id>?wait=25` waits up to 25 seconds for a change. Ask again while
   `status` is `pending`. When it's `approved`, the answer has your `token`, this once: keep it somewhere safe
   (not in this file). `denied` means no; `expired` means nobody answered within ten minutes, so ask again if
   your person wants to.

Your person can revoke the token any time, in Settings > API access.

## Using the hub

Send the token on every request: `Authorization: Bearer ts_...`.

**MCP** (best, if you can connect to MCP servers): `{base}/mcp`, over streamable HTTP, with that header. In
Claude Code: `claude mcp add --transport http touchstream {base}/mcp --header "Authorization: Bearer ts_..."`.
Its tools:

{tools}

**The HTTP API**, for anything else: every route is described at `{base}/api/openapi.json` (OpenAPI 3), with what
changing it does. Some to start with:

- `GET /api/overview`: the spokes, what's live, what's next
- `GET /api/events`: the broadcasts, past and upcoming, with their recordings
- `POST /api/events`: schedule a broadcast; `POST /api/events/<id>/start` and `/end` to go live and end
- `GET /api/events/<id>/transcript`: what was said, with times

Read access can GET; changing anything needs control; the hub's settings need admin.

## Good to know

- Times are the hub's local time unless they say otherwise.
- Publishing an edit makes a new YouTube video, and hides the original live one.
- Deleting a broadcast that's on YouTube deletes it there too. Check with your person first.
"""

TOOLS = {
    'hub_overview': 'the spokes, what is live, what is next',
    'list_broadcasts': 'upcoming or past broadcasts',
    'get_broadcast': "one broadcast: its YouTube channels, recordings and edits",
    'get_transcript': 'what was said, with times (or search it)',
    'schedule_broadcast': 'once or repeating',
    'update_broadcast': 'its title, time, spoke or channels',
    'delete_broadcast': 'this one, or all upcoming ones of a series',
    'go_live': 'a scheduled broadcast now, or a spoke on its own',
    'end_live': 'end what is live',
    'edit_video': 'cuts, fades, a color look, a brand and titles; render it, or leave a draft in the editor',
    'publish_video': 'an edit, to YouTube',
    'make_short': 'a vertical short of a moment',
    'publish_short': 'a short, to YouTube',
}


def guide(base):
    tools = '\n'.join(f'- `{name}`: {what}' for name, what in TOOLS.items())
    return GUIDE.format(base=base.rstrip('/'), tools=tools)
