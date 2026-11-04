"""The assistant's conversation loop: tools run, risky ones wait for a yes, and a no is passed on"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

from touchstream_hub import app  # noqa: F401  (loads the hub's modules in its order)
from touchstream_hub import assistant, mcp_server


def call(id_, name, **args):
    return {'id': id_, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


class Loop(unittest.TestCase):
    def setUp(self):
        # The model: look at the schedule, then try to delete broadcast 7, then sum up
        self.replies = [
            {'role': 'assistant', 'content': None, 'tool_calls': [call('a', 'list_broadcasts')]},
            {'role': 'assistant', 'content': None, 'tool_calls': [call('b', 'delete_broadcast', broadcast_id='7')]},
            {'role': 'assistant', 'content': 'Done: it is deleted.'},
        ]
        self.sent = []

        def openrouter(request):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': self.replies.pop(0)}]})
        self.bot = assistant.Assistant(db=SimpleNamespace(get_setting=lambda k: {'api_key': 'sk-test'} if k == 'ai' else None))
        self.bot.http = httpx.AsyncClient(transport=httpx.MockTransport(openrouter))
        self.ran = []

        async def call_tool(name, args):
            self.ran.append((name, args))
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({'ok': name}))], is_error=False)

        async def list_tools():
            return [SimpleNamespace(name=n, description=n, input_schema={'type': 'object'}) for n in ('list_broadcasts', 'delete_broadcast')]
        self.patches = [mock.patch.object(mcp_server.server, 'call_tool', call_tool),
                        mock.patch.object(mcp_server.server, 'list_tools', list_tools),
                        mock.patch.object(assistant.Assistant, '_page', lambda self, p: asyncio.sleep(0, 'the Schedule page')),
                        mock.patch.object(assistant.Assistant, '_describe', lambda self, n, a: asyncio.sleep(0, 'Delete it?'))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_chat(self, *args):
        return asyncio.run(self.bot.chat(*args))

    def test_asks_before_deleting_then_does_it(self):
        first = self.run_chat([{'role': 'user', 'content': 'delete the next one'}], '#/schedule')
        self.assertEqual(first['pending']['tool_call_id'], 'b')
        self.assertEqual(self.ran, [('list_broadcasts', {})])            # looked, but didn't delete yet
        self.assertIn('Schedule page', self.sent[0]['messages'][0]['content'])
        done = self.run_chat(first['messages'], '#/schedule', {'tool_call_id': 'b', 'approved': True})
        self.assertEqual(self.ran[-1], ('delete_broadcast', {'broadcast_id': '7'}))
        self.assertEqual(done['reply'], 'Done: it is deleted.')

    def test_a_no_is_passed_on_and_nothing_runs(self):
        self.replies[2] = {'role': 'assistant', 'content': 'OK, I left it.'}
        first = self.run_chat([{'role': 'user', 'content': 'delete the next one'}])
        done = self.run_chat(first['messages'], '', {'tool_call_id': 'b', 'approved': False})
        self.assertNotIn('delete_broadcast', [n for n, _ in self.ran])
        self.assertIn('said no', self.sent[-1]['messages'][-1]['content'])
        self.assertEqual(done['reply'], 'OK, I left it.')


if __name__ == '__main__':
    unittest.main()
