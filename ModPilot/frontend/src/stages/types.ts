import type { ChatState } from '@/hooks/useChatState';
import type {
  ClassificationConfirmation,
  MaterialSlotMapping,
} from '@/types/api';
import type { ModelTypeInferredEvent } from '@/types/sse';

export interface StageProps {
  sessionId: string;
  state: ChatState;
  inferredModelType: ModelTypeInferredEvent | null;
  onClassificationSubmit: (
    confirmations: ClassificationConfirmation[],
    summary: string,
  ) => Promise<void>;
  onMaterialSubmit: (
    mappings: MaterialSlotMapping[],
    summary: string,
  ) => Promise<void>;
  onErrorChoice: (keyword: string) => Promise<void>;
}
