import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Header } from '@/components/Header';
import { Shell } from '@/components/Shell';
import { ChatStrip } from '@/components/ChatStrip';
import { InterruptBanner } from '@/components/InterruptBanner';
import { ResumeSessionBanner } from '@/components/ResumeSessionBanner';
import { PhaseStepper } from '@/components/PhaseStepper';
import { StageRouter } from '@/stages/StageRouter';
import { useChatState, nextBubbleId } from '@/hooks/useChatState';
import { useSSE, type SseDispatchers } from '@/hooks/useSSE';
import { getSessionId, resetSessionId } from '@/lib/session';
import { api } from '@/lib/api';
import type {
  ClassificationConfirmation,
  MaterialSlotMapping,
  SessionStatusResponse,
} from '@/types/api';
import type { ModelTypeInferredEvent } from '@/types/sse';
import styles from './ChatPage.module.css';

const DONE_WATCHDOG_MS = 5000;
const DEBUG_STORAGE_KEY = 'modpilot.debug';

export default function ChatPage() {
  const sessionId = useMemo(() => getSessionId(), []);
  const [state, dispatch] = useChatState();
  const [debugMode, setDebugMode] = useState<boolean>(() => {
    try {
      return localStorage.getItem(DEBUG_STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  });
  const [inferredModelType, setInferredModelType] =
    useState<ModelTypeInferredEvent | null>(null);
  // Surfaces the "resume last session?" prompt when on-disk move log exists
  // but the session is incomplete. Null means either no history (fresh) or
  // the prompt has already been resolved this page lifetime.
  const [resumePrompt, setResumePrompt] = useState<SessionStatusResponse | null>(null);
  const watchdogRef = useRef<number | null>(null);

  const cancelWatchdog = useCallback(() => {
    if (watchdogRef.current !== null) {
      window.clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
  }, []);

  const armWatchdog = useCallback(() => {
    cancelWatchdog();
    watchdogRef.current = window.setTimeout(() => {
      console.warn('ModPilot: no done event after assistant message; firing phantom');
      dispatch({ type: 'done' });
    }, DONE_WATCHDOG_MS);
  }, [cancelWatchdog, dispatch]);

  // ── SSE dispatcher map ──────────────────────────────────────────────────
  const dispatchers: SseDispatchers = useMemo(
    () => ({
      message: (e) => {
        if (e.role !== 'assistant') return;
        dispatch({ type: 'append_assistant', content: e.content });
        armWatchdog();
      },
      state: (e) => dispatch({ type: 'set_loop_state', state: e.state }),
      phase_started: (e) => dispatch({ type: 'phase_started', phase: e.phase }),
      phase_completed: (e) => dispatch({ type: 'phase_completed', phase: e.phase }),
      tool_call: (e) =>
        dispatch({
          type: 'append_tool_call',
          name: e.name,
          input: e.input,
          toolId: e.id,
          phase: e.phase,
        }),
      tool_result: (e) =>
        dispatch({
          type: 'append_tool_result',
          name: e.name,
          success: e.success,
          summary: e.summary,
          toolId: e.id,
          phase: e.phase,
        }),
      agent_error: (e) =>
        dispatch({ type: 'append_agent_error', where: e.where, message: e.message }),
      done: () => {
        cancelWatchdog();
        dispatch({ type: 'done' });
      },
      interrupted: () => dispatch({ type: 'interrupted' }),
      model_type_inferred: (e) => setInferredModelType(e),
      widget_classification: (e) =>
        dispatch({ type: 'widget_received', widget: { kind: 'classification', event: e } }),
      widget_material: (e) =>
        dispatch({ type: 'widget_received', widget: { kind: 'material', event: e } }),
      error_choice: (e) => dispatch({ type: 'error_choice_received', event: e }),
    }),
    [dispatch, armWatchdog, cancelWatchdog],
  );

  // First-render check: does this session_id already have on-disk history?
  // - no history       → drop straight into a fresh session, no prompt
  // - completed        → mint a new session_id silently (the FE keeps a clean
  //                      slate; the on-disk log is left as an archive)
  // - incomplete       → show the resume prompt so the user can choose
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const status = await api.getSessionStatus(sessionId);
        if (cancelled) return;
        if (!status.has_history) return;
        if (status.completed) {
          // Past session finished. Mint a fresh id; reload so all React state
          // (sessionId useMemo, SSE connection, chat bubbles) starts clean.
          resetSessionId();
          window.location.reload();
          return;
        }
        setResumePrompt(status);
      } catch (err) {
        // Status check is best-effort. If backend is unreachable here, the
        // normal SSE retry path will surface the real issue.
        console.warn('session status check failed', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const handleResumeSession = useCallback(() => {
    setResumePrompt(null);
  }, []);

  const handleStartNewSession = useCallback(() => {
    resetSessionId();
    window.location.reload();
  }, []);

  const { status: sseStatus, reconnectIn } = useSSE(sessionId, dispatchers);

  useEffect(() => {
    if (sseStatus === 'open') {
      dispatch({ type: 'connection_open' });
    } else if (sseStatus === 'reconnecting') {
      dispatch({ type: 'connection_error', label: 'reconnecting' });
    } else if (sseStatus === 'closed') {
      dispatch({ type: 'connection_error', label: 'disconnected' });
    }
  }, [sseStatus, dispatch]);

  // ── Escape key → request interrupt ──────────────────────────────────────
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape') return;
      const tag = (ev.target as HTMLElement | null)?.tagName ?? '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (state.statusTone !== 'thinking') return;
      void api.interrupt(sessionId);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [sessionId, state.statusTone]);

  // ── Submit handlers ─────────────────────────────────────────────────────
  const handleSubmit = useCallback(
    async (content: string) => {
      dispatch({ type: 'submit_user', content, bubbleId: nextBubbleId() });
      try {
        await api.sendMessage({ session_id: sessionId, message: content });
      } catch (err) {
        dispatch({
          type: 'append_agent_error',
          where: 'submit',
          message: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [dispatch, sessionId],
  );

  const handleClassificationSubmit = useCallback(
    async (confirmations: ClassificationConfirmation[], summary: string) => {
      dispatch({ type: 'submit_widget', bubbleId: nextBubbleId(), summary });
      try {
        await api.submitClassificationWidget({ session_id: sessionId, confirmations });
      } catch (err) {
        dispatch({
          type: 'append_agent_error',
          where: 'widget/classification',
          message: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [dispatch, sessionId],
  );

  const handleMaterialSubmit = useCallback(
    async (mappings: MaterialSlotMapping[], summary: string) => {
      dispatch({ type: 'submit_widget', bubbleId: nextBubbleId(), summary });
      try {
        await api.submitMaterialWidget({ session_id: sessionId, mappings });
      } catch (err) {
        dispatch({
          type: 'append_agent_error',
          where: 'widget/material',
          message: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [dispatch, sessionId],
  );

  const handleErrorChoice = useCallback(
    async (keyword: string) => {
      dispatch({ type: 'error_choice_consumed' });
      await handleSubmit(keyword);
    },
    [dispatch, handleSubmit],
  );

  const handleDebugToggle = useCallback(() => {
    setDebugMode((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(DEBUG_STORAGE_KEY, next ? '1' : '0');
      } catch {
        // storage blocked — fine
      }
      return next;
    });
  }, []);

  const handleDismissInterrupt = useCallback(() => {
    dispatch({ type: 'dismiss_interrupt' });
  }, [dispatch]);

  useEffect(() => () => cancelWatchdog(), [cancelWatchdog]);

  const stageProps = {
    sessionId,
    state,
    inferredModelType,
    onClassificationSubmit: handleClassificationSubmit,
    onMaterialSubmit: handleMaterialSubmit,
    onErrorChoice: handleErrorChoice,
  };

  return (
    <div className={`${styles.root} ${debugMode ? styles.debugMode : ''}`}>
      <Shell
        header={
          <Header
            sessionId={sessionId}
            debugMode={debugMode}
            onToggleDebug={handleDebugToggle}
          />
        }
        stage={<StageRouter {...stageProps} />}
        phaseStepper={<PhaseStepper status={state.phaseStatus} />}
        banner={
          <>
            <ResumeSessionBanner
              status={resumePrompt}
              onResume={handleResumeSession}
              onStartNew={handleStartNewSession}
            />
            <InterruptBanner
              visible={state.interruptVisible}
              onDismiss={handleDismissInterrupt}
            />
          </>
        }
        chatStrip={
          <ChatStrip
            bubbles={state.bubbles}
            debugMode={debugMode}
            status={state.status}
            statusTone={state.statusTone}
            reconnectIn={reconnectIn}
            inputDisabled={state.inputDisabled}
            inputPlaceholder={state.inputPlaceholder}
            onSubmit={handleSubmit}
          />
        }
      />
    </div>
  );
}
