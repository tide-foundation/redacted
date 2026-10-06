import { TideCloak } from '@tidecloak/js';
import { api } from './api';
import type { TideConfig } from './tideHistory';

let client: Promise<TideCloak> | undefined;
async function setupClient() {
  if (!client) client = (async () => {
    const config = await api<TideConfig>('tide/setup/v2/adapter');
    if (config.app_origin !== window.location.origin) throw new Error('Open Redacted at its configured address.');
    const a = config.adapter;
    const tc = new TideCloak({ url: a['auth-server-url'], realm: a.realm, clientId: a.resource,
      vendorId: a.vendorId, homeOrkUrl: a.homeOrkUrl, backgroundUrl: a.backgroundUrl, logoUrl: a.logoUrl,
      clientOriginAuth: a['client-origin-auth-' + window.location.origin] });
    await tc.init({ onLoad: 'check-sso', pkceMethod: 'S256', checkLoginIframe: false,
      redirectUri: config.app_origin + '/secure-history/setup',
      silentCheckSsoRedirectUri: config.app_origin + '/silent-check-sso.html', silentCheckSsoFallback: false,
      useDPoP: { mode: 'strict', alg: 'ES256' } });
    return tc;
  })().catch(error => { client = undefined; throw error; });
  return client;
}
export async function resumeSetupLogin() { await setupClient(); }
export async function approveSetupRequests(ids: string[], post: <T>(path: string, body?: unknown) => Promise<T>) {
  const tc = await setupClient();
  if (!tc.authenticated) { await tc.login({ redirectUri: window.location.origin + '/secure-history/setup' }); return false; }
  const requests: { id: string; request: Uint8Array }[] = [];
  for (const id of ids) {
    const response = await post<{ mode?: string; requestModel?: string }>('approvals/' + encodeURIComponent(id));
    if (response.mode === 'recorded') continue;
    if (!response.requestModel) throw new Error('TideCloak did not supply an approval challenge. Retry this step.');
    requests.push({ id, request: Uint8Array.from(atob(response.requestModel), c => c.charCodeAt(0)) });
  }
  if (!requests.length) return true;
  const results = await tc.requestTideOperatorApproval(requests);
  let approved = 0;
  for (const result of results) {
    if (result.status !== 'approved' || !requests.some(r => r.id === result.id)) continue;
    let binary = ''; for (const byte of result.request) binary += String.fromCharCode(byte);
    await post('approvals/' + encodeURIComponent(result.id), { request_model: btoa(binary) }); approved++;
  }
  if (approved !== requests.length) throw new Error('Approval was not completed. Your progress is saved; you can try again.');
  return true;
}
