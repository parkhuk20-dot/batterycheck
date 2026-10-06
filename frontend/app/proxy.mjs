// Fixed backend destination; only this application's opaque session cookie is forwarded.
const allowedPaths = /^(health|meta|risk|checks|identify|sources\/(?:status|sync)|auth\/(?:register|login|logout|me|preferences|export|account)|assistant(?:\/history)?|feedback|notifications(?:\/[a-f0-9-]+\/read)?|products(?:\/[a-f0-9-]{36}(?:\/(?:action|history))?)?)$/;

async function limitedBody(request, limit) {
  if (!request.body) return undefined;
  const reader = request.body.getReader();
  const chunks = [];
  let length = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) { await reader.cancel(); throw new Error('too_large'); }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks).toString('utf8');
}

export async function proxy(request, context) {
  const { path } = await context.params;
  const endpoint = path.join('/');
  if (!allowedPaths.test(endpoint)) return Response.json({ detail: '지원하지 않는 경로입니다.' }, { status: 404 });
  const origins = (process.env.BATTERYCHECK_ALLOWED_ORIGINS || 'http://127.0.0.1:3114,http://localhost:3114').split(',').map(s => s.trim()).filter(Boolean);
  const allowedHosts = new Set(origins.map(value => new URL(value).host));
  const host = request.headers.get('host') || new URL(request.url).host;
  if (!allowedHosts.has(host)) return Response.json({ detail: '허용되지 않은 Host입니다.' }, { status: 400 });
  const origin = request.headers.get('origin');
  if (origin && !origins.includes(origin)) return Response.json({ detail: '허용되지 않은 웹 출처입니다.' }, { status: 403 });
  if (!['GET', 'HEAD'].includes(request.method) && request.headers.get('sec-fetch-site') === 'cross-site') {
    return Response.json({ detail: '다른 사이트에서 요청할 수 없습니다.' }, { status: 403 });
  }
  const limit = endpoint === 'identify' ? 6 * 1024 * 1024 : 16384;
  if (Number(request.headers.get('content-length') || 0) > limit) return Response.json({ detail: '입력이 너무 큽니다.' }, { status: 413 });
  let body;
  try { body = request.method === 'GET' ? undefined : await limitedBody(request, limit); }
  catch { return Response.json({ detail: '입력이 너무 큽니다.' }, { status: 413 }); }
  const headers = { 'Content-Type': 'application/json' };
  const cookie = request.headers.get('cookie')?.split(';').map(s => s.trim()).find(s => /^batterycheck_session=[A-Za-z0-9_-]+$/.test(s));
  if (cookie) headers.Cookie = cookie;
  if (origin) headers.Origin = origin;
  try {
    const base = process.env.BATTERYCHECK_BACKEND_URL || 'http://127.0.0.1:8814';
    const response = await fetch(`${base}/${endpoint}`, {
      method: request.method, headers, body: body || undefined, cache: 'no-store',
      signal: AbortSignal.timeout(['identify', 'assistant'].includes(endpoint) ? 120000 : endpoint === 'sources/sync' ? 60000 : 10000),
    });
    const text = await response.text();
    const responseHeaders = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
    const sessionCookie = response.headers.get('set-cookie');
    if (sessionCookie?.startsWith('batterycheck_session=')) responseHeaders['Set-Cookie'] = sessionCookie;
    return new Response(response.status === 204 ? null : text, { status: response.status, headers: responseHeaders });
  } catch {
    return Response.json({ detail: '서버에 연결하지 못해 리콜 여부를 확인하지 못했습니다. 잠시 후 다시 확인해주세요.' }, { status: 503 });
  }
}
