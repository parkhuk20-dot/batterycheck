// Existing Chromium, headless only. Accounts/products are test-owned and removed in finally.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = 'http://127.0.0.1:3114';
const browser = await chromium.launch({ headless: true });
const contexts = [];
const accounts = [];
const runtimeErrors = [];
const cases = [];
const timestamp = Date.now().toString(36);
const password = 'Synthetic-test-' + timestamp + '-42!';
let page;
let ocrEvidence = { status: 'not_run' };

async function newContext() {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, serviceWorkers: 'block', acceptDownloads: true });
  contexts.push(context);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.origin === base ? route.continue() : route.abort('blockedbyclient');
  });
  return context;
}
async function waitText(locator, text, timeout = 15000) {
  await locator.filter({ hasText: text }).waitFor({ state: 'visible', timeout });
}
async function watchPage(context) {
  const current = await context.newPage();
  current.on('pageerror', error => runtimeErrors.push(error.message));
  await current.goto(base + '/?demo=1', { waitUntil: 'networkidle' });
  await waitText(current.locator('header'), '서버 연결됨');
  return current;
}
async function authenticate(current, username, register) {
  const panel = current.locator('section[aria-label="로그인"]');
  await panel.waitFor({ state: 'visible' });
  if (((await panel.getByRole('heading').innerText()) === '회원가입') !== register) await panel.locator('.auth-switch button').click();
  assert.equal(await panel.getByRole('heading').innerText(), register ? '회원가입' : '로그인');
  await panel.getByLabel('아이디', { exact: true }).fill(username);
  await panel.getByLabel('비밀번호', { exact: true }).fill(password);
  if (register) {
    assert.equal(await panel.locator('button[type=submit]').isDisabled(), true, 'registration requires explicit privacy consent');
    await panel.getByLabel(/아이디·암호화된 비밀번호/).check();
  }
  assert.equal(await panel.locator('button[type=submit]').innerText(), register ? '회원가입' : '로그인');
  await panel.locator('button[type=submit]').click();
  await waitText(current.locator('section[aria-label="계정 관리"]'), username + '님');
  await waitText(current.locator('.message'), '로그인했습니다');
}
async function deleteAccountUI(current) {
  const panel = current.locator('section[aria-label="계정 관리"]');
  if ((await panel.locator('details').first().getAttribute('open')) === null) {
    await panel.getByText('내 기록 · 계정 관리', { exact: true }).click();
  }
  await panel.getByText('계정과 모든 기록 삭제', { exact: true }).click();
  await panel.getByLabel('삭제 확인 비밀번호', { exact: true }).fill(password);
  await panel.getByRole('button', { name: '모든 내 기록 영구 삭제', exact: true }).click();
  await waitText(current.locator('.message'), '계정과 모든 기록을 삭제했습니다');
  assert.equal(await current.locator('.saved-product').count(), 0);
}
async function cleanupAccount(account) {
  const login = await account.context.request.post(base + '/api/auth/login', { data: { username: account.username, password } });
  if (login.status() === 401) return; // Already deleted by the UI.
  assert.equal(login.status(), 200, 'cleanup login failed for test account ' + account.username);
  const removed = await account.context.request.delete(base + '/api/auth/account', { data: { password } });
  assert.equal(removed.status(), 204, 'cleanup deletion failed for test account ' + account.username);
}

