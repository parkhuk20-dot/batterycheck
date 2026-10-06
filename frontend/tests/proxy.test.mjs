import { test } from 'node:test';
import assert from 'node:assert/strict';
import { proxy } from '../app/proxy.mjs';

const context = path => ({ params: Promise.resolve({ path: path.split('/') }) });
const request = (path = 'checks', init = {}) => new Request(`http://127.0.0.1:3114/api/${path}`, init);

test('blocked routes cannot reach an arbitrary backend path', async () => {
  const response = await proxy(request('../private'), context('../private'));
  assert.equal(response.status, 404);
});

test('foreign web origin is rejected before network access', async () => {
  const response = await proxy(request('checks', { method: 'POST', headers: { origin: 'https://untrusted.example' }, body: '{}' }), context('checks'));
  assert.equal(response.status, 403);
});

test('oversized input is rejected before network access', async () => {
  const response = await proxy(request('checks', { method: 'POST', body: 'x'.repeat(17000) }), context('checks'));
  assert.equal(response.status, 413);
});

test('backend outage is surfaced as incomplete instead of a negative recall verdict', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('fixture outage'); };
  try {
    const response = await proxy(request(), context('checks'));
    assert.equal(response.status, 503);
    assert.match((await response.json()).detail, /조회 미완료/);
  } finally { globalThis.fetch = original; }
});

test('proxy does not forward credentials and preserves error status', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:8814/checks');
    assert.equal(options.headers.Cookie, undefined);
    assert.equal(options.headers.Authorization, undefined);
    assert.equal(options.cache, 'no-store');
    return Response.json({ detail: 'fixture invalid input' }, { status: 422 });
  };
  try {
    const response = await proxy(request('checks', { method: 'POST', headers: { cookie: 'private=fixture' }, body: '{}' }), context('checks'));
    assert.equal(response.status, 422);
  } finally { globalThis.fetch = original; }
});

test('deletion preserves a body-free 204 response', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(null, { status: 204 });
  try {
    const id = '12345678-1234-1234-1234-123456789abc';
    const response = await proxy(request(`products/${id}`, { method: 'DELETE' }), context(`products/${id}`));
    assert.equal(response.status, 204);
    assert.equal(await response.text(), '');
  } finally { globalThis.fetch = original; }
});


test('external Host is rejected even without an Origin', async () => {
  const response = await proxy(request('products', { headers: { host: 'untrusted.example' } }), context('products'));
  assert.equal(response.status, 400);
});

test('loopback Origin remains valid when Next uses its internal localhost URL', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ status: 'fixture' });
  try {
    const response = await proxy(new Request('http://localhost:3114/api/checks', { method: 'POST', headers: { origin: 'http://127.0.0.1:3114', host: '127.0.0.1:3114' }, body: '{}' }), context('checks'));
    assert.equal(response.status, 200);
  } finally { globalThis.fetch = original; }
});

test('only application session passes and HttpOnly set-cookie returns to browser', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(options.headers.Cookie, 'batterycheck_session=opaque_test-1');
    assert.equal(options.headers.Authorization, undefined);
    return Response.json({ id: 'test' }, { headers: { 'set-cookie': 'batterycheck_session=new-token; HttpOnly; Path=/; SameSite=Strict' } });
  };
  try {
    const response = await proxy(request('auth/me', { headers: { cookie: 'other=secret; batterycheck_session=opaque_test-1', authorization: 'Bearer do-not-forward' } }), context('auth/me'));
    assert.match(response.headers.get('set-cookie'), /HttpOnly/);
    assert.equal(response.headers.get('cache-control'), 'no-store');
  } finally { globalThis.fetch = original; }
});

test('account deletion sends password body, session clearing cookie is preserved', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(options.body, '{"password":"test-password"}');
    return new Response(null, { status: 204, headers: { 'set-cookie': 'batterycheck_session=""; Max-Age=0; Path=/; HttpOnly' } });
  };
  try {
    const response = await proxy(request('auth/account', { method: 'DELETE', body: '{"password":"test-password"}' }), context('auth/account'));
    assert.equal(response.status, 204);
    assert.match(response.headers.get('set-cookie'), /Max-Age=0/);
  } finally { globalThis.fetch = original; }
});

test('cross-site mutation without Origin is rejected', async () => {
  const response = await proxy(request('auth/logout', { method: 'POST', headers: { 'sec-fetch-site': 'cross-site' }, body: '{}' }), context('auth/logout'));
  assert.equal(response.status, 403);
});

test('photo has a separate bound and does not loosen ordinary routes', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ requires_confirmation: true });
  try {
    const body = JSON.stringify({ image_base64: 'x'.repeat(18000) });
    assert.equal((await proxy(request('identify', { method: 'POST', body }), context('identify'))).status, 200);
    assert.equal((await proxy(request('checks', { method: 'POST', body }), context('checks'))).status, 413);
    assert.equal((await proxy(request('identify', { method: 'POST', headers: { 'content-length': '7000000' }, body: '{}' }), context('identify'))).status, 413);
  } finally { globalThis.fetch = original; }
});

test('configured HTTPS origin and fixed internal destination support deployment', async () => {
  const original = globalThis.fetch;
  const previousOrigins = process.env.BATTERYCHECK_ALLOWED_ORIGINS;
  const previousBackend = process.env.BATTERYCHECK_BACKEND_URL;
  process.env.BATTERYCHECK_ALLOWED_ORIGINS = 'https://battery.example';
  process.env.BATTERYCHECK_BACKEND_URL = 'http://backend:8814';
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'http://backend:8814/health');
    assert.equal(options.headers.Origin, 'https://battery.example');
    return Response.json({ status: 'ok' });
  };
  try {
    const response = await proxy(new Request('http://localhost:3114/api/health', { headers: { host: 'battery.example', origin: 'https://battery.example' } }), context('health'));
    assert.equal(response.status, 200);
  } finally {
    globalThis.fetch = original;
    if (previousOrigins === undefined) delete process.env.BATTERYCHECK_ALLOWED_ORIGINS; else process.env.BATTERYCHECK_ALLOWED_ORIGINS = previousOrigins;
    if (previousBackend === undefined) delete process.env.BATTERYCHECK_BACKEND_URL; else process.env.BATTERYCHECK_BACKEND_URL = previousBackend;
  }
});
