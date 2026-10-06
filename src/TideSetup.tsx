import { useEffect, useRef, useState } from 'react';
import { Check, ArrowRight, LoaderCircle } from 'lucide-react';
import { api } from './api';
import { beginSetupSignIn, markLinkWindow, setupSignInState } from './setupNavigation';
import { startVisiblePolling } from './polling';
import type { IdentityState, SecureHistoryProvider } from './history';

type Installation = { state: string; configured: boolean; reachable: boolean; url: string; managed?: boolean };
type Progress = { stage: string; completed: string[]; realm?: string; email?: string; allow_registration?: boolean;
  error?: string; message?: string; pending?: { id: string; actionType: string; entityType: string; blocked?: boolean }[] };
type Session = { unlocked: boolean; configured?: boolean; saved?: boolean; csrf?: string; busy?: boolean; progress?: Progress };
const steps = [ ['details', 'Your installation'], ['realm', 'Configure TideCloak'], ['license', 'Activate Tide'],
  ['link', 'Connect your Tide account'] ];
const stageCopy: Record<string, string> = {
  realm: 'Configuring TideCloak for this Redacted installation.', license: 'Activating the licence and Tide protection.',
  configure: 'Preparing login, personal encryption permissions and Redacted branding.',
  link: 'Create or connect a Tide account in the next window for self-sovereign control of your app. Setup will continue automatically.',
  admin: 'Confirming your account as this installation’s administrator.', verify: 'Checking the saved configuration.',
};
function Command({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);
  return <div className="setup-command"><code>{children}</code><button type="button" className="text-button" onClick={() => {
    void navigator.clipboard.writeText(children).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => {});
  }}>{copied ? 'Copied' : 'Copy'}</button></div>;
}
export function TideSetup({ identity, provider, onConfigured }: { identity: IdentityState; provider: SecureHistoryProvider; onConfigured?: (configured: boolean) => void }) {
  const [installation, setInstallation] = useState<Installation | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState('');
  const [registration, setRegistration] = useState(true);
  const [terms, setTerms] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const feedback = useRef<HTMLDivElement>(null);
  const current = useRef(session); current.current = session;
  const actionBusy = useRef(false);
  const automaticCheck = useRef<Promise<unknown> | null>(null);
  const reloadOnReady = useRef(false);
  const progress = session?.progress;
  const visibleStage = progress?.stage === 'configure' ? 'license' : ['admin', 'verify'].includes(progress?.stage || '') ? 'link' : progress?.stage || 'details';
  const activePanel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (progress?.stage && progress.stage !== 'details') activePanel.current?.scrollIntoView({ block: 'nearest' });
  }, [visibleStage]);
  async function post<T>(path: string, body: unknown = {}) {
    return api<T>('tide/setup/v2/' + path, { method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-Setup-CSRF': current.current?.csrf || '' }, body: JSON.stringify(body) });
  }
  async function refresh() {
    const next = await api<Session>('tide/setup/v2/session');
    setSession(next);
    if (next.configured && reloadOnReady.current) beginSetupSignIn();
    if (next.unlocked) reloadOnReady.current = true;
    return next;
  }
  useEffect(() => {
    let stop = () => {}; let live = true;
    // A setup link opened in this same tab changes only the fragment; React
    // does not remount. Reload to exchange it and read saved server progress.
    const followSetupLink = () => {
      if (new URLSearchParams(window.location.hash.slice(1)).has('setup')) window.location.reload();
    };
    window.addEventListener('hashchange', followSetupLink);
    const init = async () => {
      const fragment = new URLSearchParams(window.location.hash.slice(1));
      const token = fragment.get('setup');
      if (token) {
        // Consume the browser handoff from the URL fragment, then erase it before
        // any network navigation. It never enters HTTP access logs or storage.
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
        try { await api('tide/setup/v2/exchange', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) }); }
        catch (e) { if (live) setError((e as Error).message); }
      } else if (!setupSignInState() && (fragment.has('code') || fragment.has('error') || new URLSearchParams(window.location.search).has('code'))) {
        try { const { resumeSetupLogin } = await import('./tideSetupApproval'); await resumeSetupLogin(); }
        catch { if (live) setError('Tide sign-in did not finish. Try the approval button again.'); }
      }
      if (!live) return;
      let statusAt = 0;
      stop = startVisiblePolling(async () => {
        try {
          if (actionBusy.current) return;
          if (Date.now() - statusAt > 15000) { const status = await api<Installation>('tide/status'); if (live) setInstallation(status); statusAt = Date.now(); }
          if (!live) return;
          const next = await refresh();
          if ((['link', 'admin', 'verify'].includes(next.progress?.stage || '') || next.progress?.pending?.length) && !next.busy && !actionBusy.current && !next.progress?.error) {
            current.current = next;
            const check = post('continue');
            automaticCheck.current = check;
            try {
              await check;
              if (live) await refresh();
            } finally {
              if (automaticCheck.current === check) automaticCheck.current = null;
            }
          }
        } catch { if (live) setError('Could not reach Redacted. Your setup progress is saved; reconnect to continue.'); }
      }, () => current.current?.busy ? 2000 : current.current?.unlocked ? 5000 : 30000);
    };
    void init();
    return () => { live = false; stop(); window.removeEventListener('hashchange', followSetupLink); };
  }, []);
  useEffect(() => { if (error || message || progress?.error) feedback.current?.scrollIntoView({ block: 'nearest' }); }, [error, message, progress?.error]);
  async function act(run: () => Promise<void>) {
    if (actionBusy.current) return;
    actionBusy.current = true; setBusy(true); setError(''); setMessage('');
    try {
      // A click takes priority over the next poll, and waits for an already
      // running check so it cannot collide with the backend's setup lock.
      await automaticCheck.current?.catch(() => {});
      await run(); await refresh();
    }
    catch (e) { setError((e as Error).message); }
    finally { actionBusy.current = false; setBusy(false); }
  }
  const configured = installation?.configured || session?.configured;
  useEffect(() => { onConfigured?.(!!configured); }, [configured, onConfigured]);
  const working = busy || session?.busy;
  const finalising = ['admin', 'verify'].includes(progress?.stage || '');
  const needsAction = !working && (!!error || !!progress?.error || !!progress?.pending?.length || (!!progress && !finalising));
  if (configured && identity.status === 'authenticated') return <p role="status">Opening Redacted…</p>;
  return <section className="tide-setup" aria-label="Set up secure history">
    {configured ? <>
      <h2>Setup complete</h2>
      <p role="status">{identity.status === 'loading' ? 'Checking your secure session…' : installation?.reachable ? 'Sign in to start redacting with encrypted history.' : 'TideCloak is stopped or unreachable. Your encrypted history is retained.'}</p>
      {!installation?.reachable && (installation?.managed ? <Command>bash scripts/tidecloak.sh start</Command> :
        <p className="setup-note">Start the TideCloak server connected to this installation, then reconnect here.</p>)}
      {identity.status === 'unavailable' && <button className="primary" onClick={() => window.location.reload()}>Continue to sign in <ArrowRight/></button>}
      {identity.status === 'signed-out' && <button className="primary" onClick={() => void act(() => provider.signIn())}>Sign in to start redacting <ArrowRight/></button>}
      {identity.status === 'error' && <button className="primary" onClick={() => void act(() => provider.signIn())}>Sign in to start redacting <ArrowRight/></button>}

    </> : !session ? <div className="setup-loading" role="status"><span className="decrypt-skeleton"/> Checking setup…</div> : !session.unlocked ? <>
      <h2>{session.saved ? 'Resume your setup' : 'Start here'}</h2>
      <p>{session.saved ? 'Your progress is saved, but this browser’s setup access has expired or is unavailable. Run this from your Redacted project folder to restore access.' : 'Run this from your Redacted project folder. It starts or connects to TideCloak and opens this guide with setup unlocked.'}</p>
      <Command>bash scripts/tidecloak.sh start</Command>
      <p className="setup-note">Returning to an unfinished setup? The same command restores access to your saved progress.</p>
    </> : <div className="setup-layout">
      <ol className="setup-checklist" aria-label="Setup progress">{steps.map(([id, title]) => {
        const done = progress?.completed.includes(id) && !(id === 'link' && finalising) && (id !== 'license' || progress?.completed.includes('configure'));
        return <li key={id} aria-current={visibleStage === id ? 'step' : undefined} className={done ? 'done' : ''}>
          <span className="setup-step-marker">{done ? <Check size={14}/> : steps.findIndex(s => s[0] === id) + 1}</span><span className="setup-step-title">{title}</span>
        </li>;
      })}</ol>
      <div className="setup-active-panel" ref={activePanel}>
      {(!progress || progress.stage === 'details') ? <form className="setup-form" onSubmit={event => {
        event.preventDefault(); void act(async () => { await post('begin', { email, allow_registration: registration, accept_terms: terms }); });
      }}>
        <h2>Your installation</h2>
        <div className="setup-email-label"><label htmlFor="licensing-email">Email for Tide licensing</label>
          <span className="setup-help"><button type="button" aria-label="About the licensing email" aria-describedby="licensing-email-help">?</button>
            <span role="tooltip" id="licensing-email-help">Sent to Tide to activate this installation’s licence and used for its setup administrator account. It is not used to save your guest documents.</span>
          </span>
        </div>
        <input id="licensing-email" type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} required maxLength={254}/>
        <label className="setup-check"><input type="checkbox" checked={registration} onChange={e => setRegistration(e.target.checked)}/> Allow others to sign up for their own encrypted history</label>
        <label className="setup-check"><input type="checkbox" checked={terms} onChange={e => setTerms(e.target.checked)} required/><span>I accept <a href="https://tide.org/legal" target="_blank" rel="noopener noreferrer">Tide’s Terms and Conditions</a> and have read the <a href="https://tide.org/privacy" target="_blank" rel="noopener noreferrer">Privacy Policy</a>.</span></label>
        <button className="primary" disabled={working || !terms}>{working ? 'Preparing…' : 'Enable secure history'} <ArrowRight/></button>
      </form> : <div className="setup-current">
        <h2>{finalising ? 'Completing setup…' : steps.find(s => s[0] === visibleStage)?.[1] || 'Secure history'}</h2>
        <div className={needsAction ? 'setup-action-message' : 'setup-progress-message'} role="status">
          <div className="setup-status-label">{needsAction ? <ArrowRight size={18} aria-hidden="true"/> : <LoaderCircle className="spin" size={18} aria-hidden="true"/>}<strong>{needsAction ? 'Your turn' : 'In progress'}</strong></div>
          <p>{error || progress.error ? 'Review the message below, then retry this step.' : progress.pending?.length ? 'Review and approve the change below to continue.' : finalising ? 'Preparing your secure session. This page will continue automatically.' : working && progress.stage === 'link' ? 'Opening your Tide account connection…' : needsAction && progress.stage !== 'link' ? 'Select Continue setup to resume this step.' : stageCopy[progress.stage]}</p>
        </div>
        {progress.stage === 'link' && <button className="primary" disabled={working} onClick={() => {
          const width = window.screen.availWidth, height = window.screen.availHeight;
          const popup = window.open('about:blank', 'redacted-tide-link', `popup,width=${width},height=${height},left=0,top=0,resizable=yes,scrollbars=yes`);
          if (!popup) { setError('Allow pop-ups for Redacted, then try again.'); return; }
          try { popup.moveTo(0, 0); popup.resizeTo(width, height); popup.focus(); } catch { /* Window sizing is controlled by the browser. */ }
          try { markLinkWindow(popup); } catch { /* Use the registered callback if storage is unavailable. */ }
          void act(async () => { try { const result = await post<{ url: string }>('link'); popup.location.href = result.url; } catch (e) { popup.close(); throw e; } });
        }}>Create or connect account <ArrowRight/></button>}
        {!!progress.pending?.length && <>
          <p>{progress.pending.length} change{progress.pending.length === 1 ? ' needs' : 's need'} your approval.</p>
          <ul>{progress.pending.map(p => <li key={p.id}>{p.actionType.replaceAll('_', ' ').toLowerCase()} ({p.entityType.toLowerCase()}){p.blocked ? ' — waiting on an earlier change' : ''}</li>)}</ul>
          <button className="primary" disabled={working} onClick={() => void act(async () => {
            const { approveSetupRequests } = await import('./tideSetupApproval');
            if (await approveSetupRequests(progress.pending!.map(p => p.id), post)) await post('continue');
          })}>Review and approve with Tide <ArrowRight/></button>
        </>}
        {!working && (progress.error || (!finalising && progress.stage !== 'link' && !progress.pending?.length)) && <button className="settings-button" onClick={() => void act(async () => { await post('continue'); })}>{progress.error ? 'Retry this step' : 'Continue setup'}</button>}
        <p className="setup-note">Progress is saved. Return in this browser within seven days to continue where you left off.</p>
      </div>}
      </div>
    </div>}
    <div ref={feedback} aria-live="polite">
      {(error || progress?.error) && <p role="alert" className="notice">{error || progress?.error}</p>}
      {message && <p role="status" className="notice">{message}</p>}
    </div>
  </section>;
}
