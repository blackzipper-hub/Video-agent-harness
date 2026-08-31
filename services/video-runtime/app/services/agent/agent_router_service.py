"""
LangGraph Agent路由器服务 - 使用AsyncPostgresSaver和流式输出
"""
import logging
import json
import asyncio
import uuid
from typing import Any, AsyncGenerator, Dict, List, Literal, Mapping, Optional, Tuple, Union
from datetime import datetime
from enum import Enum
from typing_extensions import Annotated, TypedDict
from dataclasses import dataclass

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from prompts.prompt_config import PromptName
from langgraph.config import RunnableConfig, get_stream_writer
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver, AsyncConnectionPool
from pydantic import BaseModel, Field
from sqlmodel import Session
from langgraph.graph.state import Command, Send
from langgraph.types import interrupt

from ...exceptions import BusinessException, BusinessExceptionCode
from ...models.database import get_db
from ...config import settings
from ...utils.thread_id_utils import generate_new_thread_id
from ...utils.asyncpg_utils import utc_isoformat
from ...utils.i18n import get_i18n_message_async
from ...utils.error_classification import user_facing_reason
from .base_agent import BaseAgent, MessageType, MessageRole
from ...models.task_status import TaskStatus
from .video_agent_service import get_video_agent
from ...models.user_options import UserOption
from ...models.video_state import VideoAgentState, UserInput, ImageUserInput, AudioFileUserInput, VideoFileUserInput
from ...models.conversation import ConversationDB, ConversationRunDB
from ...schemas.response import ClarifyResponse
from contextvars import ContextVar
from ...crud.conversation import async_get_runs_by_thread_id_and_status
from .utils.cancellation import set_current_run_id


_current_run_id_cv: ContextVar[Optional[str]] = ContextVar("agent_router_current_run_id", default=None)


def add_messages_with_run_id(left, right):
    """在标准 add_messages 基础上，给新加入的消息打上当前 run_id，便于按 run 过滤已取消消息。"""
    rid = _current_run_id_cv.get()
    if rid:
        items = right if isinstance(right, list) else [right]
        for _m in items:
            if isinstance(_m, BaseMessage):
                if _m.additional_kwargs is None:
                    _m.additional_kwargs = {}
                _m.additional_kwargs.setdefault("run_id", rid)
    return add_messages(left, right)

logger = logging.getLogger(__name__)


def build_recommended_smart_clip_decision(sc: Dict[str, Any]) -> Dict[str, Any]:
    """从 interrupt 上的 smart_clip 推荐区构造默认裁切决策（accepted=true）。"""
    rec = sc.get("recommended") or {}
    return {
        "accepted": True,
        "start_sec": float(rec.get("start_sec", 0.0)),
        "end_sec": float(rec.get("end_sec", 0.0)),
        "fade_in_sec": float(rec.get("fade_in_sec", 0.0)),
        "fade_out_sec": float(rec.get("fade_out_sec", 0.0)),
        "music_generation_uuid": sc.get("music_generation_uuid"),
    }


async def inject_default_smart_clip_into_resume_dict(rd_dict: Dict[str, Any]) -> Dict[str, Any]:
    """resume 未带 smart_clip 时：after_music 且 interrupt 内 smart_clip=ready → 默认 AI 推荐裁切。"""
    if not isinstance(rd_dict, dict):
        return rd_dict or {}
    if isinstance(rd_dict.get("smart_clip"), dict):
        return rd_dict

    interrupt_msgid = rd_dict.get("interrupt_msgid")
    if interrupt_msgid is None:
        return rd_dict
    try:
        msg_id_int = int(interrupt_msgid)
    except (TypeError, ValueError):
        return rd_dict

    from ...crud.conversation import async_get_message_by_id

    msg = await async_get_message_by_id(msg_id_int)
    if not msg:
        return rd_dict
    event_data = msg.event_data or {}
    interrupt_data = event_data.get("interrupt_data") or {}
    if interrupt_data.get("step") != "after_music":
        return rd_dict
    sc = interrupt_data.get("smart_clip")
    if not isinstance(sc, dict) or sc.get("status") != "ready":
        return rd_dict
    if not isinstance(sc.get("recommended"), dict):
        return rd_dict

    out = dict(rd_dict)
    out["smart_clip"] = build_recommended_smart_clip_decision(sc)
    logger.info(
        "smart_clip resume: 无显式 smart_clip，after_music 默认采用 AI 推荐 [%.2f-%.2f]s mg=%s",
        float((sc.get("recommended") or {}).get("start_sec", 0.0)),
        float((sc.get("recommended") or {}).get("end_sec", 0.0)),
        sc.get("music_generation_uuid"),
    )
    return out


