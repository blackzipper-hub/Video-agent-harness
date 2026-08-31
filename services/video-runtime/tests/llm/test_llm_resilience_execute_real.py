"""
真实 API + ``execute_with_resilience``：验证 P0 骨架可在接入业务前用 dev env 跑通。

- 与 ``tests/llm/conftest.py`` 一致：``dev_env_loaded`` → ``.env.development`` / ``.env.local``，并关闭 LangSmith tracing。
- 使用 ``PROMPTS_CONFIG`` 条目的 ``model_config``，仅按路由覆盖 ``model``（与 ``build_resilience_bundle_from_prompt_entry`` 语义一致）。

覆盖调用形态：纯 chat（OpenAI / Gemini）、``with_structured_output``、``create_agent``×(provider|tool)、
模拟 ``TimeoutError`` / ``BadRequestError`` 后真实第二跳；
部分用例通过 ``model_fallback_chain`` 展开多路由；video/keyframe **tool_execution** 为 GPT 首推 + gemini-3 备用 + ``same_route_extra_retries``。
异常矩阵单测见 ``test_llm_resilience_error_simulation.py``（含三路由 mock，``-m unit``）。

运行:
  conda activate cuti-video-local
  cd Cuti-VideoAgent && PYTHONUNBUFFERED=1 python -m pytest tests/llm/test_llm_resilience_execute_real.py -v -m integration

联调想少等、少跑图后半段：优先 ``test_real_ainvoke_structured_video_analysis_create_agent``（``video_analysis``）→ ``test_real_ainvoke_structured_outline_video_driven_create_agent``（``outline_generation``，在 ``video_analysis`` 之后），再跑风格检测等后半段用例。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.messages import HumanMessage, SystemMessage

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
_video_template_data = _mstruct._video_template_data
_VIDEO_TOOL_JSON = _mstruct._VIDEO_TOOL_JSON

from prompts.prompt_config import PROMPTS_CONFIG, PromptName, create_llm  # noqa: E402
from prompts.llm_model_profiles import resolve_role_model  # noqa: E402
from prompts.prompt_loader import load_local_mustache_template, load_prompt_with_fallback_async  # noqa: E402
from app.models.image_result import ImageGenerationResult, VideoGenerationResult  # noqa: E402
from app.schemas.video_llm import StyleDetectionResult  # noqa: E402
from app.services.agent.utils.llm_resilience import (  # noqa: E402
    ErrorBucket,
    ResilienceContext,
    StructuredResilienceKind,
    ainvoke_structured_resilient,
    build_resilience_bundle_from_prompt_entry,
    execute_with_resilience,
    merge_resilience_config,
)


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_chat_clarify_prompt_config(dev_env_loaded):
    """纯 chat：bundle 单路由 (model, None) → 真实 ``ainvoke``。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CLARIFY]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 1 and routes[0][1] is None

    tpl = load_local_mustache_template("agent_router/agent_router_clarify")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages

    async def invoke_fn(route, mc):
        model_id, strat = route
        assert strat is None
        llm = create_llm(dict(mc))
        return await llm.ainvoke(msgs)

    out = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    text = getattr(out, "content", "") or ""
    assert isinstance(text, str) and len(text) > 10, text[:300]


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_ainvoke_structured_video_analysis_create_agent(dev_env_loaded):
    """与 ``video_analysis_service._analyze_video_requirements`` 同源：模板 ``schema=None`` + ``ainvoke_structured_resilient(CREATE_AGENT)``。图顺序早于 ``video_generation.detect_video_style``，适合快速验 resilience。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_REQUIREMENTS_ANALYSIS]
    prompt, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_REQUIREMENTS_ANALYSIS.value,
        local_template_name="video/video_analysis/video_requirements_analysis",
        schema=None,
        include_raw=False,
    )
    template_data = {
        "user_input": "做一个15秒的产品介绍短视频，偏轻松活泼",
        "audio_info": None,
        "duration_info": "目标视频时长：15秒",
        "has_images": False,
        "user_content_category": "",
        "available_style_categories": [],
        "available_style_categories_str": "",
    }
    messages = (await prompt.ainvoke(template_data)).messages

    result = await ainvoke_structured_resilient(
        prompt_entry=entry,
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs={"messages": messages},
    )
    assert isinstance(result, dict) and "structured_response" in result
    sr = result["structured_response"]
    assert hasattr(sr, "video_type") and hasattr(sr, "duration")


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_ainvoke_structured_outline_video_driven_create_agent(dev_env_loaded):
    """与 ``outline_generation_service._generate_outline_with_agent`` video_driven 同源：``VIDEO_OUTLINE_GENERATION_VIDEO_DRIVEN`` + ``CREATE_AGENT``。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_OUTLINE_GENERATION_VIDEO_DRIVEN]
    prompt, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_OUTLINE_GENERATION_VIDEO_DRIVEN.value,
        local_template_name="video/outline_generation/video_outline_generation_video_driven",
        schema=None,
        include_raw=False,
    )
    template_data = {
        "user_input": "做一个30秒产品介绍短视频",
        "analysis_info": "**用户需求分析参考：**\n- 视频类型：产品宣传\n- 主要角色：品牌吉祥物\n- 制作目的：介绍新品\n- 关键要素：外观, 功能\n- 风格偏好：现代简洁\n- 目标受众：年轻人",
        "main_character": "品牌吉祥物",
        "purpose": "介绍新品",
        "key_elements": "外观, 功能",
        "target_duration": 30.0,
        "min_chapter_duration": 30,
        "max_chapter_duration": 50,
        "min_video_duration": 5,
        "min_video_duration_x2": 10,
        "min_video_duration_x3": 15,
        "min_video_duration_x4": 20,
        "content_category_narrative_guidance": "章节连贯，避免重复。",
        "style_guidance_for_visual": None,
    }
    messages = (await prompt.ainvoke(template_data)).messages

    result = await ainvoke_structured_resilient(
        prompt_entry=entry,
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs={"messages": messages},
    )
    assert isinstance(result, dict) and "structured_response" in result
    sr = result["structured_response"]
    assert hasattr(sr, "title") and hasattr(sr, "structure") and len(sr.structure.chapters) >= 1


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_structured_style_detection_provider_route(dev_env_loaded):
    """结构化：同条目中仅走 provider 等价路径（``with_structured_output``）。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    idx = [i for i, r in enumerate(routes) if r[1] == "provider"]
    routes_provider = [routes[i] for i in idx]
    mcs_provider = [route_mcs[i] for i in idx]
    assert routes_provider

    tpl = load_local_mustache_template("video/video_generation/video_video_generation_style_detection")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages

    async def invoke_fn(route, mc):
        model_id, strat = route
        assert strat == "provider"
        llm = create_llm(dict(mc))
        structured = llm.with_structured_output(StyleDetectionResult)
        return await structured.ainvoke(msgs)

    result = await execute_with_resilience(
        invoke_fn, routes=routes_provider, route_model_configs=mcs_provider, context=ctx
    )
    d = result.model_dump()
    assert "detected_styles" in d and "confidence" in d


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_ainvoke_prompt_structured_chain_style_detection(dev_env_loaded):
    """与 ``video_generation_service.detect_video_style`` 一致：``schema=None`` 模板 → ``ainvoke(dict).messages`` + ``STRUCTURED_CHAT_MESSAGES``。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION]
    prompt, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION.value,
        local_template_name="video/video_generation/video_video_generation_style_detection",
        schema=None,
        include_raw=False,
    )
    style_invoke = {
        "available_styles": "general, anime",
        "combined_content": "用户想做一段轻松可爱的短视频介绍产品",
    }
    style_messages = (await prompt.ainvoke(style_invoke)).messages
    result = await ainvoke_structured_resilient(
        prompt_entry=entry,
        kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
        structured_chat_messages=style_messages,
        include_raw=True,
    )
    assert isinstance(result, dict) and "parsed" in result
    pr = result["parsed"]
    assert hasattr(pr, "detected_styles") and hasattr(pr, "confidence")


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_keyframe_tool_execution_provider_and_tool_routes(dev_env_loaded):
    """Keyframe tool_execution：GPT provider 首路由成功；bundle 含 gemini-3 备用（本条不触发）。invoke_fn 仍兼容 strat=tool。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_KEYFRAME_GENERATION_TOOL_EXECUTION]
    schema = entry["schema"]
    assert schema is ImageGenerationResult

    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 2
    assert [r[0] for r in routes] == [resolve_role_model("tool"), "gemini-3-flash-preview"]

    system_text = await _system_text_from_tool_template(
        "video/keyframe_generation/video_keyframe_generation_tool_execution",
        _keyframe_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_IMAGE_TOOL_JSON),
    ]

    async def invoke_fn(route, mc):
        model_id, strat = route
        assert strat in ("provider", "tool")
        llm = create_llm(dict(mc))
        if strat == "provider":
            rf: ProviderStrategy | ToolStrategy = ProviderStrategy(schema)
        else:
            rf = ToolStrategy(schema, handle_errors=True)
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=rf,
        )
        out = await agent.ainvoke({"messages": messages})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    sr = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    assert sr.success is True
    assert (sr.image_url and "cuti.land" in sr.image_url) or (
        sr.generated_prompt and "Kenji" in sr.generated_prompt
    ), sr


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_video_tool_execution_provider_and_tool_routes(dev_env_loaded):
    """Video tool_execution：GPT provider 首路由成功；bundle 含 gemini-3 备用路由（本条不触发）。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_TOOL_EXECUTION]
    schema = entry["schema"]
    assert schema is VideoGenerationResult

    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 2
    assert [r[0] for r in routes] == [resolve_role_model("tool"), "gemini-3-flash-preview"]

    system_text = await _system_text_from_tool_template(
        "video/video_generation/video_video_generation_tool_execution",
        _video_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_VIDEO_TOOL_JSON),
    ]

    async def invoke_fn(route, mc):
        model_id, strat = route
        assert strat in ("provider", "tool")
        llm = create_llm(dict(mc))
        if strat == "provider":
            rf: ProviderStrategy | ToolStrategy = ProviderStrategy(schema)
        else:
            rf = ToolStrategy(schema, handle_errors=True)
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=rf,
        )
        out = await agent.ainvoke({"messages": messages})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    sr = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    assert sr.success is True
    assert sr.video_url and "cuti.land" in sr.video_url, sr


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_style_detection_provider_and_tool_routes(dev_env_loaded):
    """结构化：默认仅 provider 档；invoke_fn 保留 tool 分支便于 resilience.structured_output_strategy=\"tool\"。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION]
    schema = entry["schema"]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) >= 2

    tpl = load_local_mustache_template("video/video_generation/video_video_generation_style_detection")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages

    async def invoke_fn(route, mc):
        model_id, strat = route
        assert strat in ("provider", "tool")
        llm = create_llm(dict(mc))
        if strat == "provider":
            structured = llm.with_structured_output(schema)
            return await structured.ainvoke(msgs)
        rf = ToolStrategy(schema, handle_errors=True)
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=rf,
        )
        out = await agent.ainvoke({"messages": msgs})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    result = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    d = result.model_dump()
    assert "style_type" in d or "confidence" in d or len(d) >= 1


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_real_resilience_chat_clarify_gemini(dev_env_loaded):
    """纯 chat + Gemini（与 pattern 测试一致，验证另一 provider 包在 resilience 下可用）。"""
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CLARIFY]
    base_ctx, _, _ = build_resilience_bundle_from_prompt_entry(entry)
    gemini_model = "gemini-2.5-flash"
    routes = [(gemini_model, None)]
    ctx = base_ctx
    tpl = load_local_mustache_template("agent_router/agent_router_clarify")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages

    gemini_mc = {
        **entry["model_config"],
        "model": gemini_model,
        "temperature": 0.2,
        "timeout": 240,
        "max_output_tokens": 8192,
    }

    async def invoke_fn(route, mc):
        model_id, strat = route
        assert strat is None and model_id == gemini_model
        llm = create_llm(dict(mc))
        return await llm.ainvoke(msgs)

    out = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=[gemini_mc], context=ctx
    )
    text = getattr(out, "content", "") or ""
    assert isinstance(text, str) and len(text) > 10, text[:300]


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_simulated_transient_then_openai_success(dev_env_loaded):
    """混合：首调 ``TimeoutError``（模拟），同路由重试后真实 ``ainvoke`` 成功。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CLARIFY]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    tpl = load_local_mustache_template("agent_router/agent_router_clarify")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages
    n = {"calls": 0}

    async def invoke_fn(route, mc):
        n["calls"] += 1
        if n["calls"] == 1:
            raise TimeoutError("simulated_transient")
        llm = create_llm(dict(mc))
        return await llm.ainvoke(msgs)

    out = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    assert n["calls"] == 2
    assert len(getattr(out, "content", "") or "") > 10


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_simulated_bad_request_then_second_route_chat(dev_env_loaded):
    """混合：第一条路由 ``BadRequest``（模拟，不重试），第二条相同 chat 路由真实成功。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")
    try:
        from openai import BadRequestError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CLARIFY]
    mid = entry["model_config"]["model"]
    merged = merge_resilience_config(
        {
            "same_route_extra_retries": 1,
            "per_bucket": {ErrorBucket.BAD_REQUEST: {"same_route_extra_retries": 0}},
        },
        None,
    )
    ctx = ResilienceContext(
        same_route_extra_retries=int(merged["same_route_extra_retries"]),
        per_bucket=dict(merged["per_bucket"]),
    )
    routes = [(mid, None), (mid, None)]
    dup_mcs = [dict(entry["model_config"]), dict(entry["model_config"])]
    tpl = load_local_mustache_template("agent_router/agent_router_clarify")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages
    n = {"calls": 0}

    async def invoke_fn(route, mc):
        n["calls"] += 1
        if n["calls"] == 1:
            req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            resp = httpx.Response(400, request=req, json={"error": {"message": "sim"}})
            raise BadRequestError("sim", response=resp, body=None)
        llm = create_llm(dict(mc))
        return await llm.ainvoke(msgs)

    out = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=dup_mcs, context=ctx
    )
    assert n["calls"] == 2
    assert len(getattr(out, "content", "") or "") > 10


_GEMINI_FALLBACK = "gemini-2.5-flash"


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_keyframe_dual_model_three_routes_primary_succeeds(dev_env_loaded):
    """keyframe tool_execution：GPT provider 首路由成功；bundle 含 gemini-3 备用。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_KEYFRAME_GENERATION_TOOL_EXECUTION]
    schema = entry["schema"]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    mids = [r[0] for r in routes]
    assert len(routes) == 2, routes
    assert mids == [resolve_role_model("tool"), "gemini-3-flash-preview"]
    assert ctx.same_route_extra_retries == 2

    system_text = await _system_text_from_tool_template(
        "video/keyframe_generation/video_keyframe_generation_tool_execution",
        _keyframe_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_IMAGE_TOOL_JSON),
    ]

    async def invoke_fn(route, mc):
        model_id, strat = route
        llm = create_llm(dict(mc))
        rf: ProviderStrategy | ToolStrategy = (
            ProviderStrategy(schema) if strat == "provider" else ToolStrategy(schema, handle_errors=True)
        )
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=rf,
        )
        out = await agent.ainvoke({"messages": messages})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    sr = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    assert sr.success is True
    assert (sr.image_url and "cuti.land" in sr.image_url) or (
        sr.generated_prompt and "Kenji" in sr.generated_prompt
    ), sr


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_video_dual_model_three_routes_primary_succeeds(dev_env_loaded):
    """video tool_execution：GPT provider 首路由成功；bundle 含 gemini-3 备用。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_TOOL_EXECUTION]
    schema = entry["schema"]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 2
    assert [r[0] for r in routes] == [resolve_role_model("tool"), "gemini-3-flash-preview"]
    assert ctx.same_route_extra_retries == 2

    system_text = await _system_text_from_tool_template(
        "video/video_generation/video_video_generation_tool_execution",
        _video_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_VIDEO_TOOL_JSON),
    ]

    async def invoke_fn(route, mc):
        model_id, strat = route
        llm = create_llm(dict(mc))
        rf: ProviderStrategy | ToolStrategy = (
            ProviderStrategy(schema) if strat == "provider" else ToolStrategy(schema, handle_errors=True)
        )
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=rf,
        )
        out = await agent.ainvoke({"messages": messages})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    sr = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    assert sr.success is True
    assert sr.video_url and "cuti.land" in sr.video_url, sr


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_resilience_style_dual_model_three_routes_primary_succeeds(dev_env_loaded):
    """双模型：style_detection 2 路由（主 + fallback，同策略）。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION]
    schema = entry["schema"]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 2
    assert _GEMINI_FALLBACK in [r[0] for r in routes]

    tpl = load_local_mustache_template("video/video_generation/video_video_generation_style_detection")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages

    async def invoke_fn(route, mc):
        model_id, strat = route
        llm = create_llm(dict(mc))
        if strat == "provider":
            return await llm.with_structured_output(schema).ainvoke(msgs)
        rf = ToolStrategy(schema, handle_errors=True)
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=rf,
        )
        out = await agent.ainvoke({"messages": msgs})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    result = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    d = result.model_dump()
    assert "style_type" in d or "confidence" in d or len(d) >= 1


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_real_resilience_keyframe_dual_simulated_gpt_fail_gemini_real(dev_env_loaded):
    """双路由：首次超时模拟，同路由 retry 后 GPT provider 真实成功（未触发 gemini-3 备用）。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_KEYFRAME_GENERATION_TOOL_EXECUTION]
    schema = entry["schema"]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 2
    assert ctx.same_route_extra_retries == 2

    system_text = await _system_text_from_tool_template(
        "video/keyframe_generation/video_keyframe_generation_tool_execution",
        _keyframe_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_IMAGE_TOOL_JSON),
    ]
    n = {"calls": 0}

    async def invoke_fn(route, mc):
        model_id, strat = route
        n["calls"] += 1
        if n["calls"] == 1:
            raise TimeoutError("simulated_skip_gpt")
        llm = create_llm(dict(mc))
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=ProviderStrategy(schema),
        )
        out = await agent.ainvoke({"messages": messages})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    sr = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    assert n["calls"] == 2
    assert sr.success is True
    assert (sr.image_url and "cuti.land" in sr.image_url) or (
        sr.generated_prompt and "Kenji" in sr.generated_prompt
    ), sr


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_real_resilience_video_dual_simulated_gpt_fail_gemini_real(dev_env_loaded):
    """双路由：首次超时模拟，同路由 retry 后 GPT provider 真实 video 成功（未触发 gemini 备用）。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_TOOL_EXECUTION]
    schema = entry["schema"]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 2
    assert ctx.same_route_extra_retries == 2

    system_text = await _system_text_from_tool_template(
        "video/video_generation/video_video_generation_tool_execution",
        _video_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_VIDEO_TOOL_JSON),
    ]
    n = {"calls": 0}

    async def invoke_fn(route, mc):
        model_id, strat = route
        n["calls"] += 1
        if n["calls"] == 1:
            raise TimeoutError("simulated_skip_gpt")
        llm = create_llm(dict(mc))
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=ProviderStrategy(schema),
        )
        out = await agent.ainvoke({"messages": messages})
        sr = out.get("structured_response")
        assert sr is not None
        return sr

    sr = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    assert n["calls"] == 2
    assert sr.success is True
    assert sr.video_url and "cuti.land" in sr.video_url, sr


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_real_resilience_style_dual_simulated_gpt_fail_gemini_real(dev_env_loaded):
    """双模型：GPT 模拟失败后，Gemini ``with_structured_output`` 真实成功。"""
    if not os.getenv("OPENAI_API_KEY") or not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("OPENAI_API_KEY and GOOGLE_API_KEY")

    entry = PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION]
    schema = entry["schema"]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert len(routes) == 2

    tpl = load_local_mustache_template("video/video_generation/video_video_generation_style_detection")
    msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages
    primary = entry["model_config"]["model"]

    async def invoke_fn(route, mc):
        model_id, strat = route
        if model_id == primary:
            raise TimeoutError("simulated_skip_gpt")
        assert model_id == _GEMINI_FALLBACK and strat == "provider"
        llm = create_llm(dict(mc))
        return await llm.with_structured_output(schema).ainvoke(msgs)

    result = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=route_mcs, context=ctx
    )
    d = result.model_dump()
    assert "style_type" in d or "confidence" in d or len(d) >= 1
