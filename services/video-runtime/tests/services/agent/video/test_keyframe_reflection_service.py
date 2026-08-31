"""
关键帧反思服务单元测试

测试内容：
1. VLM 响应解析（parse_vlm_response）
2. 单帧反思 reflect_single_keyframe — 关键覆盖参考图传参链路
3. 批量反思 reflect_all_keyframes — 数量、跳过、auto_regenerate 控制
4. 重新生成 regenerate_single_keyframe — reuse_ref_image_urls 复用逻辑（最新成功版本）
5. 数据模型

⚠️ reflect_single_keyframe 走 keyframe_reflection_stage deep-agent（export + generate_keyframe_reflection_via_deep_agent）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage

from app.services.agent.video.keyframe_reflection_service import (
    reflect_single_keyframe,
    reflect_all_keyframes,
    regenerate_single_keyframe,
    parse_vlm_response,
    CharacterConsistencyIssue,
    ConsistencyAnalysisResult,
    SingleKeyframeReflectionResult,
)
from app.models.video_state import (
    KeyframeVersion,
    DetailedShot,
    CharacterProfile,
    VisualElementType,
    CharacterImageInfo,
)
from app.schemas.video_llm import (
    KeyframeReflectionVlmStructuredOutput,
    KeyframeReflectionVlmIssue,
)
from app.contracts.artifacts.keyframe_reflection import KeyframeReflectionArtifact, ReflectionItemDraft


def _reflection_artifact(
    *,
    shot_number: int = 1,
    needs_regeneration: bool = False,
    improved_description: str = "",
    issues: List[str] = None,
) -> KeyframeReflectionArtifact:
    return KeyframeReflectionArtifact(
        results=[
            ReflectionItemDraft(
                shot_number=shot_number,
                needs_regeneration=needs_regeneration,
                issues=issues or [],
                analysis_summary="ok",
                improved_description=improved_description or None,
            )
        ]
    )


# ==================== 测试 fixtures ====================


def create_mock_keyframe(
    shot_number: int = 1,
    keyframe_uuid: str = "kf-1-uuid",
    keyframe_url: str = "",
) -> KeyframeVersion:
    """创建模拟关键帧版本。"""
    return KeyframeVersion(
        version_id=f"kfv-{shot_number}-uuid",
        shot_number=shot_number,
        keyframe_url=keyframe_url or f"https://example.com/keyframe_{shot_number}.webp",
        t2i_prompt=f"Test prompt for shot {shot_number}",
        provider="gemini",
        keyframe_uuid=keyframe_uuid,
    )


def create_mock_shot(
    shot_number: int = 1,
    character_ids: List[str] = None,
    uuid_val: str = None,
) -> DetailedShot:
    """创建模拟镜头（DetailedShot 必填字段：shot_number / duration / character_ids）。"""
    return DetailedShot(
        uuid=uuid_val or f"shot-{shot_number}-uuid",
        shot_number=shot_number,
        duration=3.0,
        scene_description=f"Test scene description for shot {shot_number}",
        character_ids=character_ids if character_ids is not None else ["char-1-uuid"],
    )


def create_mock_character(char_id: str = "char-1-uuid", name: str = "测试角色") -> CharacterProfile:
    """创建模拟角色档案。"""
    return CharacterProfile(
        id=char_id,
        type=VisualElementType.CHARACTER,
        name=name,
        description=f"Description of {name}",
        appearance="黑色长发，穿着白色衬衫",
        personality="友善、开朗",
    )


def make_structured_output(
    needs_regeneration: bool = False,
    issues: List[KeyframeReflectionVlmIssue] = None,
    improved_description: str = "",
) -> KeyframeReflectionVlmStructuredOutput:
    return KeyframeReflectionVlmStructuredOutput(
        needs_regeneration=needs_regeneration,
        issues=issues or [],
        analysis_summary="ok",
        improved_description=improved_description,
        improvement_points=[],
    )


# ==================== VLM 响应解析测试 ====================


class TestParseVlmResponse:
    """VLM 响应解析测试（保留原有用例，覆盖 fallback 路径）。"""

    def test_parse_valid_json_response(self):
        response = '''
        ```json
        {
            "needs_regeneration": true,
            "issues": [
                {
                    "character_id": "char-1",
                    "character_name": "角色A",
                    "issue_type": "hair",
                    "description": "发色不一致",
                    "severity": "high"
                }
            ],
            "analysis_summary": "发现角色一致性问题",
            "improved_description": "优化后的描述",
            "improvement_points": ["修正发色"]
        }
        ```
        '''
        result = parse_vlm_response(response, shot_number=1, shot_uuid="shot-1-uuid")
        assert result.needs_regeneration is True
        assert len(result.issues) == 1
        assert result.issues[0].issue_type == "hair"
        assert result.improved_description == "优化后的描述"

    def test_parse_no_regeneration_needed(self):
        response = '''
        {
            "needs_regeneration": false,
            "issues": [],
            "analysis_summary": "角色一致性良好",
            "improved_description": "",
            "improvement_points": []
        }
        '''
        result = parse_vlm_response(response, shot_number=2, shot_uuid="shot-2-uuid")
        assert result.needs_regeneration is False
        assert len(result.issues) == 0

    def test_parse_invalid_json(self):
        result = parse_vlm_response("这不是有效的 JSON 响应", shot_number=3, shot_uuid="shot-3-uuid")
        assert result.shot_number == 3
        assert result.needs_regeneration is False

    def test_parse_issue_with_null_character_id_and_name(self):
        """LLM 偶尔会把 character_id/character_name 返回 null，应能正确兼容。"""
        response = '''
        {
            "needs_regeneration": true,
            "issues": [
                {
                    "character_id": null,
                    "character_name": null,
                    "issue_type": "missing",
                    "description": "镜头里看不到对应角色",
                    "severity": "high"
                }
            ],
            "improved_description": "确保角色出现在画面中"
        }
        '''
        result = parse_vlm_response(response, shot_number=5, shot_uuid="shot-5-uuid")
        assert result.needs_regeneration is True
        assert len(result.issues) == 1
        assert result.issues[0].character_id == ""
        assert result.issues[0].character_name == ""
        assert result.issues[0].shot_number == 5


# ==================== 单帧反思：参考图传参链路 ====================


class TestReflectSingleKeyframeRefImages:
    """重点覆盖：VLM 评审输入是否严格按 shot.character_ids 取参考图。"""

    @pytest.mark.asyncio
    async def test_only_shot_character_ids_are_used_as_ref_images(self):
        """ref_images 必须严格对齐 shot.character_ids，不能把不在镜头里的角色图也带进 VLM。"""
        keyframe = create_mock_keyframe(1)
        shot = create_mock_shot(1, character_ids=["c1", "c2"])
        char1 = create_mock_character("c1", "A")
        char2 = create_mock_character("c2", "B")
        char_not_in_shot = create_mock_character("c3", "C")
        characters = [char1, char2, char_not_in_shot]
        character_images = {
            "c1": "https://example.com/c1.jpg",
            "c2": "https://example.com/c2.jpg",
            "c3": "https://example.com/c3.jpg",  # 不在 shot.character_ids 中，绝对不应进入 ref_images
        }
        char_map = {c.id: c for c in characters}

        captured_items: List[Dict[str, Any]] = []

        def fake_export(**kwargs):
            captured_items.extend(kwargs.get("items") or [])
            return {"brief": "brief.json", "artifact_name": "keyframe_reflection.json"}

        async def fake_da(**kwargs):
            return _reflection_artifact(), []

        with patch(
            "app.services.agent.video.keyframe_reflection_stage.export_keyframe_reflection_inputs",
            side_effect=fake_export,
        ), patch(
            "app.services.agent.video.keyframe_reflection_stage.generate_keyframe_reflection_via_deep_agent",
            new=fake_da,
        ):
            result, vlm_msg = await reflect_single_keyframe(
                keyframe=keyframe,
                shot=shot,
                characters=characters,
                character_images=character_images,
                char_map=char_map,
                state={"detected_language": "zh"},
                auto_regenerate=False,
            )

        assert len(captured_items) == 1
        ref_images = captured_items[0].get("character_ref_images")
        assert isinstance(ref_images, list), "character_ref_images 必须传给 deep-agent export"
        assert len(ref_images) == 2, "只能包含 shot.character_ids 中的两个角色"
        urls = {item["url"] for item in ref_images}
        ids = {item["id"] for item in ref_images}
        assert urls == {"https://example.com/c1.jpg", "https://example.com/c2.jpg"}
        assert ids == {"c1", "c2"}
        assert "https://example.com/c3.jpg" not in urls
        assert "c3" not in ids
        indices = sorted(item["index"] for item in ref_images)
        assert indices == [0, 1]
        assert captured_items[0]["keyframe_url"] == keyframe.keyframe_url
        assert captured_items[0]["shot_number"] == 1
        assert captured_items[0]["shot_uuid"] == shot.uuid
        assert result.needs_regeneration is False

    @pytest.mark.asyncio
    async def test_missing_character_image_is_skipped_in_ref_images(self):
        """如果 character_images 字典里没有某 char_id（图缺失），不应放进 ref_images。"""
        keyframe = create_mock_keyframe(1)
        shot = create_mock_shot(1, character_ids=["c1", "c2"])
        char1 = create_mock_character("c1", "A")
        char2 = create_mock_character("c2", "B")
        characters = [char1, char2]
        # c2 缺图：不应进入 ref_images
        character_images = {"c1": "https://example.com/c1.jpg"}
        char_map = {c.id: c for c in characters}

        captured: List[Dict[str, Any]] = []

        def fake_export(**kwargs):
            captured.extend(kwargs.get("items") or [])
            return {"brief": "brief.json", "artifact_name": "keyframe_reflection.json"}

        async def fake_da(**kwargs):
            return _reflection_artifact(), []

        with patch(
            "app.services.agent.video.keyframe_reflection_stage.export_keyframe_reflection_inputs",
            side_effect=fake_export,
        ), patch(
            "app.services.agent.video.keyframe_reflection_stage.generate_keyframe_reflection_via_deep_agent",
            new=fake_da,
        ):
            await reflect_single_keyframe(
                keyframe=keyframe,
                shot=shot,
                characters=characters,
                character_images=character_images,
                char_map=char_map,
                state={},
                auto_regenerate=False,
            )

        ref_images = captured[0].get("character_ref_images")
        assert len(ref_images) == 1
        assert ref_images[0]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_shot_without_character_skips_vlm(self):
        """镜头无角色或关键帧无 URL 时，应跳过 VLM 调用并返回 needs_regeneration=False。"""
        keyframe = create_mock_keyframe(1)
        shot = create_mock_shot(1, character_ids=[])
        characters = [create_mock_character("c1", "A")]
        character_images = {"c1": "https://example.com/c1.jpg"}
        char_map = {c.id: c for c in characters}

        da_mock = AsyncMock()

        with patch(
            "app.services.agent.video.keyframe_reflection_stage.generate_keyframe_reflection_via_deep_agent",
            new=da_mock,
        ):
            result, vlm_msg = await reflect_single_keyframe(
                keyframe=keyframe,
                shot=shot,
                characters=characters,
                character_images=character_images,
                char_map=char_map,
                state={},
                auto_regenerate=False,
            )

        assert result.needs_regeneration is False
        assert vlm_msg is None
        da_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_keyframe_with_empty_url_skips_vlm(self):
        """keyframe.keyframe_url 为空时同样跳过 VLM，避免给模型空图。"""
        keyframe = create_mock_keyframe(1, keyframe_url="")
        # 强制覆盖默认 fallback URL
        keyframe.keyframe_url = ""
        shot = create_mock_shot(1, character_ids=["c1"])
        characters = [create_mock_character("c1", "A")]
        character_images = {"c1": "https://example.com/c1.jpg"}
        char_map = {c.id: c for c in characters}

        da_mock = AsyncMock()
        with patch(
            "app.services.agent.video.keyframe_reflection_stage.generate_keyframe_reflection_via_deep_agent",
            new=da_mock,
        ):
            result, vlm_msg = await reflect_single_keyframe(
                keyframe=keyframe,
                shot=shot,
                characters=characters,
                character_images=character_images,
                char_map=char_map,
                state={},
                auto_regenerate=False,
            )

        assert result.needs_regeneration is False
        assert vlm_msg is None
        da_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_vlm_says_needs_regeneration_without_auto_regenerate(self):
        """VLM 判断需要重生，但 auto_regenerate=False 时不应触发 regenerate_single_keyframe。"""
        keyframe = create_mock_keyframe(1)
        shot = create_mock_shot(1, character_ids=["c1"])
        characters = [create_mock_character("c1", "A")]
        character_images = {"c1": "https://example.com/c1.jpg"}
        char_map = {c.id: c for c in characters}

        async def fake_da(**kwargs):
            return _reflection_artifact(
                needs_regeneration=True,
                improved_description="修正后的描述",
                issues=["发色错误"],
            ), []

        regen_mock = AsyncMock()
        with patch(
            "app.services.agent.video.keyframe_reflection_stage.export_keyframe_reflection_inputs",
            return_value={"brief": "brief.json", "artifact_name": "keyframe_reflection.json"},
        ), patch(
            "app.services.agent.video.keyframe_reflection_stage.generate_keyframe_reflection_via_deep_agent",
            new=fake_da,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.regenerate_single_keyframe",
            new=regen_mock,
        ):
            result, _ = await reflect_single_keyframe(
                keyframe=keyframe,
                shot=shot,
                characters=characters,
                character_images=character_images,
                char_map=char_map,
                state={},
                auto_regenerate=False,
            )

        assert result.needs_regeneration is True
        assert result.improved_description == "修正后的描述"
        assert len(result.issues) == 1
        assert result.issues[0].shot_number == 1, "issue 必须注入 shot_number"
        regen_mock.assert_not_called()


# ==================== 批量反思 ====================


class TestReflectAllKeyframes:

    @pytest.mark.asyncio
    async def test_reflect_all_no_issues_returns_results_per_keyframe(self):
        keyframes = [create_mock_keyframe(i, keyframe_uuid=f"kf-{i}-uuid") for i in range(1, 4)]
        shots = [create_mock_shot(i) for i in range(1, 4)]
        characters = [create_mock_character()]
        character_images = {"char-1-uuid": "https://example.com/char1.jpg"}

        async def fake_da(**kwargs):
            return _reflection_artifact(shot_number=1), []

        with patch(
            "app.services.agent.video.keyframe_reflection_stage.export_keyframe_reflection_inputs",
            return_value={"brief": "brief.json", "artifact_name": "keyframe_reflection.json"},
        ), patch(
            "app.services.agent.video.keyframe_reflection_stage.generate_keyframe_reflection_via_deep_agent",
            new=fake_da,
        ):
            results, analysis, messages = await reflect_all_keyframes(
                keyframes=keyframes,
                shots=shots,
                characters=characters,
                character_images=character_images,
                state={},
                max_concurrency=2,
                auto_regenerate=False,
            )

        assert len(results) == 3
        assert all(r.needs_regeneration is False for r in results)
        assert analysis.overall_score > 0
        assert analysis.needs_improvement is False

    @pytest.mark.asyncio
    async def test_reflect_all_auto_regenerate_invokes_regenerate(self):
        """auto_regenerate=True 时，对于 needs_regeneration=true 的关键帧，应调用 regenerate_single_keyframe。"""
        keyframes = [
            create_mock_keyframe(1, keyframe_uuid="kf-1-uuid"),
            create_mock_keyframe(2, keyframe_uuid="kf-2-uuid"),
        ]
        shots = [create_mock_shot(1), create_mock_shot(2)]
        characters = [create_mock_character()]
        character_images = {"char-1-uuid": "https://example.com/char1.jpg"}

        call_counter = {"n": 0}

        async def fake_da(**kwargs):
            call_counter["n"] += 1
            sn = call_counter["n"]
            need_regen = (sn == 1)
            return _reflection_artifact(
                shot_number=sn,
                needs_regeneration=need_regen,
                improved_description="better" if need_regen else "",
            ), []

        regen_mock = AsyncMock(return_value=(
            KeyframeVersion(
                version_id="new-kf-1-uuid",
                shot_number=1,
                keyframe_url="https://example.com/new_1.webp",
                t2i_prompt="new prompt",
                provider="gemini",
                keyframe_uuid="kf-1-uuid",
            ),
            "new-version-uuid",
        ))

        async def fake_update_shot(*args, **kwargs):
            return True

        with patch(
            "app.services.agent.video.keyframe_reflection_stage.export_keyframe_reflection_inputs",
            return_value={"brief": "brief.json", "artifact_name": "keyframe_reflection.json"},
        ), patch(
            "app.services.agent.video.keyframe_reflection_stage.generate_keyframe_reflection_via_deep_agent",
            new=fake_da,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.regenerate_single_keyframe",
            new=regen_mock,
        ), patch(
            "app.crud.video.video_story.update_shot_scene_description",
            new=fake_update_shot,
        ):
            results, analysis, _msgs = await reflect_all_keyframes(
                keyframes=keyframes,
                shots=shots,
                characters=characters,
                character_images=character_images,
                state={},
                max_concurrency=2,
                auto_regenerate=True,
            )

        assert len(results) == 2
        # 仅 shot 1 触发了重生（keyframe_id=kf-1-uuid）
        regen_calls = regen_mock.call_args_list
        assert len(regen_calls) == 1
        kf_id_passed = regen_calls[0].kwargs.get("keyframe_id") or (
            regen_calls[0].args[2] if len(regen_calls[0].args) >= 3 else None
        )
        assert kf_id_passed == "kf-1-uuid"
        shot1 = next(r for r in results if r.shot_number == 1)
        shot2 = next(r for r in results if r.shot_number == 2)
        assert shot1.new_keyframe_uuid == "new-version-uuid"
        assert shot1.new_keyframe_url == "https://example.com/new_1.webp"
        assert shot2.new_keyframe_uuid is None


# ==================== 重生：reuse_ref_image_urls 复用链路 ====================


class TestRegenerateReuseRefImageUrls:
    """覆盖 regenerate_single_keyframe 在反思路径下的 ref_image_urls 复用逻辑。"""

    @pytest.mark.asyncio
    async def test_regenerate_reuses_latest_successful_version_ref_urls(self):
        """应取 version_number 最大的、success=True 且 ref_image_urls 非空的版本。"""
        shot = create_mock_shot(1, character_ids=["c1"])
        # 模拟数据库版本：v3 最新且 success+有图、v2 success 但无图、v1 success 但旧
        v1 = MagicMock(version_number=1, success=True, reference_image_urls=["https://example.com/old.jpg"])
        v2 = MagicMock(version_number=2, success=True, reference_image_urls=None)
        v3 = MagicMock(version_number=3, success=True, reference_image_urls=["https://example.com/loc.jpg", "https://example.com/c1.jpg"])
        all_versions = [v1, v2, v3]

        async def fake_get_versions(_ids):
            return all_versions

        async def fake_build_char_images(_ids, _user_id):
            return {"c1": CharacterImageInfo(
                character_id="c1",
                profile=create_mock_character("c1", "A"),
                main_image_url="https://example.com/c1.jpg",
                version_id="cv-1",
            )}

        captured_kwargs: Dict[str, Any] = {}

        async def fake_generate_batch(**kwargs):
            captured_kwargs.update(kwargs)
            kf = KeyframeVersion(
                version_id="new-v",
                shot_number=1,
                keyframe_url="https://example.com/new.webp",
                t2i_prompt="prompt",
                provider="gemini",
                success=True,
                reference_image_urls=["https://example.com/loc.jpg", "https://example.com/c1.jpg"],
            )
            return ([], [kf])

        async def fake_create_version(**kwargs):
            return "new-version-uuid"

        with patch(
            "app.services.agent.video.keyframe_reflection_service.get_keyframe_versions_by_keyframe_ids",
            new=fake_get_versions,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service._build_character_images_dict",
            new=fake_build_char_images,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.generate_batch_keyframes",
            new=fake_generate_batch,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.create_keyframe_version",
            new=fake_create_version,
        ):
            kf, new_version_uuid = await regenerate_single_keyframe(
                shot=shot,
                state={"user_id": "u1", "conversation_id": "conv", "thread_id": "th", "run_id": "run"},
                keyframe_id="kf-1-uuid",
            )

        assert kf is not None
        assert new_version_uuid == "new-version-uuid"
        # 关键断言：复用了 v3（最新成功且非空）的 ref_image_urls
        assert captured_kwargs["reuse_ref_image_urls"] == [
            "https://example.com/loc.jpg",
            "https://example.com/c1.jpg",
        ]
        # 也保留对 generate_batch_keyframes 的其它入参的最小验证
        assert captured_kwargs["skip_consistency_check"] is True
        assert captured_kwargs["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_regenerate_falls_back_to_none_when_no_successful_version_has_urls(self):
        """所有版本都没图或都 success=False 时，reuse_ref_image_urls 应为 None（让下游走重新匹配）。"""
        shot = create_mock_shot(1, character_ids=["c1"])
        v1 = MagicMock(version_number=1, success=False, reference_image_urls=["x"])
        v2 = MagicMock(version_number=2, success=True, reference_image_urls=None)
        v3 = MagicMock(version_number=3, success=True, reference_image_urls=[])

        async def fake_get_versions(_ids):
            return [v1, v2, v3]

        async def fake_build_char_images(_ids, _user_id):
            return {}

        captured_kwargs: Dict[str, Any] = {}

        async def fake_generate_batch(**kwargs):
            captured_kwargs.update(kwargs)
            kf = KeyframeVersion(
                version_id="new-v",
                shot_number=1,
                keyframe_url="https://example.com/new.webp",
                t2i_prompt="prompt",
                provider="gemini",
                success=True,
            )
            return ([], [kf])

        async def fake_create_version(**kwargs):
            return "new-version-uuid"

        with patch(
            "app.services.agent.video.keyframe_reflection_service.get_keyframe_versions_by_keyframe_ids",
            new=fake_get_versions,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service._build_character_images_dict",
            new=fake_build_char_images,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.generate_batch_keyframes",
            new=fake_generate_batch,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.create_keyframe_version",
            new=fake_create_version,
        ):
            kf, new_version_uuid = await regenerate_single_keyframe(
                shot=shot,
                state={"user_id": "u1"},
                keyframe_id="kf-1-uuid",
            )

        assert kf is not None
        # 关键断言：没找到合适版本，reuse_ref_image_urls 必须为 None
        assert captured_kwargs["reuse_ref_image_urls"] is None

    @pytest.mark.asyncio
    async def test_regenerate_without_keyframe_id_skips_reuse_and_db_write(self):
        """传入空 keyframe_id 时：不查 version 列表、reuse=None、不写新版本。"""
        shot = create_mock_shot(1, character_ids=[])

        async def fake_build_char_images(_ids, _user_id):
            return {}

        captured_kwargs: Dict[str, Any] = {}

        async def fake_generate_batch(**kwargs):
            captured_kwargs.update(kwargs)
            kf = KeyframeVersion(
                version_id="new-v",
                shot_number=1,
                keyframe_url="https://example.com/new.webp",
                t2i_prompt="prompt",
                provider="gemini",
                success=True,
            )
            return ([], [kf])

        get_versions_mock = AsyncMock()
        create_version_mock = AsyncMock()

        with patch(
            "app.services.agent.video.keyframe_reflection_service.get_keyframe_versions_by_keyframe_ids",
            new=get_versions_mock,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service._build_character_images_dict",
            new=fake_build_char_images,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.generate_batch_keyframes",
            new=fake_generate_batch,
        ), patch(
            "app.services.agent.video.keyframe_reflection_service.create_keyframe_version",
            new=create_version_mock,
        ):
            kf, new_version_uuid = await regenerate_single_keyframe(
                shot=shot,
                state={"user_id": "u1"},
                keyframe_id="",
            )

        assert kf is not None
        assert new_version_uuid is None
        # 不应该查询 version 列表（keyframe_id 为空）
        get_versions_mock.assert_not_called()
        # 不应写新版本（keyframe_id 为空）
        create_version_mock.assert_not_called()
        assert captured_kwargs["reuse_ref_image_urls"] is None


# ==================== 数据模型测试（保留） ====================


class TestDataModels:

    def test_character_consistency_issue(self):
        issue = CharacterConsistencyIssue(
            character_id="char-1",
            character_name="角色A",
            shot_number=1,
            issue_type="hair",
            description="发色不一致",
            severity="high",
        )
        assert issue.character_id == "char-1"
        assert issue.severity == "high"

    def test_consistency_analysis_result(self):
        result = ConsistencyAnalysisResult(
            overall_score=0.85,
            needs_improvement=False,
            issues=[],
            summary="分析完成",
        )
        assert result.overall_score == 0.85
        assert not result.needs_improvement

    def test_single_keyframe_reflection_result(self):
        result = SingleKeyframeReflectionResult(
            shot_number=1,
            shot_uuid="shot-1-uuid",
            needs_regeneration=True,
            issues=[],
            analysis_summary="需要重新生成",
            improved_description="优化后的描述",
        )
        assert result.shot_number == 1
        assert result.needs_regeneration is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