try {
  const context = await newContext();
  page = await watchPage(context);
  const meta = await (await context.request.get(base + '/api/meta')).json();
  assert.equal((await context.request.get(base + '/api/products')).status(), 401);
  cases.push('anonymous_vault_requires_authentication');

  const first = { context, username: 'e2e_a_' + timestamp };
  accounts.push(first); // Register cleanup before the action, including partial UI failures.
  await authenticate(page, first.username, true);
  cases.push('ui_registration_explicit_privacy_consent');
  const accountPanel = page.locator('section[aria-label="계정 관리"]');
  await waitText(accountPanel, '리콜 정보 변경 알림');
  await accountPanel.getByLabel('저장한 제품의 변경 사항을 알려주세요.', { exact: true }).click();
  await waitText(page.locator('.message'), '앱내 알림을 켰습니다');
  assert.equal(await accountPanel.getByLabel('저장한 제품의 변경 사항을 알려주세요.', { exact: true }).isChecked(), true);
  assert.equal((await (await context.request.get(base + '/api/auth/me')).json()).notification_consent, true);
  cases.push('explicit_notification_opt_in');

  const result = page.locator('article[aria-label="확인 결과"]');
  await page.getByRole('button', { name: '가상 예시 채우기', exact: true }).click();
  await page.getByRole('button', { name: '리콜 정보 확인', exact: true }).click();
  await waitText(result, '리콜 대상 제품과 일치합니다');
  assert.match(await result.innerText(), /가상 공지 기반 시연/);
  await page.getByLabel('모델명', { exact: true }).fill('');
  assert.equal(await result.count(), 0, 'editing the model removes the current result');
  await page.getByRole('button', { name: '리콜 정보 확인', exact: true }).click();
  await waitText(result, '제품 정보를 더 입력해주세요');
  await page.getByLabel('모델명', { exact: true }).fill('DEMO PB-100');
  cases.push('model_edit_invalidates_result');
  await page.getByLabel(/^LOT /).fill('');
  assert.equal(await result.count(), 0, 'editing invalidates current evidence');
  await page.getByRole('button', { name: '리콜 정보 확인', exact: true }).click();
  await waitText(result, '제품 정보를 조금 더 확인해주세요');
  await page.getByRole('button', { name: '내 제품에 저장', exact: true }).click();
  await waitText(page.locator('.message'), '내 제품에 저장했습니다');
  const products = await (await context.request.get(base + '/api/products')).json();
  assert.equal(products.length, 1);
  const productId = products[0].id;
  cases.push('typed_model_is_confirmed', 'synthetic_match', 'missing_lot', 'edit_invalidates_result', 'authenticated_save');

  await page.getByLabel(/^LOT /).fill('001A');
  await page.getByLabel('있음', { exact: true }).check();
  await page.getByLabel('연기', { exact: true }).check();
  await waitText(page.locator('main > .risk'), '긴급 상황 안내');
  await page.getByRole('button', { name: '리콜 정보 확인', exact: true }).click();
  await waitText(result, '리콜 대상 제품과 일치합니다');
  await page.getByRole('button', { name: '다시 확인한 결과 저장', exact: true }).click();
  await waitText(page.locator('.message'), '내 제품에 저장했습니다');
  const saved = page.locator('.saved-product').filter({ hasText: 'DEMO PB-100' });
  await saved.getByRole('combobox').selectOption('user_completed');
  await waitText(page.locator('.message'), '사용 재개 허가나 회수 인증이 아닙니다');
  await waitText(saved.locator('.risk'), '긴급 상황 안내');
  await saved.getByRole('button', { name: '확인 이력', exact: true }).click();
  await waitText(page.locator('.history'), '과거 기록입니다');
  assert.equal(await page.locator('.history article[aria-label="과거 확인 기록"]').count(), 2);
  await waitText(page.locator('.history'), '제품 정보를 조금 더 확인해주세요');
  await page.getByRole('button', { name: '이력 닫기', exact: true }).click();
  cases.push('recheck_history', 'action_self_report_preserves_emergency');

  await page.getByLabel('데모 출처 상태', { exact: false }).selectOption('outage');
  await page.getByRole('button', { name: '리콜 정보 확인', exact: true }).click();
  await waitText(result, '리콜 여부를 확인하지 못했습니다');
  await waitText(result, '불러오지 못함');
  await page.route('**/api/checks', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '조회 미완료: 테스트 서버 장애' }) }));
  await page.getByRole('button', { name: '리콜 정보 확인', exact: true }).click();
  await waitText(page.locator('.error'), '조회 미완료');
  assert.equal(await result.count(), 0, 'server failure removes previous current result');
  await waitText(page.locator('main > .risk'), '긴급 상황 안내');
  await page.unroute('**/api/checks');
  await context.setOffline(true);
  await waitText(page.locator('.error'), '오프라인입니다');
  await waitText(page.locator('main > .risk'), '긴급 상황 안내');
  await page.locator('main > .risk a[href="tel:119"]').waitFor({ state: 'visible' });
  await context.setOffline(false);
  cases.push('source_outage', 'http_failure_keeps_local_emergency', 'offline_keeps_local_emergency');

  await page.getByRole('button', { name: '로그아웃', exact: true }).click();
  await waitText(page.locator('.message'), '로그아웃했습니다');
  assert.equal(await page.locator('.saved-product').count(), 0);
  assert.equal((await context.request.get(base + '/api/products')).status(), 401);
  assert.equal(await page.locator('section[aria-label="로그인"]').getByLabel('비밀번호', { exact: true }).inputValue(), '', 'sign-up password is cleared on logout');
  await authenticate(page, first.username, false);
  await saved.waitFor({ state: 'visible' });
  await page.getByRole('button', { name: '로그아웃', exact: true }).click();
  await waitText(page.locator('.message'), '로그아웃했습니다');
  assert.equal(await page.locator('section[aria-label="로그인"]').getByLabel('비밀번호', { exact: true }).inputValue(), '', 'login password is cleared on logout');
  await authenticate(page, first.username, false);
  await saved.waitFor({ state: 'visible' });
  cases.push('logout_clears_private_screen', 'ui_login_restores_own_vault', 'logout_clears_login_password');

  if (meta.ocr?.available) {
    // Applying a different product must not retain fields/history from the previous one.
    await saved.getByRole('button', { name: '입력 불러오기', exact: true }).click();
    await page.getByLabel('데모 출처 상태', { exact: false }).selectOption('outage');
    assert.equal(await page.getByLabel(/^제조일 /).inputValue(), '2026-02-15');
    assert.equal(await page.getByLabel(/^구매 국가/).inputValue(), 'DEMO-KR');
    assert.equal(await page.getByLabel(/^LOT /).inputValue(), '001A');
    assert.equal(await page.getByLabel('연기', { exact: true }).isChecked(), true);
    await saved.getByRole('button', { name: '확인 이력', exact: true }).click();
    await waitText(page.locator('.history'), '과거 기록입니다');
    const labelPage = await context.newPage();
    await labelPage.setContent('<html><body style="margin:0;background:white"><div id="label" style="width:880px;padding:60px;color:#000;font:52px Arial;line-height:1.7">ANKER<br>Model: A1263<br>POWER BANK</div></body></html>');
    const label = await labelPage.locator('#label').screenshot({ type: 'png' });
    await labelPage.close();
    const identified = page.waitForResponse(response => response.url() === base + '/api/identify', { timeout: 120000 });
    await page.getByLabel('사진으로 확인', { exact: true }).setInputFiles({ name: 'synthetic-label.png', mimeType: 'image/png', buffer: label });
    const response = await identified;
    const payload = await response.json();
    assert.equal(response.status(), 200, JSON.stringify(payload));
    assert.equal(payload.processing, 'local');
    assert.equal(payload.photo_retained, false);
    assert.equal(payload.requires_confirmation, true);
    assert.equal(payload.model_confirmed, false);
    assert.ok(payload.brand_candidates.includes('ANKER'));
    assert.ok(payload.model_candidates.includes('A1263'));
    await waitText(page.locator('.ocr-result'), 'A1263');
    await waitText(page.locator('.ocr-result'), '이 정보가 맞나요?');
    assert.equal(await page.getByLabel('브랜드', { exact: true }).inputValue(), 'DEMO LAB', 'a photo reading is not applied before the user confirms it');
    const candidateRequest = page.waitForRequest(request => request.url() === base + '/api/checks' && request.method() === 'POST');
    await page.getByRole('button', { name: '맞아요, 리콜 확인', exact: true }).click();
    const sent = (await candidateRequest).postDataJSON();
    assert.deepEqual([sent.brand, sent.model, sent.model_confirmed, sent.scenario, sent.demo, sent.lot, sent.signs], ['ANKER', 'A1263', true, 'complete', false, null, []]);
    assert.equal(await page.getByLabel('가상 DEMO LAB 제품으로 시연합니다.', { exact: true }).isChecked(), false);
    assert.equal(await page.getByLabel('데모 출처 상태', { exact: false }).count(), 0);
    assert.equal(await page.getByLabel(/^제조일 /).inputValue(), '');
    assert.equal(await page.getByLabel(/^구매 국가/).inputValue(), '');
    assert.equal(await page.getByLabel(/^LOT /).inputValue(), '');
    assert.equal(await page.getByLabel('연기', { exact: true }).count(), 0);
    assert.equal(await page.locator('.history').count(), 0);
    assert.equal(await page.getByRole('button', { name: '다시 확인한 결과 저장', exact: true }).count(), 0);
    cases.push('ocr_clears_previous_conditions_demo_outage_and_history');
    await waitText(result, '제품 정보를 조금 더 확인해주세요');
    ocrEvidence = { status: 'passed', engine: payload.engine, fixture: 'generated black text on white PNG; no personal photo', brand: 'ANKER', model: 'A1263', photo_retained: false, requires_confirmation: true };
    cases.push('real_local_ocr_synthetic_label', 'photo_reading_requires_user_confirmation');
  } else {
    ocrEvidence = { status: 'not_available', reason: 'meta.ocr.available=false; no OCR success claim' };
    await page.getByLabel('브랜드', { exact: true }).fill('ANKER');
    await page.getByLabel('가상 DEMO LAB 제품으로 시연합니다.', { exact: true }).uncheck();
    await page.getByLabel('브랜드', { exact: true }).fill('ANKER');
    await page.getByLabel('모델명', { exact: true }).fill('A1263');
  }
  await page.getByRole('button', { name: '리콜 정보 확인', exact: true }).click();
  await waitText(result, '제품 정보를 조금 더 확인해주세요');
  await waitText(result, '일련번호');
  await waitText(result, '미국');
  const official = result.getByRole('link', { name: '공식 공지와 조치 방법 보기 ↗', exact: true });
  assert.match(await official.getAttribute('href'), /^https:\/\/www\.cpsc\.gov\/Recalls\/2025\//);
  assert.equal(await result.locator('.status').innerText(), '제품 정보를 조금 더 확인해주세요');
  assert.equal((await (await context.request.get(base + '/api/products')).json()).length, 1, 'actual product is queried but not stored by this test');
  cases.push('official_anker_candidate', 'official_link', 'serial_and_us_scope_pending_review');

  await page.screenshot({ path: path.join(root, 'verification/desktop-v2.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(root, 'verification/mobile-v2.png'), fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile viewport has no horizontal overflow');
  await page.setViewportSize({ width: 1280, height: 1000 });
  cases.push('desktop_and_mobile_layout');

  const otherContext = await newContext();
  const otherPage = await watchPage(otherContext);
  const second = { context: otherContext, username: 'e2e_b_' + timestamp };
  accounts.push(second);
  await authenticate(otherPage, second.username, true);
  assert.equal(await otherPage.locator('.saved-product').count(), 0);
  assert.deepEqual(await (await otherContext.request.get(base + '/api/products')).json(), []);
  assert.equal((await otherContext.request.get(base + '/api/products/' + productId + '/history')).status(), 404);
  assert.equal((await otherContext.request.delete(base + '/api/products/' + productId)).status(), 404);
  assert.equal((await (await context.request.get(base + '/api/products')).json()).length, 1);
  await deleteAccountUI(otherPage);
  second.deleted = true;
  cases.push('two_account_ui_and_api_isolation', 'second_account_deleted');

  await accountPanel.getByText('내 기록 · 계정 관리', { exact: true }).click();
  const downloadPromise = page.waitForEvent('download');
  await accountPanel.getByRole('button', { name: '내 기록 내려받기', exact: true }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const exported = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  assert.equal(exported.account.username, first.username);
  assert.equal(exported.products.length, 1);
  assert.equal(exported.history[productId].checks.length, 2);
  assert.ok(!JSON.stringify(exported).includes(password));
  assert.ok(!('password_hash' in exported.account));
  await download.delete();
  cases.push('ui_export_own_records_no_password');

  // The displayed form is ANKER, so this history belongs to an unselected saved product.
  assert.equal(await page.getByLabel('모델명', { exact: true }).inputValue(), 'A1263');
  await saved.getByRole('button', { name: '확인 이력', exact: true }).click();
  await waitText(page.locator('.history'), '과거 기록입니다');
  await saved.getByRole('button', { name: '삭제', exact: true }).click();
  await waitText(page.locator('.message'), '제품과 확인 기록을 삭제했습니다');
  assert.equal(await page.locator('.saved-product').count(), 0);
  assert.equal(await page.locator('.history').count(), 0, 'deleting an unselected history product clears visible history');
  assert.equal(await page.getByLabel('모델명', { exact: true }).inputValue(), 'A1263');
  cases.push('delete_unselected_product_clears_its_visible_history');
  assert.equal((await context.request.get(base + '/api/products/' + productId + '/history')).status(), 404);
  await deleteAccountUI(page);
  first.deleted = true;
  assert.equal((await context.request.get(base + '/api/auth/me')).status(), 401);
  const deletedLogin = await context.request.post(base + '/api/auth/login', { data: { username: first.username, password } });
  assert.equal(deletedLogin.status(), 401);
  cases.push('ui_product_delete_cascades_history', 'ui_account_delete_revokes_session_and_login');
  assert.deepEqual(runtimeErrors, [], 'no client runtime errors');
  fs.writeFileSync(path.join(root, 'verification/browser-result-v2.json'), JSON.stringify({ status: 'passed', checked_at: new Date().toISOString(), browser: 'existing Chromium headless', desktop_focus_changed: false, browser_network_scope: '127.0.0.1:3114 only; service workers blocked for deterministic network-failure tests', official_api_sync_executed: false, cases, ocr: ocrEvidence, runtime_errors: runtimeErrors }, null, 2));
  console.log('PASS: ' + cases.length + ' browser cases, OCR=' + ocrEvidence.status + ', owned test accounts removed');
} catch (error) {
  if (page) {
    await page.screenshot({ path: path.join(root, 'verification/browser-failure-v2.png'), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(root, 'verification/browser-failure-v2.json'), JSON.stringify({ error: error.message, cases_completed: cases, ocr: ocrEvidence, text: await page.locator('body').innerText() }, null, 2));
  }
  throw error;
} finally {
  const cleanupFailures = [];
  for (const account of accounts) {
    if (!account.deleted) {
      try { await account.context.setOffline(false); await cleanupAccount(account); }
      catch (error) { cleanupFailures.push(error.message); }
    }
  }
  await browser.close();
  if (cleanupFailures.length) throw new Error('Test account cleanup needs attention: ' + cleanupFailures.join('; '));
}
