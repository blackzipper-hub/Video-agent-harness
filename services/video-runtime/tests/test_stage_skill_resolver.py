from __future__ import annotations

from pathlib import Path

from app.capabilities.models import (
    CapabilityInputs,
    CapabilityManifest,
    CapabilityRegistry,
)
from app.chat.v2.models import AgentRun, PlannedTask, Task
from app.chat.v2.models import PlanPatch
from app.chat.v2.repository import InMemoryV2Repository
from app.chat.v2.skill_catalog import SkillCatalog
from app.orchestration.skills import SkillResolutionRequest, SkillResolver
from app.orchestration.task_runtime.harness import DynamicHarness


def _write_skill(
    root: Path,
    name: str,
    *,
    metadata: str,
    instructions: str,
) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"""---
name: {name}
description: Test Skill {name}.
metadata:
{metadata}
---

{instructions}
""",
        encoding="utf-8",
    )


def _capabilities() -> CapabilityRegistry:
    return CapabilityRegistry([
        CapabilityManifest(
            id="video.generate",
            description="Generate video",
            executor="video-agent.delegate",
            target_agent="video_gen",
            inputs=CapabilityInputs(),
            output_type="video",
        ),
        CapabilityManifest(
            id="subtitle.compose",
            description="Compose subtitles",
            executor="local.service",
            service_target="subtitle_compose",
            inputs=CapabilityInputs(),
            output_type="subtitle",
        ),
    ])


def _resolve(
    resolver: SkillResolver,
    capability_id: str,
    *,
    activated: list[str],
):
    capability = resolver.capabilities.get(capability_id, require_enabled=False)
    return resolver.resolve(SkillResolutionRequest(
        activated_skill_ids=activated,
        capability_id=capability.id,
        output_type=capability.output_type,
        capability_skill_id=capability.skill_name,
    ))


def test_resolver_injects_only_matching_stage_skill(tmp_path):
    _write_skill(
        tmp_path,
        "cinematic-style",
        metadata="""  version: "1.2.0"
  roles: [guidance]
  scope:
    type: stage
  selectors:
    capabilities: [video.generate]
  hooks: [before_stage]
""",
        instructions="Use restrained cinematic lighting.",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.discover()
    resolver = SkillResolver(catalog, _capabilities())
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Make a film",
        idempotency_key="run-1",
        activated_skills=["cinematic-style"],
    )

    video = _resolve(resolver, "video.generate", activated=run.activated_skills)
    subtitle = _resolve(resolver, "subtitle.compose", activated=run.activated_skills)

    assert video is not None
    assert [item.skill_id for item in video.applied_skills] == ["cinematic-style"]
    assert video.applied_skills[0].version == "1.2.0"
    assert len(video.applied_skills[0].content_hash) == 64
    assert "restrained cinematic lighting" in video.instructions
    assert subtitle is None


def test_workflow_only_skill_is_not_copied_into_stage(tmp_path):
    _write_skill(
        tmp_path,
        "planning-workflow",
        metadata="""  kind: workflow
  version: "1.0.0"
""",
        instructions="Plan every production stage.",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.discover()
    resolver = SkillResolver(catalog, _capabilities())
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Make a film",
        idempotency_key="run-1",
        activated_skills=["planning-workflow"],
    )

    assert _resolve(resolver, "video.generate", activated=run.activated_skills) is None


def test_workflow_stage_supervisor_is_injected_only_into_selected_atomic_stages(tmp_path):
    _write_skill(
        tmp_path,
        "product-ad-video",
        metadata="""  kind: workflow
  version: "1.0.0"
  roles: [workflow, stage_supervisor]
  scope:
    type: stage
  selectors:
    capabilities: [video.generate]
  hooks: [before_stage, after_stage]
  workflow:
    mode: product_ad_video
    pipeline: [video.generate]
    allowed_capabilities: [video.generate]
""",
        instructions="Preserve product packaging, label, color, and material.",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.discover()
    resolver = SkillResolver(catalog, _capabilities())
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Make a product commercial",
        idempotency_key="run-1",
        activated_skills=["product-ad-video"],
    )

    video = _resolve(resolver, "video.generate", activated=run.activated_skills)
    subtitle = _resolve(resolver, "subtitle.compose", activated=run.activated_skills)

    assert video is not None
    assert video.applied_skills[0].roles == ["workflow", "stage_supervisor"]
    assert video.applied_skills[0].hooks == ["before_stage", "after_stage"]
    assert "Preserve product packaging" in video.instructions
    assert subtitle is None


def test_task_preserves_frozen_skill_context_across_serialization(tmp_path):
    _write_skill(
        tmp_path,
        "locked-style",
        metadata="""  roles: [guidance]
  scope: run
""",
        instructions="Keep the protagonist visually consistent.",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.discover()
    resolver = SkillResolver(catalog, _capabilities())
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Make a film",
        idempotency_key="run-1",
        activated_skills=["locked-style"],
    )
    context = _resolve(resolver, "video.generate", activated=run.activated_skills)
    task = Task(
        run_id=run.id,
        revision=1,
        client_key="clip-1",
        capability_id="video.generate",
        objective="Generate clip",
        resolved_skills=list(context.applied_skills),
        skill_context=context,
    )

    restored = Task.model_validate(task.model_dump(mode="json"))

    assert restored.resolved_skills == task.resolved_skills
    assert restored.skill_context == task.skill_context
    assert "Authoritative instructions from Skills" in (
        restored.objective_with_skill_context()
    )
    assert "visually consistent" in restored.objective_with_skill_context()


async def test_repository_copies_resolved_skills_from_plan_to_durable_task(tmp_path):
    _write_skill(
        tmp_path,
        "stage-style",
        metadata="""  roles: [guidance]
  scope: run
""",
        instructions="Use a stable visual language.",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.discover()
    resolver = SkillResolver(catalog, _capabilities())
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Make a film",
        idempotency_key="run-1",
        activated_skills=["stage-style"],
    )
    planned = PlannedTask(
        capability_id="video.generate",
        objective="Generate clip",
        client_key="clip-1",
    )
    planned.skill_context = _resolve(
        resolver,
        planned.capability_id,
        activated=run.activated_skills,
    )
    planned.resolved_skills = list(planned.skill_context.applied_skills)
    repository = InMemoryV2Repository()
    await repository.create_run(run)

    created = await repository.apply_patch(
        run,
        PlanPatch(base_revision=0, add_tasks=[planned]),
    )
    snapshot = await repository.snapshot(run.id)

    assert created[0].resolved_skills[0].skill_id == "stage-style"
    assert snapshot.tasks[0].skill_context == planned.skill_context


def test_explicit_helper_skill_is_persisted_for_later_stages(tmp_path):
    _write_skill(
        tmp_path,
        "stage-style",
        metadata="""  roles: [guidance]
  scope: run
""",
        instructions="Use a stable visual language.",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.discover()
    harness = DynamicHarness.__new__(DynamicHarness)
    harness.skill_resolver = SkillResolver(catalog, _capabilities())

    activated = harness._merge_activated_skills([], "Use $stage-style for this film")
    still_active = harness._merge_activated_skills(activated, "Continue generation")

    assert activated == ["stage-style"]
    assert still_active == ["stage-style"]
