import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from batterycheck.models import ProductInput
from batterycheck.official import OfficialService

NOW = datetime(2026, 10, 6, 9, tzinfo=timezone.utc)
KEY = 'issued-test-key'
RECORD = {'recallUid': '9001', 'recallProductName': '보조배터리', 'recallBrandName': '테스트전자', 'recallModelName': 'TB-10000, TB-20000',
          'recallTypeName': '명령에따른리콜', 'recallInqryTel': '080-000-0000', 'recallCmpnyName': '(주)테스트전자', 'makerName': 'TEST ELECTRONICS',
          'publishDate': '20260901', 'harmDscr': '과충전 시 발화 위험', 'publishActionDscr': '사용 중지 후 환불'}
PRODUCT = {'model_confirmed': True, 'product_kind': 'power_bank', 'signs_answered': True}


class SafetyKoreaTests(unittest.TestCase):
    def service(self, reply, key=KEY):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.requests = []

        def handler(request):
            self.requests.append(request)
            if request.url.host == 'www.saferproducts.gov':
                return httpx.Response(503)
            return reply(request) if callable(reply) else httpx.Response(200, json=reply)
        service = OfficialService(Path(directory.name), transport=httpx.MockTransport(handler), clock=lambda: NOW)
        service.kr_key = key
        return service

    def domestic(self, service):
        return next(source for source in service.status()['sources'] if source['id'] == 'safety-korea')

    def test_without_a_key_nothing_is_requested_and_the_source_says_so(self):
        service = self.service({'resultCode': '2000', 'resultData': [RECORD]}, key='')
        result = service.evaluate(ProductInput(brand='테스트전자', model='TB-10000', **PRODUCT))
        self.assertEqual((self.domestic(service)['state'], result['notices'], self.requests), ('unconfigured', [], []))
        self.assertIn('아직 연결되지 않았습니다', result['limitations'])

    def test_first_lookup_fetches_the_list_and_matches_the_exact_model(self):
        service = self.service({'resultCode': '2000', 'resultMsg': 'Success', 'resultData': [RECORD]})
        result = service.evaluate(ProductInput(brand='테스트전자', model='tb-10000', **PRODUCT))
        request = self.requests[0]
        self.assertEqual((request.url.scheme, request.url.path, request.headers['AuthKey']), ('https', '/openapi/api/recall/recallList.json', KEY))
        self.assertEqual((request.url.params['conditionKey'], request.url.params['conditionValue']), ('recallProductName', '보조배터리'))
        self.assertEqual((result['status'], [n['id'] for n in result['notices']]), ('matched', ['kr-9001']))
        notice = result['notices'][0]
        self.assertEqual((notice['published_at'], notice['source_url']), ('2026-09-01', 'https://www.safetykorea.kr/recall/recallBoard'))
        self.assertIn('080-000-0000', notice['action'])
        self.assertEqual({c['field']: c['state'] for c in notice['checks']}, {'brand': 'match', 'model': 'match', 'extra': 'match'})
        source = self.domestic(service)
        self.assertEqual((source['state'], source['notices_count'], source['last_success_at']), ('available', 1, NOW.isoformat()))
        service.evaluate(ProductInput(brand='테스트전자', model='TB-10000', **PRODUCT))
        self.assertEqual(len(self.requests), 1, 'a fresh cache is reused')
        self.assertNotIn(KEY, json.dumps(service.status(), ensure_ascii=False) + json.dumps(result, ensure_ascii=False))

    def test_other_models_and_brands_are_not_confirmed(self):
        service = self.service({'resultCode': '2000', 'resultData': [RECORD]})
        self.assertEqual(service.evaluate(ProductInput(brand='테스트전자', model='TB-10000X', **PRODUCT))['notices'], [])
        other = service.evaluate(ProductInput(brand='Other', model='TB-20000', **PRODUCT))
        self.assertEqual({c['field']: c['state'] for c in other['notices'][0]['checks']}['brand'], 'skipped')
        nothing = service.evaluate(ProductInput(brand='Other', model='ZZ-1', **PRODUCT))
        self.assertEqual(nothing['status'], 'incomplete')
        self.assertIn('국내(Safety Korea) 보조배터리 리콜 공지를 확인했습니다', nothing['limitations'])

    def test_no_data_is_a_successful_empty_list(self):
        service = self.service({'resultCode': '2004', 'resultMsg': 'No Date'})
        self.assertTrue(service.sync()['domestic_ok'])
        self.assertEqual((self.domestic(service)['state'], self.domestic(service)['notices_count']), ('available', 0))

    def test_rejected_key_and_outage_are_failures_not_empty_results(self):
        service = self.service({'resultCode': '4000', 'resultMsg': 'Invalid Auth Key'})
        self.assertFalse(service.sync()['domestic_ok'])
        source = self.domestic(service)
        self.assertEqual(source['state'], 'failed')
        self.assertIn('인증키', source['error'])
        self.assertNotIn(KEY, source['error'])
        service.evaluate(ProductInput(brand='테스트전자', model='TB-10000', **PRODUCT))
        self.assertEqual(len([r for r in self.requests if r.url.host == 'www.safetykorea.kr']), 1, 'a failed attempt is not retried on every lookup')

        def down(request):
            raise httpx.ConnectError('down', request=request)
        offline = self.service(down)
        self.assertFalse(offline.sync()['domestic_ok'])
        self.assertEqual([r.url.scheme for r in self.requests if r.url.host == 'www.safetykorea.kr'], ['https', 'http'])
        self.assertIn('연결하지 못했습니다', self.domestic(offline)['error'])


if __name__ == '__main__':
    unittest.main()
