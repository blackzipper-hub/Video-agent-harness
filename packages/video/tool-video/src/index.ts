/** Model-facing tools for project-oriented incremental video construction. @module @cuti-ai/tool-video */

import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { RequestIdentity } from '@cuti-ai/video-runtime'

export const name = 'cuti-tool-video'
export const inject = ['tools', 'videoRuntime']

const projectOutput = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    projectId: { type: 'string' as const, required: true },
    title: { type: 'string' as const, required: true },
    status: { type: 'string' as const, required: true },
    currentVersionId: { type: 'string' as const, required: true },
    artifactCount: { type: 'integer' as const, required: true },
    activeBuildIds: { type: 'array' as const, required: true, items: { type: 'string' as const } },
    summary: { type: 'string' as const, required: true },
  },
} as const

const buildOutput = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    buildId: { type: 'string' as const, required: true },
    projectId: { type: 'string' as const, required: true },
    status: {
      type: 'string' as const,
      required: true,
      enum: ['queued', 'running', 'waiting_external', 'waiting_agent', 'completed', 'failed', 'cancelled'],
    },
    progress: { type: 'number' as const, required: true },
    message: { type: 'string' as const, required: true },
    projectVersionId: { type: 'string' as const },
    kind: { type: 'string' as const, enum: ['initial', 'incremental', 'export'] },
    estimatedCost: { type: 'number' as const },
    actualCost: { type: 'number' as const },
    error: { type: 'string' as const },
  },
} as const

const projectIntentParameters = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    title: { type: 'string' as const, required: true },
    brief: { type: 'string' as const, required: true },
    language: { type: 'string' as const, required: true },
    target_duration_seconds: { type: 'number' as const, required: true },
    aspect_ratio: { type: 'string' as const, required: true, enum: ['16:9', '9:16', '1:1'] },
    resolution: { type: 'string' as const, required: true },
    workflow_id: { type: 'string' as const, required: true },
    style_id: { type: 'string' as const, required: true },
    activated_skill_ids: { type: 'array' as const, items: { type: 'string' as const } },
    source_asset_ids: { type: 'array' as const, items: { type: 'string' as const } },
    workflow_parameters: { type: 'object' as const, additionalProperties: true },
    providers: {
      type: 'object' as const, required: true, additionalProperties: false, properties: {
        video: { type: 'string' as const, required: true },
        image: { type: 'string' as const, required: true },
        music: { type: 'string' as const, required: true },
      },
    },
    automation: {
      type: 'object' as const, required: true, additionalProperties: false, properties: {
        mode: { type: 'string' as const, required: true, enum: ['automatic'] },
        max_artifact_retries: { type: 'integer' as const, required: true },
      },
    },
    constraints: { type: 'object' as const, additionalProperties: true },
  },
} as const

