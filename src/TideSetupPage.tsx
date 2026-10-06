import { useEffect, useState } from 'react';
import { TideSetup } from './TideSetup';
import type { IdentityState, SecureHistoryProvider } from './history';

export function TideSetupPage({ identity, provider, navigate }: {
  identity: IdentityState; provider: SecureHistoryProvider; navigate: (path: string) => void;
}) {
  const [configured, setConfigured] = useState(false);
  useEffect(() => {
    if (configured && identity.status === 'authenticated') navigate('/');
  }, [configured, identity.status, navigate]);
  // Keep the verified provider in memory while moving into the app.
  if (configured && identity.status === 'authenticated') return null;
  return <main className="history-information">
    <button className="text-button back-link" onClick={() => navigate('/secure-history')}>← About secure history</button>
    <h1>{configured ? 'Secure history' : 'Set up TideCloak'}</h1>
    {!configured && <p className="information-lead">A guide for the person running this Redacted installation.</p>}
    <TideSetup identity={identity} provider={provider} onConfigured={setConfigured}/>
  </main>;
}
