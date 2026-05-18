import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { apiUrl } from '@/lib/origin';
import styles from './ViewportPane.module.css';

const POLL_INTERVAL_MS = 5000;
const MAX_SIZE = 800;

type ViewportStatus =
  | { kind: 'idle' }
  | { kind: 'refreshing' }
  | { kind: 'ok'; updatedAt: string }
  | { kind: 'error'; message: string };

export function ViewportPane() {
  const [auto, setAuto] = useState(true);
  const [status, setStatus] = useState<ViewportStatus>({ kind: 'idle' });
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState(0);
  const inFlightRef = useRef(false);
  const previousUrlRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setStatus({ kind: 'refreshing' });
    try {
      const res = await fetch(apiUrl(`/viewport_screenshot?max_size=${MAX_SIZE}`), {
        cache: 'no-store',
      });
      if (!res.ok) {
        let detail = '';
        try {
          const body = (await res.json()) as { detail?: string };
          detail = body.detail ?? '';
        } catch {
          // body wasn't json
        }
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const previous = previousUrlRef.current;
      previousUrlRef.current = url;
      setImageUrl(url);
      setImageKey((k) => k + 1);
      if (previous) URL.revokeObjectURL(previous);
      setStatus({
        kind: 'ok',
        updatedAt: new Date().toLocaleTimeString(),
      });
    } catch (err) {
      setStatus({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      inFlightRef.current = false;
    }
  }, []);

  // Initial pull + 5s poll while auto is on.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(() => {
      if (document.hidden) return;
      void refresh();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [auto, refresh]);

  // Refresh immediately on tab return when auto is on.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden || !auto) return;
      void refresh();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [auto, refresh]);

  // Cleanup blob URLs on unmount.
  useEffect(
    () => () => {
      if (previousUrlRef.current) {
        URL.revokeObjectURL(previousUrlRef.current);
        previousUrlRef.current = null;
      }
    },
    [],
  );

  const statusText =
    status.kind === 'idle'
      ? 'idle'
      : status.kind === 'refreshing'
        ? 'refreshing…'
        : status.kind === 'ok'
          ? `updated ${status.updatedAt}`
          : status.message;

  const statusTone =
    status.kind === 'error' ? styles.statusError : status.kind === 'ok' ? styles.statusOk : '';

  return (
    <aside className={styles.pane} aria-label="Blender viewport preview">
      <div className={styles.header}>
        <h3 className={styles.title}>Viewport</h3>
        <label className={styles.autoLabel} title="Periodically refresh from Blender">
          <input
            type="checkbox"
            checked={auto}
            onChange={(e) => {
              const next = e.target.checked;
              setAuto(next);
              if (next) void refresh();
            }}
          />
          <span>Auto</span>
        </label>
        <button
          type="button"
          className={styles.refreshButton}
          onClick={() => void refresh()}
          title="Refresh now"
          disabled={status.kind === 'refreshing'}
        >
          ↻
        </button>
      </div>

      <div className={styles.frame}>
        <AnimatePresence mode="wait" initial={false}>
          {imageUrl ? (
            <motion.img
              key={imageKey}
              src={imageUrl}
              alt="3D viewport preview"
              className={styles.image}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            />
          ) : (
            <motion.div
              key="placeholder"
              className={styles.placeholder}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
            >
              {status.kind === 'error' ? 'Blender unreachable' : 'Connecting to Blender…'}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className={`${styles.status} ${statusTone}`}>{statusText}</div>
    </aside>
  );
}
