import { useEffect, useRef, useState } from 'react';
import { apiUrl } from '@/lib/origin';
import type { SseEvent, SseEventByType, SseEventType } from '@/types/sse';

// All event types the backend may emit on /agent/stream/{sid}.
// We register a listener for each so the dispatcher map can subscribe to any.
const ALL_EVENT_TYPES: SseEventType[] = [
  'message',
  'state',
  'phase_started',
  'phase_completed',
  'tool_call',
  'tool_result',
  'agent_error',
  'done',
  'interrupted',
  'model_type_inferred',
  'error_choice',
  'widget_classification',
  'widget_material',
];

export type SseDispatchers = {
  [K in SseEventType]?: (event: SseEventByType<K>) => void;
};

export type SseStatus = 'connecting' | 'open' | 'reconnecting' | 'closed';

interface UseSseOptions {
  enabled?: boolean;
}

interface UseSseResult {
  status: SseStatus;
  // Wall-clock seconds until the next reconnect attempt; null when not waiting.
  reconnectIn: number | null;
}

const MAX_BACKOFF_MS = 30_000;

// Subscribe to the agent SSE stream for a session. The dispatcher map is
// looked up via ref on every event so consumers can pass freshly-bound
// closures without forcing the EventSource to reconnect.
//
// Transition note: until task #7 flips the backend, the three widget/
// error_choice events ship raw HTML strings, not JSON, in their data field.
// JSON.parse fails and we drop the event with a console.debug — the chat
// shell, viewport, and config form land first and don't depend on those.
export function useSSE(
  sessionId: string,
  dispatchers: SseDispatchers,
  { enabled = true }: UseSseOptions = {},
): UseSseResult {
  const [status, setStatus] = useState<SseStatus>('connecting');
  const [reconnectIn, setReconnectIn] = useState<number | null>(null);
  const dispatchersRef = useRef(dispatchers);
  dispatchersRef.current = dispatchers;

  useEffect(() => {
    if (!enabled || !sessionId) {
      setStatus('closed');
      return;
    }

    let attempts = 0;
    let source: EventSource | null = null;
    let cancelled = false;
    let reconnectTimer: number | null = null;

    const dispatchEvent = (type: SseEventType, raw: string) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        // Transition: error_choice / widget_* still ship HTML until task #7.
        if (
          type === 'error_choice' ||
          type === 'widget_classification' ||
          type === 'widget_material'
        ) {
          return;
        }
        console.warn('useSSE: malformed JSON payload', { type, raw });
        return;
      }
      const handler = dispatchersRef.current[type];
      if (!handler) return;
      const event = { type, ...(parsed as object) } as SseEvent;
      // Discriminator-safe: handler[type]'s parameter is SseEventByType<type>.
      (handler as (e: SseEvent) => void)(event);
    };

    const open = () => {
      if (cancelled) return;
      setStatus(attempts === 0 ? 'connecting' : 'reconnecting');
      setReconnectIn(null);
      source = new EventSource(apiUrl(`/agent/stream/${sessionId}`));

      source.addEventListener('open', () => {
        attempts = 0;
        setStatus('open');
        setReconnectIn(null);
      });

      for (const type of ALL_EVENT_TYPES) {
        source.addEventListener(type, (event) => {
          dispatchEvent(type, (event as globalThis.MessageEvent).data);
        });
      }

      source.addEventListener('error', () => {
        if (cancelled || !source) return;
        if (source.readyState !== EventSource.CLOSED) {
          // Native auto-reconnect path — let the browser try.
          setStatus('reconnecting');
          return;
        }
        source.close();
        source = null;
        const delayMs = Math.min(1000 * 2 ** attempts, MAX_BACKOFF_MS);
        attempts += 1;
        setStatus('reconnecting');
        setReconnectIn(Math.max(1, Math.round(delayMs / 1000)));
        reconnectTimer = window.setTimeout(open, delayMs);
      });
    };

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (source) source.close();
    };
  }, [sessionId, enabled]);

  return { status, reconnectIn };
}
