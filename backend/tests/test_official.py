"""Synthetic transport fixtures test wiring; curated-source tests use published facts."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from batterycheck.models import ProductInput
from batterycheck.official import API_URL, MAX_BYTES, OfficialService

NOW = datetime(2026, 10, 2, 15, tzinfo=timezone.utc)
# Deliberately fictitious notice. This transport fixture is never bundled as official data.
FIXTURE = {"RecallNumber": "TEST-0001", "RecallID": 1, "Title": "Fixture Brand power bank recall",
           "Description": "Fixture Brand power bank model FX-001 has serial restrictions.",
           "URL": "https://www.cpsc.gov/Recalls/2099/fixture-only",
           "RecallDate": "2026-09-01T00:00:00", "LastPublishDate": "2026-09-02T00:00:00",
           "Products": [{"Name": "Fixture Brand power bank", "Model": "FX-001"}],
           "Manufacturers": [{"Name": "Fixture Brand"}]}


def product(**kw):
    return ProductInput(**({"brand": "Anker", "model": "A1263", "model_confirmed": True} | kw))


class OfficialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.now = NOW
        self.requests = []

    def tearDown(self):
        self.temp.cleanup()

    def service(self, handler=None, **kw):
        def respond(request):
            self.requests.append(request)
            return handler(request) if handler else httpx.Response(200, json=[FIXTURE])
        return OfficialService(self.directory, transport=httpx.MockTransport(respond), clock=lambda: self.now, **kw)

    def test_initial_status_and_evaluation_have_no_network_or_mutation(self):
        service = self.service()
        self.assertEqual(service.status()['state'], 'not_synced')
        result = service.evaluate(product())
        self.assertEqual(result['status'], 'needs_information')
        self.assertFalse(result['coverage_complete'])
        self.assertFalse(result['synthetic'])
        self.assertEqual(result['notices'][0]['outcome'], 'pending_review')
        self.assertEqual(result['notices'][0]['evidence_kind'], 'official_web_curated')
        self.assertEqual(self.requests, [])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_curated_identifiers_do_not_allow_brand_or_model_near_matches(self):
        for fields in ({'brand': 'NotAnker'}, {'brand': 'ANK'}, {'model': 'A1263X'},
                       {'model': '1263'}, {'model': 'A1263-1'}, {'model': 'A126'}, {'model': 'A12630'}):
            with self.subTest(fields=fields):
                result = self.service().evaluate(product(**fields))
                self.assertEqual(result['notices'], [])
                self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(self.service().evaluate(product(brand='Ａｎｋｅｒ', model='ａ１２６３'))['status'], 'needs_information')

    def test_serial_is_not_misread_as_lot_and_us_input_cannot_finalize(self):
        result = self.service().evaluate(product(brand='INIU', model='BI-B41', lot='000G21', sale_region='US'))
        notice = result['notices'][0]
        serial = next(c for c in notice['checks'] if c['field'] == 'serial_number')
        self.assertEqual(serial['state'], 'unknown')
        self.assertIsNone(serial['input'])
        self.assertEqual(serial['expected'], ['000G21', '000H21', '000I21', '000L21'])
        self.assertEqual(result['status'], 'needs_information')
        self.assertEqual(notice['outcome'], 'pending_review')

    def test_unconfirmed_and_unsupported_identification_is_not_searched(self):
        for changes in ({'model': ''}, {'model_confirmed': False}, {'product_kind': 'other'}):
            self.assertEqual(self.service().evaluate(product(**changes))['status'], 'needs_identification')
        self.assertEqual(self.requests, [])

    def test_a_missing_brand_does_not_block_the_model_lookup(self):
        result = self.service().evaluate(product(brand=''))
        self.assertEqual(result['status'], 'needs_information')
        self.assertEqual({c['field']: c['state'] for c in result['notices'][0]['checks']}['brand'], 'unknown')
        self.assertEqual(self.service().brands_for_model('a1263'), ['Anker'])
        self.assertEqual(self.service().brands_for_model('A1263X'), [])

    def test_sync_calls_only_fixed_public_filter_and_persists_original(self):
        service = self.service()
        result = service.sync()
        self.assertTrue(result['ok'])
        self.assertEqual(result['state'], 'available')
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url).split('?')[0], API_URL)
        self.assertEqual(dict(request.url.params), {'format': 'json', 'ProductName': 'power bank'})
        self.assertNotIn('Authorization', request.headers)
        self.assertEqual(request.content, b'')
        cache = json.loads((self.directory / 'cpsc-cache.json').read_text())
        self.assertEqual(cache['records'], [FIXTURE])
        revision = list((self.directory / 'revisions').glob('*.json'))
        self.assertEqual(len(revision), 1)
        self.assertEqual(json.loads(revision[0].read_text())['records'], [FIXTURE])
        candidate = service.evaluate(product(brand='Fixture Brand', model='FX-001'))
        self.assertEqual(candidate['status'], 'needs_information')
        self.assertEqual(candidate['notices'][0]['evidence_kind'], 'official_api')
        self.assertEqual(candidate['notices'][0]['outcome'], 'pending_review')

    def test_api_token_boundaries_preserve_model_suffixes(self):
        service = self.service()
        service.sync()
        for brand, model in [('Brand', 'FX-00'), ('Fixture Brand', '001'), ('Fixture Brand', 'FX'), ('Fixture', 'FX-001X')]:
            result = service.evaluate(product(brand=brand, model=model))
            self.assertEqual(result['notices'], [])
            self.assertEqual(result['status'], 'incomplete')

    def test_catalog_hash_does_not_change_for_rechecks_or_refetch_timestamps(self):
        service = self.service()
        a = service.sync()['catalog_hash']
        before = service.evaluate(product(brand='Fixture Brand', model='FX-001'))['notices'][0]['content_hash']
        self.now += timedelta(hours=1)
        b = service.sync()['catalog_hash']
        after = service.evaluate(product(brand='Fixture Brand', model='FX-001'))['notices'][0]['content_hash']
        self.assertEqual(a, b)
        self.assertEqual(before, after)
        self.assertEqual(len(list((self.directory / 'revisions').glob('*.json'))), 1)

    def test_revision_changes_on_notice_content_change(self):
        first = self.service().sync()['catalog_hash']
        second = self.service(lambda _: httpx.Response(200, json=[FIXTURE | {'Description': 'Updated FX-001 serial conditions'}])).sync()['catalog_hash']
        self.assertNotEqual(first, second)
        self.assertEqual(len(list((self.directory / 'revisions').glob('*.json'))), 2)

    def test_outage_keeps_successful_cache_and_marks_failure(self):
        old = self.service().sync()
        service = self.service(lambda _: httpx.Response(503, text='Page Unavailable'))
        with patch('batterycheck.official.time.sleep'):
            status = service.sync()
        self.assertFalse(status['ok'])
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['catalog_hash'], old['catalog_hash'])
        self.assertEqual(status['last_success_at'], NOW.isoformat())
        self.assertIn('503', status['error'])
        self.assertEqual(len(self.requests), 3)  # initial request + bounded two failure attempts
        result = service.evaluate(product(brand='Fixture Brand', model='FX-001'))
        self.assertEqual(len(result['notices']), 1)
        self.assertIn('실패', result['next_action'])
        self.assertFalse(result['coverage_complete'])

    def test_redirect_is_not_followed_and_local_url_never_becomes_evidence(self):
        service = self.service(lambda _: httpx.Response(302, headers={'location': 'http://127.0.0.1/private'}))
        self.assertFalse(service.sync()['ok'])
        self.assertEqual(len(self.requests), 1)
        for url in ('http://www.cpsc.gov/Recalls/test', 'https://evil.com/Recalls/test',
                    'https://www.cpsc.gov.evil.com/Recalls/test', 'https://x@www.cpsc.gov/Recalls/test',
                    'https://www.cpsc.gov:444/Recalls/test', 'javascript:alert(1)'):
            service = self.service(lambda _, url=url: httpx.Response(200, json=[FIXTURE | {'URL': url}]))
            self.assertFalse(service.sync()['ok'])
        self.assertFalse((self.directory / 'cpsc-cache.json').exists())

    def test_malformed_oversized_or_duplicate_data_does_not_replace_cache(self):
        old = self.service().sync()['catalog_hash']
        failures = [httpx.Response(200, text='not json'), httpx.Response(200, json={'error': 'bad'}),
                    httpx.Response(200, json=[FIXTURE, FIXTURE]), httpx.Response(200, json=[{}]),
                    httpx.Response(200, content=b'x' * (MAX_BYTES + 1))]
        for response in failures:
            service = self.service(lambda _, response=response: response)
            status = service.sync()
            self.assertFalse(status['ok'])
            self.assertEqual(status['catalog_hash'], old)

    def test_empty_official_response_is_not_global_no_match(self):
        service = self.service(lambda _: httpx.Response(200, json=[]))
        self.assertTrue(service.sync()['ok'])
        result = service.evaluate(product(brand='Unknown', model='ABC123'))
        self.assertEqual(result['status'], 'incomplete')
        self.assertFalse(result['coverage_complete'])

    def test_stale_evidence_remains_visible_but_incomplete(self):
        service = self.service()
        service.sync()
        self.now += timedelta(days=8)
        self.assertEqual(service.status()['state'], 'stale')
        for input_product in (product(), product(brand='Fixture Brand', model='FX-001')):
            result = service.evaluate(input_product)
            self.assertEqual(result['status'], 'incomplete')
            self.assertTrue(result['notices'][0]['stale'])
            self.assertIn('만료', result['next_action'])

    def test_tampered_cache_fails_closed(self):
        service = self.service()
        service.sync()
        path = self.directory / 'cpsc-cache.json'
        cache = json.loads(path.read_text())
        cache['records'][0]['Description'] = 'tampered'
        path.write_text(json.dumps(cache))
        self.assertEqual(service.status()['state'], 'failed')
        self.assertEqual(service.evaluate(product(brand='Fixture Brand', model='FX-001'))['notices'], [])

    def test_risk_guidance_survives_official_missing_identification_and_outage(self):
        service = self.service(lambda _: httpx.Response(503))
        with patch('batterycheck.official.time.sleep'):
            service.sync()
        for changes in ({}, {'model_confirmed': False}, {'brand': 'Missing'}):
            result = service.evaluate(product(signs=['smoke'], signs_answered=True, **changes))
            self.assertEqual(result['risk']['level'], 'emergency')
            self.assertIn('119', result['risk']['text'])


if __name__ == '__main__':
    unittest.main()
