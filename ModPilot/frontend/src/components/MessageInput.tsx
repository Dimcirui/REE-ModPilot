import { useRef, type FormEvent } from 'react';
import styles from './MessageInput.module.css';

interface MessageInputProps {
  disabled: boolean;
  placeholder: string;
  onSubmit: (message: string) => void;
}

export function MessageInput({ disabled, placeholder, onSubmit }: MessageInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    const value = inputRef.current?.value.trim() ?? '';
    if (!value || disabled) return;
    onSubmit(value);
    if (inputRef.current) {
      inputRef.current.value = '';
      inputRef.current.focus();
    }
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <input
        ref={inputRef}
        type="text"
        className={styles.input}
        placeholder={placeholder}
        autoComplete="off"
        disabled={disabled}
        required
      />
      <button type="submit" className={styles.button} disabled={disabled}>
        Send
      </button>
    </form>
  );
}
