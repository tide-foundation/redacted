// Exercise delayed browser file selection, readiness, upload, and cancellation.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const health = deferred(), upload = deferred(), posted = deferred();
const csrfToken = 'fixture-selection-csrf';
const uploadName = 'Health report – example.pdf';
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/api/service/**', async route => {
  const request = route.request();
  if (request.url().endsWith('/capabilities')) {
    await route.fulfill({ json: { secure_history: { available: false } } });
  } else if (request.url().endsWith('/health')) {
    await health.promise;
    await route.fulfill({ json: { model_installed: true, model_loaded: false, device: 'cpu' } });
  } else if (request.method() === 'POST') {
    assert.equal(new URL(request.url()).pathname, '/api/service/guest/documents');
    assert.equal(request.headers()['x-csrf-token'], csrfToken);
    assert.equal(request.headers()['x-document-name'], encodeURIComponent(uploadName));
    assert.equal(new URL(request.url()).searchParams.get('mode'), 'redact');
    assert.equal(request.postDataBuffer().toString(), '%PDF-fixture');
    posted.resolve();
    await upload.promise;
    await route.fulfill({ json: { id: '11111111-1111-4111-8111-111111111111' } });
  } else if (request.url().endsWith('/guest/current')) {
    await route.fulfill({ json: { document: null, csrf_token: csrfToken, expires_at: null } });
  } else throw new Error(`Unexpected API request: ${request.url()}`);
});
try {
  await page.goto(process.env.BASE_URL || 'http://127.0.0.1:4173');
  const submit = page.getByRole('button', { name: 'REDACT', exact: true });
  const input = page.locator('input[type=file]');
  const chooserEvent = page.waitForEvent('filechooser');
  await page.locator('.dropzone').click();
  const chooser = await chooserEvent;
  await page.locator('.file-wait-ring').waitFor();
  assert.equal(await page.locator('.file-wait-ring').evaluate(el => getComputedStyle(el).animationName), 'spin');
  assert.equal(await submit.isDisabled(), true);
  await chooser.setFiles({ name: uploadName, mimeType: 'application/pdf', buffer: Buffer.from('%PDF-fixture') });
  await page.getByText(uploadName, { exact: true }).waitFor();
  await page.getByText('Checking service…', { exact: true }).waitFor();
  assert.equal(await submit.isDisabled(), true);
  health.resolve();
  await page.waitForFunction(() => !document.querySelector('.redact-button').disabled);
  assert.equal(await page.locator('.file-wait-ring').count(), 0);
  // Guest session setup completes independently of delayed model readiness.
  assert.equal(await page.getByText('No files yet.', { exact: true }).count(), 1);
  const cancelChooser = page.waitForEvent('filechooser');
  await page.locator('.dropzone').click();
  await cancelChooser;
  await page.locator('.file-wait-ring').waitFor();
  await input.dispatchEvent('cancel');
  assert.equal(await page.locator('.file-wait-ring').count(), 0);
  assert.equal(await page.getByText(uploadName, { exact: true }).count(), 1);
  assert.equal(await submit.isEnabled(), true);
  await submit.click();
  await posted.promise;
  await page.locator('.file-wait-ring').waitFor();
  assert.match(await page.locator('.dropzone').innerText(), /Uploading…/);
  upload.resolve();
  await page.getByRole('button', { name: 'REDACT', exact: true }).waitFor();
  assert.equal(await page.locator('.file-wait-ring').count(), 0);
  assert.equal(await page.getByText(uploadName, { exact: true }).count(), 0);
  await page.getByText('No files yet.').waitFor();
  await input.setInputFiles({ name: 'invalid.txt', mimeType: 'text/plain', buffer: Buffer.from('invalid') });
  await page.locator('.notice[role=alert]').waitFor();
  assert.equal(await page.locator('.file-wait-ring').count(), 0);
  assert.equal(await submit.isDisabled(), true);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  assert.equal(await page.getByRole('radio', { name: 'Mask', exact: true }).isChecked(), true);
  await page.getByRole('button', { name: 'About redaction modes', exact: true }).click();
  assert.match(await page.getByRole('tooltip').innerText(), /Mask: masks with \*{6}\./);
  assert.deepEqual(errors, []);
  console.log('File selection passed: picker waiting, cancel, independent readiness, upload waiting, invalid file, Mask copy.');
} finally {
  health.resolve(); upload.resolve();
  await browser.close();
}
