// Reducer-driven chat state. Mirrors the dispatcher behavior in the legacy
// app.js: SSE events fold into bubble list + phase status + status badge,
// optimistic actions cover user-typed messages.

import { useReducer } from 'react';
import { PHASE_SEQUENCE, type LoopState, type PhaseName } from '@/types/domain';
import type {
  ErrorChoiceEvent,
  WidgetClassificationEvent,
  WidgetMaterialEvent,
} from '@/types/sse';

export type BubbleRole = 'user' | 'assistant' | 'tool' | 'error';

export interface Bubble {
  id: string;
  role: BubbleRole;
  content: string;
  debug: boolean;
  ts: number;
}

export type PhaseStatus = 'pending' | 'active' | 'done' | 'error';

export type StatusLabel =
  | 'idle'
  | 'ready'
  | 'thinking'
  | 'awaiting confirmation'
  | 'ask mode'
  | 'negotiating'
  | 'error'
  | 'done'
  | 'disconnected'
  | 'reconnecting';

export type StatusTone = '' | 'thinking' | 'error' | 'done';

export type WidgetState =
  | { kind: 'classification'; event: WidgetClassificationEvent }
  | { kind: 'material'; event: WidgetMaterialEvent };

// Per-tool-call record. Pushed on tool_call; updated in place on tool_result.
// Stages filter on `phase` / `name` to show only the activity that's relevant
// to whatever surface they own.
export interface ToolRun {
  runId: string;            // local id (sequence) — guaranteed unique
  toolId: string | null;    // SSE-side id, used for pairing call→result
  name: string;
  input: Record<string, unknown>;
  phase: string | null;
  startedAt: number;
  finishedAt?: number;
  success?: boolean;
  summary?: string;
}

export interface ChatState {
  bubbles: Bubble[];
  toolRuns: ToolRun[];
  phaseStatus: Record<PhaseName, PhaseStatus>;
  status: StatusLabel;
  statusTone: StatusTone;
  loopState: LoopState;
  inputDisabled: boolean;
  inputPlaceholder: string;
  widget: WidgetState | null;
  errorChoice: ErrorChoiceEvent | null;
  interruptVisible: boolean;
}

const DEFAULT_PLACEHOLDER = 'Type a message and press Enter…';
const WIDGET_PLACEHOLDER = 'Confirm the form above first…';

function emptyPhaseStatus(): Record<PhaseName, PhaseStatus> {
  return Object.fromEntries(PHASE_SEQUENCE.map((p) => [p, 'pending'])) as Record<
    PhaseName,
    PhaseStatus
  >;
}

export const initialChatState: ChatState = {
  bubbles: [],
  toolRuns: [],
  phaseStatus: emptyPhaseStatus(),
  status: 'idle',
  statusTone: '',
  loopState: 'idle',
  inputDisabled: false,
  inputPlaceholder: DEFAULT_PLACEHOLDER,
  widget: null,
  errorChoice: null,
  interruptVisible: false,
};

const STATE_TO_STATUS: Record<LoopState, { label: StatusLabel; tone: StatusTone }> = {
  idle: { label: 'idle', tone: '' },
  running_phase: { label: 'thinking', tone: 'thinking' },
  await_confirm: { label: 'awaiting confirmation', tone: 'thinking' },
  error_handling: { label: 'error', tone: 'error' },
  ask_mode: { label: 'ask mode', tone: 'thinking' },
  negotiating: { label: 'negotiating', tone: 'thinking' },
  done: { label: 'done', tone: 'done' },
};

// Tools whose tool_call fires when a widget's data has just been consumed by
// the agent — clears the slot so a stale widget can't be resubmitted.
const WIDGET_CLEAR_TOOLS = new Set(['physics_chains', 'material_setup', 'material_generate']);

export type ChatAction =
  | { type: 'submit_user'; content: string; bubbleId: string }
  | { type: 'submit_widget'; bubbleId: string; summary: string }
  | { type: 'append_assistant'; content: string }
  | {
      type: 'append_tool_call';
      name: string;
      input: Record<string, unknown>;
      toolId?: string | null;
      phase?: string | null;
    }
  | {
      type: 'append_tool_result';
      name: string;
      success: boolean;
      summary: string;
      toolId?: string | null;
      phase?: string | null;
    }
  | { type: 'append_agent_error'; where: string; message: string }
  | { type: 'set_loop_state'; state: LoopState }
  | { type: 'phase_started'; phase: PhaseName }
  | { type: 'phase_completed'; phase: PhaseName }
  | { type: 'done' }
  | { type: 'interrupted' }
  | { type: 'dismiss_interrupt' }
  | { type: 'connection_error'; label: StatusLabel }
  | { type: 'connection_open' }
  | { type: 'widget_received'; widget: WidgetState }
  | { type: 'widget_consumed' }
  | { type: 'error_choice_received'; event: ErrorChoiceEvent }
  | { type: 'error_choice_consumed' };

let bubbleSeq = 0;
export function nextBubbleId(): string {
  bubbleSeq += 1;
  return `b${bubbleSeq}`;
}

let toolRunSeq = 0;
function nextRunId(): string {
  toolRunSeq += 1;
  return `tr${toolRunSeq}`;
}

function appendBubble(state: ChatState, bubble: Bubble): ChatState {
  return { ...state, bubbles: [...state.bubbles, bubble] };
}

function lockForWidget(state: ChatState, widget: WidgetState): ChatState {
  return {
    ...state,
    widget,
    inputDisabled: true,
    inputPlaceholder: WIDGET_PLACEHOLDER,
  };
}

