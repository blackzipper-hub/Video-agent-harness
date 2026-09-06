import { useEffect, useMemo, useRef, useState } from 'react'
import { ArtifactDocument } from './ArtifactDocument'
import {
  AlertTriangle, Check, CheckCircle2, Circle, FileText, Film, Image, Loader2, Music, RefreshCw, Scissors,
} from 'lucide-react'
import { toast } from 'sonner'
import { useLanguage } from '@/i18n/LanguageContext'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  videoRuntimeClient,
  type RuntimeWorkspace,
} from '@/features/video-runtime/client'
import type { RuntimeProductionProgress } from './AgentProductionProgress'
import {
  artifactTitle,
  capabilityLabel,
  interpolate,
  planStepLabel,
  resultCountLabel,
  runtimeMessage,
  statusLabel,
  type Translate,
} from './labels'
import type { DeepAgentArtifact, DeepAgentSnapshot, DeepAgentTask } from './types'

function formatTimestamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00.00'
  const whole = Math.floor(seconds)
  const mins = Math.floor(whole / 60)
  const secs = whole % 60
  const frac = Math.floor((seconds - whole) * 100)
  return `${mins}:${String(secs).padStart(2, '0')}.${String(frac).padStart(2, '0')}`
}

function VideoFramePicker({
  mediaUri,
  title,
  extracting,
  onExtract,
}: {
  mediaUri: string
  title?: string
  extracting: boolean
  onExtract: (timestamp: number) => void | Promise<void>
}) {
  const { t } = useLanguage()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  const capturePreview = () => {
    const video = videoRef.current
    if (!video || !video.videoWidth) return
    try {
      const canvas = document.createElement('canvas')
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight
      const context = canvas.getContext('2d')
      if (!context) return
      context.drawImage(video, 0, 0, canvas.width, canvas.height)
      setPreviewUrl(canvas.toDataURL('image/jpeg', 0.92))
    } catch {
      setPreviewUrl(null)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex max-h-[calc(100dvh-14rem)] w-full min-w-0 items-center justify-center overflow-hidden rounded-lg bg-black">
        <video
          ref={videoRef}
          key={mediaUri}
          src={mediaUri}
          controls
          playsInline
          preload="metadata"
          className="block h-auto max-h-[calc(100dvh-14rem)] max-w-full object-contain"
          onLoadedMetadata={(event) => {
            setDuration(event.currentTarget.duration || 0)
            setCurrentTime(event.currentTarget.currentTime || 0)
          }}
          onTimeUpdate={event => setCurrentTime(event.currentTarget.currentTime || 0)}
          onSeeked={capturePreview}
          onPause={capturePreview}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="font-mono text-[11px]">
          {formatTimestamp(currentTime)}
          {duration > 0 ? ` / ${formatTimestamp(duration)}` : ''}
        </Badge>
        <Button
          size="sm"
          variant="secondary"
          disabled={extracting || !mediaUri}
          onClick={() => {
            capturePreview()
            void onExtract(videoRef.current?.currentTime ?? currentTime)
          }}
        >
          {extracting ? (
            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Scissors className="mr-1 h-3.5 w-3.5" />
          )}
          {t('da.workspace.useThisFrame')}
        </Button>
        <span className="text-xs text-muted-foreground">
          {t('da.workspace.scrubHint')}
        </span>
      </div>
      {previewUrl && (
        <div className="flex items-start gap-3 rounded-lg border border-border/60 bg-muted/30 p-2">
          <img
            src={previewUrl}
            alt={title
              ? interpolate(t('da.workspace.framePreviewTitled'), { title })
              : t('da.workspace.framePreview')}
            className="h-20 w-auto rounded object-contain"
          />
          <p className="pt-1 text-xs text-muted-foreground">
            {t('da.workspace.framePreviewHint')}
          </p>
        </div>
      )}
    </div>
  )
}

const isMediaUrl = (value: string | null | undefined, type: 'image' | 'audio' | 'video') => {
  if (!value) return false
  const patterns = {
    image: /\.(png|jpe?g|webp|gif|bmp|svg)(\?|#|$)/i,
    audio: /\.(mp3|wav|m4a|ogg|aac|flac)(\?|#|$)/i,
    video: /\.(mp4|webm|mov|m3u8|mkv)(\?|#|$)/i,
  }
  if (patterns[type].test(value)) return true
  if (value.startsWith('blob:')) return true
  // Path heuristics for local storage URLs without clear extensions.
  if (type === 'video' && /\/(videos?|media\/.*concat_|files\/videos)\//i.test(value)) return true
  if (type === 'image' && /\/(images?|files\/media\/.*\.(webp|png|jpe?g))/i.test(value)) return true
  return false
}

const artifactMediaUri = (artifact: DeepAgentArtifact): string | undefined => {
  if (artifact.uri) return artifact.uri
  const meta = artifact.metadata || {}
  // Prefer typed media fields before generic url/uri.
  const preferredKeys = artifact.type.includes('video')
    ? ['video_url', 'result_url', 'uri', 'url']
    : artifact.type === 'image' || artifact.type === 'keyframe' || artifact.type === 'character'
      ? ['image_url', 'character_image_url', 'uri', 'url']
      : artifact.type === 'music'
        ? ['audio_url', 'uri', 'url']
        : ['video_url', 'image_url', 'audio_url', 'result_url', 'url', 'uri']
  for (const key of preferredKeys) {
    const value = meta[key]
    if (typeof value === 'string' && value) return value
  }
  for (const key of ['videos', 'images', 'characters', 'audio']) {
    const items = meta[key]
    if (!Array.isArray(items)) continue
    for (const item of items) {
      if (!item || typeof item !== 'object') continue
      for (const mediaKey of ['video_url', 'image_url', 'character_image_url', 'audio_url', 'url', 'uri']) {
        const value = (item as Record<string, unknown>)[mediaKey]
        if (typeof value === 'string' && value) return value
      }
    }
  }
  return undefined
}

const isStoryArtifact = (artifact: DeepAgentArtifact) =>
  ['story', 'story_outline', 'outline', 'script'].includes(artifact.type)
  || /story|script|outline|剧本|脚本|梗概/i.test(artifact.title || '')

const isImageArtifact = (artifact: DeepAgentArtifact) => {
  if (artifact.type.includes('video')) return false
  const uri = artifactMediaUri(artifact)
  // Character artifacts are image cards only when a preview URL exists.
  if (artifact.type === 'character') return Boolean(uri)
  if (['image', 'keyframe', 'poster'].includes(artifact.type)) return true
  return Boolean(uri && isMediaUrl(uri, 'image') && !isMediaUrl(uri, 'video'))
}

const isVideoArtifact = (artifact: DeepAgentArtifact) => {
  if (artifact.type === 'video_spec') return false
  if (artifact.type.includes('video') || artifact.type === 'video_segment') return true
  const uri = artifactMediaUri(artifact)
  return Boolean(uri && isMediaUrl(uri, 'video'))
}

const containsInternalModelPayload = (artifact: DeepAgentArtifact): boolean => {
  const values = [artifact.summary, ...Object.values(artifact.metadata || {})]
    .filter((value): value is string => typeof value === 'string')
  return values.some(value =>
    value.includes('encrypted_content')
    || /"type"\s*:\s*"(?:reasoning|thinking)"/.test(value)
    || /'type'\s*:\s*'(?:reasoning|thinking)'/.test(value)
    || /gAAAA[A-Za-z0-9_]{20,}/.test(value),
  )
}

const isInternalExecutionArtifact = (artifact: DeepAgentArtifact): boolean => {
  const type = (artifact.type || '').trim().toLowerCase().replace(/[.\s-]+/g, '_')
  if ([
    'action_suggestions',
    'reasoning',
    'thinking',
    'trace',
    'event_log',
    'operation_log',
    'task_log',
    'model_request',
    'model_response',
    'llm_request',
    'llm_response',
    'tool_input',
    'tool_output',
    'tool_request',
    'tool_response',
  ].includes(type)) return true

  const title = (artifact.title || '').trim()
  return /^(?:model|llm|tool)\s+(?:request|response|input|output)$|^(?:execution\s+)?trace$/i.test(title)
}

const normalizeArtifactType = (type: string): string =>
  (type || '').trim().toLowerCase().replace(/[.\s-]+/g, '_')

/** Text-generation results are user documents, not generic execution records. */
const isReadableTextArtifact = (artifact: DeepAgentArtifact): boolean => {
  const type = normalizeArtifactType(artifact.type)
  return [
    'text',
    'document',
    'markdown',
    'creative_brief',
    'product_brief',
    'shot_plan',
    'storyboard_plan',
    'video_spec',
    'characters',
    'character_definition',
    'scene',
    'scenes',
    'shot',
    'storyboard',
    'timeline',
    'subtitle',
    'subtitles',
    'validation',
    'validation_result',
    'report',
  ].includes(type)
}

const isAudioArtifact = (artifact: DeepAgentArtifact): boolean => [
  'audio',
  'bgm',
  'music',
  'narration',
  'sound_effect',
  'speech',
  'tts',
  'voiceover',
].includes(normalizeArtifactType(artifact.type))

const firstMarkdownHeading = (body: string): string | undefined => {
  const match = body.match(/^\s{0,3}#{1,3}\s+(.+?)\s*#*\s*$/m)
  return match?.[1]?.replace(/[*_`]/g, '').trim() || undefined
}

const readableTextTitle = (artifact: DeepAgentArtifact, body: string, t: Translate): string => {
  const metadataTitle = artifact.metadata && typeof artifact.metadata.title === 'string'
    ? artifact.metadata.title.trim()
    : ''
  const rawTitle = (artifact.title || '').trim()
  const titleIsGeneric = !rawTitle
    || /^(?:text|document|markdown|output|result|generated text)$/i.test(rawTitle)
  return metadataTitle
    || (!titleIsGeneric ? artifactTitle(rawTitle, artifact.type, t) : '')
    || firstMarkdownHeading(body)
    || t('da.workspace.generatedDocument')
}

const promptFromRecord = (value: unknown): string | undefined => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const row = value as Record<string, unknown>
  for (const key of ['generated_prompt', 'generation_prompt', 'final_prompt']) {
    const prompt = row[key]
    if (typeof prompt === 'string' && prompt.trim()) return prompt.trim()
  }
  return undefined
}

/**
 * Return only an explicitly persisted final generation prompt. Do not render
 * request bodies, tool arguments, provider responses, or the rest of `raw`.
 */
const artifactGenerationPrompt = (artifact: DeepAgentArtifact): string | undefined => {
  const metadata = artifact.metadata || {}
  return promptFromRecord(metadata) || promptFromRecord(metadata.raw)
}

function GenerationPrompt({ artifact }: { artifact: DeepAgentArtifact }) {
  const { t } = useLanguage()
  const prompt = artifactGenerationPrompt(artifact)
  if (!prompt) return null
  return (
    <div className="min-w-0 rounded-lg border border-border/60 bg-muted/30 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-foreground">{t('da.workspace.generationPrompt')}</p>
        <Badge variant="outline" className="shrink-0 text-[10px]">{t('da.workspace.generationResult')}</Badge>
      </div>
      <p className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
        {prompt}
      </p>
    </div>
  )
}

const storyBody = (artifact: DeepAgentArtifact): string => {
  const meta = artifact.metadata || {}
  for (const key of ['script', 'content', 'outline', 'story', 'text', 'body', 'markdown', 'description']) {
    const value = meta[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (value && typeof value === 'object') return JSON.stringify(value)
  }
  if (Array.isArray(meta.chapters) && meta.chapters.length > 0) {
    return JSON.stringify({ ...(meta.title ? { title: meta.title } : {}), chapters: meta.chapters })
  }
  if (typeof meta.chapters === 'object' && meta.chapters && !Array.isArray(meta.chapters)) {
    try {
      return '```json\n' + JSON.stringify(meta.chapters, null, 2) + '\n```'
    } catch {
      /* ignore */
    }
  }
  for (const key of ['content', 'timeline', 'result', 'data', 'payload']) {
    const value = meta[key]
    if (value === undefined || value === null || value === '') continue
    try {
      return '```json\n' + JSON.stringify(value, null, 2) + '\n```'
    } catch {
      /* ignore values that cannot be serialized */
    }
  }
  return (artifact.summary || '').trim()
}

const runIsTerminalFailed = (snapshot: DeepAgentSnapshot) =>
  snapshot.run.status === 'failed' || snapshot.run.status === 'cancelled'

const taskFailureIsSoft = (task: DeepAgentTask, snapshot: DeepAgentSnapshot) => {
  if (task.status !== 'failed') return false
  if (runIsTerminalFailed(snapshot)) return false
  // Later succeeded task with overlapping objective prefix ⇒ superseded by retry.
  const laterSuccess = snapshot.tasks.some(other =>
    other.id !== task.id
    && other.status === 'succeeded'
    && other.created_at >= task.created_at
    && other.capability_id === task.capability_id,
  )
  return laterSuccess || !['failed', 'cancelled', 'completed'].includes(snapshot.run.status)
}

export function DeepAgentArtifacts({
  snapshot,
  onSelectArtifact,
  onRefresh,
  onExtractFrame,
  onRuntimeProgress,
}: {
  snapshot: DeepAgentSnapshot
  onSelectArtifact: (versionId: string) => void
  onRefresh: () => void | Promise<void>
  onExtractFrame?: (options: {
    timestamp: number
    versionId?: string
    videoUrl?: string
  }) => Promise<unknown>
  onRuntimeProgress?: (progress: RuntimeProductionProgress | null) => void
}) {
  const { t } = useLanguage()
  const [runtimeWorkspace, setRuntimeWorkspace] = useState<RuntimeWorkspace | null>(null)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(false)
  const [selectingArtifactId, setSelectingArtifactId] = useState<string | null>(null)
  const [extractingKey, setExtractingKey] = useState<string | null>(null)
  const latestResultRef = useRef<HTMLElement | null>(null)
  const projectId = snapshot.run.project_id
  const runtimeArtifacts = useMemo<DeepAgentArtifact[]>(() => (
    runtimeWorkspace?.artifacts
      .map(artifact => ({
        id: artifact.id,
        artifact_id: artifact.artifact_id,
        project_id: projectId,
        type: artifact.type,
        version: artifact.version,
        status: artifact.status,
        produced_by_task_id: String(artifact.metadata?.build_id || 'video-runtime'),
        title: artifact.title || artifact.logicalId || artifact.type,
        summary: artifact.summary || '',
        uri: artifact.uri || null,
        metadata: {
          ...(artifact.metadata || {}),
          logical_id: artifact.logicalId,
          runtime_status: artifact.status,
          runtime_selected: artifact.isSelected,
        },
        created_at: artifact.created_at || snapshot.run.updated_at,
      })) || []
  ), [projectId, runtimeWorkspace?.artifacts, snapshot.run.updated_at])
  const allArtifacts = useMemo(() => {
    const runtimeIds = new Set(runtimeArtifacts.map(item => item.id))
    return [...snapshot.artifacts.filter(item => !runtimeIds.has(item.id)), ...runtimeArtifacts]
  }, [runtimeArtifacts, snapshot.artifacts])

  useEffect(() => {
    if (!projectId) {
      setRuntimeWorkspace(null)
      setWorkspaceError(null)
      setWorkspaceLoading(false)
      return
    }
    setRuntimeWorkspace(null)
    setWorkspaceLoading(true)
    let active = true
    const load = async () => {
      try {
        const next = await videoRuntimeClient.workspace(projectId)
        if (active) {
          setRuntimeWorkspace(next)
          setWorkspaceError(null)
        }
      } catch (error) {
        if (active) {
          setWorkspaceError(t('da.runtime.requestFailed'))
        }
      } finally {
        if (active) setWorkspaceLoading(false)
      }
    }
    void load()
    const events = new EventSource(`/api/video/projects/${encodeURIComponent(projectId)}/events`)
    const refreshFromEvent = () => void load()
    const eventNames = [
      'build.queued', 'build.status', 'build.step_status', 'artifact.committed',
      'artifact.selected', 'project.version_committed', 'project.version_restored',
      'build.phase.started', 'build.phase.completed', 'build.checkpoint.waiting',
      'build.checkpoint.planning', 'build.checkpoint.resolved', 'build.checkpoint.failed',
      'video_spec.revised', 'plan.revised',
    ]
    eventNames.forEach(name => events.addEventListener(name, refreshFromEvent))
    const interval = window.setInterval(() => void load(), 10000)
    return () => {
      active = false
      window.clearInterval(interval)
      eventNames.forEach(name => events.removeEventListener(name, refreshFromEvent))
      events.close()
    }
  }, [projectId, t])

  const latestRuntimeBuild = runtimeWorkspace?.builds[0]
  useEffect(() => {
    if (!latestRuntimeBuild) {
      onRuntimeProgress?.(null)
      return
    }
    onRuntimeProgress?.({
      buildId: latestRuntimeBuild.buildId,
      status: latestRuntimeBuild.status,
      progress: latestRuntimeBuild.progress || 0,
      message: runtimeMessage(latestRuntimeBuild.message || latestRuntimeBuild.kind || '', t),
      error: latestRuntimeBuild.error,
      phase: runtimeWorkspace?.currentBuildPhase,
      checkpoint: runtimeWorkspace?.activeCheckpoint ? {
        status: runtimeWorkspace.activeCheckpoint.status,
        nextPhase: runtimeWorkspace.activeCheckpoint.next_phase,
        artifactCount: runtimeWorkspace.activeCheckpoint.artifact_summaries.length,
        unresolvedSections: runtimeWorkspace.activeCheckpoint.unresolved_sections,
      } : null,
      steps: latestRuntimeBuild.steps.map(step => ({
        id: step.id,
        name: planStepLabel(step.plan_step_id, t),
        status: step.status,
        error: step.error,
        skills: (step.resolved_skills || []).map(skill => skill.skill_id),
      })),
    })
  }, [
    latestRuntimeBuild,
    onRuntimeProgress,
    runtimeWorkspace?.activeCheckpoint,
    runtimeWorkspace?.currentBuildPhase,
    t,
  ])

  const selectedIds = useMemo(
    () => new Set([
      ...snapshot.selections.map(selection => selection.artifact_version_id),
      ...(runtimeWorkspace?.artifacts.filter(artifact => artifact.isSelected).map(artifact => artifact.id) || []),
    ]),
    [runtimeWorkspace?.artifacts, snapshot.selections],
  )
  const uploadedVideos = useMemo(
    () => (snapshot.run.input_files || []).filter(file =>
      file.type === 'video' && Boolean(file.url),
    ),
    [snapshot.run.input_files],
  )
  const uploadedImages = useMemo(
    () => (snapshot.run.input_files || []).filter(file =>
      file.type === 'image' && Boolean(file.url),
    ),
    [snapshot.run.input_files],
  )

  const handleExtractFrame = async (options: {
    key: string
    timestamp: number
    versionId?: string
    videoUrl?: string
  }) => {
    if (!onExtractFrame) {
      toast.error(t('da.workspace.frameExtractUnavailable'))
      return
    }
    setExtractingKey(options.key)
    try {
      await onExtractFrame({
        timestamp: options.timestamp,
        versionId: options.versionId,
        videoUrl: options.videoUrl,
      })
      toast.success(interpolate(t('da.workspace.frameSaved'), {
        time: formatTimestamp(options.timestamp),
      }))
    } catch (error) {
      toast.error(t('da.workspace.frameExtractFailed'))
    } finally {
      setExtractingKey(null)
    }
  }

  // The Create workspace has one renderer. Every user-facing result, including
  // Video Runtime drafts, is classified by its Artifact type below.
  const visibleArtifacts = allArtifacts.filter(artifact => (
    !isInternalExecutionArtifact(artifact)
    && !containsInternalModelPayload(artifact)
  ))
  const latestMediaArtifact = [...visibleArtifacts]
    .filter(artifact => isImageArtifact(artifact) || isVideoArtifact(artifact))
    .sort((left, right) => right.created_at.localeCompare(left.created_at))[0]
  const latestMediaUri = latestMediaArtifact
    ? artifactMediaUri(latestMediaArtifact)
    : undefined
  const storyArtifacts = visibleArtifacts.filter(isStoryArtifact)
  const textArtifacts = visibleArtifacts.filter(artifact =>
    !isStoryArtifact(artifact) && isReadableTextArtifact(artifact),
  )
  const imageArtifacts = visibleArtifacts.filter(artifact =>
    artifact.id !== latestMediaArtifact?.id
    && !isStoryArtifact(artifact)
    && isImageArtifact(artifact),
  )
  const videoArtifacts = visibleArtifacts.filter(artifact =>
    artifact.id !== latestMediaArtifact?.id
    && !isStoryArtifact(artifact)
    && isVideoArtifact(artifact),
  )
  const audioArtifacts = visibleArtifacts.filter(artifact =>
    !isStoryArtifact(artifact)
    && isAudioArtifact(artifact),
  )
  const otherArtifacts = visibleArtifacts.filter(artifact =>
    !isStoryArtifact(artifact)
    && !isReadableTextArtifact(artifact)
    && !isImageArtifact(artifact)
    && !isVideoArtifact(artifact)
    && !isAudioArtifact(artifact),
  )
  // Runtime task objectives are execution labels; only legacy runs use them as document fallbacks.
  const segmentScripts = (runtimeWorkspace ? [] : [...snapshot.tasks])
    .filter(task =>
      /generate|seedance|video_gen|provider|ark_protocol|image\.|shot\.|keyframe\.|outline|scene|character/i
        .test(task.capability_id)
      && task.objective.trim().length > 20
      && !storyArtifacts.some(artifact => artifact.produced_by_task_id === task.id),
    )
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
  const visibleTasks = runtimeWorkspace
    ? []
    : snapshot.tasks.filter(task =>
      task.status !== 'succeeded' ||
        !snapshot.artifacts.some(artifact => artifact.produced_by_task_id === task.id),
    )
  const handleSelectArtifact = async (artifact: DeepAgentArtifact) => {
    const runtimeArtifact = runtimeWorkspace?.artifacts.find(item => item.id === artifact.id)
    if (!runtimeArtifact) {
      onSelectArtifact(artifact.id)
      return
    }
    if (runtimeArtifact.isSelected) return
    setSelectingArtifactId(artifact.id)
    try {
      await videoRuntimeClient.selectArtifact(
        projectId,
        runtimeArtifact.id,
        runtimeWorkspace.project.current_version_id,
      )
      setRuntimeWorkspace(await videoRuntimeClient.workspace(projectId))
      toast.success(t('da.workspace.artifactSwitched'))
    } catch (error) {
      toast.error(t('da.runtime.requestFailed'))
    } finally {
      setSelectingArtifactId(null)
    }
  }

  const refresh = async () => {
    setWorkspaceLoading(true)
    try {
      await onRefresh()
      if (projectId) {
        setRuntimeWorkspace(await videoRuntimeClient.workspace(projectId))
        setWorkspaceError(null)
      }
    } catch (error) {
      setWorkspaceError(t('da.runtime.requestFailed'))
    } finally {
      setWorkspaceLoading(false)
    }
  }

  useEffect(() => {
    if (!latestMediaArtifact?.id) return
    latestResultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [latestMediaArtifact?.id])

  return (
    <div className="flex h-full min-h-0 flex-col bg-white dark:bg-black">
      <div className="flex items-center justify-between border-b border-border/50 px-5 py-4">
        <div>
          <h2 className="text-lg font-semibold">{t('da.workspace.title')}</h2>
          <p className="text-xs text-muted-foreground">{t('da.workspace.subtitle')}</p>
        </div>
        <Button variant="ghost" size="sm" disabled={workspaceLoading} onClick={() => void refresh()}>
          <RefreshCw className={`mr-2 h-4 w-4 ${workspaceLoading ? 'animate-spin' : ''}`} />
          {t('da.workspace.refresh')}
        </Button>
      </div>
      <ScrollArea
        className="min-h-0 min-w-0 flex-1"
        viewportClassName="overflow-x-hidden [&>div]:!block [&>div]:min-w-0 [&>div]:max-w-full"
      >
        <div className="w-full min-w-0 max-w-full space-y-5 overflow-x-hidden p-5">
          {workspaceError && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-800 dark:text-amber-200">
              {interpolate(t('da.workspace.runtimeUnavailable'), {
                error: runtimeMessage(workspaceError, t),
              })}
            </div>
          )}

          {runtimeWorkspace && (
            <section className="space-y-4" data-testid="video-runtime-workspace">
              <Card className="border-accent-purple/30 bg-card/60">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="text-base">{runtimeWorkspace.project.title}</CardTitle>
                    <Badge variant="outline">
                      {interpolate(t('da.workspace.versionCount'), {
                        n: runtimeWorkspace.projectVersions.length,
                      })}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  {runtimeWorkspace.builds.slice(0, 1).map(build => (
                    <div key={build.buildId} className="rounded-lg border border-border/60 p-3">
                      <div className="mb-2 flex items-center justify-between text-xs">
                        <span>{runtimeMessage(build.message || build.kind, t)}</span>
                        <Badge variant="secondary">{statusLabel(build.status, t)}</Badge>
                      </div>
                      <Progress value={(build.progress || 0) * 100} className="h-1.5" />
                      <div className="mt-3 grid gap-1 text-[11px] text-muted-foreground sm:grid-cols-2">
                        {build.steps.map(step => (
                          <span key={step.id}>{step.status === 'completed' ? '✓' : '○'} {planStepLabel(step.plan_step_id, t)}</span>
                        ))}
                      </div>
                      {build.error && (
                        <p className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                          {t('da.runtime.unknownFailure')}
                        </p>
                      )}
                    </div>
                  ))}

                </CardContent>
              </Card>
            </section>
          )}
          {uploadedImages.length > 0 && (
            <section className="space-y-3" data-testid="uploaded-image-references">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Image className="h-4 w-4" />
                {t('da.workspace.uploadedReferences')}
              </h3>
              <div className="grid min-w-0 max-w-full grid-cols-1 gap-3 sm:grid-cols-2">
                {uploadedImages.map((file, index) => (
                  <Card
                    key={`uploaded-image-${file.url}-${index}`}
                    className="min-w-0 max-w-full overflow-hidden border-border/60 bg-card/60"
                  >
                    <CardHeader className="pb-2">
                      <CardTitle className="flex min-w-0 items-center gap-2 text-sm">
                        <Image className="h-4 w-4 shrink-0" />
                        <span className="truncate">{file.filename || t('da.workspace.uploadedImage')}</span>
                        <Badge variant="outline">{t('da.workspace.uploadBadge')}</Badge>
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="min-w-0 max-w-full overflow-hidden">
                      <img
                        src={file.url}
                        alt={file.filename || t('da.workspace.productReference')}
                        className="mx-auto block h-auto max-h-[min(420px,calc(100dvh-14rem))] max-w-full rounded-lg object-contain"
                      />
                    </CardContent>
                  </Card>
                ))}
              </div>
            </section>
          )}

          {latestMediaArtifact && latestMediaUri && (
            <section ref={latestResultRef} className="scroll-mt-4 space-y-3" data-testid="latest-media-result">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                {isVideoArtifact(latestMediaArtifact)
                  ? <Film className="h-4 w-4" />
                  : <Image className="h-4 w-4" />}
                {t('da.workspace.latestResult')}
              </h3>
              <Card className="min-w-0 max-w-full overflow-hidden border-accent-purple/40 bg-card/60">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="truncate text-base">
                      {artifactTitle(latestMediaArtifact.title, latestMediaArtifact.type, t)}
                    </CardTitle>
                    <div className="flex shrink-0 items-center gap-2">
                      <Badge variant="secondary">{t('da.workspace.ready')}</Badge>
                      {!selectedIds.has(latestMediaArtifact.id) && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={selectingArtifactId === latestMediaArtifact.id}
                          onClick={() => void handleSelectArtifact(latestMediaArtifact)}
                        >
                          {selectingArtifactId === latestMediaArtifact.id && (
                            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                          )}
                          {t('da.workspace.useVersion')}
                        </Button>
                      )}
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="min-w-0 max-w-full space-y-3 overflow-hidden">
                  {isVideoArtifact(latestMediaArtifact) ? (
                    <VideoFramePicker
                      mediaUri={latestMediaUri}
                      title={latestMediaArtifact.title || latestMediaArtifact.type}
                      extracting={extractingKey === `latest-${latestMediaArtifact.id}`}
                      onExtract={timestamp => handleExtractFrame({
                        key: `latest-${latestMediaArtifact.id}`,
                        timestamp,
                        versionId: latestMediaArtifact.id,
                        videoUrl: latestMediaUri,
                      })}
                    />
                  ) : (
                    <img
                      src={latestMediaUri}
                      alt={artifactTitle(latestMediaArtifact.title, latestMediaArtifact.type, t) || t('da.workspace.latestGeneratedImage')}
                      className="mx-auto block h-auto max-h-[calc(100dvh-14rem)] max-w-full rounded-lg object-contain"
                    />
                  )}
                  {latestMediaArtifact.summary && (
                    <p className="text-xs text-muted-foreground">{latestMediaArtifact.summary}</p>
                  )}
                  <GenerationPrompt artifact={latestMediaArtifact} />
                </CardContent>
              </Card>
            </section>
          )}

          {visibleTasks.length > 0 && (
            <Card className="border-border/60 bg-card/60">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{t('da.workspace.planProgress')}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {visibleTasks.map((task) => {
                  const done = task.status === 'succeeded'
                  const active = task.status === 'running' || task.status === 'waiting_external'
                  const softFail = taskFailureIsSoft(task, snapshot)
                  const hardFail = task.status === 'failed' && !softFail
                  return (
                    <div
                      key={task.id}
                      className={
                        softFail
                          ? 'rounded-lg border border-amber-500/40 bg-amber-500/5 p-3'
                          : hardFail
                            ? 'rounded-lg border border-destructive/40 bg-destructive/5 p-3'
                            : 'rounded-lg border border-border/50 p-3'
                      }
                    >
                      <div className="flex items-start gap-3">
                        {done ? (
                          <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-500" />
                        ) : active ? (
                          <Loader2 className="mt-0.5 h-4 w-4 animate-spin text-accent-purple" />
                        ) : softFail ? (
                          <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />
                        ) : hardFail ? (
                          <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
                        ) : (
                          <Circle className="mt-0.5 h-4 w-4 text-muted-foreground" />
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-3">
                            <p className="truncate text-sm font-medium">{task.objective}</p>
                            <Badge
                              variant="outline"
                              className={
                                softFail
                                  ? 'shrink-0 border-amber-500/40 text-[10px] text-amber-700 dark:text-amber-300'
                                  : hardFail
                                    ? 'shrink-0 border-destructive/40 text-[10px] text-destructive'
                                    : 'shrink-0 text-[10px]'
                              }
                            >
                              {softFail ? t('da.workspace.retryable') : statusLabel(task.status, t)}
                            </Badge>
                          </div>
                          <p className="mt-1 text-xs text-muted-foreground">{capabilityLabel(task.capability_id, t)}</p>
                          {typeof task.progress === 'number' && (
                            <Progress value={task.progress} className="mt-2 h-1.5" />
                          )}
                          {task.progress_message && (
                            <p className="mt-1 text-xs text-muted-foreground">{runtimeMessage(task.progress_message, t)}</p>
                          )}
                          {task.error && softFail && (
                            <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
                              {interpolate(t('da.workspace.attemptFailed'), { error: t('da.runtime.unknownFailure') })}
                            </p>
                          )}
                          {task.error && hardFail && (
                            <p className="mt-2 text-xs text-destructive">{t('da.runtime.unknownFailure')}</p>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </CardContent>
            </Card>
          )}

          {textArtifacts.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <FileText className="h-4 w-4" />
                  {t('da.workspace.generatedDocuments')}
                </h3>
                <span className="text-xs text-muted-foreground">
                  {resultCountLabel(textArtifacts.length, t)}
                </span>
              </div>
              {textArtifacts.map((artifact) => {
                const selected = selectedIds.has(artifact.id)
                const body = storyBody(artifact)
                const title = readableTextTitle(artifact, body, t)
                return (
                  <Card key={artifact.id} className="overflow-hidden border-border/60 bg-card shadow-sm">
                    <CardHeader className="border-b border-border/50 bg-muted/20 px-5 py-4">
                      <div className="flex min-w-0 items-start justify-between gap-4">
                        <div className="min-w-0">
                          <CardTitle className="flex min-w-0 items-center gap-2 text-base leading-6">
                            <FileText className="h-4 w-4 shrink-0 text-primary" />
                            <span className="truncate">{title}</span>
                          </CardTitle>
                          <p className="mt-1 pl-6 text-xs text-muted-foreground">
                            {interpolate(t('da.workspace.textResultVersion'), { n: artifact.version })}
                          </p>
                        </div>
                        <Button
                          size="sm"
                          variant={selected ? 'secondary' : 'ghost'}
                          className="shrink-0"
                          disabled={selected || selectingArtifactId === artifact.id}
                          onClick={() => void handleSelectArtifact(artifact)}
                        >
                          {selectingArtifactId === artifact.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                          {selected && <Check className="mr-1 h-3.5 w-3.5" />}
                          {selected ? t('da.workspace.currentVersion') : t('da.workspace.useThisVersion')}
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent className="px-5 py-5 sm:px-6">
                      {body ? (
                        <article className="prose prose-sm max-w-none break-words text-foreground prose-headings:scroll-mt-4 prose-headings:font-semibold prose-h1:text-xl prose-h2:mt-7 prose-h2:text-lg prose-h3:text-base prose-p:leading-7 prose-li:my-1 prose-li:leading-7 prose-table:block prose-table:max-w-full prose-table:overflow-x-auto dark:prose-invert">
                          <ArtifactDocument>{body}</ArtifactDocument>
                        </article>
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          {t('da.workspace.documentPreparing')}
                        </p>
                      )}
                    </CardContent>
                  </Card>
                )
              })}
            </section>
          )}

          {(storyArtifacts.length > 0 || segmentScripts.length > 0) && (
            <section className="space-y-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <FileText className="h-4 w-4" />
                {t('da.workspace.storyScripts')}
              </h3>
              {storyArtifacts.map((artifact) => {
                const selected = selectedIds.has(artifact.id)
                const body = storyBody(artifact)
                return (
                  <Card key={artifact.id} className="overflow-hidden border-border/60 bg-card/60">
                    <CardHeader className="pb-3">
                      <div className="flex items-center justify-between gap-3">
                        <CardTitle className="flex min-w-0 items-center gap-2 text-base">
                          <FileText className="h-4 w-4" />
                          <span className="truncate">{artifactTitle(artifact.title, artifact.type, t) || t('da.workspace.storyScript')}</span>
                          <Badge variant="secondary">v{artifact.version}</Badge>
                        </CardTitle>
                        <Button
                          size="sm"
                          variant={selected ? 'secondary' : 'outline'}
                          disabled={selected || selectingArtifactId === artifact.id}
                          onClick={() => void handleSelectArtifact(artifact)}
                        >
                          {selectingArtifactId === artifact.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                          {selected && <Check className="mr-1 h-3.5 w-3.5" />}
                          {selected ? t('da.workspace.selected') : t('da.workspace.useVersion')}
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent>
                      {body ? (
                        <div className="prose prose-sm max-w-none whitespace-pre-wrap text-foreground dark:prose-invert">
                          <ArtifactDocument>{body}</ArtifactDocument>
                        </div>
                      ) : (
                        <p className="text-xs text-muted-foreground">{t('da.workspace.scriptUnavailable')}</p>
                      )}
                    </CardContent>
                  </Card>
                )
              })}
              {segmentScripts.map((task) => {
                const softFail = taskFailureIsSoft(task, snapshot)
                const hardFail = task.status === 'failed' && !softFail
                return (
                  <Card
                    key={`script-${task.id}`}
                    className={
                      softFail
                        ? 'overflow-hidden border-amber-500/30 bg-amber-500/5'
                        : hardFail
                          ? 'overflow-hidden border-destructive/30 bg-destructive/5'
                          : 'overflow-hidden border-border/60 bg-card/60'
                    }
                  >
                    <CardHeader className="pb-2">
                      <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
                        <FileText className="h-4 w-4" />
                        <span className="truncate">{capabilityLabel(task.capability_id, t)}</span>
                        <Badge
                          variant="outline"
                          className={
                            softFail
                              ? 'text-[10px] text-amber-700 dark:text-amber-300'
                              : hardFail
                                ? 'text-[10px] text-destructive'
                                : 'text-[10px]'
                          }
                        >
                          {softFail ? t('da.workspace.retryable') : statusLabel(task.status, t)}
                        </Badge>
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="prose prose-sm max-w-none whitespace-pre-wrap text-foreground dark:prose-invert">
                        <ArtifactDocument>{task.objective}</ArtifactDocument>
                      </div>
                    </CardContent>
                  </Card>
                )
              })}
            </section>
          )}

          {imageArtifacts.length > 0 && (
            <section className="space-y-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Image className="h-4 w-4" />
                {t('da.workspace.images')}
              </h3>
              <div className="grid min-w-0 max-w-full grid-cols-1 gap-3 sm:grid-cols-2">
                {imageArtifacts.map((artifact) => {
                  const selected = selectedIds.has(artifact.id)
                  const mediaUri = artifactMediaUri(artifact)
                  const title = artifactTitle(artifact.title, artifact.type, t)
                  return (
                    <Card key={artifact.id} className="min-w-0 max-w-full overflow-hidden border-border/60 bg-card/60">
                      <CardHeader className="pb-2">
                        <div className="flex items-center justify-between gap-2">
                          <CardTitle className="truncate text-sm">{title}</CardTitle>
                          <Button
                            size="sm"
                            variant={selected ? 'secondary' : 'outline'}
                            disabled={selected || selectingArtifactId === artifact.id}
                            onClick={() => void handleSelectArtifact(artifact)}
                          >
                            {selectingArtifactId === artifact.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                            {selected ? t('da.workspace.selected') : t('da.workspace.use')}
                          </Button>
                        </div>
                      </CardHeader>
                      <CardContent className="min-w-0 max-w-full space-y-2 overflow-hidden">
                        {mediaUri ? (
                          <img
                            src={mediaUri}
                            alt={title || t('da.workspace.generatedImage')}
                            className="mx-auto block h-auto max-h-[min(360px,calc(100dvh-14rem))] max-w-full rounded-lg object-contain"
                          />
                        ) : (
                          <p className="text-xs text-muted-foreground">{t('da.workspace.imageMissing')}</p>
                        )}
                        {artifact.summary && (
                          <div className="prose prose-sm max-w-none text-foreground dark:prose-invert">
                            <ArtifactDocument>
                              {artifact.summary.replace(/!\[[^\]]*\]\([^)]+\)/g, '').trim()}
                            </ArtifactDocument>
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  )
                })}
              </div>
            </section>
          )}

          {uploadedVideos.length > 0 && (
            <section className="space-y-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Film className="h-4 w-4" />
                {t('da.workspace.uploadedVideos')}
              </h3>
              {uploadedVideos.map((file, index) => {
                const key = `upload-${file.url}-${index}`
                return (
                  <Card key={key} className="overflow-hidden border-border/60 bg-card/60">
                    <CardHeader className="pb-3">
                      <CardTitle className="flex min-w-0 items-center gap-2 text-base">
                        <Film className="h-4 w-4" />
                        <span className="truncate">{file.filename || t('da.workspace.uploadedVideo')}</span>
                        <Badge variant="outline">{t('da.workspace.uploadBadge')}</Badge>
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <VideoFramePicker
                        mediaUri={file.url}
                        title={file.filename || t('da.workspace.uploadedVideo')}
                        extracting={extractingKey === key}
                        onExtract={timestamp => handleExtractFrame({
                          key,
                          timestamp,
                          videoUrl: file.url,
                        })}
                      />
                    </CardContent>
                  </Card>
                )
              })}
            </section>
          )}

          {videoArtifacts.length > 0 && (
            <section className="space-y-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Film className="h-4 w-4" />
                {t('da.workspace.videos')}
              </h3>
              {videoArtifacts.map((artifact) => {
                const selected = selectedIds.has(artifact.id)
                const mediaUri = artifactMediaUri(artifact)
                const extractKey = `artifact-${artifact.id}`
                const title = artifactTitle(artifact.title, artifact.type, t)
                return (
                  <Card key={artifact.id} className="overflow-hidden border-border/60 bg-card/60">
                    <CardHeader className="pb-3">
                      <div className="flex items-center justify-between gap-3">
                        <CardTitle className="flex min-w-0 items-center gap-2 text-base">
                          <Film className="h-4 w-4" />
                          <span className="truncate">{title}</span>
                          <Badge variant="secondary">v{artifact.version}</Badge>
                        </CardTitle>
                        <Button
                          size="sm"
                          variant={selected ? 'secondary' : 'outline'}
                          disabled={selected || selectingArtifactId === artifact.id}
                          onClick={() => void handleSelectArtifact(artifact)}
                        >
                          {selectingArtifactId === artifact.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                          {selected && <Check className="mr-1 h-3.5 w-3.5" />}
                          {selected ? t('da.workspace.selected') : t('da.workspace.useVersion')}
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {mediaUri ? (
                        <VideoFramePicker
                          mediaUri={mediaUri}
                          title={title}
                          extracting={extractingKey === extractKey}
                          onExtract={timestamp => handleExtractFrame({
                            key: extractKey,
                            timestamp,
                            versionId: artifact.id,
                            videoUrl: mediaUri,
                          })}
                        />
                      ) : (
                        <p className="text-xs text-muted-foreground">{t('da.workspace.videoMissing')}</p>
                      )}
                      {artifact.summary && (
                        <div className="prose prose-sm max-w-none text-foreground dark:prose-invert">
                          <ArtifactDocument>{artifact.summary}</ArtifactDocument>
                        </div>
                      )}
                      <GenerationPrompt artifact={artifact} />
                    </CardContent>
                  </Card>
                )
              })}
            </section>
          )}

          {audioArtifacts.length > 0 && (
            <section className="space-y-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Music className="h-4 w-4" />
                {t('da.workspace.audio')}
              </h3>
              {audioArtifacts.map((artifact) => {
                const selected = selectedIds.has(artifact.id)
                const mediaUri = artifactMediaUri(artifact)
                const title = artifactTitle(artifact.title, artifact.type, t)
                return (
                  <Card key={artifact.id} className="overflow-hidden border-border/60 bg-card/60">
                    <CardHeader className="pb-3">
                      <div className="flex items-center justify-between gap-3">
                        <CardTitle className="flex min-w-0 items-center gap-2 text-base">
                          <Music className="h-4 w-4" />
                          <span className="truncate">{title}</span>
                          <Badge variant="secondary">v{artifact.version}</Badge>
                        </CardTitle>
                        <Button
                          size="sm"
                          variant={selected ? 'secondary' : 'outline'}
                          disabled={selected || selectingArtifactId === artifact.id}
                          onClick={() => void handleSelectArtifact(artifact)}
                        >
                          {selectingArtifactId === artifact.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                          {selected && <Check className="mr-1 h-3.5 w-3.5" />}
                          {selected ? t('da.workspace.selected') : t('da.workspace.useVersion')}
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {mediaUri ? (
                        <audio src={mediaUri} controls className="w-full" />
                      ) : (
                        <p className="text-xs text-muted-foreground">{t('da.workspace.audioMissing')}</p>
                      )}
                      {storyBody(artifact) && (
                        <div className="prose prose-sm max-w-none text-foreground dark:prose-invert">
                          <ArtifactDocument>{storyBody(artifact)}</ArtifactDocument>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )
              })}
            </section>
          )}

          {otherArtifacts.map((artifact) => {
            const selected = selectedIds.has(artifact.id)
            const mediaUri = artifactMediaUri(artifact)
            const body = storyBody(artifact)
            const title = artifactTitle(artifact.title, artifact.type, t)
            return (
              <Card key={artifact.id} className="overflow-hidden border-border/60 bg-card/60">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="flex min-w-0 items-center gap-2 text-base">
                      <FileText className="h-4 w-4" />
                      <span className="truncate">{title}</span>
                      <Badge variant="secondary">v{artifact.version}</Badge>
                    </CardTitle>
                    <Button
                      size="sm"
                      variant={selected ? 'secondary' : 'outline'}
                      disabled={selected || selectingArtifactId === artifact.id}
                      onClick={() => void handleSelectArtifact(artifact)}
                    >
                      {selectingArtifactId === artifact.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                      {selected && <Check className="mr-1 h-3.5 w-3.5" />}
                      {selected ? t('da.workspace.selected') : t('da.workspace.useVersion')}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {body && (
                    <div className="prose prose-sm max-w-none text-foreground dark:prose-invert">
                      <ArtifactDocument>{body}</ArtifactDocument>
                    </div>
                  )}
                  {mediaUri && (
                    <a href={mediaUri} target="_blank" rel="noreferrer" className="text-sm text-primary underline-offset-4 hover:underline">
                      {t('da.workspace.openArtifact')}
                    </a>
                  )}
                </CardContent>
              </Card>
            )
          })}

          {visibleTasks.length === 0 && visibleArtifacts.length === 0 && uploadedVideos.length === 0 && (
            <div className="flex min-h-[360px] flex-col items-center justify-center text-center text-muted-foreground">
              <Film className="mb-4 h-12 w-12 opacity-30" />
              <p className="text-sm">{t('da.workspace.empty')}</p>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}
