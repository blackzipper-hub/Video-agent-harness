import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.context import ContextAssembler
from app.chat.v2.executors import CapabilityExecutor
from app.chat.v2.models import (
    AgentRun, ArtifactVersion, ChatMessage, DomainEvent, InputFile, PlanPatch, PlannedTask,
    RunSnapshot, Task, TaskStatus,
)
from app.chat.v2.plan_validator import PlanValidator
from app.chat.v2.repository import InMemoryV2Repository
from app.chat.v2.result_reconciler import V2ResultReconciler, payload_media_uri


def test_scenario_product_workflow_requires_script_derived_setting_references():
    text = SimpleNamespace(type="text", metadata={})
    character = SimpleNamespace(
        type="image", metadata={"artifact_role": "character_setting_reference"},
    )
    scene = SimpleNamespace(
        type="image", metadata={"artifact_role": "scene_setting_reference"},
    )
    product = SimpleNamespace(
        type="image", metadata={"artifact_role": "product_setting_reference"},
    )

    for role in (
        "character_setting_reference",
        "scene_setting_reference",
        "product_setting_reference",
    ):
        PlanValidator._validate_scenario_product_reference_task(
            "atomic.image.generate",
            {"artifact_role": role, "model": "gpt-image-2"},
            [text],
        )
    PlanValidator._validate_scenario_product_reference_task(
        "api.provider.generate",
        {
            "images": ["https://example.test/product.png"],
            "ad_format": "product_commercial",
            "primary_selling_point": "嘈杂环境中依然清晰聆听",
            "segment_proof": "戴上耳机后环境噪声降低，人物准确听清节拍并带动排练同步",
            "prompt": (
                "任务类型：产品宣传片\n"
                "核心产品卖点：嘈杂环境中依然清晰聆听\n"
                "本片段卖点证明：戴上耳机后环境噪声降低，人物准确听清节拍并带动排练同步"
            ),
        },
        [character, scene, product],
    )

    with pytest.raises(ValueError, match="completed script artifact"):
        PlanValidator._validate_scenario_product_reference_task(
            "atomic.image.generate",
            {"artifact_role": "character_setting_reference", "model": "gpt-image-2"},
            [],
        )


def test_scenario_product_workflow_rejects_video_prompt_without_ad_context():
    character = SimpleNamespace(
        type="image", metadata={"artifact_role": "character_setting_reference"},
    )
    scene = SimpleNamespace(
        type="image", metadata={"artifact_role": "scene_setting_reference"},
    )
    product = SimpleNamespace(
        type="image", metadata={"artifact_role": "product_setting_reference"},
    )
    references = [character, scene, product]
    base = {
        "images": ["https://example.test/product.png"],
        "ad_format": "product_commercial",
        "primary_selling_point": "嘈杂环境中依然清晰聆听",
        "segment_proof": "戴上耳机后人物听清节拍",
    }

    with pytest.raises(ValueError, match="explicitly identify"):
        PlanValidator._validate_scenario_product_reference_task(
            "api.provider.generate",
            {**base, "prompt": "人物在排练室戴上耳机。"},
            references,
        )

    with pytest.raises(ValueError, match="segment_proof"):
        PlanValidator._validate_scenario_product_reference_task(
            "api.provider.generate",
            {
                **base,
                "prompt": "产品宣传片。核心产品卖点：嘈杂环境中依然清晰聆听。",
            },
            references,
        )
    with pytest.raises(ValueError, match="scene_setting_reference"):
        PlanValidator._validate_scenario_product_reference_task(
            "api.provider.generate", base, [character, product],
        )


def test_atomic_image_capability_accepts_text_as_durable_context():
    from app.capabilities.manifests.platform import platform_capabilities

    manifest = next(
        item for item in platform_capabilities()
        if item.id == "atomic.image.generate"
    )
    assert "text" in manifest.inputs.optional


def test_text_first_outline_builds_synthetic_analysis_without_analysis_artifact():
    from app.models.tool_enums import ContentCategory
    from app.models.user_options import UserOption
    from app.models.video_state import UserInput
    from app.services.agent.video.outline_generation_service import (
        _analysis_from_user_request,
    )

    request = UserInput(
        user_input="创建一个现代都市双女主短剧",
        user_option=UserOption(duration=60, content_category=ContentCategory.SHORT_DRAMA),
    )

    analysis = _analysis_from_user_request(request)

    assert analysis.video_type == "short_drama"
    assert analysis.duration == 60
    assert analysis.content_category == ContentCategory.SHORT_DRAMA
    assert analysis.key_elements == [request.user_input]
    assert analysis.extra["source"] == "text_first_outline"


@pytest.mark.asyncio
async def test_repository_keeps_one_run_per_thread_and_full_message_history():
    repository = InMemoryV2Repository()
    run = AgentRun(
        thread_id="thread-1", project_id="thread-1", user_id="user-1",
        objective="first", idempotency_key="request-1",
    )
    stored, created = await repository.create_run(run)
    assert created
    for index in range(35):
        await repository.add_chat_message(ChatMessage(
            run_id=stored.id, role="user", content=f"message {index}",
        ))
    assert (await repository.get_run_by_thread("user-1", "thread-1")).id == stored.id
    messages = await repository.list_chat_messages(stored.id)
    assert len(messages) == 35
    assert [item.sequence for item in messages] == list(range(1, 36))
    await repository.append_event(DomainEvent(run_id=stored.id, type="test.first"))
    await repository.append_event(DomainEvent(run_id=stored.id, type="test.second"))
    assert (await repository.snapshot(stored.id)).last_event_sequence == 2


def test_context_assembler_bounds_prompt_but_preserves_durable_coverage():
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="goal", idempotency_key="request-1",
        user_option={"aspect_ratio": "16:9"},
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[
            ChatMessage(run_id=run.id, role="user", content=f"message {index}", sequence=index)
            for index in range(1, 41)
        ],
    )
    assembler = ContextAssembler(recent_message_limit=3)
    context = assembler.assemble(snapshot)
    assert "message 38" in context
    assert "message 1" not in context
    assert "parameters.thread_id" in context
    assert "thread-1" in context
    summary, sequence = assembler.update_durable_summary(snapshot)
    assert "message 1" in summary
    assert "message 40" in summary
    assert sequence == 40


def test_context_summary_retains_foundation_and_latest_turns():
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="goal", idempotency_key="request-1",
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[
            ChatMessage(
                run_id=run.id,
                role="user",
                content="FOUNDATION CONSTRAINT" if index == 1 else f"message {index}",
                sequence=index,
            )
            for index in range(1, 121)
        ],
    )
    summary, sequence = ContextAssembler.update_durable_summary(snapshot)
    assert "FOUNDATION CONSTRAINT" in summary
    assert "message 120" in summary
    assert "intermediate messages are omitted" in summary
    assert sequence == 120


