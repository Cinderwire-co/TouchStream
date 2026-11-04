"""Admin password, session cookies (one admin account per hub) and API tokens"""
import hashlib
import hmac
import secrets
import time

SESSION_COOKIE = 'touchstream_session'
SESSION_DAYS = 30


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f'scrypt${salt.hex()}${digest.hex()}'


def check_password(password, stored):
    try:
        _, salt, digest = stored.split('$')
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(candidate.hex(), digest)
    except Exception:
        return False


SCOPES = ('read', 'control', 'admin')   # each allows what the ones before it do
TOKEN_PREFIX = 'ts_'


class Tokens:
    """API tokens for scripts and AI agents, made in Settings. Only a hash is kept, so a token is
    shown once. 'read' looks; 'control' also schedules, goes live, edits and publishes; 'admin'
    also changes settings, passwords and YouTube's sign-in."""

    def __init__(self, db):
        self.db = db

    def create(self, name, scope):
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        self.db.run('INSERT INTO api_tokens (name, scope, hash, hint, created_at) VALUES (?, ?, ?, ?, ?)',
                    name, scope, _digest(token), token[-4:], time.time())
        return token

    def list(self):
        return self.db.all('SELECT id, name, scope, hint, created_at, last_used_at FROM api_tokens ORDER BY id')

    def revoke(self, token_id):
        self.db.run('DELETE FROM api_tokens WHERE id = ?', token_id)

    def check(self, token):
        """The token's row if it's one of ours, else None."""
        if not token.startswith(TOKEN_PREFIX):
            return None
        row = self.db.one('SELECT * FROM api_tokens WHERE hash = ?', _digest(token))
        if row and time.time() - (row['last_used_at'] or 0) > 60:
            self.db.run('UPDATE api_tokens SET last_used_at = ? WHERE id = ?', time.time(), row['id'])
        return row


def _digest(token):
    # Tokens are long and random, so a plain hash is enough (no salt or slow hash needed)
    return hashlib.sha256(token.encode()).hexdigest()


class Sessions:
    """Signed, expiring session tokens. Changing the password invalidates them all."""

    def __init__(self, db):
        self.db = db
        if not db.get_setting('session_secret'):
            db.set_setting('session_secret', secrets.token_hex(32))

    def _sign(self, expires):
        # Mixing in the password hash means a password change logs everyone out
        key = (self.db.get_setting('session_secret') + (self.db.get_setting('admin_password') or '')).encode()
        return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()

    def issue(self):
        expires = int(time.time()) + SESSION_DAYS * 86400
        return f'{expires}.{self._sign(expires)}'

    def valid(self, token):
        try:
            expires, signature = token.split('.')
            return int(expires) > time.time() and hmac.compare_digest(signature, self._sign(int(expires)))
        except Exception:
            return False
