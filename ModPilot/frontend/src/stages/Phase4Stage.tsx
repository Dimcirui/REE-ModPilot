import { useMemo } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { ViewportPane } from '@/components/ViewportPane';
import { ResizeHandle } from '@/components/ResizeHandle';
import { ClassificationWidget } from '@/components/ClassificationWidget';
import { ErrorChoice } from '@/components/ErrorChoice';
import type { ToolRun } from '@/hooks/useChatState';
import type { StageProps } from './types';
import styles from './Phase4Stage.module.css';

type RunState = 'pending' | 'running' | 'ok' | 'fail';

const PHASE_TOOLS = {
  transplant: 'physics_transplant',
  classify: 'physics_classification',
  chains: 'physics_chains',
  adjust: 'physics_adjust',
} as const;

const PHASE_4_KEYS = new Set(['phase_35', 'phase_4a', 'phase_4b']);

function runState(run: ToolRun | null): RunState {
  if (!run) return 'pending';
  if (run.finishedAt === undefined) return 'running';
  return run.success ? 'ok' : 'fail';
}

function latest(runs: ToolRun[], name: string): ToolRun | null {
  const filtered = runs.filter((r) => r.name === name);
  return filtered.length > 0 ? filtered[filtered.length - 1] : null;
}

const COUNT_RE = /(\d+)\s+(?:chains?|chain\s+settings?|bones?\s+transplanted|chain\s+groups?|chain\s+objects?)/i;
function parseCount(s: string | undefined): number | null {
  if (!s) return null;
  const m = COUNT_RE.exec(s);
  if (!m) return null;
  const n = Number.parseInt(m[1], 10);
  return Number.isFinite(n) ? n : null;
}

function StepCard({
  label,
  step,
  status,
  children,
}: {
  label: string;
  step: string;
  status: RunState;
  children: React.ReactNode;
}) {
  return (
    <section className={`${styles.card} ${styles[`card_${status}`]}`}>
      <div className={styles.cardHead}>
        <div>
          <div className={styles.cardStep}>{step}</div>
          <div className={styles.cardLabel}>{label}</div>
        </div>
        <span className={styles.cardStatus}>{status}</span>
      </div>
      {children}
    </section>
  );
}