def test_task_dedupe_includes_normalized_parameters_so_styles_coexist():
    capabilities = CapabilityRegistry()
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="images", idempotency_key="request-1",
        activated_skills=["workflow-direct-video"],
    )
    snapshot = RunSnapshot(run=run)
    validator = PlanValidator(capabilities, max_revisions=10, max_tasks=10)
    patch = PlanPatch(
        base_revision=0,
        add_tasks=[
            PlannedTask(
                capability_id="atomic.image.generate", objective="portrait",
                parameters={"prompt": "portrait", "style": "watercolor"},
            ),
            PlannedTask(
                capability_id="atomic.image.generate", objective="portrait",
                parameters={"prompt": "portrait", "style": "photorealistic"},
            ),
        ],
    )
    validator.validate(snapshot, patch)

    duplicate = patch.model_copy(update={
        "add_tasks": [
            patch.add_tasks[0],
            patch.add_tasks[0].model_copy(update={"client_key": "other"}),
        ]
    })
    with pytest.raises(ValueError, match="duplicate active task"):
        validator.validate(snapshot, duplicate)


@pytest.mark.asyncio
async def test_repository_namespaces_workflow_task_ids_per_run():
    repository = InMemoryV2Repository()
    runs = []
    for index in range(2):
        run, _ = await repository.create_run(AgentRun(
            thread_id=f"thread-{index}", project_id=f"project-{index}",
            user_id="user-1", objective="short drama",
            idempotency_key=f"request-{index}",
        ))
        first = await repository.apply_patch(run, PlanPatch(
            base_revision=0,
            add_tasks=[PlannedTask(
                client_key="short-drama-outline-v1",
                capability_id="outline.generate", objective="outline",
            )],
        ))
        first[0].status = TaskStatus.SUCCEEDED
        await repository.save_task(first[0])
        run = (await repository.snapshot(run.id)).run
        second = await repository.apply_patch(run, PlanPatch(
            base_revision=1,
            add_tasks=[PlannedTask(
                client_key="short-drama-character-v1",
                capability_id="character.generate", objective="characters",
                depends_on=["short-drama-outline-v1"],
            )],
        ))
        assert second[0].depends_on == [first[0].id]
        runs.append((first[0], second[0]))

    assert runs[0][0].id != runs[1][0].id
    assert runs[0][0].client_key == runs[1][0].client_key


def test_broad_video_idea_requires_workflow_confirmation_first():
    validator = PlanValidator(CapabilityRegistry(), max_revisions=10, max_tasks=10)
    snapshot = RunSnapshot(run=AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="我想做一个关于未来城市的视频。", idempotency_key="request-1",
    ))
    with pytest.raises(ValueError, match="no confirmed workflow skill"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0,
            add_tasks=[PlannedTask(
                capability_id="video.pipeline.generate", objective="生成未来城市视频",
            )],
        ))
    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            capability_id="actions.suggest",
            objective="choice\n电影预告 | 制作电影预告风格\n直接生成 | 直接生成",
        )],
    ))


def test_single_media_request_cannot_expand_to_unrequested_media():
    validator = PlanValidator(CapabilityRegistry(), max_revisions=10, max_tasks=10)
    snapshot = RunSnapshot(run=AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="Write a short story about a robot on a rainy night.",
        idempotency_key="request-1",
    ))
    with pytest.raises(ValueError, match="exceeds the latest user request scope"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0,
            add_tasks=[PlannedTask(
                capability_id="image.generate", objective="Illustrate the robot story",
            )],
        ))


@pytest.mark.parametrize("reply", ["继续生成", "确认", "恢复并继续"])
def test_chinese_confirmation_keeps_original_image_scope(reply):
    from app.chat.v2.capabilities import CapabilityInputs, CapabilityManifest

    capabilities = CapabilityRegistry([CapabilityManifest(
        id="atomic.image.generate",
        description="image",
        executor="local.service",
        service_target="image",
        inputs=CapabilityInputs(optional=["text", "image"]),
        output_type="image",
    )])
    validator = PlanValidator(capabilities, max_revisions=10, max_tasks=10)
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="生成一张女主图", idempotency_key="request-1",
        activated_skills=["workflow-short-drama"],
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[
            ChatMessage(run_id=run.id, role="user", content="生成一张女主图"),
            ChatMessage(run_id=run.id, role="user", content=reply),
        ],
    )
    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            capability_id="atomic.image.generate", objective="女主角色图",
        )],
    ))


def test_product_commercial_continuation_keeps_original_video_scope():
    from app.chat.v2.capabilities import CapabilityInputs, CapabilityManifest

    capabilities = CapabilityRegistry([CapabilityManifest(
        id="atomic.video.generate",
        description="video",
        executor="local.service",
        service_target="video",
        inputs=CapabilityInputs(optional=["text", "image"]),
        output_type="video",
    )])
    validator = PlanValidator(capabilities, max_revisions=10, max_tasks=10)
    run = AgentRun(
        thread_id="thread-product", project_id="project-product", user_id="user-1",
        objective="继续生成", idempotency_key="request-product",
        activated_skills=["workflow-short-drama"],
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[
            ChatMessage(
                run_id=run.id,
                role="user",
                content=(
                    "使用我上传的耳机参考图制作30秒商业宣传片，"
                    "由两个完整的15秒Seedance 2.0片段组成。"
                ),
            ),
            ChatMessage(run_id=run.id, role="user", content="继续生成"),
            ChatMessage(run_id=run.id, role="user", content="继续生成"),
        ],
    )

    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            capability_id="atomic.video.generate",
            objective="生成第一个完整的15秒产品宣传片段",
        )],
    ))


def test_video_composition_allows_image_dependency_but_music_is_opt_in():
    from app.chat.v2.capabilities import CapabilityInputs, CapabilityManifest

    capabilities = CapabilityRegistry([
        CapabilityManifest(
            id="atomic.image.generate", description="image", executor="local.service",
            service_target="image", inputs=CapabilityInputs(optional=["text", "image"]),
            output_type="image",
        ),
        CapabilityManifest(
            id="atomic.music.generate", description="music", executor="local.service",
            service_target="music", inputs=CapabilityInputs(optional=["text", "image"]),
            output_type="music",
        ),
    ])
    validator = PlanValidator(capabilities, max_revisions=10, max_tasks=10)
    snapshot = RunSnapshot(run=AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="生成一个 60 秒搞笑视频", idempotency_key="request-1",
        activated_skills=["workflow-direct-video"],
    ))
    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            capability_id="atomic.image.generate", objective="character reference",
        )],
    ))
    with pytest.raises(ValueError, match="exceeds the latest user request scope"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0,
            add_tasks=[PlannedTask(
                capability_id="atomic.music.generate", objective="background music",
            )],
        ))


def test_single_video_requires_explicit_parameter_and_pipeline_is_distinct():
    capabilities = CapabilityRegistry()
    assert capabilities.get("video.generate").mode == "instant"
    assert capabilities.get("video.pipeline.generate").mode == "master"
    assert capabilities.get("video.edit").inputs.required == ["video"]
    assert capabilities.get("video_gen.generate").target_agent == "video_gen"
    assert capabilities.get("keyframe.regenerate").executor == "local.service"
    assert capabilities.get("video.assemble").service_target == "video_assembly_by_request"


