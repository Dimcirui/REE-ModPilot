import { AnimatePresence } from 'motion/react';
import { SessionConfigForm } from '@/components/SessionConfigForm';
import { ViewportPane } from '@/components/ViewportPane';
import { ClassificationWidget } from '@/components/ClassificationWidget';
import { MaterialWidget } from '@/components/MaterialWidget';
import { ErrorChoice } from '@/components/ErrorChoice';
import type { StageProps } from './types';
import styles from './FallbackStage.module.css';

// Generic stage used for any phase that doesn't have a dedicated rebuild yet
// (setup_*, phase_2/3/35/4a/4b/5/6 until they're migrated). Keeps the form +
// viewport + widget slots, minus the chat (now lives in ChatStrip) and the
// phase stepper (now persistent in Shell.tsx above all stages).
export function FallbackStage({
  sessionId,
  state,
  inferredModelType,
  onClassificationSubmit,
  onMaterialSubmit,
  onErrorChoice,
}: StageProps) {
  return (
    <div className={styles.stage}>
      <div className={styles.leftColumn}>
        <SessionConfigForm sessionId={sessionId} inferredModelType={inferredModelType} />
        <div className={styles.widgetArea}>
          <AnimatePresence>
            {state.errorChoice && (
              <ErrorChoice
                key="error-choice"
                event={state.errorChoice}
                onChoose={onErrorChoice}
              />
            )}
          </AnimatePresence>
          <AnimatePresence>
            {state.widget?.kind === 'classification' && (
              <ClassificationWidget
                key="widget-classification"
                event={state.widget.event}
                onSubmit={onClassificationSubmit}
              />
            )}
            {state.widget?.kind === 'material' && (
              <MaterialWidget
                key="widget-material"
                event={state.widget.event}
                onSubmit={onMaterialSubmit}
              />
            )}
          </AnimatePresence>
        </div>
      </div>
      <ViewportPane />
    </div>
  );
}
