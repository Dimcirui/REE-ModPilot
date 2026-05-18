import type { ComponentType } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { deriveActivePhase } from './deriveActivePhase';
import { STAGE_REGISTRY } from './registry';
import { FallbackStage } from './FallbackStage';
import { DoneStage } from './DoneStage';
import type { StageProps } from './types';
import styles from './StageRouter.module.css';

// Stable key for the cross-fade: derived from the *component* rather than
// the active phase, so sibling phases that share a stage (e.g. phase_2 and
// phase_3 both render Phase23Stage) don't trigger a remount + animation
// when the agent advances between them. Use displayName/name, falling back
// to phase name only for distinct components without an identifier.
function stageKey(
  Component: ComponentType<StageProps> | undefined,
  phase: string | null,
): string {
  if (!Component) return 'fallback';
  const ident = Component.displayName ?? Component.name;
  return ident || `phase:${phase ?? 'unknown'}`;
}

export function StageRouter(props: StageProps) {
  // Done has priority over phase-derived stages: once the loop reports
  // completion the user should land on the celebratory recap regardless of
  // which phase was last active.
  const isDone = props.state.loopState === 'done';

  const active = deriveActivePhase(props.state.phaseStatus);
  const Concrete = isDone
    ? DoneStage
    : active
      ? STAGE_REGISTRY[active]
      : undefined;
  const StageComponent = Concrete ?? FallbackStage;
  const key = stageKey(StageComponent, active);

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={key}
        className={styles.stage}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
      >
        <StageComponent {...props} />
      </motion.div>
    </AnimatePresence>
  );
}
