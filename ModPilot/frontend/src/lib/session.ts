// Session id — 12-char hex prefix of a uuid4, matching app/main.py's index handler.
// Cached in module scope so the same id is shared across the page lifetime
// (every full reload starts a fresh session, matching legacy behavior).

let cached: string | null = null;

export function getSessionId(): string {
  if (cached) return cached;
  const uuid =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2);
  cached = uuid.replace(/-/g, '').slice(0, 12);
  return cached;
}
