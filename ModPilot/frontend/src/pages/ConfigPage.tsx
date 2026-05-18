import { useEffect, useState, type FormEvent } from 'react';
import { LLM_PROVIDERS, type LlmProvider } from '@/types/domain';
import { api, ApiError } from '@/lib/api';
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

// Known-good (model, base_url) per provider. Mirrors the server-side guardrail
// in app/main.py (_validate_provider_model_combo): if you change a default
// here, make sure the same combination still passes the backend check.
const PROVIDER_DEFAULTS: Record<LlmProvider, { llm_model: string; llm_base_url: string }> = {
  openai_compatible: { llm_model: 'deepseek-chat', llm_base_url: 'https://api.deepseek.com/v1' },
  anthropic:         { llm_model: 'claude-sonnet-4-5', llm_base_url: '' },
  ollama:            { llm_model: 'deepseek-v4-flash', llm_base_url: '' },
};

const _ALL_DEFAULT_MODELS = new Set(Object.values(PROVIDER_DEFAULTS).map((d) => d.llm_model));
const _ALL_DEFAULT_BASE_URLS = new Set(Object.values(PROVIDER_DEFAULTS).map((d) => d.llm_base_url));

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

  // Provider-aware reset: when the user switches Provider, swap Model + Base URL
  // to the new provider's defaults — but ONLY if the current values are
  // recognized defaults (or empty). A custom model the user typed manually is
  // preserved. Without this, the stale model from the previous provider gets
  // saved and the agent loop 404s at runtime (regression-protected by the
  // backend guardrail, but the UI swap avoids the 422 round-trip).
  const handleProviderChange = (next: LlmProvider) => {
    setForm((prev) => {
      const defaults = PROVIDER_DEFAULTS[next];
      const modelIsDefault = prev.llm_model === '' || _ALL_DEFAULT_MODELS.has(prev.llm_model);
      const baseUrlIsDefault = _ALL_DEFAULT_BASE_URLS.has(prev.llm_base_url);
      return {
        ...prev,
        llm_provider: next,
        llm_model: modelIsDefault ? defaults.llm_model : prev.llm_model,
        llm_base_url: baseUrlIsDefault ? defaults.llm_base_url : prev.llm_base_url,
      };
    });
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
      // 422 with field_errors → surface the specific message (e.g. the
      // provider/model guardrail in app/main.py) instead of a generic
      // "POST failed: 422".
      if (err instanceof ApiError && err.status === 422) {
        const body = err.body as { detail?: { field_errors?: Record<string, string> } } | null;
        const fe = body?.detail?.field_errors;
        const first = fe ? Object.values(fe)[0] : undefined;
        setSaveState({ kind: 'error', message: first ?? err.message });
        return;
      }
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
                  onChange={(e) => handleProviderChange(e.target.value as LlmProvider)}
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
