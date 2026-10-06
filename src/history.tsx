import { Fragment, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { ArrowDownToLine, Trash2, FileText } from 'lucide-react';
import type { DetectionReview, DocumentResult, OutputFormat, RevealedDetection } from './types';
import { modeLabels, categoryLabels } from './types';
import { ReviewPanel } from './ReviewPanel';
import type { Correction } from './corrections';

export type IdentityState =
  | { status: 'unavailable'; reason: string }
  | { status: 'signed-out' | 'loading' | 'error' }
  | { status: 'authenticated'; ownerKey: string; canWrite?: boolean };
export type { RevealedDetection } from './types';
export type HistoryRecord = DocumentResult;
export type TransientHistoryInput = {
  source: Blob;
  manifest: Blob;
  outputs: Record<OutputFormat, Blob>;
  // Only safe metadata may enter the durable record. Protect filename separately from detections.
  metadata: Omit<DocumentResult, 'filename'>;
  filename?: string;
};


// Authentication and protection are supplied by the Tide adapter.
// The adapter owns authorised API access and decryption; tokens are not component props.
export interface SecureHistoryProvider {
  correction?(id: string, signal: AbortSignal): Promise<Correction>;
  saveCorrection?(id: string, value: Correction, signal: AbortSignal): Promise<Correction>;
  collectGuest?(id: string, source: Blob, signal: AbortSignal): Promise<Pick<TransientHistoryInput, 'source' | 'manifest' | 'outputs'>>;
  testProtection?(signal: AbortSignal): Promise<void>;
  getSnapshot(): IdentityState;
  subscribe(listener: () => void): () => void;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  // These receive/return transient plaintext in memory. The verified adapter
  // must protect every durable artifact before storage, including outputs.
  save(input: TransientHistoryInput, signal: AbortSignal): Promise<HistoryRecord>;
  recoverOriginal(id: string, signal: AbortSignal): Promise<Blob>;
  filename?(id: string, signal: AbortSignal): Promise<string | null>;
  list(signal: AbortSignal): Promise<HistoryRecord[]>;
  remove(id: string, signal: AbortSignal): Promise<void>;
  review(id: string, signal: AbortSignal): Promise<DetectionReview>;
  reveal(id: string, signal: AbortSignal): Promise<RevealedDetection[]>;
  download(id: string, format: OutputFormat, signal: AbortSignal): Promise<Blob>;
}

const unavailable: IdentityState = Object.freeze({ status: 'unavailable', reason: 'Secure history is not configured.' });
const rejectUnavailable = async (): Promise<never> => { throw new Error('Secure history is not configured.'); };
export const unavailableHistoryProvider: SecureHistoryProvider = Object.freeze({
  getSnapshot: () => unavailable,
  subscribe: () => () => {},
  signIn: rejectUnavailable, signOut: rejectUnavailable, list: rejectUnavailable,
  remove: rejectUnavailable, review: rejectUnavailable, reveal: rejectUnavailable, download: rejectUnavailable,
  save: rejectUnavailable, recoverOriginal: rejectUnavailable,
});
export function useIdentity(provider: SecureHistoryProvider) {
  return useSyncExternalStore(provider.subscribe, provider.getSnapshot, provider.getSnapshot);
}

function HistoryFilename({ provider, record, revision, onName }: {
  provider: SecureHistoryProvider; record: HistoryRecord; revision: number; onName: (id: string, name: string) => void;
}) {
  const [pending, setPending] = useState(Boolean(!record.filename && provider.filename));
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    if (!record.filename && provider.filename) {
      setPending(true); setFailed(false);
      void provider.filename(record.id, controller.signal).then(name => {
        if (!controller.signal.aborted && name) onName(record.id, name);
      }).catch(() => { if (!controller.signal.aborted) setFailed(true); })
        .finally(() => { if (!controller.signal.aborted) setPending(false); });
    } else setPending(false);
    return () => controller.abort();
  }, [provider, record.id, record.filename, revision, retry]);
  return <span className="document-name" title={record.filename || undefined}>
    {pending ? <span className="decrypt-skeleton filename-skeleton" role="status" aria-label="Decrypting filename"/> :
      <span className="filename-text">{record.filename || `Document ${record.id.slice(0, 8)}`}</span>}
    {failed && <button className="text-button" onClick={() => setRetry(value => value + 1)}>Retry filename</button>}
  </span>;
}

