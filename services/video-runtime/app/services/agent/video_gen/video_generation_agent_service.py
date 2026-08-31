"""
视频直生服务（video_gen）

对齐 image agent 的范式（React Agent + 流式 + 统一 LLM 配置），但：
- SD2 单模型直接出片：纯文本走 T2V、有上传图走 I2V（见 video_gen_tools）。
- 由 LLM 决定调用工具的次数（"生成 3 个" → 调 3 次），service 端从每个
  ToolMessage.artifact（VideoGenerationResult）收集成结构化 `videos[]`。
- 完成事件 `video_agent_generated` 同时给：对话 message + 结构化 videos[]（前端画廊消费）。
"""
import asyncio
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from langchain_core.messages import BaseMessage, AIMessage, ToolMessage

from ....models.image_result import VideoGenerationResult
from ....models.user_options import UserOption
from ....models.tool_enums import AspectRatio, Resolution, DefaultValues
from ....services.agent.base_agent import MessageType
from ....models.video_state import UserInput
from ....tools.context_schemas import VideoGenerationContext
from .video_gen_tools import get_video_gen_tools

logger = logging.getLogger(__name__)


def _stream_chunk_to_text(raw_chunk: Any) -> str:
    """Normalize text and multimodal LangChain chunks into plain text."""
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


def _collect_urls(items: Any) -> List[str]:
    """从 UserInput 的文件列表（images/video_files/audio_files）提取 url。"""
    urls: List[str] = []
    if not items:
        return urls
    for it in items:
        url = getattr(it, "url", None) if not isinstance(it, str) else it
        if url:
            urls.append(url)
    return urls


async def _resolve_outbound_urls(urls: List[str]) -> List[str]:
    """本地 localhost 素材先做 media egress，再交给外部模型 / LLM 多模态。"""
    if not urls:
        return []
    from app.utils.media_egress import resolve_outbound_media_url

    resolved: List[str] = []
    for url in urls:
        if not isinstance(url, str) or not url.strip():
            continue
        resolved.append(await resolve_outbound_media_url(url.strip()))
    return resolved


async def _build_prompt_messages(
    user_input_data: UserInput,
    reference_images: List[str],
    reference_videos: List[str],
    reference_audios: List[str],
    detected_language: Optional[str],
) -> List[BaseMessage]:
    """构建 video_gen 的 prompt messages（模板 + 语言后缀 + 参考图附加）。"""
    from ..utils.prompt_utils import apply_language_suffix_to_system_message_in_messages, attach_images_to_messages
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from prompts.prompt_config import PromptName

    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_AGENT_TOOL_EXECUTION.value,
        local_template_name="video_gen/video_agent_tool_execution",
        schema=None,
        include_raw=False,
    )

    references: List[Dict[str, Any]] = []
    for i, u in enumerate(reference_images):
        references.append({"kind": "图片", "index": i + 1, "url": u})
    for i, u in enumerate(reference_videos):
        references.append({"kind": "视频", "index": i + 1, "url": u})
    for i, u in enumerate(reference_audios):
        references.append({"kind": "音频", "index": i + 1, "url": u})

    template_data = {
        "user_input": user_input_data.user_input,
        "has_references": len(references) > 0,
        "references": references,
        "wants_multiple": False,
    }

    formatted_messages = (await prompt_template.ainvoke(template_data)).messages
    apply_language_suffix_to_system_message_in_messages(formatted_messages, detected_language)

    if reference_images:
        formatted_messages = attach_images_to_messages(formatted_messages, reference_images)

    return formatted_messages


def _result_to_video_item(index: int, result: VideoGenerationResult) -> Dict[str, Any]:
    """VideoGenerationResult → 结构化 video item（前端 GeneratedVideoItem）。

    仅下发前端播放所需的最小字段（video_url + 封面），不暴露 model / prompt /
    分辨率 / 比例 / 时长等内部信息给用户。
    """
    return {
        "index": index,
        "video_url": result.video_url,
        "cover_image_url": getattr(result, "keyframe_url", None),
    }


