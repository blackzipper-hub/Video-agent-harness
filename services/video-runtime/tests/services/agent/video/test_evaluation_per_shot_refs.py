"""
Keyframe / Video eval 已迁 deep-agent：brief JSON + image_urls，不再走 mustache multimodal。

运行（conda env cuti-video-local）:
  ENVIRONMENT=development python -m pytest tests/services/agent/video/test_evaluation_per_shot_refs.py -v -s
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("ENVIRONMENT", "development")


@pytest.mark.asyncio
async def test_keyframe_evaluation_exports_brief_with_ref_urls():
    """Keyframe eval：export brief 含 prompts/ref 顺序，deep-agent 收到 image_urls。"""
    from app.services.agent.video.keyframe_generation_service import (
        KeyframePromptResult,
        RefImageInfo,
        RefImageRole,
        evaluate_and_fix_batch_keyframe_prompts,
    )
    from app.models.video_state import DetailedShot
    from app.contracts.artifacts.keyframe_eval import KeyframeEvalArtifact
    from app.schemas.video_llm import KeyframePromptFixResult

    ref_url = "https://example.com/char_ref.webp"
    prompts = [
        KeyframePromptResult(
            shot_number=1,
            t2i_prompt="A cat from image 1 in the scene.",
            frame_index=0,
            ref_images=[RefImageInfo(url=ref_url, role=RefImageRole.CHARACTER)],
        )
    ]
    shots_batch = [
        DetailedShot(
            shot_number=1,
            duration=2.0,
            character_ids=[],
            scene_description="A cat sitting.",
        )
    ]
    art = KeyframeEvalArtifact(
        fixes=[
            KeyframePromptFixResult(
                shot_number=1,
                frame_index=0,
                original_prompt=prompts[0].t2i_prompt,
                needs_fix=False,
                issues_found=[],
                fixed_prompt=prompts[0].t2i_prompt,
            )
        ],
        thread_id="t",
        run_id="r",
    )

    captured = {}

    async def fake_eval(*, thread_id, run_id, input_paths, detected_language=None):
        captured["input_paths"] = input_paths
        return art, []

    with patch(
        "app.services.agent.video.keyframe_eval_stage.evaluate_keyframe_prompts_via_deep_agent",
        side_effect=fake_eval,
    ):
        msgs, out = await evaluate_and_fix_batch_keyframe_prompts(
            prompts=prompts,
            shots_batch=shots_batch,
            user_option=None,
            user_input="",
            detected_language="zh",
        )

    assert out[0].t2i_prompt == prompts[0].t2i_prompt
    paths = captured["input_paths"]
    assert ref_url in (paths.get("image_urls") or [])
    assert paths.get("expected_keys") == [(1, 0)]


@pytest.mark.asyncio
async def test_video_evaluation_exports_start_end_images():
    """Video eval：deep-agent input_paths.image_urls 按镜顺序含首帧/尾帧。"""
    from app.services.agent.video.video_generation_service import (
        evaluate_and_fix_batch_prompts,
    )
    from app.schemas.video import VideoGenerationPrompt
    from app.contracts.artifacts.video_eval import VideoEvalArtifact
    from app.schemas.video_llm import PromptEvaluationResult

    start_url = "https://example.com/kf1.webp"
    end_url = "https://example.com/kf2.webp"
    prompts = [
        VideoGenerationPrompt(
            shot_number=1,
            i2v_prompt="Scene: room. Action: person walks.",
            start_image_url=start_url,
            end_image_url=None,
        ),
        VideoGenerationPrompt(
            shot_number=2,
            i2v_prompt="Scene: street. Action: person runs.",
            start_image_url=start_url,
            end_image_url=end_url,
        ),
    ]
    art = VideoEvalArtifact(
        evaluations=[
            PromptEvaluationResult(
                shot_number=1,
                original_prompt=prompts[0].i2v_prompt,
                needs_content_fix=False,
                content_issues_found=[],
                fixed_prompt=prompts[0].i2v_prompt,
            ),
            PromptEvaluationResult(
                shot_number=2,
                original_prompt=prompts[1].i2v_prompt,
                needs_content_fix=False,
                content_issues_found=[],
                fixed_prompt=prompts[1].i2v_prompt,
            ),
        ],
        thread_id="t",
        run_id="r",
    )
    captured = {}

    async def fake_eval(*, thread_id, run_id, input_paths, detected_language=None):
        captured["input_paths"] = input_paths
        return art, [MagicMock()]

    with patch(
        "app.services.agent.video.video_eval_stage.evaluate_video_prompts_via_deep_agent",
        side_effect=fake_eval,
    ):
        msgs, out = await evaluate_and_fix_batch_prompts(
            prompts=prompts,
            keyframes_batch=[],
            detected_language="en",
        )

    assert len(out) == 2
    urls = captured["input_paths"].get("image_urls") or []
    assert start_url in urls
    assert end_url in urls
    assert urls.count(start_url) >= 2  # shot1 start + shot2 start


@pytest.mark.asyncio
async def test_video_evaluation_skips_without_real_start_image():
    """reference_t2v / 空首帧：不跑 I2V first-frame eval。"""
    from app.services.agent.video.video_generation_service import (
        evaluate_and_fix_batch_prompts,
    )
    from app.schemas.video import VideoGenerationPrompt

    prompts = [
        VideoGenerationPrompt(
            shot_number=1,
            i2v_prompt="单镜头…店内暖光。林宇说：「没钱。」",
            start_image_url="",
            end_image_url=None,
        ),
        VideoGenerationPrompt(
            shot_number=2,
            i2v_prompt="单镜头…街角。",
            start_image_url="",
            end_image_url=None,
        ),
    ]

    with patch(
        "app.services.agent.video.video_eval_stage.evaluate_video_prompts_via_deep_agent",
        new_callable=AsyncMock,
    ) as mock_eval:
        msgs, out = await evaluate_and_fix_batch_prompts(
            prompts=prompts,
            keyframes_batch=[],
            detected_language="zh",
        )

    mock_eval.assert_not_called()
    assert msgs == []
    assert out is prompts
    assert out[0].i2v_prompt == prompts[0].i2v_prompt


@pytest.mark.asyncio
async def test_video_evaluation_skips_for_reference_to_video_tool():
    """Seedance2 reference-to-video：即使误带 start URL 也不跑 I2V eval。"""
    from app.models.user_options import UserOption, VideoGenerationTool, ContentCategory
    from app.services.agent.video.video_generation_service import (
        evaluate_and_fix_batch_prompts,
    )
    from app.schemas.video import VideoGenerationPrompt

    uo = UserOption(
        content_category=ContentCategory.SHORT_DRAMA,
        video_generation_tool=VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO,
    )
    prompts = [
        VideoGenerationPrompt(
            shot_number=1,
            i2v_prompt="单镜头…",
            start_image_url="https://example.com/should-not-trigger-i2v-eval.webp",
        ),
    ]

    with patch(
        "app.services.agent.video.video_eval_stage.evaluate_video_prompts_via_deep_agent",
        new_callable=AsyncMock,
    ) as mock_eval:
        msgs, out = await evaluate_and_fix_batch_prompts(
            prompts=prompts,
            keyframes_batch=[],
            user_option=uo,
            detected_language="zh",
        )

    mock_eval.assert_not_called()
    assert msgs == []
    assert out is prompts
