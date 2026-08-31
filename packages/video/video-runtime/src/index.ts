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
  abstract previewChange(request: ChangePreviewRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<ChangePreview>
  abstract previewEdits(request: EditPreviewRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildPlanSnapshot>
  abstract planProject(request: BuildPlanRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildPlanSnapshot>
  abstract startBuild(request: RebuildRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract applyRebuild(request: RebuildRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract getBuild(projectId: string, buildId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract cancelBuild(projectId: string, buildId: string, identity: RequestIdentity, signal?: AbortSignal): Promise<BuildSnapshot>
  abstract selectArtifact(
    request: ArtifactSelectionRequest,
    identity: RequestIdentity,
    signal?: AbortSignal,
  ): Promise<ArtifactSelectionResult>
  abstract exportProject(request: ExportRequest, identity: RequestIdentity, signal?: AbortSignal): Promise<ExportResult>
}

export default VideoRuntime
