import { useMemo } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { ViewportPane } from '@/components/ViewportPane';
import { ErrorChoice } from '@/components/ErrorChoice';
import type { ToolRun } from '@/hooks/useChatState';
import type { StageProps } from './types';
import styles from './Phase6Stage.module.css';

const EXPORT_TOOL = 'batch_export';

const PART_NAMES: Record<string, string> = {
  '1': 'Arms',
  '2': 'Body',
  '3': 'Helmet',
  '4': 'Legs',
  '5': 'Waist',
};

type RunState = 'pending' | 'running' | 'ok' | 'fail';

function runState(run: ToolRun | null): RunState {
  if (!run) return 'pending';
  if (run.finishedAt === undefined) return 'running';
  return run.success ? 'ok' : 'fail';
}

function latest(runs: ToolRun[], name: string): ToolRun | null {
  const filtered = runs.filter((r) => r.name === name);
  return filtered.length > 0 ? filtered[filtered.length - 1] : null;
}

// Parse byte counts / file counts from the batch_export tool summary. Backend
// returns free-text; we pluck the most useful numbers but degrade gracefully.
const FILES_RE = /(\d+)\s+files?\b/i;
const BYTES_RE = /(\d+(?:\.\d+)?)\s*(KB|MB|GB)\b/i;
const PATH_RE = /natives?(?:_root)?[:=\s]+([A-Za-z]:[\\/][^\s\n]+|\/[^\s\n]+)/i;

export function Phase6Stage({ state, onErrorChoice }: StageProps) {
  const phaseRuns = useMemo(
    () => state.toolRuns.filter((r) => r.phase === 'phase_6'),
    [state.toolRuns],
  );

  const exportRun = useMemo(() => latest(phaseRuns, EXPORT_TOOL), [phaseRuns]);
  const status = runState(exportRun);
  const phaseStatus = state.phaseStatus.phase_6;

  const armorId = (exportRun?.input?.armor_id as string | undefined) ?? null;
  const armorVariant = (exportRun?.input?.armor_variant as string | undefined) ?? null;
  const targetParts =
    (exportRun?.input?.target_parts as string[] | undefined) ?? [];
  const nativesRoot = (exportRun?.input?.natives_root as string | undefined) ?? null;

  const summary = exportRun?.summary ?? '';
  const filesMatch = FILES_RE.exec(summary);
  const fileCount = filesMatch ? Number.parseInt(filesMatch[1], 10) : null;
  const sizeMatch = BYTES_RE.exec(summary);
  const size = sizeMatch ? `${sizeMatch[1]} ${sizeMatch[2]}` : null;
  const pathMatch = PATH_RE.exec(summary);
  const reportedPath = pathMatch ? pathMatch[1] : nativesRoot;

  return (
    <div className={styles.stage}>
      <section className={styles.canvas}>
        <ViewportPane />
      </section>

      <aside className={styles.sidebar} aria-label="Phase 6 details">
        <header className={styles.header}>
          <div className={styles.eyebrow}>Phase 6</div>
          <h2 className={styles.title}>Batch Export</h2>
          <span
            className={`${styles.phaseChip} ${styles[`chip_${phaseStatus}`]}`}
            title={`Phase status: ${phaseStatus}`}
          >
            {phaseStatus}
          </span>
        </header>

        <section className={`${styles.card} ${styles[`card_${status}`]}`}>
          <div className={styles.cardHead}>
            <span className={styles.cardLabel}>Export</span>
            <span className={styles.cardStatus}>{status}</span>
          </div>

          {status === 'pending' && (
            <div className={styles.muted}>
              Awaiting <code>batch_export</code>… mesh / mdf2 / chain2 / bonesystem
              will write to <code>natives/</code> in one operator call.
            </div>
          )}
          {status === 'running' && (
            <div className={styles.muted}>
              Writing mesh / mdf2 / chain2 + running BoneSystem…
            </div>
          )}

          {(status === 'ok' || status === 'fail') && (
            <>
              {(armorId || armorVariant) && (
                <div className={styles.armorRow}>
                  {armorId && (
                    <span className={styles.armorTag}>
                      <span className={styles.tagLabel}>id</span>
                      <code className={styles.tagValue}>{armorId}</code>
                    </span>
                  )}
                  {armorVariant && (
                    <span className={styles.variantTag} title="hunter / armor">
                      {armorVariant}
                    </span>
                  )}
                </div>
              )}

              {targetParts.length > 0 && (
                <div className={styles.parts}>
                  {(['1', '2', '3', '4', '5'] as const).map((p) => {
                    const on = targetParts.includes(p);
                    return (
                      <div
                        key={p}
                        className={`${styles.partChip} ${on ? styles.partOn : styles.partOff}`}
                        title={PART_NAMES[p]}
                      >
                        <span className={styles.partNum}>{p}</span>
                        <span className={styles.partName}>{PART_NAMES[p]}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {(fileCount !== null || size) && (
                <div className={styles.statRow}>
                  {fileCount !== null && (
                    <div className={styles.stat}>
                      <div className={styles.statValue}>{fileCount}</div>
                      <div className={styles.statLabel}>files</div>
                    </div>
                  )}
                  {size && (
                    <div className={styles.stat}>
                      <div className={styles.statValue}>{size}</div>
                      <div className={styles.statLabel}>written</div>
                    </div>
                  )}
                </div>
              )}

              {reportedPath && (
                <div className={styles.pathRow}>
                  <span className={styles.pathLabel}>natives →</span>
                  <code className={styles.pathValue} title={reportedPath}>
                    {reportedPath}
                  </code>
                </div>
              )}

              {summary && (
                <div className={styles.summary} title={summary}>
                  {summary.slice(0, 240)}
                </div>
              )}
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
