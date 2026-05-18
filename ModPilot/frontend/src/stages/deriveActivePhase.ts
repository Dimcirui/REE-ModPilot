import { PHASE_SEQUENCE, type PhaseName } from '@/types/domain';
import type { PhaseStatus } from '@/hooks/useChatState';

// Pick the phase whose stage should be on the canvas right now.
//   1. If any phase is `active`, use the latest one in PHASE_SEQUENCE order.
//   2. Else if there's a completed phase, stay on it (transition pause between
//      phase_completed and the next phase_started — the user is reviewing).
//   3. Else null (nothing started — show the fallback / setup stage).
export function deriveActivePhase(
  status: Record<PhaseName, PhaseStatus>,
): PhaseName | null {
  let lastActive: PhaseName | null = null;
  let lastDoneIdx = -1;
  for (let i = 0; i < PHASE_SEQUENCE.length; i += 1) {
    const phase = PHASE_SEQUENCE[i];
    const s = status[phase];
    if (s === 'active') lastActive = phase;
    if (s === 'done') lastDoneIdx = i;
  }
  if (lastActive) return lastActive;
  if (lastDoneIdx >= 0) return PHASE_SEQUENCE[lastDoneIdx];
  return null;
}
