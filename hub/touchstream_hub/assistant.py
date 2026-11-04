"""The assistant: ask in plain words on any dashboard page, and it runs the hub (with OpenRouter)

It has the same tools and brief as agents using the MCP endpoint (mcp_server.py), plus which page
you're on, so "cut the first ten minutes" on the editor means this broadcast. Deleting,
publishing, and going live or ending stop and ask you first.

It's off until an OpenRouter API key is added, at setup or in Settings. Without a key the
dashboard doesn't show it, and nothing is sent anywhere.

TOUCHSTREAM_OPENROUTER_BASE points it at another OpenAI-compatible server (tests use a fake).
"""
import json
import os
import time

import httpx

OPENROUTER = os.environ.get('TOUCHSTREAM_OPENROUTER_BASE', 'https://openrouter.ai/api/v1')   # (tests use a fake)
DEFAULT_MODEL = 'anthropic/claude-haiku-5.5'
MODELS = [('anthropic/claude-haiku-5.5', 'Claude Haiku 5.5', 'Fast and low cost'),
          ('anthropic/claude-sonnet-5.5', 'Claude Sonnet 5.5', 'Smarter, costs more'),
          ('openai/gpt-5.6-luna', 'GPT-5.6 Luna', 'OpenAI, low cost'),
          ('google/gemini-3.8-flash', 'Gemini 3.8 Flash', 'Google, low cost')]
CONFIRM = {'delete_broadcast', 'publish_video', 'go_live', 'end_live'}   # these ask first
MAX_STEPS = 12          # tool calls per question
MAX_RESULT = 12000      # characters of a tool's result the model sees

PERSONA = """You're the assistant inside the TouchStream Hub dashboard, talking with the person who runs it (often a
church or venue volunteer, not a technical person). Do what they ask with the tools, rather than explaining how to
click through the dashboard. Look things up before acting; don't guess ids. Keep replies short and plain: say what
you did, or ask one clear question if something's missing. Times are the hub's local time: it's now {now}.
They're on: {page}."""


class AssistantError(Exception):
    pass


def _mcp():
    from . import mcp_server   # here, not at the top: it imports the app, which imports this
    return mcp_server


