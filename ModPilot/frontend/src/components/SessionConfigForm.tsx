import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { ARMOR_VARIANTS, type ArmorSet, type XPreset } from '@/types/domain';
import type { SessionConfig } from '@/types/api';
import type { ModelTypeInferredEvent } from '@/types/sse';
import { api, asSessionConfigFieldErrors } from '@/lib/api';
import { PathField } from './PathField';
import { ToolkitStatusPanel } from './ToolkitStatusPanel';
import styles from './SessionConfigForm.module.css';

const MODEL_FILE_FILTERS = [
  { name: 'Model files', extensions: ['fbx', 'pmx'] },
  { name: 'All files', extensions: ['*'] },
];

const STORAGE_KEY = 'modpilot.config.v1';

const REQUIRED_TEXT_FIELDS: (keyof SessionConfig)[] = [
  'model_path',
  'model_type',
  'texture_dir',
  'mod_root',
  'author',
  'character_name',
  'armor_id',
];

type BodyPartValue = '1' | '2' | '3' | '4' | '5';

const BODY_PARTS: ReadonlyArray<{ value: BodyPartValue; label: string }> = [
  { value: '1', label: 'Arms (1)' },
  { value: '2', label: 'Body (2)' },
  { value: '3', label: 'Helmet (3)' },
  { value: '4', label: 'Legs (4)' },
  { value: '5', label: 'Waist (5)' },
];

const DEFAULT_BODY_PART: BodyPartValue = '2';

const VARIANT_LABELS: Record<(typeof ARMOR_VARIANTS)[number], string> = {
  ff: 'Female / Female armor (default)',
  fm: 'Female / Male armor',
  mf: 'Male / Female armor',
  mm: 'Male / Male armor',
};

const DEFAULT_CONFIG: SessionConfig = {
  model_path: '',
  model_type: 'Auto-detect',
  texture_dir: '',
  mod_root: '',
  author: '',
  character_name: '',
  use_bone_system: false,
  body_parts: [DEFAULT_BODY_PART],
  armor_variant: 'ff',
  armor_id: '',
};

function rehydrate(): SessionConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_CONFIG;
    const parsed = JSON.parse(raw) as Partial<SessionConfig>;
    const merged = { ...DEFAULT_CONFIG, ...parsed };
    // Body parts is now a single-pick — collapse legacy multi-select state to first value, default to Body.
    const first = merged.body_parts?.[0];
    merged.body_parts = [first ?? DEFAULT_BODY_PART];
    return merged;
  } catch {
    return DEFAULT_CONFIG;
  }
}

interface SessionConfigFormProps {
  sessionId: string;
  inferredModelType: ModelTypeInferredEvent | null;
}

