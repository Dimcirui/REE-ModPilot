import { useMemo, useState, type FormEvent } from 'react';
import { motion } from 'motion/react';
import type { WidgetMaterialEvent } from '@/types/sse';
import type { MaterialSlotMapping } from '@/types/api';
import { CONNECTED_NO_IMAGE, PRINCIPLED_SLOTS, type PrincipledSlot } from '@/types/domain';
import styles from './MaterialWidget.module.css';

interface MaterialWidgetProps {
  event: WidgetMaterialEvent;
  onSubmit: (payload: MaterialSlotMapping[], summary: string) => void;
}

// Pre-fill precedence: LLM suggestion > existing wired (unless connected_no_image) > empty.
function pickInitial(
  mat: string,
  slot: PrincipledSlot,
  event: WidgetMaterialEvent,
): string {
  const suggested = event.suggestions[mat]?.[slot];
  if (suggested) return suggested;
  const existing = event.existing_connections[mat]?.[slot];
  if (existing && existing !== CONNECTED_NO_IMAGE) return existing;
  return '';
}

function basename(path: string): string {
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return i >= 0 ? path.slice(i + 1) : path;
}

export function MaterialWidget({ event, onSubmit }: MaterialWidgetProps) {
  const initial = useMemo(() => {
    const out: Record<string, Partial<Record<PrincipledSlot, string>>> = {};
    for (const mat of event.materials) {
      out[mat] = {};
      for (const slot of PRINCIPLED_SLOTS) {
        out[mat][slot] = pickInitial(mat, slot, event);
      }
    }
    return out;
  }, [event]);
  const [values, setValues] = useState(initial);
  const [pending, setPending] = useState(false);

  const setSlot = (mat: string, slot: PrincipledSlot, value: string) => {
    setValues((prev) => ({ ...prev, [mat]: { ...prev[mat], [slot]: value } }));
  };

  const handleSubmit = (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    if (pending) return;
    const mappings: MaterialSlotMapping[] = [];
    for (const mat of event.materials) {
      for (const slot of PRINCIPLED_SLOTS) {
        const path = values[mat]?.[slot] ?? '';
        if (path) mappings.push({ material: mat, slot, texture_path: path });
      }
    }
    setPending(true);
    onSubmit(mappings, `[Confirmed ${mappings.length} slot assignments]`);
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
        <h3>Confirm texture mapping</h3>
        <span className={styles.hint}>
          Rows marked <span className={styles.suggestedChip}>LLM 建议</span> are pre-filled by
          the agent — accept as-is or override per slot. Leave a slot blank to skip it.
        </span>
      </header>

      {event.materials.map((mat) => (
        <details key={mat} className={styles.materialBlock} open>
          <summary className={styles.materialHeader}>{mat}</summary>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Slot</th>
                <th>Texture</th>
              </tr>
            </thead>
            <tbody>
              {PRINCIPLED_SLOTS.map((slot) => {
                const suggested = event.suggestions[mat]?.[slot];
                const isSuggested = !!suggested;
                const value = values[mat]?.[slot] ?? '';
                return (
                  <tr
                    key={`${mat}::${slot}`}
                    className={isSuggested ? styles.rowSuggested : ''}
                  >
                    <td className={styles.slotName}>
                      {slot}
                      {isSuggested && (
                        <span className={styles.suggestedChip} title="Pre-filled by LLM">
                          LLM
                        </span>
                      )}
                    </td>
                    <td>
                      <select
                        value={value}
                        onChange={(e) => setSlot(mat, slot, e.target.value)}
                      >
                        <option value="">(none)</option>
                        {event.texture_files.map((tex) => (
                          <option key={tex} value={tex}>
                            {basename(tex)}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      ))}

      <div className={styles.actions}>
        <button type="submit" className={styles.submitBtn} disabled={pending}>
          {pending ? 'Submitted — waiting for agent…' : 'Confirm material mapping'}
        </button>
      </div>
    </motion.form>
  );
}
