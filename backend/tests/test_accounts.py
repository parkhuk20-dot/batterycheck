import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from batterycheck.auth import SESSION_COOKIE, token_digest
from batterycheck.main import create_app
from batterycheck.models import ProductInput
from batterycheck.worker import once, refresh_lock

NOW = datetime(2026, 10, 2, 7, tzinfo=timezone.utc)
PASSWORD = 'test password 123'
REAL = {'brand': 'ANKER', 'model': 'A1263', 'demo': False, 'model_confirmed': True,
        'product_kind': 'power_bank', 'signs_answered': True}


class OfficialFixture:
    def __init__(self):
        self.version = 'a' * 64
        self.fetch_number = 0
        self.sync_calls = 0

    def status(self):
        return {'state': 'complete', 'catalog_hash': self.version, 'sources': []}

    def sync(self):
        self.sync_calls += 1
        self.fetch_number += 1
        return self.status()

    def evaluate(self, product):
        return {'checked_at': NOW.isoformat(), 'synthetic': False, 'catalog_hash': self.version,
                'status': 'needs_information', 'label': '공식 공지 추가 확인 필요', 'coverage_complete': False,
                'notices': [{'id': 'cpsc-fixture-1', 'title': 'Public notice fixture', 'outcome': 'pending_review',
                             'content_hash': self.version, 'fetched_at': str(self.fetch_number)}],
                'next_action': '공식 공지의 판매지역과 대상 조건을 확인하세요.', 'questions': [], 'sources': [],
                'limitations': '테스트 fixture', 'risk': {'level': 'reported_none'}}


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'accounts.sqlite3'
        self.service = OfficialFixture()
        self.app = create_app(self.path, clock=lambda: NOW, official_service=self.service)
        self.client = TestClient(self.app)
        self.other = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.other.close()
        self.temp.cleanup()

    def register(self, client=None, username='alice', consent=False, password=PASSWORD):
        client = client or self.client
        response = client.post('/auth/register', json={'username': username, 'password': password,
                               'privacy_consent': True, 'notification_consent': consent})
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def save(self, client=None):
        response = (client or self.client).post('/products', json=REAL)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def sync(self):
        self.app.state.limiter.entries.clear()
        response = self.client.post('/sources/sync')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_registration_cookie_session_password_storage_and_logout(self):
        response = self.register(username='Alice')
        self.assertEqual(response.json()['username'], 'alice')
        self.assertFalse(response.json()['notification_consent'])
        cookie = response.headers['set-cookie'].lower()
        for attr in ['httponly', 'samesite=strict', 'path=/', 'max-age=604800']:
            self.assertIn(attr, cookie)
        session = self.client.cookies.get(SESSION_COOKIE)
        self.assertEqual(self.client.get('/auth/me').json()['id'], response.json()['id'])
        with self.app.state.store.connection() as db:
            stored = db.execute('SELECT password_hash FROM users').fetchone()[0]
            self.assertNotIn(PASSWORD, stored)
            self.assertTrue(stored.startswith('pbkdf2_sha256$600000$'))
            self.assertEqual(db.execute('SELECT token_hash FROM sessions').fetchone()[0], token_digest(session))
        self.assertEqual(self.client.post('/auth/logout').status_code, 204)
        self.assertEqual(self.client.get('/auth/me').status_code, 401)
        self.other.cookies.set(SESSION_COOKIE, session)
        self.assertEqual(self.other.get('/auth/me').status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'username': 'ALICE', 'password': PASSWORD}).status_code, 200)
        self.assertNotEqual(self.client.cookies.get(SESSION_COOKIE), session)

    def test_registration_consent_validation_and_duplicates(self):
        for change in [{'password': 'short'}, {'privacy_consent': False}, {'privacy_consent': 'true'},
                       {'notification_consent': 'true'}, {'username': '<script>'}, {'extra': 'private'}]:
            with self.subTest(change=change):
                response = self.client.post('/auth/register', json={'username': 'alice', 'password': PASSWORD,
                                           'privacy_consent': True} | change)
                self.assertEqual(response.status_code, 422)
        self.register()
        duplicate = self.other.post('/auth/register', json={'username': 'ALICE', 'password': PASSWORD, 'privacy_consent': True})
        self.assertEqual(duplicate.status_code, 409)

    def test_password_is_not_trimmed(self):
        self.register(password='  password with spaces  ')
        self.client.post('/auth/logout')
        self.assertEqual(self.client.post('/auth/login', json={'username': 'alice', 'password': 'password with spaces'}).status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'username': 'alice', 'password': '  password with spaces  '}).status_code, 200)

    def test_session_expiry_and_tampering(self):
        self.register()
        original = self.client.cookies.get(SESSION_COOKIE)
        self.other.cookies.set(SESSION_COOKIE, original + 'changed')
        self.assertEqual(self.other.get('/auth/me').status_code, 401)
        with self.app.state.store.connection() as db:
            db.execute('UPDATE sessions SET expires_at=0')
        self.assertEqual(self.client.get('/auth/me').status_code, 401)

    def test_authentication_throttles_password_attempts(self):
        self.register()
        for _ in range(7):
            self.assertEqual(self.other.post('/auth/login', json={'username': 'alice', 'password': 'wrong'}).status_code, 401)
        blocked = self.other.post('/auth/login', json={'username': 'alice', 'password': 'wrong'})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers['retry-after'], '300')

    def test_anonymous_protected_endpoints_and_public_read_endpoints(self):
        for method, path, body in [('get', '/products', None), ('post', '/products', REAL),
                                   ('get', '/products/example/history', None), ('put', '/products/example', REAL),
                                   ('delete', '/products/example', None), ('patch', '/products/example/action', {'state': 'checking'}),
                                   ('get', '/notifications', None), ('patch', '/notifications/x/read', None),
                                   ('post', '/sources/sync', None), ('post', '/identify', {'image_base64': 'abc'}),
                                   ('get', '/auth/export', None), ('patch', '/auth/preferences', {'notification_consent': True})]:
            with self.subTest(path=path):
                kwargs = {'json': body} if body is not None else {}
                self.assertEqual(self.client.request(method, path, **kwargs).status_code, 401)
        for path in ['/health', '/meta', '/sources/status']:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.post('/checks', json=REAL).status_code, 200)
        self.assertEqual(self.client.post('/risk', json={'signs': ['smoke'], 'signs_answered': True}).status_code, 200)

    def test_every_product_operation_is_isolated(self):
        self.register()
        self.register(self.other, 'bob')
        saved = self.save()
        key = saved['id']
        self.assertEqual(self.other.get('/products').json(), [])
        for method, path, body in [('put', f'/products/{key}', REAL), ('get', f'/products/{key}/history', None),
                                   ('patch', f'/products/{key}/action', {'state': 'checking'}), ('delete', f'/products/{key}', None)]:
            with self.subTest(path=path, method=method):
                kwargs = {'json': body} if body is not None else {}
                self.assertEqual(self.other.request(method, path, **kwargs).status_code, 404)
        self.assertEqual(self.client.get('/products').json()[0]['id'], key)
        self.assertEqual(len(self.client.get(f'/products/{key}/history').json()['checks']), 1)
        self.assertEqual(self.other.get('/auth/export').json()['products'], [])

    def test_real_product_requires_confirmation_and_rejects_demo_scenarios(self):
        self.register()
        self.assertFalse(self.save()['latest_result']['synthetic'])
        for change in [{'model_confirmed': False}, {'model': ''}, {'brand': ''}, {'product_kind': 'other'}, {'scenario': 'stale'}]:
            self.assertEqual(self.client.post('/products', json=REAL | change).status_code, 422)

    def test_sync_rechecks_only_owner_and_notifications_are_deduplicated(self):
        self.register(consent=True)
        self.register(self.other, 'bob', consent=True)
        own = self.save()
        other = self.save(self.other)
        self.service.version = 'b' * 64
        result = self.sync()
        self.assertEqual(result['rechecked'], 1)
        self.assertEqual(result['notifications_created'], 1)
        self.assertEqual(self.service.sync_calls, 1)
        self.assertEqual(len(self.client.get(f"/products/{own['id']}/history").json()['checks']), 2)
        self.assertEqual(len(self.other.get(f"/products/{other['id']}/history").json()['checks']), 1)
        self.assertEqual(self.other.get('/notifications').json(), [])
        notification = self.client.get('/notifications').json()[0]
        self.assertIsNone(notification['read_at'])
        self.assertEqual(self.other.patch(f"/notifications/{notification['id']}/read").status_code, 404)
        self.assertIsNotNone(self.client.patch(f"/notifications/{notification['id']}/read").json()['read_at'])
        self.assertEqual(self.sync()['notifications_created'], 0)
        # Returning to previously seen evidence must not emit the same alert again.
        self.service.version = 'c' * 64
        self.assertEqual(self.sync()['notifications_created'], 1)
        self.service.version = 'b' * 64
        self.assertEqual(self.sync()['notifications_created'], 0)
        self.assertEqual(len(self.client.get('/notifications').json()), 2)

    def test_notification_consent_changes_take_effect(self):
        self.register()
        self.save()
        self.service.version = 'b' * 64
        self.assertEqual(self.sync()['notifications_created'], 0)
        self.assertTrue(self.client.patch('/auth/preferences', json={'notification_consent': True}).json()['notification_consent'])
        self.service.version = 'c' * 64
        self.assertEqual(self.sync()['notifications_created'], 1)
        self.assertFalse(self.client.patch('/auth/preferences', json={'notification_consent': False}).json()['notification_consent'])
        self.service.version = 'd' * 64
        self.assertEqual(self.sync()['notifications_created'], 0)

    def test_new_evidence_resets_user_completion(self):
        self.register()
        saved = self.save()
        path = f"/products/{saved['id']}"
        self.assertEqual(self.client.patch(path + '/action', json={'state': 'user_completed'}).json()['action_state'], 'user_completed')
        self.client.put(path, json=REAL)
        self.assertEqual(self.client.get('/products').json()[0]['action_state'], 'user_completed')
        self.service.version = 'b' * 64
        self.sync()
        self.assertEqual(self.client.get('/products').json()[0]['action_state'], 'unconfirmed')

    def test_source_sync_is_rate_limited(self):
        self.register()
        self.assertEqual(self.client.post('/sources/sync').status_code, 200)
        self.assertEqual(self.client.post('/sources/sync').status_code, 429)
        self.assertEqual(self.service.sync_calls, 1)

    def test_identify_size_and_authentication_without_running_ocr(self):
        self.register()
        with patch('batterycheck.ocr.extract_label', return_value={'text': 'fixture', 'requires_confirmation': True}) as extract:
            response = self.client.post('/identify', json={'image_base64': 'a' * 20000})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['requires_confirmation'])
            extract.assert_called_once()
        self.assertEqual(self.client.post('/identify', content='x' * (6 * 1024 * 1024 + 1)).status_code, 413)
        self.assertEqual(self.client.post('/checks', content=(b'x' * 5000 for _ in range(4))).status_code, 413)
        self.assertEqual(self.client.post('/checks', content='{}', headers={'content-length': '-1'}).status_code, 400)

    def test_export_and_account_erasure_cascade_preserve_other_account(self):
        registered = self.register(consent=True).json()
        self.register(self.other, 'bob')
        own = self.save()
        other = self.save(self.other)
        self.client.patch(f"/products/{own['id']}/action", json={'state': 'checking'})
        self.service.version = 'b' * 64
        self.sync()
        exported = self.client.get('/auth/export')
        self.assertIn('attachment', exported.headers['content-disposition'])
        self.assertEqual(exported.json()['account']['id'], registered['id'])
        self.assertEqual(len(exported.json()['products']), 1)
        self.assertEqual(len(exported.json()['history'][own['id']]['checks']), 2)
        self.assertEqual(len(exported.json()['notifications']), 1)
        self.assertNotIn('password_hash', exported.text)
        self.assertNotIn('token_hash', exported.text)
        self.assertEqual(self.client.request('DELETE', '/auth/account', json={'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.request('DELETE', '/auth/account', json={'password': PASSWORD}).status_code, 204)
        self.assertEqual(self.client.get('/auth/me').status_code, 401)
        self.assertEqual(self.other.get('/products').json()[0]['id'], other['id'])
        with self.app.state.store.connection() as db:
            for table, field in [('users', 'id'), ('sessions', 'user_id'), ('products', 'user_id'), ('notifications', 'user_id')]:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE {field}=?', (registered['id'],)).fetchone()[0], 0)
            for table in ['checks', 'actions']:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE product_id=?', (own['id'],)).fetchone()[0], 0)

    def test_origin_csrf_cache_and_production_cookie_config(self):
        self.register()
        self.assertEqual(self.client.post('/auth/logout', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/auth/logout', headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertEqual(self.client.get('/auth/me').headers['cache-control'], 'no-store')
        with patch.dict(os.environ, {'BATTERYCHECK_COOKIE_SECURE': '1', 'BATTERYCHECK_ALLOWED_HOSTS': 'example.test',
                                     'BATTERYCHECK_ALLOWED_ORIGINS': 'https://example.test'}):
            app = create_app(Path(self.temp.name) / 'secure.sqlite3', official_service=self.service)
            with TestClient(app, base_url='https://example.test') as client:
                response = self.register(client, 'secure_user')
                self.assertIn('Secure', response.headers['set-cookie'])
                self.assertEqual(client.get('/auth/me').status_code, 200)
                self.assertEqual(client.get('/health', headers={'Host': 'other.example'}).status_code, 400)
                self.assertEqual(client.post('/auth/logout', headers={'Origin': 'https://example.test'}).status_code, 204)

    def test_global_catalog_change_does_not_reset_completion_or_notify(self):
        self.register(consent=True)
        product = ProductInput(**REAL)
        initial = self.service.evaluate(product)
        initial['notices'] = []
        stored = self.app.state.store.save(product, initial, self.client.get('/auth/me').json()['id'])
        user_id = self.client.get('/auth/me').json()['id']
        self.app.state.store.action(stored['id'], user_id, 'user_completed', NOW.isoformat())
        unrelated_revision = initial | {'catalog_hash': 'unrelated-catalog-change'}
        after = self.app.state.store.save(product, unrelated_revision, user_id, stored['id'], notify=True)
        self.assertEqual(after['action_state'], 'user_completed')
        self.assertEqual(self.client.get('/notifications').json(), [])
        # A relevant source becoming stale still warrants a new verification and alert.
        unavailable = unrelated_revision | {'sources': [{'id': 'cpsc', 'state': 'stale'}]}
        after = self.app.state.store.save(product, unavailable, user_id, stored['id'], notify=True)
        self.assertEqual(after['action_state'], 'unconfirmed')
        self.assertEqual(len(self.client.get('/notifications').json()), 1)

    def test_worker_refreshes_only_consenting_accounts_and_calls_source_once(self):
        self.register(consent=True)
        self.register(self.other, 'bob', consent=False)
        own, other = self.save(), self.save(self.other)
        self.service.version = 'worker-revision'
        result = once(self.app.state.store, self.service)
        self.assertEqual(result['rechecked'], 1)
        self.assertEqual(result['notifications_created'], 1)
        self.assertEqual(self.service.sync_calls, 1)
        self.assertEqual(len(self.client.get(f"/products/{own['id']}/history").json()['checks']), 2)
        self.assertEqual(len(self.other.get(f"/products/{other['id']}/history").json()['checks']), 1)
        self.assertEqual(self.other.get('/notifications').json(), [])
        self.assertEqual(once(self.app.state.store, self.service)['notifications_created'], 0)

    def test_worker_skips_demo_products(self):
        self.register(consent=True)
        demo = REAL | {'demo': True, 'brand': 'DEMO LAB', 'model': 'DEMO PB-100'}
        self.assertEqual(self.client.post('/products', json=demo).status_code, 201)
        self.assertEqual(once(self.app.state.store, self.service)['rechecked'], 0)

    def test_worker_account_removal_during_evaluation_never_recreates_data(self):
        user = self.register(consent=True).json()
        self.save()
        original = self.service.evaluate
        def deleting_evaluate(product):
            self.app.state.store.delete_account(user['id'])
            return original(product)
        self.service.evaluate = deleting_evaluate
        result = once(self.app.state.store, self.service)
        self.assertEqual(result['rechecked'], 0)
        self.assertEqual(result['skipped_changed_products'], 1)
        with self.app.state.store.connection() as db:
            for table in ['users', 'products', 'checks', 'notifications']:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_worker_consent_withdrawal_and_edit_races_do_not_overwrite_product(self):
        user = self.register(consent=True).json()
        saved = self.save()
        original = self.service.evaluate
        def withdrawing_evaluate(product):
            self.app.state.store.preferences(user['id'], False)
            return original(product)
        self.service.evaluate = withdrawing_evaluate
        self.assertEqual(once(self.app.state.store, self.service)['rechecked'], 0)
        self.assertEqual(len(self.client.get(f"/products/{saved['id']}/history").json()['checks']), 1)
        self.app.state.store.preferences(user['id'], True)
        def editing_evaluate(product):
            edited = ProductInput(**(REAL | {'model': 'A1289'}))
            self.app.state.store.save(edited, original(edited), user['id'], saved['id'])
            return original(product)
        self.service.evaluate = editing_evaluate
        self.assertEqual(once(self.app.state.store, self.service)['rechecked'], 0)
        self.assertEqual(self.client.get('/products').json()[0]['input']['model'], 'A1289')

    def test_worker_and_manual_refresh_share_process_lock(self):
        self.register(consent=True)
        self.save()
        with refresh_lock(self.app.state.store) as acquired:
            self.assertTrue(acquired)
            self.assertEqual(once(self.app.state.store, self.service)['skipped'], 'refresh_in_progress')
            self.assertEqual(self.client.post('/sources/sync').status_code, 429)
        self.assertEqual(self.service.sync_calls, 0)
        self.assertEqual(once(self.app.state.store, self.service)['rechecked'], 1)



class MigrationTests(unittest.TestCase):
    def test_legacy_ownerless_rows_are_preserved_but_never_assigned_to_user(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE products (id TEXT PRIMARY KEY,input_json TEXT NOT NULL,action_state TEXT NOT NULL DEFAULT \'unconfirmed\',created_at TEXT NOT NULL)')
                db.execute('INSERT INTO products(id,input_json,created_at) VALUES (?,?,?)', ('legacy', json.dumps(REAL), NOW.isoformat()))
            app = create_app(path, official_service=OfficialFixture())
            with TestClient(app) as client:
                self.assertEqual(client.get('/products').status_code, 401)
                registered = client.post('/auth/register', json={'username': 'new_user', 'password': PASSWORD, 'privacy_consent': True})
                self.assertEqual(registered.status_code, 201)
                self.assertEqual(client.get('/products').json(), [])
                self.assertEqual(client.get('/products/legacy/history').status_code, 404)
                self.assertEqual(client.delete('/products/legacy').status_code, 404)
            with app.state.store.connection() as db:
                self.assertIsNone(db.execute('SELECT user_id FROM products WHERE id=\'legacy\'').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
