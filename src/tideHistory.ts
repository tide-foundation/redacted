import { TideCloak } from '@tidecloak/js';
import type { SecureHistoryProvider, IdentityState, TransientHistoryInput } from './history';
import { clearSetupSignIn, markSetupSignInAttempt } from './setupNavigation';
import { fixedReplacement } from './types';
import type { DetectionReview, DocumentResult, OutputFormat, RevealedDetection } from './types';
import { correctionReview, fromManifest, parseCorrection, type Correction } from './corrections';

export type TideConfig = { app_origin: string; issuer: string; client_id: string; adapter: Record<string, unknown> };
type Manifest = Omit<DetectionReview, 'detections'> & { filename?: string; mode: DocumentResult['mode']; detections: (DetectionReview['detections'][number] & { original: string })[] };
type RecordMetadata = DocumentResult & { warning_codes: string[] };
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const formats: OutputFormat[] = ['pdf', 'docx', 'txt'];
const prefix = encoder.encode('REDACTED-TIDE-1\n');

export function createTideHistoryProvider(config: TideConfig): SecureHistoryProvider & { initialise(options?: { completeSetup?: boolean }): Promise<void> } {
  const a = config.adapter;
  if (config.app_origin !== window.location.origin) throw new Error('Open Redacted at its configured origin.');
  const tc = new TideCloak({ url: a['auth-server-url'], realm: a.realm, clientId: a.resource,
    vendorId: a.vendorId, homeOrkUrl: a.homeOrkUrl, backgroundUrl: a.backgroundUrl, logoUrl: a.logoUrl,
    clientOriginAuth: a['client-origin-auth-' + window.location.origin], setupRequestEnclave: true });
  let state: IdentityState = { status: 'loading' };
  let epoch = 0;
  // The installed RequestEnclave matches replies by operation type, not request
  // ID. Overlapping calls can consume each other's response. Keep all Tide
  // crypto operations sequential, while HTTP artifact fetches stay parallel.
  let cryptoTail: Promise<void> = Promise.resolve();
  function inEnclave<T>(signal: AbortSignal, run: () => Promise<T>): Promise<T> {
    const version = epoch;
    const operation = cryptoTail.then(async () => {
      check(signal, version);
      return run();
    });
    cryptoTail = operation.then(() => {}, () => {});
    return operation;
  }
  const listeners = new Set<() => void>();
  // Plaintext exists only in this provider instance, never browser storage.
  // Keep memory bounded; an evicted payload can be decrypted again on demand.
  const cacheLimit = 256 * 1024 * 1024;
  let cacheBytes = 0;
  const cache = new Map<string, Blob>();
  const pending = new Map<string, { controller: AbortController; promise: Promise<Blob> }>();
  const records = new Map<string, RecordMetadata>();
  const knownNames = new Map<string, string>();
  function clearCache(id?: string) {
    for (const [key, value] of cache) if (!id || key.startsWith(id + ':')) { cacheBytes -= value.size; cache.delete(key); }
    for (const [key, value] of pending) if (!id || key.startsWith(id + ':')) { value.controller.abort(); pending.delete(key); }
    if (id) { records.delete(id); knownNames.delete(id); } else { records.clear(); knownNames.clear(); }
  }
  function remember(key: string, blob: Blob) {
    const old = cache.get(key); if (old) cacheBytes -= old.size;
    cache.delete(key); cache.set(key, blob); cacheBytes += blob.size;
    while (cacheBytes > cacheLimit) { const first = cache.keys().next().value!; cacheBytes -= cache.get(first)!.size; cache.delete(first); }
  }
  const emit = (next: IdentityState) => { clearCache(); state = next; epoch++; listeners.forEach(fn => fn()); };
  const owner = () => {
    if (state.status !== 'authenticated') throw new Error('Sign in to access your history.');
    return state.ownerKey;
  };
  const check = (signal: AbortSignal, version: number) => {
    signal.throwIfAborted();
    if (version !== epoch) throw new DOMException('Identity changed.', 'AbortError');
  };
  async function request(path: string, signal: AbortSignal, init: RequestInit = {}) {
    signal.throwIfAborted();
    const version = epoch;
    await tc.updateToken(30);
    check(signal, version);
    if (!tc.token || !tc.authenticated) throw new Error('Sign in again to access history.');
    const response = await tc.secureFetch(new URL('/api/service/' + path, window.location.origin).href,
      { ...init, signal, cache: 'no-store', credentials: 'same-origin' });
    check(signal, version);
    if (!response.ok) {
      if (response.status === 403) clearCache();
      if (response.status === 401) { tc.clearToken(); emit({ status: 'signed-out' }); }
      if (response.status === 409) throw new Error('This document changed in another tab. Close and reopen the review before saving.');
      throw new Error(response.status === 403 ? 'Personal-history permissions are not active. Complete setup and sign in again.' : 'Secure history is unavailable. Please try again.');
    }
    return response;
  }
  async function identify() {
    const result = await request('identity', new AbortController().signal);
    const identity: { owner_id: string; can_write?: boolean } = await result.json();
    emit({ status: 'authenticated', ownerKey: identity.owner_id, canWrite: identity.can_write !== false });
  }
  tc.onAuthLogout = () => emit({ status: 'signed-out' });
  tc.onAuthRefreshError = () => { tc.clearToken(); emit({ status: 'signed-out' }); };
  tc.onTokenExpired = () => { void tc.updateToken(30).catch(() => { tc.clearToken(); emit({ status: 'signed-out' }); }); };
  // Propagate logout to other tabs without sharing tokens or private data.
  const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('redacted-identity');
  if (channel) channel.onmessage = e => { if (e.data === 'logout') { tc.clearToken(); emit({ status: 'signed-out' }); } };

  async function protect(blob: Blob, id: string, kind: string, signal: AbortSignal) {
    const version = epoch;
    const context = encoder.encode(JSON.stringify({ owner: owner(), id, kind }) + '\n');
    const bytes = new Uint8Array(context.length + blob.size);
    bytes.set(context); bytes.set(new Uint8Array(await blob.arrayBuffer()), context.length);
    check(signal, version);
    let encrypted: string | Uint8Array;
    try { [encrypted] = await inEnclave(signal, () => tc.encrypt([{ data: bytes, tags: ['history'] }])); }
    finally { bytes.fill(0); }
    check(signal, version);
    if (!(encrypted instanceof Uint8Array) || !encrypted.length) throw new Error('Tide did not return a protected artifact.');
    return new Blob([prefix, new Uint8Array(encrypted)], { type: 'application/octet-stream' });
  }
  async function unprotect(blob: Blob, id: string, kind: string, signal: AbortSignal) {
    const version = epoch;
    const expectedOwner = owner();
    const bytes = new Uint8Array(await blob.arrayBuffer());
    if (!prefix.every((v, i) => bytes[i] === v)) throw new Error('Unsupported protected artifact.');
    const [plain] = await inEnclave(signal, () => tc.decrypt([{ encrypted: bytes.slice(prefix.length), tags: ['history'] }]));
    if (!(plain instanceof Uint8Array)) throw new Error('Unsupported decrypted artifact.');
    try {
      check(signal, version);
      const newline = plain.indexOf(10);
      if (newline < 0 || newline > 2048) throw new Error('Invalid protected context.');
      const context = JSON.parse(decoder.decode(plain.subarray(0, newline)));
      if (context.id !== id || context.kind !== kind || context.owner !== expectedOwner) throw new Error('Protected artifact belongs to another document.');
      return new Blob([new Uint8Array(plain.subarray(newline + 1))]);
    } finally { plain.fill(0); }
  }
  async function artifact(id: string, kind: string, signal: AbortSignal) {
    owner(); signal.throwIfAborted(); const version = epoch;
    try { await tc.updateToken(30); } catch (error) { tc.clearToken(); emit({ status: 'signed-out' }); throw error; }
    check(signal, version);
    if (!tc.authenticated || !tc.token) throw new Error('Sign in again to access history.');
    const key = id + ':' + kind;
    const hit = cache.get(key);
    if (hit) { remember(key, hit); return hit; }
    let flight = pending.get(key);
    if (!flight) {
      const controller = new AbortController();
      const task = { controller, promise: Promise.resolve(new Blob()) };
      task.promise = (async () => {
        const response = await request(`history/${encodeURIComponent(id)}/artifacts/${kind}`, controller.signal);
        const plain = await unprotect(await response.blob(), id, kind, controller.signal);
        check(controller.signal, version); remember(key, plain); return plain;
      })().finally(() => { if (pending.get(key) === task) pending.delete(key); });
      pending.set(key, task); flight = task;
    }
    const result = await flight.promise; check(signal, version); return result;
  }
  async function metadata(id: string, signal: AbortSignal) {
    owner(); signal.throwIfAborted(); const version = epoch;
    let record = records.get(id);
    if (!record) { record = await (await request(`history/${encodeURIComponent(id)}`, signal)).json() as RecordMetadata; check(signal, version); records.set(id, record); }
    return record;
  }
  async function manifest(id: string, signal: AbortSignal): Promise<Manifest> {
    const m = JSON.parse(await (await artifact(id, 'manifest', signal)).text());
    if (!m || !Array.isArray(m.detections) || !m.scan_report || typeof m.mode !== 'string') throw new Error('Invalid detection manifest.');
    return m;
  }
  async function freshMetadata(id: string, signal: AbortSignal) {
    const version=epoch;
    const record: RecordMetadata = await (await request(`history/${encodeURIComponent(id)}`,signal)).json();
    check(signal,version);
    records.set(id,record); return record;
  }
  async function corrected(id: string, signal: AbortSignal, record: RecordMetadata) {
    const version=epoch;
    const response=await request(`history/${encodeURIComponent(id)}/correction`,signal);
    const payload=JSON.parse(await (await unprotect(await response.blob(),id,'correction',signal)).text());
    const value=parseCorrection(payload.review);
    if(value.revision!==record.review_revision || value.detailCount!==record.detail_count || !payload.outputs || !formats.every(f=>typeof payload.outputs[f]==='string')) throw new Error('Saved correction does not match this revision. Reopen the review.');
    const {fromBase64}=await import('./correctionExports');
    if(await fromBase64(payload.outputs.txt,'text/plain').text()!==value.redacted) throw new Error('Saved output does not match its review.');
    check(signal,version);
    return {value,outputs:payload.outputs as Record<OutputFormat,string>};
  }
  async function loadCorrection(id: string, signal: AbortSignal) {
    const record=await freshMetadata(id,signal);
    if(record.review_revision) return (await corrected(id,signal,record)).value;
    const m=await manifest(id,signal);
    return fromManifest(m, await (await artifact(id,'output_txt',signal)).text());
  }
  async function saveCorrection(id: string, input: Correction, signal: AbortSignal, onlyIfMissing=false) {
    if(state.status!=='authenticated' || state.canWrite===false) throw new Error('This account has read-only access.');
    const version=epoch, value=parseCorrection(input);
    const {exportCorrection,toBase64}=await import('./correctionExports');
    const blobs=await exportCorrection(value.redacted,signal);
    const outputs=Object.fromEntries(await Promise.all(formats.map(async f=>[f,await toBase64(blobs[f])])));
    const review={...value,revision:value.revision+1};
    const sealed=await protect(new Blob([JSON.stringify({review,outputs})]),id,'correction',signal);
    check(signal,version);
    const record: RecordMetadata=await (await request(`history/${encodeURIComponent(id)}/correction?detail_count=${value.detailCount}&revision=${value.revision}&only_if_missing=${onlyIfMissing}`,signal,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:sealed})).json();
    check(signal,version);clearCache(id);records.set(id,record);
    return onlyIfMissing ? (await corrected(id,signal,record)).value : review;
  }
  return {
    correction: loadCorrection,
    saveCorrection,
    getSnapshot: () => state,
    subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); },
    async initialise({ completeSetup = false }: { completeSetup?: boolean } = {}) {
      try {
        const authenticated = await tc.init({
          ...(completeSetup ? { redirectUri: config.app_origin + '/secure-history/setup' } : {
            onLoad: 'check-sso' as const,
            silentCheckSsoRedirectUri: config.app_origin + '/silent-check-sso.html', silentCheckSsoFallback: false,
          }),
          pkceMethod: 'S256', checkLoginIframe: false, setupRequestEnclave: true,
          useDPoP: { mode: 'strict', alg: 'ES256' } });
        if (authenticated) {
          await identify();
          if (completeSetup) clearSetupSignIn();
        } else if (completeSetup && markSetupSignInAttempt()) {
          // A new code+PKCE exchange in this tab binds its own DPoP key. Reuse
          // the IdP session if available; never copy tokens from the link popup.
          await tc.login({ prompt: 'none', redirectUri: config.app_origin + '/secure-history/setup' });
        } else emit({ status: 'signed-out' });
      } catch { tc.clearToken(); emit({ status: 'error' }); }
    },
    async signIn() {
      // No plaintext handoff via browser storage or URL. Full-page login starts
      // a fresh working session; make that consequence explicit before redirect.
      const response = await fetch('/api/service/guest/current', { cache: 'no-store' });
      if (response.ok && (await response.json()).document && !window.confirm('Signing in starts a fresh working session. Download your current result first, then upload again after signing in. Continue?')) return;
      clearSetupSignIn();
      await tc.login({ redirectUri: config.app_origin + '/' });
    },
    async signOut() {
      emit({ status: 'signed-out' }); channel?.postMessage('logout');
      try { await tc.logout({ redirectUri: config.app_origin + '/' }); } finally { tc.clearToken(); }
    },
    async save(input: TransientHistoryInput, signal) {
      owner();
      const version = epoch;
      const m: Manifest = JSON.parse(await input.manifest.text());
      delete m.filename;
      const metadata = { source_type: input.metadata.source_type, mode: input.metadata.mode,
        sensitivity: input.metadata.sensitivity, counts: m.scan_report.counts,
        layout_preserved: m.scan_report.layout_preserved ?? false,
        warning_codes: m.scan_report.warnings.flatMap(w => w === 'Images are preserved but are not scanned for sensitive data.' ? ['images_unscanned'] : w === 'Original layout unavailable; clean rewrite used.' ? ['layout_fallback'] : []),
        protection_version: 2,
        replacements: m.detections.map(({category, occurrence, replacement}) => ({category, occurrence, replacement})) };
      const draft: RecordMetadata = await (await request('history', signal, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(metadata),
      })).json();
      try {
        const inputs: [string, Blob][] = [['filename', new Blob([JSON.stringify({ filename: input.filename || null })])], ['source', input.source], ['manifest', new Blob([JSON.stringify(m)])],
          ...formats.map(format => ['output_' + format, input.outputs[format]] as [string, Blob])];
        for (const [kind, blob] of inputs) {
          const ciphertext = await protect(blob, draft.id, kind, signal);
          check(signal, version);
          await request(`history/${draft.id}/artifacts/${kind}`, signal, {
            method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: ciphertext,
          });
        }
        const completed = await (await request(`history/${draft.id}/commit`, signal, { method: 'POST' })).json();
        check(signal, version);
        records.set(completed.id, completed);
        if ((m as any).correction) {
          await saveCorrection(completed.id, {...parseCorrection((m as any).correction), revision:0}, signal, true);
          Object.assign(completed, await freshMetadata(completed.id,signal));
        }
        if (input.filename) knownNames.set(completed.id, input.filename);
        return { ...completed, filename: input.filename };
      } catch (error) {
        if (version === epoch) await request(`history/${draft.id}`, new AbortController().signal, { method: 'DELETE' }).catch(() => {});
        throw error;
      }
    },
    async list(signal) {
      owner(); const version = epoch;
      const items: RecordMetadata[] = await (await request('history', signal)).json();
      check(signal, version);
      const ids = new Set(items.map(record => record.id));
      for (const id of records.keys()) if (!ids.has(id)) clearCache(id);
      for (const record of items) records.set(record.id, record);
      return items.map(record => ({ ...record, filename: knownNames.get(record.id) }));
    },
    async filename(id, signal) {
      owner(); signal.throwIfAborted(); const version = epoch;
      if (knownNames.has(id)) return knownNames.get(id)!;
      const record = await metadata(id, signal);
      // Legacy manifests also contain originals: never decrypt them for a label.
      if (record.protection_version !== 2) return null;
      const value = JSON.parse(await (await artifact(id, 'filename', signal)).text());
      check(signal, version);
      if (typeof value.filename === 'string') { knownNames.set(id, value.filename); return value.filename; }
      return null;
    },
    async remove(id, signal) {
      owner(); await request(`history/${encodeURIComponent(id)}`, signal, { method: 'DELETE' }); clearCache(id);
    },
    async recoverOriginal(id, signal) { return artifact(id, 'source', signal); },
    async review(id, signal) {
      const record = await freshMetadata(id, signal);
      // Generated replacements are public metadata; originals stay encrypted.
      const detections = record.replacements ?? Object.entries(record.counts).flatMap(([category, count]) =>
        Array.from({ length: count }, (_, i) => ({ category, occurrence: i + 1, replacement: fixedReplacement(record.mode, category) })));
      const warnings = (record.warning_codes || []).map(code => ({
        images_unscanned: 'Images are preserved but are not scanned for sensitive data.',
        layout_fallback: 'Original layout unavailable; clean rewrite used.',
        ocr_unavailable: 'OCR is unavailable.',
      }[code] || 'Review the output before sharing.'));
      const review = { detections, scan_report: { sensitivity: record.sensitivity, counts: record.counts,
        total_detections: detections.length, source_type: record.source_type || '',
        layout_preserved: record.layout_preserved ?? false, ocr_performed: false, warnings,
        limitations: ['Only extractable text is scanned. Images are not analysed and no OCR is run.',
          'The model can miss sensitive information or flag ordinary text. Review the output before sharing.',
          'Document properties are removed in every mode. Embedded image metadata is not scanned.'] } };
      return record.review_revision ? correctionReview((await corrected(id,signal,record)).value, review.scan_report) : review;
    },
    async reveal(id, signal): Promise<RevealedDetection[]> {
      const version = epoch;
      const fresh=await freshMetadata(id,signal);
      if(fresh.review_revision) {
        const {value}=await corrected(id,signal,fresh);const counts:Record<string,number>={};
        return value.details.map(d=>({category:d.category,occurrence:counts[d.category]=(counts[d.category]||0)+1,original:value.text.slice(d.start,d.end),replacement:d.replacement}));
      }
      const m = await manifest(id, signal);
      const record = await metadata(id, signal);
      check(signal, version);
      if (typeof m.filename === 'string') {
        knownNames.set(id, m.filename);
        // Preserve legacy documents. Only this explicit reveal may split out
        // their filename, using new ciphertext and the same owner/document ID.
        if (record.protection_version !== 2) {
          try {
            const name = await protect(new Blob([JSON.stringify({filename: m.filename})]), id, 'filename', signal);
            const upgraded: RecordMetadata = await (await request(`history/${encodeURIComponent(id)}/filename`, signal,
              {method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:name})).json();
            check(signal, version); records.set(id, upgraded);
          } catch { check(signal, version); /* A failed upgrade must not hide a successful reveal. */ }
        }
      }
      if (record.mode === 'synthetic' && !record.replacements) {
        try {
          const replacements = m.detections.map(({category, occurrence, replacement}) => ({category, occurrence, replacement}));
          const updated: RecordMetadata = await (await request(`history/${encodeURIComponent(id)}/replacements`, signal,
            {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({replacements})})).json();
          check(signal, version); records.set(id, updated);
        } catch { check(signal, version); /* A legacy metadata upgrade can retry on the next Reveal. */ }
      }
      return m.detections.map(({ category, occurrence, original, replacement }) => ({ category, occurrence, original, replacement }));
    },
    async download(id, format, signal) {
      const record=await freshMetadata(id,signal);
      if(record.review_revision) {const payload=await corrected(id,signal,record);const {fromBase64}=await import('./correctionExports');return fromBase64(payload.outputs[format],{pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',txt:'text/plain;charset=utf-8'}[format]);}
      return artifact(id, 'output_' + format, signal);
    },
    async collectGuest(id, source, signal) {
      const m = await (await request(`guest/documents/${id}/protected-manifest`, signal)).blob();
      const collectedRevision = JSON.parse(await m.text()).correction?.revision || 0;
      const outputs = {} as Record<OutputFormat, Blob>;
      for (const format of formats) {
        const response = await fetch(`/api/service/guest/documents/${id}/download/${format}`, { signal, cache: 'no-store' });
        if (!response.ok) throw new Error('The working result expired. Upload again before saving.');
        outputs[format] = await response.blob();
      }
      const latest = await (await request(`guest/documents/${id}/protected-manifest`, signal)).json();
      if ((latest.correction?.revision || 0) !== collectedRevision) throw new Error('The working review changed while saving. Retry to save its latest revision.');
      return { source, manifest: m, outputs };
    },
    async testProtection(signal) {
      const text = 'Redacted setup check: ' + crypto.randomUUID();
      const encrypted = await protect(new Blob([text]), 'setup-check', 'test', signal);
      const decrypted = await unprotect(encrypted, 'setup-check', 'test', signal);
      if (await decrypted.text() !== text) throw new Error('The encryption check failed.');
    },
  };
}
