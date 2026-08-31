"""
旁白生成真实集成测试（Product Launch / Minimax TTS 路径）

运行：
  conda run -n cuti-video-local pytest tests/services/agent/video/test_narration_generation_integration.py -v -s -m integration

仅测 db 参数已移除（不调 API）：
  conda run -n cuti-video-local pytest tests/services/agent/video/test_narration_generation_integration.py::test_generate_single_narration_callable_without_db -v
"""
import inspect

import pytest

from app.models.video_state import DetailedShot
from app.services.agent.video.narration_generation_service import (
    _generate_single_narration,
)


def test_generate_single_narration_callable_without_db():
    """回归：调用方不应再传已删除的 db 参数。"""
    sig = inspect.signature(_generate_single_narration)
    assert "db" not in sig.parameters


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_single_narration_real_minimax():
    """真实调用 react agent + Minimax TTS，验证旁白单镜生成链路。"""
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from prompts.prompt_config import PromptName

    shot = DetailedShot(
        shot_number=1,
        duration=3.0,
        character_ids=[],
        narration="欢迎来到我们的产品发布，让我们一起看看核心功能。",
        scene_description="年轻男性正对镜头讲解，明亮温暖背景。",
        lighting="柔和暖光",
        is_bridge=False,
        generation_mode="lipsync",
    )
    state = {
        "detected_language": "zh",
        "conversation_id": 0,
        "thread_id": "test-thread",
        "run_id": "test-run",
        "user_id": "test-user",
    }
    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_NARRATION_GENERATION.value,
        local_template_name="video/narration/video_narration_generation",
        schema=None,
        include_raw=False,
    )

    narration_version, messages = await _generate_single_narration(
        shot,
        "test-story-outline-uuid",
        state,
        cached_prompt_template=prompt_template,
    )

    assert narration_version.shot_number == 1
    assert narration_version.success is True, narration_version.error_msg
    assert narration_version.audio_url
    assert narration_version.duration and narration_version.duration > 0
    assert len(messages) > 0
    print(f"audio_url={narration_version.audio_url}")
    print(f"duration={narration_version.duration}s")
