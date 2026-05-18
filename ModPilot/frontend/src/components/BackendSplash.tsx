import { useEffect, useState } from 'react';
import { motion } from 'motion/react';
import { apiUrl } from '@/lib/origin';
import styles from './BackendSplash.module.css';

type Status = 'starting' | 'ready' | 'error';

// Treat HTTP 200 and 503 as "backend reachable" — 503 just means Blender
// isn't connected yet, which is the normal state at first launch (the user
// hasn't started Blender or blender-mcp yet) and shouldn't block the UI.
function isReachable(status: number): boolean {
  return status === 200 || status === 503;
}

export function BackendSplash({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>('starting');
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const started = Date.now();

    async function probe() {
      try {
        const res = await fetch(apiUrl('/health'), { cache: 'no-store' });
        if (cancelled) return;
        if (isReachable(res.status)) {
          setStatus('ready');
          return;
        }
      } catch {
        // network error / connection refused — backend isn't listening yet
      }
      if (cancelled) return;
      setElapsedMs(Date.now() - started);
      // If we've waited >20 s, surface as an error so the user knows
      // something's wrong rather than spinning forever.
      if (Date.now() - started > 20_000) {
        setStatus('error');
        return;
      }
      window.setTimeout(probe, 400);
    }

    void probe();
    return () => {
      cancelled = true;
    };
  }, []);

  if (status === 'ready') return <>{children}</>;

  const seconds = (elapsedMs / 1000).toFixed(1);
  return (
    <div className={styles.splash}>
      <motion.div
        className={styles.card}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
      >
        <div className={styles.logo}>ModPilot</div>
        {status === 'starting' && (
          <>
            <div className={styles.spinner} aria-hidden />
            <div className={styles.line}>Starting backend…</div>
            {elapsedMs > 1500 && (
              <div className={styles.hint}>
                Bootstrapping the FastAPI sidecar ({seconds}s)
              </div>
            )}
          </>
        )}
        {status === 'error' && (
          <>
            <div className={styles.errorMark} aria-hidden>!</div>
            <div className={styles.line}>Backend isn't responding.</div>
            <div className={styles.hint}>
              The bundled <code>modpilot-backend.exe</code> didn't come up within 20s.
              In dev, make sure <code>uvicorn app.main:app --port 8000</code> is
              running. Otherwise the sidecar may have crashed — check the parent
              process's stderr.
            </div>
            <button
              type="button"
              className={styles.retry}
              onClick={() => window.location.reload()}
            >
              Retry
            </button>
          </>
        )}
      </motion.div>
    </div>
  );
}
