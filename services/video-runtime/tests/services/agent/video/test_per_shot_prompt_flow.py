#!/usr/bin/env python3
"""
独立测试：keyframe 与 video 的「每 shot 独立附图」流程。
验证 _get_per_shot_ref_image_urls、generate_batch_keyframe_prompts、_build_standard_video_prompt
使用 per-shot 多模态占位，无全局 slot 映射。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.video_state import (
    DetailedShot,
    KeyframeVersion,
    CharacterImageInfo,
    CharacterProfile,
)
from app.models.video_state import VisualElementType


# -------- Keyframe: _get_per_shot_ref_image_urls --------


class TestGetPerShotRefImageUrls:
    """测试 _get_per_shot_ref_image_urls：每镜头独立参考图列表，无全局去重"""

    @pytest.mark.asyncio
    async def test_per_shot_ref_urls_order_and_count(self):
        """每镜头返回独立 URL 列表，顺序：角色参考图 → 多视角 → 首帧（尾帧时）"""
        from app.services.agent.video.keyframe_generation_service import (
            _get_per_shot_ref_image_urls,
        )

        url_a = "https://example.com/a.webp"
        url_b = "https://example.com/b.webp"
        id_a, id_b = "id-a", "id-b"
        shots = [
            DetailedShot(shot_number=1, duration=1.0, character_ids=[id_a]),
            DetailedShot(shot_number=2, duration=1.0, character_ids=[id_b, id_a]),
        ]
        profile_a = CharacterProfile(id=id_a, type=VisualElementType.OBJECT, name="A", description="", appearance="")
        profile_b = CharacterProfile(id=id_b, type=VisualElementType.OBJECT, name="B", description="", appearance="")
        character_images = {
            id_a: CharacterImageInfo(character_id=id_a, main_image_url=url_a, profile=profile_a),
            id_b: CharacterImageInfo(character_id=id_b, main_image_url=url_b, profile=profile_b),
        }
        user_option = MagicMock()

        async def mock_get_refs(shot, character_images, db=None, user_id=None, model_limit=3, pre_fetched_fusions=None):
            if shot.shot_number == 1:
                return [url_a]
            return [url_b, url_a]

        with patch(
            "app.services.agent.video.keyframe_generation_service.get_character_ref_images",
            side_effect=mock_get_refs,
        ):
            per_shot = await _get_per_shot_ref_image_urls(
                shots_batch=shots,
                character_images=character_images,
                user_option=user_option,
                user_id="test-user",
                generate_last_frame=False,
                first_frame_images=None,
            )

        assert len(per_shot) == 2
        assert per_shot[0] == [url_a]
        assert per_shot[1] == [url_b, url_a]

    @pytest.mark.asyncio
    async def test_per_shot_with_first_frame_appended(self):
        """尾帧时每镜头列表末尾追加首帧 URL"""
        from app.services.agent.video.keyframe_generation_service import (
            _get_per_shot_ref_image_urls,
        )

        url_char = "https://example.com/char.webp"
        url_first = "https://example.com/first.webp"
        shots = [
            DetailedShot(shot_number=1, duration=1.0, character_ids=["c1"]),
        ]
        profile = CharacterProfile(id="c1", type=VisualElementType.CHARACTER, name="C", description="", appearance="")
        character_images = {
            "c1": CharacterImageInfo(character_id="c1", main_image_url=url_char, profile=profile),
        }

        async def mock_get_refs(shot, character_images, db=None, user_id=None, model_limit=3, pre_fetched_fusions=None):
            return [url_char]

        with patch(
            "app.services.agent.video.keyframe_generation_service.get_character_ref_images",
            side_effect=mock_get_refs,
        ):
            per_shot = await _get_per_shot_ref_image_urls(
                shots_batch=shots,
                character_images=character_images,
                user_option=MagicMock(),
                user_id="test",
                generate_last_frame=True,
                first_frame_images={1: url_first},
            )

        assert len(per_shot) == 1
        assert per_shot[0] == [url_char, url_first]


# -------- Keyframe: generate_batch_keyframe_prompts 使用 keyframe_shots + 多模态 --------


class TestGenerateBatchKeyframePromptsPerShotFlow:
    """验证 generate_batch_keyframe_prompts 使用 per-shot 附图与 keyframe_shots，且结果带 shot_reference_image_urls"""

    @pytest.mark.asyncio
    async def test_template_data_has_keyframe_shots_and_shot_refs(self):
        """调用时 template_data 含 keyframe_shots 与 shot_0_refs, shot_1_refs，且 invoke_prompt_with_multimodal 被调用"""
        from app.services.agent.video.keyframe_generation_service import (
            generate_batch_keyframe_prompts,
            BatchKeyframePromptResultForLLM,
            KeyframePromptResultForLLM,
        )
        from langchain_core.messages import HumanMessage

        shots = [
            DetailedShot(shot_number=1, duration=2.0, scene_description="s1", character_ids=["c1"]),
            DetailedShot(shot_number=2, duration=2.0, scene_description="s2", character_ids=["c1"]),
        ]
        profile = CharacterProfile(id="c1", type=VisualElementType.CHARACTER, name="Hero", description="", appearance="")
        character_images = {
            "c1": CharacterImageInfo(character_id="c1", main_image_url="https://ex.com/1.webp", profile=profile),
        }
        captured_template_data = {}

        async def capture_invoke(template, data):
            captured_template_data.clear()
            captured_template_data.update(data)
            return [HumanMessage(content="ok")]

        with patch(
            "app.services.agent.video.keyframe_generation_service._get_per_shot_ref_image_urls",
            new_callable=AsyncMock,
            return_value=[["https://ex.com/1.webp"], ["https://ex.com/1.webp"]],
        ), patch(
            "prompts.prompt_loader.invoke_prompt_with_multimodal",
            side_effect=capture_invoke,
        ), patch(
            "prompts.prompt_loader.load_prompt_with_fallback_async",
            new_callable=AsyncMock,
            return_value=(MagicMock(), None),
        ), patch(
            "langchain.agents.create_agent",
        ) as mock_create:
            mock_agent = MagicMock()
            mock_agent.ainvoke = AsyncMock(
                return_value={
                    "structured_response": BatchKeyframePromptResultForLLM(
                        prompts=[
                            KeyframePromptResultForLLM(shot_number=1, t2i_prompt="A character from image 1..."),
                            KeyframePromptResultForLLM(shot_number=2, t2i_prompt="A character from image 1..."),
                        ]
                    ),
                    "messages": [],
                }
            )
            mock_create.return_value = mock_agent

            messages, prompts = await generate_batch_keyframe_prompts(
                shots_batch=shots,
                character_images=character_images,
                user_option=MagicMock(),
                user_id="test",
                generate_first_frame=True,
                generate_last_frame=False,
            )

        assert "keyframe_shots" in captured_template_data
        keyframe_shots = captured_template_data["keyframe_shots"]
        assert len(keyframe_shots) == 2
        assert keyframe_shots[0]["shot_number"] == 1 and keyframe_shots[0]["shot_index"] == 0
        assert keyframe_shots[1]["shot_number"] == 2 and keyframe_shots[1]["shot_index"] == 1
        assert "ref_images" in keyframe_shots[0] and len(keyframe_shots[0]["ref_images"]) == 1
        assert "shot_0_refs" in captured_template_data
        assert "shot_1_refs" in captured_template_data
        assert captured_template_data["shot_0_refs"] == ["https://ex.com/1.webp"]
        assert captured_template_data["shot_1_refs"] == ["https://ex.com/1.webp"]

        assert len(prompts) == 2
        assert prompts[0].all_ref_urls == ["https://ex.com/1.webp"]
        assert prompts[1].all_ref_urls == ["https://ex.com/1.webp"]

        assert captured_template_data["shot_0_refs"] == prompts[0].all_ref_urls


class TestKeyframeImageOrderConsistency:
    """验证从 prompt 附图到 execute 执行，参考图顺序一致（含 resolve、tool 截断为前缀）"""

    @pytest.mark.asyncio
    async def test_resolve_preserves_order_as_final_reference_image_urls(self):
        """resolve 后 final_reference_image_urls 与 shot_reference_image_urls 顺序一致，execute 用同一列表"""
        from app.services.agent.video.keyframe_generation_service import (
            resolve_keyframe_prompt_reference_images,
            KeyframePromptResult,
        )

        url_a = "https://ex.com/a.webp"
        url_b = "https://ex.com/b.webp"
        url_c = "https://ex.com/c.webp"
        from app.services.agent.video.keyframe_generation_service import RefImageInfo, RefImageRole
        prompts = [
            KeyframePromptResult(shot_number=1, t2i_prompt="from image 1 and image 2", ref_images=[
                RefImageInfo(url=url_a, role=RefImageRole.CHARACTER), RefImageInfo(url=url_b, role=RefImageRole.CHARACTER)
            ]),
            KeyframePromptResult(shot_number=2, t2i_prompt="from image 1", ref_images=[
                RefImageInfo(url=url_c, role=RefImageRole.CHARACTER)
            ]),
        ]
        shots_batch = [
            DetailedShot(shot_number=1, duration=1.0, character_ids=["c1"]),
            DetailedShot(shot_number=2, duration=1.0, character_ids=["c2"]),
        ]
        character_images = {}

        resolved = await resolve_keyframe_prompt_reference_images(
            shots_batch=shots_batch,
            character_images=character_images,
            user_option=MagicMock(),
            user_id="test",
            prompts=prompts,
        )

        assert resolved[0].final_prompt == "from image 1 and image 2"
        assert resolved[0].all_ref_urls == [url_a, url_b]
        assert resolved[1].all_ref_urls == [url_c]
        assert list(resolved[0].all_ref_urls) == [url_a, url_b]


# -------- Video: generate_batch_video_prompts deep-agent path --------


class TestBuildStandardVideoPromptPerShotFlow:
    """Legacy _build_standard_video_prompt removed; batch prompts use video-director deep-agent."""

    @pytest.mark.asyncio
    async def test_generate_batch_video_prompts_uses_deep_agent(self):
        """generate_batch_video_prompts delegates to generate_video_prompts_via_deep_agent."""
        from app.services.agent.video.video_generation_service import generate_batch_video_prompts
        from app.contracts.artifacts.video import VideoPromptArtifact, VideoPromptRow

        keyframes_batch = [
            KeyframeVersion(shot_number=1, keyframe_url="https://ex.k1.webp", t2i_prompt="p1", provider="test"),
        ]
        shots_batch = [
            DetailedShot(shot_number=1, duration=1.5, scene_description="d1", character_ids=[]),
        ]
        fake_art = VideoPromptArtifact(
            prompts=[VideoPromptRow(shot_number=1, i2v_prompt="crafted")]
        )

        with patch(
            "app.services.agent.video.video_generation_service.generate_video_prompts_via_deep_agent",
            new_callable=AsyncMock,
            return_value=(fake_art, []),
        ), patch(
            "app.services.agent.video.video_generation_service.export_video_prompt_inputs",
            return_value={},
        ):
            _, prompts = await generate_batch_video_prompts(
                keyframes_batch=keyframes_batch,
                shots_batch=shots_batch,
                user_input="test",
            )
        assert len(prompts) == 1
        assert prompts[0].i2v_prompt == "crafted"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
