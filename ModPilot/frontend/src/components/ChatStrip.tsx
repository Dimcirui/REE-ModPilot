import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { ChatLog } from './ChatLog';
import { MessageInput } from './MessageInput';
import { StatusBadge } from './StatusBadge';
import type { Bubble } from '@/hooks/useChatState';
import type { StatusLabel, StatusTone } from '@/hooks/useChatState';
import styles from './ChatStrip.module.css';

interface ChatStripProps {
  bubbles: Bubble[];
  debugMode: boolean;
  status: StatusLabel;
  statusTone: StatusTone;
  reconnectIn: number | null;
  inputDisabled: boolean;
  inputPlaceholder: string;
  onSubmit: (message: string) => void;
}

const COLLAPSED_PREVIEW_CHARS = 140;

function lastVisibleLine(bubbles: Bubble[], debugMode: boolean): string {
  for (let i = bubbles.length - 1; i >= 0; i -= 1) {
    const b = bubbles[i];
    if (b.debug && !debugMode) continue;
    return b.content;
  }
  return '';
}

export function ChatStrip({
  bubbles,
  debugMode,
  status,
  statusTone,
  reconnectIn,
  inputDisabled,
  inputPlaceholder,
  onSubmit,
}: ChatStripProps) {
  const [expanded, setExpanded] = useState(false);
  const lastSeenLenRef = useRef(0);
  const [unread, setUnread] = useState(0);

  const preview = useMemo(() => {
    const text = lastVisibleLine(bubbles, debugMode);
    if (text.length <= COLLAPSED_PREVIEW_CHARS) return text;
    return `${text.slice(0, COLLAPSED_PREVIEW_CHARS).trim()}…`;
  }, [bubbles, debugMode]);

  // Track unread count while collapsed.
  useEffect(() => {
    if (expanded) {
      lastSeenLenRef.current = bubbles.length;
      setUnread(0);
      return;
    }
    setUnread(Math.max(0, bubbles.length - lastSeenLenRef.current));
  }, [bubbles.length, expanded]);

  const toggle = useCallback(() => setExpanded((v) => !v), []);

  const handleSubmit = useCallback(
    (message: string) => {
      onSubmit(message);
      lastSeenLenRef.current = bubbles.length + 1; // optimistic
    },
    [bubbles.length, onSubmit],
  );

  return (
    <div className={`${styles.strip} ${expanded ? styles.expanded : styles.collapsed}`}>
      <button
        type="button"
        className={styles.summaryRow}
        onClick={toggle}
        aria-expanded={expanded}
        aria-controls="chatstrip-body"
        title={expanded ? 'Collapse chat' : 'Expand chat'}
      >
        <span className={styles.chevron} aria-hidden>
          {expanded ? '▾' : '▴'}
        </span>
        <StatusBadge label={status} tone={statusTone} />
        {reconnectIn !== null && (
          <span className={styles.reconnectHint}>retry in {reconnectIn}s</span>
        )}
        <span className={styles.preview} title={preview}>
          {preview || (expanded ? '' : 'Chat with the agent…')}
        </span>
        {!expanded && unread > 0 && (
          <span className={styles.unread} aria-label={`${unread} new messages`}>
            {unread}
          </span>
        )}
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            id="chatstrip-body"
            key="body"
            className={styles.body}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
          >
            <div className={styles.logWrap}>
              <ChatLog bubbles={bubbles} debugMode={debugMode} />
            </div>
            <div className={styles.inputWrap}>
              <MessageInput
                disabled={inputDisabled}
                placeholder={inputPlaceholder}
                onSubmit={handleSubmit}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
