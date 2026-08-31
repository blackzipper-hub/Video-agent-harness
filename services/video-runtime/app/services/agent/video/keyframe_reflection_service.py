"""
关键帧反思服务 - 分析关键帧的角色一致性并进行优化

功能：
1. 对每个关键帧独立进行 VLM 分析
2. VLM 判断是否需要重新生成（直接给出 true/false）
3. 如果需要重新生成，VLM 同时输出优化后的 scene_description
4. 立即为该镜头重新生成关键帧
5. 所有关键帧并发处理
"""
import logging
import asyncio
import json
import re
import uuid
from typing import List, Dict, Any, Optional, Tuple, Union, cast

from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from langgraph.runtime import Runtime
from sqlalchemy import select

from ....models.video_state import (
    VideoAgentState, KeyframeVersion, DetailedShot, CharacterProfile, CharacterImageInfo
)
from ....models.user_options import UserOption
from ....schemas.video.video_story import VideoDetailedShotDB
from ....schemas.video.video_keyframe import VideoKeyframeReflectionDB, VideoKeyframeReflectionResultDB
from ..schemas import VideoContextSchema
from ..base_agent import MessageType
from ..utils.database_utils import (
    get_keyframes_from_db,
    get_detailed_shots_from_db,
    get_characters_from_db,
    get_character_images_with_latest_versions,
    save_keyframe_to_db,
    _str_attr,
)
from .keyframe_generation_service import generate_batch_keyframes, _build_character_images_dict
from ....models.database import AsyncSessionLocal
from ....crud.video.video_keyframe import (
    create_keyframe_version,
    get_keyframe_versions_by_keyframe_ids,
    get_keyframe_versions_by_keyframe_id,
    update_keyframe_current_version_index,
)
from ....crud.video.video_character import get_character_versions_by_character_id
from app.schemas.video_llm import KeyframeReflectionVlmStructuredOutput
from ..utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient
from ..utils.cancellation import raise_if_cancelled

logger = logging.getLogger(__name__)


async def _update_keyframe_selected_to_regenerated(
    reflection_results: List["SingleKeyframeReflectionResult"],
    keyframes: List[Any],
) -> None:
    """Reflection 重新生成关键帧后，将对应 keyframe 的 selected version（current_version_index）更新为最新重生成的版本。
    仅用于 reflection 节点自动重生场景，与用户手动选版逻辑分离。"""
    shot_to_keyframe = {kf.shot_number: kf for kf in keyframes}
    for r in reflection_results:
        if not r.new_keyframe_uuid:
            continue
        kf = shot_to_keyframe.get(r.shot_number)
        if not kf or not getattr(kf, "keyframe_uuid", None):
            continue
        keyframe_uuid = kf.keyframe_uuid
        versions = await get_keyframe_versions_by_keyframe_id(keyframe_uuid)
        if not versions:
            continue
        new_version_index = None
        for idx, ver in enumerate(versions):
            if getattr(ver, "uuid", None) == r.new_keyframe_uuid:
                new_version_index = idx
                break
        if new_version_index is not None:
            await update_keyframe_current_version_index(keyframe_uuid, new_version_index)
            logger.info(f"✅ Reflection: 镜头{r.shot_number} 的 selected version 已更新为重生版本 index={new_version_index}")


# ==================== Reflection 数据模型 ====================

class CharacterConsistencyIssue(BaseModel):
    """角色一致性问题（LLM 可能返回 null 的 character_id/character_name，用 Optional+default 兼容）"""
    character_id: Optional[str] = Field(default="", description="角色ID")
    character_name: Optional[str] = Field(default="", description="角色名称")
    shot_number: int = Field(description="镜头编号")
    issue_type: str = Field(description="问题类型: face/hair/clothing/body/proportion/missing")
    description: str = Field(description="问题描述")
    severity: str = Field(description="严重程度: low/medium/high")
    suggestion: str = Field(default="", description="优化建议")


class ConsistencyAnalysisResult(BaseModel):
    """一致性分析结果"""
    overall_score: float = Field(description="整体一致性评分 (0-1)")
    needs_improvement: bool = Field(description="是否需要改进")
    issues: List[CharacterConsistencyIssue] = Field(default_factory=list, description="发现的问题列表")
    summary: str = Field(description="分析总结")


class ImprovedSceneDescription(BaseModel):
    """优化后的场景描述"""
    shot_number: int = Field(description="镜头编号")
    shot_uuid: str = Field(description="镜头UUID")
    original_description: str = Field(description="原始描述")
    improved_description: str = Field(description="优化后的描述")
    improvements: List[str] = Field(description="改进点列表")


class SingleKeyframeReflectionResult(BaseModel):
    """单个关键帧的反思结果"""
    shot_number: int = Field(description="镜头编号")
    shot_uuid: str = Field(description="镜头UUID")
    needs_regeneration: bool = Field(description="是否需要重新生成")
    issues: List[CharacterConsistencyIssue] = Field(default_factory=list, description="发现的问题")
    analysis_summary: str = Field(default="", description="分析总结")
    improved_description: str = Field(default="", description="优化后的描述")
    improvement_points: List[str] = Field(default_factory=list, description="改进点")
    new_keyframe_uuid: Optional[str] = Field(default=None, description="新生成的关键帧UUID")
    new_keyframe_url: Optional[str] = Field(default=None, description="新生成的关键帧URL")
    new_t2i_prompt: Optional[str] = Field(default=None, description="新生成时使用的t2i_prompt")
    vlm_raw_response: Optional[str] = Field(default=None, description="VLM原始响应")