def test_runtime_policy_exposes_atomic_stage_generators_and_blocks_fixed_video_graph():
    from types import SimpleNamespace
    from app.chat.v2.capability_loader import build_registry
    from app.chat.v2.container import _apply_runtime_policy
    from app.chat.v2.skill_catalog import SkillCatalog

    root = Path(__file__).resolve().parents[1] / "skills" / "system"
    registry = build_registry(SkillCatalog([root]), include_platform=True)
    _apply_runtime_policy(
        registry,
        SimpleNamespace(DEEP_AGENT_V2_SANDBOX_ENABLED=True),
    )
    enabled = {item.id for item in registry.list()}
    assert {
        "atomic.text.generate",
        "atomic.image.generate",
        "atomic.music.generate",
        "atomic.video.generate",
    } <= enabled
    assert "video.generate" not in enabled
    assert "video.pipeline.generate" not in enabled
    assert {
        "outline.generate",
        "character.generate",
        "scene.generate",
        "shot.generate",
        "keyframe.generate",
        "shot.video.generate",
        "video.assemble",
    } <= enabled
    assert all(item.target_agent != "video" for item in registry.list())


@pytest.mark.asyncio
async def test_executor_defensively_rejects_fixed_video_target():
    class Client:
        async def delegate_submit(self, **_kwargs):
            raise AssertionError("fixed video target must not be called")

    capabilities = CapabilityRegistry()
    executor = CapabilityExecutor(Client(), capabilities)
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="video", idempotency_key="request-1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="legacy-video",
        capability_id="video.generate", objective="video",
        parameters={"single_video": True},
    )
    with pytest.raises(ValueError, match="blocks the legacy target_agent=video"):
        await executor.submit(run, task, [], "attempt-1")


def test_waiting_input_requires_verified_skill_policy(tmp_path):
    from app.chat.v2.models import InterruptionRequest
    from app.chat.v2.skill_catalog import SkillCatalog

    skill_dir = tmp_path / "review-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review-skill\ndescription: gated review\n---\n\n"
        "Human approval is required before rendering.\n",
        encoding="utf-8",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.discover()
    validator = PlanValidator(
        CapabilityRegistry(), max_revisions=10, max_tasks=10,
        skill_catalog=catalog,
    )
    snapshot = RunSnapshot(run=AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="make a video", idempotency_key="request-1",
        activated_skills=["review-skill"],
    ))
    with pytest.raises(ValueError, match="structured interruption"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0, waiting_for_input=True, response="confirm",
        ))


    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        waiting_for_input=True,
        interruption=InterruptionRequest(
            category="skill_required",
            what_happened="Review checkpoint reached.",
            why_interrupted="The activated Skill requires approval.",
            what_next="Approve or request changes.",
            skill_name="review-skill",
            skill_resource="SKILL.md",
            skill_policy="Human approval is required before rendering.",
        ),
        response="confirm",
    ))
    inactive = RunSnapshot(run=AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="make a video", idempotency_key="request-2",
    ))
    with pytest.raises(ValueError, match="not activated"):
        validator.validate(inactive, PlanPatch(
            base_revision=0,
            waiting_for_input=True,
            interruption=InterruptionRequest(
                category="skill_required",
                what_happened="Review checkpoint reached.",
                why_interrupted="The activated Skill requires approval.",
                what_next="Approve or request changes.",
                skill_name="review-skill",
                skill_resource="SKILL.md",
                skill_policy="Human approval is required before rendering.",
            ),
            response="confirm",
        ))


@pytest.mark.parametrize(
    "objective",
    [
        "使用 Seedance 2.0 生成两个15秒片段，最后拼接。",
        "使用即梦生成产品广告片段。",
        "调用视频生成模型制作广告。",
    ],
)
def test_video_provider_language_authorizes_video_scope(objective):
    from app.chat.v2.capabilities import CapabilityInputs, CapabilityManifest

    capabilities = CapabilityRegistry([CapabilityManifest(
        id="api.provider.generate",
        description="provider generation",
        executor="local.service",
        service_target="provider",
        inputs=CapabilityInputs(optional=["text", "image"]),
        output_type="video",
    )])
    validator = PlanValidator(capabilities, max_revisions=10, max_tasks=10)
    snapshot = RunSnapshot(run=AgentRun(
        thread_id="thread-video", project_id="project-video", user_id="user-video",
        objective=objective, idempotency_key="request-video",
        activated_skills=["seedance2"],
    ))

    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            capability_id="api.provider.generate", objective="生成15秒视频片段",
        )],
    ))


def test_video_retry_followup_inherits_original_request_scope():
    from app.chat.v2.capabilities import CapabilityInputs, CapabilityManifest

    capabilities = CapabilityRegistry([CapabilityManifest(
        id="api.provider.generate",
        description="provider generation",
        executor="local.service",
        service_target="provider",
        inputs=CapabilityInputs(optional=["text", "image"]),
        output_type="video",
    )])
    validator = PlanValidator(capabilities, max_revisions=10, max_tasks=10)
    run = AgentRun(
        thread_id="thread-retry", project_id="project-retry", user_id="user-retry",
        objective="请重新生成第一段,然后按顺序拼接",
        idempotency_key="request-retry", activated_skills=["seedance2"],
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[
            ChatMessage(
                run_id=run.id, role="user",
                content="生成两个15秒产品宣传视频片段，最后拼接成片。", sequence=1,
            ),
            ChatMessage(
                run_id=run.id, role="user",
                content="请重新生成第一段,然后按顺序拼接", sequence=2,
            ),
        ],
    )

    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            capability_id="api.provider.generate", objective="重新生成第一段15秒视频",
        )],
    ))


def test_skill_catalog_merges_cuti_metadata_sidecar(tmp_path):
    from app.chat.v2.skill_catalog import SkillCatalog

    skill_dir = tmp_path / "vendor-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: vendor-skill\ndescription: upstream instructions\n"
        "metadata:\n  version: '6.7.0'\n---\n\nVendor body.\n",
        encoding="utf-8",
    )
    (skill_dir / ".cuti-metadata.yaml").write_text(
        "metadata:\n"
        "  kind: workflow\n"
        "  workflow:\n"
        "    mode: vendor_workflow\n"
        "    allowed_capabilities: [atomic.text.generate]\n",
        encoding="utf-8",
    )

    metadata = SkillCatalog([tmp_path]).discover()[0]
    assert metadata.metadata == {
        "version": "6.7.0",
        "kind": "workflow",
        "workflow": {
            "mode": "vendor_workflow",
            "allowed_capabilities": ["atomic.text.generate"],
        },
    }


