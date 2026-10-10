import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
from fastapi.testclient import TestClient

from batterycheck import vision
from batterycheck.main import create_app
from batterycheck.official import OfficialService

PASSWORD = 'test password 123'


def png():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x00')) + chunk(b'IEND', b'')
    return base64.b64encode(data).decode()


def reading(**values):
    seen = {'brand': None, 'product_name': None, 'model': None, 'lot_or_batch': None, 'serial_number': None,
            'brand_source': 'visible_logo_text', 'image_quality': 'clear', 'confidence': {field: 0.95 for field in vision.FIELDS}}
    return seen | values


def ocr(brands=(), models=()):
    return {'brand_candidates': list(brands), 'model_candidates': list(models), 'text': ''}


class ReadingTests(unittest.TestCase):
    def test_anker_label_is_found_when_both_readings_agree(self):
        result = vision.combine(ocr(['ANKER'], ['A1263']), reading(brand='Anker', product_name='PowerCore 10000', model='A1263'))
        self.assertEqual((result['status'], result['brand'], result['product_name'], result['model']),
                         ('found', 'Anker', 'PowerCore 10000', 'A1263'))

    def test_baseus_model_keeps_its_original_spelling(self):
        result = vision.combine(ocr(['BASEUS'], ['BS 30KP365']), reading(brand='Baseus', model='BS-30KP365'))
        self.assertEqual((result['status'], result['brand'], result['model']), ('found', 'Baseus', 'BS-30KP365'))

    def test_logo_only_photo_gives_brand_without_a_guessed_model(self):
        result = vision.combine(ocr(['ANKER']), reading(brand='Anker', image_quality='no_label'))
        self.assertEqual((result['status'], result['brand'], result['model']), ('brand_only', 'Anker', None))
        self.assertIn('Model', result['message'])

    def test_blurry_photo_asks_for_a_retake(self):
        result = vision.combine(ocr(), reading(image_quality='blurry'))
        self.assertEqual(result['status'], 'blurry')
        self.assertIn('다시 촬영', result['message'])
        self.assertIsNone(result['model'])

    def test_different_model_readings_are_never_auto_confirmed(self):
        for local, seen in ((['A1263'], 'A1268'), (['A1263'], 'A1263H'), ([], 'A1263')):
            low = reading(model=seen) if local else reading(model=seen, confidence={field: 0.3 for field in vision.FIELDS})
            result = vision.combine(ocr(models=local), low)
            self.assertEqual((result['status'], result['model']), ('unsure', None), (local, seen))
            self.assertIn(seen, result['model_candidates'])
        self.assertEqual(vision.combine(ocr(models=['A-1263']), reading(model='a1263'))['status'], 'model_only')

    def test_reply_wrapped_in_a_code_block_is_parsed(self):
        seen = vision.parse_reply('결과입니다.\n```json\n{"brand": "Anker", "model": "A1263", "product_name": null, "extra": 1,\n"confidence": {"model": 0.9}}\n```\n끝')
        self.assertEqual((seen['brand'], seen['model'], seen['product_name'], seen['lot_or_batch']), ('Anker', 'A1263', None, None))
        self.assertEqual((seen['confidence']['model'], seen['confidence']['brand'], seen['image_quality']), (0.9, 0.0, 'clear'))
        self.assertNotIn('extra', seen)
        self.assertIsNone(vision.parse_reply('읽지 못했습니다')['model'])

    def test_brand_cues_from_label_text(self):
        from batterycheck.ocr import candidates_from_text
        baseus = candidates_from_text(['Baseu5', 'Power Bank', 'Model NO.: BS-30KP365', 'Shenzhen Baseus Technology Co., Ltd.'])
        self.assertEqual((baseus['brand_candidates'][0], baseus['model_candidates'][0]), ('BASEUS', 'BS-30KP365'))
        casely = candidates_from_text(['Power Pod', 'Model E33A', 'GETCASELY.COM'])
        self.assertEqual((casely['brand_candidates'], casely['brand_sources'], casely['model_candidates']), (['CASELY'], {'CASELY': 'website_domain'}, ['E33A']))
        domain = candidates_from_text(['Model: ZX-200', 'www.getvoltix.com'])
        self.assertEqual(domain['brand_sources'], {'VOLTIX': 'website_domain'})
        maker = candidates_from_text(['Model: Q1', 'Shenzhen Lumora Technology Co., Ltd.'])
        self.assertEqual(maker['brand_sources'], {'LUMORA': 'manufacturer_text'})
        self.assertEqual(candidates_from_text(['POWER BANK', 'Input 5V', 'Model: Q1'])['brand_candidates'], [])
        found = vision.combine(casely, None)
        self.assertEqual((found['status'], found['brand'], found['brand_raw'], found['model']), ('found', 'Casely', 'CASELY', 'E33A'))

    def test_table_labels_lookalike_letters_and_product_line(self):
        from batterycheck.ocr import candidates_from_text
        table = candidates_from_text(['Model', 'Battery Energy', 'Battery Capacity', 'Type-C In/Output', 'GETCASELY.COM',
                                      'Executive Standard:GB T35590-2017', 'ЕЗЗА', '19Wh3.85V', '5000mAh', 'PD 20W(Max) 9V 2.25A'])
        self.assertEqual((table['model_candidates'], table['brand_candidates']), (['E33A'], ['CASELY']))
        anker = candidates_from_text(['PowerCore 10000', 'Model A1263', 'Cell Capacity 10000mAh/36Wh', 'Anker Innovations Limited'])
        self.assertEqual((anker['brand_candidates'], anker['product_name'], anker['model_candidates']), (['ANKER'], 'PowerCore 10000', ['A1263']))
        split = candidates_from_text(['ANKER', 'PowerCore 10000', 'Model A1263'])
        self.assertEqual((split['brand_candidates'], split['model_candidates']), (['ANKER'], ['A1263']))
        units = candidates_from_text(['Model', '5000mAh', '36Wh', '5V', 'Input 9V 2A'])
        self.assertEqual(units['model_candidates'], [], 'capacity and voltage figures are not model names')

    def test_model_without_brand_is_completed_from_official_notices(self):
        only = vision.combine(ocr(models=['BS-30KP365']), None)
        self.assertEqual((only['status'], only['brand'], only['model']), ('model_only', None, 'BS-30KP365'))
        filled = vision.enrich(only, ['Baseus'])
        self.assertEqual((filled['status'], filled['brand'], filled['brand_source']), ('found_db', 'Baseus', 'db_enrichment'))
        self.assertIn('Baseus 제품으로 확인됩니다', filled['message'])
        several = vision.enrich(only, ['Casely', '다른 브랜드'])
        self.assertEqual((several['status'], several['brand'], several['brand_options']), ('choose', None, ['Casely', '다른 브랜드']))
        self.assertEqual(vision.enrich(only, [])['status'], 'model_only')
        self.assertEqual(vision.combine(ocr(['BASEUS']), None)['status'], 'brand_only')
        self.assertEqual(vision.combine(ocr(), None)['status'], 'not_found')

    def test_without_ai_the_local_reading_is_used_alone(self):
        result = vision.combine(ocr(['ANKER'], ['A1263']), None)
        self.assertEqual((result['status'], result['model'], result['used_ai']), ('found', 'A1263', False))


