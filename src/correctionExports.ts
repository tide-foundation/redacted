import type { OutputFormat } from './types';
export async function exportCorrection(text: string, signal: AbortSignal): Promise<Record<OutputFormat,Blob>> {
  const [{Document,Paragraph,Packer}, {PDFDocument}, {default:fontkit}] = await Promise.all([import('docx'),import('pdf-lib'),import('@pdf-lib/fontkit')]);
  signal.throwIfAborted();
  const pdf=await PDFDocument.create(); pdf.registerFontkit(fontkit);
  const response=await fetch('/fonts/Cousine-Regular.ttf',{signal});
  if(!response.ok) throw new Error('Could not load the export font. Try again.');
  const font=await pdf.embedFont(await response.arrayBuffer(),{subset:true});
  const supported=new Set(font.getCharacterSet());
  if([...text].some(c=>!/[\n\r\t]/.test(c) && !supported.has(c.codePointAt(0)!))) throw new Error('This text contains characters the PDF export font cannot represent. Changes have not been saved.');
  let page=pdf.addPage([595,842]), y=797;
  const line=(value:string)=>{ if(y<45) {if(pdf.getPageCount()>=1000) throw new Error('The corrected document is too long.');page=pdf.addPage([595,842]);y=797;} page.drawText(value,{x:45,y,size:11,font});y-=15; };
  for(const paragraph of text.replace(/\r\n?/g,'\n').replace(/\t/g,'    ').split('\n')) {
    let chunk='';
    for(const char of paragraph) {if(font.widthOfTextAtSize(chunk+char,11)>505) {line(chunk);chunk='';} chunk+=char;}
    line(chunk); signal.throwIfAborted();
  }
  const doc=new Document({creator:'',lastModifiedBy:'',title:'',description:'',sections:[{children:text.split('\n').map(text=>new Paragraph({text}))}]});
  const [docx,bytes]=await Promise.all([Packer.toBlob(doc),pdf.save()]);signal.throwIfAborted();
  return {txt:new Blob([text],{type:'text/plain;charset=utf-8'}),pdf:new Blob([new Uint8Array(bytes)],{type:'application/pdf'}),docx};
}
export async function toBase64(blob:Blob) {
  const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';
  for(let i=0;i<bytes.length;i+=8192) binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return btoa(binary);
}
export function fromBase64(value:string, type:string) {
  if(typeof value!=='string' || value.length>90_000_000) throw new Error('Invalid corrected output.');
  return new Blob([Uint8Array.from(atob(value),c=>c.charCodeAt(0))],{type});
}
