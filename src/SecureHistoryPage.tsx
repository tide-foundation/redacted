import { useState } from 'react';
import type { IdentityState, SecureHistoryProvider } from './history';

export function SecureHistoryPage({ identity, provider, available, navigate }: {
  identity: IdentityState; provider: SecureHistoryProvider; available: boolean; navigate: (path: string) => void;
}) {
  const [error, setError] = useState('');
  const signedIn = identity.status === 'authenticated';
  return <main className="history-information">
    <button className="text-button back-link" onClick={() => navigate('/')}>← Back to redacting</button>
    <h1>Keep your files. Keep them private.</h1>
    <p className="information-lead">Sign in with Tide to save encrypted originals and redacted documents, and return to them later.</p>
    <ul className="history-benefits">
      <li>Your documents and detected values are encrypted before they are saved.</li>
      <li>Reveal detections and download files when you need them.</li>
      <li>Your app and stored documents stay on this machine.</li>
    </ul>
    <p>Guest redaction and downloads remain available without an account. Sign in before uploading to keep a result; download any guest result before signing in.</p>
    <p>Tide provides authentication and personal encryption using its network. Decrypted items are cached only in this browser tab’s memory for your signed-in session and cleared on sign-out or reload.</p>
    {error && <p className="notice" role="alert">{error}</p>}
    {signedIn ? <button className="primary" onClick={() => navigate('/')}>Back to your files</button> : available && identity.status !== 'unavailable' ?
      <button className="primary history-sign-in" disabled={identity.status !== 'signed-out'} onClick={() => { setError(''); void provider.signIn().catch(() => setError('Sign-in could not be started. Please try again.')); }}>Sign in to keep history</button> : <>
        <p className="notice">Secure history is not configured on this installation.</p>
        <button className="primary" onClick={() => navigate('/secure-history/setup')}>Set up TideCloak</button>
      </>}
    <p><a href="/secure-history/setup" onClick={event => { event.preventDefault(); navigate('/secure-history/setup'); }}>Setup guide for the installation owner →</a></p>
  </main>;
}
