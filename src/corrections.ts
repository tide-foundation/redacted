import type { Mode } from './types';
export type Span = { start: number; end: number; category: string };
export type Detail = Span & { label: string; replacement: string };
export type Correction = { v: 1; text: string; mode: Mode; details: Detail[]; redacted: string; detailCount: number; revision: number };
const stems: Record<string,string> = {private_person:'NAME',private_address:'ADDRESS',private_email:'EMAIL',private_phone:'PHONE',private_date:'DATE',private_url:'URL',account_number:'ACCOUNT',secret:'SECRET',manual:'HIDDEN'};
const normalize = (value: string) => value.trim().replace(/\s+/gu,' ').toLowerCase();
const boundary = (text: string, n: number) => !(n > 0 && n < text.length && /[\uD800-\uDBFF]/.test(text[n-1]) && /[\uDC00-\uDFFF]/.test(text[n]));
export function validateSpans(text: string, spans: Span[]) {
  if (typeof text !== 'string' || text.length > 2_000_000 || !Array.isArray(spans) || spans.length > 200_000) throw new Error('Invalid review document.');
  let end = 0;
  for (const s of spans) {
    if (!s || !Number.isInteger(s.start) || !Number.isInteger(s.end) || s.start < end || s.end <= s.start || s.end > text.length || !Object.hasOwn(stems,s.category) || !boundary(text,s.start) || !boundary(text,s.end)) throw new Error('Invalid or overlapping review spans.');
    end = s.end;
  }
}
export function rebuild(text: string, spans: Span[], mode: Mode, revision = 0): Correction {
  validateSpans(text,spans);
  if (!['redact','placeholder','synthetic'].includes(mode) || !Number.isInteger(revision) || revision < 0) throw new Error('Invalid review document.');
  const names = [...new Set(spans.filter(s=>s.category==='private_person').map(s=>normalize(text.slice(s.start,s.end))))];
  const canonical = (s: Span) => {
    const value = normalize(text.slice(s.start,s.end));
    if (s.category !== 'private_person') return value;
    const words = value.split(' ');
    const longer = names.filter(name => name!==value && name.split(' ').length>words.length && words.every(word=>name.split(' ').includes(word)));
    return longer.length === 1 ? longer[0] : value;
  };
  const numbers = new Map<string,Map<string,number>>();
  const details = spans.map(s => {
    let values = numbers.get(s.category); if (!values) { values=new Map(); numbers.set(s.category,values); }
    const value=canonical(s); if (!values.has(value)) values.set(value,values.size+1);
    const n=values.get(value)!, label=`[${stems[s.category]} ${n}]`;
    const examples: Record<string,string> = {private_person:`Alex Example ${n}`,private_address:`${n} Example Street, Sampletown`,private_email:`person${n}@example.invalid`,private_phone:`+1 202 555 ${String(100+n%100).padStart(4,'0')}`,private_date:`2000-01-${String(1+n%28).padStart(2,'0')}`,private_url:`https://example.invalid/reference/${n}`,account_number:`DEMO-${n}`,secret:`SYNTHETIC-${n}`,manual:label};
    const replacement=mode==='redact'?(s.category==='private_date'?'**/**/**':'******'):mode==='placeholder'?label:examples[s.category];
    return {...s,label,replacement};
  });
  let cursor=0, redacted='';
  for (const d of details) { redacted+=text.slice(cursor,d.start)+d.replacement;cursor=d.end; }
  redacted+=text.slice(cursor);
  return {v:1,text,mode,details,redacted,detailCount:[...numbers.values()].reduce((n,m)=>n+m.size,0),revision};
}
export function parseCorrection(value: unknown): Correction {
  const v=value as Correction;
  if (!v || v.v!==1) throw new Error('Unsupported review document.');
  const expected=rebuild(v.text,v.details,v.mode,v.revision);
  let redacted='',cursor=0;
  for(const d of v.details) {
    if(typeof d.replacement!=='string'||!d.replacement.length||d.replacement.length>128)throw new Error('Invalid replacement.');
    redacted+=v.text.slice(cursor,d.start)+d.replacement;cursor=d.end;
  }
  redacted+=v.text.slice(cursor);
  if (v.redacted!==redacted || v.detailCount!==expected.detailCount || v.details.some((d,i)=>d.label!==expected.details[i].label)) throw new Error('Review contents do not match their spans.');
  return {...expected,redacted,details:expected.details.map((d,i)=>({...d,replacement:v.details[i].replacement}))};
}
export function hideRange(value: Correction, start: number, end: number) {
  while(start<end && /\s/u.test(value.text[start])) start++;
  while(end>start && /\s/u.test(value.text[end-1])) end--;
  if(start===end) return value;
  const spans=value.details.filter(d=>d.end<=start || d.start>=end);
  spans.push({start,end,category:'manual',label:'',replacement:''});
  return rebuild(value.text,spans.sort((a,b)=>a.start-b.start),value.mode,value.revision);
}
export function fromManifest(m: any, output?: string): Correction {
  if (m?.correction) return parseCorrection(m.correction);
  if (!m || !Array.isArray(m.detections)) throw new Error('Invalid detection manifest.');
  let text=m.text;
  if (typeof text!=='string') {
    if(typeof output!=='string') throw new Error('Original text is unavailable for this file.');
    // Older manifests have code-point offsets and originals, but no full text.
    const out=Array.from(output); let sourceCursor=0, outputCursor=0; text='';
    for(const d of m.detections) {
      if(d.span?.unit!=='unicode_code_points' || !Number.isInteger(d.span.start) || d.span.start<sourceCursor || typeof d.original!=='string' || typeof d.replacement!=='string' || Array.from(d.original).length!==d.span.end-d.span.start) throw new Error('Invalid legacy spans.');
      const gap=d.span.start-sourceCursor; text+=out.slice(outputCursor,outputCursor+gap).join('');outputCursor+=gap;
      const width=Array.from(d.replacement).length;
      if(out.slice(outputCursor,outputCursor+width).join('')!==d.replacement) throw new Error('Legacy output does not match its detections.');
      text+=d.original; outputCursor+=width;sourceCursor=d.span.end;
    }
    text+=out.slice(outputCursor).join('');
  }
  const offsets=[0]; for(const c of text as string) offsets.push(offsets[offsets.length-1]+c.length);
  const spans=m.detections.map((d:any)=>{
    if(d.span?.unit!=='unicode_code_points' || typeof d.original!=='string') throw new Error('Unsupported span offsets.');
    const span={start:offsets[d.span.start],end:offsets[d.span.end],category:d.category};
    if(text.slice(span.start,span.end)!==d.original) throw new Error('Detection does not match original text.');
    return span;
  });
  const value=rebuild(text,spans,m.mode);
  value.details=value.details.map((d,i)=>({...d,replacement:m.detections[i].replacement}));
  let cursor=0;value.redacted='';
  for(const d of value.details){value.redacted+=text.slice(cursor,d.start)+d.replacement;cursor=d.end;}
  value.redacted+=text.slice(cursor);
  return parseCorrection(value);
}
export function correctionReview(value: Correction, report: import('./types').ScanReport): import('./types').DetectionReview {
  const counts:Record<string,number>={};
  const detections=value.details.map(d=>({category:d.category,occurrence:counts[d.category]=(counts[d.category]||0)+1,replacement:d.replacement}));
  return {detections,scan_report:{...report,counts,total_detections:detections.length,layout_preserved:false,warnings:['Corrected downloads use a clean text layout.']}};
}