export function SessionConfigForm({ sessionId, inferredModelType }: SessionConfigFormProps) {
  const [config, setConfig] = useState<SessionConfig>(rehydrate);
  const [xPresets, setXPresets] = useState<XPreset[]>([]);
  const [armorSets, setArmorSets] = useState<ArmorSet[]>([]);
  const [locked, setLocked] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof SessionConfig, string>>>({});
  const [generalError, setGeneralError] = useState('');
  const [saving, setSaving] = useState(false);
  // null = unknown (loading / Blender unreachable). We only block Start on an
  // explicit false (probe came back and a critical tool was missing/disabled).
  const [toolkitOk, setToolkitOk] = useState<boolean | null>(null);

  // ── load catalogs on mount ────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    api.getXPresets().then(
      (r) => !cancelled && setXPresets(r.presets ?? []),
      () => {
        // Offline / 503 — Auto-detect option still works
      },
    );
    api.getArmorSets().then(
      (r) => !cancelled && setArmorSets(r.armor_sets ?? []),
      () => {
        // Offline — user must hand-type via localStorage rehydrate
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // ── back-fill model_type from SSE inference ───────────────────────────
  useEffect(() => {
    if (!inferredModelType) return;
    setConfig((prev) => ({ ...prev, model_type: inferredModelType.preset }));
  }, [inferredModelType]);

  const setField = useCallback(
    <K extends keyof SessionConfig>(key: K, value: SessionConfig[K]) => {
      setConfig((prev) => ({ ...prev, [key]: value }));
      setFieldErrors((prev) => {
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      });
    },
    [],
  );

  const selectBodyPart = useCallback((value: BodyPartValue) => {
    setConfig((prev) => ({ ...prev, body_parts: [value] }));
  }, []);

  const isComplete = useMemo(() => {
    for (const k of REQUIRED_TEXT_FIELDS) {
      const v = config[k];
      if (typeof v !== 'string' || v.trim() === '') return false;
    }
    return config.body_parts.length > 0;
  }, [config]);

  const handleSubmit = async (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    if (!isComplete || saving) return;
    setSaving(true);
    setGeneralError('');
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
    } catch {
      // quota / blocked — non-fatal
    }
    try {
      await api.saveSessionConfig({ session_id: sessionId, config });
      setFieldErrors({});
      setLocked(true);
    } catch (err) {
      const fe = asSessionConfigFieldErrors(err);
      if (fe?.field_errors) {
        setFieldErrors(fe.field_errors);
      } else {
        setGeneralError(err instanceof Error ? err.message : 'Save failed');
      }
    } finally {
      setSaving(false);
    }
  };

  const inferredNote = useMemo(() => {
    if (!inferredModelType) return null;
    const pct = Math.round((inferredModelType.coverage ?? 0) * 100);
    const tag =
      inferredModelType.decision === 'exact'
        ? 'matched'
        : inferredModelType.decision === 'supplement'
          ? 'partial — supplement'
          : inferredModelType.decision === 'custom'
            ? 'low — needs custom'
            : 'unsupported';
    return `Detected: ${inferredModelType.preset} (${pct}% — ${tag})`;
  }, [inferredModelType]);

  if (locked) {
    return (
      <motion.section
        className={styles.section}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.2 }}
      >
        <div className={styles.savedBadge}>
          <span>Config saved ✓</span>
          <button
            type="button"
            className={styles.editButton}
            onClick={() => setLocked(false)}
          >
            Edit
          </button>
        </div>
      </motion.section>
    );
  }

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.section
        key="form"
        className={styles.section}
        initial={{ opacity: 0, height: 0 }}
        animate={{ opacity: 1, height: 'auto' }}
        exit={{ opacity: 0, height: 0 }}
        transition={{ duration: 0.2 }}
      >
        <div className={styles.header}>
          <h2 className={styles.title}>Session Config</h2>
          <span className={styles.hint}>
            Save this before sending your first message — values are injected into the agent's
            system prompt.
          </span>
        </div>

        <ToolkitStatusPanel onStatusChange={setToolkitOk} />

        <form onSubmit={handleSubmit}>
          <fieldset className={styles.fieldset}>
            <legend>Source Model</legend>
            <Field
              label="Model path"
              error={fieldErrors.model_path}
            >
              <PathField
                value={config.model_path}
                onChange={(v) => setField('model_path', v)}
                kind="file"
                filters={MODEL_FILE_FILTERS}
                placeholder="C:\path\to\model.fbx or .pmx"
                invalid={!!fieldErrors.model_path}
                required
              />
            </Field>
            <Field
              label="Model type"
              error={fieldErrors.model_type}
              note={inferredNote ?? undefined}
              noteTone={inferredModelType?.decision === 'exact' ? 'ok' : 'warn'}
            >
              <select
                value={config.model_type}
                onChange={(e) => setField('model_type', e.target.value)}
                required
              >
                <option value="Auto-detect">Auto-detect</option>
                {xPresets.map((p) => (
                  <option key={p.name} value={p.name} title={p.description}>
                    {p.name}
                  </option>
                ))}
                {inferredModelType &&
                  !xPresets.some((p) => p.name === inferredModelType.preset) && (
                    <option value={inferredModelType.preset}>
                      {inferredModelType.preset}
                    </option>
                  )}
              </select>
            </Field>
            <Field label="Texture directory" error={fieldErrors.texture_dir}>
              <PathField
                value={config.texture_dir}
                onChange={(v) => setField('texture_dir', v)}
                kind="directory"
                placeholder="C:\path\to\textures"
                invalid={!!fieldErrors.texture_dir}
                required
              />
            </Field>
          </fieldset>

          <fieldset className={styles.fieldset}>
            <legend>Mod Output</legend>
            <Field label="Mod root" error={fieldErrors.mod_root}>
              <PathField
                value={config.mod_root}
                onChange={(v) => setField('mod_root', v)}
                kind="directory"
                placeholder="C:\path\to\mod_root"
                invalid={!!fieldErrors.mod_root}
                required
              />
            </Field>
            <Field label="Author" error={fieldErrors.author}>
              <input
                type="text"
                value={config.author}
                onChange={(e) => setField('author', e.target.value)}
                placeholder="AuthorName"
                required
              />
            </Field>
            <Field label="Character name" error={fieldErrors.character_name}>
              <input
                type="text"
                value={config.character_name}
                onChange={(e) => setField('character_name', e.target.value)}
                placeholder="CharacterName"
                required
              />
            </Field>
          </fieldset>

          <fieldset className={styles.fieldset}>
            <legend>Batch Export</legend>
            <label className={styles.inline}>
              <input
                type="checkbox"
                checked={config.use_bone_system}
                onChange={(e) => setField('use_bone_system', e.target.checked)}
              />
              <span>Use BoneSystem</span>
            </label>

            <fieldset className={`${styles.fieldset} ${styles.bodyParts}`}>
              <legend>Body part</legend>
              {BODY_PARTS.map((p) => (
                <label key={p.value} className={styles.inline}>
                  <input
                    type="radio"
                    name="body_part"
                    value={p.value}
                    checked={config.body_parts[0] === p.value}
                    onChange={() => selectBodyPart(p.value)}
                  />
                  <span>{p.label}</span>
                </label>
              ))}
            </fieldset>

            <fieldset className={`${styles.fieldset} ${styles.variant}`}>
              <legend>Hunter type</legend>
              {ARMOR_VARIANTS.map((v) => (
                <label key={v} className={styles.inline}>
                  <input
                    type="radio"
                    name="armor_variant"
                    value={v}
                    checked={config.armor_variant === v}
                    onChange={() => setField('armor_variant', v)}
                  />
                  <span>{VARIANT_LABELS[v]}</span>
                </label>
              ))}
            </fieldset>

            <Field label="Equipment (armor id)" error={fieldErrors.armor_id}>
              <select
                value={config.armor_id}
                onChange={(e) => setField('armor_id', e.target.value)}
                required
              >
                <option value="">— pick one —</option>
                {armorSets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.id} — {a.name}
                  </option>
                ))}
              </select>
            </Field>
          </fieldset>

          <div className={styles.actions}>
            <button
              type="submit"
              className={styles.startButton}
              disabled={!isComplete || saving || toolkitOk === false}
              title={
                toolkitOk === false
                  ? 'A required Blender addon is missing or disabled. Fix and re-check above.'
                  : undefined
              }
            >
              {saving ? 'Saving…' : 'Start'}
            </button>
            {generalError && <div className={styles.generalError}>{generalError}</div>}
          </div>
        </form>
      </motion.section>
    </AnimatePresence>
  );
}

interface FieldProps {
  label: string;
  error?: string;
  note?: string;
  noteTone?: 'ok' | 'warn';
  children: React.ReactNode;
}

function Field({ label, error, note, noteTone, children }: FieldProps) {
  return (
    <label className={styles.field}>
      <span className={styles.fieldLabel}>{label}</span>
      {children}
      {note && (
        <span
          className={`${styles.fieldNote} ${
            noteTone === 'ok' ? styles.fieldNoteOk : styles.fieldNoteWarn
          }`}
        >
          {note}
        </span>
      )}
      {error && <span className={styles.fieldError}>{error}</span>}
    </label>
  );
}
