/**
 * Desktop (Tauri) bridge with browser-mode fallbacks.
 *
 * Tauri APIs are dynamically imported so the bundle still works in a plain
 * browser context — `isDesktop` is false there and every function below
 * resolves to a safe no-op / null.
 *
 * The drag-drop event is built into the Tauri v2 webview and needs no plugin
 * permission. Browser DnD cannot expose disk paths, which is the whole reason
 * we shipped Tauri in the first place.
 */

export const isDesktop: boolean =
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

export interface PickFileOptions {
  title?: string;
  initialDir?: string;
  filters?: { name: string; extensions: string[] }[];
}

export async function pickFile(opts: PickFileOptions = {}): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import('@tauri-apps/plugin-dialog');
  const result = await open({
    title: opts.title ?? 'Pick a file',
    multiple: false,
    directory: false,
    defaultPath: opts.initialDir,
    filters: opts.filters,
  });
  return typeof result === 'string' ? result : null;
}

export interface PickDirectoryOptions {
  title?: string;
  initialDir?: string;
}

export async function pickDirectory(opts: PickDirectoryOptions = {}): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import('@tauri-apps/plugin-dialog');
  const result = await open({
    title: opts.title ?? 'Pick a directory',
    multiple: false,
    directory: true,
    defaultPath: opts.initialDir,
  });
  return typeof result === 'string' ? result : null;
}

export type DragDropPhase = 'enter' | 'over' | 'drop' | 'leave';

export interface DragDropPayload {
  phase: DragDropPhase;
  paths: string[];
  position?: { x: number; y: number };
}

/**
 * Subscribes to webview drag-drop events. Returns an unsubscribe function.
 * In browser mode this is a no-op that immediately returns a noop unsubscribe.
 *
 * The handler fires for all 4 phases (enter / over / drop / leave). Most
 * callers will only care about `drop` to consume the dropped paths and
 * `enter` / `leave` for visual highlight state.
 */
export function onPathDrop(
  handler: (payload: DragDropPayload) => void,
): () => void {
  if (!isDesktop) return () => {};

  let unlisten: (() => void) | null = null;
  let cancelled = false;

  (async () => {
    const { getCurrentWebview } = await import('@tauri-apps/api/webview');
    if (cancelled) return;
    const off = await getCurrentWebview().onDragDropEvent((evt) => {
      const t = evt.payload.type;
      if (t === 'enter') {
        handler({ phase: 'enter', paths: evt.payload.paths, position: evt.payload.position });
      } else if (t === 'over') {
        handler({ phase: 'over', paths: [], position: evt.payload.position });
      } else if (t === 'drop') {
        handler({ phase: 'drop', paths: evt.payload.paths, position: evt.payload.position });
      } else if (t === 'leave') {
        handler({ phase: 'leave', paths: [] });
      }
    });
    if (cancelled) {
      off();
    } else {
      unlisten = off;
    }
  })();

  return () => {
    cancelled = true;
    if (unlisten) unlisten();
  };
}
