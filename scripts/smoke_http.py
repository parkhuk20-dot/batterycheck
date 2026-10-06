"""Actual Next.js -> FastAPI -> SQLite smoke, with isolated temporary test accounts."""
import http.cookiejar
import json
import secrets
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

BASE = 'http://127.0.0.1:3114'


class Client:
    def __init__(self):
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method='GET', body=None, expected=200):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(BASE + '/api/' + path, data=data, method=method,
                          headers={'Content-Type': 'application/json', 'Origin': BASE})
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            payload = response.read()
            assert response.status == expected, (path, response.status, payload.decode(errors='replace'))
            return json.loads(payload) if payload else None


with urlopen(BASE, timeout=15) as response:
    html = response.read().decode()
    assert response.status == 200
    assert '배터리체크' in html and '119 전화 연결' in html
anonymous, first, second = Client(), Client(), Client()
assert anonymous.request('health')['mode'] == 'evidence_assistant'
meta = anonymous.request('meta')
assert meta['accounts'] == 'local_password_sessions'
assert meta['notifications'] == 'consent_based_in_app'
assert meta['privacy']['external_product_transmission'] is False
anonymous.request('products', expected=401)
body = dict(brand='DEMO LAB', model='DEMO PB-100', model_confirmed=True,
            demo=True, manufactured_on='2026-02-15', sale_region='DEMO-KR',
            lot=None, signs=['smoke'], signs_answered=True)
assert anonymous.request('checks', 'POST', body)['status'] == 'needs_information'
password = 'Smoke-test-' + secrets.token_hex(12)
identities = [(first, 'smoke_a_' + secrets.token_hex(6)), (second, 'smoke_b_' + secrets.token_hex(6))]
registered = []
try:
    for client, username in identities:
        client.request('auth/register', 'POST', {'username': username, 'password': password,
                       'privacy_consent': True, 'notification_consent': True}, expected=201)
        registered.append(client)
        assert client.request('auth/me')['username'] == username
    saved = first.request('products', 'POST', body, expected=201)
    product_id = saved['id']
    assert second.request('products') == []
    second.request('products/' + product_id + '/history', expected=404)
    second.request('products/' + product_id, 'DELETE', expected=404)
    matched = first.request('products/' + product_id, 'PUT', body | {'lot': '001A'})
    assert matched['latest_result']['status'] == 'matched'
    updated = first.request('products/' + product_id + '/action', 'PATCH', {'state': 'user_completed'})
    assert updated['latest_result']['risk']['level'] == 'emergency'
    assert updated['action_state'] == 'user_completed'
    history = first.request('products/' + product_id + '/history')
    assert len(history['checks']) == 2
    assert history['checks'][1]['result']['status'] == 'needs_information'
    assert anonymous.request('checks', 'POST', body | {'scenario': 'outage'})['status'] == 'incomplete'
    real = anonymous.request('checks', 'POST', dict(brand='ANKER', model='A1263', model_confirmed=True))
    assert real['status'] == 'needs_information'
    assert real['synthetic'] is False and real['coverage_complete'] is False
    assert real['notices'][0]['outcome'] == 'pending_review'
    assert real['notices'][0]['source_url'].startswith('https://www.cpsc.gov/Recalls/')
    assert any(question['field'] == 'serial_number' for question in real['questions'])
    exported = first.request('auth/export')
    assert exported['account']['username'] == identities[0][1]
    assert len(exported['products']) == 1 and len(exported['history'][product_id]['checks']) == 2
    assert password not in json.dumps(exported) and 'password_hash' not in exported['account']
    first.request('auth/logout', 'POST', {}, expected=204)
    first.request('products', expected=401)
    first.request('auth/login', 'POST', {'username': identities[0][1], 'password': password})
    assert len(first.request('products')) == 1
    first.request('products/' + product_id, 'DELETE', expected=204)
    assert first.request('products') == []
    first.request('products/' + product_id + '/history', expected=404)
finally:
    for client in registered:
        client.request('auth/account', 'DELETE', {'password': password}, expected=204)
        client.request('auth/me', expected=401)
for client, username in identities:
    anonymous.request('auth/login', 'POST', {'username': username, 'password': password}, expected=401)
print('PASS: HTTP SSR + auth/consent -> proxy -> SQLite; account isolation, synthetic match/history/emergency, official candidate, export/logout/delete. No CPSC sync or personal images sent.')
