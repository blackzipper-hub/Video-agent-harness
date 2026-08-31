"""
少量真实 prompt × 多种调用方式（integration，需 GOOGLE_API_KEY）。

覆盖三类与线上一致的形态：
1. **纯 chat**：Mustache → messages → `create_llm.ainvoke`（无 schema）
2. **直连结构化**：同上 messages → `llm.with_structured_output(schema).ainvoke`（非 create_agent）
3. **Agent + ProviderStrategy**：真实 tool_execution 的 System + 工具 JSON human → `create_agent`（tools=[]）

复用 `test_all_prompts_matrix_log` 的全局占位 `_UNIVERSAL_DATA` 渲染模板；
复用 `test_structured_output_tool_execution_matrix_real` 的 keyframe system 抽取逻辑。

运行:
  conda activate cuti-video-local
  cd Cuti-VideoAgent && poetry run pytest tests/llm/test_prompt_invocation_patterns_real.py -v -m integration
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_LL_DIR = Path(__file__).resolve().parent


def _load_sibling_module(mod_name: str, file_name: str):
    path = _LL_DIR / file_name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mlog = _load_sibling_module("_prompt_matrix_log_for_data", "test_all_prompts_matrix_log.py")
_UNIVERSAL_DATA = _mlog._UNIVERSAL_DATA

_mstruct = _load_sibling_module("_struct_matrix_helpers", "test_structured_output_tool_execution_matrix_real.py")
_system_text_from_tool_template = _mstruct._system_text_from_tool_template
_keyframe_template_data = _mstruct._keyframe_template_data
_IMAGE_TOOL_JSON = _mstruct._IMAGE_TOOL_JSON


@pytest.fixture(scope="module")
def _gemini_cfg():
    return {
        "model": "gemini-2.5-flash",
        "temperature": 0.2,
        "timeout": 240,
        "max_output_tokens": 8192,
    }


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_pattern_1_plain_llm_chat_clarify_mustache(dev_env_loaded, _gemini_cfg):
    """Pattern 1: load_local_mustache_template → ainvoke(占位) → ChatModel.ainvoke（无结构化）。"""
    if not __import__("os").getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    from prompts.prompt_config import create_llm
    from prompts.prompt_loader import load_local_mustache_template

    tpl = load_local_mustache_template("agent_router/agent_router_clarify")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages
    assert len(msgs) >= 1

    llm = create_llm(_gemini_cfg)
    out = await llm.ainvoke(msgs)
    text = getattr(out, "content", "") or ""
    assert isinstance(text, str)
    assert len(text) > 10, text[:200]


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_pattern_2_structured_llm_style_detection_mustache(dev_env_loaded, _gemini_cfg):
    """Pattern 2: Mustache → messages → with_structured_output(schema).ainvoke（无 create_agent）。"""
    if not __import__("os").getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    from prompts.prompt_config import create_llm
    from prompts.prompt_loader import load_local_mustache_template
    from app.schemas.video_llm import StyleDetectionResult

    tpl = load_local_mustache_template("video/video_generation/video_video_generation_style_detection")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages

    llm = create_llm(_gemini_cfg)
    structured = llm.with_structured_output(StyleDetectionResult)
    result = await structured.ainvoke(msgs)
    assert hasattr(result, "model_dump")
    d = result.model_dump()
    assert "style_type" in d or "confidence" in d or len(d) >= 1


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_pattern_3_create_agent_provider_keyframe_tool_execution_system(dev_env_loaded, _gemini_cfg):
    """Pattern 3: 真实 keyframe tool_execution System + 工具 JSON → create_agent + ProviderStrategy。"""
    if not __import__("os").getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    import asyncio

    from langchain.agents import create_agent
    from langchain.agents.structured_output import ProviderStrategy
    from langchain_core.messages import HumanMessage, SystemMessage

    from prompts.prompt_config import create_llm
    from app.models.image_result import ImageGenerationResult

    system_text = await _system_text_from_tool_template(
        "video/keyframe_generation/video_keyframe_generation_tool_execution",
        _keyframe_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_IMAGE_TOOL_JSON),
    ]
    llm = create_llm(_gemini_cfg)
    agent = await asyncio.to_thread(
        create_agent,
        model=llm,
        tools=[],
        response_format=ProviderStrategy(ImageGenerationResult),
    )
    out = await agent.ainvoke({"messages": messages})
    sr = out.get("structured_response")
    assert sr is not None
    assert getattr(sr, "success", None) is True
    assert sr.image_url
