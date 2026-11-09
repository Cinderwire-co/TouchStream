# AI agents and the API

Everything the dashboard does, scripts and AI agents can do too: look at the spokes and the schedule, schedule
broadcasts (once or repeating), go live, read transcripts, edit the video and publish it.

## 1. Let the agent ask (the easy way)

Tell the agent to read `http://<hub address>/agent`. That page says what the hub does and how to ask for a token.
The agent asks, and the request shows up in the dashboard, on whatever page is open: its name, where it's asking
from, and a code. The agent shows you the same code, so you know the request is the one you're talking to. Approve
it, with less access than it asked for if you like, and the agent collects the token itself: there's nothing to
copy. A request nobody answers lapses after ten minutes, and so does a token nobody collects.

The page is written as a skill (`SKILL.md`), so an agent such as Claude Code can keep it and know the hub next time.
It never holds a token. For an agent that can't make web requests of its own, make a token by hand instead.

## 2. Or make a token

In the dashboard, open **Settings > API access** and make a token for each thing that will use the hub, with only
the access it needs:

| Access | Can |
|---|---|
| Read only | See everything, change nothing |
| Control | Also schedule, go live, edit and publish |
| Admin | Also change the hub's settings |

The token is shown once, so copy it then. Revoke it there any time. Tokens can never make other tokens or change the
hub's password.

## 3. Connect an AI agent (MCP)

The hub is an MCP server at `http://<hub address>/mcp`. MCP is the standard way AI agents use tools, and it works
with Claude, ChatGPT, n8n, local models and others. Send the token as a header:
`Authorization: Bearer ts_...`.

**Claude Code:**

```bash
claude mcp add --transport http touchstream http://192.168.1.20:8080/mcp --header "Authorization: Bearer ts_..."
```

**Cursor, VS Code and other apps** that take an MCP config file:

```json
{ "mcpServers": { "touchstream": { "type": "http", "url": "http://192.168.1.20:8080/mcp",
    "headers": { "Authorization": "Bearer ts_..." } } } }
```

**Claude Desktop and claude.ai** connectors sign in with OAuth rather than a token, which the hub doesn't offer yet.
For now, use Claude Code or another app that can send a header.

**n8n**: add an **MCP Client Tool** node with the same URL, and the token as a Bearer header.

The agent gets thirteen tools:
- `hub_overview`
- `list_broadcasts`, `get_broadcast`
- `schedule_broadcast` (once or repeating), `update_broadcast`, `delete_broadcast`
- `go_live`, `end_live`
- `get_transcript`, `edit_video` (cuts, fades, transitions, a color look, a brand and titles; render it, or leave
  it on the editor's timeline as a draft), `publish_video`
- `make_short`, `publish_short`

It also gets a short brief of how things ripple: what YouTube does when you change, delete or publish. So you can ask
"schedule our Sunday service every week at 10, adding the date to the title", "cut the first ten minutes off
Sunday's video and publish it", or "make a short of where he talks about walls and bridges".

Agents on another network need to reach the hub. A hub on Coolify has an HTTPS address already; for one at home,
use something like [Tailscale](https://tailscale.com).

## 4. Or call the API directly

The API is described at `http://<hub address>/api/openapi.json` (OpenAPI 3), with each route and what changing it
does. For example:

```bash
curl -H "Authorization: Bearer ts_..." http://192.168.1.20:8080/api/events
```

Recordings download from `/api/recordings/file/<path>`, and just their sound, as an MP3 for a podcast or website,
from `/api/recordings/audio/<path>` (`get_broadcast` gives both paths for each recording):

```bash
curl -OJ -H "Authorization: Bearer ts_..." http://192.168.1.20:8080/api/recordings/audio/library/12
```

GET requests need read access; anything that changes something needs control. Settings, the old-recordings rules
and YouTube's sign-in need admin.
