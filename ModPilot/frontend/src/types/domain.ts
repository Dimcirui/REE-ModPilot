// Domain-level constants and types shared across the FE.
// Backend sources (read-only mirrors):
//   - PHASE_SEQUENCE: app/agent/loop.py:_PHASE_SEQUENCE
//   - LoopState:      app/agent/loop.py:LoopState
//   - PRINCIPLED_SLOTS: app/phases/material.py:PRINCIPLED_SLOTS
//   - ARMOR_VARIANTS: app/main.py:SessionConfig.armor_variant Literal
//   - LLM_PROVIDERS:  app/main.py:AppConfigUpdate.llm_provider Literal

export const PHASE_SEQUENCE = [
  'setup_validate',
  'setup_infer',
  'setup_import',
  'phase_1',
  'phase_2',
  'phase_3',
  'phase_35',
  'phase_4a',
  'phase_4b',
  'phase_5',
  'phase_6',
] as const;
export type PhaseName = (typeof PHASE_SEQUENCE)[number];

// Phase labels for the stepper component (short = chip text, long = title attr).
export const PHASE_LABELS: Record<PhaseName, { short: string; long: string }> = {
  setup_validate: { short: 'SV', long: 'setup_validate' },
  setup_infer: { short: 'SI', long: 'setup_infer' },
  setup_import: { short: 'IM', long: 'setup_import' },
  phase_1: { short: '1', long: 'phase_1: Pose Correction' },
  phase_2: { short: '2', long: 'phase_2: Skeleton Align' },
  phase_3: { short: '3', long: 'phase_3: Vertex Groups' },
  phase_35: { short: '3.5', long: 'phase_35' },
  phase_4a: { short: '4a', long: 'phase_4a: Physics A' },
  phase_4b: { short: '4b', long: 'phase_4b: Physics B' },
  phase_5: { short: '5', long: 'phase_5: Materials' },
  phase_6: { short: '6', long: 'phase_6: Export' },
};

export type LoopState =
  | 'idle'
  | 'running_phase'
  | 'negotiating'
  | 'await_confirm'
  | 'error_handling'
  | 'ask_mode'
  | 'done';

// Principled BSDF slot order — must match app/phases/material.py:PRINCIPLED_SLOTS.
// The widget renders one column per slot; index drives the submission shape.
export const PRINCIPLED_SLOTS = [
  'Base Color',
  'Alpha',
  'Roughness',
  'Metallic',
  'Emission',
  'Normal',
] as const;
export type PrincipledSlot = (typeof PRINCIPLED_SLOTS)[number];

export const ARMOR_VARIANTS = ['ff', 'fm', 'mf', 'mm'] as const;
export type ArmorVariant = (typeof ARMOR_VARIANTS)[number];

export const LLM_PROVIDERS = ['anthropic', 'openai_compatible', 'ollama'] as const;
export type LlmProvider = (typeof LLM_PROVIDERS)[number];

// Chain decision bands from app/phases/infer_model_type.py.
export type InferDecision = 'exact' | 'supplement' | 'custom' | 'unsupported';

// Physics chain head as returned by physics_classification → annotated by
// AgentLoop._annotate_chains before reaching the widget event.
export interface ChainHead {
  name: string;
  role: string;
  depth: number;
  parent?: string;
  guessed_nature: string;
  group: 'hair' | 'cloth' | 'ribbon' | 'tail' | 'non_physics' | 'other';
  suggested_type: string;
  suggest_merge: boolean;
}

export interface XPreset {
  name: string;
  slot_count: number;
  description: string;
}

export interface ArmorSet {
  id: string;
  name: string;
}

// Material widget existing_connections sentinel for an image-less wired slot.
// Treated as "no texture" by the widget's chosen-value precedence.
export const CONNECTED_NO_IMAGE = 'connected_no_image';