def _collect_videos_from_messages(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """从 ToolMessage.artifact（VideoGenerationResult）收集成功生成的视频。"""
    videos: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        artifact = getattr(msg, "artifact", None)
        if isinstance(artifact, VideoGenerationResult) and artifact.success and artifact.video_url:
            videos.append(_result_to_video_item(len(videos), artifact))
    return videos


async def generate_video_with_agent(
    user_input_data: UserInput,
    messages: List[BaseMessage] = None,
    send_event_func=None,
    conversation_id: int = None,
    run_id: str = None,
    thread_id: str = None,
    detected_language: Optional[str] = None,
) -> Dict[str, Any]:
    """使用 React Agent 直接生成（多个）视频。"""
    try:
        logger.info("🎬 [video_gen] 开始视频直生")
        user_option = user_input_data.user_option if user_input_data.user_option else UserOption.default()

        reference_images = await _resolve_outbound_urls(
            _collect_urls(getattr(user_input_data, "images", None))
        )
        reference_videos = await _resolve_outbound_urls(
            _collect_urls(getattr(user_input_data, "video_files", None))
        )
        reference_audios = await _resolve_outbound_urls(
            _collect_urls(getattr(user_input_data, "audio_files", None))
        )
        has_input_image = len(reference_images) > 0

        video_tools = get_video_gen_tools(user_option, has_input_image=has_input_image)
        logger.info(f"🔧 [video_gen] 注入工具数={len(video_tools)}, has_input_image={has_input_image}")

        prompt_messages = await _build_prompt_messages(
            user_input_data, reference_images, reference_videos, reference_audios, detected_language
        )
        all_messages = (messages + prompt_messages) if messages else prompt_messages

        from langchain.agents import create_agent
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName
        from prompts.prompt_loader import create_llm_from_model_config
        from ..utils.llm_resilience import (
            build_resilience_bundle_from_prompt_entry,
            execute_with_resilience,
        )

        context = VideoGenerationContext(
            aspect_ratio=(
                AspectRatio(user_option.aspect_ratio.value)
                if user_option and user_option.aspect_ratio
                else DefaultValues.VIDEO_ASPECT_RATIO
            ),
            resolution=(
                Resolution(user_option.resolution.value)
                if user_option and user_option.resolution
                else DefaultValues.VIDEO_RESOLUTION
            ),
            duration=(int(user_option.duration) if user_option and getattr(user_option, "duration", None) else None),
            start_image_url=reference_images[0] if has_input_image else None,
            reference_images=reference_images or None,
            reference_videos=reference_videos or None,
            reference_audios=reference_audios or None,
            language=detected_language,
        )

        _entry = PROMPTS_CONFIG[PromptName.VIDEO_AGENT_TOOL_EXECUTION]
        _ctx, _routes, _route_mcs = build_resilience_bundle_from_prompt_entry(_entry)
        inputs = {"messages": all_messages}
        _orig_mc = len(messages) if messages else 0

        async def _invoke_video_stream(route: Tuple[str, Any], mc: Mapping[str, Any]) -> Dict[str, Any]:
            model_id, _s = route
            llm = create_llm_from_model_config(dict(mc))
            video_react_agent = await asyncio.to_thread(
                create_agent,
                llm,
                tools=video_tools,
                context_schema=VideoGenerationContext,
            )
            full_content: List[str] = []
            agent_messages: List[Any] = []
            done_count = 0
            async for event in video_react_agent.astream_events(inputs, context=context, version="v2"):
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
                                    "target_event": MessageType.VIDEO_AGENT_GENERATED.value,
                                    "content_type": "text",
                                },
                            )
                elif kind == "on_tool_end":
                    done_count += 1
                    if send_event_func and conversation_id:
                        await send_event_func(
                            event_type=MessageType.VIDEO_GEN_PROGRESS,
                            conversation_id=conversation_id,
                            message=f"已完成 {done_count} 个视频",
                            extra_data={"index": done_count - 1, "completed": done_count},
                        )
                elif kind == "on_chain_end" and event["name"] == "LangGraph":
                    result = event["data"]["output"]
                    agent_messages = result.get("messages", [])
            content = "".join(full_content) if full_content else ""
            if not content and agent_messages:
                last_msg = agent_messages[-1]
                if isinstance(last_msg, AIMessage):
                    content = _stream_chunk_to_text(getattr(last_msg, "content", None))
            new_messages = agent_messages[_orig_mc:] if len(agent_messages) > _orig_mc else agent_messages
            return {"messages": new_messages, "output": content}

        _stream_out = await execute_with_resilience(
            _invoke_video_stream,
            routes=_routes,
            route_model_configs=_route_mcs,
            context=_ctx,
            log_context={"phase": "video_gen_agent_stream"},
        )
        new_messages = _stream_out["messages"]
        content = _stream_out["output"]

        videos = _collect_videos_from_messages(new_messages)
        count = len(videos)
        logger.info(f"✅ [video_gen] 生成完成，视频数={count}")

        # 0 视频视为生成失败：抛异常让 worker 将 task 标记为 FAILED（astream 会据此发 ERROR
        # 事件，前端据此显示失败、停掉进度条），避免 task=completed 但无产物的误导语义。
        if count == 0:
            raise RuntimeError("视频生成失败：未能生成任何视频")

        if send_event_func and conversation_id:
            await send_event_func(
                event_type=MessageType.VIDEO_AGENT_GENERATED,
                conversation_id=conversation_id,
                message=content or f"已为你生成 {count} 个视频",
                extra_data={
                    "video_content": content,
                    "videos": videos,
                    "count": count,
                    "run_id": run_id,
                    "thread_id": thread_id,
                },
            )

        return {"messages": new_messages, "output": content, "videos": videos}

    except Exception as e:
        logger.error(f"❌ [video_gen] 视频直生失败: {str(e)}")
        raise
