import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, Check, ChevronDown, SlidersHorizontal, CircleHelp, FileText, LoaderCircle, Trash2, X } from 'lucide-react';
import { api, isAborted } from './api';
import { startVisiblePolling } from './polling';
import type { DetectionReview, DocumentResult, GuestState, Mode, RevealedDetection } from './types';
import { categoryLabels, modeLabels } from './types';
import { HistoryPanel, unavailableHistoryProvider, useIdentity } from './history';
import type { SecureHistoryProvider } from './history';
import { ReviewPanel } from './ReviewPanel';
import { AccountMenu } from './AccountMenu';
import { SecureHistoryPage } from './SecureHistoryPage';
import { TideLinkComplete } from './TideLinkComplete';
import { TideSetupPage } from './TideSetupPage';
import { DisclaimerPage } from './DisclaimerPage';

type Health = { model_installed: boolean; model_loaded: boolean; device: string };
function HelpTip({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  return <span className="help" ref={root}>
    <button type="button" className="help-button" aria-label={`About ${label}`} aria-expanded={open} aria-controls={id} aria-describedby={open ? id : undefined} onClick={() => setOpen(!open)}><CircleHelp size={15}/></button>
    {open && <span className="help-tip" id={id} role="tooltip">{children}</span>}
  </span>;
}
export default function App({ historyProvider = unavailableHistoryProvider }: { historyProvider?: SecureHistoryProvider }) {
  const identity = useIdentity(historyProvider);
  const identityKey = identity.status === 'authenticated' ? `owner:${identity.ownerKey}` : identity.status;
  const [path, setPath] = useState(window.location.pathname);
  const [historyAvailable, setHistoryAvailable] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [savedRecord, setSavedRecord] = useState<DocumentResult | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const autoSaveAttempt = useRef<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<Mode>('redact');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sensitivity, setSensitivity] = useState(50);
  const [current, setCurrent] = useState<DocumentResult | null>(null);
  const [csrf, setCsrf] = useState('');
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState('');
  const [connection, setConnection] = useState('');
  const [busy, setBusy] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [resultError, setResultError] = useState('');
  const [sessionNotice, setSessionNotice] = useState('');
  const [resetFailed, setResetFailed] = useState(false);
  const [drag, setDrag] = useState(false);
  const [preview, setPreview] = useState<{ id: string; text: string } | null>(null);
  const [review, setReview] = useState<DetectionReview | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [loading, setLoading] = useState(true);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const currentRef = useRef<DocumentResult | null>(null);
  const csrfRef = useRef('');
  // Kept only in memory for the authorised encrypt/save handoff. Never cached.
  const retainedSource = useRef<{ documentId: string; file: File } | null>(null);
  const generation = useRef(0);
  const detailRequest = useRef<AbortController | null>(null);
  const currentRequest = useRef<AbortController | null>(null);
  const mutationRequest = useRef<AbortController | null>(null);
  const uploading = useRef(false);
  const sessionTransition = useRef(historyProvider !== unavailableHistoryProvider);
  const previousIdentity = useRef<string | null>(historyProvider === unavailableHistoryProvider ? identityKey : null);
  const resetRequest = useRef<AbortController | null>(null);
  const processing = current?.status === 'queued' || current?.status === 'processing';
  const summaryWarning = current?.warning?.replace('Images are preserved but are not scanned for sensitive data.', '').trim();

  const closeDetails = useCallback(() => {
    detailRequest.current?.abort(); detailRequest.current = null;
    setPreview(null); setReview(null); setDetailLoading(false);
  }, []);
  const clearTransient = useCallback((keepSelected = false) => {
    generation.current += 1;
    detailRequest.current?.abort(); currentRequest.current?.abort(); mutationRequest.current?.abort();
    detailRequest.current = null; currentRequest.current = null; mutationRequest.current = null;
    retainedSource.current = null;
    setSaving(false); setSavedId(null); autoSaveAttempt.current = null;
    currentRef.current = null;
    setCurrent(null); setExpiresAt(null); setPreview(null); setReview(null); setDetailLoading(false);
    setBusy(false); setDeleting(false); setChoosing(false); uploading.current = false;
    if (!keepSelected) { setFile(null); if (input.current) input.current.value = ''; }
  }, []);
  const refreshCurrent = useCallback(async () => {
    if (uploading.current || sessionTransition.current || currentRequest.current) return;
    const controller = new AbortController(); currentRequest.current = controller;
    const version = generation.current;
    try {
      const next = await api<GuestState>('guest/current', { signal: controller.signal });
      if (controller.signal.aborted || version !== generation.current) return;
      const changedSession = csrfRef.current && next.csrf_token !== csrfRef.current;
      const replaced = currentRef.current && next.document?.id !== currentRef.current.id;
      if (changedSession && currentRef.current) setSessionNotice('Your temporary session ended or the service restarted. Add your file again to continue.');
      if (changedSession || replaced) clearTransient();
      csrfRef.current = next.csrf_token; setCsrf(next.csrf_token);
      currentRef.current = next.document; setCurrent(next.document);
      if (next.document?.status === 'failed') retainedSource.current = null;
      setExpiresAt(next.document?.expires_at || next.expires_at); setResultError('');
    } catch (e) { if (!controller.signal.aborted) { setResultError((e as Error).message); return false; } }
    finally { if (currentRequest.current === controller) currentRequest.current = null; if (!controller.signal.aborted) setLoading(false); }
  }, [clearTransient]);
  useEffect(() => {
    const picker = input.current;
    const cancel = () => setChoosing(false);
    picker?.addEventListener('cancel', cancel);
    return () => picker?.removeEventListener('cancel', cancel);
  }, [path]);
  useEffect(() => {
    const controller = new AbortController();
    let failures = 0;
    const stop = startVisiblePolling(async () => {
      try {
        const next = await api<Health>('health', { signal: controller.signal });
        if (!controller.signal.aborted) { failures = 0; setHealth(next); setConnection(''); }
      } catch {
        if (!controller.signal.aborted) { failures++; setHealth(null); setConnection('Service unavailable.'); }
      }
    }, () => failures ? Math.min(60000, 15000 * 2 ** (failures - 1)) : 60000);
    return () => { stop(); controller.abort(); };
  }, []);
  useEffect(() => {
    let failures = 0;
    return startVisiblePolling(async () => {
      failures = await refreshCurrent() === false ? failures + 1 : 0;
    }, () => failures ? Math.min(60000, 15000 * 2 ** (failures - 1)) : processing ? 2000 : 30000);
  }, [processing, refreshCurrent]);
  useEffect(() => {
    const controller = new AbortController();
    api<{ secure_history: { available: boolean } }>('capabilities', { signal: controller.signal })
      .then(next => { if (!controller.signal.aborted) setHistoryAvailable(next.secure_history.available); })
      .catch(() => { if (!controller.signal.aborted) setHistoryAvailable(false); });
    return () => { controller.abort(); currentRequest.current?.abort(); mutationRequest.current?.abort(); detailRequest.current?.abort(); retainedSource.current = null; };
  }, []);
  const resetGuestSession = useCallback(async () => {
    let token = csrfRef.current;
    clearTransient(); setSavedRecord(null); setCsrf(''); csrfRef.current = ''; sessionTransition.current = true; setResetFailed(false);
    resetRequest.current?.abort();
    const controller = new AbortController(); resetRequest.current = controller;
    try {
      // A configured provider also resets on initial mount. After an interrupted
      // logout/reload, an old guest cookie must never re-expose the prior result.
      if (!token) token = (await api<GuestState>('guest/current', { signal: controller.signal })).csrf_token;
      if (controller.signal.aborted) return;
      await api('guest/current', { method: 'DELETE', headers: { 'X-CSRF-Token': token }, signal: controller.signal });
      if (controller.signal.aborted) return;
      sessionTransition.current = false; setResultError(''); void refreshCurrent();
    } catch (e) { if (!isAborted(e)) { setResetFailed(true); setResultError('The working session could not be reset.'); } }
    finally { if (resetRequest.current === controller) resetRequest.current = null; }
  }, [clearTransient, refreshCurrent]);
  useEffect(() => {
    if (previousIdentity.current === identityKey) return;
    previousIdentity.current = identityKey;
    void resetGuestSession();
  }, [identityKey, resetGuestSession]);
  useEffect(() => () => resetRequest.current?.abort(), []);
  useEffect(() => {
    if (!expiresAt) return;
    const remaining = Date.parse(expiresAt) - Date.now();
    if (!Number.isFinite(remaining)) return;
    const timer = setTimeout(() => { clearTransient(); if (document.visibilityState !== 'hidden') void refreshCurrent(); }, Math.max(remaining, 0));
    return () => clearTimeout(timer);
  }, [expiresAt, clearTransient, refreshCurrent]);
  useEffect(() => {
    const pop = () => { closeDetails(); setPath(window.location.pathname); };
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, [closeDetails]);
  useEffect(() => { if (preview) dialog.current?.showModal(); else dialog.current?.close(); }, [preview]);
  useEffect(() => {
    const hide = () => { if (document.visibilityState === 'hidden') closeDetails(); };
    window.addEventListener('pagehide', closeDetails);
    document.addEventListener('visibilitychange', hide);
    return () => { window.removeEventListener('pagehide', closeDetails); document.removeEventListener('visibilitychange', hide); };
  }, [closeDetails]);
  function navigate(next: string) {
    closeDetails(); setChoosing(false); window.history.pushState(null, '', next); setPath(next); window.scrollTo(0, 0);
  }
  function choose(next: File | undefined) {
    setChoosing(false);
    if (!next || processing) return;
    if (!/\.(docx|pdf)$/i.test(next.name)) { setError('Choose a PDF or DOCX. Convert legacy .doc files to .docx first.'); return; }
    if (next.size > 20 * 1024 * 1024) { setError('Choose a file smaller than 20 MB.'); return; }
    if (!next.size) { setError('This file is empty.'); return; }
    closeDetails(); setFile(next); setError(''); setSessionNotice('');
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file || choosing || busy || processing || !csrfRef.current || !health?.model_installed || sessionTransition.current) return;
    const source = file, selectedMode = mode, selectedSensitivity = sensitivity;
    clearTransient(true); setBusy(true); uploading.current = true; setError('');
    const controller = new AbortController(); mutationRequest.current = controller;
    const version = generation.current;
    try {
      const suffix = '.' + source.name.split('.').pop()!.toLowerCase();
      const result = await api<{ id: string }>(`guest/documents?mode=${selectedMode}&type=${suffix}&sensitivity=${selectedSensitivity}`, {
        method: 'POST', body: source, headers: { 'X-CSRF-Token': csrfRef.current, 'X-Document-Name': encodeURIComponent(source.name) }, signal: controller.signal,
      });
      if (controller.signal.aborted || version !== generation.current) return;
      retainedSource.current = { documentId: result.id, file: source };
      const accepted: DocumentResult = { id: result.id, filename: source.name, created: new Date().toISOString(), mode: selectedMode, sensitivity: selectedSensitivity, source_type: suffix.slice(1), status: 'queued', counts: {} };
      currentRef.current = accepted; setCurrent(accepted);
      setFile(null); if (input.current) input.current.value = '';
    } catch (e) {
      if (!isAborted(e) && version === generation.current) { setError((e as Error).message); setFile(null); if (input.current) input.current.value = ''; retainedSource.current = null; }
    }
    finally {
      if (mutationRequest.current === controller) mutationRequest.current = null;
      if (version === generation.current) { uploading.current = false; setBusy(false); void refreshCurrent(); }
    }
  }
  async function remove() {
    if (!current || !window.confirm('Are you sure you want to trash this file?')) return;
    closeDetails(); setDeleting(true); setError('');
    const controller = new AbortController(); mutationRequest.current = controller;
    const version = generation.current;
    try {
      await api(`guest/documents/${current.id}`, { method: 'DELETE', headers: { 'X-CSRF-Token': csrfRef.current }, signal: controller.signal });
      if (controller.signal.aborted || version !== generation.current) return;
      clearTransient(); void refreshCurrent();
    } catch (e) { if (!isAborted(e)) setError((e as Error).message); }
    finally { if (mutationRequest.current === controller) mutationRequest.current = null; if (version === generation.current) setDeleting(false); }
  }
  async function openDetail(kind: 'preview' | 'review') {
    if (!current) return;
    closeDetails(); setDetailLoading(true);
    const controller = new AbortController(); detailRequest.current = controller;
    const id = current.id, version = generation.current;
    try {
      if (kind === 'preview') {
        const data = await api<{ text: string }>(`guest/documents/${id}/preview`, { signal: controller.signal });
        if (!controller.signal.aborted && version === generation.current && currentRef.current?.id === id) setPreview({ id, text: data.text });
      } else {
        const data = await api<DetectionReview>(`guest/documents/${id}/review`, { signal: controller.signal });
        if (!controller.signal.aborted && version === generation.current && currentRef.current?.id === id) setReview(data);
      }
    } catch (e) { if (!isAborted(e) && version === generation.current) setError((e as Error).message); }
    finally { if (detailRequest.current === controller) { detailRequest.current = null; setDetailLoading(false); } }
  }
  const saveCurrent = useCallback(async () => {
    const document = currentRef.current, retained = retainedSource.current;
    if (!document || document.status !== 'complete' || identity.status !== 'authenticated' || !historyProvider.collectGuest || !retained || retained.documentId !== document.id) return;
    const version = generation.current;
    const controller = new AbortController(); mutationRequest.current = controller;
    setSaving(true); setError('');
    try {
      const artifacts = await historyProvider.collectGuest(document.id, retained.file, controller.signal);
      const saved = await historyProvider.save({ ...artifacts, metadata: document, filename: retained.file.name }, controller.signal);
      if (!controller.signal.aborted && version === generation.current) {
        setSavedRecord(saved); setSavedId(document.id); setHistoryRevision(value => value + 1);
        try {
          await api(`guest/documents/${document.id}`, { method: 'DELETE', headers: { 'X-CSRF-Token': csrfRef.current }, signal: controller.signal });
          if (!controller.signal.aborted && version === generation.current) { clearTransient(); void refreshCurrent(); }
        } catch (e) {
          if (!isAborted(e) && version === generation.current) setError('Saved to encrypted history. The temporary working copy could not be cleared; trash the current result to remove it.');
        }
      }
    } catch (e) {
      if (!isAborted(e) && version === generation.current) setError('Could not save encrypted history. Your current downloads are still available. Retry while this result is open.');
    } finally {
      if (mutationRequest.current === controller) mutationRequest.current = null;
      if (version === generation.current) setSaving(false);
    }
  }, [identity.status, historyProvider, clearTransient, refreshCurrent]);
  useEffect(() => {
    if (current?.status !== 'complete' || identity.status !== 'authenticated' || !historyProvider.collectGuest || autoSaveAttempt.current === current.id || !retainedSource.current) return;
    autoSaveAttempt.current = current.id;
    void saveCurrent();
  }, [current, identity.status, historyProvider, saveCurrent]);
  const waiting = choosing ? 'Preparing file…' : busy ? 'Uploading…' : file && (!health || !csrf) && !connection ? 'Checking service…' : '';
  return <>
    <header className="topbar">
      <a className="brand" href="/" aria-label="Redacted home" onClick={event => { event.preventDefault(); navigate('/'); }}><img src="/brand/redacted-logo.svg" alt="Redacted" width="2048" height="455" /></a>
      <AccountMenu identity={identity} provider={historyProvider} available={historyAvailable} onInformation={() => navigate('/secure-history')}/>
    </header>
    {path === '/secure-history/linked' ? <TideLinkComplete/> : path === '/secure-history/setup' ? <TideSetupPage identity={identity} provider={historyProvider} navigate={navigate}/> : path === '/disclaimer' ? <DisclaimerPage navigate={navigate}/> : path === '/secure-history' ? <SecureHistoryPage identity={identity} provider={historyProvider} available={historyAvailable} navigate={navigate}/> : <main>
      <h1 className="sr-only">Redact a document</h1>
      <form onSubmit={submit}>
        <input ref={input} id="document" type="file" accept=".pdf,.docx" onClick={() => setChoosing(true)} onChange={e => choose(e.target.files?.[0])} className="file-input" disabled={busy || processing}/>
        <label htmlFor="document" className={`dropzone ${drag ? 'dragging' : ''} ${file ? 'selected' : ''} ${waiting ? 'waiting' : ''}`} aria-busy={Boolean(waiting)} onDragOver={e => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); if (!busy && !processing) choose(e.dataTransfer.files[0]); }}>
          {waiting ? <span className="file-wait-ring" aria-hidden="true"/> : <span className="upload-letter" aria-hidden="true"><i/><i/><i/><i/></span>}
          <strong>{file ? file.name : 'Choose a file'}</strong>
          <small role="status" aria-live="polite">{waiting || (file ? `${(file.size / 1024).toFixed(0)} KB` : 'PDF / DOCX · 20 MB max')}</small>
          {!waiting && !processing && <span className="upload-instructions">Drag and drop a file here, or click to browse</span>}
        </label>
        <div className="upload-actions">
          <button className="primary redact-button" type="submit" data-needs-file={!file && !choosing && !busy && !processing} aria-label={busy ? 'Uploading…' : 'REDACT'} disabled={!file || choosing || busy || processing || !csrf || !health?.model_installed}>
            {busy ? <><LoaderCircle className="spin" size={22}/> Uploading…</> : <><span className="redact-label">REDACT</span><ArrowRight className="redact-arrow" size={24}/></>}
          </button>
          <button className="settings-button" type="button" aria-expanded={settingsOpen} aria-controls="upload-settings" onClick={() => setSettingsOpen(!settingsOpen)}>
            <SlidersHorizontal size={17}/><span>Settings</span><ChevronDown size={15} className={settingsOpen ? 'expanded' : ''}/>
          </button>
        </div>
        <div id="upload-settings" className="settings-panel" hidden={!settingsOpen}>
          <div className="sensitivity">
            <div className="sensitivity-heading"><label htmlFor="sensitivity">Sensitivity</label><HelpTip label="sensitivity">Higher flags more potential sensitive text, but may also catch ordinary text. 50 uses the model’s default.</HelpTip><output htmlFor="sensitivity">{sensitivity}</output></div>
            <input id="sensitivity" type="range" min="0" max="100" step="5" value={sensitivity} onChange={e => setSensitivity(Number(e.target.value))} />
            <div className="range-labels" aria-hidden="true"><span>Low</span><span>High</span></div>
          </div>
          <div className="mode-heading">Mode <HelpTip label="redaction modes"><strong>Mask:</strong> masks with ******.<br/><strong>Label:</strong> labels like [Name] and [Date].<br/><strong>Replace:</strong> consistent fictional data.</HelpTip></div>
          <fieldset className="mode-options" aria-label="Replacement mode">
            {[['redact', 'Mask'], ['placeholder', 'Label'], ['synthetic', 'Replace']].map(([value, title]) => (
              <label key={value} className={`mode-option ${mode === value ? 'active' : ''}`}>
                <input type="radio" name="mode" value={value} checked={mode === value} onChange={() => setMode(value as Mode)} />
                <span className="radio-mark">{mode === value && <Check size={12}/>}</span>
                {title}
              </label>
            ))}
          </fieldset>
        </div>
      </form>
      {connection && <div className="notice" role="status">Service unavailable.</div>}
      {sessionNotice && <div className="notice" role="status">{sessionNotice}</div>}
      {health && !health.model_installed && <div className="notice" role="status">Model unavailable. See the README for setup.</div>}
      {error && <div className="notice error" role="alert">{error}<button aria-label="Dismiss error" onClick={() => setError('')}><X size={17}/></button></div>}
      <section className="library" aria-label="Files"><div className="library-heading"><h2>Files</h2></div>
        {resultError ? <div className="empty" role="status">{resetFailed ? <><span>The working session could not be reset.</span><button className="text-button" onClick={() => void resetGuestSession()}>Retry reset</button></> : <><span>Connection interrupted. Reconnecting to your temporary result…</span><button className="text-button" onClick={() => void refreshCurrent()}>Retry now</button></>}</div> : loading ? <div className="empty" role="status"><LoaderCircle className="spin" size={18}/><span>Loading…</span></div> : !current ? (identity.status !== 'authenticated' ? <div className="empty">No files yet.</div> : null) : <>
          <article className="document-row"><div className="doc-icon"><FileText size={22}/></div><div className="doc-info">
            <h3><span className={`document-name${processing ? ' is-processing' : ''}`} title={current.filename || undefined}><span className="filename-text">{current.filename || `Document ${current.id.slice(0, 8)}`}</span>{processing && <><span className="redaction-loader" aria-hidden="true"/><span className="sr-only" role="status">{current.status === 'queued' ? 'Queued' : 'Processing'}</span></>}</span><span className="mode-pill">{modeLabels[current.mode]?.[current.status === 'complete' ? 1 : 0] || current.mode}</span>{current.status === 'failed' && <span className="status failed">Failed</span>}</h3>
            <p>{new Date(current.created).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}</p>
            {current.status === 'complete' && <div className="counts">{Object.entries(current.counts).length ? Object.entries(current.counts).map(([key, value]) => <span key={key}>{value} {categoryLabels[key]?.toLowerCase() || key}</span>) : <span>No detections</span>}</div>}
            {current.error && <p className="doc-error">{current.error}</p>}{summaryWarning && <p className="doc-warning">{summaryWarning}</p>}
          </div><div className="doc-actions">
            {current.status === 'complete' && <><button className="text-button" onClick={() => openDetail('preview')}>Preview</button><button className="text-button" onClick={() => openDetail('review')}>Review detections</button>
              <div className="downloads">{(current.source_type === 'pdf' ? ['pdf', 'docx', 'txt'] : ['docx', 'pdf', 'txt']).map(ext => <a key={ext} title={current.source_type === ext && current.layout_preserved ? 'Original layout' : 'Rebuilt text'} href={`/api/service/guest/documents/${current.id}/download/${ext}`} aria-label={`Download ${ext.toUpperCase()} for document ${current.id.slice(0, 8)}`}><ArrowDownToLine size={13}/>{ext.toUpperCase()}</a>)}</div></>}
            <button className="delete" onClick={remove} disabled={processing || deleting || saving} aria-label={`Delete document ${current.id.slice(0, 8)} and all its output files`} title="Delete document and all output files">{deleting ? <LoaderCircle className="spin" size={17}/> : <Trash2 size={17}/>}</button>
          </div></article>
          {current.status === 'complete' && identity.status !== 'authenticated' && <p className="history-prompt"><a href="/secure-history" onClick={event => { event.preventDefault(); navigate('/secure-history'); }}>Set up encrypted history for future uploads.</a></p>}
          {current.status === 'complete' && identity.status === 'authenticated' && <p className="history-prompt" role="status">{saving ? 'Encrypting and saving…' : savedId === current.id ? 'Saved to your encrypted history.' : retainedSource.current ? <button className="text-button" onClick={() => void saveCurrent()}>Retry saving encrypted history</button> : 'Upload again to save the original and result to history.'}</p>}
        </>}
        {detailLoading && <p className="empty" role="status"><LoaderCircle className="spin" size={18}/>Loading details…</p>}
        {review && current && <ReviewPanel key={current.id} review={review} onClose={closeDetails} onReveal={async signal => {
          const result = await api<{ values: RevealedDetection[] }>(`guest/documents/${current.id}/revealed-detections`, { signal });
          return result.values;
        }}/> }
      {historyAvailable && identity.status === 'authenticated' && <HistoryPanel key={identity.ownerKey} provider={historyProvider} identity={identity} refreshKey={historyRevision}
        hasCurrent={Boolean(current)} excludeId={current && savedId === current.id ? savedRecord?.id : undefined} savedRecord={savedRecord}/>}
      </section>
    </main>}
    <footer className="site-footer">
      <span>A Tide community project · <a href="https://github.com/tide-foundation/redacted" target="_blank" rel="noopener noreferrer">GitHub</a></span>
      <p>No guarantees of complete redaction. Review before sharing. <a href="/disclaimer" onClick={event => { event.preventDefault(); navigate('/disclaimer'); }}>Full disclaimer</a></p>
    </footer>
    <dialog ref={dialog} onCancel={closeDetails} onClick={event => { if (event.target === event.currentTarget) closeDetails(); }}><div className="preview-header"><h2>Preview</h2><button aria-label="Close preview" onClick={closeDetails}><X/></button></div><pre>{preview?.text}</pre></dialog>
  </>;
}
