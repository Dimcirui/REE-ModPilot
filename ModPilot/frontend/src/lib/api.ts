import type {
  AppConfigGet,
  AppConfigPost,
  AppConfigSaveOk,
  ArmorSetsResponse,
  ChatRequest,
  ChatResponse,
  ClassificationWidgetSubmit,
  InterruptResponse,
  MaterialWidgetSubmit,
  SessionConfigFieldErrors,
  SessionConfigRequest,
  SessionConfigSaveOk,
  WidgetSaveOk,
  XPresetsResponse,
} from '@/types/api';
import { apiUrl } from './origin';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body: unknown = null,
  ) {
    super(message);
  }
}

async function postJson<TReq, TRes>(url: string, body: TReq): Promise<TRes> {
  const res = await fetch(apiUrl(url), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      // body was not json
    }
    throw new ApiError(res.status, `POST ${url} failed: ${res.status}`, parsed);
  }
  return res.json() as Promise<TRes>;
}

async function getJson<TRes>(url: string): Promise<TRes> {
  const res = await fetch(apiUrl(url), { cache: 'no-store' });
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${url} failed: ${res.status}`);
  }
  return res.json() as Promise<TRes>;
}

export const api = {
  sendMessage: (body: ChatRequest) =>
    postJson<ChatRequest, ChatResponse>('/agent/messages', body),

  interrupt: (sessionId: string) =>
    fetch(apiUrl(`/agent/interrupt/${sessionId}`), { method: 'POST' })
      .then(async (res) => {
        if (!res.ok && res.status !== 404) {
          throw new ApiError(res.status, `interrupt failed: ${res.status}`);
        }
        return res.ok ? ((await res.json()) as InterruptResponse) : null;
      })
      .catch(() => null),

  saveSessionConfig: (body: SessionConfigRequest) =>
    postJson<SessionConfigRequest, SessionConfigSaveOk>('/agent/config', body),

  getAppConfig: () => getJson<AppConfigGet>('/app/config'),
  saveAppConfig: (body: AppConfigPost) =>
    postJson<AppConfigPost, AppConfigSaveOk>('/app/config', body),

  getXPresets: () => getJson<XPresetsResponse>('/app/x_presets'),
  getArmorSets: () => getJson<ArmorSetsResponse>('/app/armor_sets'),

  submitClassificationWidget: (body: ClassificationWidgetSubmit) =>
    postJson<ClassificationWidgetSubmit, WidgetSaveOk>(
      '/agent/widget/classification',
      body,
    ),
  submitMaterialWidget: (body: MaterialWidgetSubmit) =>
    postJson<MaterialWidgetSubmit, WidgetSaveOk>('/agent/widget/material', body),
};

export function asSessionConfigFieldErrors(err: unknown): SessionConfigFieldErrors | null {
  if (!(err instanceof ApiError) || err.status !== 422 || !err.body) return null;
  const body = err.body as { detail?: SessionConfigFieldErrors };
  return body.detail && typeof body.detail === 'object' ? body.detail : null;
}
