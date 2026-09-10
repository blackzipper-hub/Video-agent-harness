import type {
  DeepAgentRun,
  DeepAgentRunSummary,
  DeepAgentSelection,
  DeepAgentSnapshot,
  DeepAgentMessage,
  DeepAgentInputFile,
  DeepAgentMessageOptions,
  DeepAgentEvent,
  DeepAgentSkill,
  DeepAgentArtifact,
  DeepAgentSkillLock,
  DeepAgentTokenUsage,
} from './types'

const V2_BASE_URL = '/chat-v1/service/v2'
const STUDIO_BASE_URL = '/chat-v1/service/studio'

interface ResponseModel<T> {
  code: number
  message: string
  data: T
  error_code?: string
}

export class DeepAgentApiError extends Error {
  constructor(
    readonly code: number,
    message: string,
    readonly reason?: string,
  ) {
    super(message)
    this.name = 'DeepAgentApiError'
  }
}

const STOPPED_RESUME_MESSAGE = 'Task stopped. Confirm resume before sending a continuation.'

/**
 * The run snapshot can briefly lag behind the authoritative Runtime build state.
 * Keep this check in one place so the Create workspace can recover that race by
 * retrying through the explicit resume endpoint instead of trapping the user.
 */
export const isStoppedResumeRequired = (error: unknown): boolean => (
  error instanceof DeepAgentApiError
  && error.code === 409
  && (
    error.reason === 'task_stopped_resume_required'
    || error.message.includes(STOPPED_RESUME_MESSAGE)
  )
)

async function parseResponse<T>(response: Response, fallback: string): Promise<ResponseModel<T>> {
  const raw = await response.text()
  if (!raw.trim()) {
    throw new DeepAgentApiError(response.status || 500, fallback)
  }
  try {
    const parsed = JSON.parse(raw)
    if (!response.ok && parsed.detail && !parsed.message) {
      const detail = parsed.detail
      parsed.message = typeof detail === 'string'
        ? detail
        : detail.message || JSON.stringify(detail)
      parsed.error_code = typeof detail === 'object' ? detail.code : undefined
      parsed.code = response.status
    }
    return parsed as ResponseModel<T>
  } catch {
    const message = response.ok
      ? fallback
      : `${fallback} (${response.status}${response.statusText ? ` ${response.statusText}` : ''})`
    throw new DeepAgentApiError(response.status || 500, message)
  }
}

const languageHeaders = (includeContentType: boolean): HeadersInit => ({
  ...(includeContentType ? { 'Content-Type': 'application/json' } : {}),
  'X-App-Language': localStorage.getItem('language') || 'en',
})

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${V2_BASE_URL}${path}`, {
    credentials: 'include',
    ...init,
    headers: requestHeaders(languageHeaders(!(init.body instanceof FormData)), init.headers),
  })
  const result = await parseResponse<T>(response, 'Deep Agent service returned an invalid response')
  if (!response.ok || result.code !== 0) {
    throw new DeepAgentApiError(result.code, result.message || 'Deep Agent request failed', result.error_code)
  }
  return result.data
}

async function studioRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${STUDIO_BASE_URL}${path}`, {
    credentials: 'include',
    ...init,
    headers: requestHeaders(languageHeaders(!(init.body instanceof FormData)), init.headers),
  })
  const result = await parseResponse<T>(response, 'Studio service returned an invalid response')
  if (!response.ok || result.code !== 0) {
    throw new DeepAgentApiError(result.code, result.message || 'Studio request failed', result.error_code)
  }
  return result.data
}

export const createIdempotencyKey = createClientId

