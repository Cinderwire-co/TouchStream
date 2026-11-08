"""API tokens: shown once, kept as a hash, revocable; and which routes each access level reaches"""
import shutil
import tempfile
import unittest
from pathlib import Path

from touchstream_hub import auth
from touchstream_hub.db import Database


class Tokens(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        self.tokens = auth.Tokens(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_made_checked_and_revoked(self):
        token = self.tokens.create('n8n', 'control')
        self.assertTrue(token.startswith('ts_'))
        self.assertNotIn(token, str(self.db.all('SELECT * FROM api_tokens')))   # only its hash is kept
        row = self.tokens.check(token)
        self.assertEqual((row['name'], row['scope']), ('n8n', 'control'))
        self.assertIsNotNone(self.tokens.check(token)['last_used_at'])
        self.assertIsNone(self.tokens.check(token[:-1] + 'x'))
        self.assertIsNone(self.tokens.check('not-a-token'))
        self.tokens.revoke(row['id'])
        self.assertIsNone(self.tokens.check(token))

    def test_what_needs_admin(self):
        from touchstream_hub.app import ADMIN_ONLY
        for path in ('/api/settings', '/api/retention', '/api/youtube/client', '/api/youtube/channels/1', '/api/spokes'):
            self.assertTrue(ADMIN_ONLY.match(path), path)
        for path in ('/api/events', '/api/retention/preview', '/api/spokes/x/mode', '/api/edits/1/publish'):
            self.assertFalse(ADMIN_ONLY.match(path), path)



class AccessRequests(unittest.TestCase):
    """An agent asks for a token, the dashboard approves it, and the agent collects it once"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        self.tokens = auth.Tokens(self.db)
        self.access = auth.AccessRequests(self.db, self.tokens)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_approved_then_collected_once(self):
        r = self.access.ask('Claude Code', 'admin', '192.168.1.20')
        self.assertRegex(r['code'], r'^[A-Z2-9]{3}-[A-Z2-9]{3}$')
        self.assertEqual([w['id'] for w in self.access.waiting()], [r['id']])
        self.assertIsNone(self.access.collect(r['id']))          # nothing yet
        self.access.approve(r['id'], 'control')                   # the dashboard gave it less than it asked
        token = self.access.collect(r['id'])
        self.assertEqual(self.tokens.check(token)['scope'], 'control')
        self.assertIsNone(self.access.collect(r['id']))          # only once
        self.assertEqual(self.access.get(r['id'])['status'], 'collected')
        self.assertEqual(self.access.waiting(), [])

    def test_denied(self):
        r = self.access.ask('Something odd', 'admin', '10.0.0.9')
        self.access.deny(r['id'])
        self.assertIsNone(self.access.approve(r['id'], 'admin'))
        self.assertEqual(self.access.get(r['id'])['status'], 'denied')
        self.assertEqual(self.tokens.list(), [])

    def test_lapses(self):
        asked = self.access.ask('Nobody answered', 'read', '10.0.0.9')
        approved = self.access.ask('Nobody collected', 'read', '10.0.0.9')
        self.access.approve(approved['id'], 'read')
        self.db.run('UPDATE access_requests SET expires_at = 0')
        self.assertIsNone(self.access.collect(approved['id']))
        self.assertEqual(self.access.get(asked['id'])['status'], 'expired')
        self.assertEqual(self.tokens.list(), [])                  # the uncollected token is revoked

    def test_a_restart_revokes_a_token_not_collected(self):
        r = self.access.ask('Claude Code', 'read', '10.0.0.9')
        self.access.approve(r['id'], 'read')
        restarted = auth.AccessRequests(self.db, self.tokens)     # the token was only in memory
        self.assertIsNone(restarted.collect(r['id']))
        self.assertEqual(self.tokens.list(), [])

    def test_how_many_can_wait(self):
        for _ in range(auth.MAX_WAITING_FROM_ONE):
            self.access.ask('Agent', 'read', '10.0.0.9')
        with self.assertRaises(auth.TooManyRequests):
            self.access.ask('Agent', 'read', '10.0.0.9')
        for n in range(auth.MAX_WAITING - auth.MAX_WAITING_FROM_ONE):
            self.access.ask('Agent', 'read', f'10.0.1.{n}')
        with self.assertRaises(auth.TooManyRequests):
            self.access.ask('Agent', 'read', '10.0.2.1')

    def test_the_guide_has_every_tool(self):
        import asyncio
        from touchstream_hub import agent_guide, app, mcp_server  # noqa: F401 (app sets up the server)
        names = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
        self.assertEqual(names, set(agent_guide.TOOLS))
        page = agent_guide.guide('http://192.168.1.20:8080/')
        self.assertIn('http://192.168.1.20:8080/api/access', page)
        self.assertNotIn('ts_', page.replace('Bearer ts_...', ''))  # never a real token


if __name__ == '__main__':
    unittest.main()
