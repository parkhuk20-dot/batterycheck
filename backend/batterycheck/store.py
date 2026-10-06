import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .auth import hash_password, public_user


def evidence_fingerprint(result):
    # Global catalog revisions and retrieval timestamps are audit evidence, not changes to this product.
    notices = [{k: v for k, v in notice.items() if k not in {'fetched_at', 'checked_at'}}
               for notice in result.get('notices', [])]
    sources = [{key: source.get(key) for key in ('id', 'state', 'freshness')}
               for source in result.get('sources', [])]
    evidence = {'status': result.get('status'), 'notices': sorted(notices, key=lambda value: value.get('id', '')),
                'sources': sorted(sources, key=lambda value: value.get('id', ''))}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, path, remote=None):
        self.path = Path(path)
        # (url, token[, transport]) of a Turso database; without it the local SQLite file is used.
        url = os.environ.get('TURSO_DATABASE_URL')
        self.remote = remote or ((url, os.environ.get('TURSO_AUTH_TOKEN', '')) if url else None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, privacy_consent_at TEXT NOT NULL,
                    privacy_version TEXT NOT NULL DEFAULT '2026-10-02',
                    notification_consent INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY, input_json TEXT NOT NULL,
                    action_state TEXT NOT NULL DEFAULT 'unconfirmed', created_at TEXT NOT NULL,
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    input_json TEXT NOT NULL, result_json TEXT NOT NULL, checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    state TEXT NOT NULL, recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    evidence_hash TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                    created_at TEXT NOT NULL, read_at TEXT,
                    UNIQUE(product_id,evidence_hash)
                );
                CREATE TABLE IF NOT EXISTS assistant_messages (
                    id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    rating INTEGER NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL
                );
            ''')
            # Legacy demo rows stay ownerless and inaccessible; never assign them to a new account.
            if 'user_id' not in {row['name'] for row in db.execute('PRAGMA table_info(products)')}:
                db.execute('ALTER TABLE products ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE')
            if 'ai_consent_at' not in {row['name'] for row in db.execute('PRAGMA table_info(users)')}:
                db.execute('ALTER TABLE users ADD COLUMN ai_consent_at TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS products_owner ON products(user_id)')
            db.execute('CREATE INDEX IF NOT EXISTS assistant_owner ON assistant_messages(user_id,id)')
            db.execute('CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(user_id)')
            db.execute('CREATE INDEX IF NOT EXISTS notifications_owner ON notifications(user_id,created_at)')

    @contextmanager
    def connection(self):
        if self.remote:
            from .hrana import Connection
            db = Connection(*self.remote)
            try:
                db.execute('PRAGMA foreign_keys=ON')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
            return
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                yield db
        finally:
            db.close()

    def register(self, username, password, notification_consent, now):
        user_id = str(uuid4())
        password_hash = hash_password(password)
        with self.connection() as db:
            db.execute('''INSERT INTO users(id,username,password_hash,created_at,privacy_consent_at,notification_consent)
                          VALUES (?,?,?,?,?,?)''', (user_id, username, password_hash, now, now, int(notification_consent)))
            return public_user(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())

    def preferences(self, user_id, notification_consent=None, ai_consent=None, now=None):
        with self.connection() as db:
            if notification_consent is not None:
                db.execute('UPDATE users SET notification_consent=? WHERE id=?', (int(notification_consent), user_id))
            if ai_consent is not None:
                db.execute('UPDATE users SET ai_consent_at=? WHERE id=?', (now if ai_consent else None, user_id))
            return public_user(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())

    def assistant_history(self, user_id, limit=None):
        with self.connection() as db:
            rows = db.execute('SELECT role,content,sources_json,created_at FROM assistant_messages WHERE user_id=? ORDER BY id DESC'
                              + (' LIMIT ?' if limit else ''), (user_id, limit) if limit else (user_id,)).fetchall()
            return [{'role': row['role'], 'content': row['content'], 'sources': json.loads(row['sources_json']),
                     'created_at': row['created_at']} for row in reversed(rows)]

    def assistant_append(self, user_id, question, answer, sources, now):
        with self.connection() as db:
            # Skip silently if consent was withdrawn or the account removed while the answer was generated.
            if not db.execute('SELECT 1 FROM users WHERE id=? AND ai_consent_at IS NOT NULL', (user_id,)).fetchone():
                return
            db.executemany('INSERT INTO assistant_messages(user_id,role,content,sources_json,created_at) VALUES (?,?,?,?,?)',
                           [(user_id, 'user', question, '[]', now),
                            (user_id, 'assistant', answer, json.dumps(sources, ensure_ascii=False), now)])

    def assistant_clear(self, user_id):
        with self.connection() as db:
            db.execute('DELETE FROM assistant_messages WHERE user_id=?', (user_id,))

    def add_feedback(self, user_id, rating, comment, now):
        with self.connection() as db:
            db.execute('INSERT INTO feedback(user_id,rating,comment,created_at) VALUES (?,?,?,?)', (user_id, rating, comment, now))

    def delete_account(self, user_id):
        with self.connection() as db:
            db.execute('DELETE FROM users WHERE id=?', (user_id,))

    def get(self, product_id, user_id, db=None):
        if db is None:
            with self.connection() as connection:
                return self.get(product_id, user_id, connection)
        row = db.execute('SELECT * FROM products WHERE id=? AND user_id=?', (product_id, user_id)).fetchone()
        if row is None:
            return None
        latest = db.execute('SELECT result_json FROM checks WHERE product_id=? ORDER BY id DESC LIMIT 1',
                            (product_id,)).fetchone()
        return {'id': row['id'], 'input': json.loads(row['input_json']),
                'action_state': row['action_state'], 'created_at': row['created_at'],
                'latest_result': json.loads(latest['result_json']) if latest else None,
                'action_basis': '사용자 자기보고 · 회수 인증이나 사용 재개 허가가 아님'}

    def list(self, user_id):
        with self.connection() as db:
            ids = db.execute('SELECT id FROM products WHERE user_id=? ORDER BY created_at DESC, rowid DESC', (user_id,)).fetchall()
            return [self.get(row['id'], user_id, db) for row in ids]

    def save(self, product, result, user_id, product_id=None, notify=False, *, expected_input=None, consent_required=False):
        product_json = product.model_dump_json()
        product_values = json.loads(product_json)
        with self.connection() as db:
            # Serialize the ownership/consent check and write against account removal or concurrent edits.
            db.execute('BEGIN IMMEDIATE')
            if consent_required and not db.execute('SELECT 1 FROM users WHERE id=? AND notification_consent=1', (user_id,)).fetchone():
                return None
            previous = None
            if product_id:
                previous = self.get(product_id, user_id, db)
                if not previous or (expected_input is not None and previous['input'] != expected_input):
                    return None
                db.execute('UPDATE products SET input_json=? WHERE id=? AND user_id=?', (product_json, product_id, user_id))
                # A newly changed product or evidence needs a fresh user action acknowledgement.
                identity_fields = ('brand', 'model', 'manufactured_on', 'sale_region', 'lot', 'demo')
                identity_changed = any(previous['input'].get(k) != product_values.get(k) for k in identity_fields)
                if identity_changed or evidence_fingerprint(previous['latest_result']) != evidence_fingerprint(result):
                    db.execute("UPDATE products SET action_state='unconfirmed' WHERE id=?", (product_id,))
                    if previous['action_state'] != 'unconfirmed':
                        db.execute('INSERT INTO actions(product_id,state,recorded_at) VALUES (?,?,?)',
                                   (product_id, 'unconfirmed', result['checked_at']))
            else:
                product_id = str(uuid4())
                db.execute('INSERT INTO products(id,input_json,created_at,user_id) VALUES (?,?,?,?)',
                           (product_id, product_json, result['checked_at'], user_id))
            db.execute('INSERT INTO checks(product_id,input_json,result_json,checked_at) VALUES (?,?,?,?)',
                       (product_id, product_json, json.dumps(result, ensure_ascii=False), result['checked_at']))
            if notify and previous and result.get('catalog_hash') and not product.demo:
                consent = db.execute('SELECT notification_consent FROM users WHERE id=?', (user_id,)).fetchone()
                fingerprint = evidence_fingerprint(result)
                if consent and consent[0] and fingerprint != evidence_fingerprint(previous['latest_result']):
                    db.execute('''INSERT OR IGNORE INTO notifications
                               (id,user_id,product_id,evidence_hash,title,body,created_at) VALUES (?,?,?,?,?,?,?)''',
                               (str(uuid4()), user_id, product_id, fingerprint,
                                f"{product.brand} {product.model} · 확인 결과 변경",
                                f"{result.get('label', '재확인 필요')}. {result.get('next_action', '')}", result['checked_at']))
            return self.get(product_id, user_id, db)

    def action(self, product_id, user_id, state, now):
        with self.connection() as db:
            if not self.get(product_id, user_id, db):
                return None
            db.execute('UPDATE products SET action_state=? WHERE id=? AND user_id=?', (state, product_id, user_id))
            db.execute('INSERT INTO actions(product_id,state,recorded_at) VALUES (?,?,?)', (product_id, state, now))
            return self.get(product_id, user_id, db)

    def history(self, product_id, user_id, db=None):
        if db is None:
            with self.connection() as connection:
                return self.history(product_id, user_id, connection)
        if not self.get(product_id, user_id, db):
            return None
        checks = db.execute('SELECT * FROM checks WHERE product_id=? ORDER BY id DESC', (product_id,)).fetchall()
        actions = db.execute('SELECT state,recorded_at FROM actions WHERE product_id=? ORDER BY id DESC', (product_id,)).fetchall()
        return {'checks': [{'id': row['id'], 'input': json.loads(row['input_json']),
                            'result': json.loads(row['result_json'])} for row in checks],
                'actions': [dict(row) for row in actions]}

    def delete(self, product_id, user_id):
        with self.connection() as db:
            return bool(db.execute('DELETE FROM products WHERE id=? AND user_id=?', (product_id, user_id)).rowcount)

    def consenting_users(self):
        with self.connection() as db:
            return [row['id'] for row in db.execute('SELECT id FROM users WHERE notification_consent=1')]

    def notifications(self, user_id):
        with self.connection() as db:
            return [dict(row) for row in db.execute('''SELECT id,product_id,title,body,created_at,read_at
                          FROM notifications WHERE user_id=? ORDER BY created_at DESC,rowid DESC''', (user_id,))]

    def read_notification(self, notification_id, user_id, now):
        with self.connection() as db:
            changed = db.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=? AND user_id=?',
                                 (now, notification_id, user_id)).rowcount
            if not changed:
                return None
            return dict(db.execute('SELECT id,product_id,title,body,created_at,read_at FROM notifications WHERE id=?',
                                   (notification_id,)).fetchone())

    def export(self, user_id):
        with self.connection() as db:
            # Explicit transaction provides one consistent personal-data snapshot.
            db.execute('BEGIN')
            user = public_user(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())
            products = [self.get(row['id'], user_id, db) for row in db.execute('SELECT id FROM products WHERE user_id=?', (user_id,))]
            histories = {product['id']: self.history(product['id'], user_id, db) for product in products}
            notifications = [dict(row) for row in db.execute('SELECT id,product_id,title,body,created_at,read_at FROM notifications WHERE user_id=?', (user_id,))]
            assistant = [{'role': row['role'], 'content': row['content'], 'created_at': row['created_at']} for row in db.execute(
                'SELECT role,content,created_at FROM assistant_messages WHERE user_id=? ORDER BY id', (user_id,))]
            feedback = [dict(row) for row in db.execute('SELECT rating,comment,created_at FROM feedback WHERE user_id=?', (user_id,))]
            return {'account': user, 'products': products, 'history': histories, 'notifications': notifications,
                    'assistant_messages': assistant, 'feedback': feedback}
