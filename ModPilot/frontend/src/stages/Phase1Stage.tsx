import { useMemo } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { ViewportPane } from '@/components/ViewportPane';
import { ErrorChoice } from '@/components/ErrorChoice';
import type { ToolRun } from '@/hooks/useChatState';
import type { StageProps } from './types';
import styles from './Phase1Stage.module.css';

const PHASE_TOOL = 'pose_correction';

// Extract a numeric scale ratio out of the pose_correction tool result summary.
// Backend currently returns a free-text string; we look for the canonical
// `ratio=0.872` or `ratio: 0.872` substring. If not found, render the raw
// summary so the user still sees what happened. Replace with a structured
// field once the backend exposes one.
const RATIO_RE = /\bratio[:=\s]+(-?\d+(?:\.\d+)?)/i;

function parseRatio(summary: string | undefined): number | null {
  if (!summary) return null;
  const m = RATIO_RE.exec(summary);
  if (!m) return null;
  const v = Number.parseFloat(m[1]);
  return Number.isFinite(v) ? v : null;
}

function fmtRatio(r: number): string {
  return r.toFixed(4);
}

function runState(run: ToolRun | null): 'pending' | 'running' | 'ok' | 'fail' {
  if (!run) return 'pending';
  if (run.finishedAt === undefined) return 'running';
  return run.success ? 'ok' : 'fail';
}

export function Phase1Stage({ state, onErrorChoice }: StageProps) {
  const phaseRuns = useMemo(
    () => state.toolRuns.filter((r) => r.phase === 'phase_1'),
    [state.toolRuns],
  );

  const poseRuns = useMemo(
    () => phaseRuns.filter((r) => r.name === PHASE_TOOL),
    [phaseRuns],
  );

  const latestPose = poseRuns.length > 0 ? poseRuns[poseRuns.length - 1] : null;
  const status = runState(latestPose);
  const ratio = parseRatio(latestPose?.summary);
  const phaseStatus = state.phaseStatus.phase_1;

  return (
    <div className={styles.stage}>
      <section className={styles.canvas}>
        <ViewportPane />
      </section>

      <aside className={styles.sidebar} aria-label="Phase 1 details">
        <header className={styles.header}>
          <div className={styles.eyebrow}>Phase 1</div>
          <h2 className={styles.title}>Pose Correction</h2>
          <span
            className={`${styles.phaseChip} ${styles[`chip_${phaseStatus}`]}`}
            title={`Phase status: ${phaseStatus}`}
          >
            {phaseStatus}
          </span>
        </header>

        <section className={`${styles.card} ${styles[`card_${status}`]}`}>
          <div className={styles.cardHead}>
            <span className={styles.cardLabel}>Scale align</span>
            <span className={styles.cardStatus}>{status}</span>
          </div>
          {status === 'pending' && (
            <div className={styles.muted}>Awaiting <code>pose_correction</code>…</div>
          )}
          {status === 'running' && (
            <div className={styles.muted}>Running on the rig…</div>
          )}
          {(status === 'ok' || status === 'fail') && (
            <>
              {ratio !== null ? (
                <div className={styles.bigNumber}>
                  ×<span className={styles.bigNumberValue}>{fmtRatio(ratio)}</span>
                </div>
              ) : (
                <div className={styles.muted}>No ratio reported.</div>
              )}
              <div className={styles.summary} title={latestPose?.summary}>
                {latestPose?.summary?.slice(0, 240) ?? ''}
              </div>
            </>
          )}
        </section>

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
                        {r.summary?.slice(0, 80) ?? '…'}
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
