/* oxlint-disable @stylistic/max-len -- Imported Cuti API signatures retain their legacy wire fields until removal. */
/**
 * API 服务层 - CartoonBook Backend 适配
 * 统一处理所有后端 API 调用
 */

import type {
  ResponseModel,
  ConversationListResponse,
  CreationSearchResponse,
  ConversationDetailResponse,
  CancelTaskResponse,
  RunningTasksResponse,
  ToolInfo,
  UserOption,
  CreditHistoryResponse,
  VideoHistory,
  AudioHistory,
  CharacterHistory,
  CharacterUploadRequest,
  CharacterUploadResponse,
  MediaHistoryV1PagedResponse,
  Folder,
  SubscriptionPlan,
  UserSubscription,
  CreditBreakdown,
  CreateSubscriptionRequest,
  CreateSubscriptionResponse,
  CancelSubscriptionResponse,
  RestoreSubscriptionResponse,
} from '../types/api'

// Imported Cuti endpoints have heterogeneous responses until each legacy API is retired.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type LegacyApiValue = any

// API 基础配置
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'
// Cuti-VideoAgent 专用 API 基础 URL（env 控制走 K8s 还是 EC2）
export const CUTI_VIDEO_API_BASE_URL = '/api/cv-v1'
// VideoChatAgent（与 main FastAPI 网关同域时走 /chat-v1/service；本地见 vite proxy）
export const VIDEOCHAT_AGENT_SERVICE_PREFIX = '/chat-v1/service'
export const AGENT_ROUTER_SUBMIT_URL = `${VIDEOCHAT_AGENT_SERVICE_PREFIX}/agent-router/stream`
const AGENT_ROUTER_SERVICE_HEALTH_URL = `${VIDEOCHAT_AGENT_SERVICE_PREFIX}/health`
// 本地 dev 探测端口：launch.json 里 [Video Stack] VideoChatAgent 用 SERVER_PORT=9014 避开同机其他人占用的 9004。
// 这里变量名保留 9004 后缀只是历史习惯（避免大面积改名），实际端口以 launch.json SERVER_PORT 为准。
const LOCAL_9004_GATEWAY_BASE_URL = 'http://localhost:9014'
const LOCAL_9004_AGENT_ROUTER_SUBMIT_URL = `${LOCAL_9004_GATEWAY_BASE_URL}${AGENT_ROUTER_SUBMIT_URL}`
const LOCAL_9004_HEALTH_CHECK_URL = `${LOCAL_9004_GATEWAY_BASE_URL}${AGENT_ROUTER_SERVICE_HEALTH_URL}`

let _agentRouterSubmitUrlCache: string | null = null
let _agentRouterSubmitProbePromise: Promise<string> | null = null

const isLocalDevHttpRuntime = (): boolean => {
  if (typeof window === 'undefined') return false
  return (
    window.location.protocol === 'http:' &&
    ['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname)
  )
}

const probeAgentRouterSubmitUrl = async (): Promise<string> => {
  if (!isLocalDevHttpRuntime()) {
    return AGENT_ROUTER_SUBMIT_URL
  }

  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), 1200)
  const currentLang = localStorage.getItem('language') || 'en'

  try {
    const healthResp = await fetch(LOCAL_9004_HEALTH_CHECK_URL, {
      method: 'GET',
      credentials: 'include',
      headers: {
        'X-App-Language': currentLang,
      },
      signal: controller.signal,
    })
    if (healthResp.ok) {
      console.log('🌐 [API] Detected local 9004 service, using:', LOCAL_9004_AGENT_ROUTER_SUBMIT_URL)
      return LOCAL_9004_AGENT_ROUTER_SUBMIT_URL
    }
  } catch (error) {
    console.warn('🌐 [API] local 9004 probe failed, fallback to default submit URL:', error)
  } finally {
    window.clearTimeout(timeoutId)
  }

  return AGENT_ROUTER_SUBMIT_URL
}

const resolveAgentRouterSubmitUrl = async (): Promise<string> => {
  if (_agentRouterSubmitUrlCache) {
    return _agentRouterSubmitUrlCache
  }
  if (_agentRouterSubmitProbePromise) {
    return _agentRouterSubmitProbePromise
  }

  _agentRouterSubmitProbePromise = probeAgentRouterSubmitUrl()
    .then((resolvedUrl) => {
      _agentRouterSubmitUrlCache = resolvedUrl
      return resolvedUrl
    })
    .finally(() => {
      _agentRouterSubmitProbePromise = null
    })

  return _agentRouterSubmitProbePromise
}

export interface AgentSubmitTaskResult {
  code: number
  message: string
  data: {
    run_id?: string
    thread_id?: string
    conversation_id?: number
    status?: string
    message?: string
    ignored?: boolean
  }
  stream?: ReadableStream<Uint8Array>
  directStream?: boolean
}

/** API 业务错误（code !== 0），便于前端按错误码做 i18n */
export class ApiBusinessError extends Error {
  readonly code: number

  constructor(code: number, message: string) {
    super(message)
    this.name = 'ApiBusinessError'
    this.code = code
  }
}

export type PublishCreationRequest = {
  title: string
  description?: string
  video_url: string
  cover_url: string
  tags?: string[]
  source?: string
  thread_id?: string
}

// submitTask 请求去重：同一 thread_id 的并发 POST 只发送一次
const _submitTaskPending = new Map<string, Promise<AgentSubmitTaskResult>>()
/** 当前进行中的 submitTask POST（directStream SSE） */
let _activeSubmitAbortController: AbortController | null = null
let _activeSubmitDedupeKey: string | null = null

/**
 * 通用请求函数
 */
async function makeRequest<T>(
  endpoint: string,
  options: RequestInit = {},
  baseUrl: string = API_BASE_URL,
): Promise<ResponseModel<T>> {
  // 开发环境：使用相对路径，走 Vite 代理（解决跨域 cookie 问题）
  // 生产环境：使用空字符串，因为前后端在同一域名下
  const url = `${baseUrl}${endpoint}`
  // const url = import.meta.env.DEV ? `${baseUrl}${endpoint}` : `${baseUrl}${endpoint}`;
  const currentLang = localStorage.getItem('language') || 'en'
  const defaultOptions: RequestInit = {
    headers: {
      'Content-Type': 'application/json',
      'X-App-Language': currentLang, // 注入自定义语言 Header
    },
    credentials: 'include', // 重要：包含 cookies
    ...options,
  }

  try {
    const response = await fetch(url, defaultOptions)

    // 解析响应
    const result: ResponseModel<T> = await response.json()

    // 检查业务逻辑错误
    if (result.code !== 0) {
      throw new ApiBusinessError(result.code, result.message || 'API request failed')
    }

    return result
  } catch (error) {
    console.error(`API request failed: ${url}`, error)
    throw error
  }
}

/**
 * 用户相关 API
 */
export const userApi = {
  /**
   * 获取用户积分信息
   */
  async getUserCredits(): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/users/credits', {
      method: 'GET',
    })
  },

  async getCreditHistory(params?: {
    limit?: number
    offset?: number
  }): Promise<CreditHistoryResponse> {
    const queryParams = new URLSearchParams()
    if (params?.limit) queryParams.append('limit', params.limit.toString())
    if (params?.offset) queryParams.append('offset', params.offset.toString())

    const queryString = queryParams.toString()
    const endpoint = queryString ? `/users/credit-history?${queryString}` : '/users/credit-history'

    const currentLang = localStorage.getItem('language') || 'en'
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      method: 'GET',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-App-Language': currentLang,
      },
    })

    return response.json()
  },

  async getCreditBreakdown(): Promise<ResponseModel<CreditBreakdown>> {
    return makeRequest<CreditBreakdown>('/users/credits/breakdown', {
      method: 'GET',
    })
  },

  async uploadAvatar(file: File): Promise<ResponseModel<{ avatar_url: string }>> {
    const formData = new FormData()
    formData.append('file', file)
    return makeRequest<{ avatar_url: string }>('/users/me/avatar', {
      method: 'POST',
      body: formData,
      headers: {},
    })
  },
}

/**
 * Creations publish API
 */
