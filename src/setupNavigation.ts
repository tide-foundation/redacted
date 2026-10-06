// Non-secret, tab-local flow markers. Authentication stays in the Tide SDK.
const returnKey = 'redacted.setup-link-return';
const loginKey = 'redacted.setup-login';
const lifetime = 60 * 60 * 1000;
export function markLinkWindow(popup: Window) {
  popup.sessionStorage.setItem(returnKey, String(Date.now()));
}
export function routeLinkReturn() {
  try {
    const value = sessionStorage.getItem(returnKey);
    if (!value) return;
    sessionStorage.removeItem(returnKey);
    const age = Date.now() - Number(value);
    if (age >= 0 && age < lifetime && ['/', '/secure-history/setup', '/secure-history/linked'].includes(location.pathname)) {
      // Some Tide linking flows return to the client home URL. This marker only
      // selects a page: never interpret a linking return as an authenticated user.
      history.replaceState(null, '', '/secure-history/linked');
    }
  } catch { /* The registered callback still works when storage is unavailable. */ }
}
export function beginSetupSignIn() {
  try { sessionStorage.setItem(loginKey, JSON.stringify({ time: Date.now(), attempted: false })); } catch { /* Manual sign-in remains available. */ }
  window.location.assign('/secure-history/setup');
}
export function setupSignInState(): { time: number; attempted: boolean } | null {
  try {
    const state = JSON.parse(sessionStorage.getItem(loginKey) || 'null');
    const age = Date.now() - state?.time;
    if (state && age >= 0 && age < lifetime && typeof state.attempted === 'boolean') return state;
    sessionStorage.removeItem(loginKey);
  } catch { /* No automatic sign-in without a valid local flow marker. */ }
  return null;
}
export function markSetupSignInAttempt() {
  const state = setupSignInState();
  if (!state || state.attempted) return false;
  try { sessionStorage.setItem(loginKey, JSON.stringify({ ...state, attempted: true })); return true; } catch { return false; }
}
export function clearSetupSignIn() { try { sessionStorage.removeItem(loginKey); } catch { /* No secret data is stored here. */ } }