class ReflectionResult(BaseModel):
    """反思结果"""
    analysis: ConsistencyAnalysisResult = Field(description="一致性分析结果")
    improved_shots: List[ImprovedSceneDescription] = Field(default_factory=list, description="优化后的镜头描述")
    iteration: int = Field(default=1, description="迭代次数")
    new_keyframe_uuids: List[str] = Field(default_factory=list, description="新生成的关键帧UUID")
    per_keyframe_results: List[SingleKeyframeReflectionResult] = Field(default_factory=list, description="每个关键帧的反思结果")


# ==================== 辅助函数 ====================

def parse_vlm_response(
    response_text: str,
    shot_number: int,
    shot_uuid: str
) -> SingleKeyframeReflectionResult:
    """解析 VLM 响应文本
    
    Args:
        response_text: VLM 返回的响应文本
        shot_number: 镜头编号
        shot_uuid: 镜头UUID
    
    Returns:
        SingleKeyframeReflectionResult
    """
    try:
        # 尝试提取 JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        json_str = json_match.group(1) if json_match else response_text
        
        # 清理可能的非 JSON 内容
        json_str = json_str.strip()
        if not json_str.startswith('{'):
            # 尝试找到第一个 { 开始的位置
            start_idx = json_str.find('{')
            if start_idx != -1:
                json_str = json_str[start_idx:]
        
        result_data = json.loads(json_str)
        
        # 解析问题列表（兼容 LLM 返回 null 的 character_id/character_name）
        issues = []
        for issue_data in result_data.get("issues", []):
            issue_data["shot_number"] = shot_number
            if issue_data.get("character_id") is None:
                issue_data["character_id"] = ""
            if issue_data.get("character_name") is None:
                issue_data["character_name"] = ""
            if "suggestion" not in issue_data:
                issue_data["suggestion"] = ""
            issues.append(CharacterConsistencyIssue(**issue_data))
        
        return SingleKeyframeReflectionResult(
            shot_number=shot_number,
            shot_uuid=shot_uuid,
            needs_regeneration=result_data.get("needs_regeneration", False),
            issues=issues,
            analysis_summary=result_data.get("analysis_summary", ""),
            improved_description=result_data.get("improved_description", ""),
            improvement_points=result_data.get("improvement_points", []),
            vlm_raw_response=response_text
        )
    except json.JSONDecodeError as e:
        logger.warning(f"⚠️ 解析 VLM 响应失败: {e}")
        return SingleKeyframeReflectionResult(
            shot_number=shot_number,
            shot_uuid=shot_uuid,
            needs_regeneration=False,
            analysis_summary=f"解析响应失败: {str(e)}",
            vlm_raw_response=response_text
        )
    except Exception as e:
        logger.error(f"⚠️ 处理 VLM 响应异常: {e}")
        return SingleKeyframeReflectionResult(
            shot_number=shot_number,
            shot_uuid=shot_uuid,
            needs_regeneration=False,
            analysis_summary=f"处理响应异常: {str(e)}",
            vlm_raw_response=response_text
        )


def _reflection_vlm_structured_to_single_result(
    structured: KeyframeReflectionVlmStructuredOutput,
    shot_number: int,
    shot_uuid: str,
    vlm_raw_response: Optional[str],
) -> SingleKeyframeReflectionResult:
    """将 llm_resilience 解析后的结构化结果转为 SingleKeyframeReflectionResult（issues 注入 shot_number）。"""
    issues: List[CharacterConsistencyIssue] = []
    for issue in structured.issues:
        issues.append(
            CharacterConsistencyIssue(
                character_id=issue.character_id or "",
                character_name=issue.character_name or "",
                shot_number=shot_number,
                issue_type=issue.issue_type,
                description=issue.description,
                severity=issue.severity,
                suggestion=issue.suggestion or "",
            )
        )
    return SingleKeyframeReflectionResult(
        shot_number=shot_number,
        shot_uuid=shot_uuid,
        needs_regeneration=structured.needs_regeneration,
        issues=issues,
        analysis_summary=structured.analysis_summary,
        improved_description=structured.improved_description,
        improvement_points=list(structured.improvement_points or []),
        vlm_raw_response=vlm_raw_response,
    )


# ==================== 核心函数 ====================

