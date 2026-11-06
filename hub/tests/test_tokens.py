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


if __name__ == '__main__':
    unittest.main()
