import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import type { ToolStatus, ToolkitStatusResponse } from '@/types/api';
import styles from './ToolkitStatusPanel.module.css';

interface ToolkitStatusPanelProps {
  // Bubbled up so the parent form can gate its Start button.
  // null = state unknown (loading / Blender unreachable); treat as "don't block".
  onStatusChange?: (ok: boolean | null) => void;
}

type FetchState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ok'; data: ToolkitStatusResponse }
  | { kind: 'unreachable' }
  | { kind: 'error'; message: string };

const STATUS_LABEL: Record<ToolStatus['status'], string> = {
  present: 'OK',
  disabled: 'Disabled',
  missing: 'Missing',
};

const INSTALL_HINTS: Record<string, string> = {
  mbt: 'Install / enable Modding-Toolkit and restart Blender.',
  mhws: 'Install / enable the MHWs plugin and restart Blender.',
  re_mesh: 'Install / enable RE Mesh Editor and restart Blender.',
  re_chain: 'Install / enable RE Chain Editor and restart Blender.',
};

export function ToolkitStatusPanel({ onStatusChange }: ToolkitStatusPanelProps) {
  const [state, setState] = useState<FetchState>({ kind: 'idle' });

  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try {
      const data = await api.getToolkitStatus();
      setState({ kind: 'ok', data });
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setState({ kind: 'unreachable' });
      } else {
        setState({
          kind: 'error',
          message: err instanceof Error ? err.message : 'Toolkit check failed',
        });
      }
    }
  }, []);

  // Fetch on mount.
  useEffect(() => {
    load();
  }, [load]);

  // Bubble the gate state. null when we don't know yet so the parent can
  // decide whether to block; for now SessionConfigForm treats null as
  // "don't block" so an unreachable Blender still allows Save.
  useEffect(() => {
    if (!onStatusChange) return;
    if (state.kind === 'ok') onStatusChange(state.data.ok);
    else onStatusChange(null);
  }, [state, onStatusChange]);

  const expanded = state.kind === 'ok' && !state.data.ok;

  return (
    <section className={styles.panel} aria-label="Toolkit status">
      <header className={styles.header}>
        <span className={styles.title}>Blender toolkit</span>
        <Summary state={state} />
        <button
          type="button"
          className={styles.recheck}
          onClick={load}
          disabled={state.kind === 'loading'}
        >
          {state.kind === 'loading' ? 'Checking…' : 'Re-check'}
        </button>
      </header>

      {state.kind === 'unreachable' && (
        <div className={styles.note}>
          Blender isn't reachable yet. Start Blender (with the blender-mcp addon
          enabled), then re-check.
        </div>
      )}

      {state.kind === 'error' && (
        <div className={styles.note}>Couldn't reach the backend: {state.message}</div>
      )}

      {state.kind === 'ok' && (
        <ul className={styles.list}>
          {state.data.tools.map((t) => (
            <li key={t.id} className={styles.row} data-status={t.status}>
              <span className={`${styles.pill} ${styles[t.status]}`} aria-label={t.status}>
                {STATUS_LABEL[t.status]}
              </span>
              <span className={styles.toolLabel}>{t.label}</span>
              {expanded && t.status !== 'present' && (
                <span className={styles.hint}>
                  {INSTALL_HINTS[t.id] ?? 'Install / enable this addon in Blender.'}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Summary({ state }: { state: FetchState }) {
  if (state.kind === 'idle' || state.kind === 'loading') {
    return <span className={styles.summary}>Checking…</span>;
  }
  if (state.kind === 'unreachable') {
    return <span className={`${styles.summary} ${styles.summaryWarn}`}>Blender offline</span>;
  }
  if (state.kind === 'error') {
    return <span className={`${styles.summary} ${styles.summaryWarn}`}>Error</span>;
  }
  if (state.data.ok) {
    return <span className={`${styles.summary} ${styles.summaryOk}`}>All present</span>;
  }
  const broken = state.data.tools.filter((t) => t.status !== 'present').length;
  return (
    <span className={`${styles.summary} ${styles.summaryWarn}`}>
      {broken} tool{broken === 1 ? '' : 's'} missing or disabled
    </span>
  );
}