class Assistant:
    def __init__(self, db):
        self.db = db
        self.http = httpx.AsyncClient(timeout=120)

    # ---- Settings ----
    def config(self):
        return {'model': DEFAULT_MODEL, **(self.db.get_setting('ai') or {})}

    def enabled(self):
        return bool(self.config().get('api_key'))

    def view(self):
        c = self.config()
        return {'enabled': self.enabled(), 'model': c['model'], 'models': MODELS,
                'key_hint': ('…' + c['api_key'][-4:]) if c.get('api_key') else ''}

    async def check_key(self, key):
        resp = await self.http.get(f'{OPENROUTER}/key', headers={'Authorization': f'Bearer {key}'})
        if resp.status_code != 200:
            raise AssistantError("OpenRouter didn't accept that key. Copy it again from openrouter.ai/keys")

    # ---- Talking ----
    async def chat(self, messages, page='', approve=None):
        """One turn. messages: the conversation so far (OpenAI format, without the system message).
        approve: {'tool_call_id', 'approved'} answering a question the last turn asked.
        Returns {'messages', 'reply', 'pending', 'steps'}: 'pending' is an action waiting for a yes."""
        config = self.config()
        if not config.get('api_key'):
            raise AssistantError('The assistant is off: add an OpenRouter key in Settings')
        mcp = _mcp()
        mcp.token_scope.set('control')
        tools = [{'type': 'function', 'function': {'name': t.name, 'description': t.description,
                                                   'parameters': t.input_schema}}
                 for t in await mcp.server.list_tools()]
        system = mcp.INSTRUCTIONS + '\n\n' + PERSONA.format(
            now=time.strftime('%A %d %B %Y, %H:%M'), page=await self._page(page))
        messages, steps = list(messages), []
        for _ in range(MAX_STEPS):
            waiting = self._unanswered(messages)
            if waiting:
                for call in waiting:
                    name, args = call['function']['name'], self._args(call)
                    answer = approve if approve and approve.get('tool_call_id') == call['id'] else None
                    if name in CONFIRM and not answer:
                        return {'messages': messages, 'reply': None, 'steps': steps,
                                'pending': {'tool_call_id': call['id'], 'tool': name, 'text': await self._describe(name, args)}}
                    if answer and not answer.get('approved'):
                        result = 'They said no: not done. Ask what they would like instead.'
                    else:
                        result, ok = await self._run(name, args)
                        steps.append({'tool': name, 'ok': ok})
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': result[:MAX_RESULT]})
                approve = None
                continue
            message = await self._complete(config, [{'role': 'system', 'content': system}] + messages, tools)
            calls = message.get('tool_calls')
            messages.append({'role': 'assistant', 'content': message.get('content') or (None if calls else ''),
                             **({'tool_calls': calls} if calls else {})})
            if not message.get('tool_calls'):
                return {'messages': messages, 'reply': message.get('content') or '', 'pending': None, 'steps': steps}
        return {'messages': messages, 'reply': "That took more steps than I'm allowed in one go. Shall I carry on?",
                'pending': None, 'steps': steps}

    async def _complete(self, config, messages, tools):
        try:
            resp = await self.http.post(f'{OPENROUTER}/chat/completions', json={
                'model': config['model'], 'messages': messages, 'tools': tools, 'max_tokens': 2000},
                headers={'Authorization': f"Bearer {config['api_key']}",
                         'HTTP-Referer': 'https://cinderwire.co', 'X-Title': 'TouchStream'})
        except httpx.HTTPError as e:
            raise AssistantError(f"Couldn't reach OpenRouter: {e}")
        if resp.status_code == 401:
            raise AssistantError("OpenRouter didn't accept the key: check it in Settings")
        if resp.status_code == 402:
            raise AssistantError('OpenRouter says the account is out of credit: add some at openrouter.ai')
        if resp.status_code != 200:
            raise AssistantError(f"OpenRouter answered {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if data.get('error'):
            raise AssistantError(f"OpenRouter: {data['error'].get('message', data['error'])}")
        return data['choices'][0]['message']

    @staticmethod
    def _unanswered(messages):
        """Tool calls in the last assistant message that have no result yet."""
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]['role'] == 'assistant':
                answered = {m.get('tool_call_id') for m in messages[i + 1:] if m['role'] == 'tool'}
                return [c for c in messages[i].get('tool_calls') or [] if c['id'] not in answered]
        return []

    @staticmethod
    def _args(call):
        try:
            return json.loads(call['function'].get('arguments') or '{}')
        except ValueError:
            return {}

    async def _run(self, name, args):
        try:
            result = await _mcp().server.call_tool(name, args)
            text = ' '.join(getattr(c, 'text', '') for c in result.content)
            return text, not result.is_error
        except Exception as e:
            return f'Error: {e}', False

    async def _describe(self, name, args):
        """The question to ask before an action, in plain words."""
        title = ''
        if args.get('broadcast_id') and str(args['broadcast_id']).isdigit():
            event = _mcp().hub_app.hub.broadcasts.event(int(args['broadcast_id']))
            title = f'"{event["title"]}"' if event else ''
        if name == 'delete_broadcast':
            return (f'Delete {title or "this broadcast"}' + (' and all its upcoming dates' if args.get('apply_to') == 'all_upcoming' else '')
                    + (', with its recordings' if args.get('delete_recordings') else '') + '?')
        if name == 'publish_video':
            return f'Publish the edited video of {title or "this broadcast"} to YouTube ({args.get("privacy", "public")})?'
        if name == 'go_live':
            return f'Go live now with {title or "spoke " + str(args.get("spoke_id", ""))}?'
        if name == 'end_live':
            return f'End {title or "the live stream"} now?'
        return f'{name}?'

    async def _page(self, path):
        """Which page they're on, in words, so 'this' means something."""
        parts = path.strip('#/').split('/')
        hub = _mcp().hub_app.hub
        if parts[0] in ('edit', 'transcript') and len(parts) > 1 and parts[1].isdigit():
            event = hub.broadcasts.event(int(parts[1]))
            what = 'the video editor' if parts[0] == 'edit' else 'the transcript'
            return f'{what} for broadcast {parts[1]} ("{event["title"]}")' if event else what
        if parts[0] == 'spokes' and len(parts) > 1:
            spoke = hub.db.spoke(parts[1])
            return f'the page of spoke {parts[1]} ("{spoke["name"]}")' if spoke else 'a spoke page'
        return {'': 'the Home page', 'schedule': 'the Schedule page', 'spokes': 'the Spokes page',
                'settings': 'Settings'}.get(parts[0], 'the dashboard')