async def reflect_single_keyframe(
    keyframe: KeyframeVersion,
    shot: DetailedShot,
    characters: List[CharacterProfile],
    character_images: Dict[str, str],
    char_map: Dict[str, CharacterProfile],
    state: Dict[str, Any] = None,  # 调用方可能不传，内部 regenerate 会用到
    auto_regenerate: bool = True
) -> Tuple[SingleKeyframeReflectionResult, Optional[BaseMessage]]:
    """对单个关键帧进行反思：分析 → 判断是否需要重新生成 → 优化描述 → 重新生成
    
    返回 (反思结果, VLM 实际调用的 response message)，与 keyframe_generation 返回实际 LLM 调用结果一致。
    """
    shot_number = keyframe.shot_number
    shot_uuid = shot.uuid if shot else ""
    
    # 获取该镜头中的角色
    shot_characters = []
    shot_char_images = []
    if shot and shot.character_ids:
        for cid in shot.character_ids:
            if cid in char_map and cid in character_images:
                shot_characters.append(char_map[cid])
                shot_char_images.append((char_map[cid], character_images[cid]))
    
    # 如果没有角色或没有关键帧URL，跳过
    if not shot_characters or not keyframe.keyframe_url:
        return (
            SingleKeyframeReflectionResult(
                shot_number=shot_number,
                shot_uuid=shot_uuid,
                needs_regeneration=False,
                analysis_summary="该镜头无角色或无关键帧，跳过分析"
            ),
            None,
        )
    
    # 构建角色信息
    char_names = [c.name for c in shot_characters]
    character_info = "\n".join([
        f"- {c.name} (ID: {c.id}): {c.appearance}" 
        for c in shot_characters
    ])
    
    ref_images = [
        {"url": img_url, "index": i, "name": char.name, "id": char.id}
        for i, (char, img_url) in enumerate(shot_char_images)
    ]
    keyframe_url_value = keyframe.keyframe_url
    detected_language = state.get("detected_language") if state else None

    try:
        # Deep-agent path: include character ref images + keyframe for VLM parity.
        import uuid as _uuid
        from app.services.agent.video.keyframe_reflection_stage import (
            export_keyframe_reflection_inputs,
            generate_keyframe_reflection_via_deep_agent,
        )
        tid = f"kfr_{_uuid.uuid4().hex[:8]}"
        rid = f"run_{_uuid.uuid4().hex[:8]}"
        items = [{
            "shot_number": shot_number,
            "shot_uuid": shot_uuid,
            "scene_description": shot.scene_description if shot else "",
            "character_names": ", ".join(char_names),
            "character_info": character_info,
            "character_ref_images": ref_images,
            "keyframe_url": keyframe_url_value,
        }]
        _paths = export_keyframe_reflection_inputs(
            thread_id=tid, run_id=rid, batch_id=str(shot_number), items=items,
        )
        art, _msgs = await generate_keyframe_reflection_via_deep_agent(
            thread_id=tid, run_id=rid, input_paths=_paths, detected_language=detected_language,
        )
        r0 = art.results[0] if art.results else None
        if r0 is None:
            raise Exception("keyframe reflection artifact empty")
        # Artifact issues may be plain strings; VLM schema wants structured issues.
        from app.schemas.video_llm import KeyframeReflectionVlmIssue
        issues_struct = []
        for it in (r0.issues or []):
            if isinstance(it, str):
                issues_struct.append(KeyframeReflectionVlmIssue(
                    issue_type="consistency", description=it, severity="medium", suggestion="",
                ))
            elif isinstance(it, dict):
                issues_struct.append(KeyframeReflectionVlmIssue.model_validate(it))
        structured_parsed = KeyframeReflectionVlmStructuredOutput(
            needs_regeneration=r0.needs_regeneration,
            issues=issues_struct,
            analysis_summary=r0.analysis_summary or "",
            improved_description=r0.improved_description or "",
            improvement_points=list(r0.improvement_points or []),
        )
        response_text = json.dumps(structured_parsed.model_dump(), ensure_ascii=False)
        vlm_message = None
        parsed_result = _reflection_vlm_structured_to_single_result(
            structured_parsed, shot_number, shot_uuid, response_text
        )
        
        # 记录 VLM 反馈到日志
        logger.info(f"🤖 VLM 反馈 - 镜头 {shot_number}: 需要重新生成={parsed_result.needs_regeneration}")
        
        # 提取解析结果
        needs_regeneration = parsed_result.needs_regeneration
        issues = parsed_result.issues
        analysis_summary = parsed_result.analysis_summary
        improved_description = parsed_result.improved_description
        improvement_points = parsed_result.improvement_points
        
        # 如果需要重新生成且启用自动重新生成，执行重新生成
        new_keyframe_uuid = None
        new_keyframe_url = None
        new_t2i_prompt = None
        
        if needs_regeneration and improved_description and auto_regenerate:
            logger.info(f"🔄 镜头{shot_number} 需要重新生成...")
            
            # 更新数据库中的 scene_description（使用 asyncpg CRUD）
            try:
                from ....crud.video.video_story import update_shot_scene_description
                success = await update_shot_scene_description(shot_uuid, improved_description)
                if success:
                    logger.info(f"💾 镜头{shot_number} 描述已更新")
                    # 更新 shot 对象的描述（用于重新生成）
                    shot.scene_description = improved_description
            except Exception as e:
                logger.error(f"⚠️ 更新描述失败: {e}")
            
            # 重新生成关键帧并写入新版本（内部与 node 一致用 _build_character_images_dict 取 character_images）
            new_keyframe, new_version_uuid = await regenerate_single_keyframe(
                shot=shot, state=state, keyframe_id=keyframe.keyframe_uuid or ""
            )
            
            if new_keyframe and new_version_uuid:
                new_keyframe_uuid = new_version_uuid
                new_keyframe_url = new_keyframe.keyframe_url
                new_t2i_prompt = new_keyframe.t2i_prompt
                logger.info(f"✅ 镜头{shot_number} 重新生成成功: {new_keyframe_url}, version_uuid={new_version_uuid}")
            else:
                logger.warning(f"⚠️ 镜头{shot_number} 重新生成失败")
        
        return (
            SingleKeyframeReflectionResult(
                shot_number=shot_number,
                shot_uuid=shot_uuid,
                needs_regeneration=needs_regeneration,
                issues=issues,
                analysis_summary=analysis_summary,
                improved_description=improved_description,
                improvement_points=improvement_points,
                new_keyframe_uuid=new_keyframe_uuid,
                new_keyframe_url=new_keyframe_url,
                new_t2i_prompt=new_t2i_prompt,
                vlm_raw_response=response_text
            ),
            vlm_message,  # 实际 VLM 调用的返回，与 keyframe_generation 的 messages 一致
        )
        
    except Exception as e:
        logger.error(f"❌ 镜头{shot_number} 反思失败: {e}")
        return (
            SingleKeyframeReflectionResult(
                shot_number=shot_number,
                shot_uuid=shot_uuid,
                needs_regeneration=False,
                analysis_summary=f"分析出错: {str(e)}",
                vlm_raw_response=None
            ),
            None,
        )


