import { motion } from 'motion/react';
import type { ErrorChoiceEvent } from '@/types/sse';
import styles from './ErrorChoice.module.css';

interface ErrorChoiceProps {
  event: ErrorChoiceEvent;
  onChoose: (keyword: string) => void;
}

interface Choice {
  keyword: string;
  label: string;
  variant: string;
}

const BASE_CHOICES: Choice[] = [
  { keyword: '重试', label: '重试', variant: 'retry' },
  { keyword: '跳过', label: '跳过', variant: 'skip' },
  { keyword: '查看详情', label: '查看详情', variant: 'ask' },
];

const FORCE_CUSTOM_CHOICE: Choice = {
  keyword: '[FORCE_CUSTOM] 强制自定义预设',
  label: '强制自定义',
  variant: 'force-custom',
};

export function ErrorChoice({ event, onChoose }: ErrorChoiceProps) {
  const choices = [...BASE_CHOICES];
  if (event.category === 'unsupported_rig') {
    choices.push(FORCE_CUSTOM_CHOICE);
  }
  return (
    <motion.div
      className={styles.group}
      role="group"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ duration: 0.16 }}
    >
      {choices.map((c) => (
        <button
          key={c.keyword}
          type="button"
          className={`${styles.button} ${styles[c.variant]}`}
          onClick={() => onChoose(c.keyword)}
        >
          {c.label}
        </button>
      ))}
    </motion.div>
  );
}
