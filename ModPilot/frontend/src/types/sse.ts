// SSE event union. Mirrors AgentLoop._emit call sites in app/agent/loop.py
// and the three widget/error_choice events whose payloads land as JSON after
// task #7 flips the backend to ship structured data instead of HTML fragments.
//
// Every event carries the common envelope { type, ts, phase, state } from
// AgentLoop._emit; type-specific keys come from the per-call **payload.

import type { ChainHead, InferDecision, LoopState, PhaseName, PrincipledSlot } from './domain';

interface EventBase {
  ts: number;
  phase: string | null;
  state: LoopState;
}

export interface MessageEvent extends EventBase {
  type: 'message';
  role: 'user' | 'assistant';
  content: string;
}

export interface StateEvent extends EventBase {
  type: 'state';
}

export interface PhaseStartedEvent extends EventBase {
  type: 'phase_started';
  phase: PhaseName;
  index: number;
  total: number;
}

export interface PhaseCompletedEvent extends EventBase {
  type: 'phase_completed';
  phase: PhaseName;
  index: number;
  total: number;
}

export interface ToolCallEvent extends EventBase {
  type: 'tool_call';
  id: string | null;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResultEvent extends EventBase {
  type: 'tool_result';
  id: string | null;
  name: string;
  success: boolean;
  summary: string;
}

export interface AgentErrorEvent extends EventBase {
  type: 'agent_error';
  message: string;
  where: string;
  recoverable?: boolean;
}

export interface DoneEvent extends EventBase {
  type: 'done';
  reply: string;
  session_id: string;
}

export interface InterruptedEvent extends EventBase {
  type: 'interrupted';
}

export interface ModelTypeInferredEvent extends EventBase {
  type: 'model_type_inferred';
  preset: string;
  coverage: number;
  decision: InferDecision;
  candidates: { name: string; coverage: number }[];
  uncovered_slots: string[];
}

// ── Widget + error-choice events ──────────────────────────────────────────
// These ship structured JSON post-task #7. Until then the SSE channel still
// delivers HTML strings for these three event types; the chat shell will
// branch on payload shape during the transition (see useSSE).

export interface ErrorChoiceEvent extends EventBase {
  type: 'error_choice';
  operator: string;
  category: string;
  message: string;
  summary: string;
}

export interface WidgetClassificationEvent extends EventBase {
  type: 'widget_classification';
  chains: ChainHead[];
  inferred_types: string[];
}

export type ExistingConnections = Record<
  string,
  Record<string, string> // slot → file path or "connected_no_image"
>;
export type TextureSuggestions = Record<
  string,
  Partial<Record<PrincipledSlot, string>>
>;

export interface WidgetMaterialEvent extends EventBase {
  type: 'widget_material';
  materials: string[];
  existing_connections: ExistingConnections;
  texture_files: string[];
  suggestions: TextureSuggestions;
}

export type SseEvent =
  | MessageEvent
  | StateEvent
  | PhaseStartedEvent
  | PhaseCompletedEvent
  | ToolCallEvent
  | ToolResultEvent
  | AgentErrorEvent
  | DoneEvent
  | InterruptedEvent
  | ModelTypeInferredEvent
  | ErrorChoiceEvent
  | WidgetClassificationEvent
  | WidgetMaterialEvent;

export type SseEventType = SseEvent['type'];

// Discriminated extractor: `SseEventByType<'message'>` → MessageEvent.
export type SseEventByType<T extends SseEventType> = Extract<SseEvent, { type: T }>;
