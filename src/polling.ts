// Schedule from completion so slow requests never build a polling backlog.
export function startVisiblePolling(run: () => Promise<unknown>, interval: () => number) {
  const visible = () => document.visibilityState !== 'hidden';
  let stopped = false, running = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function tick() {
    clearTimeout(timer);
    if (stopped || running || !visible()) return;
    running = true;
    try { await run(); }
    finally {
      running = false;
      if (!stopped && visible()) timer = setTimeout(() => void tick(), interval());
    }
  }
  const resume = () => { void tick(); };
  const visibility = () => { clearTimeout(timer); if (visible()) resume(); };
  document.addEventListener('visibilitychange', visibility);
  window.addEventListener('focus', resume);
  window.addEventListener('online', resume);
  resume();
  return () => {
    stopped = true; clearTimeout(timer);
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('focus', resume);
    window.removeEventListener('online', resume);
  };
}