def test_cuti_product_workflow_uses_legacy_seedance2_dependency():
    from app.chat.v2.skill_catalog import SkillCatalog
    from app.orchestration.workflow_compiler import WorkflowRegistry

    skill_root = Path(__file__).resolve().parents[1] / "skills" / "external"
    catalog = SkillCatalog([skill_root])
    catalog.discover()

    spec = WorkflowRegistry().replace_from_catalog(catalog)["cuti-product-workflow"]

    assert spec.mode == "cuti_product_workflow"
    assert spec.skill_dependencies == (
        "seedance2",
        "product-feature-demo-script",
        "product-component-exploded-view",
        "product-voiceover-narration",
    )
    assert "seedance-20" not in spec.skill_dependencies
    assert spec.requires_keyframe is False
    assert spec.parameters["concept_image_model"] == "gpt-image-2"
    assert spec.parameters["segment_duration_seconds"] == 15
    assert spec.parameters["subordinate_skills"] == [
        "product-feature-demo-script",
        "product-component-exploded-view",
        "product-voiceover-narration",
    ]
    assert spec.parameters["skill_execution_order"] == [
        "product-feature-demo-script",
        "product-component-exploded-view",
        "product-voiceover-narration",
    ]
    assert spec.parameters["shot_script_skills"] == ["product-feature-demo-script"]
    assert spec.parameters["effect_skills"] == ["product-component-exploded-view"]
    assert spec.parameters["narration_skills"] == ["product-voiceover-narration"]
    assert spec.pipeline == (
        "atomic.text.generate",
        "atomic.image.generate",
        "api.provider.generate",
        "api.ark_protocol.generate",
        "media.concat",
    )
    skill_text = (
        skill_root / "cuti-product-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "### 4. 360-degree product setting image" in skill_text
    assert "artifact_role: product_360_reference" in skill_text
    assert "front, rear, left/right side" in skill_text
    assert "model: gpt-image-2" in skill_text
    assert "input_artifact_version_ids" in skill_text
    assert "one complete 15-second Seedance video" in skill_text
    assert "set `duration: 15`" in skill_text
    assert "Do not turn its internal beats into separate generation tasks" in skill_text
    assert "`use`, `skip`, or `conceptual`" in skill_text
    assert "visible trigger, product response, and understandable result" in skill_text
    assert "native synchronized audio" in skill_text
    assert "Do not call TTS" in skill_text
    assert "sequential, not parallel authors" in skill_text
    assert "_workflow_contract" in skill_text


def test_short_drama_workflow_uses_parallel_multi_reference_t2v():
    from app.chat.v2.skill_catalog import SkillCatalog
    from app.orchestration.workflow_compiler import WorkflowRegistry

    skill_root = Path(__file__).resolve().parents[1] / "skills" / "external"
    catalog = SkillCatalog([skill_root])
    catalog.discover()

    spec = WorkflowRegistry().replace_from_catalog(catalog)["short-drama-workflow"]

    assert spec.mode == "short_drama_workflow"
    assert spec.requires_keyframe is False
    assert spec.parameters["segment_execution_mode"] == "parallel"
    assert spec.parameters["video_generation_mode"] == "t2v"
    assert spec.parameters["continuity_mode"] == "shared_reference_images"
    assert "media.extract_frame" not in spec.pipeline
    assert spec.pipeline.index("atomic.image.generate") < spec.pipeline.index(
        "atomic.video.generate"
    )

    skill_text = (
        skill_root / "short-drama-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "none may depend on another segment task" in skill_text
    assert '`generation_mode: "t2v"`' in skill_text
    assert "Do not set `start_image_url`" in skill_text


def test_cuti_product_workflow_requires_360_reference_for_video_stage():
    reference = ArtifactVersion(
        project_id="project-product",
        type="image",
        produced_by_task_id="task-product-setting",
        title="360-degree product setting image",
        metadata={"artifact_role": "product_360_reference"},
    )
    PlanValidator._validate_cuti_product_reference_task(
        "atomic.image.generate",
        {"artifact_role": "product_360_reference", "model": "gpt-image-2"},
        [],
    )
    voice_profile = {
        "language": "zh-CN",
        "timbre": "warm-mid-low",
        "pace": "calm",
    }
    voice_hash = hashlib.sha256(json.dumps(
        voice_profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    parameters = {
        "duration": 15,
        "generate_audio": True,
        "_workflow_contract": {
            "narration_mode": "native_voiceover",
            "voice_profile": voice_profile,
            "voice_profile_hash": voice_hash,
            "segment_script": {
                "segment_id": "segment-01",
                "duration_seconds": 15,
                "beats": [
                    {
                        "beat_id": "segment-01-beat-01",
                        "start_seconds": 0,
                        "end_seconds": 15,
                        "selling_point_status": "verified",
                        "visual": {"action": "show the verified product function"},
                        "narration": {
                            "start_seconds": 1,
                            "end_seconds": 4,
                            "text": "产品旁白",
                        },
                    },
                ],
            },
        },
    }
    PlanValidator._validate_cuti_product_reference_task(
        "api.provider.generate", parameters, [reference],
    )
    assert PlanValidator._validate_cuti_product_script_task(
        "api.provider.generate", parameters,
    ) == voice_hash
    with pytest.raises(ValueError, match="product_360_reference"):
        PlanValidator._validate_cuti_product_reference_task(
            "api.provider.generate", {"duration": 15}, [],
        )
    invalid = json.loads(json.dumps(parameters))
    invalid["_workflow_contract"]["voice_profile"]["pace"] = "fast"
    with pytest.raises(ValueError, match="voice_profile_hash"):
        PlanValidator._validate_cuti_product_script_task(
            "api.provider.generate", invalid,
        )


def test_product_feature_demo_is_default_shot_script_skill():
    from app.chat.v2.skill_catalog import SkillCatalog

    skill_root = Path(__file__).resolve().parents[1] / "skills" / "external"
    catalog = SkillCatalog([skill_root])
    catalog.discover()

    skill = catalog.load("product-feature-demo-script")
    metadata = skill.metadata.metadata

    assert metadata["kind"] == "shot-script"
    assert metadata["shot_script"]["activation"] == "default"
    assert metadata["scope"]["type"] == "stage"
    assert "atomic.text.generate" in metadata["selectors"]["capabilities"]
    assert "api.provider.generate" in metadata["selectors"]["capabilities"]
    assert "Never invent unsupported capabilities" in skill.metadata.description
    assert "Reject empty beauty-shot plans" in skill.instructions
    assert "trigger, response, and result" in skill.instructions


def test_product_component_exploded_view_is_conditional_effect_skill():
    from app.chat.v2.skill_catalog import SkillCatalog

    skill_root = Path(__file__).resolve().parents[1] / "skills" / "external"
    catalog = SkillCatalog([skill_root])
    catalog.discover()

    skill = catalog.load("product-component-exploded-view")
    metadata = skill.metadata.metadata

    assert metadata["kind"] == "effect"
    assert metadata["effect"]["activation"] == "conditional"
    assert metadata["scope"]["type"] == "stage"
    assert "api.provider.generate" in metadata["selectors"]["capabilities"]
    assert "Do not apply automatically to every product ad" in skill.metadata.description
    assert "make the complete disassembly-and-reassembly effect last about three seconds" in skill.instructions
    assert "between roughly 2.5 and 3.5 seconds by default" in skill.instructions
    assert "never separate short video tasks" in skill.instructions


def test_payload_media_uri_reads_direct_video_result_collection():
    assert payload_media_uri({
        "videos": [{"video_url": "http://localhost/files/generated.mp4"}],
    }) == "http://localhost/files/generated.mp4"


def test_collect_artifact_video_urls_prefers_canonical_uri():
    artifacts = [
        ArtifactVersion(
            project_id="p1", type="video", produced_by_task_id="t1",
            uri="http://localhost/files/videos/first.mp4",
            metadata={"videos": [
                {"index": 0, "video_url": "http://localhost/files/videos/a.mp4"},
                {"index": 1, "video_url": "http://localhost/files/videos/b.mp4"},
            ]},
        ),
        ArtifactVersion(
            project_id="p1", type="video", produced_by_task_id="t2",
            uri="http://localhost/files/videos/c.mp4",
            metadata={},
        ),
    ]
    assert CapabilityExecutor._collect_artifact_video_urls(artifacts) == [
        "http://localhost/files/videos/first.mp4",
        "http://localhost/files/videos/c.mp4",
    ]


@pytest.mark.asyncio
async def test_assemble_falls_back_to_artifact_concat_without_story_outline(monkeypatch):
    from app.exceptions import BusinessException, BusinessExceptionCode

    class Service:
        async def video_assembly_by_request(self, **_kwargs):
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND, "找不到故事梗概",
            )

    async def fake_concat(urls, run_id, normalize=True):
        assert urls == [
            "http://localhost/files/videos/a.mp4",
            "http://localhost/files/videos/b.mp4",
        ]
        assert run_id == "assemble-1"
        assert normalize is True
        return {"result_url": "http://localhost/files/videos/final.mp4", "duration": 30.0}

    monkeypatch.setattr(
        "app.services.agent.video_agent_service.get_video_agent_service",
        lambda: Service(),
    )
    monkeypatch.setattr("app.utils.media_service_client.video_concat", fake_concat)

    executor = CapabilityExecutor(object(), CapabilityRegistry())
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="assemble", idempotency_key="assemble-1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="assemble",
        capability_id="video.assemble", objective="concat clips",
        parameters={"thread_id": "thread-1"},
    )
    selected = [
        ArtifactVersion(
            project_id="project-1", type="video", produced_by_task_id="t1",
            uri="http://localhost/files/videos/a.mp4",
        ),
        ArtifactVersion(
            project_id="project-1", type="video", produced_by_task_id="t2",
            uri="http://localhost/files/videos/b.mp4",
        ),
    ]

    result = await executor._assemble_videos(
        project_thread_id="thread-1",
        run=run,
        task=task,
        selected=selected,
        remote_run_id="assemble-1",
        user_option=None,
        parameters=task.parameters,
    )

    assert result["uri"] == "http://localhost/files/videos/final.mp4"
    assert result["assembly_mode"] == "artifact_concat"


def test_coerce_video_agent_user_option_accepts_chat_layer_instance():
    from app.chat.models.user_options import UserOption as ChatUserOption
    from app.models.user_options import UserOption as AgentUserOption
    from app.services.agent.video.project_stage_context import coerce_video_agent_user_option

    chat_option = ChatUserOption.model_validate({
        "duration": 30, "resolution": "1080p", "aspect_ratio": "16:9",
    })
    coerced = coerce_video_agent_user_option(chat_option)
    assert isinstance(coerced, AgentUserOption)
    assert coerced.duration == 30


@pytest.mark.asyncio
async def test_executor_preserves_identity_options_files_idempotency_and_artifact_uri():
    from app.chat.v2.capability_loader import build_registry
    from app.chat.v2.skill_catalog import SkillCatalog

    class Response:
        run_id = "remote-1"
        status = "queued"

    class Client:
        async def delegate_submit(self, **kwargs):
            self.kwargs = kwargs
            return Response()

    client = Client()
    system = Path(__file__).resolve().parents[1] / "skills" / "system"
    capabilities = build_registry(SkillCatalog([system]), include_platform=True)
    executor = CapabilityExecutor(client, capabilities)
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="video", idempotency_key="request-1",
        user_option={"duration": 20},
        input_files=[InputFile(type="image", url="https://cdn/input.png")],
    )
    artifact = ArtifactVersion(
        project_id=run.project_id, type="text", produced_by_task_id="old-task",
        summary="full story details", uri="https://cdn/story.json",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-1",
        capability_id="video_gen.generate", objective="make the full video",
        input_artifact_version_ids=[artifact.id],
    )
    await executor.submit(run, task, [artifact], "attempt-key")
    assert client.kwargs["target_agent"] == "video_gen"
    assert client.kwargs["idempotency_key"] == "attempt-key"
    state = client.kwargs["state"]
    assert state["user_id"] == "user-1"
    assert state["thread_id"] == f"thread-1:v2-task:{task.id}"
    assert state["user_input_data"].images[0].url == "https://cdn/input.png"
    assert "https://cdn/story.json" in state["user_input_data"].user_input


@pytest.mark.asyncio
async def test_executor_egresses_frontend_uploaded_media_before_delegate(monkeypatch):
    from app.chat.v2.capability_loader import build_registry
    from app.chat.v2.skill_catalog import SkillCatalog

    class Response:
        run_id = "remote-egress"
        status = "queued"

    class Client:
        async def delegate_submit(self, **kwargs):
            self.kwargs = kwargs
            return Response()

    async def fake_resolve(url, **_kwargs):
        if url.startswith("http://localhost:19004/files/"):
            return "https://provider.example/uploaded-product.webp"
        return url

    monkeypatch.setattr(
        "app.utils.media_egress.resolve_outbound_media_url", fake_resolve,
    )
    client = Client()
    system = Path(__file__).resolve().parents[1] / "skills" / "system"
    capabilities = build_registry(SkillCatalog([system]), include_platform=True)
    executor = CapabilityExecutor(client, capabilities)
    local_url = "http://localhost:19004/files/images/product.webp"
    run = AgentRun(
        thread_id="thread-egress", project_id="project-egress", user_id="user-1",
        objective="analyze the uploaded product", idempotency_key="request-egress",
        input_files=[InputFile(type="image", url=local_url, filename="product.png")],
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-egress",
        capability_id="video_gen.generate",
        objective=f"write an ad brief using {local_url}",
        parameters={"input_image_urls": [local_url]},
    )

    await executor.submit(run, task, [], "attempt-egress")

    image = client.kwargs["state"]["user_input_data"].images[0]
    assert image.url == "https://provider.example/uploaded-product.webp"
    assert image.filename == "product.png"
    assert local_url not in client.kwargs["state"]["user_input_data"].user_input
    assert client.kwargs["parameters"]["input_image_urls"] == [
        "https://provider.example/uploaded-product.webp",
    ]


@pytest.mark.asyncio
async def test_single_video_adapter_fails_without_explicit_contract_flag():
    class Client:
        async def delegate_submit(self, **_kwargs):
            raise AssertionError("transport must not be called")

    capabilities = CapabilityRegistry()
    executor = CapabilityExecutor(Client(), capabilities)
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="video", idempotency_key="request-1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-1",
        capability_id="video.generate", objective="make one clip",
    )
    with pytest.raises(ValueError, match="blocks the legacy target_agent=video"):
        await executor.submit(run, task, [], "attempt-key")


