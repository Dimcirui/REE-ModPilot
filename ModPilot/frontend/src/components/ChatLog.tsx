import { useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { Bubble } from '@/hooks/useChatState';
import styles from './ChatLog.module.css';

interface ChatLogProps {
  bubbles: Bubble[];
  debugMode: boolean;
}

export function ChatLog({ bubbles, debugMode }: ChatLogProps) {
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [bubbles.length]);

  return (
    <div ref={logRef} className={styles.log} role="log" aria-live="polite">
      <AnimatePresence initial={false}>
        {bubbles.map((b) => {
          if (b.debug && !debugMode) return null;
          return (
            <motion.div
              key={b.id}
              layout
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18, ease: 'easeOut' }}
              className={`${styles.bubble} ${styles[b.role]} ${b.debug ? styles.debug : ''}`}
            >
              {b.content}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
