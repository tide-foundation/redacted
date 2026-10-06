import { useCallback, useEffect, useRef, useState } from 'react';
import { Eye, EyeOff, Pencil, X } from 'lucide-react';
import type { DetectionReview, RevealedDetection } from './types';
import { categoryLabels } from './types';
import { CorrectionEditor, type CorrectionAccess } from './CorrectionEditor';

export type RevealValues = (signal: AbortSignal) => Promise<RevealedDetection[]>;

export function ReviewPanel({ id, review, onClose, onReveal, correctionAccess }: {
  id?: string;
  review: DetectionReview;
  onClose: () => void;
  onReveal?: RevealValues;
  correctionAccess?: CorrectionAccess;
}) {
  const section = useRef<HTMLElement>(null);
  const [editing,setEditing]=useState(false);
  useEffect(() => {
    section.current?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
  }, [editing]);
  const [values, setValues] = useState<RevealedDetection[] | null>(null);
  const [pending, setPending] = useState(false);
  const [replacements, setReplacements] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const clearValues = useCallback(() => {
    request.current?.abort(); request.current = null;
    setValues(null); setPending(false); setError('');
  }, []);
  useEffect(() => {
    clearValues(); setReplacements({});
    const hide = () => { if (document.visibilityState === 'hidden') clearValues(); };
    document.addEventListener('visibilitychange', hide);
    window.addEventListener('pagehide', clearValues);
    return () => {
      request.current?.abort(); request.current = null;
      document.removeEventListener('visibilitychange', hide);
      window.removeEventListener('pagehide', clearValues);
    };
  }, [review, clearValues]);
  async function toggle() {
    if (values !== null || request.current) { clearValues(); return; }
    if (!onReveal) return;
    const controller = new AbortController(); request.current = controller;
    setPending(true); setError('');
    try {
      const originals = await onReveal(controller.signal);
      if (!controller.signal.aborted && request.current === controller) {
        setValues(originals);
        setReplacements(Object.fromEntries(originals.flatMap(value => value.replacement === undefined ? [] : [[`${value.category}:${value.occurrence}`, value.replacement]])));
      }
    } catch {
      if (!controller.signal.aborted && request.current === controller) setError('Could not reveal the values. Please try again.');
    } finally {
      if (request.current === controller) { request.current = null; setPending(false); }
    }
  }
  const revealed = new Map(values?.map(value => [`${value.category}:${value.occurrence}`, value]));
  const active = values !== null || pending;
  const report = review.scan_report;
  if(editing && correctionAccess) return <CorrectionEditor access={correctionAccess} onBack={()=>setEditing(false)} onClose={onClose}/>;
  return <section ref={section} id={id} className="review-panel" aria-label="Detection review">
    <div className="preview-header review-heading"><h2>Detections</h2><div className="review-actions">
      {correctionAccess && <button className="text-button" onClick={()=>{clearValues();setEditing(true);}}><Pencil size={14} aria-hidden="true"/> Review and correct</button>}
      {review.detections.length > 0 && <button className="text-button reveal-button" disabled={!onReveal} aria-label={active ? 'Hide original values' : 'Reveal original values'} aria-pressed={values !== null} onClick={() => void toggle()}>
        {active ? <EyeOff size={14}/> : <Eye size={14}/>} {active ? 'Hide values' : 'Reveal values'}
      </button>}
      <button aria-label="Close detection review" onClick={() => { clearValues(); onClose(); }}><X size={20}/></button>
    </div></div>
    {review.detections.length ? <div className="detection-list">{review.detections.map(detection => {
      const key = `${detection.category}:${detection.occurrence}`;
      const value = revealed.get(key);
      const original = value?.original;
      return <div className="detection-row" key={key}>
        <span className="detection-category">{categoryLabels[detection.category] || detection.category} <small>#{detection.occurrence}</small></span>
        {original !== undefined ? <span className="revealed-value">{original}</span> : <span className="concealed-value" aria-label="Original value concealed" aria-busy={pending}>{pending && <span className="reveal-wait" aria-hidden="true"/>}</span>}
        <span className="replacement-value">{detection.replacement ?? replacements[key] ?? (pending ? <span className="concealed-value" aria-label="Loading replacement" aria-busy="true"><span className="reveal-wait" aria-hidden="true"/></span> : <span title="This older file stores replacements with its originals. Reveal once to load them.">—</span>)}</span>
      </div>;
    })}</div> : <p className="review-note">No sensitive text detected.</p>}
    {error && <p className="review-note" role="alert">{error}</p>}
    <details className="scan-report" open><summary>Scan report</summary>
      <dl><div><dt>Sensitivity</dt><dd>{report.sensitivity}</dd></div><div><dt>Detections</dt><dd>{report.total_detections}</dd></div>
        <div><dt>File type</dt><dd>{report.source_type.replace('.', '').toUpperCase()}</dd></div>
        <div><dt>Matching-format layout</dt><dd>{report.layout_preserved ? 'Preserved' : 'Clean rewrite'}</dd></div>
        <div><dt>OCR</dt><dd>{report.ocr_performed ? 'Performed' : 'Not performed'}</dd></div>
      </dl>
      {Object.keys(report.counts).length > 0 && <p className="review-note">{Object.entries(report.counts).map(([category, count]) => `${count} ${(categoryLabels[category] || category).toLowerCase()}`).join(' · ')}</p>}
      {(report.warnings.length > 0 || report.limitations.length > 0) && <ul>{[...new Set([...report.warnings, ...report.limitations])].map(message => <li key={message}>{message}</li>)}</ul>}
      <p className="review-note">Sensitivity is a detection setting, not confidence. Review the output before sharing.</p>
    </details>
  </section>;
}