@pytest.mark.asyncio
async def test_video_gen_maps_generation_options_without_fixed_graph():
    from app.chat.v2.capability_loader import build_registry
    from app.chat.v2.skill_catalog import SkillCatalog

    class Response:
        run_id = "remote-instant"
        status = "queued"

    class Client:
        async def delegate_submit(self, **kwargs):
            self.kwargs = kwargs
            return Response()

    client = Client()
    system = Path(__file__).resolve().parents[1] / "skills" / "system"
    capabilities = build_registry(SkillCatalog([system]), include_platform=True)
    executor = CapabilityExecutor(client, capabilities)
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="video", idempotency_key="request-1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-1",
        capability_id="video_gen.generate", objective="make one clip",
        parameters={
            "duration": 10,
            "resolution": "1080p",
            "aspect_ratio": "16:9",
            "video_generation_tool": "seedance_2_i2v",
        },
    )
    await executor.submit(run, task, [], "attempt-key")
    user_option = client.kwargs["state"]["user_input_data"].user_option
    assert user_option.duration == 10
    assert user_option.resolution.value == "1080p"
    assert user_option.aspect_ratio.value == "16:9"
    assert user_option.video_generation_tool.value == "seedance_2_i2v"
    assert client.kwargs["target_agent"] == "video_gen"
    assert client.kwargs.get("mode") is None


