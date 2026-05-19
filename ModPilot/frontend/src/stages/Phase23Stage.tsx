import { useMemo } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { ViewportPane } from '@/components/ViewportPane';
import { ResizeHandle } from '@/components/ResizeHandle';
import { ErrorChoice } from '@/components/ErrorChoice';
import type { ToolRun } from '@/hooks/useChatState';
import type { StageProps } from './types';
import styles from './Phase23Stage.module.css';

const SKELETON_TOOL = 'skeleton_align';
const VG_TOOL = 'vertex_groups';

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

// Heuristics to surface the most-load-bearing numbers from each phase's
// tool-result summary. Backend currently returns free-text; replace with a
// structured field once exposed.
const VG_MERGED_RE = /(?:merged|merged_mesh|merge[d]?\s+into)[:=\s]+([A-Za-z0-9_.\-]+)/i;
const VG_VG_COUNT_RE = /(\d+)\s+vertex\s+groups?/i;
const VG_VERTS_RE = /(\d+)\s+verts?/i;

function parseVgSummary(s: string | undefined): {
  merged: string | null;
  vgCount: number | null;
  verts: number | null;
} {
  if (!s) return { merged: null, vgCount: null, verts: null };
  const m1 = VG_MERGED_RE.exec(s);
  const m2 = VG_VG_COUNT_RE.exec(s);
  const m3 = VG_VERTS_RE.exec(s);
  return {
    merged: m1 ? m1[1] : null,
    vgCount: m2 ? Number.parseInt(m2[1], 10) : null,
    verts: m3 ? Number.parseInt(m3[1], 10) : null,
  };
}

export function Phase23Stage({ state, onErrorChoice }: StageProps) {
  const phaseRuns = useMemo(
    () => state.toolRuns.filter((r) => r.phase === 'phase_2' || r.phase === 'phase_3'),
    [state.toolRuns],
  );

  const skeletonRun = useMemo(() => latest(phaseRuns, SKELETON_TOOL), [phaseRuns]);
  const vgRun = useMemo(() => latest(phaseRuns, VG_TOOL), [phaseRuns]);

  const skeletonStatus = runState(skeletonRun);
  const vgStatus = runState(vgRun);

  const phase2Status = state.phaseStatus.phase_2;
  const phase3Status = state.phaseStatus.phase_3;

  // Which phase is currently "the focus" — used for the eyebrow / title.
  const focusPhase: 'phase_2' | 'phase_3' =
    phase3Status === 'active' || phase2Status === 'done' ? 'phase_3' : 'phase_2';

  const xPreset = (skeletonRun?.input?.x_preset as string | undefined) ?? null;
  const yPreset = (skeletonRun?.input?.y_preset as string | undefined) ?? '怪猎荒野';
  const sourceArm = (skeletonRun?.input?.source_armature as string | undefined) ?? null;
  const targetArm = (skeletonRun?.input?.target_armature as string | undefined) ?? null;

  const meshCount =
    (vgRun?.input?.mesh_objects as unknown[] | undefined)?.length ?? null;
  const vgSummary = parseVgSummary(vgRun?.summary);

  return (
    <div className={styles.stage}>
      <section className={styles.canvas}>
        <ViewportPane />
      </section>

      <ResizeHandle storageKey="canvas" />

      <aside className={styles.sidebar} aria-label="Phase 2 / 3 details">
        <header className={styles.header}>
          <div className={styles.eyebrow}>
            {focusPhase === 'phase_2' ? 'Phase 2' : 'Phase 3'}
          </div>
          <h2 className={styles.title}>
            {focusPhase === 'phase_2' ? 'Skeleton Align' : 'Vertex Groups'}
          </h2>
          <span
            className={`${styles.phaseChip} ${styles[`chip_${focusPhase === 'phase_2' ? phase2Status : phase3Status}`]}`}
            title={`Phase status: ${focusPhase === 'phase_2' ? phase2Status : phase3Status}`}
          >
            {focusPhase === 'phase_2' ? phase2Status : phase3Status}
          </span>
        </header>

        {/* Phase 2 — Skeleton align */}
        <section className={`${styles.card} ${styles[`card_${skeletonStatus}`]}`}>
          <div className={styles.cardHead}>
            <span className={styles.cardLabel}>Skeleton align (P2)</span>
            <span className={styles.cardStatus}>{skeletonStatus}</span>
          </div>
          {skeletonStatus === 'pending' && (
            <div className={styles.muted}>Awaiting <code>skeleton_align</code>…</div>
          )}
          {skeletonStatus === 'running' && (
            <div className={styles.muted}>Snapping rigs together…</div>
          )}
          {(skeletonStatus === 'ok' || skeletonStatus === 'fail') && (
            <>
              {xPreset && (
                <div className={styles.presetRow}>
                  <span className={styles.presetTag}>{xPreset}</span>
                  <span className={styles.presetArrow}>→</span>
                  <span className={styles.presetTag}>{yPreset}</span>
                </div>
              )}
              {(sourceArm || targetArm) && (
                <div className={styles.armRow}>
                  <div className={styles.armCol}>
                    <span className={styles.armLabel}>source</span>
                    <code className={styles.armName}>{sourceArm ?? '—'}</code>
                  </div>
                  <div className={styles.armCol}>
                    <span className={styles.armLabel}>target</span>
                    <code className={styles.armName}>{targetArm ?? '—'}</code>
                  </div>
                </div>
              )}
              {skeletonRun?.summary && (
                <div className={styles.summary} title={skeletonRun.summary}>
                  {skeletonRun.summary.slice(0, 200)}
                </div>
              )}
            </>
          )}
        </section>

        {/* Phase 3 — Vertex groups */}
        <section className={`${styles.card} ${styles[`card_${vgStatus}`]}`}>
          <div className={styles.cardHead}>
            <span className={styles.cardLabel}>Vertex groups (P3)</span>
            <span className={styles.cardStatus}>{vgStatus}</span>
          </div>
          {vgStatus === 'pending' && (
            <div className={styles.muted}>
              {skeletonStatus === 'ok'
                ? 'Ready — awaiting vertex group conversion…'
                : 'Runs after skeleton align.'}
            </div>
          )}
          {vgStatus === 'running' && (
            <div className={styles.muted}>
              Merging meshes &amp; renaming vertex groups…
            </div>
          )}
          {(vgStatus === 'ok' || vgStatus === 'fail') && (
            <>
              <div className={styles.statRow}>
                {meshCount !== null && (
                  <div className={styles.stat}>
                    <div className={styles.statValue}>{meshCount}</div>
                    <div className={styles.statLabel}>meshes merged</div>
                  </div>
                )}
                {vgSummary.vgCount !== null && (
                  <div className={styles.stat}>
                    <div className={styles.statValue}>{vgSummary.vgCount}</div>
                    <div className={styles.statLabel}>vertex groups</div>
                  </div>
                )}
                {vgSummary.verts !== null && (
                  <div className={styles.stat}>
                    <div className={styles.statValue}>
                      {vgSummary.verts.toLocaleString()}
                    </div>
                    <div className={styles.statLabel}>verts</div>
                  </div>
                )}
              </div>
              {vgSummary.merged && (
                <div className={styles.mergedRow}>
                  <span className={styles.mergedLabel}>merged →</span>
                  <code className={styles.mergedName}>{vgSummary.merged}</code>
                </div>
              )}
              {vgRun?.summary && (
                <div className={styles.summary} title={vgRun.summary}>
                  {vgRun.summary.slice(0, 200)}
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