async def _apply_smart_clip_from_resume_data(
    *,
    thread_id: str,
    user_id: str,
    resume_data: Any,
    run_id: Optional[str] = None,
    detected_language: Optional[str] = None,
) -> Optional[str]:
    """处理 resume_data 中的 smart_clip 决策（在真正 ``Command(resume=...)`` 前调用）。

    支持两种 resume_data 形态：
      - JSON 字符串（前端 → API → SQS → worker → astream 链路里，prepare_resume_task 已 ``json.dumps``）
      - 直接 dict / 任意对象（兼容内部直调 / 测试）

    流程：
      1. 解析 ``resume_data.smart_clip``；非 dict 直接返回 None。
      2. 通过 ``music_generation_uuid``（前端从 interrupt_data.smart_clip 透传）找到目标 music_generation。
         若前端没传，回退用 thread_id 找最新一条 ``status == "ready"`` 的记录。
      3. ``accepted == false`` → 仅把 ``status`` 改为 ``confirmed``；不动版本。
      4. ``accepted == true``：
         - 调 ``msc.audio_trim_with_fade`` trim + fade
         - 落 v2 ``music_generation_version``，带 ``original_audio_url`` / ``segment_start_time`` / ``segment_end_time`` / ``additional_data.from_smart_clip``
         - ``update_music_generation_current_version(uuid, next_version_index)`` （0-based，与 DB 列 ``current_version_index`` 含义一致）
         - merge ``additional_data.smart_clip`` 写入 ``user_confirmed`` + ``status=confirmed``

    返回新版本 uuid（accepted），或 None（拒绝 / 未触发 / 失败 fallback）。
    """
    sc: Optional[Dict[str, Any]] = None
    rd_dict: Optional[Dict[str, Any]] = None
    try:
        if isinstance(resume_data, str) and resume_data.strip():
            try:
                rd_dict = json.loads(resume_data)
            except Exception:
                rd_dict = None
        elif isinstance(resume_data, dict):
            rd_dict = resume_data
    except Exception:
        rd_dict = None
    if isinstance(rd_dict, dict):
        rd_dict = await inject_default_smart_clip_into_resume_dict(rd_dict)
        candidate_sc = rd_dict.get("smart_clip")
        if isinstance(candidate_sc, dict):
            sc = candidate_sc
    if not sc:
        return None

    try:
        from ...crud.video.video_audio import (
            get_music_generation_by_uuid,
            get_music_generations_by_thread_id,
            get_music_generation_versions,
            create_music_generation_version,
            update_music_generation_additional_data,
            update_music_generation_current_version,
            update_music_generation_version_segments,
            create_music_from_audio_transcription_segments,
        )
        from ...models.image_result import MusicProvider
        from ...utils import media_service_client as msc

        # —— 1. 找目标 music_generation
        mg = None
        mg_uuid_from_payload = sc.get("music_generation_uuid")
        if mg_uuid_from_payload:
            mg = await get_music_generation_by_uuid(str(mg_uuid_from_payload))
        if mg is None:
            mgs = await get_music_generations_by_thread_id(thread_id)
            ready = [
                m for m in mgs
                if isinstance(getattr(m, "additional_data", None), dict)
                and (m.additional_data or {}).get("smart_clip", {}).get("status") == "ready"
            ]
            if not ready:
                logger.info("smart_clip resume: 无 status=ready 的 music_generation，跳过")
                return None
            mg = ready[-1]
        if mg is None:
            return None
        music_uuid = mg.uuid
        existing_ad: Dict[str, Any] = dict(getattr(mg, "additional_data", None) or {})
        existing_sc: Dict[str, Any] = dict(existing_ad.get("smart_clip") or {})

        # 幂等：已确认过（无论接受/拒绝）则不再 trim，避免后续 gate resume 误带 smart_clip 重复发「已应用裁切」
        if existing_sc.get("status") == "confirmed":
            logger.info(
                "smart_clip resume: 已 confirmed，跳过重复裁切 (mg=%s)",
                music_uuid,
            )
            return None

        # —— 2. 拒绝：不裁切，只标 confirmed
        if not sc.get("accepted"):
            existing_sc["user_confirmed"] = sc
            existing_sc["status"] = "confirmed"
            existing_ad["smart_clip"] = existing_sc
            await update_music_generation_additional_data(music_uuid, existing_ad)
            logger.info("smart_clip resume: 用户拒绝裁切，已 mark confirmed (mg=%s)", music_uuid)
            return None

        # —— 3. 接受：trim + 落 v2
        rec = existing_sc.get("recommended") or {}
        start = float(sc.get("start_sec", rec.get("start_sec", 0.0)))
        end = float(sc.get("end_sec", rec.get("end_sec", 0.0)))
        fi = float(sc.get("fade_in_sec", rec.get("fade_in_sec", 0.0)))
        fo = float(sc.get("fade_out_sec", rec.get("fade_out_sec", 0.0)))
        duration = max(0.0, end - start)
        if duration <= 0.5:
            logger.warning("smart_clip resume: 选区时长过短 %.3fs，跳过裁切", duration)
            existing_sc["user_confirmed"] = sc
            existing_sc["status"] = "failed"
            existing_sc["error"] = f"selection_duration_too_short:{duration:.3f}"
            existing_ad["smart_clip"] = existing_sc
            await update_music_generation_additional_data(music_uuid, existing_ad)
            return None

        original_audio_url = existing_sc.get("audio_url")
        if not original_audio_url:
            # 回退：用 v1 的 music_url
            versions = await get_music_generation_versions(music_uuid)
            if versions:
                original_audio_url = versions[0].music_url
        if not original_audio_url:
            logger.warning("smart_clip resume: 找不到 original_audio_url，跳过 (mg=%s)", music_uuid)
            return None

        try:
            trimmed = await msc.audio_trim_with_fade(
                audio_url=original_audio_url,
                start=start, duration=duration,
                fade_in_sec=fi, fade_out_sec=fo,
                run_id=mg.run_id,
            )
            trimmed_url = trimmed["result_url"]
        except Exception as e:
            logger.error("smart_clip resume: trim_with_fade 失败 (mg=%s): %s", music_uuid, e)
            existing_sc["user_confirmed"] = sc
            existing_sc["status"] = "failed"
            existing_sc["error"] = f"trim_failed:{e}"
            existing_ad["smart_clip"] = existing_sc
            await update_music_generation_additional_data(music_uuid, existing_ad)
            return None

        versions = await get_music_generation_versions(music_uuid)
        next_ver_number = (max((v.version_number for v in versions), default=0)) + 1
        prev_prompt = versions[-1].music_prompt if versions else ""
        prev_provider = versions[-1].provider if versions else MusicProvider.SUNO
        new_uuid = await create_music_generation_version(
            music_generation_id=music_uuid,
            version_number=next_ver_number,
            music_url=trimmed_url,
            provider=prev_provider or MusicProvider.SUNO,
            model="",
            prompt=prev_prompt or "",
            duration=duration,
            success=True,
            user_id=user_id,
            conversation_id=mg.conversation_id,
            thread_id=mg.thread_id,
            run_id=mg.run_id,
            additional_data={
                "trim_source_version_uuid": versions[-1].uuid if versions else None,
                "trim_start_sec": start,
                "trim_end_sec": end,
                "fade_in_sec": fi,
                "fade_out_sec": fo,
                "from_smart_clip": True,
            },
            is_instrumental=mg.is_instrumental,
            original_audio_url=original_audio_url,
        )
        await update_music_generation_version_segments(new_uuid, start, end)
        # current_version_index 是 0-based；落了 N 版后切到 N-1
        await update_music_generation_current_version(music_uuid, next_ver_number - 1)
        existing_sc["user_confirmed"] = sc
        existing_sc["status"] = "confirmed"
        existing_sc["new_version_uuid"] = new_uuid
        existing_ad["smart_clip"] = existing_sc
        await update_music_generation_additional_data(music_uuid, existing_ad)

        # —— 4. 同步切 audio_transcription / sections / segments
        # 否则 outline / scene / character 等下游节点会继续读到原始未截断的 transcription
        # （get_video_audio_transcription_by_thread_id 按 created_at DESC 取最新）。
        try:
            from ...crud.video.video_audio import (
                get_video_audio_transcription_by_thread_id,
                get_video_audio_segments_by_transcription_uuid,
                get_video_audio_sections_by_transcription_uuid,
                create_video_audio_transcription,
                create_video_audio_segment,
                create_video_audio_section,
            )
            old_tr = await get_video_audio_transcription_by_thread_id(mg.thread_id)
            if old_tr is None:
                logger.warning("smart_clip resume: 未找到原 transcription，下游仍可能用原始时长 (thread=%s)", mg.thread_id)
            else:
                old_segments = await get_video_audio_segments_by_transcription_uuid(old_tr.uuid)
                old_sections = await get_video_audio_sections_by_transcription_uuid(old_tr.uuid)
                new_duration = float(end - start)

                # 拼新 text：只保留落在 [start, end] 内的 segments 文本
                kept_seg_texts: List[str] = []
                for seg in old_segments or []:
                    seg_s = float(getattr(seg, "start", 0.0) or 0.0)
                    seg_e = float(getattr(seg, "end", 0.0) or 0.0)
                    if seg_e <= start or seg_s >= end:
                        continue
                    if getattr(seg, "text", ""):
                        kept_seg_texts.append(seg.text)
                new_text = " ".join(kept_seg_texts).strip() or (old_tr.text or "")

                new_tr = await create_video_audio_transcription(
                    run_id=mg.run_id or "",
                    task=getattr(old_tr, "task", "") or "",
                    language=getattr(old_tr, "language", "") or "",
                    duration=new_duration,
                    text=new_text,
                    audio_url=trimmed_url,
                    filename=getattr(old_tr, "filename", "") or "",
                    is_instrumental=bool(getattr(old_tr, "is_instrumental", False)),
                    user_id=user_id or "",
                    conversation_id=mg.conversation_id or "",
                    thread_id=mg.thread_id or "",
                    additional_data={
                        **(getattr(old_tr, "additional_data", None) or {}),
                        "from_smart_clip": True,
                        "source_transcription_uuid": old_tr.uuid,
                        "trim_start_sec": start,
                        "trim_end_sec": end,
                    },
                    song_name=getattr(old_tr, "song_name", None),
                    global_bpm=getattr(old_tr, "global_bpm", None),
                    genre=getattr(old_tr, "genre", None),
                    global_emotion=getattr(old_tr, "global_emotion", None),
                    suggested_global_theme=getattr(old_tr, "suggested_global_theme", None),
                    suggested_color_palette=getattr(old_tr, "suggested_color_palette", None),
                )

                # 切 segments：保留与 [start, end] 重叠的；时间轴减去 start 并 clamp 到 [0, new_duration]
                new_seg_id = 0
                for seg in old_segments or []:
                    seg_s = float(getattr(seg, "start", 0.0) or 0.0)
                    seg_e = float(getattr(seg, "end", 0.0) or 0.0)
                    overlap_s = max(seg_s, start)
                    overlap_e = min(seg_e, end)
                    if overlap_e - overlap_s < 0.05:
                        continue
                    rel_s = max(0.0, overlap_s - start)
                    rel_e = min(new_duration, overlap_e - start)
                    rel_dur = max(0.0, rel_e - rel_s)
                    if rel_dur < 0.05:
                        continue
                    await create_video_audio_segment(
                        run_id=mg.run_id or "",
                        transcription_uuid=new_tr.uuid,
                        segment_id=new_seg_id,
                        start=rel_s,
                        end=rel_e,
                        duration=rel_dur,
                        text=getattr(seg, "text", "") or "",
                        user_id=user_id or "",
                        conversation_id=mg.conversation_id or "",
                        thread_id=mg.thread_id or "",
                        emotion=getattr(seg, "emotion", None),
                        tempo=getattr(seg, "tempo", None),
                        additional_data=getattr(seg, "additional_data", None),
                        vocal_presence=getattr(seg, "vocal_presence", None),
                        vocal_gender=getattr(seg, "vocal_gender", None),
                    )
                    new_seg_id += 1

                # 切 sections：同样处理
                kept_sections = 0
                for sec in old_sections or []:
                    sec_s = float(getattr(sec, "start_time", 0.0) or 0.0)
                    sec_e = float(getattr(sec, "end_time", 0.0) or 0.0)
                    overlap_s = max(sec_s, start)
                    overlap_e = min(sec_e, end)
                    if overlap_e - overlap_s < 0.05:
                        continue
                    rel_s = max(0.0, overlap_s - start)
                    rel_e = min(new_duration, overlap_e - start)
                    if rel_e - rel_s < 0.05:
                        continue
                    await create_video_audio_section(
                        transcription_uuid=new_tr.uuid,
                        section_type=getattr(sec, "section_type", "") or "",
                        start_time=rel_s,
                        end_time=rel_e,
                        user_id=user_id or "",
                        conversation_id=mg.conversation_id or "",
                        thread_id=mg.thread_id or "",
                        run_id=mg.run_id or "",
                        musical_features=getattr(sec, "musical_features", None),
                        section_emotion=getattr(sec, "section_emotion", None),
                        suggested_visual_intensity=getattr(sec, "suggested_visual_intensity", None),
                        suggested_rhythmic_strategy=getattr(sec, "suggested_rhythmic_strategy", None),
                        suggested_visual_theme=getattr(sec, "suggested_visual_theme", None),
                        suggested_context=getattr(sec, "suggested_context", None),
                        additional_data=getattr(sec, "additional_data", None),
                    )
                    kept_sections += 1

                logger.info(
                    "smart_clip resume: 🔁 已切 transcription old=%s -> new=%s, duration=%.2fs, segments=%d, sections=%d",
                    old_tr.uuid, new_tr.uuid, new_duration, new_seg_id, kept_sections,
                )

                # —— 5. 用新 transcription 重建 per-segment mg
                # 这一组 per-segment mg 关联到 new_tr.uuid（mgv.audio_transcription_id），
                # 前端 / video_assembly 通过 ``get_music_generations_by_latest_transcription`` 拿
                # 最新 transcription 的 mg 集合 → 旧 transcription 关联的旧 mg 自然不再展示，
                # 等价于"音乐切片集合的版本升级（v1 集合 → v2 集合）"，无需 archived 标记。
                try:
                    new_seg_mg_ids = await create_music_from_audio_transcription_segments(
                        audio_transcription_id=new_tr.uuid,
                        conversation_id=mg.conversation_id or "",
                        thread_id=mg.thread_id or "",
                        run_id=mg.run_id or "",
                        user_id=user_id or "",
                        story_outline_id=None,
                    )
                    logger.info(
                        "smart_clip resume: 🆕 已按新 transcription 重建 per-segment mg：%d 条 (新 tr=%s)",
                        len(new_seg_mg_ids or []), new_tr.uuid,
                    )
                    # 通知前端：mg 集合已经被替换（v1 集合 → v2 集合）。
                    # 前端 musicData 是 thread 级缓存，只在 hasMusicContent=false 时才轮询，
                    # 不主动发事件前端就永远停留在旧的 mg 集合上 → "music section 没有立马更新"。
                    # hook 不在 LangGraph runnable context：HTTP stream writer 会 silently fail，
                    # 但 DB message + Redis Stream 两条通道都能可靠落地，
                    # 前端 SSE / conversation detail polling 任一通道都能拿到。
                    if new_seg_mg_ids and run_id and mg.conversation_id:
                        try:
                            # 与 music_generation_node 一致：直接用调用方传入的 detected_language
                            # （process_chat_request_stream → hook，与 state["detected_language"] 等价）
                            trim_duration_sec = int(round(float(end - start)))
                            trim_message = await get_i18n_message_async(
                                "music.trim_applied",
                                default="Your trim has been applied: about {duration}s of music kept. Storyboarding and shots will use this new version",
                                params={"duration": trim_duration_sec},
                                lang=detected_language,
                            )
                            await BaseAgent().async_send_event(
                                event_type=MessageType.MUSIC_GENERATED,
                                conversation_id=mg.conversation_id,
                                run_id=run_id,
                                message=trim_message,
                                extra_data={
                                    "music_generation_ids": [str(x) for x in new_seg_mg_ids],
                                    "music_count": len(new_seg_mg_ids),
                                    "run_id": run_id,
                                    "thread_id": thread_id,
                                    "refresh_after_trim": True,
                                    "trimmed_transcription_uuid": new_tr.uuid,
                                    "trim_duration_sec": trim_duration_sec,
                                },
                            )
                            logger.info(
                                "smart_clip resume: 📢 已发 music_generated(refresh_after_trim) 事件 (mg=%d, lang=%s, dur=%ds, run_id=%s)",
                                len(new_seg_mg_ids), detected_language or "en", trim_duration_sec, run_id,
                            )
                        except Exception as e:
                            logger.warning(
                                "smart_clip resume: 发 music_generated(refresh_after_trim) 事件失败（不阻塞）: %s", e,
                            )
                except Exception as e:
                    logger.exception("smart_clip resume: 重建 per-segment mg 失败（不阻塞 graph resume）: %s", e)
        except Exception as e:
            logger.exception("smart_clip resume: 切 transcription/sections/segments 失败（不阻塞 graph resume）: %s", e)

        logger.info(
            "smart_clip resume: ✅ trimmed mg=%s v%d uuid=%s [%.2f..%.2f]s fade_in=%.2f fade_out=%.2f",
            music_uuid, next_ver_number, new_uuid, start, end, fi, fo,
        )
        return new_uuid
    except Exception as e:
        logger.exception("smart_clip resume: 未预期错误 (thread_id=%s): %s", thread_id, e)
        return None


