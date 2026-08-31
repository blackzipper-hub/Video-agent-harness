"""真实端到端：video_gen 直生 Agent（React Agent → SD2 T2V leaf → 结构化事件）。

跑通整条新链路：
  generate_video_with_agent
    → 注入 T2V 包装工具（content_and_artifact）
    → React Agent（gpt-4.1-mini）理解需求并调用工具
    → 真实 WaveSpeed Seedance 2.0 Fast T2V 出片
    → 从 ToolMessage.artifact 收集结构化 videos[]
    → 发送 video_agent_generated 事件（message + videos[]）

需要 WAVESPEED_API_KEY + OPENAI_API_KEY（会真实扣费，用最便宜 480p/5s）。

运行：
    pytest tests/agent/test_video_gen_agent_real.py -v -s -k real
"""
import os
import asyncio
from pathlib import Path

import pytest
from dotenv import load_dotenv

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent
_env = os.getenv("ENVIRONMENT", "development").lower()
if _env == "production":
    load_dotenv(ROOT / ".env.production")
else:
    load_dotenv(ROOT / ".env.development")
load_dotenv(ROOT / ".env.local")

from app.models.video_state import UserInput
from app.models.user_options import UserOption, VideoGenerationTool
from app.models.tool_enums import AspectRatio, Resolution
from app.services.agent.base_agent import MessageType
from app.services.agent.video_gen.video_generation_agent_service import (
    generate_video_with_agent,
)


@pytest.mark.asyncio
async def test_video_gen_agent_real_single(monkeypatch):
    """纯文本单视频：验证 React Agent 真的调工具出片并产出结构化 videos[]。"""
    if not os.getenv("WAVESPEED_API_KEY") or not os.getenv("OPENAI_API_KEY"):
        pytest.skip("need WAVESPEED_API_KEY + OPENAI_API_KEY")

    # 默认走真实 account router（→ AppConfig 账号池 key），验证线上真实路径。
    # 仅当 AppConfig key 失效需隔离 infra 时，设 VIDEO_GEN_FORCE_ENV_KEY=1 用 .env key 直连。
    if os.getenv("VIDEO_GEN_FORCE_ENV_KEY") == "1":
        from app.services.account import account_router as _ar

        async def _direct_route(self, provider, tool_type, request_func, *args, **kwargs):
            return await request_func(os.environ["WAVESPEED_API_KEY"], *args, **kwargs)

        monkeypatch.setattr(_ar.AccountRouter, "route_tool_request", _direct_route)

    uo = UserOption.default()
    uo.video_generation_tool = VideoGenerationTool.SEEDANCE_2_FAST_I2V  # 内部映射到 Fast T2V
    uo.aspect_ratio = AspectRatio.LANDSCAPE
    uo.resolution = Resolution.P480  # 最便宜
    uo.duration = 5

    ui = UserInput(
        user_input="用 Seedance 生成一个 5 秒的视频：厨师在温暖的厨房里慢镜头摆盘，电影感灯光，缓慢推进镜头。",
        user_option=uo,
        agent_type="video_gen",
    )

    events = []
    chunks = []

    async def send_event_func(event_type=None, conversation_id=None, message=None, extra_data=None, **kwargs):
        et = getattr(event_type, "value", event_type)
        if et == MessageType.STREAMING_CHUNK.value:
            chunks.append(message or "")
            return
        events.append({"type": et, "message": message, "extra": extra_data or {}})

    print("\n" + "=" * 80)
    print("🚀 video_gen 真实端到端：480p/5s Fast T2V，纯文本单视频")
    print("=" * 80)

    result = await generate_video_with_agent(
        user_input_data=ui,
        messages=[],
        send_event_func=send_event_func,
        conversation_id=1,
        run_id="test_video_gen_real",
        thread_id="test_thread",
        detected_language="zh",
    )

    print(f"\n📡 收到事件类型: {[e['type'] for e in events]}")
    print(f"💬 流式片段数: {len(chunks)}  最终文案: {result.get('output')!r}")

    gen = [e for e in events if e["type"] == MessageType.VIDEO_AGENT_GENERATED.value]
    assert gen, f"未收到 video_agent_generated 事件；events={[e['type'] for e in events]}"

    extra = gen[-1]["extra"]
    videos = extra.get("videos") or result.get("videos") or []
    print(f"🎬 videos[] = {videos}")
    print("=" * 80 + "\n")

    assert videos, "videos[] 为空（React Agent 未成功出片或未收集到 artifact）"
    assert extra.get("count") == len(videos)
    first = videos[0]
    assert first.get("video_url"), "缺少 video_url"
    assert ".mp4" in first["video_url"] or "http" in first["video_url"]
    # 仅暴露最小字段：不应再下发 model / prompt / resolution / aspect_ratio / duration
    for leaked in ("model", "prompt", "resolution", "aspect_ratio", "duration"):
        assert leaked not in first, f"不应暴露字段 {leaked}"
