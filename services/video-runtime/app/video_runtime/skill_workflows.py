"""Adapt Cuti workflow SKILL.md declarations into Video Runtime workflow plugins."""

from __future__ import annotations

from typing import Any

from app.orchestration.workflow_compiler.registry import (
    WorkflowSpec,
)

from .models import RebuildPlan, VideoSpec
from .plugins import BaseVideoPlugin, PluginContext, VideoPluginRegistry
from .plugins.models import (
    PluginContributions,
    PluginPermissions,
    VideoPluginManifest,
)
from .skills import VideoSkillRuntime
from .workflow_plans import (
    SUPPORTED_WORKFLOW_MODES,
    UNAVAILABLE_WORKFLOW_CAPABILITIES,
    UNAVAILABLE_WORKFLOW_MODES,
    compile_skill_workflow,
)


SKILL_WORKFLOW_PLUGIN_ID = "cuti.skill-workflows"


class SkillWorkflowPlugin(BaseVideoPlugin):
    """Compile installed Workflow Skills through the deterministic build graph."""

    def __init__(self, workflows: dict[str, WorkflowSpec]) -> None:
        self._workflows = dict(workflows)

    def describe_workflows(self) -> list[dict[str, Any]]:
        return [
            {
                "id": spec.skill_name,
                "title": spec.title,
                "mode": spec.mode,
                "parameters": dict(spec.parameters),
                "pipeline": list(spec.pipeline),
                "requiresKeyframe": spec.requires_keyframe,
                "entrypoints": list(spec.entrypoints),
                "skillDependencies": list(spec.skill_dependencies),
                "source": "skill",
                "pluginId": SKILL_WORKFLOW_PLUGIN_ID,
                "available": spec.mode in SUPPORTED_WORKFLOW_MODES,
                "unavailableReason": UNAVAILABLE_WORKFLOW_MODES.get(spec.mode) or (
                    None if spec.mode in SUPPORTED_WORKFLOW_MODES
                    else f"No installed compiler for workflow mode {spec.mode}"
                ),
                "requiredCapabilities": list(spec.pipeline),
                "missingCapabilities": list(
                    UNAVAILABLE_WORKFLOW_CAPABILITIES.get(spec.mode, [])
                ),
                "userSelectable": True,
                "executionKind": "adapter",
            }
            for spec in self._workflows.values()
        ]

    async def compile_build_plan(
        self,
        context: PluginContext,
        video_spec: VideoSpec,
    ) -> RebuildPlan:
        workflow = self._workflows.get(video_spec.workflow_id)
        if workflow is None:
            raise LookupError(f"workflow Skill is not installed: {video_spec.workflow_id}")
        if not context.project_id:
            raise ValueError("workflow Skill requires a project")
        return compile_skill_workflow(workflow, context, video_spec)

async def load_workflow_skills(
    plugins: VideoPluginRegistry,
    skills: VideoSkillRuntime,
) -> int:
    """Register the workflows already validated by the process-owned Skill Runtime."""
    workflows = skills.workflows.items()
    if not workflows:
        return 0
    manifest = VideoPluginManifest(
        id=SKILL_WORKFLOW_PLUGIN_ID,
        version="1.0.0",
        runtime_entrypoint=(
            "app.video_runtime.skill_workflows:SkillWorkflowPlugin"
        ),
        trusted=True,
        contributions=PluginContributions(workflows=sorted(workflows)),
        skills=sorted(workflows),
        permissions=PluginPermissions(sandbox=False),
    )
    await plugins.register(
        manifest,
        SkillWorkflowPlugin(workflows),
        source_path=skills.roots[0],
    )
    return len(workflows)


async def reload_workflow_skills(
    plugins: VideoPluginRegistry,
    skills: VideoSkillRuntime,
) -> int:
    """Reload the unified catalog and replace its generated workflow plugin."""
    skills.reload()
    try:
        plugins.get(SKILL_WORKFLOW_PLUGIN_ID)
    except LookupError:
        pass
    else:
        await plugins.unload(SKILL_WORKFLOW_PLUGIN_ID)
    return await load_workflow_skills(plugins, skills)
