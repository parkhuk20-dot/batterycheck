// Controlled synthetic text images: this is reproducible wiring/robustness evidence, not a real-photo benchmark.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = 'http://127.0.0.1:3114';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
const username = 'ocr_' + Date.now().toString(36);
const password = 'Synthetic-ocr-42-' + Date.now().toString(36);
let registered = false;
try {
  const response = await context.request.post(base + '/api/auth/register', { data: { username, password, privacy_consent: true } });
  assert.equal(response.status(), 201); registered = true;
  const fixtures = [
    { id: 'clear_anker', group: 'clear', brand: 'ANKER', model: 'A1263', size: 52 },
    { id: 'clear_iniu', group: 'clear', brand: 'INIU', model: 'BI-B41', size: 48 },
    { id: 'suffix_preservation', group: 'identifier_boundary', brand: 'ANKER', model: 'A1263-B', size: 52 },
    { id: 'small_text', group: 'small_text', brand: 'ANKER', model: 'A1263', size: 18 },
    { id: 'rotated_text', group: 'rotation', brand: 'INIU', model: 'BI-B41', size: 46, rotate: 8 },
    { id: 'low_contrast', group: 'low_contrast', brand: 'ANKER', model: 'A1263', size: 44, color: '#bbb' },
    { id: 'no_identity', group: 'negative_control', brand: null, model: null, size: 44 },
  ];
  const page = await context.newPage();
  const results = [];
  for (const fixture of fixtures) {
    const label = fixture.brand ? `${fixture.brand}<br>Model: ${fixture.model}<br>POWER BANK` : '5000 mAh<br>USB OUTPUT';
    await page.setContent(`<body style="margin:0;background:white"><div id="frame" style="width:1000px;height:420px;padding:60px;box-sizing:border-box;background:white"><div style="font:${fixture.size}px Arial;line-height:1.7;color:${fixture.color || '#000'};transform:rotate(${fixture.rotate || 0}deg)">${label}</div></div></body>`);
    const buffer = await page.locator('#frame').screenshot({ type: 'png' });
    const started = Date.now();
    const response = await context.request.post(base + '/api/identify', { data: { image_base64: buffer.toString('base64') }, timeout: 120000 });
    const output = await response.json();
    const exact = response.status() === 200 && (fixture.model ? output.model_candidates.includes(fixture.model) && output.brand_candidates.includes(fixture.brand) : output.model_candidates.length === 0 && output.brand_candidates.length === 0);
    results.push({ id: fixture.id, group: fixture.group, expected_brand: fixture.brand, expected_model: fixture.model,
      status: response.status(), engine: output.engine, exact, elapsed_ms: Date.now() - started,
      candidates: { brands: output.brand_candidates, models: output.model_candidates },
      confirmation_required: output.requires_confirmation === true, image_retained: output.photo_retained === true });
  }
  const grouped = Object.fromEntries([...new Set(results.map(r => r.group))].map(group => {
    const selected = results.filter(r => r.group === group);
    return [group, { correct: selected.filter(r => r.exact).length, total: selected.length }];
  }));
  const report = { scope: 'synthetic text-image diagnostic; not independent real-photo accuracy',
    metric: 'expected brand and complete model both present in candidates; negative control returns no candidates',
    total: results.length, correct: results.filter(r => r.exact).length, groups: grouped, results,
    mandatory_confirmation_all: results.every(r => r.confirmation_required), retained_images: results.some(r => r.image_retained) };
  fs.writeFileSync(path.join(root, 'verification/ocr-evaluation.json'), JSON.stringify(report, null, 2));
  assert.ok(report.mandatory_confirmation_all, 'all completed extractions require user confirmation');
  assert.equal(report.retained_images, false);
  assert.ok(results.find(r => r.id === 'no_identity').exact, 'no identity is fabricated');
  console.log(`OCR synthetic diagnostic: ${report.correct}/${report.total} exact candidates. See group results; no real-photo accuracy claim.`);
} finally {
  if (registered) {
    const removed = await context.request.delete(base + '/api/auth/account', { data: { password } });
    assert.equal(removed.status(), 204, 'test account cleanup');
  }
  await browser.close();
}