const videoSpecParameters = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    title: { type: 'string' as const, required: true },
    language: { type: 'string' as const, required: true },
    target_duration_seconds: { type: 'number' as const, required: true },
    aspect_ratio: { type: 'string' as const, required: true, enum: ['16:9', '9:16', '1:1'] },
    resolution: { type: 'string' as const, required: true },
    workflow_id: {
      type: 'string' as const,
      required: true,
      description: 'Installed Video Runtime workflow id. Legacy Cuti workflow SKILL.md names are valid when installed.',
    },
    style_id: { type: 'string' as const, required: true },
    activated_skill_ids: {
      type: 'array' as const,
      items: { type: 'string' as const },
      description: 'Optional helper Skills the user or a project lock explicitly pinned. Do not list helpers that a workflow named via video_skill_load.',
    },
    source_asset_ids: {
      type: 'array' as const,
      items: { type: 'string' as const },
      description: 'Project-owned uploaded source artifact ids. Never invent ids or pass arbitrary URLs.',
    },
    workflow_parameters: {
      type: 'object' as const,
      additionalProperties: true,
      description: 'Workflow-specific structured parameters returned by video_workflow_load.',
    },
    characters: {
      type: 'array' as const, required: true, items: {
        type: 'object' as const, additionalProperties: false, properties: {
          id: { type: 'string' as const, required: true },
          name: { type: 'string' as const, required: true },
          appearance: { type: 'string' as const, required: true },
          clothing: { type: 'string' as const },
          personality: { type: 'string' as const },
          voice: { type: 'string' as const },
        },
      },
    },
    shots: {
      type: 'array' as const, required: true, items: {
        type: 'object' as const, additionalProperties: false, properties: {
          id: { type: 'string' as const, required: true },
          order: { type: 'integer' as const, required: true },
          duration_seconds: { type: 'number' as const, required: true },
          beat: { type: 'string' as const, required: true },
          visual_prompt: { type: 'string' as const, required: true },
          narration: { type: 'string' as const },
          character_ids: { type: 'array' as const, items: { type: 'string' as const } },
          reference_asset_ids: { type: 'array' as const, items: { type: 'string' as const } },
          transition: { type: 'string' as const },
        },
      },
    },
    audio: {
      type: 'object' as const, required: true, additionalProperties: false, properties: {
        narration_voice: { type: 'string' as const, required: true },
        bgm_prompt: { type: 'string' as const, required: true },
        subtitles: { type: 'boolean' as const, required: true },
      },
    },
    providers: {
      type: 'object' as const, required: true, additionalProperties: false, properties: {
        video: { type: 'string' as const, required: true },
        image: { type: 'string' as const, required: true },
        music: { type: 'string' as const, required: true },
      },
    },
    automation: {
      type: 'object' as const, required: true, additionalProperties: false, properties: {
        mode: { type: 'string' as const, required: true, enum: ['automatic'] },
        max_artifact_retries: { type: 'integer' as const, required: true },
      },
    },
  },
} as const

function identityOf(agent: { session: { header: { id: unknown } } } | undefined): RequestIdentity {
  return agent === undefined ? {} : { sessionId: String(agent.session.header.id) }
}

function withoutInjectedResources(
  value: Record<string, import('@cuti-ai/video-runtime').JsonValue>,
): Record<string, import('@cuti-ai/video-runtime').JsonValue> {
  const { resourceContents: _ignored, ...rest } = value
  return rest
}

function bundledResourceText(value: Record<string, import('@cuti-ai/video-runtime').JsonValue>): string {
  const resources = Array.isArray(value.resources) ? value.resources.map(item => String(item)) : []
  return resources.length === 0 ? '' : `\n\nResources:\n${resources.join('\n')}`
}

function projectText(value: { projectId: string; currentVersionId: string; artifactCount: number; summary: string }): string {
  return `Video project ${value.projectId} at ${value.currentVersionId}: ${value.artifactCount} artifacts. ${value.summary}`
}

function buildText(value: { buildId: string; status: string; progress: number; message: string }): string {
  return `Video build ${value.buildId}: ${value.status} (${Math.round(value.progress * 100)}%). ${value.message}`
}

