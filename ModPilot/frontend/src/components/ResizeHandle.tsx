import { useCallback, useLayoutEffect, useRef } from 'react';
import styles from './ResizeHandle.module.css';

interface ResizeHandleProps {
  // Direction of size adjustment. 'col' = changes the parent grid's last
  // column width; 'row' = changes its last row height. The component reads
  // the parent's actual grid template at drag time, so 'auto' picks the
  // axis that is multi-track (used when the layout flips between desktop
  // and responsive modes).
  axis?: 'col' | 'row' | 'auto';
  // Shared persistence key — same key across stages means resizing in one
  // phase is remembered in every other.
  storageKey?: string;
  minSize?: number;
  maxSize?: number;
}

const STORAGE_PREFIX = 'modpilot.split.';
// Sane min/max so the user can't drag past usable bounds.
const DEFAULT_MIN = 200;
const DEFAULT_MAX = 900;

type Axis = 'col' | 'row';

function detectAxis(parent: HTMLElement): Axis {
  const cs = window.getComputedStyle(parent);
  // grid-template-rows returns "Xpx Ypx ..." after layout. >= 2 tracks
  // means the responsive (rows) split is active.
  const rows = cs.gridTemplateRows.trim().split(/\s+/).filter(Boolean);
  return rows.length >= 2 ? 'row' : 'col';
}

function lsKey(storageKey: string, axis: Axis): string {
  return `${STORAGE_PREFIX}${storageKey}.${axis}`;
}

function applySize(parent: HTMLElement, axis: Axis, px: number) {
  parent.style.setProperty(axis === 'col' ? '--split-col' : '--split-row', `${px}px`);
}

export function ResizeHandle({
  axis = 'auto',
  storageKey = 'default',
  minSize = DEFAULT_MIN,
  maxSize = DEFAULT_MAX,
}: ResizeHandleProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  // Hydrate persisted size on mount and whenever the storage key changes.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const parent = el.parentElement;
    if (!parent) return;
    (['col', 'row'] as const).forEach((a) => {
      const raw = window.localStorage.getItem(lsKey(storageKey, a));
      if (!raw) return;
      const n = Number.parseFloat(raw);
      if (!Number.isFinite(n)) return;
      const clamped = Math.max(minSize, Math.min(maxSize, n));
      applySize(parent, a, clamped);
    });
  }, [storageKey, minSize, maxSize]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const el = ref.current;
      if (!el) return;
      const parent = el.parentElement;
      if (!parent) return;
      // Ignore non-primary buttons so right-click / middle-click don't drag.
      if (e.button !== 0) return;
      e.preventDefault();

      const a: Axis = axis === 'auto' ? detectAxis(parent) : axis;
      el.setPointerCapture(e.pointerId);
      el.dataset.dragging = 'true';

      const onMove = (ev: PointerEvent) => {
        const rect = parent.getBoundingClientRect();
        let size = a === 'col' ? rect.right - ev.clientX : rect.bottom - ev.clientY;
        size = Math.max(minSize, Math.min(maxSize, size));
        applySize(parent, a, size);
      };

      const onUp = (ev: PointerEvent) => {
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('pointercancel', onUp);
        try {
          el.releasePointerCapture(ev.pointerId);
        } catch {
          // already released — fine
        }
        delete el.dataset.dragging;

        // Persist current size (read from style so clamping is preserved).
        const styled = parent.style.getPropertyValue(a === 'col' ? '--split-col' : '--split-row');
        const px = Number.parseFloat(styled);
        if (Number.isFinite(px)) {
          window.localStorage.setItem(lsKey(storageKey, a), String(px));
        }
      };

      el.addEventListener('pointermove', onMove);
      el.addEventListener('pointerup', onUp);
      el.addEventListener('pointercancel', onUp);
    },
    [axis, storageKey, minSize, maxSize],
  );

  const onDoubleClick = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const parent = el.parentElement;
    if (!parent) return;
    // Reset both axes; the stylesheet's default var fallback (e.g. 360px /
    // 320px) takes over again.
    parent.style.removeProperty('--split-col');
    parent.style.removeProperty('--split-row');
    window.localStorage.removeItem(lsKey(storageKey, 'col'));
    window.localStorage.removeItem(lsKey(storageKey, 'row'));
  }, [storageKey]);

  return (
    <div
      ref={ref}
      className={styles.handle}
      role="separator"
      aria-orientation={axis === 'row' ? 'horizontal' : 'vertical'}
      aria-label="Resize viewport (double-click to reset)"
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
    />
  );
}
