import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1350,height:1000}});
const id='44444444-4444-4444-8444-444444444444';
const text='Alice and Bob. Alice met James Okafor. James left.'+'\n\nExample document paragraph for reviewing the full page.'.repeat(30);
const spans=[{start:0,end:5,category:'private_person'},{start:10,end:13,category:'private_person'},{start:15,end:20,category:'private_person'},{start:25,end:37,category:'private_person'},{start:39,end:44,category:'private_person'}];
// Derive exact fixture positions instead of hiding a hand-written offset error.
spans[3]={...spans[3],start:text.indexOf('James Okafor'),end:text.indexOf('James Okafor')+12};spans[4]={...spans[4],start:text.lastIndexOf('James'),end:text.lastIndexOf('James')+5};
const report={sensitivity:50,counts:{private_person:5},total_detections:5,source_type:'docx',layout_preserved:true,ocr_performed:false,warnings:[],limitations:[]};
let saved=null,saves=0,queued=false;const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/service/**',async route=>{
 const req=route.request(),path=new URL(req.url()).pathname;
 let json;
 if(path.endsWith('/capabilities'))json={secure_history:{available:false}};
 else if(path.endsWith('/health'))json={model_installed:true,model_loaded:true,device:'cpu'};
 else if(path.endsWith('/guest/current'))json={csrf_token:'fixture',expires_at:null,document:{id,filename:'Example.docx',created:new Date().toISOString(),status:queued?'queued':'complete',mode:'placeholder',counts:{private_person:5},sensitivity:50,source_type:'docx'}};
 else if(path.endsWith('/guest/documents')&&req.method()==='POST'){queued=true;json={id};}
 else if(path.endsWith('/review'))json={detections:spans.map((s,i)=>({category:s.category,occurrence:i+1,replacement:'[Name]'})),scan_report:report};
 else if(path.endsWith('/correction')) {
  if(req.method()==='PUT'){assert.equal(req.headers()['x-csrf-token'],'fixture');saved={...req.postDataJSON(),revision:(saved?.revision||0)+1};saves++;json=saved;}
  else json={text,mode:'placeholder',scan_report:report,detections:spans.map((s,i)=>({category:s.category,occurrence:i+1,original:text.slice(s.start,s.end),replacement:'[Name]',span:{start:s.start,end:s.end,unit:'unicode_code_points'}})),...(saved?{correction:saved}:{})};
 } else throw new Error('Unexpected request '+path);
 return route.fulfill({json});
});
try {
 await page.goto(process.env.BASE_URL||'http://127.0.0.1:4173');
 const checks=await page.evaluate(async()=>{
  const {rebuild,hideRange,parseCorrection,fromManifest}=await import('/src/corrections.ts');
  const t='😀 Alice Bob Alice',v=rebuild(t,[{start:3,end:8,category:'private_person'},{start:9,end:12,category:'private_person'},{start:13,end:18,category:'private_person'}],'placeholder');
  const overlap=hideRange(v,5,11),unhidden=rebuild(t,v.details.filter(d=>d.start!==3&&d.start!==13),'placeholder');
  let invalid=0;for(const details of [[{start:1,end:2,category:'manual'}],[{start:3,end:8,category:'manual'},{start:7,end:9,category:'manual'}]])try{rebuild(t,details,'redact');}catch{invalid++;}
  const roundtrip=fromManifest({mode:'placeholder',detections:[{category:'private_person',original:'Alice',replacement:'[Name]',span:{start:2,end:7,unit:'unicode_code_points'}}]},'😀 [Name] Bob');
  return {labels:v.details.map(d=>d.label),count:v.detailCount,overlap:overlap.details,renumber:unhidden.details[0].label,invalid,roundtrip:roundtrip.text,parsed:parseCorrection(v).redacted};
 });
 assert.deepEqual(checks.labels,['[NAME 1]','[NAME 2]','[NAME 1]']);assert.equal(checks.count,2);assert.equal(checks.overlap.length,2);assert.equal(checks.overlap[0].category,'manual');assert.equal(checks.renumber,'[NAME 1]');assert.equal(checks.invalid,2);assert.equal(checks.roundtrip,'😀 Alice Bob');
 await page.getByRole('button',{name:'Review detections',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>scrollY)).toBeGreaterThan(0);
 await expect(page.getByRole('heading',{name:'Detections',exact:true})).toBeInViewport();
 await expect(page.getByRole('button',{name:'Review and correct',exact:true}).locator('svg')).toBeVisible();await page.getByRole('button',{name:'Review and correct',exact:true}).click();
 await expect(page.getByLabel('Hidden details')).toContainText('3 hidden details');
 await expect(page.getByLabel('Original document text')).toHaveText(text);
 await expect(page.getByRole('button',{name:'Save changes',exact:true})).toHaveCount(0);

 const box=page.getByLabel('Original document text');
 for(const name of ['Original','Redacted','Highlighter'])await expect(page.getByRole('button',{name,exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Back to detections',exact:true}).locator('svg')).toBeVisible();
 await expect(page.getByRole('button',{name:'Close detection review',exact:true})).toBeVisible();
 for(const selector of ['.correction-text','.correction-summary'])assert.deepEqual(await page.locator(selector).evaluate(el=>({overflow:getComputedStyle(el).overflowY,max:getComputedStyle(el).maxHeight})),{overflow:'visible',max:'none'});
 await box.locator('[data-start="10"]').hover();
 assert.equal(await box.locator('[data-start="10"]').evaluate(el=>getComputedStyle(el,'::after').opacity),'1');
 await box.locator('[data-start="10"]').press('Enter');
 await expect(page.getByLabel('Hidden details')).toContainText('2 hidden details');assert.equal(saves,0);
 await page.getByRole('button',{name:'Discard changes',exact:true}).click();

 await expect(page.getByLabel('Original document text')).toHaveText(text);
 await expect(page.getByRole('button',{name:'Save changes',exact:true})).toHaveCount(0);

 await box.locator('[data-start="10"]').press('Enter');
 // A selection partly overlapping the first detection replaces that occurrence
 // and preserves the repeated Alice later in the text.
 await box.evaluate(el=>{const runs=[...el.querySelectorAll('[data-start]')];const point=n=>{const r=runs.find(r=>Number(r.dataset.start)<=n&&Number(r.dataset.start)+r.textContent.length>=n);return [r.firstChild,n-Number(r.dataset.start)];};const range=document.createRange();range.setStart(...point(2));range.setEnd(...point(9));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));});
 // A browser click following mouseup must not undo the freshly highlighted detail.
 await box.locator('[data-start="2"]').dispatchEvent('click');
 await expect(box.locator('.correction-added')).toHaveText('ice and');
 await expect(page.getByLabel('Hidden details')).toContainText('Added by you 1');
 await page.getByRole('button',{name:'Save changes',exact:true}).click();await expect(page.getByText('Changes saved. Downloads now include your corrections.')).toBeVisible();assert.equal(saves,1);
 await expect(page.getByRole('status').filter({hasText:'Changes saved.'})).toHaveClass('correction-status');
 assert.deepEqual(saved.details.filter(d=>d.category==='manual').map(d=>[d.start,d.end]),[[2,9]]);
 assert.equal(saved.details.filter(d=>d.category==='private_person'&&saved.text.slice(d.start,d.end)==='Alice').length,1);
 await page.reload();await page.getByRole('button',{name:'Review detections',exact:true}).click();await page.getByRole('button',{name:'Review and correct',exact:true}).click();await expect(page.getByLabel('Hidden details')).toContainText('Added by you 1');
 await expect(page.getByLabel('Original document text')).toHaveText(text);



 await page.screenshot({path:'/tmp/redacted-corrections.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.getByRole('button',{name:'Back to detections',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Detections',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Review and correct',exact:true}).click();
 await page.getByRole('button',{name:'Close detection review',exact:true}).click();
 await expect(page.getByRole('region',{name:'Review and correct',exact:true})).toHaveCount(0);
 await page.locator('input[type=file]').setInputFiles({name:'Test.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-fixture')});
 await page.getByRole('button',{name:'REDACT',exact:true}).click();
 await expect(page.locator('.document-name.is-processing')).toBeVisible();
 await expect.poll(()=>page.locator('.document-row').evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;})).toBe(true);
 assert.deepEqual(errors,[]);console.log('Corrections passed: UTF-16 validation, overlap absorption, label grouping/renumbering, keyboard unhide, explicit save, reload and mobile layout.');
} finally {await browser.close();}
