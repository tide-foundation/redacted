import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, CheckCircle2, LoaderCircle, X } from 'lucide-react';
import { hideRange, parseCorrection, rebuild, type Correction } from './corrections';
import { categoryLabels } from './types';
export type CorrectionAccess = { load(signal:AbortSignal):Promise<Correction>; save?: (value:Correction,signal:AbortSignal)=>Promise<Correction> };
export function CorrectionEditor({access,onBack,onClose}:{access:CorrectionAccess;onBack:()=>void;onClose:()=>void}) {
  const [value,setValue]=useState<Correction|null>(null),[baseline,setBaseline]=useState(''),[range,setRange]=useState<[number,number]|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const suppressClick=useRef(false);
  const savedValue=useRef<Correction|null>(null);
  const box=useRef<HTMLDivElement>(null),request=useRef<AbortController|null>(null);
  const dirty=!!value && JSON.stringify(value.details)!==baseline;
  const dirtyRef=useRef(dirty);dirtyRef.current=dirty;
  useEffect(()=>{
    const controller=new AbortController();request.current=controller;
    access.load(controller.signal).then(v=>{if(!controller.signal.aborted){const parsed=parseCorrection(v);savedValue.current=parsed;setValue(parsed);setBaseline(JSON.stringify(parsed.details));}}).catch(()=>{if(!controller.signal.aborted)setError('Could not open the original text. Please close and try again.');});
    const unload=(event:BeforeUnloadEvent)=>{if(dirtyRef.current){event.preventDefault();event.returnValue='';}};
    // Clear the active selection when the browser backgrounds the page.
    const hide=()=>{if(document.visibilityState==='hidden'){setRange(null);window.getSelection()?.removeAllRanges();}};
    window.addEventListener('beforeunload',unload);document.addEventListener('visibilitychange',hide);
    return()=>{controller.abort();request.current?.abort();window.removeEventListener('beforeunload',unload);document.removeEventListener('visibilitychange',hide);};
  },[]);
  function selection(paint=false) {
    const s=window.getSelection();setRange(null);
    if(busy || !access.save || !s || s.isCollapsed || !box.current) return;
    const offset=(node:Node|null,n:number):number|null=>{
      if(!node || !box.current!.contains(node)) return null;
      if(node.nodeType===Node.TEXT_NODE) {const el=node.parentElement;if(!el?.matches('[data-start]')) return null;return Number(el.dataset.start)+n;}
      if(node instanceof HTMLElement && node.matches('[data-start]')) return Number(node.dataset.start)+(n?node.textContent?.length||0:0);
      if(node===box.current) {const child=node.childNodes[n] as HTMLElement|undefined;return child?Number(child.dataset.start):value!.text.length;}
      return null;
    };
    const a=offset(s.anchorNode,s.anchorOffset),b=offset(s.focusNode,s.focusOffset);
    if(a===null || b===null)return;
    let start=Math.min(a,b),end=Math.max(a,b);
    while(start<end && /\s/u.test(value!.text[start]))start++;
    while(end>start && /\s/u.test(value!.text[end-1]))end--;
    if(start<end) {
      if(paint) { suppressClick.current=true;update(hideRange(value!,start,end)); }
      else setRange([start,end]);
    }
  }
  function update(next:Correction) {setValue(next);setRange(null);setNotice('');window.getSelection()?.removeAllRanges();}
  async function save() {
    if(!value || !access.save)return;
    const controller=new AbortController();request.current=controller;setBusy(true);setError('');
    try {const next=parseCorrection(await access.save(parseCorrection(value),controller.signal));if(!controller.signal.aborted){savedValue.current=next;setValue(next);setBaseline(JSON.stringify(next.details));setNotice('Changes saved. Downloads now include your corrections.');}}
    catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'Could not save changes.');}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  const groups=new Map<string,Map<string,string>>();
  for(const d of value?.details||[]) {if(!groups.has(d.category))groups.set(d.category,new Map());groups.get(d.category)!.set(d.label,value!.text.slice(d.start,d.end));}
  const runs=[];let cursor=0;
  for(const d of value?.details||[]) {
    if(d.start>cursor)runs.push(<span key={`text-${cursor}`} data-start={cursor}>{value!.text.slice(cursor,d.start)}</span>);
    const remove=(keyboard=false)=>{if((keyboard || !suppressClick.current) && access.save && !busy && window.getSelection()?.isCollapsed!==false)update(rebuild(value!.text,value!.details.filter(x=>x!==d),value!.mode,value!.revision));};
    runs.push(<span key={`detail-${d.start}`} data-start={d.start} className={`correction-hidden${d.category==='manual'?' correction-added':''}`} title={`Hidden as ${d.label}${access.save?' — × Unhide this occurrence':''}`} aria-label={access.save?`Unhide ${d.label}, this occurrence`:undefined} role={access.save?'button':undefined} tabIndex={access.save?0:undefined} onClick={()=>remove()} onKeyDown={e=>{if(access.save&&(e.key==='Enter'||e.key===' ')){e.preventDefault();remove(true);}}}>{value!.text.slice(d.start,d.end)}</span>);cursor=d.end;
  }
  if(value)runs.push(<span key={`text-${cursor}`} data-start={cursor}>{value.text.slice(cursor)}</span>);
  return <section className="correction-editor" aria-label="Review and correct">
    <div className="preview-header review-heading"><h2>Review and correct</h2><div className="review-actions"><button className="text-button" disabled={busy} onClick={()=>{if(!dirty||window.confirm('Discard your unsaved corrections?'))onBack();}}><ArrowLeft size={16}/> Back to detections</button><button aria-label="Close detection review" disabled={busy} onClick={()=>{if(!dirty||window.confirm('Discard your unsaved corrections?'))onClose();}}><X size={20}/></button></div></div>
    {value && <>
      <p className="review-note">{access.save?'Drag over text to hide it. Hover over an underline and click × to unhide that occurrence.':'Underlined details are hidden in your downloads.'}</p>
      <div className="correction-layout"><div ref={box} className={`correction-text${access.save?' is-highlighting':''}`} aria-label="Original document text" onMouseDownCapture={()=>{suppressClick.current=false;}} onMouseUp={()=>selection(true)} onKeyUp={()=>selection()} tabIndex={0}>{runs}</div><aside className="correction-summary" aria-label="Hidden details">
        <strong>{value.detailCount?`${value.detailCount} hidden details`:'Nothing is hidden.'}</strong>
        {[...groups].map(([category,items])=><section key={category}><h3>{categoryLabels[category]||category} {items.size}</h3><ul>{[...items].map(([label,text])=><li key={label}><code>{label}</code>{text!==label&&<span>{text}</span>}</li>)}</ul></section>)}
      </aside></div>
      {access.save && <div className="correction-actions">{range&&<button className="settings-button" disabled={!range||busy} onClick={()=>range&&update(hideRange(value,...range))}>Hide selected text</button>}{dirty&&<><button className="primary" disabled={busy} onClick={()=>void save()}>{busy&&<LoaderCircle className="spin" size={16}/>} {busy?'Saving…':'Save changes'}</button><button className="text-button" disabled={busy} onClick={()=>savedValue.current&&update(savedValue.current)}>Discard changes</button></>}</div>}
      {dirty&&<p className="review-note">Unsaved changes. Saving rebuilds PDF and DOCX as text; original layout and images are not retained in corrected downloads.</p>}
    </>}
    {!value&&!error&&<p role="status"><LoaderCircle className="spin" size={16}/> Opening review…</p>}
    {error&&<p role="alert" className="notice">{error}</p>}{notice&&<div role="status" className="correction-status"><CheckCircle2 size={18} aria-hidden="true"/><span>{notice}</span></div>}
  </section>;
}