@pytest.mark.asyncio
async def test_video_gen_direct_preserves_seedance_options_without_workflow_gates():
    class Response:
        run_id = "remote-direct"
        status = "queued"

    class Client:
        async def delegate_submit(self, **kwargs):
            self.kwargs = kwargs
            return Response()

    client = Client()
    executor = CapabilityExecutor(client, CapabilityRegistry())
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="video", idempotency_key="request-1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-direct",
        capability_id="video_gen.generate", objective="make one Seedance clip",
        parameters={
            "duration": 10,
            "resolution": "1080p",
            "aspect_ratio": "16:9",
            "video_generation_tool": "seedance_2_i2v",
        },
    )

    await executor.submit(run, task, [], "attempt-direct")

    assert client.kwargs["target_agent"] == "video_gen"
    user_option = client.kwargs["state"]["user_input_data"].user_option
    assert user_option.duration == 10
    assert user_option.video_generation_tool.value == "seedance_2_i2v"


@pytest.mark.asyncio
async def test_reconciler_synthesizes_interrupt_from_conversation_run():
    class Pool:
        async def fetch(self, *_args):
            return []

        async def fetchrow(self, *_args):
            return {"id": 11, "status": "interrupted", "error_message": "after_music"}

    reconciler = V2ResultReconciler("postgresql://unused")
    reconciler.pool = Pool()
    events = await reconciler._events("remote-1")
    assert events[0].event_type == "interrupt"
    assert events[0].source_event_id == "va-run:11:interrupted"


@pytest.mark.asyncio
async def test_harness_maps_interrupt_to_waiting_input():
    from app.chat.v2.context import ContextAssembler
    from app.chat.v2.executors import CapabilityExecutor
    from app.chat.v2.harness import DynamicHarness
    from app.chat.v2.models import RunStatus, SourceEvent, TaskStatus
    from app.chat.v2.plan_validator import PlanValidator

    class Client:
        async def delegate_submit(self, **_kwargs):
            raise AssertionError("not used")

    repository = InMemoryV2Repository()
    capabilities = CapabilityRegistry()
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        validator=PlanValidator(capabilities, max_revisions=20, max_tasks=50),
        executor=CapabilityExecutor(Client(), capabilities),
        context_assembler=ContextAssembler(),
    )
    run = AgentRun(
        thread_id="thread-1", project_id="project-1", user_id="user-1",
        objective="pipeline", idempotency_key="request-1",
    )
    stored, _ = await repository.create_run(run)
    created = await repository.apply_patch(
        stored,
        PlanPatch(
            response="start",
            add_tasks=[PlannedTask(
                client_key="task-1",
                capability_id="video.pipeline.generate",
                objective="full pipeline",
            )],
        ),
    )
    task = created[0]
    task.status = TaskStatus.WAITING_EXTERNAL
    task.remote_operation_id = "remote-1"
    task.remote_thread_id = "thread-1:v2-task"
    await repository.save_task(task)

    accepted = await harness.ingest_source_event(SourceEvent(
        remote_run_id="remote-1",
        event_type="interrupt",
        source_event_id="va-message:42",
        content="音乐生成失败",
        payload={
            "message_id": 42,
            "step": "music",
            "failure": {"message": "provider unavailable"},
            "disable_auto_resume": True,
        },
    ))
    assert accepted is True
    snapshot = await repository.snapshot(stored.id)
    assert snapshot.run.status == RunStatus.WAITING_INPUT
    assert snapshot.tasks[0].remote_interrupt_msgid == 42
    assert snapshot.tasks[0].status == TaskStatus.WAITING_EXTERNAL
    events = await repository.get_events(stored.id)
    waiting = next(item for item in events if item.type == "run.waiting_input")
    assert waiting.payload["interrupt_category"] == "failure"
    assert waiting.payload["capability_id"] == "video.pipeline.generate"
    assert waiting.payload["confirmation_required"] is True


def test_suggest_actions_preserves_direct_generate_affordance():
    task = Task(
        run_id="run-1", revision=1, client_key="task-1",
        capability_id="actions.suggest", objective="choice\n完善风格 | 增加电影感细节\n调整时长 | 改成 60 秒",
        parameters={"language": "zh", "generation_input": {"prompt": "完整要求"}},
    )
    result = CapabilityExecutor._suggest_actions(task)
    suggestions = result.artifact["metadata"]["suggestions"]
    direct = next(item for item in suggestions if item["is_direct_generate"])
    assert direct["label"] == "直接生成"
    assert direct["generation_input"] == {"prompt": "完整要求"}


def test_v2_api_exposes_required_routes_with_response_models():
    from app.chat.api.v2 import v2_api

    routes = {
        (method, route.path): route
        for route in v2_api.routes
        for method in route.methods
    }
    expected = {
        ("POST", "/v2/runs"),
        ("POST", "/v2/uploads"),
        ("GET", "/v2/skills"),
        ("GET", "/v2/runs"),
        ("GET", "/v2/runs/{run_id}"),
        ("POST", "/v2/runs/{run_id}/messages"),
        ("GET", "/v2/runs/{run_id}/messages"),
        ("GET", "/v2/runs/{run_id}/events"),
        ("GET", "/v2/runs/{run_id}/event-log"),
        ("POST", "/v2/runs/{run_id}/cancel"),
        ("POST", "/v2/runs/{run_id}/resume"),
        ("POST", "/v2/runs/{run_id}/artifacts/{version_id}/select"),
        ("POST", "/v2/internal/video-agent/events"),
        ("POST", "/v2/internal/skills/reload"),
        ("POST", "/v2/internal/skills/install"),
        ("POST", "/v2/internal/skills/install-archive"),
    }
    assert expected <= routes.keys()
    assert all(routes[item].response_model is not None for item in expected if "events" not in item[1])


def test_service_runtime_exposes_v2_without_legacy_router():
    from app.chat.api.v2 import v2_api
    from app.chat.main import service_app

    v2_paths = {
        route.path for route in v2_api.routes if getattr(route, "path", None)
    }
    assert "/v2/runs" in v2_paths
    assert not any(
        getattr(route, "path", None) == "/agent-router/stream"
        for route in service_app.routes
    )


