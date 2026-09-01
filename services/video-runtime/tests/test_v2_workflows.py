"""Unit tests for V2 workflow skills gating and mode injection."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.harness import DynamicHarness
from app.chat.v2.models import (
    AgentRun,
    InterruptionRequest,
    PlanPatch,
    PlannedTask,
    RunSnapshot,
)
from app.chat.v2.plan_validator import PlanValidator
from app.chat.v2.skill_catalog import SkillCatalog
from app.chat.v2.workflows import (
    WORKFLOWS,
    active_workflow,
    capability_requires_workflow,
    configure_workflows,
    inject_workflow_parameters,
    is_workflow_skill,
    parse_explicit_skill_names,
)


def _project_skill_catalog() -> SkillCatalog:
    root = Path(__file__).resolve().parents[1]
    catalog = SkillCatalog([
        root / "skills" / "system",
        root / "skills" / "builtin",
        root / "skills" / "external",
    ])
    catalog.discover()
    return catalog


def test_workflow_registry_covers_user_facing_pipelines():
    catalog = _project_skill_catalog()
    assert is_workflow_skill("workflow-keyframe-pipeline")
    assert is_workflow_skill("workflow-short-drama")
    assert is_workflow_skill("workflow-direct-video")
    assert is_workflow_skill("mv")
    if catalog.has("open-montage"):
        assert is_workflow_skill("open-montage")
    if catalog.has("product-ad-video"):
        assert is_workflow_skill("product-ad-video")
    assert not is_workflow_skill("generate-outline")
    assert WORKFLOWS["workflow-short-drama"].requires_keyframe is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$product-ad-video", ["product-ad-video"]),
        ("$product-ad-video 这个skill", ["product-ad-video"]),
        ("$product-ad-video，为产品生成广告", ["product-ad-video"]),
        ("使用$product-ad-video这个skill", ["product-ad-video"]),
        ("使用/product-ad-video生成广告", ["product-ad-video"]),
    ],
)
def test_parse_explicit_skill_names_accepts_unicode_boundaries(text, expected):
    assert parse_explicit_skill_names(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "prefix$product-ad-video",
        "$product-ad-video_suffix",
        "$product-ad-video-extra-name-that-exceeds-the-sixty-four-character-limit-123456789",
    ],
)
def test_parse_explicit_skill_names_rejects_partial_ascii_tokens(text):
    assert parse_explicit_skill_names(text) == []


def test_runware_product_ad_workflow_is_installed_and_executable_by_cuti():
    catalog = _project_skill_catalog()
    if not catalog.has("product-ad-video"):
        pytest.skip("product-ad-video Skill is not installed in this checkout")
    metadata = next(
        item for item in catalog.list_metadata()
        if item.name == "product-ad-video"
    )
    loaded = catalog.load(metadata.name)
    registry = CapabilityRegistry()
    configure_workflows(catalog, registry)

    spec = active_workflow(["product-ad-video"])
    assert metadata.enabled is True
    assert metadata.metadata["kind"] == "workflow"
    assert "Runware workflow" in loaded.instructions
    assert "references/examples.md" in catalog.list_resources(metadata.name)
    assert spec is not None
    assert spec.pipeline == (
        "atomic.text.generate",
        "atomic.image.generate",
        "atomic.video.generate",
        "media.concat",
    )
    assert spec.parameters["workflow_mode"] == "product_ad_video"
    assert spec.parameters["shot_workflow_mode"] == "product_reference_i2v"


def test_inject_workflow_parameters_sets_mode_defaults():
    spec = WORKFLOWS["workflow-short-drama"]
    merged = inject_workflow_parameters({"thread_id": "t1"}, spec)
    assert merged["thread_id"] == "t1"
    assert merged["workflow_mode"] == "short_drama"
    assert merged["shot_workflow_mode"] == "reference_t2v"
    assert merged["content_category"] == "short_drama"
    assert merged["activated_workflow"] == "workflow-short-drama"


def test_mv_workflow_pipeline_and_mode():
    assert is_workflow_skill("mv")
    spec = WORKFLOWS["mv"]
    assert spec.mode == "mv"
    assert spec.pipeline == (
        "research.generate",
        "suno.generate",
        "media.audio_analyze",
        "media.audio_cut",
        "atomic.image.generate",
        "api.provider.generate",
        "media.concat",
        "media.mix_audio",
        "media.transcribe",
        "media.hyperframes_caption",
    )
    assert spec.requires_keyframe is False
    merged = inject_workflow_parameters({}, spec)
    assert merged["workflow_mode"] == "mv"
    assert merged["activated_workflow"] == "mv"


def test_postproduction_capabilities_are_workflow_free():
    assert not capability_requires_workflow("media.concat")
    assert not capability_requires_workflow("media.extract_frame")
    assert not capability_requires_workflow("media.transcribe")
    assert not capability_requires_workflow("subtitle.compose")
    assert not capability_requires_workflow("media.subtitle_burn")
    assert not capability_requires_workflow("media.audio_trim")
    assert not capability_requires_workflow("media.audio_analyze")
    assert not capability_requires_workflow("media.audio_cut")
    assert not capability_requires_workflow("media.mix_audio")
    assert capability_requires_workflow("api.provider.generate")


def test_new_pipeline_is_injected_by_adding_only_skill_markdown(tmp_path):
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "workflow-music-video"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: workflow-music-video
description: Music-first MV workflow.
metadata:
  kind: workflow
  version: "1.0.0"
  workflow:
    title: Music Video
    mode: music_video
    entrypoints: [text, image, audio]
    parameters:
      content_category: music_video
    pipeline:
      - atomic.music.generate
      - atomic.image.generate
      - atomic.video.generate
      - media.concat
    requires_keyframe: false
    allowed_capabilities:
      - actions.suggest
      - atomic.music.generate
      - atomic.image.generate
      - atomic.video.generate
      - media.concat
---

# Music Video Workflow

Use music as the default time structure, but let the user start from images or
replace music later. Preserve unrelated artifacts when the music changes.
""",
        encoding="utf-8",
    )
    catalog = SkillCatalog([skill_root])
    catalog.discover()
    try:
        assert configure_workflows(catalog, CapabilityRegistry()) == 1
        assert is_workflow_skill("workflow-music-video")
        spec = active_workflow(["workflow-music-video"])
        assert spec is not None
        assert spec.mode == "music_video"
        assert spec.pipeline[0] == "atomic.music.generate"
        assert inject_workflow_parameters({}, spec) == {
            "content_category": "music_video",
            "workflow_mode": "music_video",
            "activated_workflow": "workflow-music-video",
        }
    finally:
        configure_workflows(_project_skill_catalog(), CapabilityRegistry())


