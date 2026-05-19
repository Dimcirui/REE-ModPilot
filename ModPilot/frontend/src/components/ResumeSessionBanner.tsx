import { AnimatePresence, motion } from 'motion/react';
import type { SessionStatusResponse } from '@/types/api';
import styles from './ResumeSessionBanner.module.css';

interface ResumeSessionBannerProps {
  status: SessionStatusResponse | null;
  onResume: () => void;
  onStartNew: () => void;
}

function formatRelative(ts: number | null): string {
  if (ts === null) return '';
  const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diffSec < 60) return `${diffSec}s ago`;
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function ResumeSessionBanner({
  status,
  onResume,
  onStartNew,
}: ResumeSessionBannerProps) {
  const visible = status !== null && status.has_history && !status.completed;
  return (
    <AnimatePresence>
      {visible && status && (
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
            上次会话进行到 <strong>{status.current_phase ?? 'unknown'}</strong>
            （{formatRelative(status.last_activity_ts)}）。是否恢复？
          </span>
          <button type="button" className={styles.primary} onClick={onResume}>
            恢复
          </button>
          <button type="button" className={styles.secondary} onClick={onStartNew}>
            开始新会话
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