/** Register the stable high-level tool surface; provider-specific operations stay behind the runtime. */
export function apply(ctx: Context): void {
  ctx.tools.register(defineTool({
    name: 'video_workflow_list',
    description: 'List Video Runtime workflows before planning. In automatic mode choose only an available user-selectable workflow whose description matches the user request.',
    parameters: {},
    output: {
      schema: { type: 'array', items: { type: 'object', additionalProperties: true } },
      render: (_args, value) => [{
        type: 'text',
        text: value.map(item => `${item.id}: ${item.available ? 'available' : `unavailable (${item.unavailableReason})`} — ${item.description}`).join('\n'),
      }],
    },
    execute: (_args, exec) => ctx.videoRuntime.listWorkflows(identityOf(exec.agent), exec.signal)
      .then(value => value as unknown as Array<Record<string, import('@cuti-ai/video-runtime').JsonValue>>),
  }))

  ctx.tools.register(defineTool({
    name: 'video_workflow_load',
    description: 'Load the authoritative instructions and execution contract for one selected available video workflow. Call this before constructing VideoSpec. Follow any video_skill_load names in those instructions.',
    parameters: { workflow_id: { type: 'string', required: true } },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{
        type: 'text',
        text: `${String(value.title)}\nMode: ${String(value.mode)}\nPipeline: ${Array.isArray(value.pipeline) ? value.pipeline.join(' -> ') : ''}\n\n${String(value.instructions ?? '')}${bundledResourceText(value)}`,
      }],
    },
    execute: (args, exec) => ctx.videoRuntime.loadWorkflow(args.workflow_id, identityOf(exec.agent), exec.signal)
      .then(value => withoutInjectedResources(value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>)),
  }))

  ctx.tools.register(defineTool({
    name: 'video_skill_load',
    description: "Load one installed Skill's full Markdown instructions after it is selected, named by another Skill, or explicitly requested. Returns bundled resource paths; read those files with video_skill_read_resource when the instructions require them.",
    parameters: { skill_id: { type: 'string', required: true } },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{
        type: 'text',
        text: `${String(value.id)}\n${String(value.instructions ?? '')}${bundledResourceText(value)}`,
      }],
    },
    execute: (args, exec) => ctx.videoRuntime.loadSkill(args.skill_id, identityOf(exec.agent), exec.signal)
      .then(value => withoutInjectedResources(value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>)),
  }))

  ctx.tools.register(defineTool({
    name: 'video_skill_read_resource',
    description: 'Read one bundled text file from an installed Skill when its instructions require that file.',
    parameters: {
      skill_id: { type: 'string', required: true },
      path: { type: 'string', required: true },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: `${value.path}\n\n${value.content}` }],
    },
    execute: (args, exec) => ctx.videoRuntime.loadSkillResource(
      args.skill_id, args.path, identityOf(exec.agent), exec.signal,
    ).then(value => value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>),
  }))

  ctx.tools.register(defineTool({
    name: 'video_project_create',
    description: 'Create a durable empty video project and bind it to the current session.',
    parameters: {
      title: { type: 'string', required: true },
      idempotency_key: { type: 'string', required: true },
    },
    output: { schema: projectOutput, render: (_args, value) => [{ type: 'text', text: projectText(value) }] },
    execute: (args, exec) => ctx.videoRuntime.createProject({
      title: args.title, idempotencyKey: args.idempotency_key,
    }, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_project_plan',
    description: 'Create the first durable phase of a video BuildPlan. Prefer project_intent when later creative decisions depend on generated media; provide exactly one of project_intent or video_spec.',
    parameters: {
      project_id: { type: 'string', required: true },
      base_project_version_id: { type: 'string', required: true },
      idempotency_key: { type: 'string', required: true },
      project_intent: projectIntentParameters,
      video_spec: videoSpecParameters,
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{
        type: 'text',
        text: `Initial video plan ${value.planId}: ${value.shotCount} shots, estimated cost $${value.estimatedCost}.`,
      }],
    },
    execute: (args, exec) => ctx.videoRuntime.planProject({
      projectId: args.project_id,
      baseProjectVersionId: args.base_project_version_id,
      idempotencyKey: args.idempotency_key,
      ...(args.video_spec === undefined ? {} : { videoSpec: args.video_spec }),
      ...(args.project_intent === undefined ? {} : { projectIntent: args.project_intent }),
    }, identityOf(exec.agent), exec.signal).then(
      value => value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>,
    ),
  }))

  ctx.tools.register(defineTool({
    name: 'video_project_build',
    description: 'Start a persisted initial video BuildPlan immediately in fully automatic mode.',
    parameters: {
      project_id: { type: 'string', required: true },
      plan_id: { type: 'string', required: true },
      base_project_version_id: { type: 'string', required: true },
      idempotency_key: { type: 'string', required: true },
    },
    output: { schema: buildOutput, render: (_args, value) => [{ type: 'text', text: buildText(value) }] },
    execute: (args, exec) => ctx.videoRuntime.startBuild({
      projectId: args.project_id,
      planId: args.plan_id,
      baseProjectVersionId: args.base_project_version_id,
      idempotencyKey: args.idempotency_key,
    }, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_project_open',
    description: 'Open an existing video project and return its current immutable version summary.',
    parameters: { project_id: { type: 'string', required: true, description: 'Opaque video project id.' } },
    output: { schema: projectOutput, render: (_args, value) => [{ type: 'text', text: projectText(value) }] },
    execute: (args, exec) => ctx.videoRuntime.openProject(args.project_id, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_project_inspect',
    description: 'Inspect the current video project version, artifact count, active builds, and concise status.',
    parameters: { project_id: { type: 'string', required: true } },
    output: { schema: projectOutput, render: (_args, value) => [{ type: 'text', text: projectText(value) }] },
    execute: (args, exec) => ctx.videoRuntime.inspectProject(args.project_id, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_change_preview',
    description: 'Preview the deterministic impact of a requested project change without modifying the project.',
    parameters: {
      project_id: { type: 'string', required: true },
      change: { type: 'string', required: true, description: 'Concrete requested change.' },
      target_artifact_version_ids: { type: 'array', items: { type: 'string' } },
      idempotency_key: { type: 'string', required: true },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false, properties: {
          planId: { type: 'string', required: true },
          projectId: { type: 'string', required: true },
          baseProjectVersionId: { type: 'string', required: true },
          staleArtifactIds: { type: 'array', required: true, items: { type: 'string' } },
          validationArtifactIds: { type: 'array', required: true, items: { type: 'string' } },
          reusedArtifactIds: { type: 'array', required: true, items: { type: 'string' } },
          rebuildOrder: { type: 'array', required: true, items: { type: 'string' } },
          estimatedCost: { type: 'number', required: true },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: `Rebuild plan ${value.planId}: ${value.staleArtifactIds.length} stale, ${value.validationArtifactIds.length} validate, ${value.reusedArtifactIds.length} reusable; estimated cost ${value.estimatedCost}.`,
      }],
    },
    execute: (args, exec) => ctx.videoRuntime.previewChange({
      projectId: args.project_id,
      change: args.change,
      targetArtifactVersionIds: args.target_artifact_version_ids ?? [],
      idempotencyKey: args.idempotency_key,
    }, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_edit_preview',
    description: 'Resolve structured character, shot, music, lipsync, timeline, or artifact edits into an executable incremental plan. This only previews impact and cost; do not execute until the user confirms.',
    parameters: {
      project_id: { type: 'string', required: true },
      base_project_version_id: { type: 'string', required: true },
      idempotency_key: { type: 'string', required: true },
      description: { type: 'string', required: true },
      edits: {
        type: 'array', required: true, items: {
          type: 'object', additionalProperties: true, properties: {
            type: {
              type: 'string', required: true,
              enum: ['patch_character', 'patch_scene', 'patch_shot', 'regenerate_artifact', 'replace_music', 'generate_lipsync', 'patch_timeline'],
            },
            id: { type: 'string' },
            artifactVersionId: { type: 'string' },
            prompt: { type: 'string' },
            patch: { type: 'object', additionalProperties: true, properties: {} },
          },
        },
      },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{
        type: 'text',
        text: `Edit plan ${String(value.planId)}: ${Array.isArray(value.steps) ? value.steps.filter(step => typeof step === 'object' && step !== null && !Array.isArray(step) && step.action === 'rebuild').length : 0} rebuild steps, estimated cost $${String(value.estimatedCost)}. Await user confirmation before applying.`,
      }],
    },
    execute: (args, exec) => ctx.videoRuntime.previewEdits({
      projectId: args.project_id,
      baseProjectVersionId: args.base_project_version_id,
      idempotencyKey: args.idempotency_key,
      description: args.description,
      edits: args.edits,
    }, identityOf(exec.agent), exec.signal).then(
      value => value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>,
    ),
  }))

  ctx.tools.register(defineTool({
    name: 'video_rebuild_apply',
    description: 'Apply an approved rebuild plan against its exact base project version.',
    parameters: {
      project_id: { type: 'string', required: true },
      plan_id: { type: 'string', required: true },
      base_project_version_id: { type: 'string', required: true },
      idempotency_key: { type: 'string', required: true },
    },
    output: { schema: buildOutput, render: (_args, value) => [{ type: 'text', text: buildText(value) }] },
    execute: (args, exec) => ctx.videoRuntime.applyRebuild({
      projectId: args.project_id,
      planId: args.plan_id,
      baseProjectVersionId: args.base_project_version_id,
      idempotencyKey: args.idempotency_key,
    }, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_build_status',
    description: 'Read the authoritative status of a durable video build.',
    parameters: {
      project_id: { type: 'string', required: true },
      build_id: { type: 'string', required: true },
    },
    output: { schema: buildOutput, render: (_args, value) => [{ type: 'text', text: buildText(value) }] },
    execute: (args, exec) => ctx.videoRuntime.getBuild(args.project_id, args.build_id, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_checkpoint_inspect',
    description: 'Inspect one durable semantic planning checkpoint, including real artifact summaries and the unresolved VideoSpec sections. Use only the project, build, and checkpoint named in the automatic continuation message.',
    parameters: {
      project_id: { type: 'string', required: true },
      build_id: { type: 'string', required: true },
      checkpoint_id: { type: 'string', required: true },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{
        type: 'text',
        text: `Checkpoint ${String(value.id)} after ${String(value.phase)}; plan ${String(value.next_phase)} from ${Array.isArray(value.artifact_summaries) ? value.artifact_summaries.length : 0} real artifacts.`,
      }],
    },
    execute: (args, exec) => ctx.videoRuntime.inspectCheckpoint(
      args.project_id, args.build_id, args.checkpoint_id, identityOf(exec.agent), exec.signal,
    ).then(value => value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>),
  }))

  ctx.tools.register(defineTool({
    name: 'video_checkpoint_resolve',
    description: 'Resolve the current semantic checkpoint with either a complete revised VideoSpec or a structured video_spec_patch. The Runtime merges and validates patches, appends only the next Workflow phase, and rejects stale revisions, workflow/provider switches, and changes to completed steps.',
    parameters: {
      project_id: { type: 'string', required: true },
      build_id: { type: 'string', required: true },
      checkpoint_id: { type: 'string', required: true },
      base_plan_revision: { type: 'integer', required: true },
      base_spec_revision: { type: 'integer', required: true },
      idempotency_key: { type: 'string', required: true },
      video_spec: videoSpecParameters,
      video_spec_patch: {
        type: 'object', additionalProperties: true,
        description: 'Partial VideoSpec fields resolved at this checkpoint. Use instead of video_spec.',
      },
      phase_inputs: { type: 'object', additionalProperties: true },
      proposed_steps: {
        type: 'array', items: { type: 'object', additionalProperties: true },
        description: 'Allowed only for agentic workflows. Deterministic workflow compilers ignore or reject these steps.',
      },
      reason: { type: 'string' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{
        type: 'text',
        text: `Resolved checkpoint; plan ${String(value.planId)} revision ${String(value.planRevision)} now runs phase ${String(value.currentPhase)}.`,
      }],
    },
    execute: (args, exec) => ctx.videoRuntime.resolveCheckpoint({
      projectId: args.project_id,
      buildId: args.build_id,
      checkpointId: args.checkpoint_id,
      basePlanRevision: args.base_plan_revision,
      baseSpecRevision: args.base_spec_revision,
      idempotencyKey: args.idempotency_key,
      ...(args.video_spec === undefined ? {} : { videoSpec: args.video_spec }),
      ...(args.video_spec_patch === undefined ? {} : { videoSpecPatch: args.video_spec_patch }),
      ...(args.phase_inputs === undefined ? {} : { phaseInputs: args.phase_inputs }),
      ...(args.proposed_steps === undefined ? {} : { proposedSteps: args.proposed_steps }),
      ...(args.reason === undefined ? {} : { reason: args.reason }),
    }, identityOf(exec.agent), exec.signal).then(
      value => value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>,
    ),
  }))

  ctx.tools.register(defineTool({
    name: 'video_build_retry_checkpoint',
    description: 'Retry automatic delivery of a failed semantic checkpoint. This does not regenerate media or create a new build.',
    parameters: {
      project_id: { type: 'string', required: true },
      build_id: { type: 'string', required: true },
      checkpoint_id: { type: 'string', required: true },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: `Checkpoint ${String(value.id)} is ${String(value.status)}.` }],
    },
    execute: (args, exec) => ctx.videoRuntime.retryCheckpoint(
      args.project_id, args.build_id, args.checkpoint_id, identityOf(exec.agent), exec.signal,
    ).then(value => value as unknown as Record<string, import('@cuti-ai/video-runtime').JsonValue>),
  }))

  ctx.tools.register(defineTool({
    name: 'video_build_cancel',
    description: 'Request cancellation of a durable video build.',
    parameters: {
      project_id: { type: 'string', required: true },
      build_id: { type: 'string', required: true },
    },
    output: { schema: buildOutput, render: (_args, value) => [{ type: 'text', text: buildText(value) }] },
    execute: (args, exec) => ctx.videoRuntime.cancelBuild(args.project_id, args.build_id, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_artifact_select',
    description: 'Select one artifact version and atomically create a new immutable project version.',
    parameters: {
      project_id: { type: 'string', required: true },
      version_id: { type: 'string', required: true },
      base_project_version_id: { type: 'string', required: true },
      idempotency_key: { type: 'string', required: true },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false, properties: {
          projectId: { type: 'string', required: true },
          artifactId: { type: 'string', required: true },
          versionId: { type: 'string', required: true },
          projectVersionId: { type: 'string', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: `Selected artifact ${value.artifactId} version ${value.versionId}; project is now ${value.projectVersionId}.` }],
    },
    execute: (args, exec) => ctx.videoRuntime.selectArtifact({
      projectId: args.project_id,
      versionId: args.version_id,
      baseProjectVersionId: args.base_project_version_id,
      idempotencyKey: args.idempotency_key,
    }, identityOf(exec.agent), exec.signal),
  }))

  ctx.tools.register(defineTool({
    name: 'video_export',
    description: 'Export one exact immutable project version in the requested format.',
    parameters: {
      project_id: { type: 'string', required: true },
      base_project_version_id: { type: 'string', required: true },
      idempotency_key: { type: 'string', required: true },
      format: { type: 'string', required: true, enum: ['mp4', 'mov', 'webm', 'project'] },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false, properties: {
          exportId: { type: 'string', required: true },
          projectId: { type: 'string', required: true },
          status: { type: 'string', required: true, enum: ['queued', 'running', 'completed', 'failed', 'cancelled'] },
          uri: { type: 'string' },
        },
      },
      render: (_args, value) => [{ type: 'text', text: `Export ${value.exportId}: ${value.status}${value.uri === undefined ? '' : ` at ${value.uri}`}.` }],
    },
    execute: (args, exec) => ctx.videoRuntime.exportProject({
      projectId: args.project_id,
      baseProjectVersionId: args.base_project_version_id,
      idempotencyKey: args.idempotency_key,
      format: args.format,
    }, identityOf(exec.agent), exec.signal),
  }))
}