async def regenerate_single_keyframe(
    shot: DetailedShot,
    state: Dict[str, Any],
    keyframe_id: str,
) -> Tuple[Optional[KeyframeVersion], Optional[str]]:
    """为单个镜头重新生成关键帧并写入新版本到数据库。
    
    内部与 node 一致用 _build_character_images_dict 取图，再走 generate_batch_keyframes；
    成功后插入新关键帧版本（create_keyframe_version），返回内存对象与真实版本 UUID。
    
    Args:
        shot: 分镜信息
        state: 当前状态（含 user_option、user_id、user_input_data、conversation_id、thread_id、run_id 等）
        keyframe_id: 父关键帧 UUID（将在此 keyframe 下新增一个 version）
    
    Returns:
        (新生成的 KeyframeVersion 对象, 新版本的 DB UUID)，失败返回 (None, None)
    """
    try:
        user_input_data = state.get("user_input_data")
        user_option = user_input_data.user_option if user_input_data else None
        if not user_option:
            user_option = UserOption()
        user_id = state.get("user_id") or ""

        # 与 keyframe_generation_node 一致：用 _build_character_images_dict 取 character_images（内含 profile）
        character_images = await _build_character_images_dict(set(shot.character_ids or []), user_id)

        detected_language = state.get("detected_language") if state else None

        # regenerate 复用：沿用父 keyframe 最新成功 version 的 reference_image_urls，跳过重新匹配。
        # 用户场景：编辑 prompt 后 regenerate 时不希望因模型能力/常量变更而丢图（典型是 location 图）。
        # 变更检测：仅当该成功版本的 character_version_ids == 当前选中版本时才复用；只要有元素（人物/物品/场所）
        # 换了版本/被增删 → 不复用，按当前选中版本重新匹配，避免复用旧角色/场所 ref。
        from .keyframe_generation_service import should_reuse_ref_image_urls
        reuse_urls: Optional[List[str]] = None
        if keyframe_id:
            try:
                existing_versions_for_reuse = await get_keyframe_versions_by_keyframe_ids([keyframe_id])
                # version_number 倒序找最近一条成功且 ref_urls 非空的
                for v in sorted(
                    existing_versions_for_reuse,
                    key=lambda x: int(getattr(x, "version_number", 0) or 0),
                    reverse=True,
                ):
                    if not getattr(v, "success", True):
                        continue
                    urls = getattr(v, "reference_image_urls", None)
                    if urls:
                        _stored_cvids = getattr(v, "character_version_ids", None)
                        if should_reuse_ref_image_urls(shot, character_images, _stored_cvids):
                            reuse_urls = list(urls)
                        else:
                            logger.info(
                                "🔄 元素版本变化，跳过复用旧 ref，按当前选中版本重新匹配：shot=%s stored_cvids=%s",
                                getattr(shot, "shot_number", None), _stored_cvids,
                            )
                        break
            except Exception as _reuse_e:
                logger.warning(f"regenerate_single_keyframe 复用 ref_image_urls 失败，将走重新匹配: {_reuse_e}")
                reuse_urls = None

        messages, keyframe_versions = await generate_batch_keyframes(
            shots_batch=[shot],
            character_images=character_images,
            prev_shot=None,
            next_shot=None,
            user_option=user_option,
            shared_semaphore=None,
            user_id=user_id,
            detected_language=detected_language,
            skip_consistency_check=True,
            reuse_ref_image_urls=reuse_urls,
        )
        
        if not keyframe_versions or len(keyframe_versions) == 0:
            return (None, None)
        kf = keyframe_versions[0]
        if not kf.success:
            return (None, None)

        logger.info(f"🎨 新关键帧URL: {kf.keyframe_url}")

        if not keyframe_id:
            logger.warning("keyframe_id 为空，跳过写入新版本")
            return (kf, None)

        # 写入新版本到数据库（与 video_agent_service 中 custom_prompt 再生路径一致）
        # 数据库列为 VARCHAR，确保传入 str（version_db 可能为 int 等）
        conversation_id = str(state.get("conversation_id") or "")
        thread_id = str(state.get("thread_id") or "")
        run_id = str(state.get("run_id") or "")
        
        existing_versions = await get_keyframe_versions_by_keyframe_ids([keyframe_id])
        version_number = len(existing_versions) + 1

        character_version_ids: List[str] = []
        if shot.character_ids:
            for char_id in shot.character_ids:
                ci = character_images.get(char_id)
                vid = getattr(ci, "version_id", None) if ci else None
                if vid:
                    character_version_ids.append(vid)

        additional_data: Dict[str, Any] = {}
        if getattr(kf, "seed", None) is not None:
            additional_data["seed"] = kf.seed
        if getattr(kf, "resolution", None):
            additional_data["resolution"] = kf.resolution

        new_version_uuid = await create_keyframe_version(
                keyframe_id=keyframe_id,
                version_number=version_number,
                shot_number=kf.shot_number,
                keyframe_url=kf.keyframe_url,
                t2i_prompt=kf.t2i_prompt,
                provider=getattr(kf, "provider", None) or "",
                is_bridge=kf.is_bridge,
                reference_image_urls=kf.reference_image_urls or [],
                success=kf.success,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                error_msg=kf.error_msg,
                raw_error_msg=getattr(kf, "raw_error_msg", None),
                audio_segment_ids=getattr(kf, "audio_segment_ids", None),
                ai_messages=getattr(kf, "ai_messages_json", None),
                character_version_ids=character_version_ids if character_version_ids else None,
                additional_data=additional_data if additional_data else None,
                aspect_ratio=_str_attr(kf, "aspect_ratio"),
                resolution=_str_attr(kf, "resolution"),
                seed=getattr(kf, "seed", None),
                model=getattr(kf, "model", None),
                image_generation_tool=_str_attr(kf, "image_generation_tool"),
                image_tool_metrics=getattr(kf, "image_tool_metrics", None),
                tool_duration_sec=getattr(kf, "tool_duration_sec", None),
                tool_cost=getattr(kf, "tool_cost", None),
            )
        return (kf, new_version_uuid)
        
    except Exception as e:
        logger.error(f"❌ 重新生成关键帧失败: {e}")
        return (None, None)


