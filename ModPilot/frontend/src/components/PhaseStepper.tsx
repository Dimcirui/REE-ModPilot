import { motion } from 'motion/react';
import { PHASE_LABELS, PHASE_SEQUENCE, type PhaseName } from '@/types/domain';
import type { PhaseStatus } from '@/hooks/useChatState';
import styles from './PhaseStepper.module.css';

interface PhaseStepperProps {
  status: Record<PhaseName, PhaseStatus>;
}

const PHASE_GROUP: Record<PhaseName, string> = {
  setup_import_source: 'setup',
  setup_validate: 'setup',
  setup_infer: 'setup',
  setup_import: 'setup',
  phase_1: 'main',
  phase_2: 'main',
  phase_3: 'main',
  phase_35: 'mid',
  phase_4a: 'phys',
  phase_4b: 'phys',
  phase_5: 'mat',
  phase_6: 'exp',
};

export function PhaseStepper({ status }: PhaseStepperProps) {
  return (
    <ol className={styles.stepper} aria-label="Phase progress">
      {PHASE_SEQUENCE.map((phase, idx) => {
        const phaseStatus = status[phase];
        const label = PHASE_LABELS[phase];
        return (
          <motion.li
            key={phase}
            className={`${styles.node} ${styles[PHASE_GROUP[phase]]} ${styles[phaseStatus]}`}
            data-phase={phase}
            data-index={idx}
            title={label.long}
            layout
            animate={{
              scale: phaseStatus === 'active' ? 1.08 : 1,
            }}
            transition={{ type: 'spring', stiffness: 320, damping: 24 }}
          >
            {label.short}
          </motion.li>
        );
      })}
    </ol>
  );
}