def merge_user_input_resources(
    new_items: List[Union[ImageUserInput, AudioFileUserInput, VideoFileUserInput]],
    old_items: List[Union[ImageUserInput, AudioFileUserInput, VideoFileUserInput]]
) -> List[Union[ImageUserInput, AudioFileUserInput, VideoFileUserInput]]:
    """
    合并新旧资源列表（images/audio_files/video_files）
    
    规则：
    1. 新资源放在列表前面（优先级更高），标记 is_new=True
    2. 旧资源放在列表后面，标记 is_new=False
    3. 基于 url 去重（保留第一次出现的）
    
    Args:
        new_items: 新的资源列表（来自当前请求，会放在前面）
        old_items: 旧的资源列表（来自历史，会放在后面）
    
    Returns:
        合并后的资源列表：[新资源...] + [旧资源（去重后）...]
    """
    seen_urls = set()
    merged = []
    
    # 第一步：添加新资源到列表前面，标记为 is_new=True
    for item in new_items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            # 确保新资源标记为 is_new=True
            item.is_new = True
            merged.append(item)
    
    # 第二步：添加旧资源到列表后面（跳过重复的），标记为 is_new=False
    for item in old_items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            # 旧资源标记为 is_new=False
            item.is_new = False
            merged.append(item)
    
    return merged


def merge_user_input_data(
    new_user_input_data: UserInput,
    old_user_input_data: Optional[UserInput]
) -> UserInput:
    """
    合并新旧 UserInput 数据
    
    规则：
    - 使用新的 user_input、user_option、agent_type
    - 合并资源列表（新的在前，去重）
    
    Args:
        new_user_input_data: 新的用户输入数据（来自当前请求）
        old_user_input_data: 旧的用户输入数据（来自历史 state）
    
    Returns:
        合并后的 UserInput
    """
    # 如果没有历史数据，直接返回新数据
    if not old_user_input_data:
        return new_user_input_data
    
    # 合并资源列表（新的在前面）
    merged_images = merge_user_input_resources(
        new_user_input_data.images,
        old_user_input_data.images
    )
    merged_audio_files = merge_user_input_resources(
        new_user_input_data.audio_files,
        old_user_input_data.audio_files
    )
    merged_video_files = merge_user_input_resources(
        new_user_input_data.video_files,
        old_user_input_data.video_files
    )
    
    # 创建合并后的 UserInput（使用新的文本和配置；content_category 在 user_option 内）
    merged_user_input = UserInput(
        user_input=new_user_input_data.user_input,
        images=merged_images,
        audio_files=merged_audio_files,
        video_files=merged_video_files,
        user_option=new_user_input_data.user_option,
        agent_type=new_user_input_data.agent_type,
    )
    
    logger.info(
        f"🔄 资源合并完成: "
        f"images={len(merged_images)} (新:{len(new_user_input_data.images)}, 旧:{len(old_user_input_data.images)}), "
        f"audio={len(merged_audio_files)} (新:{len(new_user_input_data.audio_files)}, 旧:{len(old_user_input_data.audio_files)}), "
        f"video={len(merged_video_files)} (新:{len(new_user_input_data.video_files)}, 旧:{len(old_user_input_data.video_files)})"
    )
    
    return merged_user_input


async def merge_user_option_with_input(
    user_input: str,
    default_user_option: UserOption
) -> UserOption:
    """使用 LLM 根据用户输入智能覆盖用户选项"""
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from prompts.prompt_config import PromptName, PROMPTS_CONFIG
    from app.services.agent.utils.llm_resilience import (
        StructuredResilienceKind,
        ainvoke_structured_resilient,
    )

    prompt_template, _llm = await load_prompt_with_fallback_async(
        hub_name=PromptName.AGENT_ROUTER_USER_OPTION_MERGE.value,
        local_template_name="agent_router/agent_router_user_option_merge",
        schema=None,
        include_raw=False
    )

    messages = prompt_template.format_messages(
        user_input=user_input,
        default_user_option=default_user_option.model_dump_json()
    )

    result = await ainvoke_structured_resilient(
        prompt_entry=PROMPTS_CONFIG[PromptName.AGENT_ROUTER_USER_OPTION_MERGE],
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs={"messages": messages},
    )
    merged_user_option = result["structured_response"]
    # 合并不推断「完全托管」开关，保持用户入口选择，避免结构化输出默认 false 覆盖
    merged_user_option = merged_user_option.model_copy(
        update={"full_auto": default_user_option.full_auto}
    )

    logger.info(f"✅ 用户选项合并完成: {merged_user_option.model_dump()}")

    return merged_user_option


@dataclass
class AgentRouterContextSchema:
    """Agent Router Runtime Context Schema"""
    async_db: AsyncSession  # 异步数据库连接


class AgentType(str, Enum):
    """代理类型枚举"""
    VIDEO = "video"          # 视频代理（旧 Workflow 多节点流水线）
    VIDEO_GEN = "video_gen"  # 视频直生代理（SD2 单模型直接出片，对齐 image agent）
    STORY = "story"          # 故事代理
    MUSIC = "music"          # 音乐代理
    IMAGE = "image"          # 图像代理
    CLARIFY = "clarify"      # 澄清代理，用于询问用户意图
    UNKNOWN = "unknown"       # 未知类型，需要用户选择（保留备用）


class InterruptType(str, Enum):
    """中断类型枚举"""
    AGENT_SELECTION = "agent_selection"  # 代理选择


class RouterAnalysisResult(BaseModel):
    """路由分析结果"""
    agent_type: AgentType = Field(description="选择的代理类型")
    confidence: float = Field(description="置信度 0.0-1.0")
    reason: str = Field(description="选择原因")
    key_indicators: List[str] = Field(description="关键指标", default_factory=list)
    detected_language: str = Field(default="en", description="检测到的用户输入语言的ISO 639-1语言代码（如en, zh, fr, es, ja, de等）")



class AgentRouterRequest(BaseModel):
    """Agent路由器请求模型"""
    user_id: str = Field(description="用户ID")
    user_input_data: UserInput = Field(description="用户输入数据")
    conversation_id: Optional[int] = Field(description="会话ID", default=None)
    thread_id: Optional[str] = Field(description="线程ID", default=None)



apool = AsyncConnectionPool(
    conninfo=settings.DATABASE_URL,
    max_size=10,
    max_idle=300,  # 对标 asyncpg max_inactive_connection_lifetime
    check=AsyncConnectionPool.check_connection,  # 对标 SQLAlchemy pool_pre_ping
    open=False,
    kwargs={'autocommit': True, 'prepare_threshold': 0},
)

async def setup_async_checkpointer():
    await apool.open()
    apostgres_checkpointer = AsyncPostgresSaver(apool)
    await apostgres_checkpointer.setup()
    return apostgres_checkpointer

_apostgres_checkpointer = None

async def apostgres_checkpointer():
    global _apostgres_checkpointer
    if not _apostgres_checkpointer:
        _apostgres_checkpointer = await setup_async_checkpointer()
    return _apostgres_checkpointer

video_agent = get_video_agent()

def _run_id_reducer(current: Optional[str], update: Optional[str]) -> Optional[str]:
    """Resolve concurrent run_id updates in one step (e.g. resume + Command(update={\"run_id\": ...})). Prefer non-None update."""
    return update if update is not None else current


# 定义Agent路由器的状态类型
class AgentRouterState(BaseModel):
    """Agent路由器状态定义"""
    # 原始请求
    request: Optional[AgentRouterRequest] = Field(default=None, description="原始请求对象")
    
    # 处理后的字段
    user_id: str = Field(default="", description="用户ID")
    user_input_data: Optional[UserInput] = Field(default=None, description="用户输入数据")
    conversation_id: Optional[int] = Field(default=None, description="会话ID")
    conversation_uuid: Optional[str] = Field(default=None, description="会话UUID")
    thread_id: Optional[str] = Field(default=None, description="线程ID")
    run_id: Annotated[Optional[str], _run_id_reducer] = Field(default=None, description="运行ID")
    
    # 语言识别
    detected_language: Optional[str] = Field(default=None, description="检测到的用户输入语言（ISO 639-1代码）")
    
    # 分析结果
    router_analysis: Optional[RouterAnalysisResult] = Field(default=None, description="路由分析结果")
    selected_agent: Optional[AgentType] = Field(default=None, description="选中的代理类型")

    video_agent_state: Optional[VideoAgentState] = Field(default=None, description="视频代理状态")

    # 全自动模式（仅 admin 测试用，不暴露前端）：True 时视频门控不 interrupt
    full_auto: Optional[bool] = Field(default=None, description="全自动执行到完成，不暂停等待用户")

    # 测试用语言覆盖（如 zh），由 task_data.language 传入，有则覆盖路由分析得到的 detected_language
    language: Optional[str] = Field(default=None, description="测试用语言（ISO 639-1），有则覆盖 detected_language")

    # ChatAgent 委托：已选定下游 agent 时跳过 AGENT_ROUTER_ANALYSIS LLM
    skip_router_analysis: Optional[bool] = Field(default=None, description="True 时跳过路由 LLM，使用 selected_agent 与 detected_language")

    # failed_video 重试：checkpoint 已越过 interrupt 时，route_to_video 用 Command(goto=video_generation) 续跑
    retry_video_generation: Optional[bool] = Field(
        default=None,
        description="True 时 video 子图跳到 video_generation 重跑镜头视频",
    )
    
    # LangGraph标准消息历史
    messages: Annotated[List[BaseMessage], add_messages_with_run_id] = Field(default_factory=list, description="消息历史")



