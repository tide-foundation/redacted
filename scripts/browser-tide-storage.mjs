// Isolated adapter test: real WebCrypto fixture encryption, NOT Tide network
// verification. Proves the application never uploads plaintext artifacts and
// rejects swaps/late decryptions. Python tests verify real JWT/DPoP signatures.
import { chromium } from '@playwright/test';
import { createServer as createViteServer } from 'vite';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const entry = resolve('__tide_storage_fixture__.ts');
const sdk = '\0tide-test-sdk';
const mock = `
export class TideCloak {
  authenticated = true; token = 'test-only';
  constructor() { window.tcFixture = this; this.key = crypto.subtle.generateKey({name:'AES-GCM',length:256}, false, ['encrypt','decrypt']); }
  async init() { this.authenticated = true; this.token = 'test-only'; return true; }
  async updateToken() {}
  async secureFetch(url, init) { return fetch(url, init); }
  clearToken() { this.authenticated = false; this.onAuthLogout?.(); }
  async cryptoOperation(run) {
    if (this.cryptoBusy) throw new Error('Concurrent Tide enclave calls share a response channel');
    this.cryptoBusy=true;
    try { await new Promise(resolve=>setTimeout(resolve,5)); return await run(); }
    finally { this.cryptoBusy=false; }
  }
  async encrypt(items) { return this.cryptoOperation(async () => {
    if (window.failEncrypt) throw new Error('fixture encryption failure');
    return Promise.all(items.map(async ({data}) => {
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const encrypted = new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv},await this.key,data));
      const result = new Uint8Array(12+encrypted.length); result.set(iv); result.set(encrypted,12); return result;
    }));
  }); }
  async decrypt(items) { return this.cryptoOperation(async () => {
    window.decryptCalls = (window.decryptCalls || 0) + items.length;
    return Promise.all(items.map(async ({encrypted}) => {
      const data = new Uint8Array(await crypto.subtle.decrypt({name:'AES-GCM',iv:encrypted.slice(0,12)},await this.key,encrypted.slice(12)));
      window.lastPlain = data;
      if (window.holdDecrypt) await new Promise(resolve => { window.finishDecrypt = resolve; });
      return data;
    }));
  }); }
}
`;
const fixture = `
import { createTideHistoryProvider } from '/src/tideHistory.ts';
window.provider = createTideHistoryProvider({app_origin:location.origin,issuer:'fixture',client_id:'redacted',adapter:{}});
window.ready = window.provider.initialise();
window.input = () => ({
 source:new Blob(['PRIVATE SOURCE: Alice 0422000000']), filename:'Private Alice health.pdf',
 manifest:new Blob([JSON.stringify({filename:'Private Alice health.pdf',mode:'redact',
 detections:[{category:'private_person',occurrence:1,replacement:'******',original:'Alice'}],
 scan_report:{sensitivity:50,counts:{private_person:1},total_detections:1,source_type:'pdf',layout_preserved:true,ocr_performed:false,warnings:[],limitations:[]}})]),
 outputs:{pdf:new Blob(['PRIVATE PDF OUTPUT']),docx:new Blob(['PRIVATE DOCX OUTPUT']),txt:new Blob(['PRIVATE TXT OUTPUT'])},
 metadata:{source_type:'pdf',mode:'redact',sensitivity:50,counts:{private_person:1},layout_preserved:true}
});
`;
const vite = await createViteServer({ cacheDir: 'node_modules/.vite-browser-tide-storage', configFile: false, appType: 'custom', optimizeDeps: { exclude: ['@tidecloak/js'] },
  plugins: [{ name: 'isolated-tide-sdk', enforce: 'pre',
    resolveId: id => id === '@tidecloak/js' ? sdk : id === '/__tide_storage_fixture__.ts' ? entry : undefined,
    load: id => id === sdk ? mock : id === entry ? fixture : undefined }],
  server: { middlewareMode: true, hmr: false },
});
const server = createServer(async (req, res) => {
  if (req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(await vite.transformIndexHtml('/', '<html><body><script type="module" src="/__tide_storage_fixture__.ts"></script></body></html>'));
  } else vite.middlewares(req, res);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const artifacts = new Map(), metadata = [], deleted = [];
const id = '11111111-1111-4111-8111-111111111111';
let committed = 0, swapping = false, record = null, upgrades = 0, artifactReads = [];
let correctionBytes=null, canWrite=true;
let guestRevision=0, changeGuestDuringCollection=false;
await page.route('**/api/service/**', async route => {
  const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
  if(path.endsWith('/protected-manifest'))return route.fulfill({json:{correction:{revision:guestRevision}}});
  if(path.includes('/guest/documents/')&&path.includes('/download/')){
    if(changeGuestDuringCollection&&path.endsWith('/txt'))guestRevision++;
    return route.fulfill({body:'fixture output'});
  }
  if (path.endsWith('/identity')) return route.fulfill({ json: { owner_id: 'owner-fixture',can_write:canWrite } });
  if(path.endsWith('/correction')) {
    if(method==='GET')return route.fulfill({contentType:'application/octet-stream',body:correctionBytes});
    const query=new URL(req.url()).searchParams;
    if(Number(query.get('revision'))!==(record.review_revision||0))return route.fulfill({status:409,json:{detail:'Conflict'}});
    correctionBytes=req.postDataBuffer();record.review_revision=(record.review_revision||0)+1;record.detail_count=Number(query.get('detail_count'));
    return route.fulfill({json:record});
  }
  if (path.endsWith('/history') && method === 'POST') {
    const body = req.postDataJSON(); metadata.push(body);
    const draft = { ...body, id:metadata.length === 1 ? id : '22222222-2222-4222-8222-222222222222', created: new Date().toISOString(), status:'draft' };
    if (!record) record = draft;
    return route.fulfill({ status: 201, json:draft });
  }
  if (path.endsWith('/history') && method === 'GET') return route.fulfill({json:record ? [record] : []});
  if (path.endsWith('/history/' + id) && method === 'GET') return route.fulfill({json:record});
  if (path.endsWith('/history/' + id + '/filename') && method === 'PUT') {
    upgrades++; artifacts.set('filename',req.postDataBuffer()); record.protection_version = 2;
    return route.fulfill({json:record});
  }
  if (path.endsWith('/replacements') && method === 'PUT') { record.replacements = req.postDataJSON().replacements; return route.fulfill({json:record}); }
  if (path.includes('/artifacts/')) {
    const kind = path.split('/').at(-1);
    if (method === 'PUT') { artifacts.set(kind, req.postDataBuffer()); return route.fulfill({ json: { stored: true } }); }
    artifactReads.push(kind);
    return route.fulfill({ contentType: 'application/octet-stream', body: artifacts.get(swapping ? 'output_txt' : kind) });
  }
  if (path.endsWith('/commit')) { committed++; record.status='complete'; return route.fulfill({ json:record }); }
  if (method === 'DELETE') { deleted.push(path); if (path.endsWith(id)) record = null; return route.fulfill({ json:{deleted:true} }); }
  throw new Error('Unexpected fixture request: ' + path);
});
const countDecrypts = () => page.evaluate(() => window.decryptCalls || 0);
const coldSession = () => page.evaluate(async () => { window.tcFixture.clearToken(); await window.provider.initialise(); });
const call = (method, ...args) => page.evaluate(({method,args}) => window.provider[method](...args,new AbortController().signal), {method,args});
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`, { timeout: 60000 });
  await page.evaluate(() => window.ready);
  assert.equal(await page.evaluate(() => window.provider.getSnapshot().status), 'authenticated');
  await page.evaluate(() => window.provider.save(window.input(), new AbortController().signal));
  assert.equal(committed, 1); assert.equal(artifacts.size, 6);
  for (const bytes of artifacts.values()) {
    assert.ok(bytes.subarray(0,16).toString().startsWith('REDACTED-TIDE-1\n'));
    for (const text of ['Alice', 'PRIVATE', '0422000000', 'health.pdf']) assert.equal(bytes.includes(Buffer.from(text)), false);
  }
  assert.equal(JSON.stringify(metadata).includes('Alice'), false);
  assert.equal(JSON.stringify(metadata).includes('filename'), false);
  assert.equal(record.protection_version, 2);
  assert.deepEqual(record.replacements,[{category:'private_person',occurrence:1,replacement:'******'}]);
  await coldSession();
  await call('list');
  const review = await call('review',id);
  assert.equal(JSON.stringify(review).includes('Alice'), false);
  assert.equal(review.detections[0].replacement, '******');
  assert.equal(await countDecrypts(), 0);
  assert.deepEqual(artifactReads, []);
  assert.equal(await call('filename',id), 'Private Alice health.pdf');
  assert.equal(await call('filename',id), 'Private Alice health.pdf');
  await call('review',id);
  assert.equal(await countDecrypts(), 1);
  assert.deepEqual(artifactReads,['filename']);
  const revealed = await call('reveal',id);
  assert.equal(revealed[0].original, 'Alice');
  assert.equal(revealed[0].replacement, '******');
  await call('reveal',id);
  assert.equal(await countDecrypts(), 2);
  assert.equal(await page.evaluate(async id => (await window.provider.recoverOriginal(id,new AbortController().signal)).text(), id), 'PRIVATE SOURCE: Alice 0422000000');
  await call('recoverOriginal',id);
  await call('download',id,'txt'); await call('download',id,'txt');
  assert.equal(await countDecrypts(), 4);

  // Concurrent requests share one decryption. Cancelling one caller must not
  // invalidate another caller or cause a second network decryption.
  await page.evaluate(id => {
    window.holdDecrypt = true;
    window.cancelFirst = new AbortController();
    window.first = window.provider.download(id,'pdf',window.cancelFirst.signal).then(()=>'unexpected success',e=>e.name);
    window.second = window.provider.download(id,'pdf',new AbortController().signal).then(blob=>blob.text());
  },id);
  await page.waitForFunction(() => window.finishDecrypt);
  assert.equal(await countDecrypts(),5);
  await page.evaluate(() => {window.cancelFirst.abort(); window.holdDecrypt=false; window.finishDecrypt(); window.finishDecrypt=null;});
  assert.equal(await page.evaluate(() => window.first),'AbortError');
  assert.equal(await page.evaluate(() => window.second),'PRIVATE PDF OUTPUT');
  await call('download',id,'pdf'); assert.equal(await countDecrypts(),5);

  await coldSession();
  const beforeConcurrent=await countDecrypts();
  const concurrent=await page.evaluate(async id=>{
    const signal=new AbortController().signal;
    const [name,original,pdf,values]=await Promise.all([
      window.provider.filename(id,signal),window.provider.recoverOriginal(id,signal),
      window.provider.download(id,'pdf',signal),window.provider.reveal(id,signal)]);
    return {name,original:await original.text(),pdf:await pdf.text(),value:values[0].original};
  },id);
  assert.deepEqual(concurrent,{name:'Private Alice health.pdf',original:'PRIVATE SOURCE: Alice 0422000000',pdf:'PRIVATE PDF OUTPUT',value:'Alice'});
  assert.equal(await countDecrypts(),beforeConcurrent+4);
  await coldSession();
  swapping = true;
  assert.match(await page.evaluate(async id => { try { await window.provider.recoverOriginal(id,new AbortController().signal); return 'unexpected success'; } catch(e) {return e.message;} }, id), /another document/);
  swapping = false;
  await page.evaluate(() => { window.failEncrypt = true; });
  await page.evaluate(async () => { try {await window.provider.save(window.input(),new AbortController().signal);} catch {} });
  assert.equal(committed, 1); assert.equal(deleted.length, 1);
  await page.evaluate(id => {
    window.failEncrypt = false; window.holdDecrypt = true;
    window.lateResult = window.provider.reveal(id,new AbortController().signal).then(()=>'unexpected success', e=>e.name);
    window.queuedResult = window.provider.download(id,'docx',new AbortController().signal).then(()=>'unexpected success',e=>e.name);
  }, id);
  await page.waitForFunction(() => window.finishDecrypt);
  await page.evaluate(() => { window.tcFixture.clearToken(); window.holdDecrypt=false; window.finishDecrypt(); window.finishDecrypt=null; });
  assert.equal(await page.evaluate(() => window.lateResult), 'AbortError');
  assert.equal(await page.evaluate(() => window.queuedResult), 'AbortError');
  assert.equal(await page.evaluate(() => window.lastPlain.every(v=>v===0)), true);

  // Old documents keep their original ciphertext. Only an explicit Reveal can
  // decrypt their combined manifest and split out a new protected filename.
  await coldSession();
  const oldManifest = await page.evaluate(async id => {
    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify({owner:'owner-fixture',id,kind:'manifest'})+'\n'+await window.input().manifest.text());
    const [encrypted] = await window.tcFixture.encrypt([{data}]);
    const prefix = encoder.encode('REDACTED-TIDE-1\n');
    const result = new Uint8Array(prefix.length+encrypted.length); result.set(prefix); result.set(encrypted,prefix.length);
    return [...result];
  },id);
  artifacts.set('manifest',Buffer.from(oldManifest)); artifacts.delete('filename'); record.protection_version=1; delete record.replacements;
  const originals = new Map(artifacts);
  const beforeLegacy = await countDecrypts();
  await call('list'); assert.equal(await call('filename',id),null); assert.equal((await call('review',id)).detections[0].replacement,'******');
  assert.equal(await countDecrypts(),beforeLegacy);
  assert.equal((await call('reveal',id))[0].original,'Alice');
  assert.equal(await countDecrypts(),beforeLegacy+1);
  assert.equal(upgrades,1); assert.equal(record.protection_version,2);
  for (const [kind,bytes] of originals) assert.deepEqual(artifacts.get(kind),bytes);
  await call('reveal',id); assert.equal(upgrades,1); assert.equal(await countDecrypts(),beforeLegacy+1);
  await coldSession(); await call('list');
  assert.equal(await call('filename',id),'Private Alice health.pdf');
  await call('review',id); assert.equal(await countDecrypts(),beforeLegacy+2);
  // Synthetic legacy values cannot be reconstructed from category counts.
  // An explicit bulk Reveal publishes just the generated values for later use.
  await coldSession();
  const syntheticManifest = await page.evaluate(async id => {
    const encoder = new TextEncoder(), manifest = JSON.parse(await window.input().manifest.text());
    manifest.mode='synthetic'; delete manifest.filename;
    manifest.detections[0].replacement='Alex Example 1';
    const data=encoder.encode(JSON.stringify({owner:'owner-fixture',id,kind:'manifest'})+'\n'+JSON.stringify(manifest));
    const [encrypted]=await window.tcFixture.encrypt([{data}]);
    const prefix=encoder.encode('REDACTED-TIDE-1\n');
    const result=new Uint8Array(prefix.length+encrypted.length); result.set(prefix); result.set(encrypted,prefix.length); return [...result];
  },id);
  artifacts.set('manifest',Buffer.from(syntheticManifest)); record.mode='synthetic'; delete record.replacements;
  const beforeSynthetic = await countDecrypts();
  await call('list'); assert.equal((await call('review',id)).detections[0].replacement,undefined);
  assert.equal(await countDecrypts(),beforeSynthetic);
  assert.equal((await call('reveal',id))[0].replacement,'Alex Example 1');
  assert.equal(await countDecrypts(),beforeSynthetic+1);
  await coldSession(); await call('list');
  assert.equal((await call('review',id)).detections[0].replacement,'Alex Example 1');
  assert.equal(await countDecrypts(),beforeSynthetic+1);
  assert.equal(JSON.stringify(record).includes('Alice'),false);
  await page.evaluate(id=>window.provider.collectGuest(id,new Blob(['fixture source']),new AbortController().signal),id);
  changeGuestDuringCollection=true;
  assert.match(await page.evaluate(async id=>{try{await window.provider.collectGuest(id,new Blob(['fixture source']),new AbortController().signal);return 'unexpected';}catch(e){return e.message;}},id),/changed while saving/);
  changeGuestDuringCollection=false;
  // Corrections and all exports are encrypted as one atomic revision using the
  // same owner/document context and history tag. Original artifacts stay intact.
  const corrected = await page.evaluate(async id=>{
    const {rebuild}=await import('/src/corrections.ts');
    const value=rebuild('Alice and Bob.',[{start:0,end:5,category:'private_person'}],'placeholder');
    return window.provider.saveCorrection(id,value,new AbortController().signal);
  },id);
  assert.equal(corrected.revision,1);assert.equal(record.detail_count,1);
  for(const text of ['Alice','Bob','[NAME','details','redacted'])assert.equal(correctionBytes.includes(Buffer.from(text)),false);
  const sealed=Buffer.from(correctionBytes);
  await coldSession();
  assert.equal((await call('correction',id)).redacted,'[NAME 1] and Bob.');
  assert.equal((await call('reveal',id))[0].original,'Alice');
  const files=await page.evaluate(async id=>{
    const files={};for(const f of ['txt','pdf','docx'])files[f]=[...new Uint8Array(await (await window.provider.download(id,f,new AbortController().signal)).arrayBuffer())];return files;
  },id);
  for(const [format,bytes] of Object.entries(files))await writeFile(`/tmp/redacted-corrected.${format}`,Buffer.from(bytes));
  assert.equal(Buffer.from(files.txt).toString(),'[NAME 1] and Bob.');
  execFileSync('.venv/bin/python',['-c',`from docx import Document\nimport pymupdf\nassert '[NAME 1] and Bob.' in '\\n'.join(p.text for p in Document('/tmp/redacted-corrected.docx').paragraphs)\nwith pymupdf.open('/tmp/redacted-corrected.pdf') as pdf:\n text=''.join(p.get_text() for p in pdf)\n assert '[NAME 1] and Bob.' in text and 'Alice' not in text`]);
  await page.evaluate(()=>window.failEncrypt=true);
  assert.match(await page.evaluate(async ({id,value})=>{try{await window.provider.saveCorrection(id,value,new AbortController().signal);return 'unexpected';}catch(e){return e.message;}},{id,value:corrected}),/fixture encryption failure/);
  assert.deepEqual(correctionBytes,sealed);await page.evaluate(()=>window.failEncrypt=false);
  canWrite=false;await coldSession();
  assert.equal((await call('correction',id)).detailCount,1);
  assert.match(await page.evaluate(async ({id,value})=>{try{await window.provider.saveCorrection(id,value,new AbortController().signal);return 'unexpected';}catch(e){return e.message;}},{id,value:corrected}),/read-only/);
  canWrite=true;await coldSession();
  // Delete invalidates decrypted filename and manifest caches as well.
  await call('remove',id);
  assert.deepEqual(await call('list'),[]);
  console.log('Tide adapter checks passed: six encrypted artifacts, metadata-only list/review, on-demand decryption, cache reuse/coalescing, swap rejection, save cleanup, logout fencing and legacy upgrade. This fixture does not verify the live Tide network.');
} finally { await browser.close(); await vite.close(); await new Promise(resolve => server.close(resolve)); }
