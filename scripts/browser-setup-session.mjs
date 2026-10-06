// Fixture-only SDK boundary checks; no live Tide sign-in is simulated as proof.
import { chromium, expect } from '@playwright/test';
import { createServer as createViteServer } from 'vite';
import react from '@vitejs/plugin-react';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
const entry = resolve('__setup_session_fixture__.tsx'), sdk = '\0setup-session-sdk';
const mock = `export class TideCloak {
 constructor() { window.sdk = this; window.loginCalls = []; }
 async init(options) { window.initOptions = options; this.authenticated = new URL(location.href).searchParams.get('callback') === 'valid'; this.token = this.authenticated ? 'fixture-token' : undefined; return this.authenticated; }
 async login(options) { window.loginCalls.push(options); }
 async updateToken() {}
 async secureFetch(url, options) { return fetch(url, options); }
 clearToken() { this.authenticated = false; this.token = undefined; this.onAuthLogout?.(); }
}`;
const fixture = `
import { createRoot } from 'react-dom/client';
import { TideSetupPage } from '/src/TideSetupPage.tsx';
import { useState } from 'react';
import { TideLinkComplete } from '/src/TideLinkComplete.tsx';
import { useIdentity } from '/src/history.tsx';
import { createTideHistoryProvider } from '/src/tideHistory.ts';
import { routeLinkReturn, markLinkWindow, setupSignInState } from '/src/setupNavigation.ts';
window.markLinkWindow = markLinkWindow;
routeLinkReturn();
if (location.pathname === '/secure-history/linked') createRoot(document.getElementById('root')).render(<TideLinkComplete/>);
else {
 const provider = createTideHistoryProvider({app_origin:location.origin,issuer:'fixture',client_id:'redacted',adapter:{}});
 window.provider = provider;
 function View() { const identity = useIdentity(provider); const [path, navigate] = useState('/secure-history/setup'); return path === '/' ? <h1>Choose a file</h1> : <TideSetupPage identity={identity} provider={provider} navigate={navigate}/>; }
 createRoot(document.getElementById('root')).render(<View/>);
 window.ready = provider.initialise({completeSetup:!!setupSignInState()});
}
`;
const vite = await createViteServer({configFile:false, appType:'custom', cacheDir:'node_modules/.vite-setup-session', optimizeDeps:{exclude:['@tidecloak/js']}, plugins:[react(), {
 name:'setup-session', enforce:'pre', resolveId:id=>id==='@tidecloak/js'?sdk:id==='/__setup_session_fixture__.tsx'?entry:undefined,
 load:id=>id===sdk?mock:id===entry?fixture:undefined,
}], server:{middlewareMode:true,hmr:false}});
const server=createServer(async(req,res)=>{
 if (['/','/secure-history/setup','/secure-history/linked'].includes(new URL(req.url,'http://local').pathname)) {
  const html=await vite.transformIndexHtml(req.url,'<html><body><div id="root"></div><script type="module" src="/__setup_session_fixture__.tsx"></script></body></html>');
  res.writeHead(200,{'Content-Type':'text/html'}); res.end(html);
 } else vite.middlewares(req,res);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true});
const context=await browser.newContext(), page=await context.newPage();
let rejectIdentity=false, identities=0;
await context.route('**/api/service/**',route=>{
 const path=new URL(route.request().url()).pathname;
 if(path.endsWith('/identity')) { identities++; return route.fulfill({status:rejectIdentity?401:200,json:rejectIdentity?{detail:'Invalid proof'}:{owner_id:'verified-fixture-owner'}}); }
 if(path.endsWith('/guest/current')) return route.fulfill({json:{document:null}});
 if(path.endsWith('/status')) return route.fulfill({json:{configured:true,reachable:true,managed:true}});
 if(path.endsWith('/session')) return route.fulfill({json:{configured:true}});
 throw Error('Unexpected API '+path);
});
try {
 await page.goto(base); await page.evaluate(()=>window.ready);
 await page.evaluate(()=>sessionStorage.setItem('redacted.setup-login',JSON.stringify({time:Date.now(),attempted:false})));
 await page.reload(); await page.evaluate(()=>window.ready);
 assert.deepEqual(await page.evaluate(()=>window.loginCalls),[{prompt:'none',redirectUri:base+'/secure-history/setup'}]);
 const options=await page.evaluate(()=>window.initOptions);
 assert.equal(options.pkceMethod,'S256'); assert.deepEqual(options.useDPoP,{mode:'strict',alg:'ES256'});
 assert.equal(identities,0,'No success without an authenticated SDK and verified backend identity');
 await page.reload(); await page.evaluate(()=>window.ready);
 assert.deepEqual(await page.evaluate(()=>window.loginCalls),[],'No automatic redirect loop');
 await expect(page.getByRole('button',{name:'Sign in to start redacting',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Sign in to start redacting',exact:true}).click();
 assert.deepEqual(await page.evaluate(()=>window.loginCalls),[{redirectUri:base+'/'}]);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('redacted.setup-login')),null);
 await page.goto(base+'/?callback=valid'); await page.evaluate(()=>window.ready);
 await expect(page.getByRole('heading',{name:'Choose a file'})).toBeVisible();
 await expect(page.getByRole('heading',{name:'Setup complete'})).toHaveCount(0);
 assert.equal(identities,1);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('redacted.setup-login')),null);
 assert.equal(await page.evaluate(()=>window.provider.getSnapshot().status),'authenticated');
 await page.evaluate(()=>sessionStorage.setItem('redacted.setup-login',JSON.stringify({time:Date.now(),attempted:true})));
 await page.goto(base+'/?callback=valid'); await page.evaluate(()=>window.ready);
 await expect(page.getByRole('heading',{name:'Choose a file'})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.provider.getSnapshot().status),'authenticated');
 rejectIdentity=true;
 await page.goto(base+'/?callback=valid'); await page.evaluate(()=>window.ready);
 await expect(page.getByRole('heading',{name:'Choose a file'})).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'Setup complete'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Sign in to start redacting'})).toBeVisible();
 // Tag only the linking popup, then simulate Tide returning it to client home.
 const opened=page.waitForEvent('popup');
 await page.evaluate(()=>{const popup=window.open('about:blank','fixture-link'); window.markLinkWindow(popup); popup.location.href=location.origin+'/';});
 const popup=await opened;
 await expect(popup.getByRole('heading',{name:'You can close this page now'})).toBeVisible();
 assert.equal(new URL(popup.url()).pathname,'/secure-history/linked');
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('redacted.setup-link-return')),null);
 await popup.close();
 // An unmarked tab returning to the homepage must stay on the homepage.
 await page.goto(base); assert.equal(new URL(page.url()).pathname,'/');
 console.log('Setup session: popup home-return recovery, one SSO attempt, PKCE/DPoP options, identity verification, sign-in fallback and authenticated start passed.');
} finally {await browser.close(); await new Promise(resolve=>server.close(resolve)); await vite.close();}
