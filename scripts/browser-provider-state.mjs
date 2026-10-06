// Isolated component fixture: no production identity injection or fake accounts.
// Exercises provider/lifecycle boundaries only, not authentication or encryption.
import { chromium } from '@playwright/test';
import { createServer as createViteServer } from 'vite';
import react from '@vitejs/plugin-react';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';

const fixtureId = resolve('__provider_fixture__.tsx');
const fixture = `
import { createRoot } from 'react-dom/client';
import App from '/src/App.tsx';
import '/src/globals.css';
let state = {status:'signed-out'};
const listeners = new Set();
let pendingReveal, pendingFilename; let holdFilename = true;
let reviewCalls = 0, revealCalls = 0;
let signIns = 0, signOuts = 0, saveCalls = 0, allowSave = false;
const review = {detections:[{category:'private_person',occurrence:1},{category:'private_date',occurrence:1}],scan_report:{sensitivity:50,counts:{private_person:1,private_date:1},total_detections:2,source_type:'.pdf',layout_preserved:true,ocr_performed:false,warnings:[],limitations:[]}};
const provider = {
  getSnapshot: () => state,
  subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
  signIn: async () => { signIns++; },
  signOut: async () => { signOuts++; state = {status:'signed-out'}; listeners.forEach(listener => listener()); },
  list: async () => [{id:state.ownerKey === 'owner-a' ? 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' : 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',created:'2026-10-01T00:00:00Z',status:'complete',mode:'redact',sensitivity:50,source_type:'pdf',counts:{private_person:1}}],
  filename: async () => holdFilename ? new Promise(resolve => { pendingFilename = resolve; }) : null,
  review: async () => { reviewCalls++; return review; },
  reveal: () => { revealCalls++; return new Promise(resolve => { pendingReveal = resolve; }); },
  remove: async () => {}, download: async () => new Blob(['fixture']),
  collectGuest: async (id, source) => ({source,manifest:new Blob(['fixture manifest']),outputs:{pdf:new Blob(),docx:new Blob(),txt:new Blob()}}),
  save: async input => { saveCalls++; if (!allowSave) throw new Error('Controlled save failure'); if (await input.source.text() !== '%PDF-autosave-fixture') throw new Error('Wrong source handed off'); return {...input.metadata,filename:input.filename,id:'saved-fixture'}; },
  recoverOriginal: async () => { throw new Error('No encryption in this fixture'); },
};
window.fixture = {
  identity: next => { state = next; listeners.forEach(listener => listener()); },
  reveal: () => pendingReveal?.([{category:'private_person',occurrence:1,replacement:'******',original:'TRANSIENT FIXTURE ORIGINAL'},{category:'private_date',occurrence:1,replacement:'**/**/**',original:'TRANSIENT FIXTURE DATE'}]),
  filename: () => { holdFilename = false; pendingFilename?.(null); },
  reviews: () => reviewCalls, reveals: () => revealCalls,
  signIns: () => signIns, signOuts: () => signOuts,
  allowSave: () => { allowSave = true; }, saves: () => saveCalls,
};
createRoot(document.getElementById('root')).render(<App historyProvider={provider}/>);
`;
const vite = await createViteServer({
  cacheDir: 'node_modules/.vite-browser-provider-state', configFile: false, appType: 'custom',
  plugins: [react(), { name: 'isolated-provider-fixture', resolveId: id => id === '/__provider_fixture__.tsx' ? fixtureId : undefined, load: id => id === fixtureId ? fixture : undefined }],
  server: { middlewareMode: true, hmr: false },
});
const server = createServer(async (request, response) => {
  if (request.url === '/') {
    const html = await vite.transformIndexHtml('/', '<!doctype html><html><head><title>Provider lifecycle fixture</title></head><body><div id="root"></div><script type="module" src="/__provider_fixture__.tsx"></script></body></html>');
    response.writeHead(200, { 'Content-Type': 'text/html' }); response.end(html);
  } else vite.middlewares(request, response);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let failReset = true, workingDeletes = 0;
let current = { id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',created:'2026-10-01T00:00:00Z',status:'complete',mode:'redact',sensitivity:50,counts:{} };
await page.route('**/api/service/**', async route => {
  const request = route.request(), path = new URL(request.url()).pathname;
  if (path.endsWith('/capabilities')) return route.fulfill({ json: { secure_history: { available: true } } });
  if (path.endsWith('/health')) return route.fulfill({ json: { model_installed: true, model_loaded: false, device:'cpu' } });
  if (path === '/api/service/guest/documents' && request.method() === 'POST') {
    current = {id:'dddddddd-dddd-4ddd-8ddd-dddddddddddd',filename:'Auto-save.pdf',created:new Date().toISOString(),status:'complete',mode:'redact',sensitivity:50,source_type:'pdf',counts:{},layout_preserved:true};
    return route.fulfill({status:202,json:{id:current.id}});
  }
  if (path === '/api/service/guest/documents/dddddddd-dddd-4ddd-8ddd-dddddddddddd' && request.method() === 'DELETE') {
    workingDeletes++; current = null; return route.fulfill({json:{deleted:true}});
  }
  assert.equal(path, '/api/service/guest/current');
  if (request.method() === 'DELETE') {
    assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf');
    if (failReset) return route.fulfill({ status: 503, json: { detail: 'Fixture reset failure' } });
    current = null;
    return route.fulfill({ json: { deleted: true } });
  }
  return route.fulfill({ json: { document: current, csrf_token:'fixture-csrf',expires_at:null } });
});
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`, { timeout: 60000 });
  await page.getByRole('button', { name: 'Retry reset', exact: true }).waitFor();
  assert.equal(await page.locator('.filename-text').filter({ hasText: /^Document cccccccc$/ }).count(), 0);
  await page.reload({timeout:60000});
  await page.getByRole('button', { name: 'Retry reset', exact: true }).waitFor();
  assert.equal(await page.locator('.filename-text').filter({ hasText: /^Document cccccccc$/ }).count(), 0);
  failReset = false;
  await page.getByRole('button', { name: 'Retry reset', exact: true }).click();
  await page.getByText('No files yet.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByText('Sign in', { exact: true }).click();
  assert.equal(await page.evaluate(() => window.fixture.signIns()), 1);
  await page.evaluate(() => window.fixture.identity({ status:'authenticated', ownerKey:'owner-a' }));
  await page.getByRole('status', {name:'Decrypting filename',exact:true}).waitFor();
  assert.ok((await page.locator('.filename-skeleton').boundingBox()).width > 40);
  await page.screenshot({path:'/tmp/redacted-filename-skeleton.png'});
  await page.evaluate(() => window.fixture.filename());
  await page.locator('.filename-text').filter({ hasText: /^Document aaaaaaaa$/ }).waitFor();
  assert.equal(await page.evaluate(() => window.fixture.reviews()), 0);
  await page.getByRole('button', { name: 'Review detections', exact: true }).click();
  assert.equal(await page.evaluate(() => window.fixture.reveals()), 0);
  await page.getByRole('button', { name: 'Reveal original values', exact: true }).click();
  assert.equal(await page.evaluate(() => window.fixture.reveals()), 1);
  assert.equal(await page.locator('.concealed-value[aria-busy=true] .reveal-wait').count(),4);
  await page.screenshot({path:'/tmp/redacted-reveal-skeleton.png'});
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable:true, value:'hidden' });
    document.dispatchEvent(new Event('visibilitychange')); window.fixture.reveal();
  });
  await page.waitForTimeout(100);
  assert.equal(await page.getByText('TRANSIENT FIXTURE ORIGINAL', { exact: true }).count(), 0);
  assert.equal(await page.getByText('TRANSIENT FIXTURE DATE', { exact: true }).count(), 0);
  await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable:true, value:'visible' }));
  await page.getByRole('button', { name: 'Reveal original values', exact: true }).click();
  await page.evaluate(() => window.fixture.reveal());
  await page.getByText('TRANSIENT FIXTURE ORIGINAL', { exact: true }).waitFor();
  await page.getByText('TRANSIENT FIXTURE DATE', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.fixture.reveals()), 2);
  await page.getByRole('button', { name: 'Hide original values', exact: true }).click();
  assert.deepEqual(await page.locator('.replacement-value').allTextContents(), ['******','**/**/**']);
  assert.equal(await page.getByText('TRANSIENT FIXTURE ORIGINAL', { exact: true }).count(), 0);
  assert.equal(await page.getByText('TRANSIENT FIXTURE DATE', { exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Reveal original values', exact: true }).click();
  await page.evaluate(() => { window.fixture.identity({ status:'authenticated', ownerKey:'owner-b' }); window.fixture.reveal(); });
  await page.locator('.filename-text').filter({ hasText: /^Document bbbbbbbb$/ }).waitFor();
  assert.equal(await page.locator('.filename-text').filter({ hasText: /^Document aaaaaaaa$/ }).count(), 0);
  assert.equal(await page.getByText('TRANSIENT FIXTURE ORIGINAL', { exact: true }).count(), 0);
  assert.equal(await page.getByText('TRANSIENT FIXTURE DATE', { exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  assert.equal(await page.getByText('Secure history settings', {exact:true}).count(),0);
  await page.getByText('Sign out', { exact: true }).click();
  assert.equal(await page.evaluate(() => window.fixture.signOuts()), 1);
  await page.locator('.filename-text').filter({ hasText: /^Document bbbbbbbb$/ }).waitFor({ state:'detached' });
  const resetFinished = page.waitForResponse(r => r.url().endsWith('/guest/current') && r.request().method() === 'GET');
  await page.evaluate(() => window.fixture.identity({status:'authenticated',ownerKey:'owner-a'}));
  await resetFinished;
  await page.locator('input[type=file]').setInputFiles({name:'Auto-save.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-autosave-fixture')});
  await page.getByRole('button',{name:'REDACT',exact:true}).click();
  await page.getByRole('button',{name:'Retry saving encrypted history'}).waitFor();
  assert.equal(workingDeletes, 0);
  assert.equal(await page.evaluate(() => window.fixture.saves()), 1);
  await page.evaluate(() => window.fixture.allowSave());
  await page.getByRole('button',{name:'Retry saving encrypted history'}).click();
  await page.locator('.document-row').filter({hasText:'Auto-save.pdf'}).getByRole('button',{name:'Original',exact:true}).waitFor();
  assert.equal(workingDeletes, 1);
  assert.equal(await page.getByRole('heading',{name:'Files',exact:true}).count(),1);
  assert.equal(await page.getByRole('button',{name:'Reprocess',exact:true}).count(),0);
  assert.match(await page.locator('.document-row').first().innerText(),/Auto-save.pdf/);
  assert.match(await page.locator('.document-row').last().locator('.counts').innerText(),/1 names/);
  assert.equal(await page.evaluate(() => window.fixture.saves()), 2);
  const rows=page.locator('article.document-row');
  assert.equal(await rows.count(),2);
  await rows.first().getByRole('button',{name:'Review detections',exact:true}).click();
  await page.getByRole('region',{name:'Detection review',exact:true}).waitFor();
  assert.equal(await rows.first().evaluate(row=>row.nextElementSibling?.classList.contains('review-panel')),true);
  assert.equal(await rows.last().evaluate(row=>row.previousElementSibling?.classList.contains('review-panel')),true);
  await rows.last().getByRole('button',{name:'Review detections',exact:true}).click();
  await page.getByRole('region',{name:'Detection review',exact:true}).waitFor();
  assert.equal(await page.locator('.review-panel').count(),1);
  assert.equal(await rows.first().evaluate(row=>row.nextElementSibling?.classList.contains('document-row')),true);
  assert.equal(await rows.last().evaluate(row=>row.nextElementSibling?.classList.contains('review-panel')),true);

  assert.deepEqual(errors, []);
  console.log('Provider fixture passed: account sign-in/sign-out actions, failed reset fenced across reload, safe retry, lazy review, one-call bulk reveal, hide/tab cancellation identity clearing, automatic save retry and working-copy cleanup only after successful save. No real auth or encryption is simulated.');
} finally {
  await browser.close(); await vite.close(); await new Promise(resolve => server.close(resolve));
}