export function Phase4Stage({ state, onClassificationSubmit, onErrorChoice }: StageProps) {
  const phaseRuns = useMemo(
    () => state.toolRuns.filter((r) => r.phase && PHASE_4_KEYS.has(r.phase)),
    [state.toolRuns],
  );

  const transplantRun = useMemo(() => latest(phaseRuns, PHASE_TOOLS.transplant), [phaseRuns]);
  const classifyRun = useMemo(() => latest(phaseRuns, PHASE_TOOLS.classify), [phaseRuns]);
  const chainsRun = useMemo(() => latest(phaseRuns, PHASE_TOOLS.chains), [phaseRuns]);
  const adjustRun = useMemo(() => latest(phaseRuns, PHASE_TOOLS.adjust), [phaseRuns]);

  const transplantStatus = runState(transplantRun);
  const classifyStatus = runState(classifyRun);
  const chainsStatus = runState(chainsRun);
  const adjustStatus = runState(adjustRun);

  // Focus phase determines the eyebrow / title chip — walk active first, then
  // fall back to the latest done phase.
  const ps = state.phaseStatus;
  const focusPhase: 'phase_35' | 'phase_4a' | 'phase_4b' =
    ps.phase_4b === 'active' || ps.phase_4a === 'done'
      ? 'phase_4b'
      : ps.phase_4a === 'active' || ps.phase_35 === 'done'
        ? 'phase_4a'
        : 'phase_35';

  const focusTitle =
    focusPhase === 'phase_35'
      ? 'Physics Bone Transplant'
      : focusPhase === 'phase_4a'
        ? 'Physics Classification'
        : 'Physics Chains';

  const focusEyebrow =
    focusPhase === 'phase_35' ? 'Phase 3.5' : focusPhase === 'phase_4a' ? 'Phase 4A' : 'Phase 4B';

  const transplantedCount = parseCount(transplantRun?.summary);
  const chainCount = parseCount(chainsRun?.summary);
  const inferredTypeCount =
    state.widget?.kind === 'classification'
      ? state.widget.event.inferred_types.length
      : null;
  const chainHeadCount =
    state.widget?.kind === 'classification' ? state.widget.event.chains.length : null;

  const showClassificationWidget = state.widget?.kind === 'classification';

  return (
    <div className={styles.stage}>
      <section className={styles.canvas}>
        <ViewportPane />
        <AnimatePresence>
          {showClassificationWidget && (
            <motion.div
              key="classification-widget"
              className={styles.widgetOverlay}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ duration: 0.18 }}
            >
              <ClassificationWidget
                event={state.widget!.event as never}
                onSubmit={onClassificationSubmit as never}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </section>

      <ResizeHandle storageKey="canvas" />

      <aside className={styles.sidebar} aria-label="Phase 4 details">
        <header className={styles.header}>
          <div className={styles.eyebrow}>{focusEyebrow}</div>
          <h2 className={styles.title}>{focusTitle}</h2>
          <span
            className={`${styles.phaseChip} ${styles[`chip_${ps[focusPhase]}`]}`}
            title={`Phase status: ${ps[focusPhase]}`}
          >
            {ps[focusPhase]}
          </span>
        </header>

        <StepCard label="Bone transplant" step="P3.5" status={transplantStatus}>
          {transplantStatus === 'pending' && (
            <div className={styles.muted}>Awaiting <code>physics_transplant</code>…</div>
          )}
          {transplantStatus === 'running' && (
            <div className={styles.muted}>Grafting physics bones onto MHWs rig…</div>
          )}
          {(transplantStatus === 'ok' || transplantStatus === 'fail') && (
            <>
              {transplantedCount !== null && (
                <div className={styles.bigNumber}>
                  <span className={styles.bigNumberValue}>{transplantedCount}</span>
                  <span className={styles.bigNumberUnit}>bones</span>
                </div>
              )}
              {transplantRun?.summary && (
                <div className={styles.summary}>{transplantRun.summary.slice(0, 180)}</div>
              )}
            </>
          )}
        </StepCard>

        <StepCard label="Chain classification" step="P4A" status={classifyStatus}>
          {classifyStatus === 'pending' && (
            <div className={styles.muted}>Awaiting <code>physics_classification</code>…</div>
          )}
          {classifyStatus === 'running' && (
            <div className={styles.muted}>Scanning chain topology…</div>
          )}
          {classifyStatus === 'ok' && !showClassificationWidget && (
            <>
              {chainHeadCount !== null && (
                <div className={styles.statRow}>
                  <div className={styles.stat}>
                    <div className={styles.statValue}>{chainHeadCount}</div>
                    <div className={styles.statLabel}>chain heads</div>
                  </div>
                  {inferredTypeCount !== null && (
                    <div className={styles.stat}>
                      <div className={styles.statValue}>{inferredTypeCount}</div>
                      <div className={styles.statLabel}>types</div>
                    </div>
                  )}
                </div>
              )}
              {classifyRun?.summary && (
                <div className={styles.summary}>{classifyRun.summary.slice(0, 180)}</div>
              )}
            </>
          )}
          {showClassificationWidget && (
            <div className={styles.muted}>
              Confirm chain types in the panel on the left, then submit to continue.
            </div>
          )}
          {classifyStatus === 'fail' && classifyRun?.summary && (
            <div className={styles.summary}>{classifyRun.summary.slice(0, 180)}</div>
          )}
        </StepCard>

        <StepCard label="RE Chain creation" step="P4B" status={chainsStatus}>
          {chainsStatus === 'pending' && (
            <div className={styles.muted}>
              {classifyStatus === 'ok'
                ? 'Awaiting user confirmation, then <code>physics_chains</code>…'
                : 'Runs after classification.'}
            </div>
          )}
          {chainsStatus === 'running' && (
            <div className={styles.muted}>Creating RE Chain settings &amp; params…</div>
          )}
          {(chainsStatus === 'ok' || chainsStatus === 'fail') && (
            <>
              {chainCount !== null && (
                <div className={styles.bigNumber}>
                  <span className={styles.bigNumberValue}>{chainCount}</span>
                  <span className={styles.bigNumberUnit}>chains</span>
                </div>
              )}
              {chainsRun?.summary && (
                <div className={styles.summary}>{chainsRun.summary.slice(0, 180)}</div>
              )}
              {adjustStatus !== 'pending' && (
                <div className={styles.adjustRow}>
                  <span className={`${styles.adjustDot} ${styles[`item_${adjustStatus}`]}`} />
                  <span className={styles.adjustText}>
                    physics_adjust · {adjustStatus}
                  </span>
                </div>
              )}
            </>
          )}
        </StepCard>

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
