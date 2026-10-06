import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import App from './App';
import { unavailableHistoryProvider } from './history';
import type { SecureHistoryProvider } from './history';
import './globals.css';
import { routeLinkReturn, setupSignInState } from './setupNavigation';

routeLinkReturn();

function Root() {
  const [provider, setProvider] = useState<SecureHistoryProvider>(unavailableHistoryProvider);
  useEffect(() => {
    if (window.location.pathname === '/secure-history/linked') return;
    let live = true;
    void fetch('/api/service/capabilities', { cache: 'no-store' }).then(async capabilities => {
      if (!capabilities.ok || !(await capabilities.json()).secure_history.available) return;
      const response = await fetch('/api/service/tide/config', { cache: 'no-store' });
      if (!response.ok) return;
      const config = await response.json();
      const { createTideHistoryProvider } = await import('./tideHistory');
      if (!live) return;
      const next = createTideHistoryProvider(config);
      setProvider(next);
      void next.initialise({ completeSetup: window.location.pathname === '/secure-history/setup' && !!setupSignInState() });
    }).catch(() => {});
    return () => { live = false; };
  }, []);
  return <App historyProvider={provider}/>;
}
createRoot(document.getElementById('root')!).render(<Root />);
