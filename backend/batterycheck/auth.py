"""Local accounts and opaque, server-side sessions; no external identity service."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict

SESSION_COOKIE = 'batterycheck_session'
SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}'


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Bounded, process-local abuse protection for the single-worker deployment."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key, limit, window):
        now = time.monotonic()
        with self.lock:
            attempts = [value for value in self.entries.pop(key, []) if value > now - window]
            accepted = len(attempts) < limit
            if accepted:
                attempts.append(now)
            self.entries[key] = attempts
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)
            return accepted


def public_user(row):
    return {'id': row['id'], 'username': row['username'], 'created_at': row['created_at'],
            'privacy_consent': True, 'privacy_consent_at': row['privacy_consent_at'],
            'privacy_version': row['privacy_version'],
            'notification_consent': bool(row['notification_consent']),
            'ai_consent': bool(row['ai_consent_at']), 'ai_consent_at': row['ai_consent_at']}


class Auth:
    def __init__(self, store):
        self.store = store
        # Unknown users still incur the password KDF to reduce username timing leaks.
        self.dummy_hash = hash_password(secrets.token_urlsafe(32))

    def create_session(self, user_id, previous_token=None):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.store.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
            if previous_token:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (token_digest(previous_token),))
            db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)',
                       (token_digest(token), user_id, now + SESSION_SECONDS))
        return token

    def authenticate(self, username, password):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            valid = verify_password(password, row['password_hash'] if row else self.dummy_hash)
            return public_user(row) if row and valid else None

    def current(self, token):
        if not token or len(token) > 128:
            return None
        with self.store.connection() as db:
            row = db.execute('''SELECT users.* FROM users JOIN sessions ON users.id=sessions.user_id
                                WHERE sessions.token_hash=? AND sessions.expires_at>?''',
                             (token_digest(token), time.time())).fetchone()
            return public_user(row) if row else None

    def logout(self, token):
        if token:
            with self.store.connection() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (token_digest(token),))
