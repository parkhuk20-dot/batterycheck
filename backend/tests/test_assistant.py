import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from batterycheck.assistant import _safety_documents, search
from batterycheck.main import create_app
from batterycheck.official import OfficialService

PASSWORD = 'test password 123'
PRODUCT = {'brand': 'Anker', 'model': 'A1263', 'demo': False, 'model_confirmed': True,
           'product_kind': 'power_bank', 'signs_answered': True}


def text(value):
    return SimpleNamespace(stop_reason='end_turn', model='fake-model', content=[SimpleNamespace(type='text', text=value)])


def tools(*calls):
    return SimpleNamespace(stop_reason='tool_use', model='fake-model', content=[
        SimpleNamespace(type='tool_use', id=f'call-{index}', name=name, input=arguments)
        for index, (name, arguments) in enumerate(calls)])


class FakeClaude:
    """Scripted stand-in for the Messages API; records every request."""
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request | {'messages': list(request['messages'])})
        return self.script.pop(0)


class AssistantTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.claude = FakeClaude([])
        self.app = create_app(root / 'test.sqlite3', official_service=OfficialService(root / 'official'), ai_client=self.claude)

    def account(self, username, consent=True):
        client = TestClient(self.app)
        self.assertEqual(client.post('/auth/register', json={'username': username, 'password': PASSWORD,
                                                             'privacy_consent': True}).status_code, 201)
        if consent:
            self.assertTrue(client.patch('/auth/preferences', json={'ai_consent': True}).json()['ai_consent'])
        return client

    def test_retrieval_finds_notices_and_safety_guidance(self):
        documents = OfficialService(Path(self.directory.name) / 'official').documents() + _safety_documents()
        self.assertEqual(search(documents, 'Anker A1263 리콜')[0]['id'], 'cpsc-25-338')
        self.assertEqual(search(documents, '연기 불꽃')[0]['id'], 'safety-emergency')
        self.assertEqual(search(documents, 'zzz-unknown-9999'), [])

    def test_nothing_is_sent_without_ai_consent(self):
        client = self.account('noconsent', consent=False)
        self.assertEqual(client.post('/assistant', json={'question': '내 배터리 괜찮나요?'}).status_code, 403)
        self.assertEqual(TestClient(self.app).post('/assistant', json={'question': '질문'}).status_code, 401)
        self.assertEqual(self.claude.requests, [])

    def test_agent_uses_tools_cites_sources_and_remembers(self):
        client = self.account('owner')
        self.assertEqual(client.post('/products', json=PRODUCT).status_code, 201)
        self.claude.script += [tools(('get_my_products', {}), ('search_official_documents', {'query': 'Anker A1263'}),
                                     ('check_product', {'brand': 'Anker', 'model': 'A1263', 'sale_region': 'KR'})),
                               text('A1263은 미국 공지 후보(cpsc-25-338)가 있습니다. 일련번호를 확인하세요.')]
        reply = client.post('/assistant', json={'question': '내 보조배터리 리콜인가요?'})
        self.assertEqual(reply.status_code, 200, reply.text)
        body = reply.json()
        self.assertTrue(body['ai_generated'])
        self.assertIn('cpsc-25-338', body['answer'])
        self.assertEqual([s['id'] for s in body['sources']], ['cpsc-25-338'])
        self.assertTrue(body['sources'][0]['url'].startswith('https://www.cpsc.gov/Recalls/'))
        mine, found, checked = [r['content'] for r in self.claude.requests[1]['messages'][-1]['content']]
        self.assertIn('A1263', mine)
        self.assertIn('qualifying serial numbers', found)
        self.assertIn('needs_information', checked)

        self.claude.script.append(text('지난번에 물어보신 A1263 이야기입니다.'))
        client.post('/assistant', json={'question': '아까 그 제품 다시 알려줘'})
        replayed = self.claude.requests[2]['messages']
        self.assertEqual([m['role'] for m in replayed], ['user', 'assistant', 'user'])
        self.assertEqual(replayed[0]['content'], '내 보조배터리 리콜인가요?')
        self.assertEqual(len(client.get('/assistant/history').json()), 4)
        self.assertEqual(len(client.get('/auth/export').json()['assistant_messages']), 4)

        other = self.account('stranger')
        self.assertEqual(other.get('/assistant/history').json(), [])
        self.claude.script += [tools(('get_my_products', {})), text('저장된 제품이 없습니다.')]
        other.post('/assistant', json={'question': '내 제품 보여줘'})
        self.assertNotIn('A1263', str(self.claude.requests[-1]['messages']))

        self.assertEqual(client.delete('/assistant/history').status_code, 204)
        self.assertEqual(client.get('/assistant/history').json(), [])

    def test_bad_tool_input_is_reported_to_the_model_not_the_user(self):
        client = self.account('badtool')
        self.claude.script += [tools(('check_product', {'brand': 'x' * 200, 'model': 'm', 'sale_region': 'KR'})), text('확인하지 못했습니다.')]
        self.assertEqual(client.post('/assistant', json={'question': '확인'}).status_code, 200)
        self.assertTrue(self.claude.requests[1]['messages'][-1]['content'][0]['is_error'])

    def test_feedback_is_stored_and_removed_with_the_account(self):
        client = self.account('reviewer', consent=False)
        self.assertEqual(client.post('/feedback', json={'rating': 4, 'comment': '다음 행동이 명확함'}).status_code, 201)
        self.assertEqual(client.post('/feedback', json={'rating': 9}).status_code, 422)
        self.assertEqual(client.get('/auth/export').json()['feedback'][0]['rating'], 4)


if __name__ == '__main__':
    unittest.main()
