"""
音乐生成服务
"""
import asyncio
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from ....services.agent.base_agent import MessageType
from ....tools.music.suno import get_suno_tools
from ....models.video_state import UserInput

logger = logging.getLogger(__name__)


async def build_prompt_for_music_generation(user_input_data: UserInput, detected_language: Optional[str] = None) -> List[BaseMessage]:
    """构建音乐创作的 prompt messages，包含图片"""
    from ..utils.prompt_utils import apply_language_suffix_to_system_message_in_messages, attach_images_to_messages
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from prompts.prompt_config import PromptName
    
    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.MUSIC_AGENT_TOOL_EXECUTION.value,
        local_template_name="music/music_agent_tool_execution",
        schema=None,
        include_raw=False
    )
    
    # 准备模板数据
    has_images = user_input_data.images and len(user_input_data.images) > 0
    template_data = {
        "user_input": user_input_data.user_input,
        "has_images": has_images,
        "images_count": len(user_input_data.images) if has_images else 0,
        "images": [{"index": i+1, "url": img.url} for i, img in enumerate(user_input_data.images)] if has_images else []
    }
    
    # 格式化消息
    # 使用 invoke 格式化消息（LangSmith 可追踪）
    messages = (await prompt_template.ainvoke(template_data)).messages
    
    # 添加语言提示（System + Human 都强化）
    apply_language_suffix_to_system_message_in_messages(messages, detected_language)
    
    # 如果有图片，使用 attach_images_to_messages 添加
    if has_images:
        image_urls = [image.url for image in user_input_data.images]
        messages = attach_images_to_messages(messages, image_urls)
    
    return messages


async def generate_music_with_agent(user_input_data: UserInput, messages: List[BaseMessage] = None, send_event_func=None, conversation_id: int = None, run_id: str = None, thread_id: str = None, detected_language: Optional[str] = None) -> Dict[str, Any]:
    """使用React Agent生成音乐"""
    try:
        logger.info(f"🎵 开始音乐生成")
        
        # 获取工具（get_suno_tools 返回 List[ToolInfo]，create_agent 需要 List[BaseTool]）
        music_tools_info = get_suno_tools()
        music_tools = [info.tool for info in music_tools_info]
        
        prompt_messages = await build_prompt_for_music_generation(user_input_data, detected_language)

        if messages:
            all_messages = messages + prompt_messages
        else:
            all_messages = prompt_messages

        from langchain.agents import create_agent
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableConfig
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName
        from prompts.prompt_loader import create_llm_from_model_config
        from ..utils.llm_resilience import (
            build_resilience_bundle_from_prompt_entry,
            execute_with_resilience,
        )

        _music_entry = PROMPTS_CONFIG[PromptName.MUSIC_AGENT_TOOL_EXECUTION]
        _ctx, _routes, _route_mcs = build_resilience_bundle_from_prompt_entry(_music_entry)
        inputs = {"messages": all_messages}
        config = RunnableConfig(recursion_limit=11)
        _orig_mc = len(messages) if messages else 0

        async def _invoke_music_stream(
            route: Tuple[str, Any], mc: Mapping[str, Any]
        ) -> Dict[str, Any]:
            from ..utils.prompt_utils import llm_chunk_content_to_text

            model_id, _s = route
            llm = create_llm_from_model_config(dict(mc))
            music_react_agent = await asyncio.to_thread(
                create_agent,
                llm,
                tools=music_tools,
            )
            full_content: List[str] = []
            agent_messages: List[Any] = []
            async for event in music_react_agent.astream_events(inputs, config=config, version="v2"):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    # gpt-5.x / Responses API may stream list[dict] blocks;
                    # joining raw chunk.content raises TypeError (list in join).
                    text = llm_chunk_content_to_text(getattr(chunk, "content", None))
                    if text:
                        full_content.append(text)
                        if send_event_func and conversation_id:
                            await send_event_func(
                                event_type=MessageType.STREAMING_CHUNK,
                                conversation_id=conversation_id,
                                message=text,
                                extra_data={
                                    "target_event": MessageType.MUSIC_AGENT_GENERATED.value,
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
                    content = llm_chunk_content_to_text(getattr(last_msg, "content", None))
            new_messages = (
                agent_messages[_orig_mc:]
                if len(agent_messages) > _orig_mc
                else agent_messages
            )
            return {"messages": new_messages, "output": content}

        _stream_out = await execute_with_resilience(
            _invoke_music_stream,
            routes=_routes,
            route_model_configs=_route_mcs,
            context=_ctx,
            log_context={"phase": "music_agent_stream"},
        )
        new_messages = _stream_out["messages"]
        content = _stream_out["output"]

        if send_event_func and conversation_id:
            # 发送音乐生成完成事件，使用content而不是结构化响应
            await send_event_func(
                event_type=MessageType.MUSIC_AGENT_GENERATED,
                conversation_id=conversation_id,
                message="音乐生成完成",
                extra_data={
                    "music_content": content,
                    "run_id": run_id,
                    "thread_id": thread_id
                }
            )
        
        logger.info(f"✅ 音乐生成完成")
        return {
            "messages": new_messages,
            "output": content
        }
        
    except Exception as e:
        logger.error(f"❌ 音乐生成失败: {str(e)}")
        raise
