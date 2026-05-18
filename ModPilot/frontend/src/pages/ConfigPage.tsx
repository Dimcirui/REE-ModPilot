import { useEffect, useState, type FormEvent } from 'react';
import { LLM_PROVIDERS, type LlmProvider } from '@/types/domain';
import { api } from '@/lib/api';
import styles from './ConfigPage.module.css';

interface ConfigFormState {
  llm_provider: LlmProvider;
  llm_api_key: string;
  llm_model: string;
  llm_base_url: string;
  blender_host: string;
  blender_port: number;
}

const DEFAULTS: ConfigFormState = {
  llm_provider: 'openai_compatible',
  llm_api_key: '',
  llm_model: '',
  llm_base_url: '',
  blender_host: '127.0.0.1',
  blender_port: 9876,
};

const PROVIDER_LABELS: Record<LlmProvider, string> = {
  openai_compatible: 'OpenAI-compatible (DeepSeek / Qwen / …)',
  anthropic: 'Anthropic (Claude)',
  ollama: 'Ollama (Cloud or local daemon)',
};

export default function ConfigPage() {
  const [form, setForm] = useState<ConfigFormState>(DEFAULTS);
  const [hasKey, setHasKey] = useState(false);
  const [saveState, setSaveState] = useState<
    { kind: 'idle' } | { kind: 'saving' } | { kind: 'ok'; message: string } | { kind: 'error'; message: string }
  >({ kind: 'idle' });

  useEffect(() => {
    api.getAppConfig().then(
      (cfg) => {
        setForm({
          llm_provider: cfg.llm_provider,
          // Don't populate the password field with the mask "***" — leave it
          // blank so the user knows typing is optional. has_api_key drives the
          // "key configured" note.
          llm_api_key: '',
          llm_model: cfg.llm_model,
          llm_base_url: cfg.llm_base_url,
          blender_host: cfg.blender_host,
          blender_port: cfg.blender_port,
        });
        setHasKey(cfg.has_api_key);
      },
      (err) =>
        setSaveState({
          kind: 'error',
          message: err instanceof Error ? err.message : 'Failed to load settings',
        }),
    );
  }, []);

  const setField = <K extends keyof ConfigFormState>(key: K, value: ConfigFormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = async (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    setSaveState({ kind: 'saving' });
    try {
      const res = await api.saveAppConfig(form);
      const statusBits: string[] = [];
      if (res.status.llm.startsWith('error')) {
        statusBits.push(`LLM: ${res.status.llm}`);
      }
      if (res.status.blender !== 'unchanged') {
        statusBits.push(`Blender: ${res.status.blender}`);
      }
      const tail = statusBits.length ? ` (${statusBits.join(', ')})` : '';
      setSaveState({ kind: 'ok', message: `Saved.${tail}` });
      if (form.llm_api_key) setHasKey(true);
    } catch (err) {
      setSaveState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'Save failed',
      });
    }
  };

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1>ModPilot</h1>
        <span className={styles.subtitle}>Global Settings</span>
      </header>

      <main className={styles.main}>
        <section className={styles.card}>
          <h2>LLM &amp; Blender</h2>
          <p className={styles.hint}>
            These settings persist across sessions in <code>~/.modpilot/config.json</code>.
            Per-mod parameters (paths, character name, etc.) live on the chat page.
          </p>

          <form onSubmit={handleSubmit}>
            <fieldset className={styles.fieldset}>
              <legend>LLM</legend>
              <label>
                <span>Provider</span>
                <select
                  value={form.llm_provider}
                  onChange={(e) => setField('llm_provider', e.target.value as LlmProvider)}
                  required
                >
                  {LLM_PROVIDERS.map((p) => (
                    <option key={p} value={p}>
                      {PROVIDER_LABELS[p]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>API key</span>
                <input
                  type="password"
                  value={form.llm_api_key}
                  onChange={(e) => setField('llm_api_key', e.target.value)}
                  autoComplete="new-password"
                  placeholder={hasKey ? 'leave blank to keep existing key' : 'paste key'}
                />
                <span
                  className={`${styles.fieldNote} ${
                    hasKey ? styles.fieldNoteOk : styles.fieldNoteWarn
                  }`}
                >
                  {hasKey ? 'Key configured (kept on save unless replaced)' : 'No key yet'}
                </span>
              </label>
              <label>
                <span>Model</span>
                <input
                  type="text"
                  value={form.llm_model}
                  onChange={(e) => setField('llm_model', e.target.value)}
                  placeholder="claude-sonnet-4-5 / deepseek-chat / deepseek-v4-flash / …"
                  required
                />
              </label>
              <label>
                <span>Base URL (optional)</span>
                <input
                  type="text"
                  value={form.llm_base_url}
                  onChange={(e) => setField('llm_base_url', e.target.value)}
                  placeholder="https://api.deepseek.com/v1  |  https://ollama.com (default for ollama)"
                />
              </label>
            </fieldset>

            <fieldset className={styles.fieldset}>
              <legend>Blender</legend>
              <label>
                <span>Host</span>
                <input
                  type="text"
                  value={form.blender_host}
                  onChange={(e) => setField('blender_host', e.target.value)}
                  required
                />
              </label>
              <label>
                <span>Port</span>
                <input
                  type="number"
                  value={form.blender_port}
                  min={1}
                  max={65535}
                  onChange={(e) => setField('blender_port', Number(e.target.value))}
                  required
                />
              </label>
            </fieldset>

            <div className={styles.actions}>
              <button
                type="submit"
                className={styles.saveBtn}
                disabled={saveState.kind === 'saving'}
              >
                {saveState.kind === 'saving' ? 'Saving…' : 'Save'}
              </button>
              <a href="/" className={styles.backLink}>
                Back to chat →
              </a>
              <div
                className={`${styles.saveStatus} ${
                  saveState.kind === 'ok'
                    ? styles.saveStatusOk
                    : saveState.kind === 'error'
                      ? styles.saveStatusError
                      : ''
                }`}
                role="status"
              >
                {saveState.kind === 'ok' || saveState.kind === 'error'
                  ? saveState.message
                  : ''}
              </div>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
