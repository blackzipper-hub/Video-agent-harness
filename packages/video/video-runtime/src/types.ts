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
  reference_asset_ids?: string[]
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
  source_asset_ids?: string[]
  workflow_parameters?: Record<string, JsonValue>
  characters: VideoCharacterSpec[]
  shots: VideoShotSpec[]
  audio: { narration_voice: string; bgm_prompt: string; subtitles: boolean }
  providers: { video: string; image: string; music: string }
  automation: { mode: 'automatic'; max_artifact_retries: number }
}

export interface ProjectIntent {
  title: string
  brief: string
  language: string
  target_duration_seconds: number
  aspect_ratio: '16:9' | '9:16' | '1:1'
  resolution: string
  workflow_id: string
  style_id: string
  activated_skill_ids?: string[]
  source_asset_ids?: string[]
  workflow_parameters?: Record<string, JsonValue>
  providers: { video: string; image: string; music: string }
  automation: { mode: 'automatic'; max_artifact_retries: number }
  constraints?: Record<string, JsonValue>
}

export interface CreateProjectRequest {
  title: string
  idempotencyKey: string
}

export interface BuildPlanRequest {
  projectId: string
  baseProjectVersionId: string
  idempotencyKey: string
  videoSpec?: VideoSpec
  projectIntent?: ProjectIntent
}

export interface BuildPlanSnapshot {
  planId: string
  projectId: string
  kind: 'initial' | 'incremental' | 'export'
  status: string
  baseProjectVersionId: string
  workflowId: string
  videoSpec?: VideoSpec
  projectIntent?: ProjectIntent
  shotCount: number
  schemaVersion: 1 | 2
  planRevision: number
  specRevisionId?: string
  currentPhase: string
  nextCheckpoint?: Record<string, JsonValue>
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
  status: 'queued' | 'running' | 'waiting_external' | 'waiting_agent' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message: string
  projectVersionId?: string
  kind: 'initial' | 'incremental' | 'export'
  estimatedCost: number
  actualCost: number
  error?: string
}

export interface PlanCheckpoint {
  id: string
  project_id: string
  build_id: string
  plan_id: string
  workflow_id: string
  session_id: string
  phase: string
  next_phase: string
  status: 'pending' | 'planning' | 'resolved' | 'failed'
  artifact_summaries: Array<Record<string, JsonValue>>
  resolved_sections: string[]
  unresolved_sections: string[]
  planner_instruction: string
  planning_mode: 'staged' | 'agentic'
  base_plan_revision: number
  base_spec_revision: number
  delivery_attempts: number
  error?: string
}

export interface CheckpointResolutionRequest {
  projectId: string
  buildId: string
  checkpointId: string
  basePlanRevision: number
  baseSpecRevision: number
  idempotencyKey: string
  videoSpec?: VideoSpec
  videoSpecPatch?: Record<string, JsonValue>
  phaseInputs?: Record<string, JsonValue>
  proposedSteps?: Array<Record<string, JsonValue>>
  reason?: string
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

export interface WorkflowSummary {
  id: string
  title: string
  description: string
  mode: string
  available: boolean
  unavailableReason?: string
  requiredCapabilities: string[]
  missingCapabilities: string[]
  userSelectable: boolean
  executionKind: string
  /** Exact Runtime compiler selected for this Workflow; absent only when unavailable. */
  compiler?: string | null
  entrypoints: string[]
}

export interface WorkflowDetail extends WorkflowSummary {
  parameters: Record<string, JsonValue>
  pipeline: string[]
  skillDependencies: string[]
  instructions: string
  /** Optional Skill whose original instructions define this plugin Workflow alias. */
  instructionSkillId?: string
  /** Explicit plugin-only extension appended after the source Skill instructions. */
  instructionAppendix?: string
  /** Skill id that must be passed to loadSkillResource for every listed path. */
  resourceOwnerSkillId: string
  resources: string[]
  resourceContents: Array<{ path: string; content: string }>
}

export interface SkillDetail {
  id: string
  description: string
  kind: string
  instructions: string
  /** Skill id that must be passed to loadSkillResource for every listed path. */
  resourceOwnerSkillId: string
  resources: string[]
  resourceContents: Array<{ path: string; content: string }>
}

export interface SkillResourceDetail {
  id: string
  path: string
  content: string
}
