import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { CallId } from '@deepseek-ai/dsh-llm'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { VideoRuntime } from '@cuti-ai/video-runtime'
import type {
  ArtifactSelectionRequest, ArtifactSelectionResult, BuildPlanRequest, BuildPlanSnapshot,
  BuildSnapshot, ChangePreview, CreateProjectRequest,
  ChangePreviewRequest, ExportRequest, ExportResult, ProjectSnapshot, RebuildRequest,
  RequestIdentity,
  EditPreviewRequest,
  WorkflowSummary, WorkflowDetail, SkillDetail,
} from '@cuti-ai/video-runtime'
import * as videoTools from '../src/index.ts'

const project: ProjectSnapshot = {
  projectId: 'project-1', title: 'Demo', status: 'active', currentVersionId: 'version-1',
  artifactCount: 3, activeBuildIds: [], summary: 'ready',
}

class FakeVideoRuntime extends VideoRuntime {
  createProject(_request: CreateProjectRequest, _identity: RequestIdentity): Promise<ProjectSnapshot> { return Promise.resolve(project) }
  openProject(_id: string, _identity: RequestIdentity): Promise<ProjectSnapshot> { return Promise.resolve(project) }
  inspectProject(_id: string, _identity: RequestIdentity): Promise<ProjectSnapshot> { return Promise.resolve(project) }
  listWorkflows(): Promise<WorkflowSummary[]> { return Promise.resolve([]) }
  loadWorkflow(workflowId: string): Promise<WorkflowDetail> {
    return Promise.resolve({ id: workflowId, title: workflowId, description: '', mode: 'test', available: true, requiredCapabilities: [], missingCapabilities: [], userSelectable: true, executionKind: 'adapter', entrypoints: [], parameters: {}, pipeline: [], skillDependencies: [], instructions: '', resources: [], resourceContents: [] })
  }
  loadSkill(skillId: string): Promise<SkillDetail> { return Promise.resolve({ id: skillId, description: '', kind: 'helper', instructions: '', resources: [], resourceContents: [] }) }
  previewChange(request: ChangePreviewRequest): Promise<ChangePreview> {
    return Promise.resolve({ planId: 'plan-1', projectId: request.projectId, baseProjectVersionId: 'version-1', staleArtifactIds: [], validationArtifactIds: [], reusedArtifactIds: [], rebuildOrder: [], estimatedCost: 0 })
  }
  previewEdits(request: EditPreviewRequest): Promise<BuildPlanSnapshot> {
    return Promise.resolve({
      planId: 'edit-plan-1', projectId: request.projectId, kind: 'incremental', status: 'preview',
      baseProjectVersionId: request.baseProjectVersionId, workflowId: 'cuti.seedance-story',
      videoSpec: {} as BuildPlanSnapshot['videoSpec'], shotCount: 0, estimatedCost: 0, steps: [],
    })
  }
  planProject(request: BuildPlanRequest): Promise<BuildPlanSnapshot> {
    return Promise.resolve({
      planId: 'initial-plan-1', projectId: request.projectId, kind: 'initial', status: 'preview',
      baseProjectVersionId: request.baseProjectVersionId, workflowId: request.videoSpec.workflow_id,
      videoSpec: request.videoSpec, shotCount: request.videoSpec.shots.length, estimatedCost: 1,
      steps: [],
    })
  }
  startBuild(request: RebuildRequest): Promise<BuildSnapshot> { return Promise.resolve({ ...this.build(request.projectId), kind: 'initial' }) }
  applyRebuild(request: RebuildRequest): Promise<BuildSnapshot> { return Promise.resolve(this.build(request.projectId)) }
  getBuild(projectId: string): Promise<BuildSnapshot> { return Promise.resolve(this.build(projectId)) }
  cancelBuild(projectId: string): Promise<BuildSnapshot> { return Promise.resolve({ ...this.build(projectId), status: 'cancelled' }) }
  selectArtifact(request: ArtifactSelectionRequest): Promise<ArtifactSelectionResult> { return Promise.resolve({ projectId: request.projectId, artifactId: 'artifact-1', versionId: request.versionId, projectVersionId: 'version-2' }) }
  exportProject(request: ExportRequest): Promise<ExportResult> { return Promise.resolve({ exportId: 'export-1', projectId: request.projectId, status: 'queued' }) }
  private build(projectId: string): BuildSnapshot { return { buildId: 'build-1', projectId, status: 'queued', progress: 0, message: 'Queued', kind: 'incremental', estimatedCost: 0, actualCost: 0 } }
}

describe('video tool composition', () => {
  it('mounts exactly the stable high-level tool surface and dispatches through the runtime service', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(FakeVideoRuntime)
    await ctx.plugin(videoTools)

    const names = [
      'video_workflow_list', 'video_workflow_load', 'video_skill_load',
      'video_project_create', 'video_project_plan', 'video_project_build',
      'video_project_open', 'video_project_inspect', 'video_change_preview', 'video_edit_preview', 'video_rebuild_apply',
      'video_build_status', 'video_build_cancel', 'video_artifact_select', 'video_export',
    ]
    for (const name of names) expect(ctx.tools.get(name)).toBeDefined()
    const result = await ctx.tools.execute({
      callId: CallId('video-call-1'), name: 'video_project_open',
      arguments: { project_id: 'project-1' }, signal: new AbortController().signal,
    })
    expect(result.isError).toBe(false)
    expect(result.value).toEqual(project)

    const legacyWorkflowPlan = await ctx.tools.execute({
      callId: CallId('video-call-2'), name: 'video_project_plan',
      arguments: {
        project_id: 'project-1',
        base_project_version_id: 'version-1',
        idempotency_key: 'legacy-workflow-plan',
        video_spec: {
          title: 'Short drama', language: 'zh-CN', target_duration_seconds: 5,
          aspect_ratio: '16:9', resolution: '1080p', workflow_id: 'workflow-short-drama',
          style_id: 'cuti.cinematic', characters: [],
          shots: [{
            id: 'shot-1', order: 1, duration_seconds: 5, beat: 'Opening',
            visual_prompt: 'An actor enters', character_ids: [],
          }],
          audio: { narration_voice: 'default', bgm_prompt: '', subtitles: false },
          providers: { video: 'seedance-2.0', image: 'gpt-image-2', music: 'suno' },
          automation: { mode: 'automatic', max_artifact_retries: 1 },
        },
      },
      signal: new AbortController().signal,
    })
    expect(legacyWorkflowPlan.isError).toBe(false)
    expect((legacyWorkflowPlan.value as unknown as BuildPlanSnapshot).workflowId)
      .toBe('workflow-short-drama')
  })
})
