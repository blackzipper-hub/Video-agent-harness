"""
用户输入分析服务 - 分析用户上传的视频内容
"""

import logging
import tempfile
import subprocess
import os
import base64
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.models.video_state import VideoAnalysisResult, UserInput, AudioTranscription
from app.schemas.video_llm import GeminiVideoAnalysisResult
from app.tools.transcribe.gemini import transcribe_audio_with_gemini
from app.services.agent.base_agent import MessageType
from app.crud.video.video_audio import create_video_audio_transcription, create_video_audio_segment
from app.utils.s3_utils import s3_utils
from app.utils.file_utils import SUPPORTED_VIDEO_EXTENSIONS, SUPPORTED_VIDEO_MIMETYPES
from app.exceptions import BusinessException, BusinessExceptionCode
from app.utils import media_service_client as msc
from prompts.prompt_config import PromptName

logger = logging.getLogger(__name__)



async def analyze_video_with_gemini(
    video_url: str,
    user_input: Optional[str] = None,
    log_context: Optional[Dict[str, Any]] = None,
) -> Tuple[GeminiVideoAnalysisResult, List[BaseMessage]]:
    """
    使用Gemini分析视频内容
    
    Args:
        video_url: 视频文件URL
        user_input: 用户提供的额外描述
        
    Returns:
        Tuple[GeminiVideoAnalysisResult, List[BaseMessage]]: 
            - 视频分析结果
            - LLM 的新 input + output messages（只返回新增部分）
    """
    try:
        logger.info(f"🎬 开始使用Gemini分析视频: {video_url}")
        
        # skill-based multimodal prompt
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.orchestration.skills.prompt_context import (
            facts_human_message,
            skill_system_message,
        )
        from app.services.agent.utils.multimodal_post_process import process as multimodal_process

        system = skill_system_message(
            "gemini-video-analysis-director",
            lead="Follow gemini-video-analysis-director.",
        )
        facts = {
            "user_input": user_input or "",
            "has_video": True,
        }
        human = facts_human_message(facts) + "\n__VID_video_content__"
        messages = [SystemMessage(content=system), HumanMessage(content=human)]
        messages = await multimodal_process(messages, {"video_content": video_url})
        
        # 强制语言：按当前请求语言输出分析结果
        from ....services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from ....utils.i18n import get_current_language
        _lang = get_current_language() or "en"
        apply_language_suffix_to_system_message_in_messages(messages, _lang)
        
        logger.info("🤖 正在调用 Gemini Agent 进行视频分析（ainvoke_structured_resilient CREATE_AGENT）...")
        from ....services.agent.utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName

        result = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_GEMINI_ANALYSIS],
            kind=StructuredResilienceKind.CREATE_AGENT,
            agent_inputs={"messages": messages},
            log_context=log_context,
        )
        
        # 从 agent 结果中提取 structured_response
        if "structured_response" not in result:
            logger.error(f"❌ Agent 结果中缺少 structured_response: {result.keys()}")
            raise BusinessException(
                BusinessExceptionCode.OPENAI_IMAGE_ANALYSIS_ERROR,
                "视频分析失败：Agent 返回结果格式错误"
            )
        
        parsed = result["structured_response"]
        
        # ✅ 提取完整的 agent messages
        # 返回本次LLM调用的 input messages + output messages
        agent_full_messages = result.get("messages", [])
        input_message_count = len(messages)
        output_messages = agent_full_messages[input_message_count:] if len(agent_full_messages) > input_message_count else []
        
        # 返回本次调用的 input messages + output messages
        all_messages = messages + output_messages
        
        logger.info(f"✅ Gemini视频分析完成，识别到 {len(parsed.characters)} 个角色，{len(parsed.storyboard)} 个场景片段")
        logger.info(f"📨 视频分析完成，返回 {len(all_messages)} 条消息（{len(messages)} 条input + {len(output_messages)} 条output）")
        if output_messages:
            _last = output_messages[-1]
            _c = getattr(_last, "content", None)
            _ctype = type(_c).__name__
            _clen = len(_c) if isinstance(_c, str) else (len(_c) if isinstance(_c, list) else 0)
            logger.info(
                "user_input_analysis Gemini: 最后一条输出消息 type=%s content_type=%s content_len=%s",
                type(_last).__name__,
                _ctype,
                _clen,
            )
        
        return parsed, all_messages
        
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"❌ Gemini视频分析失败: {e}", exc_info=True)
        raise BusinessException(BusinessExceptionCode.OPENAI_IMAGE_ANALYSIS_ERROR)
    
