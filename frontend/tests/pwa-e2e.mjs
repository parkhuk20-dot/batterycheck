import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  await context.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:3114/', { waitUntil: 'networkidle' });
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
  const keys = await page.evaluate(async () => {
    const all = [];
    for (const key of await caches.keys()) for (const req of await (await caches.open(key)).keys()) all.push(new URL(req.url).pathname);
    return all.sort();
  });
  assert.deepEqual(keys, ['/icon.svg', '/offline.html']);
  const manifest = await (await context.request.get('http://127.0.0.1:3114/manifest.webmanifest')).json();
  assert.equal(manifest.name, '배터리체크');
  await context.setOffline(true);
  await page.reload({ waitUntil: 'domcontentloaded' });
  assert.match(await page.locator('body').innerText(), /지금은 리콜 정보와 내 제품을 확인할 수 없습니다/);
  await page.locator('a[href="tel:119"]').waitFor();
  assert.equal(await page.locator('.saved-product').count(), 0);
  fs.writeFileSync(path.join(root, 'verification/pwa-result.json'), JSON.stringify({ status: 'passed', cached_paths: keys,
    cases: ['manifest', 'public_only_cache', 'offline_navigation', 'no_private_offline_records', 'offline_emergency_link'] }, null, 2));
  console.log('PASS: PWA manifest, public-only cache, offline guidance, no private record caching');
} finally { await browser.close(); }