export const creationsApi = {
  async uploadVideo(file: File): Promise<ResponseModel<{ video_url: string }>> {
    const formData = new FormData()
    formData.append('file', file)
    return makeRequest<{ video_url: string }>('/creations/upload-video', {
      method: 'POST',
      body: formData,
      headers: {},
    })
  },

  async uploadCover(file: File): Promise<ResponseModel<{ cover_url: string }>> {
    const formData = new FormData()
    formData.append('file', file)
    return makeRequest<{ cover_url: string }>('/creations/upload-cover', {
      method: 'POST',
      body: formData,
      headers: {},
    })
  },

  async publish(data: PublishCreationRequest): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/creations/publish', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  async getMyPublished(): Promise<ResponseModel<{ items: LegacyApiValue[]; total?: number }>> {
    return makeRequest<{ items: LegacyApiValue[]; total?: number }>('/users/me/creations', {
      method: 'GET',
    })
  },

  async deletePublished(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/users/me/creations/${encodeURIComponent(uuid)}`, {
      method: 'DELETE',
    })
  },
}

export type ExploreFeedItem = {
  uuid: string
  title: string
  description: string
  video_url: string
  cover_url: string
  tags: string
  source: string
  view_count: number
  like_count: number
  comment_count: number
  remix_count: number
  favorite_count?: number
  share_count?: number
  is_favorited?: boolean
  published_at: string
  creator?: {
    user_id: string
    username: string
    avatar_url?: string
  }
  character?: {
    name?: string
    avatar_url?: string
  }
  character_name?: string
  character_avatar_url?: string
  content_hook?: string
  hook?: string
  story_hook?: string
}

export type ExploreFeedData = {
  items: ExploreFeedItem[]
  next_cursor: string
  has_more: boolean
}

/**
 * Explore 信息流（公开 feed，来自数据库）
 */
export const exploreApi = {
  async getFeed(nextCursor?: string | null): Promise<ResponseModel<ExploreFeedData>> {
    const query = nextCursor ? `?next_cursor=${encodeURIComponent(nextCursor)}` : ''
    return makeRequest<ExploreFeedData>(`/explore/feed${query}`, {
      method: 'GET',
    })
  },
}

/**
 * Video Analysis 相关详情 API
 */
export const videoAnalysisApi = {

  /**
   * 获取视频分析详情（按 uuid，仅保留兼容）
   */
  async getAnalysisData(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-analysis/analysis/${uuid}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 按 thread_id 获取该 thread 下最新视频分析（仅返回 style_preferences，用于 StyleSection）
   */
  async getAnalysisDataByThreadId(threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/video-analysis/analysis-by-thread', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取音频转录详情（含音乐线三层：Global + sections + segments，含 emotion/tempo/vocal_presence）
   */
  async getAudioTranscriptionData(uuid: string): Promise<ResponseModel<import('@/types/api').AudioTranscriptionDetailResponse>> {
    return makeRequest<import('@/types/api').AudioTranscriptionDetailResponse>(`/video-analysis/audio-transcription/${uuid}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取故事大纲详情
   */
  async getStoryOutlineData(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-analysis/story-outline/${uuid}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取角色详情
   */
  async getCharactersData(uuids: string[]): Promise<ResponseModel<LegacyApiValue>> {
    const uuidsParam = uuids.join(',')
    return makeRequest<LegacyApiValue>(`/video-analysis/characters?uuids=${uuidsParam}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取详细分镜详情
   */
  async getStoryboardDetailData(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-analysis/storyboard-detail/${uuid}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取视频合成详情
   */
  async getVideoAssemblyData(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-analysis/video-assembly/${uuid}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  async getVideoAssemblyDataByThreadId(threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-analysis/video-assembly/thread/${threadId}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取旁白数据
   */
  async getNarrationsData(conversationId: string, threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-analysis/narrations?conversation_id=${conversationId}&thread_id=${threadId}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取音效数据
   */
  async getAudioEffectsData(conversationId: string, threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-analysis/audio-effects?conversation_id=${conversationId}&thread_id=${threadId}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 根据UUIDs批量获取旁白数据
   */
  async getNarrationsByUuids(uuids: string[]): Promise<ResponseModel<LegacyApiValue>> {
    const uuidsParam = uuids.join(',')
    return makeRequest<LegacyApiValue>(`/video-analysis/narrations/by-uuids?uuids=${uuidsParam}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 根据UUIDs批量获取音效数据
   */
  async getAudioEffectsByUuids(uuids: string[]): Promise<ResponseModel<LegacyApiValue>> {
    const uuidsParam = uuids.join(',')
    return makeRequest<LegacyApiValue>(`/video-analysis/audio-effects/by-uuids?uuids=${uuidsParam}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 方案 A：按 thread_id 聚合，一个 video space 展示该 thread 下所有 run 的数据 */
  async getScenesDataByThreadId(threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/video-analysis/scenes-by-thread', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 根据故事大纲 UUID 获取章节列表（含 uuid，供双击编辑） */
  async getChaptersByOutlineId(outlineUuid: string): Promise<ResponseModel<{ chapters: LegacyApiValue[] }>> {
    return makeRequest<{ chapters: LegacyApiValue[] }>(`/video-analysis/chapters-by-outline/${encodeURIComponent(outlineUuid)}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 编辑章节（直接改主表，无版本表）。uuid 与字段均在 request body。 */
  async patchChapter(body: { uuid: string; title?: string; description?: string; duration?: number; order?: number }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/video-analysis/chapter', {
      method: 'POST',
      body: JSON.stringify(body),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 编辑场景（直接改主表，无版本表）。uuid 与字段均在 request body。 */
  async patchScene(body: { uuid: string; title?: string; description?: string; duration?: number }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/video-analysis/scene', {
      method: 'POST',
      body: JSON.stringify(body),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  async getKeyframesDataByThreadId(threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/video-analysis/keyframes-by-thread', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  async getVideoGenerationsDataByThreadId(threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/video-analysis/video-generations-by-thread', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  async getMusicGenerationsDataByThreadId(threadId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/video-analysis/music-generations-by-thread', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  async getCharactersDataByThreadId(threadId: string, includeVersions = false): Promise<ResponseModel<LegacyApiValue>> {
    const q = includeVersions ? '?include_versions=true' : ''
    return makeRequest<LegacyApiValue>(`/video-analysis/characters-by-thread${q}`, {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },
}

/**
 * 上传音频 Smart Clip 推荐（裁剪仍在前端本地完成）
 */
export const audioApi = {
  async recommendAudioCrop(
    file: File,
    targetDurationSec: number,
  ): Promise<ResponseModel<{ status: string; recommended?: { start_sec: number; end_sec: number } | null; error?: string }>> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('target_duration_sec', String(targetDurationSec))
    const currentLang = localStorage.getItem('language') || 'en'
    const response = await fetch(`${CUTI_VIDEO_API_BASE_URL}/audio/smart-clip/recommend`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: { 'X-App-Language': currentLang },
    })
    const result = await response.json()
    if (result.code !== 0) {
      throw new ApiBusinessError(result.code, result.message || 'Smart clip recommend failed')
    }
    return result
  },
}

/** 同一 run_id 只保留一个 stream，新连接时由前端 abort 掉上一次（仅本 tab） */
const streamAbortControllers: Record<string, AbortController> = {}

/**
 * Agent 对话相关 API
 */
export const agentApi = {
  /**
   * 提交任务（新：返回任务信息，不再是流式响应）
   */
  async submitTask(data: {
    userInput?: string
    files?: File[]
    threadId?: string
    conversationId?: number
    userOption?: UserOption
    agentType?: string
    hasConfirmed?: boolean
    confirmedInputSummary?: string
    generationInput?: string
    originalUserInput?: string
    preferPanelUserOption?: boolean
    /** resume 时传 JSON：{"run_id":"...","interrupt_msgid":123}，此时可不传 userInput */
    resumeData?: string
    /** 为 true 时后端拒绝无 conversation_id/thread_id 的请求，防止误开新会话（续聊场景） */
    requireConversationContext?: boolean
  }): Promise<AgentSubmitTaskResult> {
    const dedupeKey = data.threadId

    // 如果同一 thread_id 已有请求在途中，直接复用那个 Promise，不再发第二次 POST
    if (dedupeKey && _submitTaskPending.has(dedupeKey)) {
      console.warn('[submitTask] Duplicate request for thread:', dedupeKey, '- reusing in-flight promise')
      const pending = _submitTaskPending.get(dedupeKey)
      if (pending) return pending
    }

    // 将 fetch 逻辑包在 IIFE 里，得到一个普通 Promise
    // 必须在任何 await 之前把 promise 注册到 Map，保证后续同步调用能命中去重逻辑
    const submitDedupeKey = dedupeKey || `ephemeral:${Date.now()}:${Math.random().toString(36).slice(2, 9)}`

    const promise = (async () => {
      if (_activeSubmitAbortController) {
        _activeSubmitAbortController.abort()
      }
      const submitAbortController = new AbortController()
      _activeSubmitAbortController = submitAbortController
      _activeSubmitDedupeKey = submitDedupeKey

      const formData = new FormData()
      const isVideoResume = !!data.resumeData && data.resumeData.trim().startsWith('{')
      formData.append('user_input', isVideoResume ? '' : (data.userInput ?? ''))

      if (data.threadId) {
        formData.append('thread_id', data.threadId)
      }

      if (data.conversationId) {
        formData.append('conversation_id', data.conversationId.toString())
      }

      if (data.userOption) {
        formData.append('user_option', JSON.stringify(data.userOption))
      }

      if (data.agentType) {
        formData.append('agent_type', data.agentType)
      }

      if (typeof data.hasConfirmed === 'boolean') {
        formData.append('has_confirmed', String(data.hasConfirmed))
      }

      if (data.confirmedInputSummary) {
        formData.append('confirmed_input_summary', data.confirmedInputSummary)
      }

      if (data.generationInput) {
        formData.append('generation_input', data.generationInput)
      }

      if (data.originalUserInput) {
        formData.append('original_user_input', data.originalUserInput)
      }

      if (data.preferPanelUserOption === true) {
        formData.append('prefer_panel_user_option', 'true')
      }

      if (data.resumeData) {
        formData.append('resume_data', data.resumeData)
      }

      if (data.requireConversationContext === true) {
        formData.append('require_conversation_context', 'true')
      }

      // 添加文件
      if (data.files && data.files.length > 0) {
        data.files.forEach((file) => {
          formData.append('files', file)
        })
      }

      const currentLang = localStorage.getItem('language') || 'en'
      const submitUrl = await resolveAgentRouterSubmitUrl()
      const buildSubmitRequestInit = (): RequestInit => ({
        method: 'POST',
        body: formData,
        credentials: 'include',
        headers: {
          'X-App-Language': currentLang,
          'Accept': 'text/event-stream',
        },
        signal: submitAbortController.signal,
      })

      let response: Response
      try {
        response = await fetch(submitUrl, buildSubmitRequestInit())
      } catch (error) {
        if (submitAbortController.signal.aborted) {
          throw error
        }
        if (submitUrl !== AGENT_ROUTER_SUBMIT_URL) {
          console.warn('🌐 [API] submit via local 9004 failed, retry default submit URL')
          _agentRouterSubmitUrlCache = AGENT_ROUTER_SUBMIT_URL
          response = await fetch(AGENT_ROUTER_SUBMIT_URL, buildSubmitRequestInit())
        } else {
          throw error
        }
      }

      if (!response.ok && submitUrl !== AGENT_ROUTER_SUBMIT_URL && !submitAbortController.signal.aborted) {
        console.warn(`🌐 [API] submit via local 9004 got status ${response.status}, retry default submit URL`)
        _agentRouterSubmitUrlCache = AGENT_ROUTER_SUBMIT_URL
        response = await fetch(AGENT_ROUTER_SUBMIT_URL, buildSubmitRequestInit())
      }

      if (!response.ok) {
        let errorMessage = `HTTP error! status: ${response.status}`
        try {
          const contentType = (response.headers.get('content-type') || '').toLowerCase()
          if (contentType.includes('application/json')) {
            const result = await response.json()
            errorMessage = result?.message || result?.detail || result?.error || errorMessage
          } else {
            const text = await response.text()
            errorMessage = text || errorMessage
          }
        } catch {
          /* keep status fallback */
        }
        throw new Error(errorMessage)
      }

      const contentType = (response.headers.get('content-type') || '').toLowerCase()
      if (!contentType.includes('application/json')) {
        if (!response.body) {
          throw new Error('Response body is null')
        }
        return {
          code: 0,
          message: 'direct stream',
          data: {},
          stream: response.body,
          directStream: true,
        } as AgentSubmitTaskResult
      }

      const result = await response.json()
      if (result.code !== 0) {
        throw new Error(result.message || 'Request failed')
      }

      return result as AgentSubmitTaskResult
    })()

    // 同步注册（在 IIFE 第一个 await 之前），确保并发的第二次调用能命中去重检查
    _submitTaskPending.set(submitDedupeKey, promise)
    if (dedupeKey) {
      _submitTaskPending.set(dedupeKey, promise)
    }
    promise.finally(() => {
      _submitTaskPending.delete(submitDedupeKey)
      if (dedupeKey) {
        _submitTaskPending.delete(dedupeKey)
      }
      if (_activeSubmitDedupeKey === submitDedupeKey) {
        _activeSubmitAbortController = null
        _activeSubmitDedupeKey = null
      }
    })

    return promise
  },

  abortSubmitStream(threadId?: string) {
    if (_activeSubmitAbortController) {
      _activeSubmitAbortController.abort()
      _activeSubmitAbortController = null
    }
    if (threadId) {
      _submitTaskPending.delete(threadId)
    }
    if (_activeSubmitDedupeKey) {
      _submitTaskPending.delete(_activeSubmitDedupeKey)
      _activeSubmitDedupeKey = null
    }
  },

  /**
   * 获取消息流（SSE）- 持续监听直到任务完成
   * 不再需要 fromId 参数，后端会自动从 last_generated_id 开始
   * 流会在任务完成（COMPLETED/FAILED/CANCELLED）时自动结束
   */
  async getMessageStream(
    runId: string,
    usePost: boolean = true,
  ): Promise<ReadableStream<Uint8Array>> {
    if (!runId || !String(runId).trim()) {
      throw new Error('Missing run_id for message stream')
    }
    // 同一 run_id 新连接时先 abort 掉上一次（本 tab 内只保留一个 stream）
    if (streamAbortControllers[runId]) {
      streamAbortControllers[runId].abort()
    }
    const controller = new AbortController()
    streamAbortControllers[runId] = controller

    console.log('🌐 [API] ===== getMessageStream CALLED =====')
    console.log('🌐 [API] run_id:', runId)
    console.log('🌐 [API] usePost:', usePost)
    console.log('🌐 [API] URL:', `${CUTI_VIDEO_API_BASE_URL}/agent-router/messages/stream`)

    let response: Response
    if (usePost) {
      const requestBody = { run_id: runId }
      console.log('🌐 [API] POST body:', JSON.stringify(requestBody))
      const currentLang = localStorage.getItem('language') || 'en'
      response = await fetch(
        `${CUTI_VIDEO_API_BASE_URL}/agent-router/messages/stream`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-App-Language': currentLang,
          },
          credentials: 'include',
          body: JSON.stringify(requestBody),
          signal: controller.signal,
        },
      )
    } else {
      console.log('🌐 [API] Using GET method')
      const currentLang = localStorage.getItem('language') || 'en'
      response = await fetch(
        `${CUTI_VIDEO_API_BASE_URL}/agent-router/messages/${runId}`,
        {
          method: 'GET',
          credentials: 'include',
          headers: {
            'X-App-Language': currentLang,
          },
          signal: controller.signal,
        },
      )
    }

    console.log('🌐 [API] Response status:', response.status)
    console.log('🌐 [API] Response headers:', {
      'content-type': response.headers.get('content-type'),
      'content-length': response.headers.get('content-length'),
      'transfer-encoding': response.headers.get('transfer-encoding'),
    })

    if (!response.ok) {
      console.error('🌐 [API] ❌ Response not OK!')
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    if (!response.body) {
      console.error('🌐 [API] ❌ Response body is null!')
      throw new Error('Response body is null')
    }

    console.log('🌐 [API] ✅ Response body exists, returning stream')
    return response.body
  },

  abortMessageStream(runId?: string) {
    if (!runId) return
    if (streamAbortControllers[runId]) {
      streamAbortControllers[runId].abort()
      Reflect.deleteProperty(streamAbortControllers, runId)
    }
  },

  /**
   * 按当前选中的 image_tool / video_tool / lipsync_tool 获取支持的 aspect_ratio、resolution、lipsync_video_tools 等（供前端展示/置灰）
   */
  async getOptionsCapabilities(params?: { image_tool?: string; video_tool?: string; lipsync_tool?: string }): Promise<ResponseModel<{
    aspect_ratios: { value: string; label: string }[]
    resolutions: { value: string; label: string }[]
    warnings: string[]
    lipsync_video_tools?: { value: string; label: string }[]
  }>> {
    const search = new URLSearchParams()
    if (params?.image_tool) search.set('image_tool', params.image_tool)
    if (params?.video_tool) search.set('video_tool', params.video_tool)
    if (params?.lipsync_tool) search.set('lipsync_tool', params.lipsync_tool)
    const q = search.toString()
    return makeRequest<{
      aspect_ratios: { value: string; label: string }[]
      resolutions: { value: string; label: string }[]
      warnings: string[]
      lipsync_video_tools?: { value: string; label: string }[]
    }>(`/agent-router/options/capabilities${q ? `?${q}` : ''}`, {
      method: 'GET',
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取任务状态（使用 run_id）
   */
  async getTaskStatus(runId: string): Promise<ResponseModel<{
    run_id: string
    status: string
    is_terminal?: boolean // 后端返回：终态含 completed/failed/cancelled/interrupted
    progress?: string
    current_step?: string
    last_generated_event?: string
    last_generated_id?: string
    analysis_uuid?: string
    story_outline_uuid?: string
    video_assembly_uuid?: string
  }>> {
    return makeRequest<{
      run_id: string
      status: string
      is_terminal?: boolean
      progress?: string
      current_step?: string
      last_generated_event?: string
      last_generated_id?: string
      analysis_uuid?: string
      story_outline_uuid?: string
      video_assembly_uuid?: string
    }>('/agent-router/task/status', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 创建流式对话请求（保留向后兼容，内部调用submitTask + getMessageStream）
   * @deprecated 使用 submitTask + getMessageStream 代替
   */
  async createStreamRequest(data: {
    userInput: string
    files?: File[]
    threadId?: string
    conversationId?: number
    userOption?: UserOption
    agentType?: string
  }): Promise<ReadableStream<Uint8Array>> {
    // 向后兼容：先提交任务，再获取消息流
    const taskResponse = await this.submitTask(data)
    const { run_id } = taskResponse.data
    return this.getMessageStream(run_id)
  },

  /**
   * 取消任务（thread_id 与 run_id 至少传一个）
   */
  async cancelTask(params: { threadId?: string; runId?: string }): Promise<ResponseModel<CancelTaskResponse>> {
    const threadId = params.threadId?.trim()
    const runId = params.runId?.trim()
    if (!threadId && !runId) {
      throw new Error('cancelTask requires thread_id or run_id')
    }
    return makeRequest<CancelTaskResponse>('/agent-router/cancel', {
      method: 'POST',
      body: JSON.stringify({
        ...(threadId ? { thread_id: threadId } : {}),
        ...(runId ? { run_id: runId } : {}),
      }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 关闭「本次 interrupt」的 15s 自动继续（仍可手动点继续）；须 full_auto / 前端自动继续开启时才有意义。
   */
  async dismissInterruptAutoResume(body: {
    thread_id: string
    interrupted_run_id: string
    interrupt_msgid: number
  }): Promise<ResponseModel<{ ok: boolean }>> {
    return makeRequest<{ ok: boolean }>('/agent-router/dismiss-interrupt-auto-resume', {
      method: 'POST',
      body: JSON.stringify({
        thread_id: body.thread_id,
        interrupted_run_id: body.interrupted_run_id,
        interrupt_msgid: body.interrupt_msgid,
      }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 视频 pipeline resume：直接发给 VideoAgent（VA 有 interrupt checkpoint，ChatAgent 没有）
   */
  async submitVideoResume(data: {
    threadId: string
    resumeData: string
  }): Promise<AgentSubmitTaskResult> {
    const formData = new FormData()
    formData.append('thread_id', data.threadId)
    formData.append('resume_data', data.resumeData)
    const currentLang = localStorage.getItem('language') || 'en'
    const response = await fetch(`${CUTI_VIDEO_API_BASE_URL}/agent-router/stream`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: {
        'X-App-Language': currentLang,
      },
    })
    const result = await response.json()
    if (!response.ok) {
      throw new Error(result?.message || `VideoAgent resume failed: ${response.status}`)
    }
    if (result.code !== 0) {
      throw new ApiBusinessError(result.code, result.message || 'Request failed')
    }
    if (!result.data?.run_id) {
      throw new ApiBusinessError(result.code ?? -1, result.message || 'missing run_id')
    }
    return result as AgentSubmitTaskResult
  },

  /**
   * 获取运行中的任务列表
   */
  async getRunningTasks(): Promise<ResponseModel<RunningTasksResponse>> {
    return makeRequest<RunningTasksResponse>('/agent-router/running-tasks', {}, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 提交澄清回复
   */
  async submitClarification(threadId: string, data: {
    clarification_answers: Record<string, string>
    clarification_data: LegacyApiValue
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/agent-router/submit-clarification', {
      method: 'POST',
      body: JSON.stringify({
        thread_id: threadId,
        clarification_answers: data.clarification_answers,
        clarification_data: data.clarification_data,
      }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 更新音乐提示词
   */
  async updateMusicPrompt(musicUuid: string, versionUuid: string, musicPrompt: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/agent-router/music-editing/update-prompt/${musicUuid}/${versionUuid}`, {
      method: 'PUT',
      body: JSON.stringify({ music_prompt: musicPrompt }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 生成关键帧提示词
   */
  async generateKeyframePrompt(request: {
    keyframe_uuid: string
    version_number: number
    edit_instruction: string
    conversation_id: number
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/agent-router/keyframe/generate-prompt', {
      method: 'POST',
      body: JSON.stringify(request),
    }, CUTI_VIDEO_API_BASE_URL)
  },
}

/**
 * 对话历史相关 API (迁移到 Cuti-VideoAgent)
 */
export const conversationApi = {
  /**
   * 获取对话列表
   */
  /**
   * 获取对话列表（使用 thread_id）
   */
  async getConversations(
    page = 1,
    size = 20,
    opts?: { q?: string; start_time?: string; end_time?: string; agent_type?: string },
  ): Promise<ResponseModel<ConversationListResponse>> {
    return makeRequest<ConversationListResponse>('/conversations', {
      method: 'POST',
      body: JSON.stringify({ page, size, ...(opts || {}) }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 检索「我的作品」成片（按标题/首条用户输入做子串匹配，时间按 created_at 过滤）
   */
  async searchCreations(
    page = 1,
    size = 20,
    opts?: { q?: string; start_time?: string; end_time?: string; success_only?: boolean },
  ): Promise<ResponseModel<CreationSearchResponse>> {
    return makeRequest<CreationSearchResponse>('/creations/search', {
      method: 'POST',
      body: JSON.stringify({ page, size, ...(opts || {}) }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取对话详情（使用 thread_id）
   */
  async getConversationDetail(threadId: string): Promise<ResponseModel<ConversationDetailResponse>> {
    return makeRequest<ConversationDetailResponse>('/conversation/detail', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 删除对话（使用 thread_id）
   */
  async deleteConversation(threadId: string): Promise<ResponseModel<{ message: string }>> {
    return makeRequest('/conversation/delete', {
      method: 'POST',
      body: JSON.stringify({ thread_id: threadId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 获取当前用户某次 run 的完整任务详情（与 admin 视频任务详情同结构）
   * run_id 即 task_id（LangGraph run_id）
   */
  async getConversationTaskDetail(runId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/conversation/task-detail', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId }),
    }, CUTI_VIDEO_API_BASE_URL)
  },
}

/**
 * 视频片段相关 API
 */
export const videoSegmentsApi = {
  /**
   * 根据UUID获取视频片段详情
   */
  async getVideoSegment(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/video-segments/${uuid}`, {}, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 根据UUID获取Lipsync生成数据
   */
  async getLipsyncGeneration(lipsyncUuid: string, conversationId?: string): Promise<ResponseModel<LegacyApiValue>> {
    const params = conversationId ? `?conversation_id=${conversationId}` : ''
    return makeRequest<LegacyApiValue>(`/video-segments/lipsync/${lipsyncUuid}${params}`, {}, CUTI_VIDEO_API_BASE_URL)
  },
}

/**
 * 工具相关 API
 */
export const toolsApi = {
  /**
   * 获取图像生成工具列表
   */
  async getImageGenerationTools(): Promise<ResponseModel<ToolInfo[]>> {
    return makeRequest<ToolInfo[]>('/tools/image-generation')
  },

  /**
   * 获取视频生成工具列表
   */
  async getVideoGenerationTools(): Promise<ResponseModel<ToolInfo[]>> {
    return makeRequest<ToolInfo[]>('/tools/video-generation')
  },

  /**
   * 获取 Flux 后端列表
   */
  async getFluxBackends(): Promise<ResponseModel<ToolInfo[]>> {
    return makeRequest<ToolInfo[]>('/tools/flux-backends')
  },
}

// ============ Admin API ============
export const adminApi = {
  /**
   * 时间线仅返回 run 列表，每条 run 带 user_input、run_type 及全部计费字段。Query: page, size, thread_id, user_id；agent_type、run_type、status 支持多选（数组会传多个同名字段）
   */
  async getUserInputsAndRuns(params: {
    page?: number
    size?: number
    thread_id?: string
    user_id?: string
    agent_type?: string | string[]
    run_type?: string | string[]
    status?: string | string[]
  }): Promise<ResponseModel<{
    items: Array<{
      item_type: 'run'
      run_id: string
      thread_id: string
      user_id: string
      conversation_id?: number
      user_input?: string
      user_input_files?: unknown
      agent_type?: string
      status?: string
      run_type?: string
      created_at?: string
      updated_at?: string
      completed_at?: string
      error_message?: string
      billing_status?: string
      cost_credits?: number
      langsmith_cost?: number
      cost?: number
      cost_calculated?: boolean
      credits_deducted?: boolean
      credits_amount?: number
    }>
    total: number
    page: number
    size: number
  }>> {
    const q = new URLSearchParams()
    q.set('page', String(params.page ?? 1))
    q.set('size', String(params.size ?? 50))
    if (params.thread_id) q.set('thread_id', params.thread_id)
    if (params.user_id) q.set('user_id', params.user_id)
    if (params.agent_type != null) {
      if (Array.isArray(params.agent_type)) params.agent_type.forEach(v => q.append('agent_type', v))
      else if (params.agent_type) q.set('agent_type', params.agent_type)
    }
    if (params.run_type != null) {
      if (Array.isArray(params.run_type)) params.run_type.forEach(v => q.append('run_type', v))
      else if (params.run_type) q.set('run_type', params.run_type)
    }
    if (params.status != null) {
      if (Array.isArray(params.status)) params.status.forEach(v => q.append('status', v))
      else if (params.status) q.set('status', params.status)
    }
    return makeRequest<LegacyApiValue>(`/admin/user-inputs-and-runs?${q}`, { method: 'GET' })
  },

  /**
   * 获取所有用户列表（支持分页、搜索、按状态筛选）
   */
  async getAllUsers(page: number = 1, size: number = 10, search?: string, status?: string): Promise<ResponseModel<LegacyApiValue>> {
    const params = new URLSearchParams({
      page: page.toString(),
      size: size.toString(),
    })
    if (search) {
      params.append('search', search)
    }
    if (status) {
      params.append('status', status)
    }
    return makeRequest<LegacyApiValue>(`/admin/users?${params}`, {
      method: 'GET',
    })
  },

  /**
   * 获取所有邀请码列表（支持分页、搜索、按状态筛选：new/sent/used/disabled，按来源筛选：admin/user）
   */
  async getAllInviteCodes(page: number = 1, size: number = 10, search?: string, status?: string, source?: string): Promise<ResponseModel<LegacyApiValue>> {
    const params = new URLSearchParams({
      page: page.toString(),
      size: size.toString(),
    })
    if (search) {
      params.append('search', search)
    }
    if (status) {
      params.append('status', status)
    }
    if (source) {
      params.append('source', source)
    }
    return makeRequest<LegacyApiValue>(`/admin/invite-codes?${params}`, {
      method: 'GET',
    })
  },

  /**
   * 获取邀请码统计数据
   */
  async getInviteCodeStats(): Promise<ResponseModel<Record<string, number>>> {
    return makeRequest<Record<string, number>>('/admin/invite-codes/stats', {
      method: 'GET',
    })
  },

  /**
   * 批量禁用邀请码
   */
  async disableInviteCodes(ids: number[]): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/admin/invite-codes/disable', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    })
  },

  /**
   * 获取用户积分信息（路径与 Go 后端一致：/admin/users/{userId}/credits）
   */
  async getUserCredits(userId: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/admin/users/${userId}/credits`, {
      method: 'GET',
    })
  },

  /**
   * 获取用户积分历史
   */
  async getUserCreditHistory(userId: string): Promise<ResponseModel<LegacyApiValue[]>> {
    return makeRequest<LegacyApiValue[]>(`/admin/users/${userId}/credit-history`, {
      method: 'GET',
    })
  },

  /**
   * 添加用户积分
   */
  async addUserCredits(userId: string, amount: number, description?: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/admin/users/${userId}/add-credits`, {
      method: 'POST',
      body: JSON.stringify({
        amount,
        description: description || '管理员添加积分',
      }),
    })
  },

  /**
   * 批量生成邀请码
   */
  async generateBatchInviteCodes(request: {
    amount: number
    expires_days?: number
    description?: string
  }): Promise<ResponseModel<LegacyApiValue[]>> {
    return makeRequest<LegacyApiValue[]>('/admin/batch-invite-codes', {
      method: 'POST',
      body: JSON.stringify(request),
    })
  },

  /**
   * 批量标记邀请码已发送/取消已发送
   */
  async markInviteCodesSent(ids: number[], sent: boolean): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/admin/invite-codes/mark-sent', {
      method: 'POST',
      body: JSON.stringify({ ids, sent }),
    })
  },

  // ---------- Explore 作品管理 (demo 视频，Go 后端 /api) ----------
  /**
   * 获取 Explore 作品列表（分页，支持按状态筛选、按标题/用户ID搜索）
   */
  async getCreations(
    page: number,
    size: number,
    status?: string,
    search?: string,
  ): Promise<ResponseModel<{ items: LegacyApiValue[]; total: number; page: number; size: number }>> {
    const params = new URLSearchParams({ page: String(page), size: String(size) })
    if (status) params.set('status', status)
    if (search) params.set('search', search)
    return makeRequest<{ items: LegacyApiValue[]; total: number; page: number; size: number }>(
      `/admin/creations?${params}`,
      { method: 'GET' },
    )
  },

  /**
   * 编辑 Explore 作品（仅传需要修改的字段）
   */
  async updateCreation(
    uuid: string,
    payload: {
      title?: string
      description?: string
      video_url?: string
      cover_url?: string
      tags?: string[]
      status?: string
    },
  ): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/admin/creations/${encodeURIComponent(uuid)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  /**
   * 删除 Explore 作品（软删除）
   */
  async deleteCreation(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/admin/creations/${encodeURIComponent(uuid)}`, {
      method: 'DELETE',
    })
  },

  // ---------- 精选风格提示词（Cuti-VideoAgent 后端 /api/cuti） ----------
  async getCuratedStylePrompts(params: {
    category?: string
    limit?: number
    offset?: number
  } = {}): Promise<ResponseModel<LegacyApiValue[]>> {
    const { category, limit = 200, offset = 0 } = params
    const q = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (category) q.set('category', category)
    return makeRequest<LegacyApiValue[]>(`/admin/curated-style-prompts?${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  async getCuratedStylePromptByUuid(uuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/admin/curated-style-prompts/${uuid}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  async createCuratedStylePrompt(body: {
    category: string
    name: string
    description_en: string
    name_zh?: string
    thumbnail_url?: string
    description_zh?: string
    sort_order?: number
    is_available?: boolean
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/admin/curated-style-prompts', {
      method: 'POST',
      body: JSON.stringify(body),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  async updateCuratedStylePrompt(uuid: string, body: {
    category?: string
    name?: string
    name_zh?: string
    thumbnail_url?: string
    description_en?: string
    description_zh?: string
    sort_order?: number
    is_available?: boolean
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/admin/curated-style-prompts/${uuid}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  async deleteCuratedStylePrompt(uuid: string): Promise<ResponseModel<boolean>> {
    return makeRequest<boolean>(`/admin/curated-style-prompts/${uuid}`, { method: 'DELETE' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 上传缩略图到 S3，返回 { url } */
  async uploadCuratedStyleThumbnail(file: File): Promise<ResponseModel<{ url: string }>> {
    const formData = new FormData()
    formData.append('file', file)
    const currentLang = localStorage.getItem('language') || 'en'
    const response = await fetch(`${CUTI_VIDEO_API_BASE_URL}/admin/curated-style-prompts/upload-thumbnail`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: {
        'X-App-Language': currentLang,
      },
    })
    const result: ResponseModel<{ url: string }> = await response.json()
    if (result.code !== 0) throw new Error(result.message || '上传失败')
    return result
  },

  /** 监控：当前机器负载（CPU/内存/存储/磁盘 I/O），手动点击获取，不实时刷新 */
  async getMonitoringSystemLoad(): Promise<ResponseModel<{
    available: boolean
    reason?: string
    cpu?: { percent: number; description: string }
    memory?: { percent: number; used_mb: number; total_mb: number; description: string }
    disk?: { percent: number; used_gb: number; total_gb: number; path: string; description: string }
    disk_io?: { read_mb: number | null; write_mb: number | null; read_count: number | null; write_count: number | null; description: string; read_mb_per_s?: number; write_mb_per_s?: number; rate_note?: string; cumulative_note?: string }
  }>> {
    return makeRequest<LegacyApiValue>('/admin/monitoring/system-load', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 监控：SQS、Worker、Redis 限流（以 AppConfig 为基准完整展示） */
  async getMonitoringStats(): Promise<ResponseModel<{
    sqs: { available: boolean; pending?: number; in_flight?: number; delayed?: number; reason?: string }
    worker: { in_flight: number | null; description: string }
    redis_rate_limit: LegacyApiValue
    appconfig_rate_limits: {
      available: boolean
      by_provider?: Record<string, {
        tool_types: Array<{ tool_type: string; rules: Array<{ strategy: string; limit: number; window_seconds: number; accounts: Array<{ account: string; current: number }> }> }>
        shared: Array<{ shared_key: string; rules: Array<{ strategy: string; limit: number; window_seconds: number; accounts: Array<{ account: string; current: number }> }> }>
      }>
      reason?: string
    }
  }>> {
    return makeRequest<LegacyApiValue>('/admin/monitoring', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },

  /** Prompt Shield 审计：越狱/套取等命中记录（shield_events 表） */
  async getShieldEvents(params?: {
    page?: number
    size?: number
    user_id?: string
    layer?: string
  }): Promise<ResponseModel<{
    items: Array<{
      id: number
      uuid: string
      created_at: string | null
      user_id: string | null
      thread_id: string | null
      run_id: string | null
      layer: string
      reason: string
      matched_snippet: string | null
      extra: Record<string, unknown> | null
    }>
    total: number
    page: number
    size: number
    warning?: string
  }>> {
    const q = new URLSearchParams()
    q.set('page', String(params?.page ?? 1))
    q.set('size', String(params?.size ?? 50))
    if (params?.user_id) q.set('user_id', params.user_id)
    if (params?.layer) q.set('layer', params.layer)
    return makeRequest<LegacyApiValue>(`/admin/shield-events?${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 智能测试 - 基准 Tool 类型列表 */
  async getSmartTestingBaselineToolTypes(): Promise<ResponseModel<{ t2i: string[]; i2i: string[]; i2v: string[]; t2v: string[] }>> {
    return makeRequest<{ t2i: string[]; i2i: string[]; i2v: string[]; t2v: string[] }>('/admin/smart-testing/baseline-tool-types', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 启动基准运行 */
  async startSmartTestingBaselineRun(params: {
    prompts: string[]
    tool_types: string[]
    start_image_url?: string
    duration?: number
    max_concurrency?: number
    reference_image_urls_per_prompt?: (string[] | null)[]
  }): Promise<ResponseModel<{ run_id: string }>> {
    return makeRequest<{ run_id: string }>('/admin/smart-testing/baseline-runs/start', {
      method: 'POST',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 查询基准运行状态 */
  async getSmartTestingBaselineRun(runId: string): Promise<ResponseModel<{
    run_id: string
    status: string
    config?: LegacyApiValue
    results_count?: number
    report_url?: string
    error?: string
  }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/baseline-runs/${encodeURIComponent(runId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 列出指定场景(t2i/i2i/i2v/t2v)下的 baseline prompt 批次（每个场景独立 dataset） */
  async listSmartTestingBaselineBatches(scenario: string): Promise<ResponseModel<Array<{ batch_id: string; name: string; prompts_count: number; created_at?: string; has_image_urls?: boolean }>>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/baseline-prompts/batches?scenario=${encodeURIComponent(scenario)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 列出基准运行（最新在前） */
  async listSmartTestingBaselineRuns(limit?: number): Promise<ResponseModel<Array<{ run_id: string; status: string; created_at?: string; report_url?: string; results_count?: number; prompts_count?: number; tool_types?: string[] }>>> {
    const q = limit != null ? `?limit=${limit}` : ''
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/baseline-runs${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 获取某场景下某批次详情（含 items 时为 prompt+url 对） */
  async getSmartTestingBaselineBatch(scenario: string, batchId: string): Promise<ResponseModel<{ batch_id: string; name: string; prompts: string[]; items?: Array<{ prompt: string; image_url?: string }>; created_at?: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/baseline-prompts/batches/${encodeURIComponent(scenario)}/${encodeURIComponent(batchId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 创建 baseline 批次，归属到指定场景的 dataset */
  async createSmartTestingBaselineBatch(params: { scenario: string; name: string; prompts?: string[]; items?: Array<{ prompt: string; image_url?: string }> }): Promise<ResponseModel<{ batch_id: string; name: string; prompts_count: number; created_at?: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/baseline-prompts/batches', {
      method: 'POST',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 更新某场景下某 baseline 批次（名称和/或 items） */
  async updateSmartTestingBaselineBatch(scenario: string, batchId: string, params: { name?: string; items?: Array<{ prompt: string; image_url?: string }> }): Promise<ResponseModel<{ batch_id: string; name: string; prompts_count: number; created_at?: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/baseline-prompts/batches/${encodeURIComponent(scenario)}/${encodeURIComponent(batchId)}`, {
      method: 'PUT',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 删除某场景下某 baseline 批次 */
  async deleteSmartTestingBaselineBatch(scenario: string, batchId: string): Promise<ResponseModel<unknown>> {
    return makeRequest<unknown>(`/admin/smart-testing/baseline-prompts/batches/${encodeURIComponent(scenario)}/${encodeURIComponent(batchId)}`, { method: 'DELETE' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - AI 批量生成 prompt（仅文案，无图），可指定 scenario 保存到对应 dataset */
  async generateSmartTestingBaselinePrompts(params: { user_input: string; count: number; scenario?: string; save_batch_name?: string }): Promise<ResponseModel<{ batch_id: string; name: string; prompts_count: number; prompts: string[]; created_at?: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/baseline-prompts/generate-with-agent', {
      method: 'POST',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 根据图片 URL + 用户描述生成 (prompt, image_url) 列表 */
  async generateSmartTestingBaselinePromptsFromImages(params: { image_urls: string[]; user_input: string; count: number }): Promise<ResponseModel<{ items: Array<{ prompt: string; image_url?: string }> }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/baseline-prompts/generate-from-images', {
      method: 'POST',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 获取当前 Keyframe 三件套 + Video 三件套（6 份） */
  async getSmartTestingProductionPromptsCurrent(): Promise<ResponseModel<{ files: Record<string, string>; keyframe: Array<{ key: string; label: string }>; video: Array<{ key: string; label: string }> }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/production-prompts/current', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 用 AI 修改某份 prompt（返回草稿） */
  async modifySmartTestingProductionPrompt(params: { key: string; user_input: string; current_content?: string }): Promise<ResponseModel<{ key: string; draft_content: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/production-prompts/modify-with-agent', {
      method: 'POST',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 保存变体（files: key -> content） */
  async saveSmartTestingProductionVariant(params: { variant_name: string; files: Record<string, string> }): Promise<ResponseModel<{ variant_id: string; name: string; created_at: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/production-prompts/save-variant', {
      method: 'POST',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 列出 prompt 变体 */
  async listSmartTestingProductionVariants(): Promise<ResponseModel<Array<{ variant_id: string; name: string; created_at?: string; keys?: string[] }>>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/production-prompts/variants', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 获取某变体内容 */
  async getSmartTestingProductionVariant(variantId: string): Promise<ResponseModel<{ variant_id: string; name: string; created_at?: string; files: Record<string, string> }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/production-prompts/variants/${encodeURIComponent(variantId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 列出 mock 数据集 */
  async listSmartTestingProductionDatasets(): Promise<ResponseModel<Array<{ dataset_id: string; name: string; created_at?: string; shots_count?: number }>>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/production-datasets', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 创建 mock 数据集 */
  async createSmartTestingProductionDataset(params: { name: string; shots: Record<string, unknown>[]; character_images?: Record<string, unknown> }): Promise<ResponseModel<{ dataset_id: string; name: string; created_at: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/production-datasets', {
      method: 'POST',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 获取 mock 数据集详情 */
  async getSmartTestingProductionDataset(datasetId: string): Promise<ResponseModel<{ dataset_id: string; name: string; created_at?: string; shots: unknown[]; character_images: Record<string, unknown> }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/production-datasets/${encodeURIComponent(datasetId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 获取 baseline 参考图数据集 */
  async getSmartTestingBaselineDatasetImages(): Promise<ResponseModel<{ urls: string[]; description?: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/baseline-datasets/images', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 写入 baseline 参考图数据集 */
  async putSmartTestingBaselineDatasetImages(params: { urls: string[]; description?: string }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/baseline-datasets/images', {
      method: 'PUT',
      body: JSON.stringify(params),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 智能测试 - 获取合格组合（aspect_ratio + resolution + image_tool + video_tool） */
  async getSmartTestingEligibleCombinations(): Promise<ResponseModel<Array<{ case_id: string; aspect_ratio: string; resolution: string; image_tool: string; video_tool: string; duration: number }>>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/eligible-combinations', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 启动完整测试（功能 + 效果）或仅效果测试 */
  async startSmartTestingFullRun(params: { function_case_ids?: string[]; effect_dataset_ids: string[]; effect_times: number; effect_tool_combo_ids?: string[] }): Promise<ResponseModel<{ full_run_id: string; message: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/full-runs/start', { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 查询完整/效果测试运行进度 */
  async getSmartTestingFullRun(runId: string): Promise<ResponseModel<{ full_run_id: string; status: string; function_cases?: Array<{ case_id: string; run_id?: string; thread_id?: string; status: string }>; effect_cases?: Array<{ effect_case_id: string; run_id?: string; thread_id?: string; status: string }> }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/full-runs/${encodeURIComponent(runId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 对效果测试中指定 effect case 强制重新校验一次（与功能测试一致） */
  async reverifySmartTestingFullRunEffectCase(fullRunId: string, effectCaseId: string): Promise<ResponseModel<{ case: Record<string, unknown>; message: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/full-runs/${encodeURIComponent(fullRunId)}/effect-cases/${encodeURIComponent(effectCaseId)}/reverify`, { method: 'POST' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 列出完整/效果测试运行记录（历史） */
  async listSmartTestingFullRuns(limit?: number): Promise<ResponseModel<Array<{ full_run_id: string; status: string; created_at?: string }>>> {
    const q = limit != null ? `?limit=${limit}` : ''
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/full-runs${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 全面测试 case 列表 */
  async getSmartTestingComprehensiveCases(): Promise<ResponseModel<Array<{ case_id: string; type: string; params: Record<string, unknown>; index: number }>>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/comprehensive-cases', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 启动全面测试运行 */
  async startSmartTestingComprehensiveRun(params: { case_ids?: string[] }): Promise<ResponseModel<{ comprehensive_run_id: string; message: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/comprehensive-runs/start', { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 查询全面测试运行进度 */
  async getSmartTestingComprehensiveRun(runId: string): Promise<ResponseModel<{ comprehensive_run_id: string; status: string; cases: Array<{ case_id: string; run_id?: string; status: string; error?: string }> }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/comprehensive-runs/${encodeURIComponent(runId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 对指定 case 强制重新校验一次（下载真实尺寸比对） */
  async reverifySmartTestingComprehensiveCase(comprehensiveRunId: string, caseId: string): Promise<ResponseModel<{ case: Record<string, unknown>; message: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/comprehensive-runs/${encodeURIComponent(comprehensiveRunId)}/cases/${encodeURIComponent(caseId)}/reverify`, { method: 'POST' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 对指定 case 跑 3 个 regenerate（角色/关键帧/视频）并校验，结果写入 manifest */
  async runRegenerateTestForCase(comprehensiveRunId: string, caseId: string): Promise<ResponseModel<{ case: Record<string, unknown>; message: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/comprehensive-runs/${encodeURIComponent(comprehensiveRunId)}/cases/${encodeURIComponent(caseId)}/regenerate-test`, { method: 'POST' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 对选中的多个 case 依次跑 regenerate 测试 */
  async runRegenerateTestBatch(comprehensiveRunId: string, caseIds: string[]): Promise<ResponseModel<{ updated: number; results: Array<{ case_id: string; ok?: boolean; skipped?: boolean; reason?: string; error?: string }>; message: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/comprehensive-runs/${encodeURIComponent(comprehensiveRunId)}/regenerate-test-batch`, { method: 'POST', body: JSON.stringify({ case_ids: caseIds }) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 列出全面测试运行 */
  async listSmartTestingComprehensiveRuns(limit?: number): Promise<ResponseModel<Array<{ comprehensive_run_id: string; status: string; created_at?: string }>>> {
    const q = limit != null ? `?limit=${limit}` : ''
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/comprehensive-runs${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：列出一致性测试集（image | video） */
  async listConsistencyDatasets(datasetType: 'image' | 'video', limit?: number): Promise<ResponseModel<Array<{ dataset_id: string; name: string; dataset_type: string; created_at?: string; updated_at?: string; items?: unknown[] }>>> {
    const q = limit != null ? `?limit=${limit}` : ''
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/${datasetType}${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：创建测试集 */
  async createConsistencyDataset(params: { name: string; dataset_type: 'image' | 'video' }): Promise<ResponseModel<{ dataset_id: string; name: string; dataset_type: string; created_at: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/consistency-datasets', { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：获取测试集详情 */
  async getConsistencyDataset(datasetType: 'image' | 'video', datasetId: string): Promise<ResponseModel<{ dataset_id: string; name: string; items: unknown[]; created_at?: string; updated_at?: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/${datasetType}/${encodeURIComponent(datasetId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：更新测试集名称 */
  async updateConsistencyDataset(datasetType: 'image' | 'video', datasetId: string, params: { name: string }): Promise<ResponseModel<unknown>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/${datasetType}/${encodeURIComponent(datasetId)}`, { method: 'PATCH', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：删除测试集 */
  async deleteConsistencyDataset(datasetType: 'image' | 'video', datasetId: string): Promise<ResponseModel<unknown>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/${datasetType}/${encodeURIComponent(datasetId)}`, { method: 'DELETE' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：删除测试集中一条条目 */
  async deleteConsistencyDatasetItem(datasetType: 'image' | 'video', datasetId: string, itemId: string): Promise<ResponseModel<unknown>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/${datasetType}/${encodeURIComponent(datasetId)}/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：更新测试集中一条条目（可编辑字段） */
  async updateConsistencyDatasetItem(
    datasetType: 'image' | 'video',
    datasetId: string,
    itemId: string,
    payload: Record<string, unknown>,
  ): Promise<ResponseModel<{ item: unknown }>> {
    return makeRequest<LegacyApiValue>(
      `/admin/smart-testing/consistency-datasets/${datasetType}/${encodeURIComponent(datasetId)}/items/${encodeURIComponent(itemId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
      CUTI_VIDEO_API_BASE_URL,
    )
  },
  /** 智能测试 - 一致性测试：获取条目编辑用选项（image_generation_tool、video_generation_tool、generation_mode、aspect_ratio、resolution） */
  async getConsistencyOptions(): Promise<
    ResponseModel<{
      image_generation_tool: Array<{ value: string; label: string }>
      video_generation_tool: Array<{ value: string; label: string }>
      generation_mode: Array<{ value: string; label: string }>
      aspect_ratio: Array<{ value: string; label: string }>
      resolution: Array<{ value: string; label: string }>
    }>
  > {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/consistency-options', { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：添加图片一致性 item */
  async addConsistencyImageItem(datasetId: string, params: { keyframe_version_uuid: string; run_id?: string; thread_id?: string; shot_number: number; prompt_source?: string }): Promise<ResponseModel<{ dataset_id: string; item: unknown }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/image/${encodeURIComponent(datasetId)}/items`, { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：添加视频一致性 item */
  async addConsistencyVideoItem(datasetId: string, params: { video_generation_version_uuid: string; run_id?: string; thread_id?: string; shot_number: number; prompt_source?: string }): Promise<ResponseModel<{ dataset_id: string; item: unknown }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/video/${encodeURIComponent(datasetId)}/items`, { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：按 thread_id 拉取候选（图/视频版本列表），供多选批量加入 dataset */
  async getConsistencyCandidatesFromThread(threadId: string): Promise<
    ResponseModel<{
      thread_id: string
      conversation_id: string
      image_candidates: Array<{
        keyframe_version_uuid: string
        shot_number: number
        run_id: string
        thread_id: string
        reference_image_urls?: string[]
        prompt_used?: string
        keyframe_url?: string
        image_generation_tool?: string
        aspect_ratio?: string
        resolution?: string
      }>
      video_candidates: Array<{
        video_generation_version_uuid: string
        shot_number: number
        run_id: string
        thread_id: string
        start_image_url?: string
        i2v_prompt?: string
        duration?: number
        video_url?: string
        video_generation_tool?: string
        generation_mode?: string
        aspect_ratio?: string
        resolution?: string
      }>
    }>
  > {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-datasets/from-thread?thread_id=${encodeURIComponent(threadId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：将 from-thread 多选的候选批量加入对应 dataset */
  async batchAddConsistencyFromThread(params: {
    image_dataset_id?: string
    video_dataset_id?: string
    image_selections: Array<{ keyframe_version_uuid: string; run_id?: string; thread_id?: string; shot_number: number; prompt_source?: string }>
    video_selections: Array<{ video_generation_version_uuid: string; run_id?: string; thread_id?: string; shot_number: number; prompt_source?: string }>
  }): Promise<ResponseModel<{ added_image_count: number; added_video_count: number; image_items: unknown[]; video_items: unknown[] }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/consistency-datasets/batch-add-from-thread', { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：启动运行 */
  async startConsistencyRun(params: { dataset_ids: Array<{ type: string; dataset_id: string }>; concurrency?: number }): Promise<ResponseModel<{ consistency_run_id: string; message: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/consistency-runs/start', { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：列出运行 */
  async listConsistencyRuns(limit?: number): Promise<ResponseModel<Array<{ consistency_run_id: string; status: string; created_at?: string; cases?: unknown[] }>>> {
    const q = limit != null ? `?limit=${limit}` : ''
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-runs${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 一致性测试：获取运行详情 */
  async getConsistencyRun(runId: string): Promise<ResponseModel<{ run_id: string; status: string; created_at?: string; cases?: Array<{ item_id: string; type: string; passed?: boolean; reason?: string }> }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/consistency-runs/${encodeURIComponent(runId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 管理员通用 - 按 run_id 获取该次任务用户可见内容（用户输入、图/音/视、生成的故事/音乐/图片/视频），与智能测试无关 */
  async getRunContent(runId: string): Promise<ResponseModel<{ agent_type?: string; user_input?: string; user_input_files?: unknown; story_content?: string | null; music_content?: string | null; image_content?: string | null; story_title?: string; story_description?: string; story_themes?: string[]; music_urls?: string[]; image_urls?: string[]; video_url?: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/run-content/${encodeURIComponent(runId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 上传媒体（图/音/视）到 S3，返回 URL。本地 demo 上传后用此 URL 即可。 */
  async uploadSmartTestingMedia(file: File): Promise<ResponseModel<{ url: string }>> {
    const formData = new FormData()
    formData.append('file', file)
    const currentLang = localStorage.getItem('language') || 'en'
    const response = await fetch(`${CUTI_VIDEO_API_BASE_URL}/admin/smart-testing/upload-media`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: { 'X-App-Language': currentLang },
    })
    const result: ResponseModel<{ url: string }> = await response.json()
    if (result.code !== 0) throw new Error(result.message || '上传失败')
    return result
  },
  /** 智能测试 - 全自动运行（仅 API，不暴露前端） */
  async startSmartTestingFullAutoRun(params: { user_input: string; thread_id: string; user_option?: Record<string, unknown>; agent_type?: string; user_input_files?: Record<string, unknown> }): Promise<ResponseModel<{ run_id: string; thread_id: string; message: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/full-auto-run', { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 基准数据集：按 thread 维度列出已完成 video 任务（仅非 admin，分页；每 thread 一行，代表 run 为该 thread 下完成时间最新） */
  async listSmartTestingBenchmarkThreads(params?: { page?: number; page_size?: number }): Promise<ResponseModel<{ items: Array<{ run_id: string; thread_id: string; user_id: string; conversation_id?: number; status: string; user_input?: string; user_option?: Record<string, unknown>; user_input_files?: { images?: unknown[]; audio_files?: unknown[]; video_files?: unknown[] }; created_at?: string; completed_at?: string; agent_type?: string; run_type?: string }>; total: number; page: number; page_size: number }>> {
    const sp = new URLSearchParams()
    if (params?.page != null) sp.set('page', String(params.page))
    if (params?.page_size != null) sp.set('page_size', String(params.page_size))
    const q = sp.toString() ? `?${sp.toString()}` : ''
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/threads${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 基准数据集：获取 run 详情 */
  async getSmartTestingBenchmarkRunDetail(runId: string): Promise<ResponseModel<{ run_id: string; run: Record<string, unknown>; task: Record<string, unknown>; character_versions_by_character: unknown[]; keyframe_versions: unknown[]; video_versions: unknown[] }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/runs/${encodeURIComponent(runId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 基准数据集：创建（按 thread 维度） */
  async createSmartTestingBenchmarkDataset(params: { name: string; thread_ids: string[] }): Promise<ResponseModel<{ dataset_id: string; name: string; thread_ids: string[]; created_at: string }>> {
    return makeRequest<LegacyApiValue>('/admin/smart-testing/benchmark-dataset', { method: 'POST', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 基准数据集：列表 */
  async listSmartTestingBenchmarkDatasets(limit?: number): Promise<ResponseModel<Array<{ dataset_id: string; name: string; thread_ids?: string[]; run_ids?: string[]; created_at?: string }>>> {
    const q = limit != null ? `?limit=${limit}` : ''
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset${q}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 效果测试数据集：详情 */
  async getSmartTestingBenchmarkDataset(datasetId: string): Promise<ResponseModel<{ dataset_id: string; name: string; thread_ids?: string[]; run_ids?: string[]; created_at?: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/${encodeURIComponent(datasetId)}`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 效果测试数据集：更新名称 */
  async updateSmartTestingBenchmarkDataset(datasetId: string, params: { name: string }): Promise<ResponseModel<{ dataset_id: string; name: string; thread_ids?: string[]; created_at?: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/${encodeURIComponent(datasetId)}`, { method: 'PATCH', body: JSON.stringify(params) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 效果测试数据集：删除 */
  async deleteSmartTestingBenchmarkDataset(datasetId: string): Promise<ResponseModel<{ dataset_id: string; deleted: boolean }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/${encodeURIComponent(datasetId)}`, { method: 'DELETE' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 效果测试数据集：获取内部 thread 列表详情（展示数据集内容） */
  async getSmartTestingBenchmarkDatasetThreads(datasetId: string): Promise<ResponseModel<{ items: Array<{ run_id: string; thread_id: string; user_id: string; user_input?: string; user_option?: Record<string, unknown>; user_input_files?: { images?: { url?: string }[]; audio_files?: { url?: string }[]; video_files?: { url?: string }[] }; created_at?: string }>; dataset_id: string; name: string }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/${encodeURIComponent(datasetId)}/threads`, { method: 'GET' }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 效果测试数据集：手动添加一条（不必来自 run） */
  async addSmartTestingBenchmarkDatasetItem(datasetId: string, body: { user_input: string; user_option?: Record<string, unknown>; user_input_files?: { images?: { url?: string }[]; audio_files?: { url?: string }[]; video_files?: { url?: string }[] } }): Promise<ResponseModel<{ dataset_id: string; item: { thread_id: string; run_id: string; user_id: string; user_input: string; user_option: Record<string, unknown>; user_input_files: Record<string, unknown>; created_at: string } }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/${encodeURIComponent(datasetId)}/items`, { method: 'POST', body: JSON.stringify(body) }, CUTI_VIDEO_API_BASE_URL)
  },
  /** 智能测试 - 效果测试数据集：更新一条的 user_option（仅手动添加的条目可改） */
  async updateSmartTestingBenchmarkDatasetItem(datasetId: string, body: { thread_id: string; user_option: Record<string, unknown> }): Promise<ResponseModel<{ dataset_id: string; thread_id: string; user_option: Record<string, unknown> }>> {
    return makeRequest<LegacyApiValue>(`/admin/smart-testing/benchmark-dataset/${encodeURIComponent(datasetId)}/items`, { method: 'PATCH', body: JSON.stringify(body) }, CUTI_VIDEO_API_BASE_URL)
  },

  /** 错误追踪 - 管理员代理「合并视频」，与用户端 video-assembly 一致：先 sync 再合成最终视频 */
  async adminVideoAssembly(request: {
    run_id: string
    segment_versions?: Array<{ segment_uuid: string; segment_version_uuid: string }>
    videos?: Array<{ uuid: string; selected_version?: { uuid: string } }>
    user_option?: LegacyApiValue
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/admin/error-tracking/video-assembly', {
      method: 'POST',
      body: JSON.stringify(request),
    }, CUTI_VIDEO_API_BASE_URL)
  },

}

/** 与后端 KeyframeRegenerateStrategy 一致 */
export type KeyframeRegenerateStrategy =
  | 'prompt_regenerate'
  | 'instruction_regenerate'
  | 'instruction_merge_prompt'
  | 'instruction_edit_image'

/** 与后端 CharacterRegenerateStrategy 一致（无 instruction_regenerate） */
export type CharacterRegenerateStrategy =
  | 'prompt_regenerate'
  | 'instruction_merge_prompt'
  | 'instruction_edit_image'

/** 与后端 VideoRegenerateStrategy 一致 */
export type VideoRegenerateStrategy = 'prompt_regenerate' | 'instruction_merge_prompt'

/**
 * 视频编辑相关 API
 */
export const videoEditingApi = {
  /**
   * 重新生成关键帧
   */
  async regenerateKeyframes(request: {
    keyframes: Array<{
      uuid?: string
      shot_number?: number
      frame_index?: number
      versions: Array<{
        uuid?: string
        custom_prompt?: string
        /** 可选：与 custom_prompt 分开展示给 LLM 的用户自然语言修改说明 */
        instruction?: string
        /** 见 KeyframeRegenerateStrategy；省略时后端默认 prompt_regenerate */
        regenerate_strategy?: KeyframeRegenerateStrategy
        use_reflection?: boolean  // AI improve 功能
      }>
    }>
    user_option?: LegacyApiValue
    thread_id?: string
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/agent-router/video-editing/regenerate-keyframes', {
      method: 'POST',
      body: JSON.stringify(request),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 重新生成视频
   */
  async regenerateVideos(request: {
    videos: Array<{
      uuid?: string
      shot_number?: number
      versions: Array<{
        uuid?: string
        custom_prompt?: string
        instruction?: string
        /** 见 VideoRegenerateStrategy */
        regenerate_strategy?: VideoRegenerateStrategy
      }>
    }>
    user_option?: LegacyApiValue
    thread_id?: string
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/agent-router/video-editing/regenerate-videos', {
      method: 'POST',
      body: JSON.stringify(request),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 重新生成角色
   */
  async regenerateCharacters(request: {
    characters: Array<{
      uuid: string
      versions: Array<{
        uuid: string
        custom_prompt?: string
        instruction?: string
        regenerate_strategy?: CharacterRegenerateStrategy
      }>
    }>
    user_option?: LegacyApiValue
    thread_id?: string
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/agent-router/video-editing/regenerate-characters', {
      method: 'POST',
      body: JSON.stringify(request),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * Regenerate 完成后的下一步（继续编辑 / 同步下游），见 interaction_post_regenerate 消息
   */
  async postRegenerateAction(request: {
    conversation_id: number
    message_id: number
    action: 'continue_edit' | 'sync_downstream'
  }): Promise<ResponseModel<{ ok?: boolean; action?: string; new_run_id?: string | null; message?: string }>> {
    return makeRequest('/agent-router/video-editing/post-regenerate-action', {
      method: 'POST',
      body: JSON.stringify(request),
    }, CUTI_VIDEO_API_BASE_URL)
  },

  /**
   * 融合编辑弹窗：按当前 prompt 生成 AI 建议指令芯片
   */
  async suggestPromptEditPresets(
    request: {
      artifact_kind: 'character' | 'keyframe' | 'video'
      prompt: string
      image_url?: string | null
      /** 镜头 video artifact：当前版本视频 URL，供后端多模态 */
      video_url?: string | null
      thread_id?: string | null
    },
    signal?: AbortSignal,
  ): Promise<ResponseModel<{ presets: Array<{ label: string; emoji: string; instruction: string }> }>> {
    return makeRequest<{ presets: Array<{ label: string; emoji: string; instruction: string }> }>(
      '/agent-router/video-editing/suggest-prompt-edit-presets',
      {
        method: 'POST',
        body: JSON.stringify({
          artifact_kind: request.artifact_kind,
          prompt: request.prompt,
          image_url: request.image_url ?? undefined,
          video_url: request.video_url ?? undefined,
          thread_id: request.thread_id ?? undefined,
        }),
        signal,
      },
      CUTI_VIDEO_API_BASE_URL,
    )
  },

  /**
   * 重新生成角色多视角图
   */
  /**
   * 选中角色版本
   */
  async selectCharacterVersion(characterUuid: string, versionUuid: string): Promise<ResponseModel<{ character_uuid: string; selected_version_id: string }>> {
    return makeRequest<{ character_uuid: string; selected_version_id: string }>(
      `/agent-router/characters/${encodeURIComponent(characterUuid)}/select-version`,
      {
        method: 'POST',
        body: JSON.stringify({ version_uuid: versionUuid }),
      },
      CUTI_VIDEO_API_BASE_URL,
    )
  },

  /**
   * 选中关键帧版本（持久化 current_version_index，刷新后保留）
   */
  async selectKeyframeVersion(keyframeUuid: string, versionUuid: string): Promise<ResponseModel<{ keyframe_uuid: string; current_version_index: number; version_uuid: string }>> {
    return makeRequest<{ keyframe_uuid: string; current_version_index: number; version_uuid: string }>(
      `/agent-router/keyframes/${encodeURIComponent(keyframeUuid)}/select-version`,
      { method: 'POST', body: JSON.stringify({ version_uuid: versionUuid }) },
      CUTI_VIDEO_API_BASE_URL,
    )
  },

  /**
   * 选中视频片段版本（持久化 current_version_index，刷新后保留）
   */
  async selectVideoVersion(videoUuid: string, versionUuid: string): Promise<ResponseModel<{ video_uuid: string; current_version_index: number; version_uuid: string }>> {
    return makeRequest<{ video_uuid: string; current_version_index: number; version_uuid: string }>(
      `/agent-router/videos/${encodeURIComponent(videoUuid)}/select-version`,
      { method: 'POST', body: JSON.stringify({ version_uuid: versionUuid }) },
      CUTI_VIDEO_API_BASE_URL,
    )
  },

  /**
   * 选中多视角图版本
   */
  async selectMultiViewVersion(characterUuid: string, versionUuid: string, multiViewVersionUuid: string): Promise<ResponseModel<{ character_uuid: string; version_uuid: string; selected_multi_view_version_id: string }>> {
    return makeRequest<{ character_uuid: string; version_uuid: string; selected_multi_view_version_id: string }>(
      `/agent-router/characters/${encodeURIComponent(characterUuid)}/versions/${encodeURIComponent(versionUuid)}/select-multi-view`,
      {
        method: 'POST',
        body: JSON.stringify({ multi_view_version_uuid: multiViewVersionUuid }),
      },
      CUTI_VIDEO_API_BASE_URL,
    )
  },

  async regenerateCharacterMultiView(request: {
    characters: Array<{
      uuid: string  // 角色 UUID
      versions: Array<{
        uuid: string  // 角色版本 UUID（用于关联）
        custom_prompt?: string
      }>
    }>
    user_option?: LegacyApiValue
    thread_id?: string
  }): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>('/agent-router/video-editing/regenerate-character-multi-view', {
      method: 'POST',
      body: JSON.stringify(request),
    }, CUTI_VIDEO_API_BASE_URL)
  },
}

export const videoHistoryApi = {
  async getVideos(params?: {
    limit?: number
    offset?: number
    success_only?: boolean
    content_type?: 'all' | 'video' | 'audio' | 'character' | 'image'
  }): Promise<ResponseModel<{ total: number; videos: LegacyApiValue[] }>> {
    const queryParams = new URLSearchParams()
    if (params?.limit) queryParams.append('limit', params.limit.toString())
    if (params?.offset) queryParams.append('offset', params.offset.toString())
    if (params?.success_only) queryParams.append('success_only', 'true')
    if (params?.content_type && params.content_type !== 'all') {
      queryParams.append('content_type', params.content_type)
    }

    const queryString = queryParams.toString()
    const endpoint = queryString ? `/vault/videos?${queryString}` : '/vault/videos'

    return makeRequest<{ total: number; videos: LegacyApiValue[] }>(endpoint, {
      method: 'GET',
    }, API_BASE_URL)
  },

  async getVideoByUuid(videoUuid: string): Promise<ResponseModel<LegacyApiValue>> {
    return makeRequest<LegacyApiValue>(`/vault/videos/${videoUuid}`, {
      method: 'GET',
    }, API_BASE_URL)
  },
}

/**
 * Audio History API
 */
export const audioHistoryApi = {
  async getAudios(params?: {
    limit?: number
    offset?: number
  }): Promise<ResponseModel<{ total: number; audios: AudioHistory[] }>> {
    const queryParams = new URLSearchParams()
    if (params?.limit) queryParams.append('limit', params.limit.toString())
    if (params?.offset) queryParams.append('offset', params.offset.toString())

    const queryString = queryParams.toString()
    const endpoint = queryString ? `/vault/audios?${queryString}` : '/vault/audios'

    return makeRequest<{ total: number; audios: AudioHistory[] }>(endpoint, {
      method: 'GET',
    }, API_BASE_URL)
  },
}

/**
 * Character History API
 */
export const characterHistoryApi = {
  async getCharacters(params?: {
    limit?: number
    offset?: number
  }): Promise<ResponseModel<{ total: number; characters: CharacterHistory[] }>> {
    const queryParams = new URLSearchParams()
    if (params?.limit) queryParams.append('limit', params.limit.toString())
    if (params?.offset) queryParams.append('offset', params.offset.toString())

    const queryString = queryParams.toString()
    const endpoint = queryString ? `/vault/characters?${queryString}` : '/vault/characters'

    return makeRequest<{ total: number; characters: CharacterHistory[] }>(endpoint, {
      method: 'GET',
    }, API_BASE_URL)
  },

}

async function convertFileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.readAsDataURL(file)
    reader.onload = () => {
      const result = reader.result as string
      const base64 = result.split(',')[1]
      resolve(base64)
    }
    reader.onerror = error => reject(error)
  })
}

export const characterUploadApi = {
  async createCharacter(data: {
    characterName: string
    images: File[]
    parentFolderId?: number | null
    additionalData?: Record<string, LegacyApiValue>
  }): Promise<ResponseModel<CharacterUploadResponse>> {
    const imagePromises = data.images.map(async file => ({
      image_data: await convertFileToBase64(file),
      filename: file.name,
    }))

    const images = await Promise.all(imagePromises)

    const requestBody: CharacterUploadRequest = {
      character_name: data.characterName,
      images: images,
      parent_folder_id: data.parentFolderId ?? null,
      additional_data: data.additionalData ? JSON.stringify(data.additionalData) : '{}',
    }

    return makeRequest<CharacterUploadResponse>('/characters/v2/create', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    }, API_BASE_URL)
  },
}

/**
 * Combined Media History API (Videos + Audios + Characters)
 */
export const mediaHistoryApi = {
  async getMedia(params?: {
    video_limit?: number
    video_offset?: number
    audio_limit?: number
    audio_offset?: number
    character_limit?: number
    character_offset?: number
    success_only?: boolean
  }): Promise<ResponseModel<{ total_videos: number; total_audios: number; total_characters: number; videos: VideoHistory[]; audios: AudioHistory[]; characters: CharacterHistory[] }>> {
    const queryParams = new URLSearchParams()
    if (params?.video_limit) queryParams.append('video_limit', params.video_limit.toString())
    if (params?.video_offset) queryParams.append('video_offset', params.video_offset.toString())
    if (params?.audio_limit) queryParams.append('audio_limit', params.audio_limit.toString())
    if (params?.audio_offset) queryParams.append('audio_offset', params.audio_offset.toString())
    if (params?.character_limit) queryParams.append('character_limit', params.character_limit.toString())
    if (params?.character_offset) queryParams.append('character_offset', params.character_offset.toString())
    if (params?.success_only) queryParams.append('success_only', 'true')

    const queryString = queryParams.toString()
    const endpoint = queryString ? `/vault/media?${queryString}` : '/vault/media'

    return makeRequest<{ total_videos: number; total_audios: number; total_characters: number; videos: VideoHistory[]; audios: AudioHistory[]; characters: CharacterHistory[] }>(endpoint, {
      method: 'GET',
    }, API_BASE_URL)
  },

  async getMediaV1Paged(params?: {
    page?: number
    limit?: number
    success_only?: boolean
  }): Promise<MediaHistoryV1PagedResponse> {
    const queryParams = new URLSearchParams()
    if (params?.page) queryParams.append('page', params.page.toString())
    if (params?.limit) queryParams.append('limit', params.limit.toString())
    if (params?.success_only) queryParams.append('success_only', 'true')

    const queryString = queryParams.toString()
    const endpoint = queryString ? `/vault/media?${queryString}` : '/vault/media'

    return makeRequest<LegacyApiValue>(endpoint, {
      method: 'GET',
    }, API_BASE_URL) as Promise<MediaHistoryV1PagedResponse>
  },
}

export const folderApi = {
  async updateFolder(folderId: number, data: {
    name?: string
    display_order?: number
  }): Promise<ResponseModel<Folder>> {
    return makeRequest<Folder>(`/folders/${folderId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }, API_BASE_URL)
  },

  async deleteFolder(folderId: number): Promise<ResponseModel<{ deleted: boolean }>> {
    const endpoint = `/folders/${folderId}`
    return makeRequest<{ deleted: boolean }>(endpoint, {
      method: 'DELETE',
    }, API_BASE_URL)
  },
}

export const subscriptionApi = {
  async getPlans(): Promise<ResponseModel<SubscriptionPlan[]>> {
    const url = `${API_BASE_URL}/subscription/plans`
    const currentLang = localStorage.getItem('language') || 'en'

    try {
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-App-Language': currentLang,
        },
      })

      const data = await response.json()

      if (data && typeof data === 'object' && 'code' in data) {
        return data as ResponseModel<SubscriptionPlan[]>
      }

      if (Array.isArray(data)) {
        return {
          code: 0,
          message: 'success',
          data: data as SubscriptionPlan[],
        }
      }

      throw new Error('Invalid response format')
    } catch (error) {
      console.error('Failed to fetch subscription plans:', error)
      throw error
    }
  },

  async getMySubscription(): Promise<ResponseModel<UserSubscription>> {
    const url = `${API_BASE_URL}/subscription/me`
    const currentLang = localStorage.getItem('language') || 'en'

    try {
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-App-Language': currentLang,
        },
      })

      const result = await response.json()

      if (result && typeof result === 'object' && 'code' in result) {
        return result as ResponseModel<UserSubscription>
      }

      if (result && 'plan_id' in result) {
        return {
          code: 0,
          message: 'success',
          data: result as UserSubscription,
        }
      }

      throw new Error('Invalid response format')
    } catch (error) {
      console.error('Failed to fetch subscription:', error)
      throw error
    }
  },

  async createSubscription(data: CreateSubscriptionRequest): Promise<ResponseModel<CreateSubscriptionResponse>> {
    const url = `${API_BASE_URL}/subscription/create`
    const currentLang = localStorage.getItem('language') || 'en'

    try {
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-App-Language': currentLang,
        },
        body: JSON.stringify(data),
      })

      const result = await response.json()

      if (result && typeof result === 'object' && 'code' in result) {
        return result as ResponseModel<CreateSubscriptionResponse>
      }

      if (result && 'checkout_url' in result) {
        return {
          code: 0,
          message: 'success',
          data: result as CreateSubscriptionResponse,
        }
      }

      throw new Error('Invalid response format')
    } catch (error) {
      console.error('Failed to create subscription:', error)
      throw error
    }
  },

  async cancelSubscription(): Promise<ResponseModel<CancelSubscriptionResponse>> {
    const url = `${API_BASE_URL}/subscription/cancel`
    const currentLang = localStorage.getItem('language') || 'en'

    try {
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-App-Language': currentLang,
        },
      })

      // Try to parse as JSON
      const data = await response.json()

      // Handle various response formats
      if (response.ok) {
        // If response is successful but doesn't have standard structure
        if (!data.code && data.message) {
          return {
            code: 0,
            message: data.message,
            data: data as CancelSubscriptionResponse,
          }
        }

        // Standard response format
        return data as ResponseModel<CancelSubscriptionResponse>
      }

      // Error response
      throw new Error(data.message || 'Failed to cancel subscription')
    } catch (error) {
      console.error('Failed to cancel subscription:', error)
      throw error
    }
  },

  async restoreSubscription(): Promise<ResponseModel<RestoreSubscriptionResponse>> {
    const url = `${API_BASE_URL}/subscription/restore`
    const currentLang = localStorage.getItem('language') || 'en'

    try {
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-App-Language': currentLang,
        },
      })

      const data = await response.json()

      if (response.ok) {
        if (!data.code && data.message) {
          return {
            code: 0,
            message: data.message,
            data: data as RestoreSubscriptionResponse,
          }
        }
        return data as ResponseModel<RestoreSubscriptionResponse>
      }

      throw new Error(data.message || 'Failed to restore subscription')
    } catch (error) {
      console.error('Failed to restore subscription:', error)
      throw error
    }
  },
}

/**
 * 统一导出
 */
const creditPackagesApi = {
  getPackages: async (): Promise<ResponseModel<LegacyApiValue[]>> => {
    return makeRequest<LegacyApiValue[]>('/payment/packages')
  },
}

const paymentApi = {
  createCheckoutSession: async (data: {
    package_id: string
    language: string
    success_url?: string
    cancel_url?: string
  }): Promise<ResponseModel<{ checkout_url: string; session_id: string }>> => {
    return makeRequest<{ checkout_url: string; session_id: string }>('/payment/create-checkout-session', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  getPaymentDetails: async (sessionId: string): Promise<ResponseModel<LegacyApiValue>> => {
    return makeRequest<LegacyApiValue>(`/payment/payment-details/${sessionId}`)
  },

  getTransactions: async (limit: number = 20, offset: number = 0): Promise<ResponseModel<LegacyApiValue[]>> => {
    return makeRequest<LegacyApiValue[]>(`/payment/transactions?limit=${limit}&offset=${offset}`)
  },
}

export const inviteCodeApi = {
  getMyInviteCodes: (): Promise<ResponseModel<{
    invite_codes: Array<{
      code: string
      status: 'new' | 'sent' | 'used'
      created_at: string
      used_by_id: string | null
      used_at: string | null
      sent_at: string | null
    }>
    summary: { total: number; new: number; sent: number; used: number }
  }>> => {
    return makeRequest('/users/invite-codes')
  },

  markSent: (code: string): Promise<ResponseModel<LegacyApiValue>> => {
    return makeRequest('/users/invite-codes/mark-sent', {
      method: 'POST',
      body: JSON.stringify({ code }),
    })
  },
}

export const api = {
  user: userApi,
  agent: agentApi,
  conversation: conversationApi,
  tools: toolsApi,
  admin: adminApi,
  videoEditing: videoEditingApi,
  creditPackages: creditPackagesApi,
  payment: paymentApi,
  characterUpload: characterUploadApi,
  folder: folderApi,
  subscription: subscriptionApi,
  inviteCode: inviteCodeApi,
  audio: audioApi,
}

export default api
