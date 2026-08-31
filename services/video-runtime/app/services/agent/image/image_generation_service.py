"""
图像生成服务

Agent state 与图片 (LangGraph best practice):
- 以 messages 为单一数据源，通过 parse_image_artifacts_from_messages 解析出「当前+历史」所有图片。
- 解析结果用于：1) prompt 中的参考图描述 2) context.reference_image_urls 供 i2i 工具使用。
- 多轮对话：用户上传 + 历史生成图都会纳入，便于模型选择 t2i / i2i。
"""
import asyncio
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from ....services.agent.base_agent import MessageType
from ....models.video_state import UserInput
from ....models.user_options import UserOption
from ....services.tool_service import ToolService
from .image_state_utils import parse_image_artifacts_from_messages

logger = logging.getLogger(__name__)


def _stream_chunk_to_text(raw_chunk: Any) -> str:
    """Normalize modern multimodal LangChain chunks to plain streamed text."""
    if raw_chunk is None:
        return ""
    if isinstance(raw_chunk, str):
        return raw_chunk
    if isinstance(raw_chunk, list):
        return "".join(filter(None, (_stream_chunk_to_text(item) for item in raw_chunk)))
    if isinstance(raw_chunk, dict):
        if raw_chunk.get("type") in {"reasoning", "thinking"}:
            return ""
        return _stream_chunk_to_text(raw_chunk.get("text") or raw_chunk.get("content"))
    return _stream_chunk_to_text(getattr(raw_chunk, "text", None))


async def build_prompt_for_image_generation(
    user_input_data: UserInput,
    messages: List[BaseMessage] = None,
    detected_language: Optional[str] = None,
) -> Tuple[List[BaseMessage], List[str]]:
    """构建图像生成的 prompt messages，包含当前与历史图片（从 state/messages 解析）。
    
    Returns:
        Tuple[List[BaseMessage], List[str]]: (messages, reference_image_urls)
    """
    from ..utils.prompt_utils import apply_language_suffix_to_system_message_in_messages, attach_images_to_messages
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from prompts.prompt_config import PromptName

    # 当前轮用户上传的 URL
    current_upload_urls: List[str] = []
    if user_input_data.images:
        current_upload_urls = [img.url for img in user_input_data.images if getattr(img, "url", None)]

    # 从消息中解析所有图片（user_upload + generated），单一数据源 = messages
    artifacts, reference_image_urls = parse_image_artifacts_from_messages(messages, current_upload_urls)
    all_images = artifacts  # ImageArtifact 有 .url 属性，与模板兼容

    # 仅加载模板；流式 agent 由 llm_resilience 路由 + create_agent（无 structured）创建
    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.IMAGE_AGENT_TOOL_EXECUTION.value,
        local_template_name="image/image_agent_tool_execution",
        schema=None,
        include_raw=False,
    )

    has_images = len(all_images) > 0
    template_data = {
        "user_input": user_input_data.user_input,
        "has_images": has_images,
        "images_count": len(all_images) if has_images else 0,
        "images": [{"index": i + 1, "url": img.url} for i, img in enumerate(all_images)] if has_images else [],
    }

    formatted_messages = (await prompt_template.ainvoke(template_data)).messages
    apply_language_suffix_to_system_message_in_messages(formatted_messages, detected_language)

    if has_images:
        image_urls = [img.url for img in all_images]
        formatted_messages = attach_images_to_messages(formatted_messages, image_urls)

    return formatted_messages, reference_image_urls


