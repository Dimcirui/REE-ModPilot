import { useMemo } from 'react';
import { motion } from 'motion/react';
import { ViewportPane } from '@/components/ViewportPane';
import type { ToolRun } from '@/hooks/useChatState';
import type { StageProps } from './types';
import styles from './DoneStage.module.css';

const PATH_RE = /natives?(?:_root)?[:=\s]+([A-Za-z]:[\\/][^\s\n]+|\/[^\s\n]+)/i;

function findExportRun(runs: ToolRun[]): ToolRun | null {
  for (let i = runs.length - 1; i >= 0; i -= 1) {
    if (runs[i].name === 'batch_export' && runs[i].success) return runs[i];
  }
  return null;
}

export function DoneStage({ state }: StageProps) {
  const exportRun = useMemo(() => findExportRun(state.toolRuns), [state.toolRuns]);
  const nativesRoot =
    (exportRun?.input?.natives_root as string | undefined) ??
    (exportRun?.summary && PATH_RE.exec(exportRun.summary)?.[1]) ??
    null;
  const armorId = (exportRun?.input?.armor_id as string | undefined) ?? null;

  const stats = useMemo(() => {
    const phaseCount = Object.values(state.phaseStatus).filter((s) => s === 'done').length;
    const toolCount = state.toolRuns.filter((r) => r.success).length;
    const failCount = state.toolRuns.filter((r) => r.success === false).length;
    return { phaseCount, toolCount, failCount };
  }, [state.phaseStatus, state.toolRuns]);

  return (
    <div className={styles.stage}>
      <section className={styles.canvas}>
        <ViewportPane />
      </section>

      <aside className={styles.sidebar} aria-label="Run complete">
        <motion.div
          className={styles.hero}
          initial={{ opacity: 0, scale: 0.96, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={{ duration: 0.35, ease: 'easeOut' }}
        >
          <div className={styles.checkRing}>
            <motion.svg
              viewBox="0 0 32 32"
              width="48"
              height="48"
              aria-hidden
            >
              <motion.path
                d="M7 16 L14 23 L25 10"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ delay: 0.2, duration: 0.45, ease: 'easeOut' }}
              />
            </motion.svg>
          </div>
          <h2 className={styles.heroTitle}>Mod build complete</h2>
          <div className={styles.heroSub}>
            {armorId ? (
              <>
                Exported as <code>{armorId}</code>
              </>
            ) : (
              'All phases finished.'
            )}
          </div>
        </motion.div>

        <section className={styles.statRow}>
          <div className={styles.stat}>
            <div className={styles.statValue}>{stats.phaseCount}</div>
            <div className={styles.statLabel}>phases done</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statValue}>{stats.toolCount}</div>
            <div className={styles.statLabel}>tool calls</div>
          </div>
          {stats.failCount > 0 && (
            <div className={`${styles.stat} ${styles.statFail}`}>
              <div className={styles.statValue}>{stats.failCount}</div>
              <div className={styles.statLabel}>failures</div>
            </div>
          )}
        </section>

        {nativesRoot && (
          <section className={styles.pathCard}>
            <div className={styles.pathLabel}>Output</div>
            <code className={styles.pathValue} title={nativesRoot}>
              {nativesRoot}
            </code>
            <div className={styles.pathHint}>
              Drop the <code>natives/</code> folder into your MHWs mod manager.
            </div>
          </section>
        )}

        <div className={styles.nextSteps}>
          <div className={styles.nextHead}>Next</div>
          <ul className={styles.nextList}>
            <li>Test in-game and verify the mesh is rigged correctly.</li>
            <li>Check physics chains on hair / cloth at extreme poses.</li>
            <li>To run another mod, type a new model path in chat below.</li>
          </ul>
        </div>
      </aside>
    </div>
  );
}
