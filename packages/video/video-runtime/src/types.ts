/** Serializable video project runtime vocabulary. @module @cuti-ai/video-runtime/types */

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }

export interface ProjectSnapshot {
  projectId: string
  title: string
  status: string
  currentVersionId: string
  artifactCount: number
  activeBuildIds: string[]
  summary: string
}

export interface VideoCharacterSpec {
  id: string
  name: string
  appearance: string
  clothing?: string
  personality?: string
  voice?: string
}

export interface VideoShotSpec {
  id: string
  order: number
  duration_seconds: number
  beat: string
  visual_prompt: string
  narration?: string
  character_ids?: string[]
  transition?: string
}

export interface VideoSpec {
  title: string
  language: string
  target_duration_seconds: number
  aspect_ratio: '16:9' | '9:16' | '1:1'
  resolution: string
  /** Installed Video Runtime workflow id, including workflow SKILL.md adapters. */
  workflow_id: string
  style_id: string
  activated_skill_ids?: string[]
  characters: VideoCharacterSpec[]
  shots: VideoShotSpec[]
  audio: { narration_voice: string; bgm_prompt: string; subtitles: boolean }
  providers: { video: string; image: string; music: string }
  automation: { mode: 'automatic'; max_artifact_retries: number }
}

export interface CreateProjectRequest {
  title: string
  idempotencyKey: string
}

export interface BuildPlanRequest {
  projectId: string
  baseProjectVersionId: string
  idempotencyKey: string
  videoSpec: VideoSpec
}

export interface BuildPlanSnapshot {
  planId: string
  projectId: string
  kind: 'initial' | 'incremental' | 'export'
  status: string
  baseProjectVersionId: string
  workflowId: string
  videoSpec: VideoSpec
  shotCount: number
  estimatedCost: number
  steps: Array<Record<string, JsonValue>>
}

export interface ChangePreviewRequest {
  projectId: string
  change: string
  targetArtifactVersionIds: string[]
  idempotencyKey: string
}

export interface ChangePreview {
  planId: string
  projectId: string
  baseProjectVersionId: string
  staleArtifactIds: string[]
  validationArtifactIds: string[]
  reusedArtifactIds: string[]
  rebuildOrder: string[]
  estimatedCost: number
}

export type VideoEditType =
  | 'patch_character' | 'patch_scene' | 'patch_shot' | 'regenerate_artifact'
  | 'replace_music' | 'generate_lipsync' | 'patch_timeline'

export interface VideoEdit {
  type: VideoEditType
  id?: string
  artifactVersionId?: string
  prompt?: string
  patch?: Record<string, JsonValue>
}

export interface EditPreviewRequest {
  projectId: string
  baseProjectVersionId: string
  idempotencyKey: string
  description: string
  edits: VideoEdit[]
}

export interface RebuildRequest {
  projectId: string
  planId: string
  baseProjectVersionId: string
  idempotencyKey: string
}

export interface BuildSnapshot {
  buildId: string
  projectId: string
  status: 'queued' | 'running' | 'waiting_external' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message: string
  projectVersionId?: string
  kind: 'initial' | 'incremental' | 'export'
  estimatedCost: number
  actualCost: number
  error?: string
}

export interface ArtifactSelectionRequest {
  projectId: string
  versionId: string
  baseProjectVersionId: string
  idempotencyKey: string
}

export interface ArtifactSelectionResult {
  projectId: string
  artifactId: string
  versionId: string
  projectVersionId: string
}

export interface ExportRequest {
  projectId: string
  baseProjectVersionId: string
  idempotencyKey: string
  format: string
}

export interface ExportResult {
  exportId: string
  projectId: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  uri?: string
}

export interface RequestIdentity {
  sessionId?: string
  userId?: string
}