async def reflect_all_keyframes(
    keyframes: List[KeyframeVersion],
    shots: List[DetailedShot],
    characters: List[CharacterProfile],
    character_images: Dict[str, str],
    state: Dict[str, Any],
    max_concurrency: int = 10,
    auto_regenerate: bool = True
) -> Tuple[List[SingleKeyframeReflectionResult], ConsistencyAnalysisResult, List[BaseMessage]]:
    """对所有关键帧进行反思（方案C：VLM评审和重生使用独立信号量，互不阻塞）
    返回 (反思结果列表, 一致性分析, 实际 VLM 调用的 messages)，与 keyframe_generation 返回 messages 一致。
    架构：
    - vlm_semaphore: 控制VLM评审的并发（评审完立即释放，不等重生）
    - regen_semaphore: 控制重新生成的并发（独立于评审）
    - 每个keyframe: VLM评审(持有vlm_semaphore) → 释放 → 如需重生(持有regen_semaphore)
    
    Args:
        keyframes: 关键帧列表
        shots: 分镜列表
        characters: 角色列表
        character_images: 角色图片URL映射
        state: 当前状态
        max_concurrency: VLM评审最大并发数
        auto_regenerate: 是否自动重新生成
    
    Returns:
        (反思结果列表, 汇总的一致性分析结果)
    """
    from ..utils.prompt_utils import get_concurrency_limit, add_random_delay
    
    regen_concurrency = get_concurrency_limit("keyframe_reflection_regen")
    
    logger.info(f"🔄 开始关键帧反思（方案C：VLM评审与重生独立并发）...")
    logger.info(f"📊 共 {len(keyframes)} 个关键帧，VLM评审并发: {max_concurrency}，重生并发: {regen_concurrency}")
    
    # 构建映射
    shot_map = {shot.shot_number: shot for shot in shots}
    char_map = {char.id: char for char in characters}
    
    # ⭐ 方案C核心：两个独立信号量
    vlm_semaphore = asyncio.Semaphore(max_concurrency)      # VLM评审并发
    regen_semaphore = asyncio.Semaphore(regen_concurrency)   # 重生并发
    
    async def reflect_with_separated_semaphores(kf: KeyframeVersion) -> Tuple[SingleKeyframeReflectionResult, Optional[BaseMessage]]:
        shot = shot_map.get(kf.shot_number)
        if not shot:
            return (
                SingleKeyframeReflectionResult(
                    shot_number=kf.shot_number,
                    shot_uuid="",
                    needs_regeneration=False,
                    analysis_summary="未找到对应分镜"
                ),
                None,
            )
        
        # ====== 阶段1: VLM评审（只持有 vlm_semaphore）======
        async with vlm_semaphore:
            await add_random_delay()
            logger.info(f"🔍 开始反思镜头 {kf.shot_number}...")
            result, vlm_message = await reflect_single_keyframe(
                keyframe=kf,
                shot=shot,
                characters=characters,
                character_images=character_images,
                char_map=char_map,
                state=state,
                auto_regenerate=False  # ⭐ 关键：评审阶段不重生，释放slot后再重生
            )
        # ← vlm_semaphore 立即释放！不等重生
        
        # ====== 阶段2: 如需重生（持有 regen_semaphore，独立于VLM）======
        if result.needs_regeneration and result.improved_description and auto_regenerate:
            async with regen_semaphore:
                await add_random_delay()
                shot_number = kf.shot_number
                shot_uuid = shot.uuid if shot else ""
                logger.info(f"🔄 镜头{shot_number} 开始重新生成（独立并发）...")
                
                # 更新数据库中的 scene_description
                try:
                    from ....crud.video.video_story import update_shot_scene_description
                    success = await update_shot_scene_description(shot_uuid, result.improved_description)
                    if success:
                        logger.info(f"💾 镜头{shot_number} 描述已更新")
                        shot.scene_description = result.improved_description
                except Exception as e:
                    logger.error(f"⚠️ 更新描述失败: {e}")
                
                # 重新生成关键帧
                new_keyframe, new_version_uuid = await regenerate_single_keyframe(
                    shot=shot, state=state, keyframe_id=kf.keyframe_uuid or ""
                )
                
                if new_keyframe and new_version_uuid:
                    result.new_keyframe_uuid = new_version_uuid
                    result.new_keyframe_url = new_keyframe.keyframe_url
                    result.new_t2i_prompt = new_keyframe.t2i_prompt
                    logger.info(f"✅ 镜头{shot_number} 重新生成成功: {new_keyframe.keyframe_url}, version_uuid={new_version_uuid}")
                else:
                    logger.warning(f"⚠️ 镜头{shot_number} 重新生成失败")
        
        status = "✅" if not result.needs_regeneration else ("🔄" if result.new_keyframe_uuid else "⚠️")
        logger.info(f"{status} 镜头{kf.shot_number} 反思完成 - 需要重新生成: {result.needs_regeneration}")
        return (result, vlm_message)
    
    # 并发执行所有关键帧的反思（VLM评审完立即释放，重生独立并发）
    logger.info(f"🚀 开始并发反思（VLM与重生独立信号量）...")
    tasks = [reflect_with_separated_semaphores(kf) for kf in keyframes]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
    await raise_if_cancelled()
    
    # 处理结果：收集 result 与 实际 VLM 调用的 messages（与 keyframe_generation 一致）
    reflection_results: List[SingleKeyframeReflectionResult] = []
    all_issues: List[CharacterConsistencyIssue] = []
    all_messages: List[BaseMessage] = []
    regenerated_count = 0
    
    for item in results:
        if isinstance(item, BaseException):
            logger.error(f"⚠️ 反思任务异常: {item}")
        else:
            result, vlm_msg = item
            reflection_results.append(result)
            all_issues.extend(result.issues)
            if result.new_keyframe_uuid:
                regenerated_count += 1
            if vlm_msg is not None:
                all_messages.append(vlm_msg)
    
    # 生成汇总的一致性分析结果
    needs_improvement = any(r.needs_regeneration and not r.new_keyframe_uuid for r in reflection_results)
    overall_score = 1.0 - (len(all_issues) / max(len(keyframes), 1)) * 0.5
    
    summary = f"分析了 {len(keyframes)} 个关键帧，发现 {len(all_issues)} 个问题，重新生成了 {regenerated_count} 个关键帧。"
    
    analysis_result = ConsistencyAnalysisResult(
        overall_score=overall_score,
        needs_improvement=needs_improvement,
        issues=all_issues,
        summary=summary
    )
    
    logger.info(f"✅ 反思完成: {summary}")

    return reflection_results, analysis_result, all_messages


