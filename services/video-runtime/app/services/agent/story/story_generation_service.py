"""
故事生成服务 - 流式输出版本
"""
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage

from ....models.video_state import UserInput
from ....services.agent.base_agent import MessageType
from prompts.prompt_config import PROMPTS_CONFIG, PromptName
from prompts.prompt_loader import create_llm_from_model_config, load_prompt_with_fallback_async
from ..utils.llm_resilience import (
    build_resilience_bundle_from_prompt_entry,
    execute_with_resilience,
)

logger = logging.getLogger(__name__)


def _stream_chunk_to_text(content: Any) -> str:
    """Return only user-visible text from LangChain/Responses chunks.

    Responses models interleave opaque reasoning blocks with text blocks.
    Serializing unknown blocks leaks their ids and encrypted payload into the
    story artifact, so use the shared strict extractor instead.
    """
    from ..utils.prompt_utils import llm_chunk_content_to_text

    return llm_chunk_content_to_text(content)


async def build_prompt_for_story_generation(
    user_input_data: UserInput, detected_language: Optional[str] = None
) -> List[BaseMessage]:
    """构建故事直接创作的 prompt messages（LLM 与换路由由 generate_story_with_agent 经 llm_resilience 处理）。"""
    from ..utils.prompt_utils import apply_language_suffix_to_system_message_in_messages, attach_images_to_messages

    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.STORY_DIRECT_GENERATION.value,
        local_template_name="story/story_direct_generation",
        schema=None,
        include_raw=False,
    )
    
    # 准备模板数据
    has_images = user_input_data.images and len(user_input_data.images) > 0
    template_data = {
        "user_input": user_input_data.user_input,
        "has_images": has_images,
        "image_count": len(user_input_data.images) if has_images else 0
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


async def generate_story_with_agent(
    user_input_data: UserInput, 
    messages: List[BaseMessage] = None, 
    send_event_func=None, 
    conversation_id: int = None, 
    run_id: str = None, 
    thread_id: str = None, 
    detected_language: Optional[str] = None
) -> Dict[str, Any]:
    """流式生成故事
    
    Args:
        user_input_data: 用户输入数据
        messages: 历史消息
        send_event_func: 发送事件的函数
        conversation_id: 会话ID
        run_id: 运行ID
        thread_id: 线程ID
        detected_language: 检测到的语言
        
    Returns:
        Dict[str, Any]: 包含output和messages的字典
    """
    try:
        logger.info(f"📚 开始流式故事生成")
        
        prompt_messages = await build_prompt_for_story_generation(user_input_data, detected_language)

        if messages:
            all_messages = messages + prompt_messages
            logger.info(
                f"🔍 LLM输入 - 流式故事生成: {len(messages)} 条历史消息 + {len(prompt_messages)} 条新消息"
            )
        else:
            all_messages = prompt_messages
            logger.info(f"🔍 LLM输入 - 流式故事生成: {len(all_messages)} 条消息")

        logger.info("🤖 开始调用 LLM 流式生成故事（llm_resilience 模型链）...")

        _entry = PROMPTS_CONFIG[PromptName.STORY_DIRECT_GENERATION]
        _ctx, _routes, _route_mcs = build_resilience_bundle_from_prompt_entry(_entry)

        async def _invoke_story_stream(
            route: Tuple[str, Any], mc: Mapping[str, Any]
        ) -> Dict[str, Any]:
            model_id, _s = route
            llm = create_llm_from_model_config(dict(mc))
            full_content: List[str] = []
            async for chunk in llm.astream(all_messages):
                raw_c = chunk.content if hasattr(chunk, "content") else chunk
                content = _stream_chunk_to_text(raw_c)
                if content:
                    full_content.append(content)
                    if send_event_func and conversation_id:
                        await send_event_func(
                            event_type=MessageType.STREAMING_CHUNK,
                            conversation_id=conversation_id,
                            message=content,
                            extra_data={
                                "target_event": MessageType.STORY_AGENT_GENERATED.value,
                                "content_type": "text",
                            },
                        )
            final_content = "".join(full_content)
            ai_message = AIMessage(content=final_content)
            new_messages = prompt_messages + [ai_message]
            if send_event_func and conversation_id:
                await send_event_func(
                    event_type=MessageType.STORY_AGENT_GENERATED,
                    conversation_id=conversation_id,
                    message="故事生成完成",
                    extra_data={
                        "story_content": final_content,
                        "word_count": len(final_content),
                        "run_id": run_id,
                        "thread_id": thread_id,
                    },
                    hidden=False,
                    save_to_db=True,
                )
            return {"messages": new_messages, "output": final_content}

        _stream_out = await execute_with_resilience(
            _invoke_story_stream,
            routes=_routes,
            route_model_configs=_route_mcs,
            context=_ctx,
            log_context={
                "phase": "story_generation_stream",
                "conversation_id": conversation_id,
                "run_id": run_id,
            },
        )
        final_content = _stream_out["output"]
        logger.info(f"✅ 流式故事生成完成，总长度: {len(final_content)}字")

        return {
            "messages": _stream_out["messages"],
            "output": final_content,
        }
        
    except Exception as e:
        logger.error(f"❌ 故事生成失败: {str(e)}")
        raise