async def user_input_analysis_node(
    state: Dict[str, Any], 
    runtime: Any,
    send_event_func: Optional[callable] = None
) -> Dict[str, Any]:
    """
    用户输入分析节点 - 分析上传的视频内容，提取音频，统一处理
    
    Args:
        state: 当前状态
        runtime: 运行时上下文
        send_event_func: 事件发送函数（已废弃，不再使用）
        
    Returns:
        Dict[str, Any]: 更新的状态，包含 user_input_data 和 messages
    """
    try:
        logger.info("🔍 开始用户输入分析节点...")
        _rid = state.get("run_id")
        _tid = state.get("thread_id")
        _cid = state.get("conversation_id")
        logger.info(
            "user_input_analysis_node: run_id=%s thread_id=%s conversation_id=%s",
            _rid,
            _tid,
            _cid,
        )
        
        # 检查是否有视频或音频文件需要处理
        user_input_data = state["user_input_data"]
        _nv = len(user_input_data.video_files or [])
        _na = len(user_input_data.audio_files or [])
        logger.info(
            "user_input_analysis_node: video_files=%s audio_files=%s user_input_len=%s",
            _nv,
            _na,
            len(user_input_data.user_input or ""),
        )
        if not (user_input_data.video_files or user_input_data.audio_files):
            logger.info("📝 没有视频或音频文件，跳过用户输入分析 → return keys=%s", ["messages"])
            return {"messages": []}
        
        # 处理用户输入
        enhanced_user_input = user_input_data.user_input  # 默认使用原始输入
        modified_user_input = user_input_data.model_copy()  # 复制一份用于修改
        all_messages = []  # 收集所有 LLM 消息
        
        # 处理视频文件（只处理第一个）
        if user_input_data.video_files:
            logger.info(f"📹 发现 {len(user_input_data.video_files)} 个视频文件，处理第一个...")
            
            video_file = user_input_data.video_files[0]  # 只处理第一个视频
            _vurl = video_file.url or ""
            logger.info(
                "user_input_analysis_node: 即将 Gemini 分析 video filename=%s url_prefix=%s",
                getattr(video_file, "filename", None),
                _vurl[:120] + ("..." if len(_vurl) > 120 else ""),
            )
            
            # 1. 分析视频内容
            video_analysis, video_messages = await analyze_video_with_gemini(
                video_url=video_file.url,
                user_input=user_input_data.user_input,
                log_context={
                    "run_id": state.get("run_id"),
                    "thread_id": state.get("thread_id"),
                    "conversation_id": state.get("conversation_id"),
                    "caller": "user_input_analysis_gemini",
                },
            )
            all_messages.extend(video_messages)
            
            # 将视频分析结果转换为文本
            video_text = _convert_video_analysis_to_text(video_analysis, video_file.filename)
            enhanced_user_input = f"{user_input_data.user_input}\n\n## 参考视频风格分析\n\n{video_text}"
            
            logger.info(
                "✅ 视频分析完成: %s enhanced_user_input_len=%s (was %s)",
                video_file.filename,
                len(enhanced_user_input),
                len(user_input_data.user_input or ""),
            )
            
            # 2. 从视频提取音频，添加到audio_files中
            logger.info("user_input_analysis_node: 开始从视频提取音频...")
            extracted_audio_url = await _extract_audio_from_video(video_file.url)
            if extracted_audio_url:
                logger.info(f"✅ 音频提取完成: {video_file.filename}")
                
                # 创建AudioFileUserInput并添加到audio_files
                from app.models.video_state import AudioFileUserInput
                extracted_audio = AudioFileUserInput(
                    url=extracted_audio_url,
                    filename=f"{video_file.filename}_audio"
                )
                
                # 将提取的音频添加到audio_files列表中
                modified_user_input.audio_files = list(modified_user_input.audio_files) + [extracted_audio]
                logger.info(f"🎵 已将提取的音频添加到audio_files: {extracted_audio.filename}")
            else:
                logger.warning(f"⚠️ 无法从视频 {video_file.filename} 提取音频")
        elif _na > 0:
            logger.info(
                "user_input_analysis_node: 仅有 audio_files（无 video），跳过 Gemini 与抽音轨，交给 music_generation"
            )
        
        # 注意：用户直接上传的 audio_files 不需要在这里处理
        # 它们会在 music_generation_node 中统一处理（转录）
        # 这里只需要检查是否存在，不需要额外操作
        
        # 更新user_input_data
        if enhanced_user_input != user_input_data.user_input:
            modified_user_input.user_input = enhanced_user_input
        
        logger.info(
            "✅ 用户输入分析完成 → return user_input_data=%s messages_count=%s audio_files_after=%s",
            True,
            len(all_messages),
            len(modified_user_input.audio_files or []),
        )
        # 返回更新的状态和消息
        return {
            "user_input_data": modified_user_input,
            "messages": all_messages
        }
        
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"❌ 用户输入分析失败: {e}", exc_info=True)
        raise BusinessException(BusinessExceptionCode.AGENT_INPUT_ANALYSIS_ERROR)
    
