"""Runs the account/product flow through the Turso adapter against an in-process Hrana stand-in."""
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from batterycheck.main import create_app
from batterycheck.official import OfficialService
from batterycheck.store import Store

PASSWORD = 'test password 123'
PRODUCT = {'brand': 'Anker', 'model': 'A1263', 'demo': False, 'model_confirmed': True,
           'product_kind': 'power_bank', 'signs_answered': True}


class FakeHrana:
    """One sqlite3 connection per stream (baton), the way the real server keeps a transaction open."""
    def __init__(self, path):
        self.path, self.streams, self.counter = path, {}, 0

    def __call__(self, request):
        body = json.loads(request.content)
        assert request.headers['authorization'] == 'Bearer test-token' and request.url.path == '/v2/pipeline'
        baton = body['baton']
        if baton is None:
            self.counter += 1
            baton = f'stream-{self.counter}'
            self.streams[baton] = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        db, results = self.streams[baton], []
        for item in body['requests']:
            try:
                if item['type'] == 'close':
                    self.streams.pop(baton).close()
                    baton = None
                    results.append({'type': 'ok', 'response': {'type': 'close'}})
                elif item['type'] == 'sequence':
                    db.executescript(item['sql'])
                    results.append({'type': 'ok', 'response': {'type': 'sequence'}})
                else:
                    args = [None if a['type'] == 'null' else int(a['value']) if a['type'] == 'integer' else a['value']
                            for a in item['stmt'].get('args', [])]
                    cursor = db.execute(item['stmt']['sql'], args)
                    rows = [[{'type': 'null'} if v is None else {'type': 'integer', 'value': str(v)} if isinstance(v, int)
                             else {'type': 'float', 'value': v} if isinstance(v, float) else {'type': 'text', 'value': v}
                             for v in row] for row in cursor.fetchall()]
                    results.append({'type': 'ok', 'response': {'type': 'execute', 'result': {
                        'cols': [{'name': d[0]} for d in cursor.description or []], 'rows': rows,
                        'affected_row_count': max(cursor.rowcount, 0)}}})
            except sqlite3.Error as exc:
                results.append({'type': 'error', 'error': {'message': str(exc)}})
        return httpx.Response(200, json={'baton': baton, 'results': results})


class RemoteStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.server = FakeHrana(root / 'remote.sqlite3')
        self.addCleanup(lambda: [db.close() for db in self.server.streams.values()])
        self.app = create_app(root / 'unused.sqlite3', official_service=OfficialService(root / 'official'))
        remote = ('libsql://example.turso.io', 'test-token', httpx.MockTransport(self.server))
        self.app.state.store = self.app.state.auth.store = self.app.state.assistant.store = Store(root / 'unused.sqlite3', remote)

    def test_accounts_products_and_cascade_delete_work_over_the_remote_store(self):
        owner, other = TestClient(self.app), TestClient(self.app)
        body = {'username': 'remote', 'password': PASSWORD, 'privacy_consent': True}
        self.assertEqual(owner.post('/auth/register', json=body).status_code, 201)
        self.assertEqual(other.post('/auth/register', json=body).status_code, 409)
        self.assertEqual(other.post('/auth/login', json={'username': 'remote', 'password': PASSWORD}).status_code, 200)
        saved = owner.post('/products', json=PRODUCT)
        self.assertEqual(saved.status_code, 201, saved.text)
        product_id = saved.json()['id']
        self.assertEqual(owner.put(f'/products/{product_id}', json=PRODUCT | {'sale_region': 'KR'}).status_code, 200)
        self.assertEqual(owner.patch(f'/products/{product_id}/action', json={'state': 'checking'}).json()['action_state'], 'checking')
        self.assertEqual(len(other.get('/products').json()), 1)
        self.assertEqual(len(owner.get(f'/products/{product_id}/history').json()['checks']), 2)
        self.assertTrue(owner.patch('/auth/preferences', json={'ai_consent': True}).json()['ai_consent'])
        self.assertEqual(owner.post('/feedback', json={'rating': 5, 'comment': 'ok'}).status_code, 201)
        self.assertEqual(owner.get('/auth/export').json()['feedback'][0]['rating'], 5)
        self.assertEqual(owner.request('DELETE', '/auth/account', json={'password': PASSWORD}).status_code, 204)
        self.assertEqual(other.get('/auth/me').status_code, 401)
        with closing(sqlite3.connect(self.server.path)) as db:
            self.assertEqual([db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                              for t in ('users', 'sessions', 'products', 'checks', 'actions', 'feedback')], [0] * 6)
        self.assertEqual(self.server.streams, {})

    def test_a_failed_write_rolls_back(self):
        store = self.app.state.store
        with self.assertRaises(sqlite3.IntegrityError):
            with store.connection() as db:
                db.execute("INSERT INTO users(id,username,password_hash,created_at,privacy_consent_at) VALUES ('a','x','h','t','t')")
                db.execute("INSERT INTO users(id,username,password_hash,created_at,privacy_consent_at) VALUES ('b','x','h','t','t')")
        with store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM users').fetchone()['n'], 0)


if __name__ == '__main__':
    unittest.main()
