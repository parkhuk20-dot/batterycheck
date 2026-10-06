import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = 'http://127.0.0.1:3114';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: 'block' });
await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
const username = 'tabs_' + Date.now().toString(36);
const password = 'Tabs-test-private-42!';
let registered = false;
let releaseDelayedHistory;
let delayedHistoryDone;
function bounded(promise, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), 15000); })])
    .finally(() => clearTimeout(timer));
}
try {
  const response = await context.request.post(base + '/api/auth/register', { data: { username, password, privacy_consent: true } });
  assert.equal(response.status(), 201); registered = true;
  const saved = await context.request.post(base + '/api/products', { data: { brand: 'ANKER', model: 'A1263', model_confirmed: true } });
  assert.equal(saved.status(), 201);
  const productId = (await saved.json()).id;
  const first = await context.newPage(), second = await context.newPage();
  await Promise.all([first.goto(base, { waitUntil: 'networkidle' }), second.goto(base, { waitUntil: 'networkidle' })]);
  await first.locator('.saved-product').waitFor();
  await second.locator('.saved-product').waitFor();
  // Capture a real, already-authorized 200 response before logout; only delivery is delayed.
  const historyUrl = base + '/api/products/' + productId + '/history';
  let historyCaptured;
  const capturedHistory = new Promise(resolve => { historyCaptured = resolve; });
  const historyRelease = new Promise(resolve => { releaseDelayedHistory = resolve; });
  let historyFinished;
  delayedHistoryDone = new Promise(resolve => { historyFinished = resolve; });
  await second.route(historyUrl, async route => {
    try {
      const response = await route.fetch();
      const body = await response.body();
      historyCaptured({ status: response.status(), payload: JSON.parse(body.toString('utf8')) });
      await historyRelease;
      await route.fulfill({ response, body });
      historyFinished(null);
    } catch (error) {
      historyCaptured({ error: error.message });
      historyFinished(error);
      await route.abort().catch(() => {});
    }
  });
  await second.locator('.saved-product').getByRole('button', { name: '확인 이력', exact: true }).click();
  const captured = await bounded(capturedHistory, 'authorized history response was not captured');
  assert.equal(captured.error, undefined);
  assert.equal(captured.status, 200, 'history must have been authorized before logout');
  assert.equal(captured.payload.checks.length, 1);
  assert.equal(captured.payload.checks[0].input.model, 'A1263');
  await first.getByRole('button', { name: '로그아웃', exact: true }).click();
  await second.locator('section[aria-label="로그인"]').waitFor();
  assert.equal(await second.locator('.saved-product').count(), 0);
  assert.equal(await second.locator('.history').count(), 0);
  const delivered = second.waitForResponse(response => response.url() === historyUrl, { timeout: 15000 });
  const finished = second.waitForEvent('requestfinished', request => request.url() === historyUrl);
  releaseDelayedHistory();
  assert.equal((await delivered).status(), 200, 'the delayed response is a former authorized success, not a 403');
  assert.equal(await bounded(delayedHistoryDone, 'delayed history route did not finish'), null);
  await finished;
  await second.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  // Wait for the response body and subsequent render opportunities, not only logout's reset.
  await second.getByRole('button', { name: '리콜 정보 확인', exact: true }).waitFor({ state: 'visible' });
  assert.equal(await second.locator('section[aria-label="로그인"]').count(), 1);
  assert.equal(await second.locator('.history').count(), 0, 'late authorized history must not repopulate an anonymous tab');
  assert.equal(await second.locator('.saved-product').count(), 0);
  assert.equal(await second.getByLabel('브랜드', { exact: true }).inputValue(), '');
  await second.unroute(historyUrl);
  await first.getByLabel('아이디', { exact: true }).fill(username);
  await first.getByLabel('비밀번호', { exact: true }).fill(password);
  await first.getByRole('button', { name: '로그인', exact: true }).click();
  await second.locator('.saved-product').waitFor();
  const account = first.locator('section[aria-label="계정 관리"]');
  await account.getByText('내 기록 · 계정 관리', { exact: true }).click();
  await account.getByText('계정과 모든 기록 삭제', { exact: true }).click();
  await account.getByLabel('삭제 확인 비밀번호', { exact: true }).fill(password);
  await account.getByRole('button', { name: '모든 내 기록 영구 삭제', exact: true }).click();
  await second.locator('section[aria-label="로그인"]').waitFor();
  assert.equal(await second.locator('.saved-product').count(), 0);
  registered = false;
  fs.writeFileSync(path.join(root, 'verification/auth-tabs-result.json'), JSON.stringify({ status: 'passed', cases: ['logout_clears_other_tab', 'login_refreshes_other_tab', 'account_deletion_clears_other_tab', 'late_authorized_history_response_does_not_restore_private_state'] }, null, 2));
  console.log('PASS: 4 same-browser tab cases; logout/deletion clear private state, login refreshes, late authorized history is ignored');
} finally {
  releaseDelayedHistory?.();
  if (delayedHistoryDone) await bounded(delayedHistoryDone, 'delayed history cleanup timed out').catch(() => {});
  if (registered) {
    await context.request.post(base + '/api/auth/login', { data: { username, password } });
    const removed = await context.request.delete(base + '/api/auth/account', { data: { password } });
    assert.equal(removed.status(), 204);
  }
  await browser.close();
}
