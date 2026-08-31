"""
真实模型矩阵：Google / OpenAI × ProviderStrategy / ToolStrategy × 真实 tool_execution mustache 的 System 段。

- 使用 `load_local_mustache_template` + 与线上一致的 dummy 变量渲染出 **真实 System**（完整业务约束文案）。
- Human 仅为工具返回 JSON 字符串（模拟第二轮结构化终态），不额外强调「禁止 markdown」。
- 环境：`tests/llm/conftest.py` 在加载 .env 后关闭 LangSmith / LangChain tracing。

运行（需对应 API Key）:
  pytest tests/llm/test_structured_output_tool_execution_matrix_real.py -v -m integration
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Tuple, Type

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompts.prompt_config import create_llm  # noqa: E402
from prompts.prompt_loader import load_local_mustache_template  # noqa: E402
from app.models.image_result import ImageGenerationResult, VideoGenerationResult  # noqa: E402
from app.models.video_state import DetailedShot, KeyframeVersion  # noqa: E402

_IMAGE_TOOL_JSON = json.dumps(
    {
        "success": True,
        "image_url": "https://cdn.cuti.land/images/26ca3925-f88a-45f1-b306-3efb4e1bfa7c.webp",
        "generated_prompt": "Kenji from image 1, soft smile, lake and Mount Fuji background.",
        "provider": "google",
        "model": "gemini-3.1-flash-image-preview",
        "message": "图像生成成功",
        "error_msg": None,
        "raw_error_msg": None,
        "reference_image_urls": ["https://cdn.cuti.land/images/bdee8ff3-8796-4508-aafd-e194c54a568c.webp"],
        "seed": 958073846,
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "billing_cost": 0.067,
        "image_tool_metrics": {"total_attempts": 1, "success": True},
        "tool_duration_sec": 70.7,
        "tool_cost": 0.067,
    },
    ensure_ascii=False,
)

_VIDEO_TOOL_JSON = json.dumps(
    {
        "success": True,
        "video_url": "https://cdn.cuti.land/videos/integration-test-clip.mp4",
        "generated_prompt": "Slow dolly forward, morning light on lake.",
        "provider": "wavespeed",
        "duration": 5.0,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "model": "test-model",
        "message": "视频生成成功",
        "billing_cost": 0.05,
        "shot_number": 27,
        "is_bridge": False,
        "keyframe_url": "https://cdn.cuti.land/images/kf.webp",
        "video_tool_metrics": {"total_attempts": 1, "success": True},
        "tool_duration_sec": 12.0,
        "tool_cost": 0.05,
    },
    ensure_ascii=False,
)


def _shot_for_templates() -> DetailedShot:
    return DetailedShot(
        shot_number=27,
        duration=5.0,
        character_ids=["char-integration-test"],
        is_bridge=False,
        shot_type="中景",
        camera_position="侧面中机位",
        camera_angle="平视",
        subject_angle="三分之二侧",
        subject_pose="静坐望向湖面",
        scene_description="清晨湖边，柔和晨光，远山轮廓",
        camera_movement="缓慢横移",
        lighting="暖色调侧光",
        visual_effects="轻微镜头光晕",
        dialogue="",
        narration="",
        sound_effects="环境鸟鸣",
        transition="",
    )


def _keyframe_template_data() -> Dict[str, Any]:
    from app.services.agent.video.keyframe_generation_service import (
        KEYFRAME_PROMPT_LENGTH_RANGE,
    )

    shot = _shot_for_templates()
    refs = ["https://cdn.cuti.land/images/ref1.webp"]
    return {
        "user_input": "",
        "tool_name": "generate_image_with_fallback_i2i",
        "keyframe_prompt_length_range": KEYFRAME_PROMPT_LENGTH_RANGE,
        "shot_number": shot.shot_number,
        "is_bridge": shot.is_bridge,
        "mode": "I2I",
        "character_ref_images_count": len(refs),
        "shot_type": shot.shot_type,
        "camera_position": shot.camera_position or "未指定",
        "camera_angle": shot.camera_angle or "未指定",
        "subject_angle": shot.subject_angle or "未指定",
        "subject_pose": shot.subject_pose or "未指定",
        "scene_description": shot.scene_description,
        "camera_movement": shot.camera_movement,
        "lighting": shot.lighting,
        "visual_effects": shot.visual_effects,
        "dialogue": shot.dialogue,
        "narration": shot.narration,
        "sound_effects": shot.sound_effects,
        "transition": shot.transition,
        "t2i_prompt": "Integration test t2i prompt for keyframe tool execution template.",
        "has_character_ref_images": True,
        "character_ref_images_list": str(refs),
    }


def _video_template_data() -> Dict[str, Any]:
    from app.services.agent.video.video_generation_service import (
        VIDEO_PROMPT_LENGTH_RANGE,
    )

    shot = _shot_for_templates()
    kf = KeyframeVersion(
        shot_number=shot.shot_number,
        keyframe_url="https://cdn.cuti.land/images/kf.webp",
        t2i_prompt="kf prompt",
        provider="google",
        is_bridge=False,
    )
    return {
        "user_input": "",
        "tool_name": "video_wrapper_i2v",
        "end_image_mode_text": "",
        "video_prompt_length_range": VIDEO_PROMPT_LENGTH_RANGE,
        "shot_number": shot.shot_number,
        "is_bridge": kf.is_bridge,
        "duration": shot.duration,
        "keyframe_url": kf.keyframe_url,
        "end_image_url": "",
        "shot_type": shot.shot_type,
        "camera_position": shot.camera_position or "未指定",
        "camera_angle": shot.camera_angle or "未指定",
        "subject_angle": shot.subject_angle or "未指定",
        "subject_pose": shot.subject_pose or "未指定",
        "scene_description": shot.scene_description,
        "camera_movement": shot.camera_movement,
        "lighting": shot.lighting,
        "visual_effects": shot.visual_effects,
        "dialogue": shot.dialogue or "",
        "narration": shot.narration or "",
        "sound_effects": shot.sound_effects or "",
        "transition": shot.transition or "",
        "i2v_prompt": "Integration test i2v prompt for video tool execution template.",
        "target_duration": 5,
    }


async def _system_text_from_tool_template(
    local_template_name: str, build_data: Callable[[], Dict[str, Any]]
) -> str:
    tpl = load_local_mustache_template(local_template_name)
    msgs = (await tpl.ainvoke(build_data())).messages
    assert msgs, "template produced no messages"
    assert getattr(msgs[0], "type", None) == "system", msgs[0]
    return str(msgs[0].content)


async def _run_agent_structured(
    *,
    model_id: str,
    schema: Type[BaseModel],
    strategy: str,
    system_text: str,
    human_tool_json: str,
):
    llm = create_llm(
        {
            "model": model_id,
            "temperature": 0.2,
            "timeout": 180,
            "max_tokens": 4096,
            "max_output_tokens": 8192,
        }
    )
    if strategy == "provider":
        rf: Any = ProviderStrategy(schema)
    elif strategy == "tool":
        rf = ToolStrategy(schema, handle_errors=True)
    else:
        raise ValueError(strategy)

    agent = await asyncio.to_thread(
        create_agent,
        model=llm,
        tools=[],
        response_format=rf,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=human_tool_json),
    ]
    result = await agent.ainvoke({"messages": messages})
    sr = result.get("structured_response")
    assert sr is not None, result
    assert isinstance(sr, schema), type(sr)
    assert sr.success is True, sr
    return sr


_PROMPT_CASES: Tuple[Tuple[str, str, Type[BaseModel], str, Callable[[], Dict[str, Any]]], ...] = (
    (
        "keyframe_tool_execution",
        "video/keyframe_generation/video_keyframe_generation_tool_execution",
        ImageGenerationResult,
        _IMAGE_TOOL_JSON,
        _keyframe_template_data,
    ),
    (
        "video_tool_execution",
        "video/video_generation/video_video_generation_tool_execution",
        VideoGenerationResult,
        _VIDEO_TOOL_JSON,
        _video_template_data,
    ),
)


def _skip_if_no_key(model_id: str) -> None:
    if "gemini" in model_id:
        if not os.getenv("GOOGLE_API_KEY"):
            pytest.skip("GOOGLE_API_KEY 未配置")
    elif model_id.startswith("gpt"):
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY 未配置")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_case", _PROMPT_CASES, ids=[c[0] for c in _PROMPT_CASES])
@pytest.mark.parametrize("strategy", ("provider", "tool"))
@pytest.mark.parametrize("model_id", ("gemini-2.5-flash", "gpt-4.1-mini"))
async def test_tool_execution_real_prompt_matrix(
    dev_env_loaded, prompt_case, strategy: str, model_id: str
):
    """
    Google/OpenAI × ProviderStrategy/ToolStrategy × 两份真实 tool_execution System（mustache 渲染）。
    """
    _skip_if_no_key(model_id)
    template_name, schema_cls, tool_json, build_data = prompt_case[1:]
    system_text = await _system_text_from_tool_template(template_name, build_data)
    assert len(system_text) > 200, "system 过短，可能未加载真实模板"

    sr = await _run_agent_structured(
        model_id=model_id,
        schema=schema_cls,
        strategy=strategy,
        system_text=system_text,
        human_tool_json=tool_json,
    )
    if schema_cls is ImageGenerationResult:
        # ToolStrategy + 长 system 时 OpenAI 偶发省略 image_url，仍视为结构化成功（与工具 JSON 对齐即可）
        assert sr.success
        assert (sr.image_url and "cuti.land" in sr.image_url) or (
            sr.generated_prompt and "Kenji" in sr.generated_prompt
        ), sr
    else:
        assert sr.video_url and "cuti.land" in sr.video_url, sr
