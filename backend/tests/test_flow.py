import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from batterycheck.engine import CATALOG_PATH, evaluate
from batterycheck.main import create_app
from batterycheck.models import Catalog, ProductInput

NOW = datetime(2026, 10, 2, 7, tzinfo=timezone.utc)
VALID = dict(brand='DEMO LAB', model='DEMO PB-100', model_confirmed=True, demo=True,
             manufactured_on='2026-02-15', sale_region='DEMO-KR', lot='001A', signs_answered=True)


def verdict(**changes):
    return evaluate(ProductInput(**(VALID | changes)), now=NOW)


class DecisionTests(unittest.TestCase):
    def test_exact_match_has_condition_evidence_and_versions(self):
        result = verdict()
        self.assertEqual(result['status'], 'matched')
        self.assertTrue(result['synthetic'])
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(len(result['notices'][0]['checks']), 4)
        self.assertEqual(len(result['catalog_hash']), 64)
        self.assertEqual(result['catalog_version'], 'demo-2026-10-02-v1')
        self.assertIn('가상', result['limitations'])

    def test_period_boundaries_are_inclusive(self):
        for value, expected in [('2025-12-31', 'no_match'), ('2026-01-01', 'matched'),
                                ('2026-03-31', 'matched'), ('2026-04-01', 'no_match')]:
            with self.subTest(value=value):
                self.assertEqual(verdict(manufactured_on=value)['status'], expected)

    def test_missing_conditions_produce_precise_questions(self):
        result = verdict(manufactured_on=None, sale_region=None, lot='')
        self.assertEqual(result['status'], 'needs_information')
        self.assertEqual({q['field'] for q in result['questions']}, {'manufactured_on', 'sale_region', 'lot'})
        self.assertNotIn('구매일로 대신', result['next_action'])
        self.assertTrue(any('구매일로 대신하지' in q['text'] for q in result['questions']))

    def test_missing_plus_mismatch_is_not_silently_negative(self):
        self.assertEqual(verdict(manufactured_on=None, lot='BAD')['status'], 'needs_information')

    def test_complete_known_mismatch_is_scoped_negative(self):
        for change in [{'sale_region': 'DEMO-US'}, {'lot': '1A'}, {'model': 'DEMO PB-100X'}]:
            with self.subTest(change=change):
                result = verdict(**change)
                self.assertEqual(result['status'], 'no_match')
                self.assertIn('안전을 보증하지', result['next_action'])

    def test_case_width_spacing_do_not_change_identity(self):
        self.assertEqual(verdict(brand=' demo  lab ', model='ｄｅｍｏ ｐｂ-１００', lot='001a')['status'], 'matched')

    def test_unconfirmed_or_unsupported_needs_identification(self):
        for change in [{'model_confirmed': False}, {'brand': ''}, {'model': ''}, {'product_kind': 'other'}]:
            with self.subTest(change=change):
                self.assertEqual(verdict(**change)['status'], 'needs_identification')

    def test_real_brand_or_missing_demo_consent_never_uses_fake_notice(self):
        for change in [{'brand': 'Actual Brand'}, {'demo': False}, {'model': 'A1263'}]:
            with self.subTest(change=change):
                result = verdict(**change)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['notices'], [])
                self.assertEqual(result['sources'][0]['state'], 'unconfigured')

    def test_unreviewed_notice_is_not_a_confirmed_match(self):
        result = verdict(model='DEMO PB-300')
        self.assertEqual(result['status'], 'needs_information')
        self.assertEqual(result['notices'][0]['outcome'], 'pending_review')

    def test_failure_and_stale_sources_never_produce_current_verdict(self):
        for scenario in ['outage', 'stale']:
            for model in ['DEMO PB-100', 'DEMO PB-999']:
                with self.subTest(scenario=scenario, model=model):
                    result = verdict(scenario=scenario, model=model)
                    self.assertEqual(result['status'], 'incomplete')
                    self.assertFalse(result['coverage_complete'])
                    self.assertIsNotNone(result['sources'][0]['last_success_at'])

    def test_age_expires_without_scenario_switch(self):
        later = datetime(2026, 10, 10, tzinfo=timezone.utc)
        # A catalogue fetched from elsewhere expires by age; the practice catalogue bundled with the app does not.
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / 'catalog.json'
            copy.write_text(CATALOG_PATH.read_text())
            self.assertEqual(evaluate(ProductInput(**VALID), copy, later)['status'], 'incomplete')
        self.assertEqual(evaluate(ProductInput(**VALID), now=later)['status'], 'matched')

    def test_malformed_or_missing_catalog_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            for content in ['not JSON', '{}', CATALOG_PATH.read_text().replace('date_range', 'unsupported')]:
                path.write_text(content)
                self.assertEqual(evaluate(ProductInput(**VALID), path, NOW)['status'], 'incomplete')
            path.unlink()
            self.assertEqual(evaluate(ProductInput(**VALID), path, NOW)['status'], 'incomplete')

    def test_catalog_rejects_actual_brands_duplicate_ids_and_invalid_dates(self):
        original = json.loads(CATALOG_PATH.read_text())
        for mutate in [lambda c: c['notices'][0].update(brand='Real Brand'),
                       lambda c: c['notices'].append(c['notices'][0]),
                       lambda c: c['notices'][0]['conditions'][0].update(values=['2026-04-01', '2026-01-01']),
                       lambda c: c['source'].update(last_success_at='2026-10-02T00:00:00')]:
            candidate = json.loads(json.dumps(original))
            mutate(candidate)
            with self.assertRaises(ValidationError):
                Catalog.model_validate(candidate)

    def test_missing_timezone_or_future_source_date_cannot_complete_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            candidate = json.loads(CATALOG_PATH.read_text())
            candidate['source']['last_success_at'] = '2026-11-01T00:00:00Z'
            path.write_text(json.dumps(candidate))
            self.assertEqual(evaluate(ProductInput(**VALID), path, NOW)['status'], 'incomplete')

    def test_date_input_rejects_partial_impossible_and_future_dates(self):
        for value in ['2026-01', '2026-02-29', '2026-01-01T00:00:00Z', '2099-01-01', 123]:
            with self.subTest(value=value), self.assertRaises((ValidationError, ValueError)):
                ProductInput(**(VALID | {'manufactured_on': value}))
        self.assertEqual(ProductInput(**(VALID | {'manufactured_on': '2024-02-29'})).manufactured_on.isoformat(), '2024-02-29')

    def test_explicit_demo_and_model_consent_cannot_be_string_or_number(self):
        for field in ['demo', 'model_confirmed', 'signs_answered']:
            for value in ['true', 1]:
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    ProductInput(**(VALID | {field: value}))

    def test_risk_is_independent_of_every_recall_state(self):
        cases = [{}, {'lot': None}, {'lot': 'BAD'}, {'model_confirmed': False}, {'scenario': 'outage'}]
        for changes in cases:
            for sign in ['smoke', 'flame', 'swelling', 'heat', 'odor', 'sound', 'discoloration', 'damage']:
                with self.subTest(changes=changes, sign=sign):
                    result = verdict(**(changes | {'signs': [sign]}))
                    self.assertIn(result['risk']['level'], ['emergency', 'concern'])
                    self.assertIn('119', result['risk']['text'])
                    self.assertEqual(result['risk']['sources'][0]['checked_on'], '2026-10-02')
                    self.assertIn('전문가 검토 미완료', result['risk']['review_status'])

    def test_unanswered_is_different_from_no_signs_reported(self):
        self.assertEqual(verdict(signs_answered=False)['risk']['level'], 'unanswered')
        self.assertEqual(verdict()['risk']['level'], 'reported_none')
        self.assertIn('제품 안전을 확인한 결과가 아닙니다', verdict()['risk']['text'])

    def test_signs_require_an_answer_marker(self):
        with self.assertRaises(ValidationError):
            ProductInput(**(VALID | {'signs': ['smoke'], 'signs_answered': False}))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'demo.sqlite3'
        self.app = create_app(self.db, clock=lambda: NOW)
        self.client = TestClient(self.app)
        registered = self.client.post('/auth/register', json={'username': 'flow_tester', 'password': 'test password 123', 'privacy_consent': True})
        self.assertEqual(registered.status_code, 201, registered.text)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def add(self, changes=None):
        response = self.client.post('/products', json=VALID | (changes or {}))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_register_recheck_action_history_persist_delete(self):
        saved = self.add({'lot': None, 'signs': ['smoke']})
        product_id = saved['id']
        self.assertEqual(saved['latest_result']['status'], 'needs_information')
        response = self.client.put(f'/products/{product_id}', json=VALID | {'signs': ['smoke']})
        self.assertEqual(response.json()['latest_result']['status'], 'matched')
        response = self.client.patch(f'/products/{product_id}/action', json={'state': 'user_completed'})
        self.assertEqual(response.json()['action_state'], 'user_completed')
        self.assertEqual(response.json()['latest_result']['risk']['level'], 'emergency')
        history = self.client.get(f'/products/{product_id}/history').json()
        self.assertEqual(len(history['checks']), 2)
        self.assertIsNone(history['checks'][1]['input']['lot'])
        self.assertEqual(history['checks'][1]['result']['status'], 'needs_information')
        self.assertEqual(history['actions'][0]['state'], 'user_completed')
        with TestClient(create_app(self.db)) as second:
            self.assertEqual(second.post('/auth/login', json={'username': 'flow_tester', 'password': 'test password 123'}).status_code, 200)
            self.assertEqual(len(second.get('/products').json()), 1)
        self.assertEqual(self.client.delete(f'/products/{product_id}').status_code, 204)
        self.assertEqual(self.client.get('/products').json(), [])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM checks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM actions').fetchone()[0], 0)

    def test_user_completion_does_not_clear_concern(self):
        saved = self.add({'signs': ['heat']})
        updated = self.client.patch(f"/products/{saved['id']}/action", json={'state': 'user_completed'}).json()
        self.assertEqual(updated['latest_result']['risk']['level'], 'concern')

    def test_recheck_stale_data_preserves_prior_result_as_history(self):
        saved = self.add()
        updated = self.client.put(f"/products/{saved['id']}", json=VALID | {'scenario': 'stale'}).json()
        self.assertEqual(updated['latest_result']['status'], 'incomplete')
        history = self.client.get(f"/products/{saved['id']}/history").json()
        self.assertEqual(history['checks'][1]['result']['status'], 'matched')
        self.assertEqual(history['checks'][0]['result']['status'], 'incomplete')

    def test_preview_does_not_save_product(self):
        self.assertEqual(self.client.post('/checks', json=VALID).status_code, 200)
        self.assertEqual(self.client.get('/products').json(), [])

    def test_invalid_payloads_do_not_modify_store(self):
        for body in [VALID | {'manufactured_on': '2026-02-29'}, VALID | {'serial': 'PRIVATE'},
                     VALID | {'brand': 'Actual Brand'}, VALID | {'demo': False},
                     VALID | {'model_confirmed': False}, VALID | {'signs': ['unsupported']}]:
            with self.subTest(keys=list(body)):
                self.assertEqual(self.client.post('/products', json=body).status_code, 422)
        self.assertEqual(self.client.get('/products').json(), [])

    def test_unknown_ids_and_invalid_actions(self):
        self.assertEqual(self.client.patch('/products/absent/action', json={'state': 'checking'}).status_code, 404)
        self.assertEqual(self.client.put('/products/absent', json=VALID).status_code, 404)
        self.assertEqual(self.client.get('/products/absent/history').status_code, 404)
        self.assertEqual(self.client.delete('/products/absent').status_code, 404)
        self.assertEqual(self.client.patch('/products/absent/action', json={'state': 'certified_safe'}).status_code, 422)

    def test_origin_host_and_body_size_boundaries(self):
        self.assertEqual(self.client.post('/products', json=VALID, headers={'Origin': 'https://untrusted.example'}).status_code, 403)
        self.assertEqual(self.client.get('/health', headers={'Host': 'untrusted.example'}).status_code, 400)
        self.assertEqual(self.client.post('/checks', content='x' * 17000).status_code, 413)
        self.assertIn(self.client.get('/health').json()['ai'], {'local_ocr', 'ocr_unavailable'})

    def test_risk_endpoint_does_not_require_product_identity(self):
        response = self.client.post('/risk', json={'signs': ['flame'], 'signs_answered': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['level'], 'emergency')

    def test_storage_failure_does_not_claim_success(self):
        with patch.object(self.app.state.store, 'save', side_effect=sqlite3.OperationalError('fixture failure')):
            response = self.client.post('/products', json=VALID)
            self.assertEqual(response.status_code, 503)
            self.assertIn('저장 완료로 처리되지', response.json()['detail'])
        self.assertEqual(self.client.get('/products').json(), [])

    def test_transaction_failure_rolls_back_product_insert(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TRIGGER fail_check BEFORE INSERT ON checks BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        self.assertEqual(self.client.post('/products', json=VALID).status_code, 503)
        self.assertEqual(self.client.get('/products').json(), [])


if __name__ == '__main__':
    unittest.main()
