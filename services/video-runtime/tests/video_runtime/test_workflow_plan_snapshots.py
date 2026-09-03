from __future__ import annotations

import hashlib
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.orchestration.workflow_compiler.registry import WorkflowSpec
from app.video_runtime.initial_build import BuildPlanValidationError, topological_steps
from app.video_runtime.models import MediaArtifactVersion, ValidationResult, VideoSpec
from app.video_runtime.plugins import PluginContext, VideoPluginRegistry
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.skill_workflows import load_workflow_skills
from app.video_runtime.skills import VideoSkillRuntime
from app.video_runtime.workflow_plans import (
    ORIGINAL_CUTI_WORKFLOW_CONTRACTS,
    WORKFLOW_ID_COMPILERS,
    _scenario_provider_prompt,
    compile_seedance2,
    compile_skill_workflow,
    validate_original_cuti_workflow_contract,
)


def _spec(
    workflow_id: str,
    *,
    segment_seconds: float = 5,
    source_asset_ids: list[str] | None = None,
    bgm_prompt: str = "",
    subtitles: bool = False,
) -> VideoSpec:
    return VideoSpec.model_validate({
        "title": f"{workflow_id} snapshot",
        "target_duration_seconds": segment_seconds * 2,
        "workflow_id": workflow_id,
        "source_asset_ids": source_asset_ids or [],
        "characters": [{
            "id": "hero", "name": "Hero", "appearance": "black hair",
        }],
        "shots": [{
            "id": str(index), "order": index,
            "duration_seconds": segment_seconds,
            "beat": f"beat {index}",
            "visual_prompt": (
                f"镜头{index}：0-{int(segment_seconds)}秒，主体完成一次清晰可见的动作变化。"
            ),
            "narration": "让产品价值通过画面中的动作被看见。",
            "character_ids": ["hero"],
        } for index in (1, 2)],
        "audio": {"bgm_prompt": bgm_prompt, "subtitles": subtitles},
        "workflow_parameters": {
            "scenes": [{
                "id": "main-location",
                "name": "Main location",
                "description": "A stable recurring interior with clear spatial geography",
            }],
        },
    })


def _signature(plan) -> list[str]:
    return [
        f"{item.step_id}:{item.action}:{item.capability}"
        for item in topological_steps(plan.items)
    ]


def _scenario_spec(source_asset_id: str) -> VideoSpec:
    spec = _spec(
        "cuti-scenario-product-workflow",
        segment_seconds=15,
        source_asset_ids=[source_asset_id],
    )
    return spec.model_copy(update={
        "workflow_parameters": {
            "primary_selling_point": "嘈杂环境中依然清晰聆听",
            # A planner may accidentally put the whole creative bible here.
            # The compiler must prefer the structured scenario.setting below.
            "scene_setting_prompt": (
                "Hero with black hair holds the advertised headphones in the rehearsal room"
            ),
            "segment_proofs": {
                "1": "戴上耳机后环境噪声降低，人物听清节拍",
                "2": "人物按清晰节拍带动团队完成同步表演",
            },
            "dramatic_proposition": "耳机让嘈杂排练恢复清晰协作",
            "scenario": {
                "setting": "雨夜的空置排练室，深色吸音墙和暖色顶灯",
            },
            "character_setting_reference_prompt": "仅生成角色设定图，不得包含产品或场景。",
            "product_setting_reference_prompt": "仅生成产品设定图，不得包含人物或使用场景。",
        },
    })


class ProviderMockExecutor:
    async def execute_plan_step(
        self, *, build, step, completed_artifacts, idempotency_key,
        report_remote_operation=None,
    ) -> MediaArtifactVersion:
        metadata = {
            "generation_parameters": dict(step.parameters),
            "plan_step_id": step.step_id,
        }
        if step.output_artifact_type == "video_spec":
            metadata["content"] = step.parameters["content"]
        if step.output_artifact_type == "timeline":
            cursor = 0.0
            items = []
            for shot, source_step in zip(
                step.parameters["shots"], step.parameters["video_steps"], strict=True,
            ):
                source = completed_artifacts[source_step]
                items.append({
                    "artifactVersionId": source.id,
                    "startSeconds": cursor,
                    "durationSeconds": shot["duration_seconds"],
                })
                cursor += shot["duration_seconds"]
            metadata["timeline"] = {"items": items, "durationSeconds": cursor}
        document_types = {
            "video_spec", "script", "characters", "storyboard",
            "outline", "scenes", "shots", "timeline", "research",
        }
        return MediaArtifactVersion(
            project_id=build.project_id,
            type=step.output_artifact_type,
            uri=(
                None if step.output_artifact_type in document_types
                else f"https://provider-mock.test/{step.step_id}"
            ),
            metadata=metadata,
            provenance={"idempotency_key": idempotency_key},
        )


class WorkflowPlanSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.skills = VideoSkillRuntime()
        plugins = VideoPluginRegistry()
        await load_workflow_skills(plugins, self.skills)
        self.plugin = plugins.get("cuti.skill-workflows").implementation
        self.source = MediaArtifactVersion(
            id="source-version-1", artifact_id="source:product",
            project_id="project-1", type="source_image",
            uri="https://example.test/product.png",
        )

    async def _compile(self, workflow_id: str, spec: VideoSpec | None = None):
        return await self.plugin.compile_build_plan(
            PluginContext(project_id="project-1", values={
                "base_project_version_id": "version-1",
                "source_artifacts": {self.source.artifact_id: self.source},
            }),
            spec or _spec(workflow_id),
        )

    def test_scenario_execution_header_is_canonical_after_repair(self) -> None:
        old_header = (
            "任务类型: 产品宣传片\n"
            "核心产品卖点：旧值\n"
            "本片段卖点证明: 旧证明\n"
            "镜头创作要求：旧要求\n"
        )
        result = _scenario_provider_prompt(
            visual_prompt=f"{old_header}{old_header}\n0-15秒：完整剧情动作。",
            primary_selling_point="新卖点",
            segment_proof="新证明",
        )
        self.assertEqual(result.count("任务类型：产品宣传片"), 1)
        self.assertEqual(result.count("核心产品卖点：新卖点"), 1)
        self.assertEqual(result.count("本片段卖点证明：新证明"), 1)
        self.assertNotIn("旧值", result)
        self.assertNotIn("旧证明", result)
        self.assertIn("0-15秒：完整剧情动作。", result)

    def test_every_original_workflow_has_a_distinct_named_compiler(self) -> None:
        compilers = [compiler for _mode, compiler in WORKFLOW_ID_COMPILERS.values()]
        self.assertEqual(len(compilers), len(set(compilers)))
        self.assertEqual(
            {function.__name__ for function in compilers},
            {
                "compile_keyframe", "compile_direct", "compile_seedance2",
                "compile_mv_compat", "compile_seedance_mv",
                "compile_system_short_drama", "compile_short_drama_workflow",
                "compile_product_ad", "compile_cuti_product",
                "compile_scenario_product", "compile_libtv_product",
            },
        )

    def test_every_installed_workflow_matches_its_locked_cuti_source_contract(self) -> None:
        self.assertEqual(
            set(self.plugin._workflows),
            set(ORIGINAL_CUTI_WORKFLOW_CONTRACTS),
        )
        for workflow_id, workflow in self.plugin._workflows.items():
            with self.subTest(workflow_id=workflow_id):
                validate_original_cuti_workflow_contract(workflow)

    def test_same_id_and_mode_cannot_run_after_pipeline_contract_drift(self) -> None:
        original = self.plugin._workflows["workflow-direct-video"]
        drifted = WorkflowSpec(
            skill_name=original.skill_name,
            title=original.title,
            mode=original.mode,
            parameters=dict(original.parameters),
            pipeline=("atomic.text.generate", *original.pipeline),
            requires_keyframe=original.requires_keyframe,
            allowed_capabilities=original.allowed_capabilities,
            entrypoints=original.entrypoints,
            skill_dependencies=original.skill_dependencies,
            planning=original.planning,
        )
        with self.assertRaisesRegex(
            BuildPlanValidationError,
            "pipeline differs from the original Cuti contract",
        ):
            compile_skill_workflow(
                drifted,
                PluginContext(project_id="project-1", values={
                    "base_project_version_id": "version-1",
                    "source_artifacts": {},
                }),
                _spec("workflow-direct-video"),
            )

        plugin = type(self.plugin)({drifted.skill_name: drifted})
        view = plugin.describe_workflows()[0]
        self.assertFalse(view["available"])
        self.assertFalse(view["userSelectable"])
        self.assertEqual(view["executionKind"], "unavailable")
        self.assertIn("pipeline differs", view["unavailableReason"])
        self.assertEqual(
            {
                workflow_id: (mode, compiler.__name__)
                for workflow_id, (mode, compiler) in WORKFLOW_ID_COMPILERS.items()
            },
            {
                "workflow-keyframe-pipeline": ("keyframe_pipeline", "compile_keyframe"),
                "workflow-direct-video": ("direct_video", "compile_direct"),
                "seedance2": ("seedance2", "compile_seedance2"),
                "mv": ("mv", "compile_mv_compat"),
                "seedance-mv": ("seedance_mv", "compile_seedance_mv"),
                "workflow-short-drama": ("short_drama", "compile_system_short_drama"),
                "short-drama-workflow": (
                    "short_drama_workflow", "compile_short_drama_workflow",
                ),
                "product-ad-video": ("product_ad_video", "compile_product_ad"),
                "cuti-product-workflow": (
                    "cuti_product_workflow", "compile_cuti_product",
                ),
                "cuti-scenario-product-workflow": (
                    "cuti_scenario_product_workflow", "compile_scenario_product",
                ),
                "libtv-product-workflow": (
                    "libtv_product_workflow", "compile_libtv_product",
                ),
            },
        )

    def test_known_mode_cannot_act_as_an_implicit_default_compiler(self) -> None:
        context = PluginContext(project_id="project-1", values={
            "base_project_version_id": "version-1", "source_artifacts": {},
        })
        copied_mode = WorkflowSpec(
            skill_name="third-party-lookalike",
            title="Not a Cuti workflow",
            mode="direct_video",
        )
        with self.assertRaisesRegex(
            BuildPlanValidationError,
            "has no locked original Cuti contract",
        ):
            compile_skill_workflow(
                copied_mode,
                context,
                _spec("third-party-lookalike"),
            )

    def test_workflow_id_cannot_change_its_compiler_mode(self) -> None:
        context = PluginContext(project_id="project-1", values={
            "base_project_version_id": "version-1", "source_artifacts": {},
        })
        wrong_mode = WorkflowSpec(
            skill_name="workflow-direct-video",
            title="Direct Video",
            mode="keyframe_pipeline",
        )
        with self.assertRaisesRegex(
            BuildPlanValidationError,
            "original Cuti contract requires direct_video",
        ):
            compile_skill_workflow(
                wrong_mode,
                context,
                _spec("workflow-direct-video"),
            )

    def test_every_original_cuti_workflow_fails_closed_without_a_dedicated_compiler(self) -> None:
        views = {item["id"]: item for item in self.plugin.describe_workflows()}
        expected = {
            "mv": "compile_mv_compat",
            "seedance2": "compile_seedance2",
            "seedance-mv": "compile_seedance_mv",
            "short-drama-workflow": "compile_short_drama_workflow",
            "workflow-short-drama": "compile_system_short_drama",
            "workflow-keyframe-pipeline": "compile_keyframe",
            "workflow-direct-video": "compile_direct",
            "product-ad-video": "compile_product_ad",
            "cuti-product-workflow": "compile_cuti_product",
            "cuti-scenario-product-workflow": "compile_scenario_product",
            "libtv-product-workflow": "compile_libtv_product",
        }
        for workflow_id, compiler in expected.items():
            self.assertIn(workflow_id, views)
            self.assertTrue(views[workflow_id]["available"], workflow_id)
            self.assertEqual(views[workflow_id]["executionKind"], "dedicated_compiler")
            self.assertEqual(views[workflow_id]["compiler"], compiler)
        for workflow_id in ("open-montage", "ink-press-product-workflow"):
            self.assertIn(workflow_id, views)
            self.assertFalse(views[workflow_id]["available"])
            self.assertEqual(views[workflow_id]["executionKind"], "unavailable")
            self.assertIsNone(views[workflow_id]["compiler"])

        self.assertIn(
            "seedance2",
            views["short-drama-workflow"]["skillDependencies"],
        )
        self.assertIn(
            "video-shotcraft",
            views["ink-press-product-workflow"]["skillDependencies"],
        )

    def test_original_cuti_workflow_instruction_bodies_are_preserved(self) -> None:
        """Planning frontmatter may grow; the original Cuti instructions may not drift."""
        expected_hashes = {
            "cuti-product-workflow": "141aa613a932a7552e6c6ea62fd88ad7ceec35c61ab7dff407eb138db7b68fb7",
            "cuti-scenario-product-workflow": "46f179443bbf2377dfe238a519b15cfff54adfee239a6b0bce61a5a1bc9a3996",
            "ink-press-product-workflow": "c4c2e3dd19eb56b6f7af4669fd8682fce9fd752aa37223067bba36c5dbd15da1",
            "libtv-product-workflow": "8a41ee03d48eda29d1b574c9b512f9b259f216f8cce9be69ffd8455c2736ee64",
            "open-montage": "fd107a14105e90c6f39daf98c645f509a94b1330ac1a420e91c655b5b596afbf",
            "product-ad-video": "250e1b246c6f3fc4560c97598f92d10209d4105e313598928fd8c31a43de2657",
            "seedance-mv": "91e42b047b6f9fb31ccc25599d3d91344f9690a9ae5d1200025b2e61ad2a6940",
            "seedance2": "ec4757af24cc91f48462b916c5bed5f5b576eae4dbe634ffb0decb1034de82dc",
            "short-drama-workflow": "d81a99f35a4fddf2772e1aa5c861ec7f53ec88690e136f3048523c68c8143c4c",
            "workflow-direct-video": "8dc9fd30fac2b7d4591ad3d62b080e3d7927c51089212a924d01c5518f01c634",
            "workflow-keyframe-pipeline": "9208a988b68f9772fa4e0c98c8dc1d1433995eda43a8f01973b54e40c04489ea",
            "workflow-short-drama": "57fa42cc68556e5a0c2ab27aa5a3399653e9bbf8bdc140614e488d63fe4203c4",
        }
        root = Path(__file__).resolve().parents[2] / "skills"
        skills = {path.parent.name: path for path in root.rglob("SKILL.md")}
        self.assertTrue(set(expected_hashes) <= set(skills))
        for workflow_id, expected_hash in expected_hashes.items():
            text = skills[workflow_id].read_text(encoding="utf-8")
            parts = text.split("---", 2)
            self.assertEqual(len(parts), 3, workflow_id)
            body = re.sub(r"[ \t]+(?=\n|$)", "", parts[2].strip())
            actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
            self.assertEqual(actual, expected_hash, workflow_id)

    async def test_every_available_workflow_has_its_own_plan_contract(self) -> None:
        installed = set(self.plugin._workflows)
        plans = {
            "workflow-keyframe-pipeline": await self._compile("workflow-keyframe-pipeline"),
            "workflow-direct-video": await self._compile("workflow-direct-video"),
            "workflow-short-drama": await self._compile("workflow-short-drama"),
        }
        if "short-drama-workflow" in installed:
            plans["short-drama-workflow"] = await self._compile(
                "short-drama-workflow",
                _spec("short-drama-workflow", segment_seconds=15),
            )
        if "seedance2" in installed:
            plans["seedance2"] = await self._compile("seedance2")
        if "seedance-mv" in installed:
            plans["seedance-mv"] = await self._compile(
                "seedance-mv", _spec("seedance-mv", bgm_prompt="test song"),
            )
        mv_spec = _spec("mv", bgm_prompt="test song", subtitles=True)
        plans["mv"] = await self._compile(
            "mv",
            mv_spec.model_copy(update={
                "providers": mv_spec.providers.model_copy(update={"video": "minimax-h3"}),
            }),
        )
        for workflow_id in (
            "product-ad-video", "cuti-product-workflow",
            "cuti-scenario-product-workflow", "libtv-product-workflow",
        ):
            if workflow_id not in installed:
                continue
            product_spec = (
                _scenario_spec(self.source.artifact_id)
                if workflow_id == "cuti-scenario-product-workflow"
                else _spec(
                    workflow_id,
                    segment_seconds=15 if workflow_id == "cuti-product-workflow" else 5,
                    source_asset_ids=[self.source.artifact_id],
                )
            )
            plans[workflow_id] = await self._compile(workflow_id, product_spec)

        snapshots = {name: _signature(plan) for name, plan in plans.items()}
        self.assertIn("shot-1-keyframe:create:atomic.image.generate", snapshots["workflow-keyframe-pipeline"])
        keyframe = plans["workflow-keyframe-pipeline"]
        keyframe_by_id = {item.step_id: item for item in keyframe.items}
        self.assertNotIn("script", keyframe_by_id)
        self.assertIn("scene-main-location-reference", keyframe_by_id)
        self.assertEqual(
            keyframe_by_id["shot-1-video"].parameters["start_image_from_step"],
            "shot-1-keyframe",
        )
        self.assertFalse({
            "media.tts", "atomic.music.generate", "subtitle.compose", "media.subtitle.burn",
        } & {item.capability for item in keyframe.items})
        self.assertNotIn("shot-1-keyframe:create:atomic.image.generate", snapshots["workflow-direct-video"])
        self.assertNotIn("script:create:runtime.artifact.persist", snapshots["workflow-direct-video"])
        self.assertNotIn("storyboard:create:runtime.artifact.persist", snapshots["workflow-direct-video"])
        self.assertIn("outline:create:runtime.artifact.persist", snapshots["workflow-short-drama"])
        self.assertIn("scenes:create:runtime.artifact.persist", snapshots["workflow-short-drama"])
        self.assertIn("shots:create:runtime.artifact.persist", snapshots["workflow-short-drama"])
        system_short = plans["workflow-short-drama"]
        self.assertNotIn("script", {item.step_id for item in system_short.items})
        self.assertFalse(any(item.output_artifact_type == "keyframe" for item in system_short.items))
        self.assertFalse(any(item.capability == "media.extract_frame" for item in system_short.items))
        self.assertTrue(all(
            item.parameters["generation_mode"] == "t2v" and not item.skill_ids
            for item in system_short.items if item.output_artifact_type == "video_clip"
        ))
        if "short-drama-workflow" in snapshots:
            self.assertNotIn("shot-1-tail:create:media.extract_frame", snapshots["short-drama-workflow"])
        self.assertIn("character-hero-reference:create:atomic.image.generate", snapshots["mv"])
        self.assertIn("music:create:suno.generate", snapshots["mv"])
        self.assertIn("music-analysis:create:media.audio_analyze", snapshots["mv"])
        self.assertIn("music-cut:create:media.audio_cut", snapshots["mv"])
        self.assertNotIn("shot-1-audio:create:media.audio.trim", snapshots["mv"])
        self.assertNotIn("research:create:runtime.artifact.persist", snapshots["mv"])
        self.assertNotIn("script:create:runtime.artifact.persist", snapshots["mv"])
        self.assertNotIn("storyboard:create:runtime.artifact.persist", snapshots["mv"])
        self.assertIn("mv-shot-plan:create:runtime.artifact.persist", snapshots["mv"])
        self.assertNotIn("music-window:create:media.audio.trim", snapshots["mv"])
        self.assertIn("mixed-video:create:media.mix_audio", snapshots["mv"])
        self.assertIn("transcription:create:media.transcribe", snapshots["mv"])
        self.assertIn("final-video:create:media.hyperframes_caption", snapshots["mv"])
        self.assertNotIn("shot-1-tail:create:media.extract_frame", snapshots["mv"])
        mv_clips = [
            item for item in plans["mv"].items
            if item.capability == "api.provider.generate"
        ]
        self.assertTrue(all(item.parameters["model"] == "minimax-h3" for item in mv_clips))

        if "seedance-mv" in plans:
            seedance_mv = plans["seedance-mv"]
            seedance_mv_sig = snapshots["seedance-mv"]
            self.assertIn("music:create:atomic.music.generate", seedance_mv_sig)
            self.assertIn("music-analysis:create:media.audio_analyze", seedance_mv_sig)
            self.assertIn("music-window:create:media.audio.trim", seedance_mv_sig)
            self.assertIn("shot-1-audio:create:media.audio.trim", seedance_mv_sig)
            self.assertIn("shot-2-audio:create:media.audio.trim", seedance_mv_sig)
            self.assertIn("shot-1-tail-for-2:create:media.extract_frame", seedance_mv_sig)
            self.assertNotIn("suno.generate", {item.capability for item in seedance_mv.items})
            seedance_mv_clips = [
                item for item in seedance_mv.items
                if item.output_artifact_type == "video_clip"
            ]
            self.assertTrue(all(item.capability == "api.provider.generate" for item in seedance_mv_clips))
            self.assertTrue(all(item.parameters["model"] == "doubao-seedance-2-0" for item in seedance_mv_clips))
            self.assertTrue(all("audio_segment_index" not in item.parameters for item in seedance_mv_clips))
            self.assertTrue(all("seedance2" not in item.skill_ids for item in seedance_mv.items))
        for workflow_id in (
            "product-ad-video", "cuti-product-workflow",
            "cuti-scenario-product-workflow", "libtv-product-workflow",
        ):
            if workflow_id not in snapshots:
                continue
            self.assertNotIn(
                "shot-1-product-validation:validate:cuti.continuity.validate",
                snapshots[workflow_id],
            )

        cuti_product = plans.get("cuti-product-workflow")
        if cuti_product is not None:
            by_id = {item.step_id: item for item in cuti_product.items}
            self.assertEqual(
                by_id["product-360-reference"].parameters["artifact_role"],
                "product_360_reference",
            )
            self.assertEqual(by_id["product-360-reference"].parameters["model"], "gpt-image-2")
            clips = [item for item in cuti_product.items if item.output_artifact_type == "video_clip"]
            self.assertTrue(all(item.capability == "api.provider.generate" for item in clips))
            self.assertTrue(all(item.parameters["duration"] == 15 for item in clips))
            self.assertTrue(all(item.parameters["model"] == "seedance-2.5" for item in clips))
            self.assertTrue(all(item.parameters["generation_mode"] == "t2v" for item in clips))
            self.assertTrue(all(item.parameters["generate_audio"] is True for item in clips))
            self.assertTrue(all("video-director" not in item.skill_ids for item in clips))
            self.assertTrue(all(
                item.execution_group == "cuti-product-segments"
                and item.max_parallelism == 2
                for item in clips
            ))
            self.assertFalse({
                "media.tts", "atomic.music.generate", "suno.generate", "media.subtitle.burn",
            } & {item.capability for item in cuti_product.items})

        libtv = plans.get("libtv-product-workflow")
        if libtv is not None:
            by_id = {item.step_id: item for item in libtv.items}
            for required in (
                "product-intake", "reference-role-map", "product-identity-contract",
                "creative-system", "timed-sequence-plan", "shot-reference-packages",
            ):
                self.assertIn(required, by_id)
            self.assertNotIn("storyboard", by_id)
            self.assertFalse(any(item.output_artifact_type == "keyframe" for item in libtv.items))
            self.assertNotIn("atomic.image.generate", {item.capability for item in libtv.items})
            clips = [item for item in libtv.items if item.output_artifact_type == "video_clip"]
            self.assertTrue(all(item.capability == "api.provider.generate" for item in clips))
            self.assertTrue(all(
                item.parameters["model"] == "doubao-seedance-2-0-260128"
                for item in clips
            ))
            self.assertTrue(all(item.parameters["generation_mode"] == "reference_to_video" for item in clips))
            self.assertTrue(all("start_image_from_step" not in item.parameters for item in clips))
            self.assertTrue(all(
                item.execution_group == "libtv-product-shots"
                and item.max_parallelism == 2
                for item in clips
            ))

        product_ad = plans.get("product-ad-video")
        if product_ad is not None:
            product_by_id = {item.step_id: item for item in product_ad.items}
            self.assertIn("commercial-brief", product_by_id)
            self.assertNotIn("script", product_by_id)
            self.assertNotIn("characters", product_by_id)
            self.assertNotIn("storyboard", product_by_id)
            product_clips = [
                item for item in product_ad.items
                if item.output_artifact_type == "video_clip"
            ]
            self.assertTrue(all(item.capability == "atomic.video.generate" for item in product_clips))
            self.assertTrue(all(item.parameters["generation_mode"] == "i2v" for item in product_clips))
            self.assertTrue(all(not item.skill_ids for item in product_clips))
            self.assertTrue(all(item.parameters["generate_audio"] is False for item in product_clips))
            self.assertNotIn("atomic.image.generate", {item.capability for item in product_ad.items})

        scenario = plans.get("cuti-scenario-product-workflow")
        if scenario is not None:
            by_id = {item.step_id: item for item in scenario.items}
            roles = {
                by_id["character-setting-reference"].parameters["artifact_role"],
                by_id["scene-setting-reference"].parameters["artifact_role"],
                by_id["product-setting-reference"].parameters["artifact_role"],
            }
            self.assertEqual(roles, {
                "character_setting_reference",
                "scene_setting_reference",
                "product_setting_reference",
            })
            self.assertTrue(all(
                by_id[step].parameters["model"] == "gpt-image-2"
                and "scenario-script" in by_id[step].depends_on
                for step in (
                    "character-setting-reference",
                    "scene-setting-reference",
                    "product-setting-reference",
                )
            ))
            self.assertTrue(all(
                by_id[step].execution_group == "scenario-setting-references"
                and by_id[step].max_parallelism == 3
                for step in (
                    "character-setting-reference",
                    "scene-setting-reference",
                    "product-setting-reference",
                )
            ))
            self.assertEqual(
                set(by_id["validate-setting-references"].depends_on),
                {
                    "character-setting-reference",
                    "scene-setting-reference",
                    "product-setting-reference",
                },
            )
            self.assertEqual(
                by_id["validate-setting-references"].capability,
                "cuti.continuity.validate",
            )
            scene_prompt = by_id["scene-setting-reference"].parameters["prompt"]
            self.assertIn("unoccupied environment-only", scene_prompt)
            self.assertIn("雨夜的空置排练室", scene_prompt)
            self.assertNotIn("Hero", scene_prompt)
            self.assertNotIn("black hair", scene_prompt)
            self.assertNotIn("headphones", scene_prompt)
            self.assertNotIn("嘈杂环境中依然清晰聆听", scene_prompt)
            self.assertNotIn("character", scene_prompt.casefold())
            self.assertNotIn("product", scene_prompt.casefold())
            self.assertNotIn("bottle", scene_prompt.casefold())
            character_prompt = by_id["character-setting-reference"].parameters["prompt"]
            self.assertIn("Hero", character_prompt)
            self.assertIn("black hair", character_prompt)
            self.assertIn("仅生成角色设定图，不得包含产品或场景。", character_prompt)
            product_prompt = by_id["product-setting-reference"].parameters["prompt"]
            self.assertIn("uploaded product truth reference", product_prompt)
            self.assertIn("仅生成产品设定图，不得包含人物或使用场景。", product_prompt)
            scenario_clips = [
                item for item in scenario.items
                if item.output_artifact_type == "video_clip"
            ]
            self.assertTrue(all(item.capability == "api.provider.generate" for item in scenario_clips))
            self.assertTrue(all(item.parameters["duration"] == 15 for item in scenario_clips))
            self.assertTrue(all(item.parameters["generate_audio"] is True for item in scenario_clips))
            self.assertTrue(all(item.parameters["generation_mode"] == "t2v" for item in scenario_clips))
            self.assertTrue(all("start_image_from_step" not in item.parameters for item in scenario_clips))
            self.assertTrue(all(
                "validate-setting-references" in item.depends_on
                for item in scenario_clips
            ))
            self.assertTrue(all(
                item.execution_group == "scenario-product-segments"
                and item.max_parallelism == 2
                for item in scenario_clips
            ))
            self.assertTrue(all("产品宣传片" in item.parameters["prompt"] for item in scenario_clips))
            self.assertTrue(all(
                item.parameters["prompt"].count("任务类型：产品宣传片") == 1
                and item.parameters["prompt"].count("核心产品卖点：") == 1
                and item.parameters["prompt"].count("本片段卖点证明：") == 1
                for item in scenario_clips
            ))
            self.assertNotIn("media.tts", {item.capability for item in scenario.items})
            self.assertNotIn("media.subtitle.burn", {item.capability for item in scenario.items})

        seedance = plans.get("seedance2")
        if seedance is not None:
            forbidden = {
                "media.tts", "atomic.music.generate", "media.subtitle.compose",
                "media.subtitle.burn",
            }
            self.assertFalse(forbidden & {item.capability for item in seedance.items})
            self.assertFalse(any(item.output_artifact_type == "keyframe" for item in seedance.items))
            clips = [item for item in seedance.items if item.capability == "atomic.video.generate"]
            self.assertTrue(all(
                item.parameters["model"] == "doubao-seedance-2-0-260128"
                for item in clips
            ))
            self.assertTrue(all(not item.skill_ids for item in clips))
            self.assertNotIn("characters:create:runtime.artifact.persist", snapshots["seedance2"])
            self.assertNotIn(
                "character-hero-reference:create:atomic.image.generate",
                snapshots["seedance2"],
            )
            self.assertNotIn("atomic.image.generate", {item.capability for item in seedance.items})

        if "short-drama-workflow" in plans:
            parallel_clips = [
                item for item in plans["short-drama-workflow"].items
                if item.capability == "atomic.video.generate"
            ]
            self.assertTrue(all(item.parameters["model"] == "seedance-2.5" for item in parallel_clips))
            self.assertNotIn("shot-1-video", parallel_clips[1].depends_on)
            self.assertNotIn("shot-1-tail", parallel_clips[1].depends_on)
            self.assertTrue(all(
                item.execution_group == "short-drama-segments"
                and item.max_parallelism == len(parallel_clips)
                for item in parallel_clips
            ))

    async def test_seedance2_uses_uploaded_images_as_shot_refs(self) -> None:
        spec = _spec("seedance2", source_asset_ids=[self.source.artifact_id])
        plan = compile_seedance2(
            WorkflowSpec(skill_name="seedance2", title="seedance2", mode="seedance2"),
            PluginContext(project_id="project-1", values={
                "base_project_version_id": "version-1",
                "source_artifacts": {self.source.artifact_id: self.source},
            }),
            spec,
        )
        by_id = {item.step_id: item for item in plan.items}
        self.assertNotIn("characters", by_id)
        self.assertNotIn("character-hero-reference", by_id)
        self.assertNotIn("atomic.image.generate", {item.capability for item in plan.items})
        clips = [item for item in plan.items if item.capability == "atomic.video.generate"]
        self.assertTrue(clips)
        self.assertEqual(clips[0].parameters["reference_from_steps"], ["source-1"])

    async def test_seedance2_preserves_uploaded_video_and_audio_references(self) -> None:
        video = MediaArtifactVersion(
            id="source-video-version", artifact_id="source:video",
            project_id="project-1", type="source_video",
            uri="https://example.test/motion.mp4",
        )
        audio = MediaArtifactVersion(
            id="source-audio-version", artifact_id="source:audio",
            project_id="project-1", type="source_audio",
            uri="https://example.test/rhythm.mp3",
        )
        spec = _spec(
            "seedance2",
            source_asset_ids=[video.artifact_id, audio.artifact_id],
        )
        plan = compile_seedance2(
            WorkflowSpec(skill_name="seedance2", title="seedance2", mode="seedance2"),
            PluginContext(project_id="project-1", values={
                "base_project_version_id": "version-1",
                "source_artifacts": {
                    video.artifact_id: video,
                    audio.artifact_id: audio,
                },
            }),
            spec,
        )
        clips = [item for item in plan.items if item.output_artifact_type == "video_clip"]
        self.assertTrue(clips)
        self.assertTrue(all(
            item.parameters["video_reference_from_steps"] == ["source-1"]
            and item.parameters["audio_reference_from_steps"] == ["source-2"]
            and item.parameters["generation_mode"] == "reference_to_video"
            for item in clips
        ))

        direct_spec = spec.model_copy(update={"workflow_id": "workflow-direct-video"})
        direct = await self.plugin.compile_build_plan(
            PluginContext(project_id="project-1", values={
                "base_project_version_id": "version-1",
                "source_artifacts": {
                    video.artifact_id: video,
                    audio.artifact_id: audio,
                },
            }),
            direct_spec,
        )
        direct_clips = [
            item for item in direct.items if item.output_artifact_type == "video_clip"
        ]
        self.assertTrue(all(
            item.parameters["video_reference_from_steps"] == ["source-1"]
            and item.parameters["audio_reference_from_steps"] == ["source-2"]
            and item.parameters["generation_mode"] == "reference_to_video"
            for item in direct_clips
        ))

    async def test_seedance2_tail_continuation_is_explicit_not_default(self) -> None:
        workflow = WorkflowSpec(skill_name="seedance2", title="seedance2", mode="seedance2")
        context = PluginContext(project_id="project-1", values={
            "base_project_version_id": "version-1", "source_artifacts": {},
        })
        cut_plan = compile_seedance2(workflow, context, _spec("seedance2"))
        self.assertFalse(any(item.capability == "media.extract_frame" for item in cut_plan.items))
        continuous = _spec("seedance2")
        continuous = continuous.model_copy(update={
            "shots": [
                continuous.shots[0],
                continuous.shots[1].model_copy(update={"transition": "continuous"}),
            ],
        })
        continuous_plan = compile_seedance2(workflow, context, continuous)
        tails = [item for item in continuous_plan.items if item.capability == "media.extract_frame"]
        self.assertEqual([item.step_id for item in tails], ["shot-1-tail-for-2"])

    async def test_scene_based_workflows_refuse_a_generic_runtime_location(self) -> None:
        for workflow_id in (
            "workflow-keyframe-pipeline",
            "workflow-short-drama",
            "short-drama-workflow",
        ):
            if workflow_id not in self.plugin._workflows:
                continue
            spec = _spec(
                workflow_id,
                segment_seconds=15 if workflow_id == "short-drama-workflow" else 5,
            ).model_copy(update={"workflow_parameters": {}})
            with self.assertRaisesRegex(
                BuildPlanValidationError,
                "requires workflow_parameters.scenes",
            ):
                await self._compile(workflow_id, spec)

    async def test_scenario_product_fails_closed_without_execution_contract(self) -> None:
        if "cuti-scenario-product-workflow" not in self.plugin._workflows:
            self.skipTest("scenario product workflow is not installed")
        missing_selling_point = _spec(
            "cuti-scenario-product-workflow",
            segment_seconds=15,
            source_asset_ids=[self.source.artifact_id],
        )
        with self.assertRaisesRegex(BuildPlanValidationError, "primary_selling_point"):
            await self._compile(
                "cuti-scenario-product-workflow",
                missing_selling_point,
            )

        invalid_duration = _scenario_spec(self.source.artifact_id).model_copy(update={
            "target_duration_seconds": 10,
            "shots": [
                shot.model_copy(update={"duration_seconds": 5})
                for shot in _scenario_spec(self.source.artifact_id).shots
            ],
        })
        with self.assertRaisesRegex(BuildPlanValidationError, "15-second"):
            await self._compile("cuti-scenario-product-workflow", invalid_duration)

        contaminated = _scenario_spec(self.source.artifact_id).model_copy(deep=True)
        contaminated.workflow_parameters.pop("scenario", None)
        with self.assertRaisesRegex(
            BuildPlanValidationError,
            "scene_setting_prompt must describe the empty environment only",
        ):
            await self._compile("cuti-scenario-product-workflow", contaminated)

    async def test_scenario_product_copies_proof_from_canonical_prompt_header(self) -> None:
        if "cuti-scenario-product-workflow" not in self.plugin._workflows:
            self.skipTest("scenario product workflow is not installed")
        spec = _scenario_spec(self.source.artifact_id)
        parameters = dict(spec.workflow_parameters)
        parameters.pop("segment_proofs")
        shots = [
            shot.model_copy(update={
                "visual_prompt": (
                    "任务类型：产品宣传片\n"
                    "核心产品卖点：嘈杂环境中依然清晰聆听\n"
                    f"本片段卖点证明：第{index}段可见因果证明\n"
                    "0-15秒完整动作。"
                ),
            })
            for index, shot in enumerate(spec.shots, start=1)
        ]
        plan = await self._compile(
            "cuti-scenario-product-workflow",
            spec.model_copy(update={"workflow_parameters": parameters, "shots": shots}),
        )
        clips = [item for item in plan.items if item.output_artifact_type == "video_clip"]
        self.assertEqual(
            [item.parameters["segment_proof"] for item in clips],
            ["第1段可见因果证明", "第2段可见因果证明"],
        )
        self.assertTrue(all(
            item.parameters["prompt"].count("任务类型：产品宣传片") == 1
            and item.parameters["prompt"].count("核心产品卖点：") == 1
            and item.parameters["prompt"].count("本片段卖点证明：") == 1
            for item in clips
        ))

    async def test_product_ad_rejects_non_image_source(self) -> None:
        if "product-ad-video" not in self.plugin._workflows:
            self.skipTest("product ad workflow is not installed")
        audio = MediaArtifactVersion(
            id="source-audio-version", artifact_id="source:audio",
            project_id="project-1", type="source_audio",
            uri="https://example.test/music.mp3",
        )
        spec = _spec("product-ad-video", source_asset_ids=[audio.artifact_id])
        with self.assertRaisesRegex(BuildPlanValidationError, "uploaded product image"):
            await self.plugin.compile_build_plan(
                PluginContext(project_id="project-1", values={
                    "base_project_version_id": "version-1",
                    "source_artifacts": {audio.artifact_id: audio},
                }),
                spec,
            )

    async def test_unavailable_and_unknown_workflows_fail_closed(self) -> None:
        views = {item["id"]: item for item in self.plugin.describe_workflows()}
        for workflow_id in ("open-montage", "ink-press-product-workflow"):
            if workflow_id in views:
                with self.assertRaises(BuildPlanValidationError):
                    await self._compile(workflow_id)
                self.assertFalse(views[workflow_id]["available"])
                self.assertFalse(views[workflow_id]["userSelectable"])
            else:
                with self.assertRaises(LookupError):
                    await self._compile(workflow_id)
        if "open-montage" in views:
            self.assertEqual(
                views["open-montage"]["missingCapabilities"],
                ["open_montage.tool.invoke"],
            )

    async def test_every_available_workflow_completes_with_provider_mock(self) -> None:
        runtime = VideoBuildRuntime(skill_runtime=self.skills)
        await runtime.plugins.load_directories([
            Path(__file__).resolve().parents[2] / "plugins",
        ])
        await load_workflow_skills(runtime.plugins, self.skills)
        executable_capabilities = {
            capability
            for loaded in runtime.plugins.loaded
            for capability in loaded.manifest.contributions.capabilities
        }

        async def validate(*, build_id, artifact):
            return [ValidationResult(
                project_id=artifact.project_id,
                build_id=build_id,
                artifact_version_id=artifact.id,
                validator_id="provider-mock",
                passed=True,
            )]

        runtime.validate_artifact = validate
        installed = set(self.plugin._workflows)
        workflow_specs = {
            "workflow-keyframe-pipeline": _spec("workflow-keyframe-pipeline"),
            "workflow-direct-video": _spec("workflow-direct-video"),
            "workflow-short-drama": _spec("workflow-short-drama"),
            "mv": _spec("mv", bgm_prompt="test song"),
        }
        if "short-drama-workflow" in installed:
            workflow_specs["short-drama-workflow"] = _spec(
                "short-drama-workflow", segment_seconds=15,
            )
        if "seedance2" in installed:
            workflow_specs["seedance2"] = _spec("seedance2")
        if "seedance-mv" in installed:
            workflow_specs["seedance-mv"] = _spec(
                "seedance-mv", bgm_prompt="test song",
            )
        for workflow_id in (
            "product-ad-video", "cuti-product-workflow",
            "cuti-scenario-product-workflow", "libtv-product-workflow",
        ):
            if workflow_id not in installed:
                continue
            workflow_specs[workflow_id] = (
                _scenario_spec("source:product")
                if workflow_id == "cuti-scenario-product-workflow"
                else _spec(
                    workflow_id,
                    segment_seconds=15 if workflow_id == "cuti-product-workflow" else 5,
                    source_asset_ids=["source:product"],
                )
            )

        for index, (workflow_id, spec) in enumerate(workflow_specs.items(), start=1):
            project, version = await runtime.create_project(
                user_id="user-1", title=f"Mock {workflow_id}",
            )
            retained_source = await runtime.add_artifact(MediaArtifactVersion(
                artifact_id="source:retained",
                project_id=project.id,
                type="source_image",
                uri="https://provider-mock.test/retained.png",
            ))
            if spec.source_asset_ids:
                await runtime.add_artifact(MediaArtifactVersion(
                    artifact_id="source:product",
                    project_id=project.id,
                    type="source_image",
                    uri="https://provider-mock.test/product.png",
                ))
            project = await runtime.repo.get_project(project.id)
            plan = await runtime.plan_project(
                project_id=project.id,
                base_project_version_id=project.current_version_id,
                video_spec=spec,
                idempotency_key=f"mock-plan-{index}",
            )
            missing_handlers = sorted({
                item.capability for item in plan.items
                if (
                    item.action == "create"
                    and item.capability
                    and item.capability not in executable_capabilities
                )
            })
            self.assertEqual(
                missing_handlers, [],
                f"{workflow_id} compiled capabilities with no Runtime handler",
            )
            build = await runtime.start_build(
                project_id=project.id,
                plan_id=plan.id,
                base_project_version_id=project.current_version_id,
                idempotency_key=f"mock-build-{index}",
            )
            completed, committed = await runtime.execute_build(
                project_id=project.id,
                build_id=build.id,
                executor=ProviderMockExecutor(),
            )
            self.assertEqual(completed.status, "completed", workflow_id)
            self.assertIn(
                retained_source.artifact_id,
                committed.selections,
                f"{workflow_id} dropped an existing source artifact",
            )
