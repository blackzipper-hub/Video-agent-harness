#!/usr/bin/env python3
"""批量 video / keyframe prompt：LLM 返回遗漏、重复、乱序、空串、多余镜头时的对齐与补齐。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.video_state import DetailedShot, KeyframeVersion, CharacterImageInfo, CharacterProfile
from app.models.video_state import VisualElementType
from app.schemas.video import BatchVideoPromptResult, VideoPromptResult
from app.services.agent.video.keyframe_generation_service import (
    generate_batch_keyframe_prompts,
    BatchKeyframePromptResultForLLM,
    KeyframePromptResultForLLM,
)


def _shots(*spec: tuple) -> list:
    """spec: (shot_number, scene_description | None)"""
    out = []
    for sn, desc in spec:
        out.append(
            DetailedShot(
                shot_number=sn,
                duration=1.0,
                scene_description=desc,
                character_ids=[],
            )
        )
    return out


def _keyframes_for_shots(*shot_numbers: int) -> list:
    return [
        KeyframeVersion(
            shot_number=sn,
            keyframe_url=f"https://kf.example/s{sn}.jpg",
            t2i_prompt="k",
            provider="test",
            frame_index=0,
        )
        for sn in shot_numbers
    ]


@pytest.mark.asyncio
class TestVideoBatchPromptAlignment:
    """generate_batch_video_prompts：与 shots_batch 对齐"""

    async def _run_video(self, shots, keyframes, llm_prompts: list):
        from app.services.agent.video.video_generation_service import generate_batch_video_prompts
        from app.contracts.artifacts.video import VideoPromptArtifact, VideoPromptRow

        fake_art = VideoPromptArtifact(
            prompts=[VideoPromptRow(shot_number=p[0], i2v_prompt=p[1]) for p in llm_prompts]
        )

        with patch(
            "app.services.agent.video.video_generation_service.generate_video_prompts_via_deep_agent",
            new_callable=AsyncMock,
            return_value=(fake_art, []),
        ), patch(
            "app.services.agent.video.video_generation_service.export_video_prompt_inputs",
            return_value={},
        ):
            return await generate_batch_video_prompts(
                keyframes_batch=keyframes,
                shots_batch=shots,
                user_input="u",
            )

    async def test_full_match_order_preserved(self):
        shots = _shots((1, "a"), (2, "b"), (3, "c"))
        kf = _keyframes_for_shots(1, 2, 3)
        _, prompts = await self._run_video(
            shots,
            kf,
            [(1, "p1"), (2, "p2"), (3, "p3")],
        )
        assert [p.shot_number for p in prompts] == [1, 2, 3]
        assert [p.i2v_prompt for p in prompts] == ["p1", "p2", "p3"]

    async def test_llm_out_of_order_outputs_shots_batch_order(self):
        shots = _shots((1, "a"), (2, "b"), (3, "c"))
        kf = _keyframes_for_shots(1, 2, 3)
        _, prompts = await self._run_video(
            shots,
            kf,
            [(3, "p3"), (1, "p1"), (2, "p2")],
        )
        assert [p.shot_number for p in prompts] == [1, 2, 3]
        assert [p.i2v_prompt for p in prompts] == ["p1", "p2", "p3"]

    async def test_missing_shot_padded_with_scene_description(self):
        shots = _shots((1, "scene_one"), (2, "scene_two"), (3, "scene_three"))
        kf = _keyframes_for_shots(1, 2, 3)
        _, prompts = await self._run_video(
            shots,
            kf,
            [(1, "p1"), (3, "p3")],
        )
        assert [p.shot_number for p in prompts] == [1, 2, 3]
        assert prompts[0].i2v_prompt == "p1"
        assert prompts[1].i2v_prompt == "scene_two"
        assert prompts[2].i2v_prompt == "p3"

    async def test_empty_i2v_treated_as_missing_and_padded(self):
        shots = _shots((1, "s1"), (2, "s2"))
        kf = _keyframes_for_shots(1, 2)
        _, prompts = await self._run_video(
            shots,
            kf,
            [(1, "ok"), (2, "   ")],
        )
        assert prompts[0].i2v_prompt == "ok"
        assert prompts[1].i2v_prompt == "s2"

    async def test_duplicate_llm_shot_keeps_first(self):
        shots = _shots((1, "s1"), (2, "s2"))
        kf = _keyframes_for_shots(1, 2)
        _, prompts = await self._run_video(
            shots,
            kf,
            [(1, "first"), (1, "second"), (2, "p2")],
        )
        assert prompts[0].i2v_prompt == "first"
        assert prompts[1].i2v_prompt == "p2"

    async def test_extra_llm_shot_ignored(self):
        shots = _shots((1, "s1"), (2, "s2"))
        kf = _keyframes_for_shots(1, 2)
        _, prompts = await self._run_video(
            shots,
            kf,
            [(1, "p1"), (2, "p2"), (99, "ghost")],
        )
        assert len(prompts) == 2
        assert [p.i2v_prompt for p in prompts] == ["p1", "p2"]

    async def test_no_scene_description_fallback_text(self):
        shots = _shots((1, None), (2, None))
        kf = _keyframes_for_shots(1, 2)
        _, prompts = await self._run_video(
            shots,
            kf,
            [(1, "only")],
        )
        assert prompts[0].i2v_prompt == "only"
        assert prompts[1].i2v_prompt == "镜头2的视频内容"


@pytest.mark.asyncio
class TestKeyframeBatchPromptAlignment:
    """generate_batch_keyframe_prompts：与 shots_batch 对齐"""

    async def _run_keyframe(
        self,
        shots,
        llm_prompts: list,
        *,
        generate_last_frame: bool = False,
        first_frame_images=None,
        per_shot_urls=None,
    ):
        profile = CharacterProfile(
            id="c1", type=VisualElementType.CHARACTER, name="H", description="", appearance=""
        )
        character_images = {
            "c1": CharacterImageInfo(
                character_id="c1", main_image_url="https://ex.com/1.webp", profile=profile
            ),
        }
        ref_url = "https://ex.com/1.webp"
        if per_shot_urls is None:
            per_shot_urls = [[ref_url] for _ in shots]
            if generate_last_frame and first_frame_images:
                per_shot_urls = [
                    [ref_url, first_frame_images.get(s.shot_number, ref_url)] for s in shots
                ]

        async def fake_resilience(**kwargs):
            return {
                "structured_response": BatchKeyframePromptResultForLLM(
                    prompts=[
                        KeyframePromptResultForLLM(shot_number=p[0], t2i_prompt=p[1]) for p in llm_prompts
                    ]
                ),
                "messages": [],
            }

        with patch(
            "app.services.agent.video.keyframe_generation_service._get_per_shot_ref_image_urls",
            new_callable=AsyncMock,
            return_value=per_shot_urls,
        ), patch(
            "prompts.prompt_loader.invoke_prompt_with_multimodal",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "prompts.prompt_loader.load_prompt_with_fallback_async",
            new_callable=AsyncMock,
            return_value=(MagicMock(), None),
        ), patch(
            "app.services.agent.utils.llm_resilience.ainvoke_structured_resilient",
            side_effect=fake_resilience,
        ):
            return await generate_batch_keyframe_prompts(
                shots_batch=shots,
                character_images=character_images,
                user_option=MagicMock(),
                user_id="test",
                generate_first_frame=not generate_last_frame,
                generate_last_frame=generate_last_frame,
                first_frame_images=first_frame_images or {},
            )

    async def test_full_match_and_order(self):
        shots = _shots((1, "a"), (2, "b"))
        _, prompts = await self._run_keyframe(shots, [(1, "t1"), (2, "t2")])
        assert [p.shot_number for p in prompts] == [1, 2]
        assert [p.t2i_prompt for p in prompts] == ["t1", "t2"]
        assert all(p.frame_index == 0 for p in prompts)

    async def test_missing_padded_with_scene_description(self):
        shots = _shots((1, "sc1"), (2, "sc2"), (3, "sc3"))
        _, prompts = await self._run_keyframe(shots, [(1, "a"), (3, "c")])
        assert [p.shot_number for p in prompts] == [1, 2, 3]
        assert prompts[1].t2i_prompt == "sc2"

    async def test_out_of_order_normalized(self):
        shots = _shots((1, "s1"), (2, "s2"), (3, "s3"))
        _, prompts = await self._run_keyframe(shots, [(3, "c"), (1, "a"), (2, "b")])
        assert [p.t2i_prompt for p in prompts] == ["a", "b", "c"]

    async def test_empty_t2i_padded(self):
        shots = _shots((1, "s1"), (2, "s2"))
        _, prompts = await self._run_keyframe(shots, [(1, "ok"), (2, "")])
        assert prompts[1].t2i_prompt == "s2"

    async def test_duplicate_keeps_first(self):
        shots = _shots((1, "s1"), (2, "s2"))
        _, prompts = await self._run_keyframe(shots, [(1, "first"), (1, "dup"), (2, "b")])
        assert prompts[0].t2i_prompt == "first"

    async def test_extra_shot_ignored(self):
        shots = _shots((1, "s1"), (2, "s2"))
        _, prompts = await self._run_keyframe(shots, [(1, "a"), (2, "b"), (99, "x")])
        assert len(prompts) == 2

    async def test_fallback_without_scene_description(self):
        shots = _shots((1, None), (2, None))
        _, prompts = await self._run_keyframe(shots, [(2, "b")])
        assert prompts[0].t2i_prompt == "镜头1的首帧画面"
        assert prompts[1].t2i_prompt == "b"

    async def test_last_frame_batch_fallback_without_scene_description(self):
        shots = _shots((1, None))
        _, prompts = await self._run_keyframe(
            shots,
            [],
            generate_last_frame=True,
            first_frame_images={1: "https://first.example/1.jpg"},
        )
        assert len(prompts) == 1
        assert prompts[0].frame_index == -1
        assert prompts[0].t2i_prompt == "镜头1的尾帧画面"
