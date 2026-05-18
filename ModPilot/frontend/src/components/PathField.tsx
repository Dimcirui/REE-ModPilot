import { useCallback, useEffect, useRef, useState } from 'react';
import {
  isDesktop,
  onPathDrop,
  pickDirectory,
  pickFile,
} from '@/lib/desktop';
import styles from './PathField.module.css';

export interface PathFieldProps {
  value: string;
  onChange: (next: string) => void;
  kind: 'file' | 'directory';
  placeholder?: string;
  filters?: { name: string; extensions: string[] }[];
  required?: boolean;
  invalid?: boolean;
  id?: string;
}

export function PathField({
  value,
  onChange,
  kind,
  placeholder,
  filters,
  required,
  invalid,
  id,
}: PathFieldProps) {
  const rowRef = useRef<HTMLDivElement>(null);
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });

  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const handleBrowse = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const picked =
        kind === 'file' ? await pickFile({ filters }) : await pickDirectory();
      if (picked) onChangeRef.current(picked);
    } finally {
      setBusy(false);
    }
  }, [busy, kind, filters]);

  useEffect(() => {
    if (!isDesktop) return;
    return onPathDrop((payload) => {
      const el = rowRef.current;
      if (!el) return;
      const dpr = window.devicePixelRatio || 1;
      let inside = false;
      if (payload.position) {
        const x = payload.position.x / dpr;
        const y = payload.position.y / dpr;
        const r = el.getBoundingClientRect();
        inside = x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
      }
      if (payload.phase === 'enter' || payload.phase === 'over') {
        setDragOver(inside);
      } else if (payload.phase === 'leave') {
        setDragOver(false);
      } else if (payload.phase === 'drop') {
        setDragOver(false);
        if (inside && payload.paths.length > 0) {
          onChangeRef.current(payload.paths[0]);
        }
      }
    });
  }, []);

  return (
    <div
      ref={rowRef}
      className={[
        styles.row,
        dragOver ? styles.dragOver : '',
        invalid ? styles.invalid : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <input
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        className={styles.input}
        spellCheck={false}
      />
      {isDesktop && (
        <button
          type="button"
          onClick={handleBrowse}
          disabled={busy}
          className={styles.browseButton}
          title={kind === 'file' ? 'Pick a file…' : 'Pick a folder…'}
        >
          {busy ? '…' : 'Browse…'}
        </button>
      )}
    </div>
  );
}