class FakeVision:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(model='fake', choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply, tool_calls=None, refusal=None))])


class IdentifyTests(unittest.TestCase):
    def app(self, fake):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        client = TestClient(create_app(root / 'test.sqlite3', official_service=OfficialService(root / 'official'), ai_client=fake))
        client.app.state.assistant.vision = True
        client.post('/auth/register', json={'username': 'photo', 'password': PASSWORD, 'privacy_consent': True})
        return client

    def test_photo_reaches_the_model_as_image_input_only_after_consent(self):
        fake = FakeVision('{"brand": "Anker", "product_name": "PowerCore 10000", "model": "A1263", "serial_number": "SN123456789", "confidence": {"model": 0.95}}')
        client = self.app(fake)
        local = {'text': 'ANKER\nModel: A1263\nS/N SN123456789', 'brand_candidates': ['ANKER'], 'model_candidates': ['A1263'],
                 'requires_confirmation': True, 'model_confirmed': False, 'photo_retained': False, 'engine': 'fixture'}
        with patch('batterycheck.ocr.extract_label', return_value=dict(local)):
            before = client.post('/identify', json={'image_base64': png()}).json()
            self.assertEqual((before['processing'], before['used_ai'], before['ai_hint'], fake.requests), ('local', False, True, []))
            client.patch('/auth/preferences', json={'ai_consent': True})
            after = client.post('/identify', json={'image_base64': png()})
        self.assertEqual(after.status_code, 200, after.text)
        body = after.json()
        self.assertEqual((body['status'], body['brand'], body['model'], body['product_name'], body['processing']),
                         ('found', 'Anker', 'A1263', 'PowerCore 10000', 'ai'))
        self.assertEqual((body['model_confirmed'], body['requires_confirmation'], body['photo_retained']), (False, True, False))
        # The label's own serial goes back to the person who photographed it, as a candidate until a second reading agrees.
        self.assertEqual((body['serial_number'], body['serial_state'], body['serial_candidates']), (None, 'uncertain', ['SN123456789']))
        self.assertNotIn('text', body)
        parts = fake.requests[0]['messages'][0]['content']
        self.assertEqual([part['type'] for part in parts], ['text', 'image_url'])
        self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/png;base64,iVBOR'))

    def test_ai_failure_shows_a_plain_message(self):
        error = openai.APIStatusError('down', response=httpx.Response(500, request=httpx.Request('POST', 'https://example.test')), body=None)
        client = self.app(FakeVision(error=error))
        client.patch('/auth/preferences', json={'ai_consent': True})
        with patch('batterycheck.ocr.extract_label', return_value=ocr()):
            response = client.post('/identify', json={'image_base64': png()})
        self.assertEqual((response.status_code, response.json()['detail']), (502, vision.FAILURE))


if __name__ == '__main__':
    unittest.main()