async def generate_image_with_agent(user_input_data: UserInput, messages: List[BaseMessage] = None, send_event_func=None, conversation_id: int = None, run_id: str = None, thread_id: str = None, detected_language: Optional[str] = None) -> Dict[str, Any]:
    """使用React Agent生成图像"""
    try:
        logger.info(f"🎨 开始图像生成")
        
        # 同时提供 t2i 与 i2i 两个工具，由模型根据 prompt（有无参考图、用户意图）自行选择；优先用 user_input_data 里的 user_option
        user_option = user_input_data.user_option if user_input_data.user_option else UserOption.default()
        tools_info = ToolService.get_image_generation_tools(user_option, mode=None)
        image_tools = tools_info.tool_objects
        logger.info(f"🔧 已注入 t2i + i2i 两个工具，由模型根据上下文选择")
        
        # 构建包含图片的 prompt，并从 messages 解析出所有参考图（state = messages）
        prompt_messages, reference_image_urls = await build_prompt_for_image_generation(
            user_input_data, messages, detected_language
        )

        # 如果有历史消息，添加到 prompt_messages 前面
        if messages:
            all_messages = messages + prompt_messages
        else:
            all_messages = prompt_messages

        from langchain.agents import create_agent
        from ....tools.context_schemas import ImageGenerationContext
        from ....models.tool_enums import AspectRatio, Resolution, DefaultValues
        from ..video.agent_video_constants import SKIP_IMAGE_AGENT_CONSISTENCY_CHECK
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName
        from prompts.prompt_loader import create_llm_from_model_config
        from ..utils.llm_resilience import (
            build_resilience_bundle_from_prompt_entry,
            execute_with_resilience,
        )
        from langchain_core.messages import AIMessage

        context = ImageGenerationContext(
            aspect_ratio=(
                AspectRatio(user_option.aspect_ratio.value)
                if user_option and user_option.aspect_ratio
                else DefaultValues.IMAGE_ASPECT_RATIO
            ),
            resolution=(
                Resolution(user_option.resolution.value)
                if user_option and user_option.resolution
                else DefaultValues.IMAGE_RESOLUTION
            ),
            model=tools_info.tool_type if tools_info and tools_info.tools else None,
            reference_image_urls=reference_image_urls if reference_image_urls else None,
            language=detected_language,
            skip_consistency_check=SKIP_IMAGE_AGENT_CONSISTENCY_CHECK,
        )

        _img_entry = PROMPTS_CONFIG[PromptName.IMAGE_AGENT_TOOL_EXECUTION]
        _ctx, _routes, _route_mcs = build_resilience_bundle_from_prompt_entry(_img_entry)
        inputs = {"messages": all_messages}
        _orig_mc = len(messages) if messages else 0

        async def _invoke_image_stream(
            route: Tuple[str, Any], mc: Mapping[str, Any]
        ) -> Dict[str, Any]:
            model_id, _s = route
            llm = create_llm_from_model_config(dict(mc))
            image_react_agent = await asyncio.to_thread(
                create_agent,
                llm,
                tools=image_tools,
                context_schema=ImageGenerationContext,
            )
            full_content: List[str] = []
            agent_messages: List[Any] = []
            async for event in image_react_agent.astream_events(inputs, context=context, version="v2"):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    chunk_text = _stream_chunk_to_text(getattr(chunk, "content", None))
                    if chunk_text:
                        full_content.append(chunk_text)
                        if send_event_func and conversation_id:
                            await send_event_func(
                                event_type=MessageType.STREAMING_CHUNK,
                                conversation_id=conversation_id,
                                message=chunk_text,
                                extra_data={
                                    "target_event": MessageType.IMAGE_AGENT_GENERATED.value,
                                    "content_type": "text",
                                },
                            )
                elif kind == "on_chain_end" and event["name"] == "LangGraph":
                    result = event["data"]["output"]
                    agent_messages = result.get("messages", [])
            content = "".join(full_content) if full_content else ""
            if not content and agent_messages:
                last_msg = agent_messages[-1]
                if isinstance(last_msg, AIMessage):
                    content = _stream_chunk_to_text(
                        last_msg.content if hasattr(last_msg, "content") else ""
                    )
            new_messages = (
                agent_messages[_orig_mc:]
                if len(agent_messages) > _orig_mc
                else agent_messages
            )
            return {"messages": new_messages, "output": content}

        _stream_out = await execute_with_resilience(
            _invoke_image_stream,
            routes=_routes,
            route_model_configs=_route_mcs,
            context=_ctx,
            log_context={"phase": "image_agent_stream"},
        )
        new_messages = _stream_out["messages"]
        content = _stream_out["output"]

        # 发送图像生成完成事件
        if send_event_func and conversation_id:
            await send_event_func(
                event_type=MessageType.IMAGE_AGENT_GENERATED,
                conversation_id=conversation_id,
                message="图像生成完成",
                extra_data={
                    "image_content": content,
                    "run_id": run_id,
                    "thread_id": thread_id
                }
            )
        
        logger.info(f"✅ 图像生成完成")
        return {
            "messages": new_messages,
            "output": content
        }
        
    except Exception as e:
        logger.error(f"❌ 图像生成失败: {str(e)}")
        raise
