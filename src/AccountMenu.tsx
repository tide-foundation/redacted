import { useEffect, useRef, useState } from 'react';
import { UserRound } from 'lucide-react';
import type { IdentityState, SecureHistoryProvider } from './history';

export function AccountMenu({ identity, provider, available, onInformation }: {
  identity: IdentityState;
  provider: SecureHistoryProvider;
  available: boolean;
  onInformation: () => void;
}) {
  const configured = available && identity.status !== 'unavailable';
  const authenticated = identity.status === 'authenticated';
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const action = useRef<HTMLButtonElement>(null);
  const generation = useRef(0);
  const identityKey = authenticated ? identity.ownerKey : identity.status;
  useEffect(() => {
    generation.current += 1;
    setOpen(false); setBusy(false); setError('');
    return () => { generation.current += 1; };
  }, [identityKey, configured]);
  useEffect(() => {
    if (!open) return;
    action.current?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  async function authenticate() {
    const version = generation.current;
    setBusy(true); setError('');
    try {
      if (authenticated) await provider.signOut(); else await provider.signIn();
      if (version === generation.current) { setOpen(false); trigger.current?.focus(); }
    } catch {
      if (version === generation.current) setError(`Could not sign ${authenticated ? 'out' : 'in'}. Please try again.`);
    } finally { if (version === generation.current) setBusy(false); }
  }
  return <div className="account" ref={root}>
    <button ref={trigger} type="button" className={`account-button${authenticated ? ' signed-in' : ''}`} aria-label="Account" title="Account"
      aria-haspopup={configured ? 'menu' : undefined} aria-expanded={configured ? open : undefined}
      onClick={() => { setError(''); if (configured) setOpen(value => !value); else onInformation(); }}><UserRound size={19} strokeWidth={1.3}/></button>
    {open && configured && <div className="account-menu" role="menu" aria-label="Account options">
      {identity.status === 'signed-out' || authenticated ? <button ref={action} role="menuitem" disabled={busy} onClick={() => void authenticate()}>{busy ? (authenticated ? 'Signing out…' : 'Signing in…') : (authenticated ? 'Sign out' : 'Sign in')}</button> : <span role="status">{identity.status === 'loading' ? 'Connecting…' : 'Sign in is unavailable.'}</span>}
      {identity.status === 'error' && <button role="menuitem" onClick={() => { setOpen(false); onInformation(); }}>Sign-in help</button>}
      {error && <p role="alert">{error}</p>}
    </div>}
  </div>;
}
