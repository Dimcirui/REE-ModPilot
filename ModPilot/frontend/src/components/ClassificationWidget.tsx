import { useMemo, useState, type FormEvent } from 'react';
import { motion } from 'motion/react';
import type { WidgetClassificationEvent } from '@/types/sse';
import type { ChainHead } from '@/types/domain';
import type { ClassificationWidgetSubmit } from '@/types/api';
import styles from './ClassificationWidget.module.css';

interface ClassificationWidgetProps {
  event: WidgetClassificationEvent;
  onSubmit: (payload: ClassificationWidgetSubmit['confirmations'], summary: string) => void;
}

interface RowState {
  inferred_type: string;
  description: string;
  merge_to_parent: boolean;
  override: boolean; // accepted-chip (false) vs override panel (true)
}

type ChainGroup = ChainHead['group'];

const GROUP_LABELS: Record<ChainGroup, string> = {
  hair: 'Hair 头发',
  cloth: 'Cloth / Skirt 布料',
  ribbon: 'Ribbon / Belt 飘带',
  tail: 'Tail 尾巴',
  non_physics: 'Non-Physics 非物理骨',
  other: 'Other 其他',
};

const GROUP_ORDER: ChainGroup[] = ['hair', 'cloth', 'ribbon', 'tail', 'non_physics', 'other'];

function initialRows(chains: ChainHead[]): Record<string, RowState> {
  const out: Record<string, RowState> = {};
  for (const c of chains) {
    out[c.name] = {
      inferred_type: c.suggested_type || '',
      description: '',
      merge_to_parent: !!c.suggest_merge,
      override: false,
    };
  }
  return out;
}

export function ClassificationWidget({ event, onSubmit }: ClassificationWidgetProps) {
  const [rows, setRows] = useState<Record<string, RowState>>(() => initialRows(event.chains));
  const [pending, setPending] = useState(false);

  const grouped = useMemo(() => {
    const groups: Record<ChainGroup, ChainHead[]> = {
      hair: [],
      cloth: [],
      ribbon: [],
      tail: [],
      non_physics: [],
      other: [],
    };
    for (const ch of event.chains) {
      (groups[ch.group] ?? groups.other).push(ch);
    }
    return groups;
  }, [event.chains]);

  const setRow = <K extends keyof RowState>(name: string, key: K, value: RowState[K]) => {
    setRows((prev) => ({ ...prev, [name]: { ...prev[name], [key]: value } }));
  };

  const handleSubmit = (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    if (pending) return;
    const confirmations = event.chains.map((c) => ({
      chain_name: c.name,
      inferred_type: rows[c.name]?.inferred_type ?? '',
      description: rows[c.name]?.description ?? '',
      merge_to_parent: rows[c.name]?.merge_to_parent ?? false,
    }));
    const filled = confirmations.filter((c) => c.inferred_type.trim() !== '').length;
    const summary = `[Confirmed ${filled}/${confirmations.length} rows]`;
    setPending(true);
    onSubmit(confirmations, summary);
  };

  return (
    <motion.form
      className={`${styles.form} ${pending ? styles.pending : ''}`}
      onSubmit={handleSubmit}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.2 }}
    >
      <header className={styles.header}>
        <h3>Confirm physics chain classifications</h3>
        <span className={styles.hint}>
          Click ✏ to override the agent's chip. Check &ldquo;合并到父级&rdquo; for chains that
          should merge into the parent before physics setup.
        </span>
      </header>

      {GROUP_ORDER.map((group) => {
        const chains = grouped[group];
        if (chains.length === 0) return null;
        return (
          <details key={group} className={styles.group} open>
            <summary className={styles.groupHeader}>
              {GROUP_LABELS[group]} ({chains.length})
            </summary>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Chain head</th>
                  <th>Role</th>
                  <th>Depth</th>
                  <th>猜测种类</th>
                  <th>Inferred type</th>
                  <th>合并到父级</th>
                </tr>
              </thead>
              <tbody>
                {chains.map((ch) => {
                  const row = rows[ch.name];
                  return (
                    <tr key={ch.name}>
                      <td className={styles.boneName}>{ch.name}</td>
                      <td>{ch.role}</td>
                      <td>{ch.depth}</td>
                      <td>{ch.guessed_nature || '—'}</td>
                      <td className={styles.inferredCell}>
                        {!row.override ? (
                          <div className={styles.accept}>
                            <span
                              className={`${styles.chip} ${
                                !row.inferred_type ? styles.chipEmpty : ''
                              }`}
                            >
                              {row.inferred_type || '—'}
                            </span>
                            <button
                              type="button"
                              className={styles.overrideBtn}
                              title="手动覆盖"
                              onClick={() => setRow(ch.name, 'override', true)}
                            >
                              ✏
                            </button>
                          </div>
                        ) : (
                          <div className={styles.override}>
                            <select
                              value={row.inferred_type}
                              onChange={(e) =>
                                setRow(ch.name, 'inferred_type', e.target.value)
                              }
                            >
                              <option value="">—</option>
                              {event.inferred_types.map((t) => (
                                <option key={t} value={t}>
                                  {t}
                                </option>
                              ))}
                            </select>
                            <textarea
                              value={row.description}
                              onChange={(e) =>
                                setRow(ch.name, 'description', e.target.value)
                              }
                              rows={2}
                              placeholder="或描述物感…"
                            />
                            <button
                              type="button"
                              className={styles.cancelBtn}
                              onClick={() => {
                                setRow(ch.name, 'override', false);
                                setRow(ch.name, 'inferred_type', ch.suggested_type || '');
                                setRow(ch.name, 'description', '');
                              }}
                            >
                              ✕ 取消
                            </button>
                          </div>
                        )}
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          checked={row.merge_to_parent}
                          onChange={(e) =>
                            setRow(ch.name, 'merge_to_parent', e.target.checked)
                          }
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </details>
        );
      })}

      <div className={styles.actions}>
        <button type="submit" className={styles.submitBtn} disabled={pending}>
          {pending ? 'Submitted — waiting for agent…' : 'Confirm classifications'}
        </button>
      </div>
    </motion.form>
  );
}
