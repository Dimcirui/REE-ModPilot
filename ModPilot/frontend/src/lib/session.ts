// Session id — 12-char hex prefix of a uuid4, matching app/main.py's index handler.
//
// Persisted in localStorage so the backend's per-session move log (and the
// phase-granular recovery hydrated from it in AgentLoop.__init__) can rebuild
// state when the user reloads the page or restarts the backend. Without this
// the FE mints a fresh id on every reload, the backend creates a fresh
// AgentLoop, and the prior session's moves.jsonl is orphaned on disk.
//
// Fallback: when localStorage is unavailable (locked-down WebView,
// Safari private mode with quotas, etc.) we cache in module scope only —
// same behavior as before this change, just without cross-reload persistence.

const STORAGE_KEY = 'modpilot.session_id.v1';
const ID_PATTERN = /^[a-f0-9]{12}$/i;

let cached: string | null = null;

function mintId(): string {
  const uuid =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2);
  return uuid.replace(/-/g, '').slice(0, 12);
}

function readFromStorage(): string | null {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v && ID_PATTERN.test(v) ? v : null;
  } catch {
    return null;
  }
}

function writeToStorage(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Module-scope cache still works for the current page lifetime;
    // we just lose the cross-reload promise.
  }
}

export function getSessionId(): string {
  if (cached) return cached;
  const fromStorage = readFromStorage();
  if (fromStorage) {
    cached = fromStorage;
    return cached;
  }
  cached = mintId();
  writeToStorage(cached);
  return cached;
}

/**
 * Discard the persisted session_id and mint a new one.
 *
 * Use when the user explicitly asks to start a fresh mod session — minting
 * a new id means the next backend call hydrates from an empty (non-existent)
 * move log, so the prior session's state is abandoned. The old moves.jsonl
 * remains on disk until the user (or a future GC sweep) deletes it.
 */
export function resetSessionId(): string {
  cached = null;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore — same fallback as writeToStorage */
  }
  return getSessionId();
}