// Saved rows share the same Files section as the current working row.
export function HistoryPanel({ provider, identity, refreshKey = 0, hasCurrent = false, excludeId, savedRecord }: {
  provider: SecureHistoryProvider; identity: IdentityState; refreshKey?: number;
  hasCurrent?: boolean; excludeId?: string; savedRecord?: HistoryRecord | null;
}) {
  const [records, setRecords] = useState<HistoryRecord[]>([]);
  const [selected, setSelected] = useState<{ record: HistoryRecord; review: DetectionReview } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [revision, setRevision] = useState(0);
  const [nameRevision, setNameRevision] = useState(0);
  const [busyAction, setBusyAction] = useState('');
  const activeAction = useRef<AbortController | null>(null);
  const removed = useRef(new Set<string>());
  const owner = identity.status === 'authenticated' ? identity.ownerKey : null;
  useEffect(() => {
    setRecords([]); setSelected(null); removed.current.clear();
    return () => { activeAction.current?.abort(); };
  }, [provider, owner]);
  useEffect(() => {
    const controller = new AbortController(); setError('');
    if (owner) {
      setBusy(true);
      provider.list(controller.signal).then(items => { if (!controller.signal.aborted) setRecords(items); })
        .catch(() => { if (!controller.signal.aborted) setError('Could not load saved files.'); })
        .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    }
    return () => controller.abort();
  }, [provider, owner, revision, refreshKey]);
  async function startAction(key: string, run: (signal: AbortSignal) => Promise<void>) {
    activeAction.current?.abort();
    const controller = new AbortController(); activeAction.current = controller;
    setBusyAction(key); setError('');
    try { await run(controller.signal); }
    catch { if (!controller.signal.aborted) setError('The saved file action could not be completed.'); }
    finally { if (activeAction.current === controller) { activeAction.current = null; setBusyAction(''); } }
  }
  function download(blob: Blob, name: string) {
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const nameLoaded = (id: string, name: string) => setRecords(items => items.map(item => item.id === id ? {...item, filename: name} : item));
  const combined = new Map(records.map(record => [record.id, record]));
  if (savedRecord && !combined.has(savedRecord.id)) combined.set(savedRecord.id, savedRecord);
  const shown = [...combined.values()].filter(record => record.id !== excludeId && !removed.current.has(record.id))
    .sort((a, b) => b.created.localeCompare(a.created));
  if (!owner) return null;
  return <>
    {error && <p role="alert" className="notice">{error}<button className="text-button" onClick={() => setRevision(value => value + 1)}>Retry</button></p>}
    {busy && !shown.length ? <div className="document-row" aria-label="Loading saved files"><span className="decrypt-skeleton filename-skeleton" role="status" aria-label="Loading filenames"/></div> : !shown.length && !hasCurrent && !error ? <p className="empty">No files yet.</p> : null}
    {shown.map(record => <Fragment key={record.id}><article className="document-row" data-document-id={record.id}>
      <div className="doc-icon"><FileText size={22}/></div>
      <div className="doc-info"><h3><HistoryFilename provider={provider} record={record} revision={nameRevision} onName={nameLoaded}/><span className="mode-pill">{modeLabels[record.mode][1]}</span></h3>
        <p>{new Date(record.created).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}</p>
        <div className="counts">{record.review_revision ? <span>{record.detail_count} hidden details · Reviewed</span> : Object.entries(record.counts).length ? Object.entries(record.counts).map(([category, count]) => <span key={category}>{count} {categoryLabels[category]?.toLowerCase() || category}</span>) : <span>No detections</span>}</div>
      </div>
      <div className="doc-actions">
        <button className={`text-button${busyAction === record.id + ':source' ? ' action-decrypting' : ''}`} aria-busy={busyAction === record.id + ':source'} onClick={() => void startAction(record.id + ':source', async signal => {
          const blob = await provider.recoverOriginal(record.id, signal);
          if (!signal.aborted) download(blob, record.filename || `original.${record.source_type}`);
        })}>Original</button>
        <button className="text-button" aria-expanded={selected?.record.id === record.id} aria-controls={`review-${record.id}`} onClick={() => { if (selected?.record.id === record.id) { setSelected(null); return; } setSelected(null); void startAction(record.id + ':review', async signal => {
          const review = await provider.review(record.id, signal); if (!signal.aborted) setSelected({ record, review });
        }); }}>Review detections</button>
        <div className="downloads">{(['pdf', 'docx', 'txt'] as OutputFormat[]).map(format => <button key={format}
          className={busyAction === record.id + ':' + format ? 'action-decrypting' : undefined} aria-busy={busyAction === record.id + ':' + format}
          onClick={() => void startAction(record.id + ':' + format, async signal => {
            const blob = await provider.download(record.id, format, signal);
            if (!signal.aborted) download(blob, `sanitized-${record.id.slice(0, 8)}.${format}`);
          })}><ArrowDownToLine size={13}/>{format.toUpperCase()}</button>)}</div>
        <button className="delete" aria-label="Delete saved document" onClick={() => {
          if (!window.confirm('Are you sure you want to trash this file?')) return;
          void startAction(record.id + ':delete', async signal => {
            await provider.remove(record.id, signal);
            if (!signal.aborted) { removed.current.add(record.id); setRecords(items => items.filter(item => item.id !== record.id)); if (selected?.record.id === record.id) setSelected(null); setRevision(value => value + 1); }
          });
        }}><Trash2 size={17}/></button>
      </div>
    </article>
    {selected?.record.id === record.id && <ReviewPanel id={`review-${record.id}`} review={selected.review} onClose={() => setSelected(null)}
      correctionAccess={provider.correction ? {
        load: signal=>provider.correction!(record.id,signal),
        save: identity.status==='authenticated' && identity.canWrite!==false && provider.saveCorrection ? async (value,signal)=>{
          const saved=await provider.saveCorrection!(record.id,value,signal);
          const review=await provider.review(record.id,signal);
          if(!signal.aborted){setSelected({record,review});setRevision(n=>n+1);}
          return saved;
        } : undefined,
      }:undefined}
      onReveal={async signal => { const values = await provider.reveal(record.id, signal); if (!signal.aborted) setNameRevision(value => value + 1); return values; }}/>}
    </Fragment>)}
  </>;
}
