/**
 * Resolve the backend's HTTP origin for `fetch` / `EventSource`.
 *
 * Three production contexts to handle:
 *   1. **Browser, served by FastAPI** — backend serves the SPA itself at /,
 *      so the bundle's origin IS the backend. Relative paths work.
 *   2. **Vite dev (`pnpm dev`)** — bundle is at :5173 but Vite proxies
 *      /agent /app /viewport_screenshot /health to :8000. Relative works.
 *   3. **Tauri bundled (`tauri:dev` / installed)** — bundle is served from
 *      `tauri://localhost`, which does NOT reach the Python backend. We
 *      MUST use an absolute URL like `http://localhost:<port>`. The port
 *      is dynamic: Tauri probes for a free port at startup (so a leaked
 *      socket from a previous crash doesn't block boot), then exposes it
 *      via the `backend_port` Tauri command.
 *
 * Returns '' (relative) for cases 1 + 2; an absolute URL for case 3.
 *
 * Call `initApiOrigin()` ONCE at app bootstrap (main.tsx) before mounting
 * React. After that, `apiBase()` / `apiUrl()` are safe sync calls — the
 * resolved origin is cached at module scope.
 */

import { invoke } from '@tauri-apps/api/core';
import { isDesktop } from './desktop';

const FALLBACK_TAURI_ORIGIN = 'http://localhost:8000';

let _origin: string | null = null;

/**
 * Resolve and cache the backend origin. Must be awaited before React mounts.
 *
 * Idempotent — second calls are no-ops. The invoke failure path falls back
 * to :8000 so the splash UI can still surface a clear error to the user
 * instead of throwing before render.
 *
 * The `@tauri-apps/api/core` import is STATIC (not dynamic) on purpose:
 * dynamic imports inside `tauri://localhost` can fail to resolve their
 * lazy chunks under WebView2's protocol handler. Vite bundles the import
 * stub in both browser and Tauri builds; in browser, the `isDesktop`
 * guard prevents `invoke` from being called, so the import is harmless.
 */
export async function initApiOrigin(): Promise<void> {
  if (_origin !== null) return;
  if (!isDesktop) {
    _origin = '';
    return;
  }
  try {
    const port = await invoke<number>('backend_port');
    _origin = `http://localhost:${port}`;
  } catch (e) {
    // Tauri command unavailable (older shell? broken plugin?). Fall back
    // to the historical default so the splash error path can run instead
    // of the React tree blowing up on first fetch.
    // eslint-disable-next-line no-console
    console.warn('[modpilot] backend_port invoke failed, falling back to :8000', e);
    _origin = FALLBACK_TAURI_ORIGIN;
  }
}

export function apiBase(): string {
  if (_origin !== null) return _origin;
  // Pre-init call (shouldn't happen — main.tsx awaits initApiOrigin
  // before render — but keep a sensible default so any race is benign).
  return isDesktop ? FALLBACK_TAURI_ORIGIN : '';
}

/** Convenience: prefix a backend path with `apiBase()`. */
export function apiUrl(path: string): string {
  return apiBase() + path;
}