export const deepAgentV2Client = {
  listRuns: (signal?: AbortSignal) => request<DeepAgentRunSummary[]>('/runs', { signal }),

  listSkills: (signal?: AbortSignal) => request<DeepAgentSkill[]>('/skills', { signal }),

  listProjectSkills: (threadId: string, signal?: AbortSignal) =>
    studioRequest<DeepAgentSkillLock[]>(`/projects/${encodeURIComponent(threadId)}/skills`, { signal }),

  setProjectSkillEnabled: (threadId: string, skillId: string, enabled: boolean) =>
    studioRequest<DeepAgentSkillLock>(
      `/projects/${encodeURIComponent(threadId)}/skills/${encodeURIComponent(skillId)}/enable`,
      { method: 'POST', body: JSON.stringify({ enabled }) },
    ),

  installProjectSkill: async (bundle: File, overwrite = false) => {
    const body = new FormData()
    body.append('bundle', bundle)
    body.append('overwrite', String(overwrite))
    return studioRequest<{ name: string; skill_count: number }>('/skills/install', {
      method: 'POST',
      body,
    })
  },

  createRun: (
    objective: string,
    threadId?: string,
    options: DeepAgentMessageOptions = {},
    signal?: AbortSignal,
  ) =>
    request<DeepAgentRun>('/runs', {
      method: 'POST',
      signal,
      body: JSON.stringify({
        objective,
        idempotency_key: createIdempotencyKey(),
        ...(threadId ? { thread_id: threadId } : {}),
        ...options,
      }),
    }),

  getRun: (runId: string, signal?: AbortSignal) =>
    request<DeepAgentSnapshot>(`/runs/${encodeURIComponent(runId)}`, { signal }),

  deleteRun: (runId: string) =>
    request<{ deleted: boolean; records: Record<string, number> }>(
      `/runs/${encodeURIComponent(runId)}`,
      { method: 'DELETE' },
    ),

  getMessages: (runId: string, signal?: AbortSignal) =>
    request<DeepAgentMessage[]>(`/runs/${encodeURIComponent(runId)}/messages`, { signal }),

  getEventLog: (runId: string, signal?: AbortSignal) =>
    request<DeepAgentEvent[]>(`/runs/${encodeURIComponent(runId)}/event-log`, { signal }),

  getTokenUsage: (runId: string, signal?: AbortSignal) =>
    request<DeepAgentTokenUsage>(`/runs/${encodeURIComponent(runId)}/token-usage`, { signal }),

  sendMessage: (
    runId: string,
    content: string,
    options: DeepAgentMessageOptions = {},
    signal?: AbortSignal,
  ) =>
    request<DeepAgentRun>(`/runs/${encodeURIComponent(runId)}/messages`, {
      method: 'POST',
      signal,
      body: JSON.stringify({
        content,
        idempotency_key: createIdempotencyKey(),
        ...options,
      }),
    }),

  cancelRun: (runId: string) =>
    request<DeepAgentRun>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),

  resumeRun: (
    runId: string,
    response: string,
    options: DeepAgentMessageOptions = {},
    signal?: AbortSignal,
  ) =>
    request<DeepAgentRun>(`/runs/${encodeURIComponent(runId)}/resume`, {
      method: 'POST',
      signal,
      body: JSON.stringify({
        // Backend ResumeRequest extends MessageRequest → field name is `content`.
        content: response || '继续',
        idempotency_key: createIdempotencyKey(),
        ...options,
      }),
    }),

  selectArtifact: (runId: string, versionId: string) =>
    request<DeepAgentSelection>(
      `/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(versionId)}/select`,
      { method: 'POST' },
    ),

  extractFrame: (
    runId: string,
    body: {
      timestamp: number
      versionId?: string
      videoUrl?: string
      format?: 'jpeg' | 'png'
      select?: boolean
    },
  ) => {
    const path = body.versionId
      ? `/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(body.versionId)}/extract-frame`
      : `/runs/${encodeURIComponent(runId)}/extract-frame`
    return request<DeepAgentArtifact>(path, {
      method: 'POST',
      body: JSON.stringify({
        timestamp: body.timestamp,
        version_id: body.versionId,
        video_url: body.videoUrl,
        format: body.format ?? 'jpeg',
        select: body.select ?? true,
      }),
    })
  },

  uploadFiles: async (files: File[]): Promise<DeepAgentInputFile[]> => {
    const form = new FormData()
    files.forEach((file) =>{  form.append('files', file) })
    const result = await request<{ files: DeepAgentInputFile[] }>('/uploads', {
      method: 'POST',
      body: form,
    })
    return result.files
  },

  eventsUrl: (runId: string, after: number) =>
    `${V2_BASE_URL}/runs/${encodeURIComponent(runId)}/events?after=${after}`,
}
import { requestHeaders } from '../../utils/requestHeaders'
import { createClientId } from '../../utils/createClientId'