# ==================== 节点函数 ====================

async def keyframe_reflection_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """
    关键帧反思节点 - 分析关键帧的角色一致性并进行优化（节点内按需 load VLM；文本 LLM 在 reflect/regenerate 内按需 load）
    
    工作流程：
    1. 获取所有关键帧和对应的角色参考图
    2. 对每个关键帧独立进行 VLM 分析
    3. 如果需要重新生成，优化描述并重新生成
    4. 支持多轮迭代直到达到阈值或最大迭代次数
    """
    logger.info("🔄 开始关键帧反思节点...")

    from .music_generation_service import should_skip_keyframe_pipeline_from_state

    if should_skip_keyframe_pipeline_from_state(state):
        logger.info("⏭️ 跳过关键帧反思（shot_workflow_mode=reference_t2v）")
        return {}
    
    # 获取配置
    user_input_data = state.get("user_input_data")
    user_option = user_input_data.user_option if user_input_data else None
    
    # 从 user_option 获取 reflection 配置（如果有的话）
    max_iterations = getattr(user_option, 'max_reflection_iterations', 2) if user_option else 2
    reflection_threshold = getattr(user_option, 'reflection_threshold', 0.8) if user_option else 0.8
    enable_reflection = getattr(user_option, 'enable_keyframe_reflection', False) if user_option else False
    
    # ✅ 使用统一的并发配置（优先使用 user_option，否则使用 CONCURRENCY_LIMITS）
    from ..utils.prompt_utils import get_concurrency_limit
    default_concurrency = get_concurrency_limit("keyframe_reflection")
    max_concurrency = default_concurrency
    
    # 如果禁用 reflection，直接返回
    if not enable_reflection:
        logger.info("⏭️ 关键帧反思已禁用，跳过")
        return {}
    
    # ✅ 修复：按需创建数据库连接，不依赖 context
    from ....models.database import AsyncSessionLocal
    
    # 获取数据
    keyframe_uuids = state.get("keyframe_uuids", [])
    shot_uuids = state.get("shot_uuids", [])
    character_uuids = state.get("character_uuids", [])
    user_id = state["user_id"]
    conversation_id = state.get("conversation_id")
    
    if not keyframe_uuids:
        logger.warning("⚠️ 没有关键帧数据，跳过反思")
        return {}
    
    # 从数据库获取数据（使用asyncpg CRUD）
    keyframes = await get_keyframes_from_db(keyframe_uuids)
    shots = await get_detailed_shots_from_db(shot_uuids)
    characters = await get_characters_from_db(character_uuids)
    character_images = await get_character_images_with_latest_versions(character_uuids, user_id)
    
    logger.info(f"📊 数据: {len(keyframes)} 关键帧, {len(shots)} 镜头, {len(characters)} 角色")
    
    total_keyframes = len(keyframes)
    # 发送初始进度（与 keyframe_generation 一致：仅 PROGRESS + COMPLETED 两个事件）
    if send_event_func and conversation_id:
        await send_event_func(
            event_type=MessageType.KEYFRAME_REFLECTION_PROGRESS,
            conversation_id=conversation_id,
            extra_data={"completed": 0, "total": total_keyframes},
            hidden=True,
            save_to_db=False,
        )
    
    # 迭代反思（VLM 与文本 LLM 均在 reflect_single_keyframe / regenerate_single_keyframe 内按需 load）
    current_iteration = 0
    current_keyframe_uuids = list(keyframe_uuids)
    all_reflection_results = []
    reflection_messages: List[BaseMessage] = []  # 实际 VLM 调用的 returns，与 keyframe_generation 的 all_messages 一致
    
    while current_iteration < max_iterations:
        current_iteration += 1
        logger.info(f"🔄 Reflection 迭代 {current_iteration}/{max_iterations}")
        
        # 获取当前关键帧（使用asyncpg CRUD）
        keyframes = await get_keyframes_from_db(current_keyframe_uuids)
        
        # 执行反思（返回 结果、分析、实际 LLM 调用的 messages）
        reflection_results, analysis, reflection_messages = await reflect_all_keyframes(
            keyframes=keyframes,
            shots=shots,
            characters=characters,
            character_images=character_images,
            state=state,
            max_concurrency=max_concurrency,
            auto_regenerate=True
        )
        
        # 保存反思记录到数据库（使用asyncpg CRUD）
        await _save_reflection_to_db(
            state=state,
            iteration=current_iteration,
            analysis=analysis,
            reflection_results=reflection_results,
            keyframe_uuids=current_keyframe_uuids,
            keyframes=keyframes,
        )
        
        all_reflection_results.append({
            "iteration": current_iteration,
            "analysis": analysis,
            "per_keyframe_results": reflection_results
        })
        
        logger.info(f"📈 迭代 {current_iteration} 完成:")
        logger.info(f"    - 总关键帧数: {len(keyframes)}")
        logger.info(f"    - 发现问题数: {len(analysis.issues)}")
        logger.info(f"    - 整体评分: {analysis.overall_score}")
        
        # 发送进度事件（参考 keyframe_generation 每批完成后上报）
        if send_event_func and conversation_id:
            await send_event_func(
                event_type=MessageType.KEYFRAME_REFLECTION_PROGRESS,
                conversation_id=conversation_id,
                extra_data={"completed": len(keyframes), "total": total_keyframes},
                hidden=True,
                save_to_db=False,
            )
        
        # 检查是否需要继续迭代
        if analysis.overall_score >= reflection_threshold:
            logger.info(f"✅ 一致性评分 {analysis.overall_score} >= {reflection_threshold}，停止迭代")
            break
        
        # 检查是否有需要改进但未成功重新生成的
        needs_more_iteration = any(r.needs_regeneration and not r.new_keyframe_uuid for r in reflection_results)
        if not needs_more_iteration:
            logger.info("✅ 所有需要重新生成的关键帧都已完成，停止迭代")
            break
        
        # 更新关键帧 UUID 列表（替换已重新生成的）
        shot_to_new_uuid = {}
        for r in reflection_results:
            if r.new_keyframe_uuid:
                shot_to_new_uuid[r.shot_number] = r.new_keyframe_uuid
        
        if shot_to_new_uuid:
            updated_keyframe_uuids = []
            for kf in keyframes:
                if kf.shot_number in shot_to_new_uuid:
                    updated_keyframe_uuids.append(shot_to_new_uuid[kf.shot_number])
                else:
                    updated_keyframe_uuids.append(kf.version_id)
            current_keyframe_uuids = updated_keyframe_uuids
            logger.info(f"🔄 已替换 {len(shot_to_new_uuid)} 个关键帧 UUID")
            await _update_keyframe_selected_to_regenerated(reflection_results, keyframes)
    
    # 发送完成事件：用实际 LLM 调用的 messages（reflect_all_keyframes 返回），直接拿 generate_completion_message_stream 返回的 user_message
    final_analysis = all_reflection_results[-1]["analysis"] if all_reflection_results else None
    from ....services.agent.utils.prompt_utils import generate_completion_message_stream
    detected_language = state.get("detected_language")
    user_message, completion_message = await generate_completion_message_stream(
        event_type=MessageType.KEYFRAMES_REFLECTION_COMPLETED,
        messages=reflection_messages,
        send_event_func=send_event_func,
        conversation_id=conversation_id,
        lang=detected_language,
    )
    if send_event_func and conversation_id:
        await send_event_func(
            conversation_id=conversation_id,
            event_type=MessageType.KEYFRAMES_REFLECTION_COMPLETED,
            message=user_message,
            extra_data={
                "iterations": current_iteration,
                "final_score": final_analysis.overall_score if final_analysis else 1.0,
                "total_issues": len(final_analysis.issues) if final_analysis else 0,
                "keyframe_uuids": current_keyframe_uuids,
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id"),
            },
        )
    logger.info(f"📝 反思完成消息(LLM): {user_message}")
    logger.info(f"🏁 关键帧反思节点完成，共 {current_iteration} 轮迭代")
    return {
        "keyframe_uuids": current_keyframe_uuids,
        "reflection_iteration": current_iteration,
        "reflection_results": [r for r in all_reflection_results],
        "reflection_message": user_message,
    }


