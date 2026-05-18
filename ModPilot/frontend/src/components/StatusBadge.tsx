import { AnimatePresence, motion } from 'motion/react';
import type { StatusLabel, StatusTone } from '@/hooks/useChatState';
import styles from './StatusBadge.module.css';

interface StatusBadgeProps {
  label: StatusLabel;
  tone: StatusTone;
}

export function StatusBadge({ label, tone }: StatusBadgeProps) {
  return (
    <div className={`${styles.status} ${tone ? styles[tone] : ''}`} role="status">
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={label}
          initial={{ opacity: 0, y: 3 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -3 }}
          transition={{ duration: 0.12 }}
        >
          {tone === 'thinking' && <span className={styles.dot} aria-hidden />}
          {label}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}
