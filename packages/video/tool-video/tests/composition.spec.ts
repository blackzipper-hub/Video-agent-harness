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
  WorkflowSummary, WorkflowDetail,   SkillDetail,
  SkillResourceDetail, PlanCheckpoint, CheckpointResolutionRequest,
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
    return Promise.resolve({
      id: workflowId, title: workflowId, description: '', mode: 'test', available: true,
      requiredCapabilities: [], missingCapabilities: [], userSelectable: true,
      executionKind: 'adapter', entrypoints: [], parameters: {}, pipeline: [],
      skillDependencies: [], instructions: 'Call video_skill_load("helper-skill").',
      resources: ['reference.md'],
      resourceContents: [{ path: 'reference.md', content: 'should not reach the model' }],
    })
  }
  loadSkill(skillId: string): Promise<SkillDetail> {
    return Promise.resolve({
      id: skillId, description: '', kind: 'helper', instructions: 'Read note.md.',
      resources: ['references/note.md'],
      resourceContents: [{ path: 'references/note.md', content: 'should not reach the model' }],
    })
  }
  loadSkillResource(skillId: string, path: string): Promise<SkillResourceDetail> {
    return Promise.resolve({ id: skillId, path, content: 'file body' })
  }
  previewChange(request: ChangePreviewRequest): Promise<ChangePreview> {
    return Promise.resolve({ planId: 'plan-1', projectId: request.projectId, baseProjectVersionId: 'version-1', staleArtifactIds: [], validationArtifactIds: [], reusedArtifactIds: [], rebuildOrder: [], estimatedCost: 0 })
  }
  previewEdits(request: EditPreviewRequest): Promise<BuildPlanSnapshot> {
    return Promise.resolve({
      planId: 'edit-plan-1', projectId: request.projectId, kind: 'incremental', status: 'preview',
      baseProjectVersionId: request.baseProjectVersionId, workflowId: 'cuti.seedance-story',
      videoSpec: {} as NonNullable<BuildPlanSnapshot['videoSpec']>, shotCount: 0, estimatedCost: 0, steps: [],
      schemaVersion: 1, planRevision: 1, currentPhase: 'full',
    })
  }
  planProject(request: BuildPlanRequest): Promise<BuildPlanSnapshot> {
    const workflowId = request.videoSpec?.workflow_id ?? request.projectIntent?.workflow_id ?? 'unknown'
    return Promise.resolve({
      planId: 'initial-plan-1', projectId: request.projectId, kind: 'initial', status: 'preview',
      baseProjectVersionId: request.baseProjectVersionId, workflowId,
      ...(request.videoSpec === undefined ? {} : { videoSpec: request.videoSpec }),
      ...(request.projectIntent === undefined ? {} : { projectIntent: request.projectIntent }),
      shotCount: request.videoSpec?.shots.length ?? 0, estimatedCost: 1,
      steps: [], schemaVersion: 2, planRevision: 1, currentPhase: 'creative_intent',
    })
  }
  startBuild(request: RebuildRequest): Promise<BuildSnapshot> { return Promise.resolve({ ...this.build(request.projectId), kind: 'initial' }) }
  applyRebuild(request: RebuildRequest): Promise<BuildSnapshot> { return Promise.resolve(this.build(request.projectId)) }
  getBuild(projectId: string): Promise<BuildSnapshot> { return Promise.resolve(this.build(projectId)) }
  cancelBuild(projectId: string): Promise<BuildSnapshot> { return Promise.resolve({ ...this.build(projectId), status: 'cancelled' }) }
  inspectCheckpoint(projectId: string, buildId: string, checkpointId: string): Promise<PlanCheckpoint> {
    return Promise.resolve({
      id: checkpointId, project_id: projectId, build_id: buildId, plan_id: 'plan-1',
      workflow_id: 'mv', session_id: 'session-1', phase: 'music_analysis',
      next_phase: 'visual_production', status: 'planning', artifact_summaries: [],
      resolved_sections: [], unresolved_sections: ['shots'], planner_instruction: '',
      planning_mode: 'staged', base_plan_revision: 1, base_spec_revision: 1,
      delivery_attempts: 1,
    })
  }
  resolveCheckpoint(request: CheckpointResolutionRequest): Promise<BuildPlanSnapshot> {
    const videoSpec = request.videoSpec!
    return Promise.resolve({
      planId: 'plan-1', projectId: request.projectId, kind: 'initial', status: 'applied',
      baseProjectVersionId: 'version-1', workflowId: videoSpec.workflow_id,
      videoSpec, shotCount: videoSpec.shots.length,
      estimatedCost: 1, steps: [], schemaVersion: 2, planRevision: 2,
      currentPhase: 'visual_production',
    })
  }
  retryCheckpoint(projectId: string, buildId: string, checkpointId: string): Promise<PlanCheckpoint> {
    return this.inspectCheckpoint(projectId, buildId, checkpointId)
  }
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
      'video_skill_read_resource',
      'video_project_create', 'video_project_plan', 'video_project_build',
      'video_project_open', 'video_project_inspect', 'video_change_preview', 'video_edit_preview', 'video_rebuild_apply',
      'video_build_status', 'video_checkpoint_inspect', 'video_checkpoint_resolve',
      'video_build_retry_checkpoint', 'video_build_cancel', 'video_artifact_select', 'video_export',
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

    const loadedSkill = await ctx.tools.execute({
      callId: CallId('video-call-3'), name: 'video_skill_load',
      arguments: { skill_id: 'helper-skill' }, signal: new AbortController().signal,
    })
    expect(loadedSkill.isError).toBe(false)
    expect(loadedSkill.value).toMatchObject({
      id: 'helper-skill',
      instructions: 'Read note.md.',
      resources: ['references/note.md'],
    })
    expect(loadedSkill.value).not.toHaveProperty('resourceContents')

    const skillFile = await ctx.tools.execute({
      callId: CallId('video-call-4'), name: 'video_skill_read_resource',
      arguments: { skill_id: 'helper-skill', path: 'references/note.md' },
      signal: new AbortController().signal,
    })
    expect(skillFile.isError).toBe(false)
    expect(skillFile.value).toEqual({
      id: 'helper-skill', path: 'references/note.md', content: 'file body',
    })

    const loadedWorkflow = await ctx.tools.execute({
      callId: CallId('video-call-5'), name: 'video_workflow_load',
      arguments: { workflow_id: 'demo-workflow' }, signal: new AbortController().signal,
    })
    expect(loadedWorkflow.isError).toBe(false)
    expect(loadedWorkflow.value).toMatchObject({
      id: 'demo-workflow',
      resources: ['reference.md'],
    })
    expect(loadedWorkflow.value).not.toHaveProperty('resourceContents')
  })
})
