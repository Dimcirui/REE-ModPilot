// HTTP request / response shapes for every route the FE hits.
// Backend source: app/main.py.

import type { ArmorSet, ArmorVariant, LlmProvider, XPreset } from './domain';

// ── /agent/messages, /agent/chat ──────────────────────────────────────────

export interface ChatRequest {
  message: string;
  session_id: string;
}

export interface ChatResponse {
  reply: string;
  state: string;
  session_id: string;
}

// ── /agent/interrupt/{session_id} ─────────────────────────────────────────

export interface InterruptResponse {
  session_id: string;
  interrupted: boolean;
}

// ── /agent/session/status ─────────────────────────────────────────────────

export interface SessionStatusResponse {
  session_id: string;
  has_history: boolean;
  completed: boolean;
  phase_idx: number;
  current_phase: string | null;
  last_activity_ts: number | null;
}

// ── /agent/config (POST) ──────────────────────────────────────────────────

export interface SessionConfig {
  model_path: string;
  model_type: string; // "Auto-detect" or any preset name from /app/x_presets
  texture_dir: string;
  mod_root: string;
  author: string;
  character_name: string;
  use_bone_system: boolean;
  body_parts: ('1' | '2' | '3' | '4' | '5')[]; // 1=arms, 2=body, 3=helmet, 4=legs, 5=waist
  armor_variant: ArmorVariant;
  armor_id: string;
}

export interface SessionConfigRequest {
  session_id: string;
  config: SessionConfig;
}

export interface SessionConfigSaveOk {
  session_id: string;
  saved: true;
}

// 422 detail: { field_errors: { fieldName: humanMessage, ... } }
export interface SessionConfigFieldErrors {
  field_errors: Partial<Record<keyof SessionConfig, string>>;
}

// ── /agent/widget/{classification,material} ───────────────────────────────
// Target shape after task #7 flips the BE to accept structured JSON.

export interface ClassificationConfirmation {
  chain_name: string;
  inferred_type: string;
  description: string;
  merge_to_parent: boolean;
}

export interface ClassificationWidgetSubmit {
  session_id: string;
  confirmations: ClassificationConfirmation[];
}

export interface MaterialSlotMapping {
  material: string;
  slot: string; // one of PRINCIPLED_SLOTS
  texture_path: string; // empty string = skip
}

export interface MaterialWidgetSubmit {
  session_id: string;
  mappings: MaterialSlotMapping[];
}

export interface WidgetSaveOk {
  saved: true;
  count?: number;
  materials?: number;
}

// ── /app/config ───────────────────────────────────────────────────────────

export interface AppConfigGet {
  llm_provider: LlmProvider;
  llm_api_key: string; // "***" when a key is set, "" when not
  llm_model: string;
  llm_base_url: string;
  blender_host: string;
  blender_port: number;
  has_api_key: boolean;
}

export interface AppConfigPost {
  llm_provider: LlmProvider;
  llm_api_key: string; // empty = preserve existing
  llm_model: string;
  llm_base_url: string;
  blender_host: string;
  blender_port: number;
}

export interface AppConfigSaveOk {
  saved: true;
  status: { llm: string; blender: string };
}

// ── /app/x_presets, /app/armor_sets ───────────────────────────────────────

export interface XPresetsResponse {
  presets: XPreset[];
}

export interface ArmorSetsResponse {
  armor_sets: ArmorSet[];
}

// ── /app/toolkit_status ───────────────────────────────────────────────────

export type ToolStatusValue = 'present' | 'disabled' | 'missing';

export interface ToolStatus {
  id: string;
  label: string;
  status: ToolStatusValue;
  critical: boolean;
}

export interface ToolkitStatusResponse {
  ok: boolean;
  tools: ToolStatus[];
}

// ── /health ───────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: 'ok';
  blender: {
    host: string;
    port: number;
    scene: string | null;
    objects: number | null;
  };
}