class AgentRouterService(BaseAgent):
    """Agent路由器服务 - 使用LangGraph和AsyncPostgresSaver"""
    
    def __init__(self):
        """初始化Agent路由器服务"""
        BaseAgent.__init__(self)
        # 路由分析使用 load_prompt(AGENT_ROUTER_ANALYSIS) 取 LLM，不再维护 default LLM

    def _create_async_send_event_func(self, runtime: Runtime[AgentRouterContextSchema], state: AgentRouterState):
        """创建 send_event 包装：注入 conversation_uuid 和 run_id，确保事件能推送到 Redis stream。
        resume 时优先使用 config 中的 run_id（当前任务 run_id），否则事件会写入旧 run_id 的 stream。"""
        conversation_uuid = state.conversation_uuid if state.conversation_uuid else None
        run_id = state.run_id if state.run_id else None
        try:
            from langgraph.config import get_config
            config_run_id = (get_config() or {}).get("configurable", {}).get("run_id")
            if config_run_id:
                run_id = config_run_id
        except Exception:
            pass

        async def async_send_event_with_db(*args, **kwargs):
            kwargs.pop("async_db", None)  # BaseAgent.async_send_event 不接受，避免 image/story/music 报错
            if conversation_uuid and "conversation_uuid" not in kwargs:
                kwargs["conversation_uuid"] = conversation_uuid
            if run_id and "run_id" not in kwargs:
                kwargs["run_id"] = run_id
            extra = kwargs.get("extra_data") or {}
            if run_id and "run_id" not in extra:
                extra["run_id"] = run_id
                kwargs["extra_data"] = extra
            return await self.async_send_event(*args, **kwargs)
        return async_send_event_with_db
    
    def _route_entry(self, state: AgentRouterState) -> str:
        """START 分支：委托任务只合并输入，不走路由 LLM 与路由层事件。"""
        if state.skip_router_analysis and state.selected_agent is not None:
            return "delegated_merge_input"
        return "process_user_request"
    
    async def delegated_merge_input(
        self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]
    ) -> Dict[str, Any]:
        """ChatAgent 已决策下游 agent：仅合并 user_input / user_option 与资源列表；不校验会话、不写 DB、不发 SESSION_CREATED/USER_INPUT/AGENT_TYPE_DETERMINED。"""
        request = state.request
        thread_id = (request.thread_id if request else None) or state.thread_id
        conversation_id = (request.conversation_id if request else None) or state.conversation_id
        user_id = (request.user_id if request else None) or state.user_id
        run_id = state.run_id or str(uuid.uuid4())
        conversation_uuid = state.conversation_uuid

        if request:
            user_input_data = merge_user_input_data(
                new_user_input_data=request.user_input_data,
                old_user_input_data=state.user_input_data,
            )
        else:
            user_input_data = state.user_input_data

        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)

        default_user_option = user_input_data.user_option if user_input_data.user_option else UserOption.default()
        merged_user_option = default_user_option

        if user_input_data.user_input:
            try:
                merged_user_option = await merge_user_option_with_input(
                    user_input=user_input_data.user_input,
                    default_user_option=default_user_option,
                )
                user_input_data.user_option = merged_user_option
                logger.info("✅ 用户选项已根据输入智能合并 (delegated)")
            except Exception as e:
                logger.warning(f"⚠️ 用户选项合并失败，使用默认值: {e}")

        user_input_files_dict = None
        if user_input_data.images or user_input_data.audio_files or user_input_data.video_files:
            user_input_files_dict = {
                "images": [img.model_dump() for img in user_input_data.images] if user_input_data.images else [],
                "audio_files": [audio.model_dump() for audio in user_input_data.audio_files] if user_input_data.audio_files else [],
                "video_files": [video.model_dump() for video in user_input_data.video_files] if user_input_data.video_files else [],
            }

        detected_language = state.detected_language or getattr(state, "language", None) or "en"
        if getattr(state, "language", None):
            detected_language = state.language

        selected_agent = state.selected_agent
        if selected_agent is None:
            raise BusinessException(
                BusinessExceptionCode.INTERNAL_SERVER_ERROR,
                "delegated_merge_input 需要 state.selected_agent",
            )

        logger.info("✅ 委托入口：agent=%s language=%s (无路由事件)", selected_agent.value, detected_language)

        return {
            "user_id": user_id,
            "user_input_data": user_input_data,
            "conversation_id": conversation_id,
            "conversation_uuid": conversation_uuid,
            "thread_id": thread_id,
            "run_id": run_id,
            "detected_language": detected_language,
            "router_analysis": None,
            "selected_agent": selected_agent,
            "messages": list(state.messages or []),
        }
    
    async def _build_graph(self) -> CompiledStateGraph:
        """构建Agent路由器图（用于FastAPI服务，使用PostgreSQL持久化）"""
        graph = StateGraph(AgentRouterState)
        
        # 添加节点（路由分析已合并到 process_user_request，一开始即确定 language + agent_type）
        graph.add_node("delegated_merge_input", self.delegated_merge_input)
        graph.add_node("process_user_request", self.process_user_request)
        graph.add_node("route_to_video", self._route_to_video)
        graph.add_node("route_to_video_gen", self._route_to_video_gen)
        graph.add_node("route_to_story", self._route_to_story)
        graph.add_node("route_to_music", self._route_to_music)
        graph.add_node("route_to_image", self._route_to_image)
        graph.add_node("route_to_clarify", self._route_to_clarify)
        graph.add_node("route_to_unknown", self._route_to_unknown)
        
        graph.add_conditional_edges(
            START,
            self._route_entry,
            {
                "delegated_merge_input": "delegated_merge_input",
                "process_user_request": "process_user_request",
            },
        )
        route_targets = {
            AgentType.VIDEO.value: "route_to_video",
            AgentType.VIDEO_GEN.value: "route_to_video_gen",
            AgentType.STORY.value: "route_to_story",
            AgentType.MUSIC.value: "route_to_music",
            AgentType.IMAGE.value: "route_to_image",
            AgentType.CLARIFY.value: "route_to_clarify",
            AgentType.UNKNOWN.value: "route_to_unknown",
        }
        graph.add_conditional_edges("delegated_merge_input", self._decide_route, route_targets)
        graph.add_conditional_edges("process_user_request", self._decide_route, route_targets)
        graph.add_edge("route_to_video", END)
        graph.add_edge("route_to_video_gen", END)
        graph.add_edge("route_to_story", END)
        graph.add_edge("route_to_music", END)
        graph.add_edge("route_to_image", END)
        graph.add_edge("route_to_clarify", END)
        # route_to_unknown 后使用条件边路由到对应的代理
        graph.add_conditional_edges(
            "route_to_unknown",
            self._decide_route_after_selection,
            {
                AgentType.VIDEO.value: "route_to_video",
                AgentType.VIDEO_GEN.value: "route_to_video_gen",
                AgentType.STORY.value: "route_to_story",
                AgentType.MUSIC.value: "route_to_music",
                AgentType.IMAGE.value: "route_to_image"
            }
        )
        
        memory = await apostgres_checkpointer()
        return graph.compile(checkpointer=memory, name="Agent Router")
    
    def build_graph_for_langsmith(self) -> CompiledStateGraph:
        """
        构建用于 LangSmith Deployment 的图（同步版本，无需 PostgreSQL checkpointer）。
        仅用于 langgraph dev 和 LangSmith cloud deployment，不影响 FastAPI 服务逻辑。
        """
        graph = StateGraph(AgentRouterState)
        
        # 添加节点（路由分析已合并到 process_user_request）
        graph.add_node("delegated_merge_input", self.delegated_merge_input)
        graph.add_node("process_user_request", self.process_user_request)
        graph.add_node("route_to_video", self._route_to_video)
        graph.add_node("route_to_video_gen", self._route_to_video_gen)
        graph.add_node("route_to_story", self._route_to_story)
        graph.add_node("route_to_music", self._route_to_music)
        graph.add_node("route_to_image", self._route_to_image)
        graph.add_node("route_to_clarify", self._route_to_clarify)
        graph.add_node("route_to_unknown", self._route_to_unknown)
        
        graph.add_conditional_edges(
            START,
            self._route_entry,
            {
                "delegated_merge_input": "delegated_merge_input",
                "process_user_request": "process_user_request",
            },
        )
        route_targets_ls = {
            AgentType.VIDEO.value: "route_to_video",
            AgentType.VIDEO_GEN.value: "route_to_video_gen",
            AgentType.STORY.value: "route_to_story",
            AgentType.MUSIC.value: "route_to_music",
            AgentType.IMAGE.value: "route_to_image",
            AgentType.CLARIFY.value: "route_to_clarify",
            AgentType.UNKNOWN.value: "route_to_unknown",
        }
        graph.add_conditional_edges("delegated_merge_input", self._decide_route, route_targets_ls)
        graph.add_conditional_edges("process_user_request", self._decide_route, route_targets_ls)
        graph.add_edge("route_to_video", END)
        graph.add_edge("route_to_video_gen", END)
        graph.add_edge("route_to_story", END)
        graph.add_edge("route_to_music", END)
        graph.add_edge("route_to_image", END)
        graph.add_edge("route_to_clarify", END)
        graph.add_conditional_edges(
            "route_to_unknown",
            self._decide_route_after_selection,
            {
                AgentType.VIDEO.value: "route_to_video",
                AgentType.VIDEO_GEN.value: "route_to_video_gen",
                AgentType.STORY.value: "route_to_story",
                AgentType.MUSIC.value: "route_to_music",
                AgentType.IMAGE.value: "route_to_image"
            }
        )
        
        return graph.compile()
    
    async def process_user_request(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """处理用户请求 - 初始化会话并验证用户权限"""
        request = state.request
        thread_id = (request.thread_id if request else None) or state.thread_id
        
        # 处理conversation_id并验证安全性（必须在最开始验证）
        conversation_id = (request.conversation_id if request else None) or state.conversation_id
        user_id = (request.user_id if request else None) or state.user_id
        # 使用state中的run_id（从任务中传递过来的）
        # AgentRouterState是Pydantic BaseModel，使用属性访问而不是.get()
        run_id = state.run_id or str(uuid.uuid4())
        
        # ✅ 修复：按需创建连接，不依赖 context
        from ...crud.conversation import async_get_conversation_by_id, async_get_conversation_by_thread_id, async_create_conversation
        from ...models.database import AsyncSessionLocal
        
        # 安全验证：对话表应该在任务提交时已创建，这里只验证和获取
        if conversation_id:
            conversation = await async_get_conversation_by_id(conversation_id)
            if not conversation:
                raise BusinessException(BusinessExceptionCode.NOT_FOUND, "会话不存在")
            if conversation.user_id != user_id:
                raise BusinessException(BusinessExceptionCode.FORBIDDEN, "无权访问此会话")
            if conversation.thread_id != thread_id:
                raise BusinessException(BusinessExceptionCode.FORBIDDEN, "会话thread_id不匹配")
        else:
            # 通过thread_id查找现有会话（应该已存在）
            existing_conversation = await async_get_conversation_by_thread_id(thread_id)
            if not existing_conversation:
                raise BusinessException(
                    BusinessExceptionCode.NOT_FOUND, 
                    "对话不存在，应该在任务提交时创建"
                )
            # 验证用户权限
            if existing_conversation.user_id != user_id:
                raise BusinessException(BusinessExceptionCode.FORBIDDEN, "无权访问此会话")
            conversation_id = existing_conversation.id
            conversation = existing_conversation
            logger.info(f"找到现有会话: {conversation_id}")
        
        is_new_conversation = False  # 不再在这里创建新对话
        
        # 获取并合并用户输入数据
        if request:
            # 有新请求：合并新旧数据（新资源在前）
            user_input_data = merge_user_input_data(
                new_user_input_data=request.user_input_data,
                old_user_input_data=state.user_input_data
            )
        else:
            # 无新请求：直接使用 state 中的数据
            user_input_data = state.user_input_data
        
        # ✅ 修复：在 langgraph dev 环境下，state 中的对象会被序列化为 dict
        # 需要将 dict 转换回 UserInput 对象
        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)

        # 处理 userOption：使用 LLM 根据用户输入智能覆盖
        default_user_option = user_input_data.user_option if user_input_data.user_option else UserOption.default()
        merged_user_option = default_user_option
        
        if user_input_data.user_input:
            try:
                merged_user_option = await merge_user_option_with_input(
                    user_input=user_input_data.user_input,
                    default_user_option=default_user_option
                )
                # 更新 user_input_data 中的 user_option
                user_input_data.user_option = merged_user_option
                logger.info(f"✅ 用户选项已根据输入智能合并")
            except Exception as e:
                logger.warning(f"⚠️ 用户选项合并失败，使用默认值: {e}")
                # 失败时使用默认值，不影响流程
        
        # 准备文件列表（用于存储）
        user_input_files_dict = None
        if user_input_data.images or user_input_data.audio_files or user_input_data.video_files:
            user_input_files_dict = {
                "images": [img.model_dump() for img in user_input_data.images] if user_input_data.images else [],
                "audio_files": [audio.model_dump() for audio in user_input_data.audio_files] if user_input_data.audio_files else [],
                "video_files": [video.model_dump() for video in user_input_data.video_files] if user_input_data.video_files else []
            }
        
        # ConversationRunDB记录应该在任务提交时已创建，这里不再创建
        # 只需要获取conversation对象（如果还没有）
        if 'conversation' not in locals():
            from ...crud.conversation import async_get_conversation_by_id
            conversation = await async_get_conversation_by_id(conversation_id)
        
        logger.info(f"✅ 使用已有对话运行记录: run_id={run_id}, conversation_id={conversation_id}")

        # 发送会话创建事件，前端会监听此事件来刷新对话列表
        # hidden=True: 不在对话框中显示，但前端需要监听此事件来刷新对话列表
        await self.async_send_event(
            event_type=MessageType.SESSION_CREATED,
            conversation_id=conversation_id,
            conversation_uuid=conversation.uuid if conversation else None,
            extra_data={
                "conversation_id": conversation_id,
                "thread_id": thread_id,
                "user_id": user_id,
                "run_id": run_id
            },
            hidden=True
        )
        
        # 先做路由分析，拿到 detected_language 和 agent_type，再发 USER_INPUT（前端从该事件或 detail 都能拿到）
        user_input = user_input_data.user_input
        images = user_input_data.images
        agent_type = user_input_data.agent_type
        messages = await self._filter_cancelled_history(state.messages or [], state.thread_id)
        has_history = len(messages) > 0
        
        from prompts.prompt_loader import load_prompt_with_fallback_async
        from prompts.prompt_config import PromptName
        
        prompt_template, _router_llm = await load_prompt_with_fallback_async(
            hub_name=PromptName.AGENT_ROUTER_ANALYSIS.value,
            local_template_name="agent_router/agent_router_analysis",
            schema=None,
            include_raw=False
        )
        template_data = {
            "user_input": user_input,
            "has_images": len(images) > 0 if images else False,
            "specified_agent_type": agent_type if agent_type else None,
            "has_history": has_history
        }
        prompt_messages = (await prompt_template.ainvoke(template_data)).messages
        all_messages = messages + prompt_messages
        from prompts.prompt_config import PROMPTS_CONFIG
        from app.services.agent.utils.llm_resilience import (
            StructuredResilienceKind,
            ainvoke_structured_resilient,
        )
        raw_result = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[PromptName.AGENT_ROUTER_ANALYSIS],
            kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
            structured_chat_messages=all_messages,
            include_raw=True,
            structured_schema=RouterAnalysisResult,
        )
        analysis_result = raw_result["parsed"]
        raw_message = raw_result.get("raw")
        detected_language = analysis_result.detected_language
        if getattr(state, "language", None):
            detected_language = state.language
        agent_type_value = analysis_result.agent_type.value
        logger.info(f"🌐 识别到用户输入语言: {detected_language}")
        logger.info(f"🎯 选择的 Agent: {agent_type_value}")
        logger.info(f"📊 置信度: {analysis_result.confidence}")
        
        # 写入 DB 两次（conversation 一次、run 一次），供对话列表/详情接口返回
        try:
            from ...crud.conversation import (
                async_update_conversation_agent_type_and_language,
                async_update_conversation_run_agent_type_and_language,
            )
            await async_update_conversation_agent_type_and_language(
                conversation_id, agent_type_value, detected_language
            )
            await async_update_conversation_run_agent_type_and_language(
                run_id, agent_type_value, detected_language
            )
            logger.info(f"✅ 已更新 conversation {conversation_id} 与 run {run_id} agent_type={agent_type_value} language={detected_language}")
        except Exception as e:
            logger.warning(f"⚠️ 更新 conversation/run agent_type/language 失败: {e}")
        
        # 发送用户输入事件（带上 language + agent_type，前端从 stream 或 detail 都能确定）
        new_images = [img for img in user_input_data.images if img.is_new]
        new_audio_files = [audio for audio in user_input_data.audio_files if audio.is_new]
        new_video_files = [video for video in user_input_data.video_files if video.is_new]
        
        await self.async_send_event(
            event_type=MessageType.USER_INPUT,
            conversation_id=conversation_id,
            conversation_uuid=conversation.uuid if conversation else None,
            message=user_input_data.user_input,
            extra_data={
                "thread_id": thread_id,
                "run_id": run_id,
                "user_id": user_id,
                "detected_language": detected_language,
                "agent_type": agent_type_value,
                "images": [img.model_dump() for img in new_images],
                "images_count": len(new_images),
                "audio_files": [audio.model_dump() for audio in new_audio_files],
                "audio_files_count": len(new_audio_files),
                "video_files": [video.model_dump() for video in new_video_files],
                "video_files_count": len(new_video_files)
            }
        )
        
        # 兼容前端：仍发 AGENT_TYPE_DETERMINED（前端 agentType===auto 时据此更新 UI）
        await self.async_send_event(
            event_type=MessageType.AGENT_TYPE_DETERMINED,
            conversation_id=conversation_id,
            conversation_uuid=conversation.uuid if conversation else None,
            extra_data={
                "agent_type": agent_type_value,
                "detected_language": detected_language,
                "thread_id": thread_id,
                "run_id": run_id
            },
            hidden=True,
        )
        
        collected_messages = prompt_messages + ([raw_message] if raw_message else [])
        logger.info(f"✅ 路由分析完成: {agent_type_value}")
        
        return {
            "user_id": user_id,
            "user_input_data": user_input_data,
            "conversation_id": conversation_id,
            "conversation_uuid": conversation.uuid if conversation else None,
            "thread_id": thread_id,
            "run_id": run_id,
            "detected_language": detected_language,
            "router_analysis": analysis_result,
            "selected_agent": AgentType(analysis_result.agent_type),  # enum for state
            "messages": collected_messages,
        }

    def _decide_route(self, state: AgentRouterState):
        """决定路由方向"""
        return state.selected_agent.value
    
    def _decide_route_after_selection(self, state: AgentRouterState):
        """用户选择后的路由决策"""
        # 如果用户已经选择了代理（通过 interrupt resume），使用用户选择
        selected_agent = state.selected_agent
        if selected_agent:
            return selected_agent.value
        # 默认路由到故事代理
        return AgentType.STORY.value
    
    
    
    async def _filter_cancelled_history(self, messages: List[BaseMessage], thread_id: Optional[str]) -> List[BaseMessage]:
        """过滤「已取消 run」的历史消息后带进 LLM context。

        保留取消 run 里的 HumanMessage（用户原始诉求不丢，续跑/后续轮次仍能延续语义），
        仅丢弃取消 run 产生的 AI/其他消息（半成品、被打断的回复），避免污染上下文。
        """
        if not messages or not thread_id:
            return messages
        try:
            cancelled = await async_get_runs_by_thread_id_and_status(thread_id, [TaskStatus.CANCELLED.value])
        except Exception as e:
            logger.warning(f"获取已取消run失败，不过滤历史: {e}")
            return messages
        cancelled_ids = {r.get("run_id") for r in (cancelled or []) if r.get("run_id")}
        if not cancelled_ids:
            return messages
        return [
            m for m in messages
            if isinstance(m, HumanMessage)
            or (getattr(m, "additional_kwargs", None) or {}).get("run_id") not in cancelled_ids
        ]

    @staticmethod
    def _collect_interrupt_steps(snapshot) -> List[str]:
        """收集父图 + video 子图上的 interrupt step（嵌套 ainvoke 时 interrupt 常只在子图）。"""
        steps: List[str] = []
        if not snapshot:
            return steps

        def _from_tasks(tasks) -> None:
            for t in tasks or []:
                for i in getattr(t, "interrupts", None) or []:
                    val = getattr(i, "value", i)
                    if isinstance(val, dict) and val.get("step"):
                        steps.append(str(val["step"]))
                    elif val is not None:
                        steps.append(str(val)[:80])
                sub = getattr(t, "state", None)
                if sub is not None:
                    _from_tasks(getattr(sub, "tasks", None))

        _from_tasks(getattr(snapshot, "tasks", None))
        return steps

    async def _try_recover_failed_video_resume(
        self,
        router_agent,
        config: Dict[str, Any],
        thread_id: Optional[str],
        run_id: Optional[str],
    ) -> bool:
        """仅在「错误 resume 后卡在 video_segments、且任何层都没有 interrupt」时恢复。

        注意：正常 after_shots 门控的 interrupt 往往只挂在 video 子图上；父图 tasks 可能 ints=0。
        那种情况必须走 Command(resume)，绝不能再 goto video_generation（否则会死循环）。
        """
        if not thread_id:
            return False
        try:
            snapshot = await router_agent.aget_state(config, subgraphs=True)
        except Exception as e:
            logger.warning("failed_video 恢复：取 checkpoint 失败: %s", e)
            return False
        if not snapshot:
            return False

        interrupt_steps = self._collect_interrupt_steps(snapshot)
        if interrupt_steps:
            logger.info(
                "failed_video 恢复：检测到 interrupt=%s，跳过恢复走正常 resume thread=%s",
                interrupt_steps[:5],
                thread_id,
            )
            return False

        video_task = next(
            (t for t in (getattr(snapshot, "tasks", None) or []) if getattr(t, "name", None) == "route_to_video"),
            None,
        )
        sub = getattr(video_task, "state", None) if video_task else None
        if sub is None:
            return False
        sub_next = tuple(getattr(sub, "next", None) or ())
        sub_vals = getattr(sub, "values", None) or {}
        stage_failure = sub_vals.get("stage_failure") if isinstance(sub_vals, dict) else None
        sf_stage = stage_failure.get("stage") if isinstance(stage_failure, dict) else None
        task_error = str(getattr(video_task, "error", None) or "")
        # 只认「已越过门控、卡在 segments/assembly」的失败态；不要把 after_shots / video_generation 当成恢复目标
        stuck_past_gate = (
            sf_stage == "video"
            and ("video_segments" in sub_next or "video_assembly" in sub_next)
        ) or (
            ("video_segments" in sub_next or "video_assembly" in sub_next)
            and ("组装" in task_error or "video_segments" in task_error)
        )
        if not stuck_past_gate:
            logger.info(
                "failed_video 恢复：未命中卡住条件 next=%s sf=%s err=%s thread=%s",
                sub_next,
                sf_stage,
                task_error[:120] if task_error else None,
                thread_id,
            )
            return False

        sub_cfg = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "route_to_video",
            }
        }
        try:
            target_cfg = None
            async for h in router_agent.aget_state_history(sub_cfg, limit=30):
                nxt = tuple(getattr(h, "next", None) or ())
                if nxt == ("video_generation",):
                    target_cfg = h.config
                    break
            if target_cfg is None:
                logger.warning(
                    "failed_video 恢复：未找到 next=video_generation 的历史 checkpoint thread=%s",
                    thread_id,
                )
                return False
            await router_agent.aupdate_state(target_cfg, {"stage_failure": None})
            logger.info(
                "🔁 failed_video 恢复：已 fork 子图到 video_generation thread=%s",
                thread_id,
            )

            parent_update: Dict[str, Any] = {"retry_video_generation": True}
            if run_id:
                parent_update["run_id"] = run_id
            await router_agent.aupdate_state(
                config,
                parent_update,
                as_node="delegated_merge_input",
            )
            logger.info(
                "🔁 failed_video 恢复：父图将重进 route_to_video（retry_video_generation）thread=%s run_id=%s",
                thread_id,
                run_id,
            )
            return True
        except Exception as e:
            logger.warning("failed_video 恢复失败 thread=%s: %s", thread_id, e, exc_info=True)
            return False

    async def _detect_cancel_resume_snapshot(self, router_agent, config, thread_id: Optional[str]):
        """判断这条「新消息」是否应续跑「被硬取消、checkpoint 仍挂起」的运行；是则返回其 state 快照，否则 None。

        三条件全部满足才续跑（避免误判新任务/正常完成/interrupt 等待）：
          1) 该 thread 存在 CANCELLED 的 run（确实发生过取消）；
          2) 父图 checkpoint 仍有挂起节点（state.next 非空，即未跑到 END）；
          3) 挂起任务不是在等待 interrupt 输入（那走 interrupt-resume 通道，须排除）。
        说明：先查取消 run，再取 checkpoint，普通新 thread 只多一次轻量 DB 查询。
        """
        if not thread_id:
            return None
        try:
            cancelled = await async_get_runs_by_thread_id_and_status(thread_id, [TaskStatus.CANCELLED.value])
        except Exception as e:
            logger.warning("resume-from-cancel 检测：查取消 run 失败，按新任务处理: %s", e)
            return None
        if not cancelled:
            return None
        try:
            snapshot = await router_agent.aget_state(config)
        except Exception as e:
            logger.warning("resume-from-cancel 检测：取 checkpoint 失败，按新任务处理: %s", e)
            return None
        if not snapshot or not getattr(snapshot, "next", None):
            return None
        for _t in (getattr(snapshot, "tasks", None) or []):
            if getattr(_t, "interrupts", None):
                logger.info("resume-from-cancel 检测：存在 pending interrupt，交由 interrupt-resume 通道处理")
                return None
        return snapshot

    async def _route_to_video(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到视频代理。"""
        run_id = state.run_id
        history_messages = await self._filter_cancelled_history(state.messages or [], state.thread_id)
        execution_state = {
            "user_input_data": state.user_input_data,
            "user_id": state.user_id,
            "conversation_id": state.conversation_id,
            "thread_id": state.thread_id,
            "run_id": run_id,
            "detected_language": state.detected_language or "en",  # 从路由状态获取语言
            "messages": history_messages,  # 传递历史消息（已过滤取消run）
            "full_auto": getattr(state, "full_auto", None),
        }
        
        # ✅ 修复：不通过 context 传递 async_db，让 Agent 节点按需创建连接
        # 避免整个 Agent 执行期间（5-10分钟）连接一直被占用
        from ..agent.video_agent_service import VideoContextSchema
        # 创建空的 context（不再传递 async_db）
        context = VideoContextSchema()
        
        from langgraph.errors import GraphInterrupt
        from langgraph.types import Command as LGCommand
        retry_video = bool(getattr(state, "retry_video_generation", None))
        try:
            if retry_video:
                logger.info(
                    "🎬 _route_to_video: retry_video_generation → Command(goto=video_generation) run_id=%s",
                    run_id,
                )
                video_input: Any = LGCommand(
                    goto="video_generation",
                    update={"stage_failure": None, "run_id": run_id},
                )
            else:
                logger.info("🎬 _route_to_video: calling video_agent.ainvoke() ...")
                video_input = execution_state
            video_agent_state = await video_agent.ainvoke(video_input, context=context)
            logger.info(
                "🎬 _route_to_video: ainvoke RETURNED NORMALLY (keys=%s, has __interrupt__=%s)",
                list(video_agent_state.keys()) if isinstance(video_agent_state, dict) else type(video_agent_state).__name__,
                "__interrupt__" in video_agent_state if isinstance(video_agent_state, dict) else "N/A",
            )
        except GraphInterrupt as gi:
            logger.info("🎬 _route_to_video: ainvoke raised GraphInterrupt (%d interrupts) — re-raising", len(gi.interrupts) if hasattr(gi, 'interrupts') else -1)
            raise
        except Exception as exc:
            logger.error("🎬 _route_to_video: ainvoke raised %s: %s", type(exc).__name__, exc, exc_info=True)
            raise
        
        # 添加路由完成消息
        route_message = AIMessage(content="已路由到视频代理")
        
        return {
            "messages": [route_message],
            "video_agent_state": video_agent_state,
            "retry_video_generation": None,
        }
    
    async def _emit_simple_agent_time_estimate(
        self, agent_type: str, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]
    ) -> None:
        """给 image/music/story/video_gen 这类单步 agent 下发整体耗时预估（复用 workflow_state 通道）。

        前端单步进度条据此显示「预计 ~Xs」倒计时。best-effort，失败不影响主流程。
        """
        try:
            from .video.time_estimation import estimate_simple_agent_seconds
            user_input_data = state.user_input_data
            user_option = getattr(user_input_data, "user_option", None) if user_input_data else None
            secs = estimate_simple_agent_seconds(agent_type, user_option)
            if not secs or secs <= 0:
                return
            send_event_func = self._create_async_send_event_func(runtime, state)
            await send_event_func(
                event_type=MessageType.WORKFLOW_STATE,
                conversation_id=state.conversation_id,
                extra_data={
                    "run_id": state.run_id,
                    "workflow_version": "2",
                    "agent_kind": agent_type,
                    "total_est_seconds": int(secs),
                    "estimate_confidence": "low",
                },
                hidden=True,
            )
        except Exception as e:
            logger.debug("simple agent time estimate emit skip (%s): %s", agent_type, e)

    async def _route_to_story(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到故事代理"""
        from .story.story_generation_service import generate_story_with_agent
        run_id = state.run_id
        await self._emit_simple_agent_time_estimate("story", state, runtime)
        
        # 执行故事生成
        user_input_data = state.user_input_data
        messages = await self._filter_cancelled_history(state.messages or [], state.thread_id)
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        result = await generate_story_with_agent(
            user_input_data=user_input_data,
            messages=messages,
            send_event_func=self._create_async_send_event_func(runtime, state),
            conversation_id=conversation_id,
            run_id=run_id,
            thread_id=state.thread_id,
            detected_language=detected_language
        )
        
        # 返回故事代理生成的所有消息，保持对话历史
        return {
            "messages": result.get("messages", [])
        }
    
    async def _route_to_music(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到音乐代理"""
        from .music.music_generation_service import generate_music_with_agent
        
        # 生成run_id（类似视频代理）
        run_id = state.run_id
        await self._emit_simple_agent_time_estimate("music", state, runtime)
        
        # 执行音乐生成
        user_input_data = state.user_input_data
        messages = await self._filter_cancelled_history(state.messages or [], state.thread_id)
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        result = await generate_music_with_agent(
            user_input_data=user_input_data,
            messages=messages,
            send_event_func=self._create_async_send_event_func(runtime, state),
            conversation_id=conversation_id,
            run_id=run_id,
            thread_id=state.thread_id,
            detected_language=detected_language
        )
        
        # 返回音乐代理生成的所有消息，保持对话历史
        return {
            "messages": result.get("messages", [])
        }
    
    async def _route_to_image(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到图像代理"""
        from .image.image_generation_service import generate_image_with_agent
        
        # 生成run_id（类似其他代理）
        run_id = state.run_id
        await self._emit_simple_agent_time_estimate("image", state, runtime)
        
        # 执行图像生成
        user_input_data = state.user_input_data
        messages = await self._filter_cancelled_history(state.messages or [], state.thread_id)
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        result = await generate_image_with_agent(
            user_input_data=user_input_data,
            messages=messages,
            send_event_func=self._create_async_send_event_func(runtime, state),
            conversation_id=conversation_id,
            run_id=run_id,
            thread_id=state.thread_id,
            detected_language=detected_language
        )
        
        # 返回图像代理生成的所有消息，保持对话历史
        return {
            "messages": result.get("messages", [])
        }

    async def _route_to_video_gen(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到视频直生代理（SD2 单模型直接出片，对齐 image agent）"""
        from .video_gen.video_generation_agent_service import generate_video_with_agent

        run_id = state.run_id
        await self._emit_simple_agent_time_estimate("video_gen", state, runtime)
        user_input_data = state.user_input_data
        messages = state.messages or []
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        result = await generate_video_with_agent(
            user_input_data=user_input_data,
            messages=messages,
            send_event_func=self._create_async_send_event_func(runtime, state),
            conversation_id=conversation_id,
            run_id=run_id,
            thread_id=state.thread_id,
            detected_language=detected_language
        )

        return {
            "messages": result.get("messages", [])
        }
    
    async def _route_to_clarify(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到澄清代理 - 流式生成澄清消息"""
        user_input_data = state.user_input_data
        
        # ✅ 修复：在 langgraph dev 环境下，state 中的对象会被序列化为 dict
        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)
        
        user_input = user_input_data.user_input
        detected_language = state.detected_language or "en"
        conversation_id = state.conversation_id
        run_id = state.run_id
        messages = await self._filter_cancelled_history(state.messages or [], state.thread_id)
        
        # 导入必要的模块
        from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from prompts.prompt_loader import load_prompt_with_fallback_async
        from prompts.prompt_config import PromptName
        
        prompt_template, _ = await load_prompt_with_fallback_async(
            hub_name=PromptName.AGENT_ROUTER_CLARIFY.value,
            local_template_name="agent_router/agent_router_clarify",
            schema=None,
            include_raw=False,
        )
        
        # 准备模板数据
        template_data = {
            "user_input": user_input
        }
        
        # 格式化消息
        # 使用 invoke 格式化消息（LangSmith 可追踪）
        prompt_messages = (await prompt_template.ainvoke(template_data)).messages
        
        # 添加语言提示（System + Human 都强化）
        apply_language_suffix_to_system_message_in_messages(prompt_messages, detected_language)
        
        # 如果有历史消息，添加到prompt_messages前面
        if messages:
            all_messages = messages + prompt_messages
        else:
            all_messages = prompt_messages
        
        from prompts.prompt_config import PROMPTS_CONFIG
        from prompts.prompt_loader import create_llm_from_model_config
        from .story.story_generation_service import _stream_chunk_to_text
        from .utils.llm_resilience import (
            build_resilience_bundle_from_prompt_entry,
            execute_with_resilience,
        )

        send_event_func = self._create_async_send_event_func(runtime, state)

        _entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CLARIFY]
        _ctx, _routes, _route_mcs = build_resilience_bundle_from_prompt_entry(_entry)

        async def _invoke_clarify_stream(
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
                                "target_event": MessageType.CLARIFY_RESPONSE.value,
                                "content_type": "text",
                            },
                        )
            final_content = "".join(full_content)
            ai_message = AIMessage(content=final_content)
            new_messages = prompt_messages + [ai_message]
            if send_event_func and conversation_id:
                await send_event_func(
                    event_type=MessageType.CLARIFY_RESPONSE,
                    conversation_id=conversation_id,
                    message=final_content,
                    extra_data={
                        "run_id": run_id,
                        "thread_id": state.thread_id,
                    },
                )
            return {"messages": new_messages}

        return await execute_with_resilience(
            _invoke_clarify_stream,
            routes=_routes,
            route_model_configs=_route_mcs,
            context=_ctx,
            log_context={
                "phase": "agent_router_clarify_stream",
                "conversation_id": conversation_id,
                "run_id": run_id,
            },
        )
    
    async def _route_to_unknown(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到未知类型 - 使用 LangGraph interrupt 机制让用户选择"""
        from langgraph.types import interrupt
        
        # 使用 LangGraph interrupt 机制 - 暂停执行等待用户输入
        # 如果是 resume 的情况，interrupt() 会直接返回 resume_data 的值（用户选择的 AgentType 字符串值）
        # 如果是首次进入，会暂停等待用户通过 resume_data 恢复
        user_choice = interrupt({
            "interrupt_type": InterruptType.AGENT_SELECTION.value,
            "available_agents": [AgentType.STORY.value, AgentType.MUSIC.value, AgentType.VIDEO.value, AgentType.IMAGE.value]
        })
        
        # 将用户选择保存到 state 中，供 conditional edge 使用
        # user_choice 应该是 AgentType 的字符串值（如 "video", "story", "music"）
        selected_agent = AgentType(user_choice) if user_choice else AgentType.STORY
        
        return {
            "selected_agent": selected_agent
        }

    
    
    async def astream(
        self, 
        user_input_data: UserInput,
        user_id: str, 
        conversation_id: int, 
        thread_id: str,
        async_db = None,
        resume_data: str = None,
        run_id: Optional[str] = None,
        conversation_uuid: Optional[str] = None,
        credit_callback=None,
        full_auto: bool = False,
        language: Optional[str] = None,
        skip_router_analysis: bool = False,
        delegated_agent_type: Optional[str] = None,
        delegated_detected_language: Optional[str] = None,
     ) -> AsyncGenerator[str, None]:
        """异步流式处理请求
        
        Args:
            resume_data: 如果提供，表示这是从 interrupt 恢复的请求
            run_id: 如果提供，使用此run_id（用于Worker执行任务）
            credit_callback: 外部传入的成本追踪callback（task_worker创建），若为None则内部自建
            full_auto: 仅 admin 测试用；True 时视频门控不 interrupt，一路执行到完成
            skip_router_analysis: True 且 delegated_agent_type 合法时跳过路由 LLM（ChatAgent 委托）
        """
        from langgraph.config import RunnableConfig
        
        router_agent = await self._build_graph()
        # 处理thread_id
        if not thread_id:
            thread_id = generate_new_thread_id(user_id)
            logger.info(f"生成新thread_id: {thread_id}")
        
        # 保存 conversation_id、thread_id 和 conversation_uuid 以便在异常处理和结束事件中使用
        _conversation_id = conversation_id
        _thread_id = thread_id
        _conversation_uuid = conversation_uuid  # 直接使用传入的参数
        
        # ✅ 修复：如果 async_db 为 None，不创建 context
        # 让 Agent 节点按需创建连接，避免长时间占用连接
        context = AgentRouterContextSchema(async_db=async_db) if async_db is not None else None

        try:
            # 准备初始状态（只在非 resume 模式下需要）
            initial_state = None
            if not resume_data:
                request = AgentRouterRequest(
                    user_id=user_id,
                    user_input_data=user_input_data,
                    conversation_id=conversation_id,
                    thread_id=thread_id
                )
                initial_state = {
                    "request": request,
                    "run_id": run_id,  # 使用任务中的 run_id
                    "full_auto": full_auto,
                    "language": language,
                }
                if skip_router_analysis and delegated_agent_type:
                    try:
                        _sa = AgentType(delegated_agent_type.lower())
                    except ValueError:
                        logger.warning(
                            "skip_router_analysis 无效 delegated_agent_type=%s，将执行路由 LLM",
                            delegated_agent_type,
                        )
                    else:
                        initial_state["skip_router_analysis"] = True
                        initial_state["selected_agent"] = _sa
                        initial_state["detected_language"] = (
                            delegated_detected_language or language or "en"
                        )
                        initial_state["user_id"] = user_id
                        initial_state["conversation_id"] = conversation_id
                        initial_state["thread_id"] = thread_id
                        initial_state["conversation_uuid"] = conversation_uuid
            
            # 设置配置（同时设置 thread_id 和 run_id，与 LangGraph 保持一致）
            configurable = {"thread_id": thread_id}
            if run_id:
                configurable["run_id"] = run_id
            
            # 积分检查callback：外部传入（task_worker创建）直接使用
            callbacks = []
            if credit_callback is not None:
                callbacks.append(credit_callback)
            
            # run_id 放 config 顶层，LangSmith tracer 才会用此 id 作为 root run，后续 read_run(run_id) 才能查到
            config = RunnableConfig(
                configurable=configurable,
                recursion_limit=300,
                callbacks=callbacks if callbacks else None,
                **({"run_id": run_id} if run_id else {}),
            )
            if run_id:
                _current_run_id_cv.set(run_id)
                # 同步设置「协作式取消」用的 run_id contextvar，供重节点内部长轮询/批次边界主动检查取消
                set_current_run_id(run_id)
            
            # 创建流式生成器
            # 根据 GitHub 讨论，需要使用 ["updates", "custom"] 来同时支持 interrupt 和自定义事件
            astream_kwargs = {
                "config": config,
                "stream_mode": ["updates", "custom"],  # 同时支持 interrupt 和自定义事件
                "subgraphs": True
            }
            
            # 如果提供了context，添加到参数中
            if context is not None:
                astream_kwargs["context"] = context
            
            # 根据是否有 resume_data 来决定调用方式
            if resume_data:
                # ✨ Smart Clip：在 graph resume 之前先处理 resume_data.smart_clip（trim + 落 v2 + 切版本）
                # 从 checkpointed state 取 detected_language（与 music_generation_node 同源：state["detected_language"]）。
                # 调用方参数（delegated_detected_language / language）只在「新对话路由」分支才设置，
                # SmartClip resume 走的是「已中断的现有对话」分支，需要从 checkpoint 读 graph state。
                _resume_lang: Optional[str] = None
                try:
                    _resume_state = await router_agent.aget_state(config)
                    _state_values = getattr(_resume_state, "values", None) or {}
                    _resume_lang = _state_values.get("detected_language")
                except Exception as _e_state:
                    logger.debug("smart_clip resume: 取 checkpoint detected_language 失败: %s", _e_state)
                try:
                    await _apply_smart_clip_from_resume_data(
                        thread_id=thread_id,
                        user_id=user_id,
                        resume_data=resume_data,
                        run_id=run_id,
                        detected_language=(_resume_lang or delegated_detected_language or language),
                    )
                except Exception as e:
                    logger.warning("smart_clip resume hook 失败（不阻塞 graph resume）: %s", e)
                # 继续被中断的对话 - 使用 Command(resume=...)，并注入当前任务 run_id 到 state，确保事件写入新 run_id 的 Redis stream
                from langgraph.types import Command
                # 诊断：检查 checkpoint；若 failed_video 错误 resume 后已无 interrupt，走恢复重跑 video_generation
                _recovered_failed_video = False
                try:
                    _state = await router_agent.aget_state(config, subgraphs=True)
                    if _state:
                        logger.info(
                            "🔎 checkpoint BEFORE resume: thread=%s, next=%s, "
                            "tasks=%d, created_at=%s",
                            thread_id,
                            _state.next,
                            len(_state.tasks) if _state.tasks else 0,
                            getattr(_state, 'created_at', '?'),
                        )
                        _interrupt_steps = self._collect_interrupt_steps(_state)
                        _has_interrupt = bool(_interrupt_steps)
                        if _state.tasks:
                            for _t in _state.tasks[:3]:
                                _ints = getattr(_t, "interrupts", None) or []
                                logger.info(
                                    "🔎   task: id=%s, name=%s, interrupts=%s, error=%s",
                                    getattr(_t, 'id', '?'),
                                    getattr(_t, 'name', '?'),
                                    [i.value if hasattr(i, 'value') else str(i) for i in _ints][:2],
                                    getattr(_t, "error", None),
                                )
                                _sub = getattr(_t, "state", None)
                                if _sub is not None:
                                    logger.info(
                                        "🔎   subgraph next=%s interrupt_steps=%s",
                                        getattr(_sub, "next", None),
                                        self._collect_interrupt_steps(_sub)[:5],
                                    )
                        logger.info(
                            "🔎 interrupt_steps(all)=%s has_interrupt=%s thread=%s",
                            _interrupt_steps[:8],
                            _has_interrupt,
                            thread_id,
                        )
                        if not _state.next:
                            logger.warning(
                                "⚠️ state.next is EMPTY — graph is at END, nothing to resume! "
                                "thread=%s", thread_id,
                            )
                        # 有 after_shots / failed_* 等任何门控 interrupt 时禁止走恢复（否则会死循环）
                        if not _has_interrupt:
                            _recovered_failed_video = await self._try_recover_failed_video_resume(
                                router_agent, config, thread_id, run_id,
                            )
                    else:
                        logger.warning("⚠️ No state found for thread_id=%s", thread_id)
                except Exception as _e:
                    logger.warning("⚠️ checkpoint inspection failed: %s", _e)

                if _recovered_failed_video:
                    logger.info(
                        "🔁 astream FAILED_VIDEO RECOVER mode: thread_id=%s, run_id=%s (astream None)",
                        thread_id, run_id,
                    )
                    stream_generator = router_agent.astream(None, **astream_kwargs)
                else:
                    cmd = Command(resume=resume_data, update={"run_id": run_id}) if run_id else Command(resume=resume_data)
                    logger.info(
                        "🔄 astream RESUME mode: thread_id=%s, run_id=%s, resume_data=%s, cmd=%s",
                        thread_id, run_id, repr(resume_data)[:200], repr(cmd),
                    )
                    stream_generator = router_agent.astream(
                        cmd,
                        **astream_kwargs
                    )
            else:
                # 检测「硬取消后留下脏 checkpoint」的未完成运行：此时不从入口重头跑（否则会重新生成
                # outline 等、丢失已完成进度），而是把这条新消息注入 state 后用 astream(None) 续跑父图挂起节点；
                # 子图据其自身 checkpoint 续跑并 skip 已完成（事件 run_id 由 config.configurable.run_id 对齐当前任务）。
                cancel_snapshot = await self._detect_cancel_resume_snapshot(router_agent, config, thread_id)
                if cancel_snapshot is not None:
                    logger.info(
                        "🔁 resume-from-cancel: thread=%s 续跑被取消的未完成运行（注入新消息 + astream(None)），run_id=%s",
                        thread_id, run_id,
                    )
                    _vals = getattr(cancel_snapshot, "values", None) or {}
                    _detected_language = _vals.get("detected_language") or language or "en"
                    _agent_type_val = None
                    _sa = _vals.get("selected_agent")
                    if _sa is not None:
                        _agent_type_val = _sa.value if hasattr(_sa, "value") else str(_sa)
                    # 把用户这次输入作为 HumanMessage 注入 state（reducer 用 _current_run_id_cv 打上新 run_id），
                    # 供后续轮次 LLM context；并把 state.run_id 切到当前任务 run_id。aupdate_state 保留挂起任务。
                    try:
                        _human_text = (user_input_data.user_input or "").strip() if user_input_data else ""
                        _update: Dict[str, Any] = {}
                        if _human_text:
                            _update["messages"] = [HumanMessage(content=_human_text)]
                        if run_id:
                            _update["run_id"] = run_id
                        if _update:
                            await router_agent.aupdate_state(config, _update)
                    except Exception as _e_inject:
                        logger.warning("resume-from-cancel 注入新消息失败（不阻塞续跑）: %s", _e_inject)
                    # 持久化并展示用户这条消息（与正常流程的 USER_INPUT 对齐，便于刷新后仍在历史里）
                    try:
                        await self.async_send_event(
                            event_type=MessageType.USER_INPUT,
                            conversation_id=_conversation_id,
                            conversation_uuid=_conversation_uuid,
                            message=(user_input_data.user_input if user_input_data else None),
                            run_id=run_id,
                            extra_data={
                                "thread_id": _thread_id,
                                "run_id": run_id,
                                "user_id": user_id,
                                "detected_language": _detected_language,
                                **({"agent_type": _agent_type_val} if _agent_type_val else {}),
                            },
                            save_to_db=True,
                            send_to_stream=True,
                        )
                    except Exception as _e_evt:
                        logger.warning("resume-from-cancel 发送 USER_INPUT 失败（不阻塞续跑）: %s", _e_evt)
                    stream_generator = router_agent.astream(None, **astream_kwargs)
                else:
                    # 新的对话
                    stream_generator = router_agent.astream(initial_state, **astream_kwargs)
            
            # 处理流式事件
            # 注意：取消检查在 _execute_agent 中通过 cancelled_tasks 进行（Pub/Sub 通知）
            # 这里不需要额外的 Redis 状态检查，避免冗余查询
            stream_ended_by_interrupt = False
            _event_count = 0
            async for event in stream_generator:
                _event_count += 1
                if _event_count <= 5 or _event_count % 20 == 0:
                    _ns, _md, _dt = event
                    _dt_summary = type(_dt).__name__
                    if isinstance(_dt, dict):
                        _dt_summary = f"dict(keys={list(_dt.keys())[:5]})"
                    logger.info(
                        "📡 astream event #%d: namespace=%s, mode=%s, data=%s",
                        _event_count, _ns, _md, _dt_summary,
                    )
                namespace, mode, data = event
                
                # 处理 interrupt 事件
                if mode == "updates" and isinstance(data, dict) and "__interrupt__" in data:
                    interrupt_data = data["__interrupt__"]
                    # 提取 interrupt 的值
                    interrupt_value = interrupt_data[0].value if isinstance(interrupt_data, (list, tuple)) and len(interrupt_data) > 0 else interrupt_data
                    
                    # 通过 async_send_event 发送中断事件：写 DB + 写 Redis stream（event_data 里已含 message_id），status=interrupted 由 worker 在流结束时统一写；run_id 由 base_agent 写入 event_data
                    event_result = await self.async_send_event(
                        event_type=MessageType.INTERRUPT,
                        conversation_id=_conversation_id,
                        conversation_uuid=_conversation_uuid,
                        run_id=run_id,
                        extra_data={
                            "interrupt_data": interrupt_value,
                            "thread_id": _thread_id
                        },
                        save_to_db=True,
                        send_to_stream=True,
                        hidden=False
                    )
                    message_id = event_result.get("message_id") if event_result else None
                    # 同时 yield 给前端（SSE），与 Redis stream 一致带 message_id 作为 msgid
                    interrupt_event = {
                        "type": MessageType.INTERRUPT.value,
                        "interrupt_data": interrupt_value,
                        "thread_id": _thread_id,
                        "message_id": message_id,
                        "timestamp": utc_isoformat(datetime.utcnow())
                    }
                    yield f"data: {json.dumps(interrupt_event, ensure_ascii=False)}\n\n"
                    stream_ended_by_interrupt = True
                    break
                elif mode == "custom":
                    # 其他事件（custom 事件）
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            
            # 仅当流正常结束（非 interrupt 退出）时发送 STREAM_END，否则前端会误判为完成
            logger.info(
                "📊 astream loop done: total_events=%d, interrupted=%s, thread=%s, is_resume=%s",
                _event_count, stream_ended_by_interrupt, _thread_id, bool(resume_data),
            )
            if not stream_ended_by_interrupt:
                logger.info(f'🔚 Stream读取完成，发送结束事件: {_thread_id}')
                await self.async_send_event(
                    event_type=MessageType.STREAM_END,
                    conversation_id=_conversation_id,
                    conversation_uuid=_conversation_uuid,
                    run_id=run_id,
                    extra_data={"thread_id": _thread_id},
                    save_to_db=True,
                    send_to_stream=True,
                    hidden=True
                )
                end_event = {
                    'type': MessageType.STREAM_END.value,
                    'conversation_id': _conversation_id,
                    'thread_id': _thread_id,
                    'timestamp': utc_isoformat(datetime.utcnow())
                }
                yield f"data: {json.dumps(end_event, ensure_ascii=False)}\n\n"
            
        except asyncio.CancelledError:
            logger.info(f"Task {_thread_id} was cancelled")
            
            # 通过 async_send_event 发送取消事件（保存到DB和Redis）
            if async_db:
                try:
                    await self.async_send_event(
                        event_type=MessageType.CANCELLED,
                        conversation_id=_conversation_id,
                        conversation_uuid=_conversation_uuid,
                        run_id=run_id,
                        extra_data={"thread_id": _thread_id},
                        save_to_db=True,
                        send_to_stream=True
                    )
                except Exception as e:
                    logger.error(f"发送取消事件失败: {e}")
            
            # 同时 yield 给前端（SSE）
            cancel_event = {
                'type': MessageType.CANCELLED.value, 
                'conversation_id': _conversation_id,
                'thread_id': _thread_id,
                'timestamp': utc_isoformat(datetime.utcnow())
            }
            yield f"data: {json.dumps(cancel_event, ensure_ascii=False)}\n\n"
            # 不 raise，正常结束，让 _execute_agent 通过事件类型判断终态
            # 这样 process_task 可以统一处理所有终态，不需要额外的 except CancelledError
        except Exception as error:
            logger.error(f'智能对话流式处理失败: {error}', exc_info=True)

            # 用户可见文案走官方分类（不泄露厂商/模型/堆栈/原始报错）；原始信息仅留 extra_data 内部调试 + 日志
            safe_message = user_facing_reason(str(error))

            # 通过 async_send_event 发送错误事件（保存到DB和Redis）
            if async_db:
                try:
                    await self.async_send_event(
                        event_type=MessageType.ERROR,
                        conversation_id=_conversation_id,
                        conversation_uuid=_conversation_uuid,
                        run_id=run_id,
                        message=safe_message,
                        extra_data={"error_details": str(error), "thread_id": _thread_id},
                        save_to_db=True,
                        send_to_stream=True
                    )
                except Exception as e:
                    logger.error(f"发送错误事件失败: {e}")
            
            # 同时 yield 给前端（SSE）：message 为脱敏后的官方文案
            error_event = {
                'type': MessageType.ERROR.value,
                'conversation_id': _conversation_id,
                'thread_id': _thread_id,
                'timestamp': utc_isoformat(datetime.utcnow()),
                'message': safe_message
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            # 重新抛出异常，让调用者处理（Worker模式需要知道任务失败）
            raise
    

_agent_router_service_instance = None
def get_agent_router_service() -> AgentRouterService:
    """获取Agent路由器服务单例"""
    global _agent_router_service_instance
    if _agent_router_service_instance is None:
        _agent_router_service_instance = AgentRouterService()
    return _agent_router_service_instance


def get_agent_router() -> CompiledStateGraph:
    return get_agent_router_service().agent