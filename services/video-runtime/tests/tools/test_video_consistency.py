"""
Video consistency 逻辑测试：默认通过、invoke_structured_llm_resilient、load_prompt retry、真实 check_video_consistency_llm。

覆盖：
  1. 无首帧/无视频 -> 默认通过
  2. invoke_structured_llm_resilient：先 None 再返回值 -> 返回该值；全 None -> 返回 None
  3. load_prompt_with_fallback_async(..., retry_config=...) 不抛异常且返回 runnable
  4. 真实调用 check_video_consistency_llm（首帧图 + 视频 URL）-> 得到合法 VideoConsistencyCheckResult

运行（conda env cuti-video-local，会加载 .env.development）：
  cd Cuti-VideoAgent && ENVIRONMENT=development python -m pytest tests/tools/test_video_consistency.py -v -s
  cd Cuti-VideoAgent && ENVIRONMENT=development python -m pytest tests/tools/test_video_consistency.py -v -s -k "real"
"""
import os
from pathlib import Path

# 与 test_video_wrapper_integration 一致：优先加载 env
_env = os.getenv("ENVIRONMENT", "development").lower()
from dotenv import load_dotenv
if _env == "production":
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.production")
else:
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.development")

import pytest

from app.schemas.video_llm import (
    VideoConsistencyCheckResult,
    VideoConsistencyLevel,
)


# ==================== 无首帧/无视频 -> 默认通过 ====================

@pytest.mark.asyncio
async def test_check_video_consistency_llm_empty_input_default_pass():
    """无首帧或无视频时直接返回默认通过，不调 LLM。"""
    from app.tools.video.video_consistency import check_video_consistency_llm

    r1 = await check_video_consistency_llm("", "https://example.com/v.mp4", "prompt")
    assert r1.passed is True
    assert r1.reason_overall and "无首帧" in r1.reason_overall
    assert r1.first_frame_consistency == VideoConsistencyLevel.N_A

    r2 = await check_video_consistency_llm("https://example.com/img.webp", "", "prompt")
    assert r2.passed is True
    assert r2.reason_overall and "无首帧" in r2.reason_overall or "无视频" in r2.reason_overall


# ==================== invoke_structured_llm_resilient ====================

@pytest.mark.asyncio
async def test_invoke_structured_llm_resilient_returns_first_non_none():
    """第一次返回 None，第二次返回非 None -> 得到第二次的返回值。"""
    from langchain_core.messages import HumanMessage
    from app.services.agent.utils.prompt_utils import invoke_structured_llm_resilient

    call_count = 0

    class FakeRunnable:
        async def ainvoke(self, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return {"value": 42}

    messages = [HumanMessage(content="test")]
    out = await invoke_structured_llm_resilient(
        FakeRunnable(), messages, max_attempts=3, logger_instance=None
    )
    assert out == {"value": 42}
    assert call_count == 2


@pytest.mark.asyncio
async def test_invoke_structured_llm_resilient_all_none_returns_none():
    """全部返回 None -> 函数返回 None。"""
    from langchain_core.messages import HumanMessage
    from app.services.agent.utils.prompt_utils import invoke_structured_llm_resilient

    class FakeRunnable:
        async def ainvoke(self, messages):
            return None

    messages = [HumanMessage(content="test")]
    out = await invoke_structured_llm_resilient(
        FakeRunnable(), messages, max_attempts=2, logger_instance=None
    )
    assert out is None


# ==================== load_prompt with retry_config ====================

@pytest.mark.asyncio
async def test_load_prompt_with_retry_config_returns_runnable():
    """load_prompt_with_fallback_async(..., retry_config=...) 不抛异常，返回 (template, runnable)。"""
    from prompts.prompt_loader import load_prompt_with_fallback_async, DEFAULT_LLM_RETRY_CONFIG
    from prompts.prompt_config import PromptName

    prompt_template, llm = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_CONSISTENCY_CHECK.value,
        local_template_name="video/video_consistency/video_consistency_check",
        schema=VideoConsistencyCheckResult,
        include_raw=False,
        retry_config=DEFAULT_LLM_RETRY_CONFIG,
    )
    assert prompt_template is not None
    assert llm is not None
    assert hasattr(llm, "ainvoke")


# ==================== 真实调用 check_video_consistency_llm ====================

# 与 test_video_wrapper_integration 一致的首帧图
START_IMAGE_URL = "https://cdn-dev.newai.land/images/5bc522f7-26ab-4c7c-9a9b-31a4caa9c158.webp"
# 公开短 mp4，用于真实调用一致性校验（Gemini 需能访问；若不可访问会走异常默认通过）
SAMPLE_VIDEO_URL = "https://sample-videos.com/video321/mp4/720/big_buck_bunny_720p_1mb.mp4"


@pytest.mark.asyncio
async def test_video_consistency_character_ref_block_appears_once():
    """
    有多个角色参考图时，「本镜头依赖的角色参考图」只出现一次，不因 Mustache 列表迭代而重复。
    """
    from prompts.prompt_loader import load_prompt_with_fallback_async, invoke_prompt_with_multimodal
    from prompts.prompt_config import PromptName
    from app.schemas.video_llm import VideoConsistencyCheckResult

    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_CONSISTENCY_CHECK.value,
        local_template_name="video/video_consistency/video_consistency_check",
        schema=VideoConsistencyCheckResult,
        include_raw=False,
    )
    ref_urls = [
        "https://example.com/ref1.webp",
        "https://example.com/ref2.webp",
        "https://example.com/ref3.webp",
    ]
    template_data = {
        "i2v_prompt": "Test prompt.",
        "first_frame": "https://example.com/first.webp",
        "generated_video": "https://example.com/video.mp4",
        "character_ref_image_urls": ref_urls,
        "has_character_ref_images": True,
    }
    messages = await invoke_prompt_with_multimodal(
        prompt_template, template_data, model_provider="gemini"
    )
    human = next((m for m in messages if m.__class__.__name__ == "HumanMessage"), None)
    assert human is not None
    content = human.content
    if isinstance(content, list):
        text_parts = [
            c.get("text", "") for c in content
            if isinstance(c, dict) and "text" in c
        ]
        full_text = " ".join(text_parts)
    else:
        full_text = content or ""
    label = "本镜头依赖的角色参考图"
    assert full_text.count(label) == 1, (
        f"角色参考图标签应只出现 1 次，实际出现 {full_text.count(label)} 次"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_check_video_consistency_llm_real_call_returns_valid_result():
    """
    真实调用 check_video_consistency_llm（首帧图 + 视频 URL）。
    预期：得到 VideoConsistencyCheckResult（要么 LLM 正常结果，要么异常/None 时的默认通过），且必有 first_frame_consistency 等字段。
    """
    from app.tools.video.video_consistency import check_video_consistency_llm

    result = await check_video_consistency_llm(
        start_image_url=START_IMAGE_URL,
        video_url=SAMPLE_VIDEO_URL,
        i2v_prompt="A character looks up slowly. Warm lighting, film grain.",
        character_ref_image_urls=None,
    )
    assert isinstance(result, VideoConsistencyCheckResult)
    assert result.passed is not None
    assert hasattr(result, "reason_overall")
    assert result.first_frame_consistency is not None or result.reason_overall