def test_dynamic_workflow_rejects_unknown_capability(tmp_path):
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "workflow-invalid"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: workflow-invalid
description: Invalid workflow fixture.
metadata:
  kind: workflow
  workflow:
    title: Invalid
    mode: invalid
    pipeline: [missing.tool]
    allowed_capabilities: [missing.tool]
---
# Invalid
""",
        encoding="utf-8",
    )
    catalog = SkillCatalog([skill_root])
    catalog.discover()
    with pytest.raises(ValueError, match="unknown capability missing.tool"):
        configure_workflows(catalog, CapabilityRegistry())
    configure_workflows(_project_skill_catalog(), CapabilityRegistry())


def test_plan_validator_blocks_stage_tasks_without_workflow():
    registry = CapabilityRegistry()
    validator = PlanValidator(
        registry,
        max_revisions=20,
        max_tasks=50,
        max_parallel_generation_tasks=3,
    )
    run = AgentRun(
        thread_id="t",
        project_id="t",
        user_id="u",
        objective="make a video",
        idempotency_key="k1",
        activated_skills=[],
    )
    snapshot = RunSnapshot(run=run, tasks=[], artifacts=[], messages=[])
    patch = PlanPatch(
        base_revision=0,
        reason="start",
        add_tasks=[
            PlannedTask(
                capability_id="outline.generate",
                objective="outline",
                parameters={"thread_id": "t"},
            )
        ],
    )
    with pytest.raises(ValueError, match="no confirmed workflow"):
        validator.validate(snapshot, patch)


def test_plan_validator_allows_stage_tasks_with_workflow():
    registry = CapabilityRegistry()
    # outline.generate may be disabled in default registry depending on skill load;
    # register a minimal enabled stand-in if missing/disabled.
    from app.chat.v2.capabilities import CapabilityInputs, CapabilityManifest

    if "outline.generate" not in {c.id for c in registry.list(include_disabled=True)}:
        registry.register(
            CapabilityManifest(
                id="outline.generate",
                description="outline",
                executor="local.service",
                service_target="generate_outline_by_request",
                inputs=CapabilityInputs(),
                output_type="outline",
                enabled=True,
            )
        )
    else:
        cap = registry.get("outline.generate", require_enabled=False)
        cap.enabled = True
        registry.register(cap, overwrite=True)

    validator = PlanValidator(
        registry,
        max_revisions=20,
        max_tasks=50,
        max_parallel_generation_tasks=3,
    )
    run = AgentRun(
        thread_id="t",
        project_id="t",
        user_id="u",
        objective="make a video",
        idempotency_key="k2",
        activated_skills=["workflow-keyframe-pipeline"],
    )
    snapshot = RunSnapshot(run=run, tasks=[], artifacts=[], messages=[])
    patch = PlanPatch(
        base_revision=0,
        reason="start",
        add_tasks=[
            PlannedTask(
                capability_id="outline.generate",
                objective="outline",
                parameters={"thread_id": "t"},
            )
        ],
    )
    validator.validate(snapshot, patch)


def test_short_drama_rejects_keyframe_generate():
    registry = CapabilityRegistry()
    from app.chat.v2.capabilities import CapabilityInputs, CapabilityManifest

    registry.register(
        CapabilityManifest(
            id="keyframe.generate",
            description="keyframes",
            executor="local.service",
            service_target="generate_keyframes_by_request",
            inputs=CapabilityInputs(),
            output_type="keyframe",
            enabled=True,
        ),
        overwrite=True,
    )
    validator = PlanValidator(
        registry, max_revisions=20, max_tasks=50, max_parallel_generation_tasks=3
    )
    run = AgentRun(
        thread_id="t",
        project_id="t",
        user_id="u",
        objective="short drama video",
        idempotency_key="k3",
        activated_skills=["workflow-short-drama"],
    )
    snapshot = RunSnapshot(run=run, tasks=[], artifacts=[], messages=[])
    patch = PlanPatch(
        base_revision=0,
        reason="bad",
        add_tasks=[
            PlannedTask(
                capability_id="keyframe.generate",
                objective="kf",
                parameters={"thread_id": "t"},
            )
        ],
    )
    with pytest.raises(ValueError, match="skips keyframes"):
        validator.validate(snapshot, patch)


def test_libtv_requires_direct_seedance2_multireference_generation():
    catalog = _project_skill_catalog()
    if not catalog.has("libtv-product-workflow"):
        pytest.skip("libtv-product-workflow Skill is not installed in this checkout")
    registry = CapabilityRegistry()
    validator = PlanValidator(
        registry, max_revisions=20, max_tasks=50, max_parallel_generation_tasks=3
    )
    run = AgentRun(
        thread_id="t", project_id="t", user_id="u",
        objective="make a 60 second product video",
        idempotency_key="libtv-r2v",
        activated_skills=["libtv-product-workflow"],
    )
    snapshot = RunSnapshot(run=run, tasks=[], artifacts=[], messages=[])

    with pytest.raises(ValueError, match="not allowed|storyboard/keyframe and I2V"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0,
            add_tasks=[PlannedTask(
                capability_id="atomic.video.generate", objective="legacy i2v",
                parameters={"duration_seconds": 8},
            )],
        ))

    with pytest.raises(ValueError, match="mode=reference_to_video"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0,
            add_tasks=[PlannedTask(
                capability_id="api.provider.generate", objective="bad i2v",
                parameters={
                    "prompt": "bad", "model": "doubao-seedance-2-0-260128",
                    "mode": "i2v", "images": ["https://example.com/a.webp"],
                    "reference_roles": ["product_identity"],
                },
            )],
        ))

    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            capability_id="api.provider.generate", objective="direct multiref",
            parameters={
                "prompt": "@图片1约束产品外观，镜头环绕展示。",
                "model": "doubao-seedance-2-0-260128",
                "mode": "reference_to_video",
                "images": ["https://example.com/a.webp", "https://example.com/b.webp"],
                "reference_roles": ["product_identity", "detail"],
                "duration": 8,
            },
        )],
    ))


def test_workflow_confirm_interruption_ok():
    registry = CapabilityRegistry()
    validator = PlanValidator(
        registry, max_revisions=20, max_tasks=50, max_parallel_generation_tasks=3
    )
    run = AgentRun(
        thread_id="t",
        project_id="t",
        user_id="u",
        objective="idea",
        idempotency_key="k4",
    )
    snapshot = RunSnapshot(run=run, tasks=[], artifacts=[], messages=[])
    patch = PlanPatch(
        base_revision=0,
        reason="ask",
        waiting_for_input=True,
        response="Recommend keyframe pipeline",
        interruption=InterruptionRequest(
            category="workflow_confirm",
            requires_confirmation=True,
            what_happened="Matched classic directed production",
            why_interrupted="Need user confirmation before stages",
            what_next="Reply `$workflow-keyframe-pipeline` or 确认",
            skill_name="workflow-keyframe-pipeline",
        ),
    )
    validator.validate(snapshot, patch)


def test_merge_activated_workflows_latest_wins():
    merged = DynamicHarness._merge_activated_workflows(
        ["workflow-keyframe-pipeline"],
        "switch to $workflow-short-drama please",
    )
    assert merged[0] == "workflow-short-drama"
    assert active_workflow(merged).skill_name == "workflow-short-drama"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply", ["继续生成", "继续", "恢复并继续", "Continue generation"]
)
async def test_workflow_confirmation_accepts_resume_button_replies(reply):
    class EventRepo:
        async def get_events(self, run_id, after=0):
            from app.chat.v2.models import DomainEvent

            return [DomainEvent(
                run_id=run_id,
                sequence=1,
                type="run.waiting_input",
                payload={"interruption": {
                    "category": "workflow_confirm",
                    "skill_name": "workflow-short-drama",
                }},
            )]

    harness = object.__new__(DynamicHarness)
    harness.repo = EventRepo()
    confirmed = await harness._confirmed_workflow_from_reply("run-1", reply)
    assert confirmed == "workflow-short-drama"


@pytest.mark.asyncio
async def test_workflow_confirmation_does_not_reuse_stale_confirmation():
    class EventRepo:
        async def get_events(self, run_id, after=0):
            from app.chat.v2.models import DomainEvent

            return [
                DomainEvent(
                    run_id=run_id, sequence=1, type="run.waiting_input",
                    payload={"interruption": {
                        "category": "workflow_confirm",
                        "skill_name": "workflow-short-drama",
                    }},
                ),
                DomainEvent(
                    run_id=run_id, sequence=2, type="run.waiting_input",
                    payload={"interruption": {"category": "blocked"}},
                ),
            ]

    harness = object.__new__(DynamicHarness)
    harness.repo = EventRepo()
    confirmed = await harness._confirmed_workflow_from_reply(
        "run-1", "继续生成"
    )
    assert confirmed is None