@pytest.mark.asyncio
async def test_reconciler_uses_conversation_run_failure_fallback():
    class Pool:
        async def fetch(self, *_args):
            return []

        async def fetchrow(self, *_args):
            return {"id": 9, "status": "cancelled", "error_message": "stopped"}

    reconciler = V2ResultReconciler("postgresql://unused")
    reconciler.pool = Pool()
    events = await reconciler._events("remote-1")
    assert events[0].event_type == "cancelled"
    assert events[0].source_event_id == "va-run:9:cancelled"


def test_atomic_capabilities_are_platform_manifests_not_system_skills():
    from pathlib import Path
    from app.chat.v2.capability_loader import build_registry
    from app.chat.v2.skill_catalog import SkillCatalog

    root = Path(__file__).resolve().parents[1] / "skills" / "system"
    catalog = SkillCatalog([root])
    names = {item.name for item in catalog.discover()}
    assert not {"atomic-text", "atomic-image", "generate-story", "generate-shots"} & names
    assert {"workflow-direct-video", "workflow-keyframe-pipeline", "workflow-short-drama"} <= names
    registry = build_registry(catalog, include_platform=True)
    assert registry.get("atomic.text.generate").executor == "atomic.direct"
    assert registry.get("atomic.text.generate").target_agent is None
    assert registry.get("keyframe.regenerate").service_target == "execute_regenerate_keyframes"
    assert registry.get("shot.generate").service_target == "generate_shots_by_request"


def test_capability_loader_merges_workflow_skills_over_platform_manifests():
    from pathlib import Path
    from app.chat.v2.capability_loader import build_registry
    from app.chat.v2.skill_catalog import SkillCatalog

    root = Path(__file__).resolve().parents[1] / "skills" / "system"
    registry = build_registry(SkillCatalog([root]), include_platform=True)
    for capability_id in (
        "atomic.text.generate", "atomic.image.generate",
        "atomic.music.generate", "atomic.video.generate",
    ):
        capability = registry.get(capability_id)
        assert capability.executor == "atomic.direct"
        assert capability.target_agent is None
        assert capability.parameters_schema["required"] == ["prompt"]
    assert registry.get("story.generate").skill_name == "generate-story"
    assert registry.get("keyframe.regenerate").service_target == "execute_regenerate_keyframes"
    assert registry.get("video_gen.generate").target_agent == "video_gen"
    assert registry.get("shot.generate").service_target == "generate_shots_by_request"
    assert registry.get("shot.generate").skill_name == "generate-shots"
    assert registry.get("keyframe.generate").service_target == "generate_keyframes_by_request"
    assert registry.get("keyframe.generate").skill_name == "generate-keyframe"
    assert registry.get("outline.generate").service_target == "generate_outline_by_request"
    assert registry.get("character.generate").service_target == "generate_characters_by_request"
    assert registry.get("scene.generate").service_target == "generate_scenes_by_request"
    assert registry.get("shot.video.generate").service_target == "generate_shot_videos_by_request"


def test_unsupported_executor_is_rejected_by_contract_schema():
    from app.chat.v2.skill_catalog import SkillContract

    with pytest.raises(ValueError, match="unsupported executor"):
        SkillContract(
            executor="unknown.executor", target="x", produces="x", capability_id="weird.tool",
        )


@pytest.mark.asyncio
async def test_local_service_executor_maps_regenerate_keyframes(monkeypatch):
    from app.chat.v2.executors import CapabilityExecutor

    called = {}

    async def fake_execute_regenerate_keyframes(**kwargs):
        called.update(kwargs)
        return {"summary": "ok", "uri": "https://cdn/kf.png"}

    monkeypatch.setattr(
        "app.services.task_enqueue_service.execute_regenerate_keyframes",
        fake_execute_regenerate_keyframes,
    )

    class Client:
        async def delegate_submit(self, **_kwargs):
            raise AssertionError("delegate must not be used")

    capabilities = CapabilityRegistry()
    executor = CapabilityExecutor(Client(), capabilities)
    run = AgentRun(
        thread_id="chat-thread", project_id="project-1", user_id="user-1",
        objective="regen", idempotency_key="request-1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-1",
        capability_id="keyframe.regenerate", objective="regen kf",
        parameters={
            "thread_id": "project-thread",
            "keyframes": [{"keyframe_uuid": "kf-1"}],
        },
    )
    result = await executor.submit(run, task, [], "attempt-key")
    assert result.status == "completed"
    assert called["thread_id"] == "project-thread"
    assert called["user_id"] == "user-1"
    assert called["keyframes"] == [{"keyframe_uuid": "kf-1"}]
    assert result.artifact["uri"] == "https://cdn/kf.png"


@pytest.mark.asyncio
async def test_local_service_executor_maps_generate_shots(monkeypatch):
    from app.chat.v2.executors import CapabilityExecutor

    called = {}

    async def fake_generate_shots_by_request(**kwargs):
        called.update(kwargs)
        return {
            "summary": "generated 3 detailed shots",
            "shot_uuids": ["s1", "s2", "s3"],
            "thread_id": kwargs["thread_id"],
        }

    monkeypatch.setattr(
        "app.services.agent.video.generate_shots_by_request_service.generate_shots_by_request",
        fake_generate_shots_by_request,
    )

    class Client:
        async def delegate_submit(self, **_kwargs):
            raise AssertionError("delegate must not be used")

    capabilities = CapabilityRegistry()
    executor = CapabilityExecutor(Client(), capabilities)
    run = AgentRun(
        thread_id="chat-thread", project_id="project-1", user_id="user-1",
        objective="generate shots", idempotency_key="request-shots",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-shots",
        capability_id="shot.generate", objective="generate shots",
        parameters={"thread_id": "project-thread"},
    )
    result = await executor.submit(run, task, [], "attempt-shots")
    assert result.status == "completed"
    assert called["thread_id"] == "project-thread"
    assert called["user_id"] == "user-1"
    assert result.artifact["metadata"]["shot_uuids"] == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_local_service_executor_maps_generate_keyframes(monkeypatch):
    from app.chat.v2.executors import CapabilityExecutor

    called = {}

    async def fake_generate_keyframes_by_request(**kwargs):
        called.update(kwargs)
        return {
            "summary": "generated 2 keyframes",
            "keyframe_uuids": ["k1", "k2"],
            "shot_uuids": kwargs.get("shot_uuids") or ["s1", "s2"],
            "thread_id": kwargs["thread_id"],
        }

    monkeypatch.setattr(
        "app.services.agent.video.generate_keyframes_by_request_service.generate_keyframes_by_request",
        fake_generate_keyframes_by_request,
    )

    class Client:
        async def delegate_submit(self, **_kwargs):
            raise AssertionError("delegate must not be used")

    capabilities = CapabilityRegistry()
    executor = CapabilityExecutor(Client(), capabilities)
    run = AgentRun(
        thread_id="chat-thread", project_id="project-1", user_id="user-1",
        objective="generate keyframes", idempotency_key="request-keyframes",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="task-keyframes",
        capability_id="keyframe.generate", objective="generate keyframes",
        parameters={
            "thread_id": "project-thread",
            "shot_uuids": ["s1", "s2"],
        },
    )
    result = await executor.submit(run, task, [], "attempt-keyframes")
    assert result.status == "completed"
    assert called["thread_id"] == "project-thread"
    assert called["user_id"] == "user-1"
    assert called["shot_uuids"] == ["s1", "s2"]
    assert result.artifact["metadata"]["keyframe_uuids"] == ["k1", "k2"]


