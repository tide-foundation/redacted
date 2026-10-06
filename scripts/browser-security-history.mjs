// Fixture-only UI checks. Server tests verify real cookies, ownership and storage.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const errors = [], paths = [], revealRequests = [];
page.on('pageerror', error => errors.push(error.message));
const base = process.env.BASE_URL || 'http://127.0.0.1:4173';
const imageWarning = 'Images are preserved but are not scanned for sensitive data.';
const originals = { private_person: 'Alice Fixture Original ' + 'unbroken-original-value-'.repeat(16), private_date: '12 February 2001' };
const makeDocument = id => ({
  id, filename: 'Private report.pdf', created: new Date().toISOString(), status: 'complete', mode: 'redact', sensitivity: 75,
  source_type: 'pdf', layout_preserved: false, warning: imageWarning + ' Original layout unavailable; clean rewrite used.', counts: { private_person: 1, private_date: 1 },
});
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
let document = makeDocument('22222222-2222-4222-8222-222222222222');
let csrf = 'first-session';
const delayedPreview = deferred(), previewRequested = deferred();
let holdPreview = false, heldOriginal = null;
function holdOriginals() {
  heldOriginal = { ready: deferred(), release: deferred() };
  return heldOriginal;
}
await page.route('**/api/service/**', async route => {
  const request = route.request(), path = new URL(request.url()).pathname;
  paths.push(path);
  let body;
  if (path.endsWith('/tide/config')) return route.fulfill({ status: 503, json: { detail: 'Secure history is not configured.' } });
  if (path.endsWith('/tide/setup/v2/session')) return route.fulfill({ json: { unlocked: false } });
  if (path.endsWith('/tide/status')) return route.fulfill({ json: { state: 'stopped', configured: false, reachable: false, url: 'http://localhost:8080' } });
  if (path.endsWith('/health')) body = { model_installed: true, model_loaded: true, device: 'cpu' };
  // Even a capability response cannot manufacture a configured identity provider.
  else if (path.endsWith('/capabilities')) body = { secure_history: { available: true } };
  else if (path.endsWith('/guest/current')) body = { document, csrf_token: csrf, expires_at: document?.expires_at || null };
  else if (path.endsWith('/review')) body = {
    detections: [{ category: 'private_person', occurrence: 1, replacement: '******' }, { category: 'private_date', occurrence: 1, replacement: '**/**/**' }],
    scan_report: { sensitivity: 75, counts: document.counts, total_detections: 2, source_type: '.pdf', layout_preserved: false, ocr_performed: false,
      warnings: [imageWarning, 'Original layout unavailable; clean rewrite used.'], limitations: ['Images are not scanned for sensitive data.'] },
  };
  else if (path.endsWith('/revealed-detections')) {
    assert.equal(request.method(), 'GET');
    revealRequests.push(path);
    const held = heldOriginal;
    if (held) { held.ready.resolve(); await held.release.promise; }
    body = { values: Object.entries(originals).map(([category, original]) => ({ category, occurrence: 1, original })) };
  }
  else if (path.endsWith('/preview')) {
    previewRequested.resolve();
    if (holdPreview) await delayedPreview.promise;
    body = { text: 'A previous session preview must be discarded.' };
  } else throw new Error(`Unexpected API request: ${path}`);
  try { await route.fulfill({ json: body }); } catch (error) { if (!request.failure()) throw error; }
});
const revealValues = () => page.getByRole('button', { name: 'Reveal original values', exact: true });
const hideValues = () => page.getByRole('button', { name: 'Hide original values', exact: true });
async function assertConcealed() {
  assert.equal(await page.getByLabel('Original value concealed', { exact: true }).count(), 2);
  assert.equal(await revealValues().count(), 1);
  for (const original of Object.values(originals)) assert.equal(await page.getByText(original, { exact: true }).count(), 0);
}
async function replacementColumns() {
  return page.locator('.replacement-value').evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect();
    return { x: rect.x, width: rect.width };
  }));
}
async function assertFooter() {
  const footer = page.getByRole('contentinfo');
  await footer.waitFor();
  assert.equal(await footer.locator('a[href="https://github.com/tide-foundation/redacted"]').count(), 1);
  assert.equal(await footer.getByRole('link', { name: 'Full disclaimer', exact: true }).getAttribute('href'), '/disclaimer');
  assert.match(await footer.innerText(), /guarantee/i);
}
async function openReview() {
  await page.getByRole('button', { name: 'Review detections', exact: true }).click();
  await page.getByRole('region', { name: 'Detection review' }).waitFor();
}
try {
  await page.goto(`${base}/secure-history`);
  await page.getByRole('heading', { name: 'Keep your files. Keep them private.', exact: true }).waitFor();
  await assertFooter();
  assert.match(await page.locator('main').innerText(), /without an account/);
  assert.match(await page.locator('main').innerText(), /not configured/);
  assert.equal(await page.locator('.setup-command').count(), 0);
  await page.getByRole('button', { name: 'Set up TideCloak', exact: true }).click();
  await page.getByRole('heading', { name: 'Set up TideCloak', exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/secure-history/setup');
  await page.reload();
  await page.getByRole('heading', { name: 'Set up TideCloak', exact: true }).waitFor();
  await page.getByRole('button', { name: 'About secure history', exact: false }).click();
  assert.equal(await page.locator('.topbar').getByText('Secure history', { exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Back to redacting', exact: false }).click();
  await assertFooter();
  await page.getByRole('link', { name: 'Full disclaimer', exact: true }).click();
  await page.getByRole('heading', { name: 'Disclaimer', exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/disclaimer');
  await assertFooter();
  await page.reload();
  await page.getByRole('heading', { name: 'Disclaimer', exact: true }).waitFor();
  await assertFooter();
  await page.getByRole('button', { name: 'Back to redacting', exact: false }).click();
  await page.getByRole('button', { name: 'Review detections', exact: true }).waitFor();
  assert.equal(await page.locator('.document-row .doc-warning').innerText(), 'Original layout unavailable; clean rewrite used.');
  assert.equal(await page.locator('.document-row').getByText(imageWarning, { exact: false }).count(), 0);
  await page.getByRole('button', { name: 'Review detections', exact: true }).click();
  await page.getByRole('region', { name: 'Detection review' }).waitFor();
  await assertConcealed();
  assert.equal(await page.getByRole('region', { name: 'Detection review' }).getByText(imageWarning, { exact: true }).count(), 1);
  assert.equal(await revealValues().innerText(), 'Reveal values');
  assert.deepEqual(revealRequests, []);
  for (const original of Object.values(originals)) assert.equal(await page.getByText(original, { exact: true }).count(), 0);
  assert.equal(await page.getByText('Clean rewrite', { exact: true }).count(), 1);
  assert.equal(await page.getByText('Not performed', { exact: true }).count(), 1);
  assert.equal(await page.getByText('PDF', { exact: true }).count() > 0, true);
  assert.match(await page.getByRole('region', { name: 'Detection review' }).innerText(), /75/);
  assert.doesNotMatch(await page.getByRole('region', { name: 'Detection review' }).innerText(), /\d+%/);

  // A single action fetches every value. Long originals may wrap but must not
  // move or resize the replacement column at either desktop or mobile widths.
  await page.evaluate(() => document.fonts.ready);
  for (const viewport of [{ width: 1440, height: 1100 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const before = await replacementColumns();
    const requestsBefore = revealRequests.length;
    await revealValues().click();
    for (const original of Object.values(originals)) await page.getByText(original, { exact: true }).waitFor();
    assert.equal(revealRequests.length, requestsBefore + 1);
    assert.equal(await hideValues().count(), 1);
    assert.equal(await hideValues().innerText(), 'Hide values');
    const after = await replacementColumns();
    assert.equal(after.length, before.length);
    for (let index = 0; index < before.length; index++) {
      assert.ok(Math.abs(after[index].x - before[index].x) <= 0.5, `Replacement column moved at ${viewport.width}px`);
      assert.ok(Math.abs(after[index].width - before[index].width) <= 0.5, `Replacement column changed width at ${viewport.width}px`);
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    await hideValues().click();
    await assertConcealed();
  }
  await page.setViewportSize({ width: 1440, height: 1100 });

  // Hide cancels an outstanding bulk reveal; late data cannot restore values.
  const hidden = holdOriginals();
  await revealValues().click(); await hidden.ready.promise;
  await hideValues().click();
  hidden.release.resolve(); heldOriginal = null;
  await page.waitForTimeout(100);
  await assertConcealed();

  // Closing the panel also cancels an outstanding bulk request.
  const closed = holdOriginals();
  await revealValues().click(); await closed.ready.promise;
  await page.getByRole('button', { name: 'Close detection review', exact: true }).click();
  closed.release.resolve(); heldOriginal = null;
  await page.waitForTimeout(100);
  assert.equal(await page.getByRole('region', { name: 'Detection review' }).count(), 0);
  for (const original of Object.values(originals)) assert.equal(await page.getByText(original, { exact: true }).count(), 0);

  // Switching away clears both displayed values and pending requests.
  await openReview();
  await revealValues().click();
  for (const original of Object.values(originals)) await page.getByText(original, { exact: true }).waitFor();
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  for (const original of Object.values(originals)) await page.getByText(original, { exact: true }).waitFor({ state: 'detached' });
  await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }));
  await openReview();
  const tabHidden = holdOriginals();
  await revealValues().click(); await tabHidden.ready.promise;
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  tabHidden.release.resolve(); heldOriginal = null;
  await page.waitForTimeout(100);
  for (const original of Object.values(originals)) assert.equal(await page.getByText(original, { exact: true }).count(), 0);
  await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }));
  await openReview();
  await assertConcealed();
  await revealValues().click();
  for (const original of Object.values(originals)) await page.getByText(original, { exact: true }).waitFor();
  // Unconfigured Account action leads to information and clears open plaintext.
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('heading', { name: 'Keep your files. Keep them private.', exact: true }).waitFor();
  assert.equal(await page.getByRole('region', { name: 'Detection review' }).count(), 0);
  assert.equal(await page.getByText(originals.private_person, { exact: true }).count(), 0);
  await page.goBack();
  await page.getByRole('button', { name: 'Preview', exact: true }).waitFor();
  holdPreview = true;
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await previewRequested.promise;
  document = null; csrf = 'new-session';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.getByText('No files yet.', { exact: true }).waitFor();
  delayedPreview.resolve();
  await page.waitForTimeout(100);
  assert.equal(await page.locator('dialog').isVisible(), false);
  assert.equal(await page.getByText('A previous session preview must be discarded.', { exact: true }).count(), 0);

  // A reveal completing after expiry is discarded along with the working file.
  document = makeDocument('33333333-3333-4333-8333-333333333333');
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.getByRole('button', { name: 'Review detections', exact: true }).click();
  const expired = holdOriginals();
  await revealValues().click(); await expired.ready.promise;
  document = null; csrf = 'third-session';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.getByText('No files yet.', { exact: true }).waitFor();
  expired.release.resolve(); heldOriginal = null;
  await page.waitForTimeout(100);
  assert.equal(await page.getByText(originals.private_person, { exact: true }).count(), 0);
  assert.equal(await page.getByRole('region', { name: 'Detection review' }).count(), 0);

  document = { ...makeDocument('44444444-4444-4444-8444-444444444444'), expires_at: new Date(Date.now() + 5000).toISOString() };
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.getByRole('button', { name: 'Review detections', exact: true }).waitFor();
  await page.locator('input[type=file]').setInputFiles({ name: 'next.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-fixture') });
  await page.getByText('next.pdf', { exact: true }).waitFor();
  document = null;
  await page.getByText('No files yet.', { exact: true }).waitFor();
  assert.equal(await page.getByText('next.pdf', { exact: true }).count(), 0);
  assert.equal(await page.locator('input[type=file]').inputValue(), '');
  assert.equal(await page.getByRole('button', { name: 'REDACT', exact: true }).isDisabled(), true);
  assert.deepEqual(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length })), { local: 0, session: 0 });
  assert.equal(paths.some(path => path === '/api/service/documents' || path.includes('/history')), false);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await assertFooter();
  await page.getByRole('link', { name: 'Full disclaimer', exact: true }).click();
  await page.getByRole('heading', { name: 'Disclaimer', exact: true }).waitFor();
  await assertFooter();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  assert.deepEqual(errors, []);
  console.log('Security-history UI passed: one bulk reveal/hide control, stable columns at desktop/mobile widths, hide/close/tab/expiry cancellation, account information, detail-only image warning, permanent footer/disclaimer navigation, truthful report and no browser persistence.');
} finally { heldOriginal?.release.resolve(); delayedPreview.resolve(); await browser.close(); }
