"""
Lipsync Wrapper 单测 — wan_2_6_flash，长 I2V prompt + 指定首帧图，验证「一次修复」流程。

场景：纯音乐间奏舞台、霓虹光束、特写纵摇+推镜、2.8s 动作。
配置：model wan-2.5-i2v, tool wan_2_6_flash, provider wavespeed, 1080p, 16:9, mode lipsync。
目的：单独测 lipsync tool wrapper，断言最终 result.success（允许一次 consistency 重试用 suggested_prompt 后成功）。

运行（conda env cuti-video-local）：
  ENVIRONMENT=development conda run -n cuti-video-local pytest tests/tools/test_lipsync_wrapper_one_shot.py -v -s

注意：依赖 Redis（AccountConfigLoader）与正常网络/代理；若仅需验证 wrapper 逻辑且无 Redis，可临时 patch
  app.tools.video.wan26_flash.get_account_router，用环境变量 WAVESPEED_API_KEY 直接调 request_func(api_key)，测完改回。
"""
import json
import os
from pathlib import Path

_environment = os.getenv("ENVIRONMENT", "development").lower()
from dotenv import load_dotenv
if _environment == "production":
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.production")
else:
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.development")

import asyncio
from types import SimpleNamespace

import pytest

from app.models.image_result import VideoGenerationResult
from app.models.tool_enums import Resolution, AspectRatio
from app.models.user_options import VideoGenerationTool
from app.tools.context_schemas import VideoGenerationContext
from app.tools.video.lipsync_tool_wrapper import create_lipsync_wrapper_tools


# 用户提供的首帧图
START_IMAGE_URL = "https://cdn-dev.newai.land/images/4f92d4e5-12a6-45bb-a669-7d09f4cbde0c.webp"

# 长 I2V prompt（纯音乐间奏舞台、霓虹、特写纵摇+推镜、2.8s）
LIPSYNC_I2V_PROMPT = (
    "Scene: 纯音乐间奏的舞台背景，霓虹光束交织，营造出紧张而充满力量感的氛围。"
    "Lighting: 侧逆光，硬光，霓虹色（红蓝），高对比度，面部轮廓光，锐利光效。"
    "Camera: 特写，平视机位，平视角度，纵摇 (Tilt Up) + 推镜头 (Dolly In)，镜头运动快速。"
    "Action (2.8s total): 0-1s: 镜头首先是一个手部特写，歌手右手紧握麦克风，指关节因用力而微微泛白，"
    "霓虹光斑在手背上柔和流转，背景舞台灯光开始缓慢流转，光束交织。"
    "1-2s: 镜头快速向上纵摇并推近，歌手手部略微上抬，麦克风随之向上，"
    "背景霓虹光线交织成锐利光束，预示着情绪的递进，舞台烟雾弥漫。"
    "2-2.8s: 镜头聚焦于歌手自信而略带挑衅的半侧脸极特写，她嘴角微扬，眼神锐利坚定，"
    "面部轮廓被红蓝霓虹灯光勾勒得棱角分明，展现出强大的掌控力，为下一段演唱积蓄能量。"
    "Environment: 霓虹舞台背景光束缓慢流转，舞台烟雾缭绕，增强视觉冲击力。"
    "Style: 强烈的色彩对比，霓虹光影在歌手面部和手部形成戏剧性效果，强调细节与情绪，"
    "高对比度黑、红、电光蓝色调，精致偶像舞台美学。"
)

VIDEO_DURATION = 3  # 2.8s 动作，API 最小 3s


async def _run_lipsync_wrapper_one_shot():
    """Lipsync wrapper：wan_2_6_flash，1080p 16:9，单次调用，断言 success（可经一次 consistency 重试）。"""
    user_option = SimpleNamespace(video_generation_tool=VideoGenerationTool.WAN_2_6)
    tool_infos, primary_tool = create_lipsync_wrapper_tools(user_option=user_option)
    assert tool_infos, "lipsync wrapper tool_infos should not be empty"
    wrapper_tool = tool_infos[0].tool

    ctx = VideoGenerationContext(
        start_image_url=START_IMAGE_URL,
        resolution=Resolution.P1080,
        aspect_ratio=AspectRatio.LANDSCAPE,
    )
    runtime = SimpleNamespace(context=ctx)

    invoke_args = {
        "i2v_prompt": LIPSYNC_I2V_PROMPT,
        "start_image_url": START_IMAGE_URL,
        "duration": VIDEO_DURATION,
        "runtime": runtime,
    }
    raw = await wrapper_tool.ainvoke(invoke_args)
    if isinstance(raw, tuple) and len(raw) == 2:
        result = raw[1]
    elif isinstance(raw, str):
        result = VideoGenerationResult.model_validate(json.loads(raw))
    else:
        result = raw
    return result


@pytest.mark.asyncio
async def test_lipsync_wrapper_wan26_flash_one_shot():
    """Lipsync wrapper 单测：wan_2_6_flash + 长 prompt + 指定图，1080p 16:9，最终成功（允许一次 consistency 修复）。"""
    result = await _run_lipsync_wrapper_one_shot()
    assert isinstance(result, VideoGenerationResult), f"expected VideoGenerationResult, got {type(result)}"
    assert result.success, (
        f"lipsync wrapper should succeed (possibly after one suggested_prompt retry). "
        f"error_msg={result.error_msg!r} raw_error_msg={result.raw_error_msg!r}"
    )
    assert result.video_url, "video_url should be set on success"


@pytest.mark.asyncio
async def test_lipsync_wrapper_wan26_flash_concurrent_3():
    """Lipsync wrapper 并发 3 次：同时发起 3 次调用，断言全部 success。"""
    results = await asyncio.gather(
        _run_lipsync_wrapper_one_shot(),
        _run_lipsync_wrapper_one_shot(),
        _run_lipsync_wrapper_one_shot(),
    )
    for i, result in enumerate(results):
        assert isinstance(result, VideoGenerationResult), f"run {i}: expected VideoGenerationResult, got {type(result)}"
        assert result.success, (
            f"run {i}: lipsync wrapper should succeed. "
            f"error_msg={result.error_msg!r} raw_error_msg={result.raw_error_msg!r}"
        )
        assert result.video_url, f"run {i}: video_url should be set on success"