@pytest.mark.asyncio
async def test_local_service_executor_maps_stage_generate_skills(monkeypatch):
    from app.chat.v2.executors import CapabilityExecutor

    calls = []

    async def fake_outline(**kwargs):
        calls.append(("outline", kwargs))
        return {"story_outline_uuid": "o1", "summary": "outline ok"}

    async def fake_characters(**kwargs):
        calls.append(("characters", kwargs))
        return {"character_uuids": ["c1"], "summary": "characters ok"}

    async def fake_scenes(**kwargs):
        calls.append(("scenes", kwargs))
        return {"scene_uuids": ["sc1"], "summary": "scenes ok"}

    async def fake_shot_videos(**kwargs):
        calls.append(("shot_videos", kwargs))
        return {"video_generation_uuids": ["v1"], "summary": "videos ok"}

    monkeypatch.setattr(
        "app.services.agent.video.generate_outline_by_request_service.generate_outline_by_request",
        fake_outline,
    )
    monkeypatch.setattr(
        "app.services.agent.video.generate_characters_by_request_service.generate_characters_by_request",
        fake_characters,
    )
    monkeypatch.setattr(
        "app.services.agent.video.generate_scenes_by_request_service.generate_scenes_by_request",
        fake_scenes,
    )
    monkeypatch.setattr(
        "app.services.agent.video.generate_shot_videos_by_request_service.generate_shot_videos_by_request",
        fake_shot_videos,
    )

    class Client:
        async def delegate_submit(self, **_kwargs):
            raise AssertionError("delegate must not be used")

    capabilities = CapabilityRegistry()
    executor = CapabilityExecutor(Client(), capabilities)
    run = AgentRun(
        thread_id="chat-thread", project_id="project-1", user_id="user-1",
        objective="stage chain", idempotency_key="request-stages",
    )
    for capability_id, client_key in [
        ("outline.generate", "t-outline"),
        ("character.generate", "t-characters"),
        ("scene.generate", "t-scenes"),
        ("shot.video.generate", "t-shot-videos"),
    ]:
        task = Task(
            run_id=run.id, revision=1, client_key=client_key,
            capability_id=capability_id, objective=capability_id,
            parameters={
                "thread_id": "project-thread",
                "content_category": (
                    "scenario_product_ad"
                    if capability_id == "shot.video.generate"
                    else "short_drama"
                ),
                "duration": 60,
            },
        )
        result = await executor.submit(run, task, [], f"attempt-{client_key}")
        assert result.status == "completed"
    assert [name for name, _ in calls] == [
        "outline", "characters", "scenes", "shot_videos",
    ]
    assert all(kwargs["thread_id"] == "project-thread" for _, kwargs in calls)
    assert all(kwargs["user_id"] == "user-1" for _, kwargs in calls)
    assert all(kwargs["user_option"].duration == 60 for _, kwargs in calls)
    assert [kwargs["user_option"].content_category.value for _, kwargs in calls] == [
        "Short Drama",
        "Short Drama",
        "Short Drama",
        "Product Launch",
    ]
    assert [kwargs["user_input"] for _, kwargs in calls] == [
        "outline.generate",
        "character.generate",
        "scene.generate",
        "shot.video.generate",
    ]


def test_harness_injects_session_thread_id_into_task_parameters():
    from app.chat.v2.harness import DynamicHarness

    run = AgentRun(
        thread_id="project-thread-xyz", project_id="project-thread-xyz",
        user_id="user-1", objective="goal", idempotency_key="request-1",
    )
    patch = PlanPatch(
        add_tasks=[
            PlannedTask(
                capability_id="shot.generate",
                objective="generate shots",
                parameters={},
            ),
            PlannedTask(
                capability_id="keyframe.generate",
                objective="generate keyframes",
                parameters={"thread_id": "keep-me"},
            ),
        ],
    )
    harness = DynamicHarness.__new__(DynamicHarness)
    harness.capabilities = CapabilityRegistry()
    ensured = harness._ensure_project_thread_parameters(run, patch)
    assert ensured.add_tasks[0].parameters["thread_id"] == "project-thread-xyz"
    assert ensured.add_tasks[1].parameters["thread_id"] == "keep-me"


def test_harness_does_not_inject_workflow_fields_into_media_concat():
    from app.chat.v2.harness import DynamicHarness

    run = AgentRun(
        thread_id="project-thread-xyz",
        project_id="project-thread-xyz",
        user_id="user-1",
        objective="assemble clips",
        idempotency_key="request-concat",
        activated_skills=["seedance2"],
    )
    expected = {
        "video_urls": ["https://media.test/01.mp4", "https://media.test/02.mp4"],
        "normalize": True,
        "transition_duration": 0.125,
        "run_id": "run-1",
    }
    patch = PlanPatch(add_tasks=[PlannedTask(
        capability_id="media.concat",
        objective="assemble in order",
        parameters=dict(expected),
    )])
    harness = DynamicHarness.__new__(DynamicHarness)
    harness.capabilities = CapabilityRegistry()

    ensured = harness._ensure_project_thread_parameters(run, patch)

    assert ensured.add_tasks[0].parameters == expected
    assert "workflow_mode" not in ensured.add_tasks[0].parameters
    assert "shot_workflow_mode" not in ensured.add_tasks[0].parameters
    assert "activated_workflow" not in ensured.add_tasks[0].parameters


def test_install_external_skill_and_reload(tmp_path):
    from app.chat.v2.capability_loader import build_registry
    from app.chat.v2.skill_catalog import SkillCatalog
    from app.chat.v2.skill_install import install_skill_directory

    system = Path(__file__).resolve().parents[1] / "skills" / "system"
    external = tmp_path / "external"
    external.mkdir()
    source = tmp_path / "demo-skill"
    source.mkdir()
    (source / "run").mkdir()
    (source / "run" / "__main__.py").write_text(
        "print('sandbox fixture')\n",
        encoding="utf-8",
    )
    (source / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo external skill.\n---\n\n"
        "# Demo\n\n```cuti-contract\n"
        '{"capability_id":"demo.skill","executor":"sandbox.run",'
        '"target":"run/__main__.py","accepts":[],"produces":"action_suggestions",'
        '"sandbox":{"entrypoint":"run/__main__.py","network":"none"}}\n'
        "```\n",
        encoding="utf-8",
    )
    catalog = SkillCatalog([system, external])
    registry = build_registry(catalog)
    name = install_skill_directory(
        source, external, catalog=catalog, registry=registry, overwrite=True,
    )
    assert name == "demo-skill"
    assert registry.get("demo.skill").executor == "sandbox.run"
    assert registry.get("demo.skill").trust_level == "untrusted"
    assert (external / "demo-skill" / "SKILL.md").is_file()

