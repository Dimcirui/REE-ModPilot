import { useEffect } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import styles from './InterruptBanner.module.css';

interface InterruptBannerProps {
  visible: boolean;
  onDismiss: () => void;
  autoHideMs?: number;
}

const DEFAULT_AUTO_HIDE = 6000;

export function InterruptBanner({
  visible,
  onDismiss,
  autoHideMs = DEFAULT_AUTO_HIDE,
}: InterruptBannerProps) {
  useEffect(() => {
    if (!visible) return;
    const timer = window.setTimeout(onDismiss, autoHideMs);
    return () => window.clearTimeout(timer);
  }, [visible, autoHideMs, onDismiss]);

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          className={styles.banner}
          role="status"
          aria-live="polite"
          initial={{ y: -24, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -24, opacity: 0 }}
          transition={{ duration: 0.18, ease: 'easeOut' }}
        >
          <span className={styles.text}>
            已打断 — agent has been interrupted. The next message starts a fresh turn.
          </span>
          <button
            type="button"
            className={styles.dismiss}
            onClick={onDismiss}
            aria-label="Dismiss"
          >
            ✕
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
