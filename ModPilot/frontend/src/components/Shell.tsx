import type { ReactNode } from 'react';
import styles from './Shell.module.css';

interface ShellProps {
  header: ReactNode;
  stage: ReactNode;
  chatStrip: ReactNode;
  phaseStepper?: ReactNode;
  banner?: ReactNode;
}

export function Shell({ header, stage, chatStrip, phaseStepper, banner }: ShellProps) {
  return (
    <div className={styles.shell}>
      <div className={styles.headerSlot}>{header}</div>
      {phaseStepper && <div className={styles.phaseStepperSlot}>{phaseStepper}</div>}
      <main className={styles.stageSlot}>{stage}</main>
      {banner && <div className={styles.bannerSlot}>{banner}</div>}
      <div className={styles.chatSlot}>{chatStrip}</div>
    </div>
  );
}
