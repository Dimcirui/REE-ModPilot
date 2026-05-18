import styles from './Header.module.css';

interface HeaderProps {
  sessionId: string;
  debugMode: boolean;
  onToggleDebug: () => void;
}

export function Header({ sessionId, debugMode, onToggleDebug }: HeaderProps) {
  return (
    <header className={styles.header}>
      <h1 className={styles.title}>ModPilot</h1>
      <span className={styles.sessionId} title="Session ID">
        {sessionId}
      </span>
      <button
        type="button"
        className={`${styles.debugToggle} ${debugMode ? styles.debugActive : ''}`}
        onClick={onToggleDebug}
        title="Show/hide tool call details"
      >
        Debug
      </button>
      <a href="/config" className={styles.settingsLink} title="Global settings">
        ⚙ Settings
      </a>
    </header>
  );
}
