/** Video project runtime capability seam (`ctx.videoRuntime`). @module @cuti-ai/video-runtime */

import { Context, Service } from '@deepseek-ai/cordis'
import type {
  ArtifactSelectionRequest,
  ArtifactSelectionResult,
  BuildSnapshot,
  BuildPlanRequest,
  BuildPlanSnapshot,
  ChangePreview,
  ChangePreviewRequest,
  ExportRequest,
  ExportResult,
  ProjectSnapshot,
  CreateProjectRequest,
  RebuildRequest,
  RequestIdentity,
  EditPreviewRequest,
  MediaArtifactSummary,
  PlanPatchCapabilityContract,
  PlanPatchPreviewRequest,
  WorkflowSummary,
  WorkflowDetail,
  SkillDetail,
  SkillResourceDetail,
  PlanCheckpoint,
  CheckpointResolutionRequest,
} from './types.ts'

export type * from './types.ts'

declare module '@deepseek-ai/cordis' {
  interface Context {
    videoRuntime: VideoRuntime
  }
}

/** Stable interface implemented by local or remote video project runtimes. */
export abstract class VideoRuntime extends Service {
  constructor(ctx: Context) {
    super(ctx, 'videoRuntime')
  }

  abstract openProject(projectId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<ProjectSnapshot>
  abstract createProject(request: CreateProjectRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<ProjectSnapshot>
  abstract inspectProject(projectId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<ProjectSnapshot>
  abstract listWorkflows(identity: RequestIdentity, signal?: AbortSignal): Promise<WorkflowSummary[]>
  abstract loadWorkflow(workflowId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<WorkflowDetail>
  abstract loadSkill(skillId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<SkillDetail>
  abstract loadSkillResource(skillId: string, path: string, identity: RequestIdentity, signal?: AbortSignal): Promise<SkillResourceDetail>
  abstract previewChange(request: ChangePreviewRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<ChangePreview>
  abstract previewEdits(request: EditPreviewRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildPlanSnapshot>
  abstract listArtifacts(projectId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<MediaArtifactSummary[]>
  abstract listPlanPatchCapabilities(identity: RequestIdentity, signal?: AbortSignal): Promise<PlanPatchCapabilityContract[]>
  abstract previewPlanPatch(request: PlanPatchPreviewRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildPlanSnapshot>
  abstract planProject(request: BuildPlanRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildPlanSnapshot>
  abstract startBuild(request: RebuildRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract applyRebuild(request: RebuildRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract getBuild(projectId: string, buildId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract cancelBuild(projectId: string, buildId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract inspectCheckpoint(
    projectId: string,
    buildId: string,
    checkpointId: string,
    identity: RequestIdentity,
    signal?: AbortSignal,
  ): Promise<PlanCheckpoint>
  abstract resolveCheckpoint(
    request: CheckpointResolutionRequest,
    identity: RequestIdentity,
    signal?: AbortSignal,
  ): Promise<BuildPlanSnapshot>
  abstract retryCheckpoint(
    projectId: string,
    buildId: string,
    checkpointId: string,
    identity: RequestIdentity,
    signal?: AbortSignal,
  ): Promise<PlanCheckpoint>
  abstract selectArtifact(
    request: ArtifactSelectionRequest,
    identity: RequestIdentity,
    signal?: AbortSignal,
  ): Promise<ArtifactSelectionResult>
  abstract exportProject(request: ExportRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<ExportResult>
}

export default VideoRuntime