def _convert_video_analysis_to_text(video_analysis: GeminiVideoAnalysisResult, filename: str) -> str:
        """
        将视频分析结果转换为文本描述
        
        Args:
            video_analysis: 视频分析结果
            filename: 视频文件名
            
        Returns:
            str: 文本描述
        """
        text_parts = [f"### 视频文件: {filename}"]
        
        # 整体风格
        text_parts.append(f"**整体风格**: {video_analysis.overall_style}")
        text_parts.append(f"**主题**: {video_analysis.theme}")
        text_parts.append(f"**情绪调性**: {video_analysis.mood}")
        text_parts.append(f"**色彩风格**: {video_analysis.color_palette}")
        text_parts.append(f"**叙事结构**: {video_analysis.narrative_structure}")
        
        # 角色信息
        if video_analysis.characters:
            text_parts.append("\n**角色设定**:")
            for char in video_analysis.characters:
                text_parts.append(f"- {char.name}: {char.description} ({char.role})")
        
        # 关键视觉元素
        if video_analysis.visual_elements:
            text_parts.append(f"\n**关键视觉元素**: {', '.join(video_analysis.visual_elements)}")
        
        # 制作技术
        if video_analysis.production_techniques:
            text_parts.append(f"**制作技术**: {', '.join(video_analysis.production_techniques)}")
        
        # 关键场景
        if video_analysis.storyboard:
            text_parts.append("\n**关键场景参考**:")
            for i, scene in enumerate(video_analysis.storyboard):
                text_parts.append(f"{i+1}. {scene.time_start}-{scene.time_end}: {scene.action_description}")
                if scene.visual_style:
                    text_parts.append(f"   风格: {scene.visual_style}")
                if scene.emotion:
                    text_parts.append(f"   情感: {scene.emotion}")
        
        return "\n".join(text_parts)
    
async def _extract_audio_from_video(
    video_url: str,
    output_format: str = "mp3",
    audio_quality: str = "192k"
) -> Optional[str]:
    """
    从视频文件中提取音频并上传到S3
    
    Args:
        video_url: 视频文件URL
        output_format: 输出音频格式 (mp3, wav, aac等)
        audio_quality: 音频质量 (128k, 192k, 320k等)
        
    Returns:
        Optional[str]: 提取的音频文件S3 URL，失败返回None
    """
    result = await msc.audio_extract(
        video_url=video_url,
        run_id=str(uuid.uuid4()),
        fmt=output_format,
    )
    logger.info("user_input_analysis audio_extract: response keys=%s", list(result.keys()))
    audio_url = result.get("result_url")
    if audio_url:
        logger.info("✅ Media service audio extraction succeeded: %s", audio_url)
    else:
        logger.info("user_input_analysis: audio_extract 无 result_url（视频无音轨或未产出），不追加 audio_files")
    return audio_url
