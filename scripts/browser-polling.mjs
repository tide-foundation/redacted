// Exercise the actual app's request cadence with virtual time, including tab
// visibility and unavailable/slow service responses. No document/model work.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { setTimeout as settle } from 'node:timers/promises';
const browser = await chromium.launch({headless:true});
const page = await browser.newPage();
let health = 0, current = 0, document = null, failHealth = false, holdHealth = false, releaseHealth;
let failCurrent = false, csrf = 'fixture';
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/service/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if (path.endsWith('/capabilities')) return route.fulfill({json:{secure_history:{available:false}}});
  if (path.endsWith('/health')) {
    health++;
    if (holdHealth) await new Promise(resolve=>{releaseHealth=resolve;});
    return route.fulfill({status:failHealth?503:200,json:failHealth?{detail:'Unavailable'}:{model_installed:true,model_loaded:true,device:'cpu'}});
  }
  if (path.endsWith('/guest/current')) {current++;return route.fulfill({status:failCurrent?503:200,json:failCurrent?{detail:'Unavailable'}:{document,csrf_token:csrf,expires_at:null}});}
  throw new Error('Unexpected polling fixture path: '+path);
});
const advance = async ms => {await page.clock.runFor(ms);await settle(100);};
const event = async name => {await page.evaluate(name=>window.dispatchEvent(new Event(name)),name);await settle(100);};
const visibility = async value => {
  await page.evaluate(value=>{Object.defineProperty(document,'visibilityState',{configurable:true,value});document.dispatchEvent(new Event('visibilitychange'));},value);
  await settle(100);
};
try {
  await page.clock.install();
  await page.goto(process.env.BASE_URL || 'http://127.0.0.1:4173');
  await page.getByText('No files yet.',{exact:true}).waitFor();
  await page.clock.pauseAt(await page.evaluate(()=>Date.now()+1000));
  await settle(100);
  const initial={health,current};
  await advance(10000);assert.deepEqual({health,current},initial);
  await advance(21000);assert.equal(health,initial.health);assert.equal(current,initial.current+1);
  await advance(31000);assert.equal(health,initial.health+1);assert.equal(current,initial.current+2);
  await visibility('hidden');const hidden={health,current};
  await advance(300000);assert.deepEqual({health,current},hidden);
  await visibility('visible');assert.deepEqual({health,current},{health:hidden.health+1,current:hidden.current+1});

  document={id:'11111111-1111-4111-8111-111111111111',filename:'Polling fixture.pdf',created:new Date().toISOString(),status:'processing',mode:'redact',sensitivity:50,counts:{}};
  await event('focus');
  await page.locator('.document-name.is-processing').waitFor();
  const processing={health,current};
  await advance(2100);assert.equal(current,processing.current+1);assert.equal(health,processing.health);
  await advance(2100);assert.equal(current,processing.current+2);assert.equal(health,processing.health);
  document={...document,status:'complete',source_type:'pdf'};
  await advance(2100);
  await page.getByRole('button',{name:'Review detections',exact:true}).waitFor();
  const completed=current;await advance(10000);assert.equal(current,completed);

  failHealth=true;await event('online');const failed=health;
  await advance(14000);assert.equal(health,failed);
  await advance(2000);assert.equal(health,failed+1);
  await advance(29000);assert.equal(health,failed+1);
  await advance(2000);assert.equal(health,failed+2);
  await advance(58000);assert.equal(health,failed+2);
  await advance(3000);assert.equal(health,failed+3);

  // No overlap/backlog when a health request takes longer than its interval.
  failHealth=false;holdHealth=true;await event('online');const slow=health;
  await event('focus');await advance(120000);assert.equal(health,slow);
  holdHealth=false;releaseHealth();await settle(100);
  assert.equal(await page.getByText('Service unavailable.',{exact:true}).count(),0);
  // A server restart loses temporary guest state. Recover with a new CSRF token
  // and an actionable notice, rather than leaving an error or stale result.
  failCurrent=true;await event('online');
  await page.getByRole('button',{name:'Retry now',exact:true}).waitFor();
  failCurrent=false;document=null;csrf='replacement-session';
  await page.getByRole('button',{name:'Retry now',exact:true}).click();
  await page.getByText('Your temporary session ended or the service restarted. Add your file again to continue.',{exact:true}).waitFor();
  await page.getByText('No files yet.',{exact:true}).waitFor();
  await page.locator('input[type=file]').setInputFiles({name:'Retry.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-fixture')});
  assert.equal(await page.getByRole('button',{name:'REDACT',exact:true}).isEnabled(),true);
  assert.deepEqual(errors,[]);
  console.log('Polling passed: health 60s, idle job 30s, processing job 2s, hidden-tab pause, immediate return/online refresh, 15/30/60s error backoff and no overlapping health requests.');
} finally {await browser.close();}
