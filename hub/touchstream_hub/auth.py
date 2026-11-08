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


ACCESS_SECONDS = 600            # to approve a request, and then to collect its token
CODE_LETTERS = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'   # none that look alike
MAX_WAITING, MAX_WAITING_FROM_ONE = 10, 3


class TooManyRequests(Exception):
    pass


class AccessRequests:
    """An agent asking for an API token, so nobody copies one by hand. It asks (with no token), the
    dashboard shows the request with a code the agent also shows its person, and once it's approved
    the agent collects its token, once. A request nobody answers, or a token nobody collects, lapses
    after ten minutes. The token itself is only ever in memory, until it's collected."""

    def __init__(self, db, tokens):
        self.db, self.tokens = db, tokens
        self.ready = {}   # request id -> its token, approved and not yet collected

    def ask(self, name, scope, ip):
        self.expire()
        waiting = self.db.all("SELECT ip FROM access_requests WHERE status = 'pending'")
        if len(waiting) >= MAX_WAITING or sum(w['ip'] == ip for w in waiting) >= MAX_WAITING_FROM_ONE:
            raise TooManyRequests()
        code = ''.join(secrets.choice(CODE_LETTERS) for _ in range(6))
        request_id, now = secrets.token_urlsafe(24), time.time()
        self.db.run("INSERT INTO access_requests (id, name, scope, code, ip, status, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)", request_id, name, scope, f'{code[:3]}-{code[3:]}',
                    ip, now, now + ACCESS_SECONDS)
        return self.get(request_id)

    def get(self, request_id):
        return self.db.one('SELECT * FROM access_requests WHERE id = ?', request_id)

    def waiting(self):
        """The requests for the dashboard to answer, oldest first."""
        self.expire()
        return self.db.all("SELECT id, name, scope, code, ip, created_at, expires_at FROM access_requests "
                           "WHERE status = 'pending' ORDER BY created_at")

    def approve(self, request_id, scope):
        self.expire()
        row = self.get(request_id)
        if not row or row['status'] != 'pending':
            return None
        token = self.tokens.create(row['name'], scope)
        token_id = self.db.one('SELECT id FROM api_tokens WHERE hash = ?', _digest(token))['id']
        self.ready[request_id] = token
        self.db.run("UPDATE access_requests SET status = 'approved', scope = ?, token_id = ?, expires_at = ? "
                    "WHERE id = ?", scope, token_id, time.time() + ACCESS_SECONDS, request_id)
        return self.get(request_id)

    def deny(self, request_id):
        self.db.run("UPDATE access_requests SET status = 'denied' WHERE id = ? AND status = 'pending'", request_id)

    def collect(self, request_id):
        """The token, the first time an approved request is asked about; None after that."""
        self.expire()
        token = self.ready.pop(request_id, None)
        if token:
            self.db.run("UPDATE access_requests SET status = 'collected' WHERE id = ?", request_id)
        return token

    def expire(self):
        now = time.time()
        self.db.run("UPDATE access_requests SET status = 'expired' WHERE status = 'pending' AND expires_at < ?", now)
        # Approved but never collected (or the hub restarted in between, losing it): it's revoked
        for row in self.db.all("SELECT id, token_id, expires_at FROM access_requests WHERE status = 'approved'"):
            if row['expires_at'] < now or row['id'] not in self.ready:
                self.ready.pop(row['id'], None)
                self.tokens.revoke(row['token_id'])
                self.db.run("UPDATE access_requests SET status = 'expired' WHERE id = ?", row['id'])
        self.db.run('DELETE FROM access_requests WHERE created_at < ?', now - 86400)


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