function unlockFromWidget(state: ChatState): ChatState {
  return {
    ...state,
    widget: null,
    inputDisabled: state.statusTone === 'thinking',
    inputPlaceholder: DEFAULT_PLACEHOLDER,
  };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'submit_user':
      return {
        ...appendBubble(state, {
          id: action.bubbleId,
          role: 'user',
          content: action.content,
          debug: false,
          ts: Date.now(),
        }),
        status: 'thinking',
        statusTone: 'thinking',
        inputDisabled: true,
      };

    case 'submit_widget':
      return appendBubble(state, {
        id: action.bubbleId,
        role: 'user',
        content: action.summary,
        debug: false,
        ts: Date.now(),
      });

    case 'append_assistant':
      return appendBubble(state, {
        id: nextBubbleId(),
        role: 'assistant',
        content: action.content,
        debug: false,
        ts: Date.now(),
      });

    case 'append_tool_call': {
      const inputJson = JSON.stringify(action.input ?? {}).slice(0, 200);
      const ts = Date.now();
      const run: ToolRun = {
        runId: nextRunId(),
        toolId: action.toolId ?? null,
        name: action.name,
        input: action.input ?? {},
        phase: action.phase ?? null,
        startedAt: ts,
      };
      const next: ChatState = {
        ...appendBubble(state, {
          id: nextBubbleId(),
          role: 'tool',
          content: `> ${action.name}  ${inputJson}`,
          debug: true,
          ts,
        }),
        toolRuns: [...state.toolRuns, run],
      };
      if (WIDGET_CLEAR_TOOLS.has(action.name) && state.widget) {
        return unlockFromWidget(next);
      }
      return next;
    }

    case 'append_tool_result': {
      const tag = action.success ? 'ok' : 'FAIL';
      const ts = Date.now();
      const withBubble = appendBubble(state, {
        id: nextBubbleId(),
        role: 'tool',
        content: `< [${tag}] ${action.name}: ${(action.summary || '').slice(0, 300)}`,
        debug: true,
        ts,
      });
      // Pair against the most recent unfinished run: prefer toolId match, else
      // fall back to last-unfinished-by-name (covers providers that don't ship
      // tool_use ids — Ollama, DSML-mode DeepSeek).
      const runs = state.toolRuns;
      let matchIdx = -1;
      if (action.toolId) {
        for (let i = runs.length - 1; i >= 0; i -= 1) {
          const r = runs[i];
          if (r.toolId === action.toolId && r.finishedAt === undefined) {
            matchIdx = i;
            break;
          }
        }
      }
      if (matchIdx === -1) {
        for (let i = runs.length - 1; i >= 0; i -= 1) {
          const r = runs[i];
          if (r.name === action.name && r.finishedAt === undefined) {
            matchIdx = i;
            break;
          }
        }
      }
      let nextRuns: ToolRun[];
      if (matchIdx >= 0) {
        const r = runs[matchIdx];
        const updated: ToolRun = {
          ...r,
          finishedAt: ts,
          success: action.success,
          summary: action.summary,
          // Inherit phase from result if call didn't have one (e.g. early-stream order).
          phase: r.phase ?? action.phase ?? null,
        };
        nextRuns = [...runs.slice(0, matchIdx), updated, ...runs.slice(matchIdx + 1)];
      } else {
        // Orphan result — synthesize a closed run so the activity feed still shows it.
        nextRuns = [
          ...runs,
          {
            runId: nextRunId(),
            toolId: action.toolId ?? null,
            name: action.name,
            input: {},
            phase: action.phase ?? null,
            startedAt: ts,
            finishedAt: ts,
            success: action.success,
            summary: action.summary,
          },
        ];
      }
      return { ...withBubble, toolRuns: nextRuns };
    }

    case 'append_agent_error':
      return {
        ...appendBubble(state, {
          id: nextBubbleId(),
          role: 'error',
          content: `Error (${action.where}): ${action.message}`,
          debug: false,
          ts: Date.now(),
        }),
        status: 'error',
        statusTone: 'error',
        inputDisabled: false,
      };

    case 'set_loop_state': {
      const { label, tone } = STATE_TO_STATUS[action.state];
      return {
        ...state,
        loopState: action.state,
        status: label,
        statusTone: tone,
      };
    }

    case 'phase_started':
      // Don't downgrade done → active for a phase the user has already passed.
      if (state.phaseStatus[action.phase] === 'done') return state;
      return {
        ...state,
        phaseStatus: { ...state.phaseStatus, [action.phase]: 'active' },
      };

    case 'phase_completed':
      return {
        ...state,
        phaseStatus: { ...state.phaseStatus, [action.phase]: 'done' },
      };

    case 'done':
      return {
        ...state,
        status: 'ready',
        statusTone: '',
        inputDisabled: state.widget !== null,
      };

    case 'interrupted':
      return { ...state, interruptVisible: true };

    case 'dismiss_interrupt':
      return { ...state, interruptVisible: false };

    case 'connection_error':
      return {
        ...state,
        status: action.label,
        statusTone: 'error',
        // Free the chat input so the user isn't stuck staring at a disabled
        // box if the stream dropped mid-turn.
        inputDisabled: false,
      };

    case 'connection_open':
      // Only clear the disconnected/reconnecting badge — preserve thinking etc.
      if (state.status === 'disconnected' || state.status === 'reconnecting') {
        return { ...state, status: 'ready', statusTone: '' };
      }
      return state;

    case 'widget_received':
      return lockForWidget(state, action.widget);

    case 'widget_consumed':
      return unlockFromWidget(state);

    case 'error_choice_received':
      return { ...state, errorChoice: action.event };

    case 'error_choice_consumed':
      return { ...state, errorChoice: null };

    default:
      return state;
  }
}

export function useChatState() {
  return useReducer(chatReducer, initialChatState);
}
