from __future__ import annotations

from pathlib import Path

from app.capabilities.models import (
    CapabilityInputs,
    CapabilityManifest,
    CapabilityRegistry,
)
from app.chat.v2.models import AgentRun, Task
from app.chat.v2.skill_catalog import SkillCatalog
from app.orchestration.skills import SkillResolutionRequest, SkillResolver


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
            id="atomic.video.generate",
            description="Generate video",
            executor="atomic.direct",
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
    capabilities: [atomic.video.generate]
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

    video = _resolve(resolver, "atomic.video.generate", activated=run.activated_skills)
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

    assert _resolve(resolver, "atomic.video.generate", activated=run.activated_skills) is None


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
    capabilities: [atomic.video.generate]
  hooks: [before_stage, after_stage]
  workflow:
    mode: product_ad_video
    pipeline: [atomic.video.generate]
    allowed_capabilities: [atomic.video.generate]
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

    video = _resolve(resolver, "atomic.video.generate", activated=run.activated_skills)
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
    context = _resolve(resolver, "atomic.video.generate", activated=run.activated_skills)
    task = Task(
        run_id=run.id,
        revision=1,
        client_key="clip-1",
        capability_id="atomic.video.generate",
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
