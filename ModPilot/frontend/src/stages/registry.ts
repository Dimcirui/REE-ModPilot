import type { ComponentType } from 'react';
import type { PhaseName } from '@/types/domain';
import type { StageProps } from './types';
import { Phase1Stage } from './Phase1Stage';
import { Phase23Stage } from './Phase23Stage';
import { Phase4Stage } from './Phase4Stage';
import { Phase5Stage } from './Phase5Stage';
import { Phase6Stage } from './Phase6Stage';

// Phases not in this map render the FallbackStage (the legacy multi-purpose
// surface — session config form + stepper + viewport + widget slots). As each
// phase gets its own stage in this rebuild, add the entry here.
//
// Multiple phase keys may point to the same component (e.g. phase_2 and
// phase_3 share Phase23Stage); StageRouter keys the cross-fade on the
// component identity, so the stage doesn't remount when the agent advances
// between sibling phases that share a surface.
export const STAGE_REGISTRY: Partial<Record<PhaseName, ComponentType<StageProps>>> = {
  phase_1: Phase1Stage,
  phase_2: Phase23Stage,
  phase_3: Phase23Stage,
  phase_35: Phase4Stage,
  phase_4a: Phase4Stage,
  phase_4b: Phase4Stage,
  phase_5: Phase5Stage,
  phase_6: Phase6Stage,
};
