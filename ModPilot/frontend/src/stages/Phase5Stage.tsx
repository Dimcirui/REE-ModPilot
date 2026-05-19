import { useMemo } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { ViewportPane } from '@/components/ViewportPane';
import { ResizeHandle } from '@/components/ResizeHandle';
import { MaterialWidget } from '@/components/MaterialWidget';
import { ErrorChoice } from '@/components/ErrorChoice';
import type { ToolRun } from '@/hooks/useChatState';
import type { StageProps } from './types';
import styles from './Phase5Stage.module.css';

type RunState = 'pending' | 'running' | 'ok' | 'fail';

const PHASE_TOOLS = ['material_consolidate', 'material_inspect', 'material_setup', 'material_generate'] as const;

function runState(run: ToolRun | null): RunState {
  if (!run) return 'pending';
  if (run.finishedAt === undefined) return 'running';
  return run.success ? 'ok' : 'fail';
}

function latest(runs: ToolRun[], name: string): ToolRun | null {
  const filtered = runs.filter((r) => r.name === name);
  return filtered.length > 0 ? filtered[filtered.length - 1] : null;
}

const MAT_COUNT_RE = /(\d+)\s+materials?/i;
const SLOT_COUNT_RE = /(\d+)\s+(?:slots?\s+wired|textures?\s+assigned)/i;

export function Phase5Stage({ state, onMaterialSubmit, onErrorChoice }: StageProps) {
  const phaseRuns = useMemo(
    () => state.toolRuns.filter((r) => r.phase === 'phase_5'),
    [state.toolRuns],
  );

  const steps = useMemo(
    () =>
      PHASE_TOOLS.map((name) => {
        const run = latest(phaseRuns, name);
        return { name, run, status: runState(run) };
      }),
    [phaseRuns],
  );

  const showWidget = state.widget?.kind === 'material';
  const phaseStatus = state.phaseStatus.phase_5;

  // For the headline number, prefer materials-count from material_inspect (or
  // any successful step that reports it).
  const matCount = (() => {
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      const s = steps[i].run?.summary;
      if (!s) continue;
      const m = MAT_COUNT_RE.exec(s);
      if (m) return Number.parseInt(m[1], 10);
    }
    return null;
  })();
  const slotsWired = (() => {
    const gen = latest(phaseRuns, 'material_generate');
    if (!gen?.summary) return null;
    const m = SLOT_COUNT_RE.exec(gen.summary);
    return m ? Number.parseInt(m[1], 10) : null;
  })();

  return (
    <div className={styles.stage}>
      <section className={styles.canvas}>
        {showWidget ? (
          <div className={styles.widgetMount}>
            <MaterialWidget
              event={state.widget!.event as never}
              onSubmit={onMaterialSubmit as never}
            />
          </div>
        ) : (
          <ViewportPane />
        )}
      </section>

      <ResizeHandle storageKey="canvas" />

      <aside className={styles.sidebar} aria-label="Phase 5 details">
        <header className={styles.header}>
          <div className={styles.eyebrow}>Phase 5</div>
          <h2 className={styles.title}>Materials</h2>
          <span
            className={`${styles.phaseChip} ${styles[`chip_${phaseStatus}`]}`}
            title={`Phase status: ${phaseStatus}`}
          >
            {phaseStatus}
          </span>
        </header>

        {(matCount !== null || slotsWired !== null) && (
          <section className={styles.summaryCard}>
            <div className={styles.statRow}>
              {matCount !== null && (
                <div className={styles.stat}>
                  <div className={styles.statValue}>{matCount}</div>
                  <div className={styles.statLabel}>materials</div>
                </div>
              )}
              {slotsWired !== null && (
                <div className={styles.stat}>
                  <div className={styles.statValue}>{slotsWired}</div>
                  <div className={styles.statLabel}>slots wired</div>
                </div>
              )}
            </div>
          </section>
        )}

        <section className={styles.stepList}>
          <div className={styles.stepListHead}>Pipeline</div>
          {steps.map(({ name, run, status }) => (
            <div
              key={name}
              className={`${styles.step} ${styles[`step_${status}`]}`}
              title={run?.summary}
            >
              <span className={styles.stepDot} aria-hidden />
              <span className={styles.stepName}>{name}</span>
              <span className={styles.stepStatus}>{status}</span>
            </div>
          ))}
        </section>

        {showWidget && (
          <div className={styles.widgetHint}>
            Fill in texture paths in the panel on the left and submit to continue.
          </div>
        )}

        <section className={styles.activity}>
          <div className={styles.activityHead}>Activity</div>
          {phaseRuns.length === 0 && (
            <div className={styles.muted}>No tool calls yet.</div>
          )}
          <ul className={styles.activityList}>
            <AnimatePresence initial={false}>
              {phaseRuns
                .slice()
                .reverse()
                .slice(0, 6)
                .map((r) => {
                  const rs = runState(r);
                  return (
                    <motion.li
                      key={r.runId}
                      layout
                      initial={{ opacity: 0, x: 6 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.16 }}
                      className={`${styles.activityItem} ${styles[`item_${rs}`]}`}
                    >
                      <span className={styles.itemDot} aria-hidden />
                      <span className={styles.itemName}>{r.name}</span>
                      <span className={styles.itemSummary}>
                        {r.summary?.slice(0, 70) ?? '…'}
                      </span>
                    </motion.li>
                  );
                })}
            </AnimatePresence>
          </ul>
        </section>

        <AnimatePresence>
          {state.errorChoice && (
            <motion.div
              key="error-choice"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
            >
              <ErrorChoice event={state.errorChoice} onChoose={onErrorChoice} />
            </motion.div>
          )}
        </AnimatePresence>
      </aside>
    </div>
  );
}