async def _save_reflection_to_db(
    state: VideoAgentState,
    iteration: int,
    analysis: ConsistencyAnalysisResult,
    reflection_results: List[SingleKeyframeReflectionResult],
    keyframe_uuids: List[str],
    keyframes: Optional[List[Any]] = None,
) -> str:
    """保存反思记录到数据库（使用asyncpg CRUD）
    
    Args:
        state: 当前状态
        iteration: 当前迭代次数
        analysis: 一致性分析结果
        reflection_results: 反思结果列表（与 keyframe_uuids 顺序一致）
        keyframe_uuids: 关键帧版本 UUID 列表（与 reflection_results 一一对应）
        keyframes: 关键帧主记录列表（可选，用于填 keyframe_id）
    
    Returns:
        反思记录UUID
    """
    from ....crud.video.video_keyframe import create_keyframe_reflection, create_keyframe_reflection_result
    
    # 创建反思主记录
    reflection_uuid = await create_keyframe_reflection(
        conversation_id=str(state.get("conversation_id", "")),
        thread_id=str(state.get("thread_id", "")),
        run_id=state.get("run_id", ""),
        user_id=state["user_id"],
        story_outline_id=state.get("story_outline_uuid", ""),
        iteration_number=iteration,
        total_keyframes=len(keyframe_uuids),
        analyzed_keyframes=len(reflection_results),
        regenerated_keyframes=sum(1 for r in reflection_results if r.new_keyframe_uuid),
        consistency_score=analysis.overall_score,
        analysis_duration_seconds=0.0,
        regeneration_duration_seconds=0.0,
        additional_data={
            "summary": analysis.summary,
            "needs_improvement": analysis.needs_improvement
        }
    )
    
    # 创建每个关键帧的反思结果记录（reflection_results[i] 与 keyframes[i] 一一对应）
    # keyframe_version_id 必须用版本 UUID (video_keyframe_versions.uuid)，接口按 version.uuid 查询。
    for i, result in enumerate(reflection_results):
        if keyframes and i < len(keyframes):
            kf = keyframes[i]
            keyframe_version_id = kf.version_id
            keyframe_id = kf.keyframe_uuid or ""
        else:
            keyframe_version_id = keyframe_uuids[i] if i < len(keyframe_uuids) else ""
            keyframe_id = ""
        
        # 处理 issues - 如果有 model_dump 方法则调用
        issues_data = None
        if result.issues:
            issues_data = [issue.model_dump() if hasattr(issue, 'model_dump') else issue for issue in result.issues]
        
        await create_keyframe_reflection_result(
            reflection_id=reflection_uuid,
            keyframe_id=keyframe_id,
            keyframe_version_id=keyframe_version_id,
            conversation_id=str(state.get("conversation_id", "")),
            thread_id=str(state.get("thread_id", "")),
            run_id=state.get("run_id", ""),
            user_id=state["user_id"],
            shot_number=result.shot_number,
            shot_uuid=result.shot_uuid,
            needs_regeneration=result.needs_regeneration,
            issues=issues_data,
            analysis_summary=result.analysis_summary,
            improvement_points=result.improvement_points if result.improvement_points else None,
            original_description=None,
            improved_description=result.improved_description if result.improved_description else None,
            new_keyframe_version_id=result.new_keyframe_uuid,
            regeneration_success=result.new_keyframe_uuid is not None if result.needs_regeneration else None,
            regeneration_error=None,
            additional_data={
                "vlm_raw_response": result.vlm_raw_response
            } if result.vlm_raw_response else None
        )
    
    logger.info(f"💾 已保存反思记录到数据库: {reflection_uuid}")
    return reflection_uuid
