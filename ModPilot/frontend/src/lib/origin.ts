/**
 * Resolve the backend's HTTP origin for `fetch` / `EventSource`.
 *
 * Two production contexts to handle:
 *   1. **Browser, served by FastAPI** — backend serves the SPA itself at /,
 *      so the bundle's origin IS the backend. Relative paths work.
 *   2. **Vite dev (`pnpm dev`)** — bundle is at :5173 but Vite proxies
 *      /agent /app /viewport_screenshot /health to :8000. Relative works.
 *   3. **Tauri bundled (`tauri:dev` / installed)** — bundle is served from
 *      `tauri://localhost`, which does NOT reach the Python backend. We
 *      MUST use an absolute URL like `http://localhost:8000` for every
 *      request.
 *
 * Returns '' (relative) for cases 1 + 2; an absolute URL for case 3.
 */

import { isDesktop } from './desktop';

const TAURI_BACKEND_ORIGIN = 'http://localhost:8000';

export function apiBase(): string {
  return isDesktop ? TAURI_BACKEND_ORIGIN : '';
}

/** Convenience: prefix a backend path with `apiBase()`. */
export function apiUrl(path: string): string {
  return apiBase() + path;
}
