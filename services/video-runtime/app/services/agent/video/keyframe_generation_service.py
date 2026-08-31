"""
关键帧生成功能模块
负责关键帧生成节点的实现和相关功能
"""
import dataclasses
import json
import logging
import os
import asyncio
import msgspec
import msgspec.structs
from enum import Enum
from typing import List, Optional, Dict, Any, Union, cast, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from datetime import datetime
from pydantic import BaseModel, Field


def _shot_to_dict(shot: Any) -> Optional[Dict[str, Any]]:
    """将 DetailedShot (Pydantic BaseModel) 或 msgspec.Struct 转为 dict。仅支持这两种类型。"""
    if shot is None:
        return None
    if hasattr(shot, "model_dump"):
        return shot.model_dump()
    if hasattr(shot, "__struct_fields__"):
        return msgspec.structs.asdict(shot)
    raise TypeError(f"shot 须为 Pydantic 模型或 msgspec.Struct，当前为 {type(shot)}")


from .regenerate.regenerate_context import forced_character_version_uuids_cv
from ..utils.cancellation import raise_if_cancelled


# 图片类型枚举
class ImageType(str, Enum):
    """图片类型枚举"""
    MAIN = "main"  # 主图
    MAIN_FUSION = "main_fusion"  # 主图融合图
    MULTIVIEW = "multiview"  # 多视角图
    MULTIVIEW_FUSION = "multiview_fusion"  # 多视角融合图
    FIRST_FRAME = "first_frame"  # 首帧图片


# ---------------------------------------------------------------------------
# 参考图结构化信息
# ---------------------------------------------------------------------------
class RefImageRole(str, Enum):
    """参考图角色类型"""
    CHARACTER = "character"
    SHEET = "sheet"
    FIRST_FRAME = "first_frame"


class RefImageInfo(BaseModel):
    """单张参考图的结构化信息"""
    url: str = Field(description="图片 URL（干净，无内部 tag）")
    role: RefImageRole = Field(description="图片角色：角色参考图 / Reference Sheet / 首帧")
    label: Optional[str] = Field(default=None, description="标签（角色名、'Reference Sheet (溢出角色)' 等）")


# ---------------------------------------------------------------------------
# 关键帧 prompt 结果
# ---------------------------------------------------------------------------
class KeyframePromptResult(BaseModel):
    """单个关键帧 prompt 生成结果"""
    shot_number: int = Field(description="镜头编号")
    t2i_prompt: str = Field(description="生成的关键帧提示词（使用本镜头内 image 1, image 2 等引用）")
    frame_index: int = Field(default=0, description="帧索引：0=首帧, -1=尾帧")

    ref_images: List[RefImageInfo] = Field(
        default_factory=list,
        description="本镜头参考图列表（含角色图、拼接表、首帧等），顺序与 prompt 中 image 1, image 2 对应"
    )

    final_prompt: Optional[str] = Field(
        default=None,
        description="最终执行的 prompt（resolve 阶段设置，可能含 Reference Sheet 引导语前缀）"
    )

    user_regenerate_instruction: Optional[str] = Field(
        default=None,
        description="再生时用户自然语言修改说明；与 t2i_prompt 在 tool execution 模板中分开展示",
    )

    # -- computed properties --------------------------------------------------
    @property
    def has_reference_sheet(self) -> bool:
        return any(img.role == RefImageRole.SHEET for img in self.ref_images)

    @property
    def sheet_index(self) -> Optional[int]:
        for i, img in enumerate(self.ref_images):
            if img.role == RefImageRole.SHEET:
                return i
        return None

    @property
    def all_ref_urls(self) -> List[str]:
        """所有参考图 URL（传给模型）"""
        return [img.url for img in self.ref_images]

    @property
    def display_ref_urls(self) -> List[str]:
        """展示给前端的参考图 URL（排除 Reference Sheet）"""
        return [img.url for img in self.ref_images if img.role != RefImageRole.SHEET]


class BatchKeyframePromptResult(BaseModel):
    """批量关键帧prompt生成结果包装类"""
    prompts: List[KeyframePromptResult] = Field(description="批量关键帧prompt生成结果列表")


# 仅用于 LLM 结构化输出的精简 schema（不含 ref_images、final_prompt 等后端填充字段）
class KeyframePromptResultForLLM(BaseModel):
    """单个关键帧 prompt 的 LLM 输出（仅 shot_number + t2i_prompt）"""
    shot_number: int = Field(description="镜头编号")
    t2i_prompt: str = Field(description="生成的关键帧提示词，须使用本镜头内 image 1, image 2 等引用参考图")


class BatchKeyframePromptResultForLLM(BaseModel):
    """批量关键帧 prompt 的 LLM 输出（仅 prompts 列表，供 response_format 使用）"""
    prompts: List[KeyframePromptResultForLLM] = Field(description="每个镜头的关键帧提示词")

    def resilience_empty_reason(self) -> Optional[str]:
        # 被 llm_resilience._wrap_invoke_with_result_check 自动调用：返回非空字符串触发 EMPTY_RESPONSE 桶。
        # 输入 N 个 shot 必须每个都产出 prompt；prompts 空 = 该批 keyframe 无法生成。
        # 单条空 t2i_prompt 由调用方逐镜补齐，这里只关心整体是否为空。
        if not self.prompts:
            return "prompts is empty"
        return None

from ....models.video_state import VideoAgentState, StoryOutline, CharacterProfile, KeyframeVersion, DetailedShot, CharacterImageInfo, ContentCategory, VisualElementType
from ....models.user_options import UserOption, ImageGenerationTool, DEFAULT_IMAGE_TOOL
from ....exceptions import BusinessException, BusinessExceptionCode
from ....crud.video.video_keyframe import create_keyframe, create_keyframe_version
from ....crud.video.video_character import get_character_versions_batch
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from .stage_failure import detect_stage_failure, emit_stage_failure_event
from ....utils.error_classification import classify_failure
from ....services.agent.utils.prompt_utils import (
    apply_language_suffix_to_system_message_in_messages,
    log_video_spin_prompt_trace,
)
from ....services.agent.utils.database_utils import get_story_outline_from_db, get_characters_from_db, get_detailed_shots_from_db, save_keyframe_to_db, get_completed_keyframe_uuids_by_shot
from ....services.tool_service import ToolService, _USER_OPTION_TO_TOOL_TYPE
from ..utils.message_utils import extract_ai_message_json, patch_tool_metrics_from_last_tool_message
from .character_fusion_service import get_model_limit
from ....tools.image.ref_utils import supports_venue_ref_image

logger = logging.getLogger(__name__)

# Reference Sheet 相关标记：当 get_character_ref_images 返回的 URL 列表中包含拼接图时，
# 该 URL 会以此前缀标记，供下游 prompt 构建识别并添加引导语。
REFERENCE_SHEET_URL_TAG = "__ref_sheet__"


def resolve_use_venue_ref_image(
    user_option: Optional[UserOption],
    reuse_ref_image_urls: Optional[List[str]] = None,
) -> bool:
    """是否在关键帧生成中喂入 location(场所) 参考图。
    优先级：
    0) regenerate 复用模式（reuse_ref_image_urls 非 None）→ 强制 True，让 character labels / 槽位与复用的 ref_urls 数量对齐
    1) user_option.use_venue_ref_image_in_keyframe 显式设值（True/False）→ 直接使用
    2) 否则按所选图片模型能力查 `supports_venue_ref_image(tool_type)`（gpt_image_2 → True，其它 → False）
    3) 任何异常 / 无 tool_type → False（保守兜底）

    与原 KEYFRAME_USE_VENUE_REF_IMAGE 全局常量的差异：常量是"一刀切 False"，新逻辑是"按模型能力"。
    新增图像模型必须在 ref_utils._SUPPORTS_VENUE_REF_IMAGE_BY_MODEL 中显式登记。"""
    if reuse_ref_image_urls is not None:
        return True
    explicit = getattr(user_option, "use_venue_ref_image_in_keyframe", None) if user_option else None
    if explicit is not None:
        return bool(explicit)
    img_tool = getattr(user_option, "image_generation_tool", None) if user_option else None
    if img_tool == ImageGenerationTool.AUTO or img_tool is None:
        img_tool = DEFAULT_IMAGE_TOOL
    tool_type = _USER_OPTION_TO_TOOL_TYPE.get(img_tool)
    return supports_venue_ref_image(tool_type)


async def create_and_upload_reference_sheet(
    image_urls: List[str],
    labels: Optional[List[str]] = None,
    max_cols: int = 3,
    cell_size: Tuple[int, int] = (512, 512),
    padding: int = 12,
) -> Optional[str]:
    """将多张参考图拼成 grid Reference Sheet 并上传到 S3，返回 CDN URL。
    
    纯 PIL 操作，CPU 开销极低（~50ms for 6 images）。
    """
    import math
    import tempfile
    import os
    from io import BytesIO

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("PIL 未安装，无法生成 Reference Sheet")
        return None

    from ....utils.s3_utils import s3_utils
    import uuid as _uuid

    pil_images: List[Image.Image] = []
    for url in image_urls:
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
            tmp.close()
            ok = await s3_utils.download_file(url, tmp.name)
            if ok:
                img = Image.open(tmp.name).convert("RGB")
                pil_images.append(img)
            os.unlink(tmp.name)
        except Exception as e:
            logger.warning(f"Reference Sheet: 下载图片失败 {url}: {e}")

    if len(pil_images) < 2:
        return None

    n = len(pil_images)
    cols = min(n, max_cols)
    rows = math.ceil(n / cols)
    label_h = 28 if labels else 0

    canvas_w = cols * cell_size[0] + (cols + 1) * padding
    canvas_h = rows * (cell_size[1] + label_h) + (rows + 1) * padding
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    font = ImageFont.load_default()
    for _font_path in [
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            font = ImageFont.truetype(_font_path, 16)
            break
        except Exception:
            continue

    for idx, img in enumerate(pil_images):
        col = idx % cols
        row = idx // cols
        x0 = padding + col * (cell_size[0] + padding)
        y0 = padding + row * (cell_size[1] + label_h + padding)

        img_ratio = img.width / img.height
        cell_ratio = cell_size[0] / cell_size[1]
        if img_ratio > cell_ratio:
            new_w = cell_size[0]
            new_h = int(cell_size[0] / img_ratio)
        else:
            new_h = cell_size[1]
            new_w = int(cell_size[1] * img_ratio)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        offset_x = x0 + (cell_size[0] - new_w) // 2
        offset_y = y0 + (cell_size[1] - new_h) // 2
        canvas.paste(resized, (offset_x, offset_y))

        if labels and idx < len(labels):
            text_x = x0 + cell_size[0] // 2
            text_y = y0 + cell_size[1] + 4
            draw.text((text_x, text_y), labels[idx], fill=(40, 40, 40), font=font, anchor="mt")

    buf = BytesIO()
    canvas.save(buf, format="WEBP", quality=90)
    data = buf.getvalue()
    key = f"images/ref-sheet-{_uuid.uuid4()}.webp"
    try:
        cdn_url = await s3_utils.upload_file(data, key, content_type="image/webp")
        logger.info(f"📋 Reference Sheet 已上传: {cdn_url} ({canvas_w}x{canvas_h}, {n} 张图)")
        return cdn_url
    except Exception as e:
        logger.error(f"Reference Sheet 上传失败: {e}")
        return None


def _keyframe_input_to_jsonable(obj: Any) -> Any:
    """将关键帧相关输入转为可 JSON 序列化的结构，便于打日志和后续 mock 复现"""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_keyframe_input_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _keyframe_input_to_jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        return _keyframe_input_to_jsonable(obj.model_dump())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _keyframe_input_to_jsonable(dataclasses.asdict(obj))
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)

# 常量配置
KEYFRAME_PROMPT_LENGTH_RANGE = "400-500"  # 关键帧提示词字数要求

def strip_reference_sheet_tag(url: str) -> Tuple[str, bool]:
    """Strip REFERENCE_SHEET_URL_TAG prefix from URL, return (clean_url, is_reference_sheet)."""
    if url.startswith(REFERENCE_SHEET_URL_TAG):
        return url[len(REFERENCE_SHEET_URL_TAG):], True
    return url, False


def process_ref_urls_with_sheet_detection(urls: List[str]) -> Tuple[List[str], bool, Optional[int]]:
    """Process a list of ref URLs: strip tags and detect if any is a reference sheet.
    
    Returns:
        (clean_urls, has_reference_sheet, sheet_index)
        sheet_index: position of the reference sheet in clean_urls (None if no sheet)
    """
    clean_urls = []
    has_sheet = False
    sheet_index: Optional[int] = None
    for i, url in enumerate(urls):
        clean, is_sheet = strip_reference_sheet_tag(url)
        clean_urls.append(clean)
        if is_sheet:
            has_sheet = True
            sheet_index = i
    return clean_urls, has_sheet, sheet_index




async def get_character_ref_images(
    shot: DetailedShot,
    character_images: Dict[str, CharacterImageInfo],
    db: Optional[Any] = None,
    user_id: Optional[str] = None,
    model_limit: int = 3,
    pre_fetched_fusions: Optional[List[Any]] = None,
    use_venue_ref_image: Optional[bool] = None,
) -> List[str]:
    """
    获取角色参考图片，智能使用融合图

    Args:
        shot: 镜头信息
        character_images: 角色图片字典（Dict[str, CharacterImageInfo]）
        db: 数据库session（可选，已废弃，保留兼容）
        user_id: 用户ID（当未提供 pre_fetched_fusions 时用于查询融合图）
        model_limit: 模型限制（nano=3, pro=5）
        pre_fetched_fusions: 可选，keyframe 入口处一次性拉取的融合图列表，避免每 shot/batch 重复查库
        use_venue_ref_image: 是否包含场所(location)的参考图。None=按保守兜底 False；调用方应预先用 resolve_use_venue_ref_image(user_option) 解析后显式传入。
    Returns:
        图片URL列表（同一 URL 可重复，多角色共享一图时保留顺序与数量）
    """
    if use_venue_ref_image is None:
        use_venue_ref_image = False
    _input = {
        "shot_number": shot.shot_number,
        "character_ids": shot.character_ids,
        "model_limit": model_limit,
        "pre_fetched_fusions_count": len(pre_fetched_fusions) if pre_fetched_fusions else 0,
        "character_images_keys": list(character_images.keys()),
        "character_images_summary": {
            cid: {
                "main_image_url": (ci.main_image_url[:80] + "..." if (getattr(ci, "main_image_url", None) and len(ci.main_image_url or "") > 80) else getattr(ci, "main_image_url", None)),
                "has_multiview": bool(getattr(ci, "multiview_url", None)),
                "has_profile": bool(getattr(ci, "profile", None)),
            }
            for cid, ci in character_images.items()
        },
    }
    try:
        logger.info("[keyframe_input] get_character_ref_images input: %s", json.dumps(_keyframe_input_to_jsonable(_input), ensure_ascii=False, default=str))
    except Exception as _e:
        logger.debug("keyframe_input get_character_ref_images json dump skip: %s", _e)

    if not shot.character_ids:
        return []
    
    character_count = len(shot.character_ids)
    shot_char_set = set(shot.character_ids)

    # 如果角色数量超过限制 且 融合图开关打开，尝试使用 DB 中已有的融合图
    from .agent_video_constants import ENABLE_FUSION
    if ENABLE_FUSION and character_count > model_limit and user_id:
        try:
            from .character_fusion_service import split_characters_by_model_limit

            # 按模型能力分组，查找对应的融合图
            groups = split_characters_by_model_limit(shot.character_ids, model_limit)

            # 使用预取列表或按 shot 查一次（keyframe 入口处建议传 pre_fetched_fusions 避免每 batch 重复查）
            if pre_fetched_fusions is not None:
                all_fusions = [f for f in pre_fetched_fusions if getattr(f, "character_ids", None) and set(f.character_ids) & shot_char_set]
            else:
                from ....crud.video.video_character import get_character_fusion_images_by_character_ids
                all_fusions = await get_character_fusion_images_by_character_ids(character_ids=shot.character_ids)
            
            # 尝试为每个组找到对应的融合图
            fusion_images = []
            remaining_char_ids = list(shot.character_ids)
            
            for group in groups:
                if len(group) >= 2:  # 至少2个角色才可能有融合图
                    # 查找完全匹配的融合图
                    matching_fusion = None
                    for fusion in all_fusions:
                        if fusion.character_ids and set(group) == set(fusion.character_ids):
                            if fusion.fusion_image_url:
                                matching_fusion = fusion
                                break
                    
                    if matching_fusion:
                        fusion_images.append(matching_fusion.fusion_image_url)
                        # 从remaining中移除已融合的角色
                        for char_id in group:
                            if char_id in remaining_char_ids:
                                remaining_char_ids.remove(char_id)
                        logger.info(f"🎯 镜头{shot.shot_number}: 找到融合图（{len(group)}个角色）")
            
            # 如果找到了融合图，返回融合图 + 剩余角色的单独图片
            if fusion_images:
                # 添加剩余角色的单独图片（不含场所图时跳过 type=location）
                for char_id in remaining_char_ids:
                    if char_id in character_images:
                        char_info = character_images[char_id]
                        if isinstance(char_info, CharacterImageInfo) and getattr(char_info, "profile", None):
                            if getattr(char_info.profile, "type", "character") == "location" and not use_venue_ref_image:
                                continue
                        if isinstance(char_info, CharacterImageInfo):
                            image_url = char_info.get_image_for_keyframe(prefer_multiview=False)
                        elif isinstance(char_info, str):
                            image_url = char_info
                        else:
                            image_url = None
                        if image_url:
                            fusion_images.append(image_url)
                
                logger.info(f"🎯 镜头{shot.shot_number}: 使用融合图策略，共{len(fusion_images)}张图片（融合图+单独图片）")
                try:
                    logger.info("[keyframe_input] get_character_ref_images output: shot_number=%s urls_count=%s urls=%s", shot.shot_number, len(fusion_images), _keyframe_input_to_jsonable(fusion_images))
                except Exception:
                    pass
                return fusion_images
        except Exception as e:
            logger.warning(f"获取融合图失败，降级到 Reference Sheet: {e}")

    # 收集所有原始参考图（按 character_ids 顺序，已排好 character > object > location）
    # use_venue_ref_image=False 时不加入 type=location 的图
    # ENABLE_FUSION=False 时直接取 main_image_url，跳过 get_image_for_keyframe 中的融合图优先逻辑
    all_ref_images = []
    all_ref_labels = []
    for char_id in shot.character_ids:
        char_info = character_images.get(char_id)
        if not char_info:
            continue
        if isinstance(char_info, CharacterImageInfo) and getattr(char_info, "profile", None):
            if getattr(char_info.profile, "type", "character") == "location" and not use_venue_ref_image:
                continue
        if isinstance(char_info, CharacterImageInfo):
            if ENABLE_FUSION:
                image_url = char_info.get_image_for_keyframe(prefer_multiview=False)
            else:
                image_url = char_info.main_image_url or char_info.multiview_url
        elif isinstance(char_info, str):
            image_url = char_info
        else:
            image_url = None
        if image_url:
            all_ref_images.append(image_url)
            label = getattr(getattr(char_info, "profile", None), "name", None) if isinstance(char_info, CharacterImageInfo) else None
            all_ref_labels.append(label or char_id[:8])

    # 超限时：前 (model_limit-1) 个保留独立图，溢出部分拼成 Reference Sheet 占最后 1 槽
    if character_count > model_limit and len(all_ref_images) > model_limit:
        keep_count = model_limit - 1  # 留 1 个槽给 sheet
        individual_urls = all_ref_images[:keep_count]
        overflow_urls = all_ref_images[keep_count:]
        overflow_labels = all_ref_labels[keep_count:]

        try:
            sheet_url = await create_and_upload_reference_sheet(
                image_urls=overflow_urls,
                labels=overflow_labels,
            )
            if sheet_url:
                tagged_url = REFERENCE_SHEET_URL_TAG + sheet_url
                result_urls = individual_urls + [tagged_url]
                overflow_names = ", ".join(overflow_labels)
                logger.info(
                    f"📋 镜头{shot.shot_number}: {keep_count} 张独立图 + 1 张 Reference Sheet"
                    f"（溢出 {len(overflow_urls)} 个: {overflow_names}）"
                )
                try:
                    logger.info("[keyframe_input] get_character_ref_images output: shot_number=%s individual=%s sheet_overflow=%s", shot.shot_number, keep_count, len(overflow_urls))
                except Exception:
                    pass
                return result_urls
        except Exception as e:
            logger.warning(f"Reference Sheet 生成失败，降级截断: {e}")

    # 未超限或 sheet 失败：直接返回原始图（可能超限由下游截断）
    try:
        logger.info("[keyframe_input] get_character_ref_images output: shot_number=%s urls_count=%s urls=%s", shot.shot_number, len(all_ref_images), _keyframe_input_to_jsonable(all_ref_images))
    except Exception:
        pass
    return all_ref_images


async def _build_character_images_dict(
    all_character_ids: set,
    user_id: str
) -> Dict[str, CharacterImageInfo]:
    """
    构建角色图片信息字典（含 profile、version_id，统一由本函数管理，无需再单独传 characters_data / character_to_version_map）
    
    Args:
        all_character_ids: 所有角色ID集合
        user_id: 用户ID
    
    Returns:
        character_images: 角色图片字典，每项含 profile 与 version_id
    """
    from ....crud.video.video_character import (
        get_character_versions_batch,
        get_character_multi_view_images_batch,
        get_character_fusion_images_batch,
        get_characters_by_uuids,
        pick_selected_character_version,
    )
    
    character_images: Dict[str, CharacterImageInfo] = {}
    
    if not all_character_ids:
        return character_images
    
    try:
        requested_ids = list(all_character_ids)
        characters_db_list = await get_characters_by_uuids(requested_ids)
        char_row_by_uuid = {c.uuid: c for c in characters_db_list}
        characters_data = [
            CharacterProfile(
                id=char.uuid,
                type=VisualElementType(getattr(char, "type", "character")),
                name=char.name,
                description=char.description,
                role=char.role,
                personality=char.personality or "",
                appearance=char.appearance or "",
                style=char.style or "",
                character_image_url=char.image_url or "",
                body_type=char.body_type or "",
            )
            for char in characters_db_list
        ]
        db_returned_ids = [c.id for c in characters_data]
        missing_in_db = set(requested_ids) - set(db_returned_ids)
        try:
            logger.info(
                "[keyframe_datasource] _build_character_images_dict: requested=%d ids=%s, db_returned=%d ids=%s, missing_in_db=%s",
                len(requested_ids), requested_ids, len(db_returned_ids), db_returned_ids, list(missing_in_db) if missing_in_db else None
            )
        except Exception as _e:
            logger.debug("keyframe_datasource log skip: %s", _e)

        character_uuids = [c.uuid for c in characters_db_list if getattr(c, "image_url", None)]
        
        if character_uuids:
            all_versions = await get_character_versions_batch(character_uuids, user_id)
            all_multiviews = await get_character_multi_view_images_batch(character_uuids, user_id)
            all_fusions = await get_character_fusion_images_batch(character_uuids)
            
            uuid_to_selected_version = {}
            uuid_to_selected_image = {}
            forced_cv = forced_character_version_uuids_cv.get()
            for character_uuid, versions in all_versions.items():
                if versions:
                    row = char_row_by_uuid.get(character_uuid)
                    picked = None
                    if forced_cv:
                        want = (forced_cv.get(character_uuid) or "").strip()
                        if want:
                            picked = next((v for v in versions if getattr(v, "uuid", None) == want), None)
                    if picked is None:
                        picked = pick_selected_character_version(versions, row)
                    if picked:
                        uuid_to_selected_version[character_uuid] = picked.uuid
                        if picked.character_image_url:
                            uuid_to_selected_image[character_uuid] = picked.character_image_url
            
            for character in characters_data:
                char_info = CharacterImageInfo(
                    character_id=character.id,
                    main_image_url=uuid_to_selected_image.get(character.id) or character.character_image_url or None,
                    profile=character,
                    version_id=uuid_to_selected_version.get(character.id),
                )
                
                multiview_record = all_multiviews.get(character.id)
                if multiview_record and multiview_record.multi_view_image_url:
                    char_info.multiview_url = multiview_record.multi_view_image_url
                
                fusions = all_fusions.get(character.id, [])
                for fusion in fusions:
                    if fusion.character_ids and len(fusion.character_ids) == 1:
                        if fusion.fusion_image_url and not char_info.main_fusion_url:
                            char_info.main_fusion_url = fusion.fusion_image_url
                    elif fusion.character_ids and character.id in fusion.character_ids:
                        if fusion.fusion_image_url and not char_info.main_fusion_url:
                            char_info.main_fusion_url = fusion.fusion_image_url
                
                character_images[character.id] = char_info
                logger.info(f"🎭 角色 {character.name}: 主图={char_info.main_image_url is not None}, multiview={char_info.multiview_url is not None}, 主图融合={char_info.main_fusion_url is not None}, multiview融合={char_info.multiview_fusion_url is not None}")
        else:
            for character in characters_data:
                char_info = CharacterImageInfo(
                    character_id=character.id,
                    main_image_url=character.character_image_url or None,
                    profile=character,
                )
                character_images[character.id] = char_info

        missing_in_result = set(all_character_ids) - set(character_images.keys())
        if missing_in_result:
            try:
                logger.warning(
                    "[keyframe_datasource] _build_character_images_dict result: character_images has %d entries; missing_ids (in all_character_ids but not in character_images)=%s",
                    len(character_images), list(missing_in_result)
                )
            except Exception as _e:
                logger.debug("keyframe_datasource log skip: %s", _e)
                
    except Exception as e:
        logger.warning(f"获取角色信息失败: {e}")
        try:
            from ....services.agent.utils.database_utils import get_characters_from_db
            characters_data = await get_characters_from_db(list(all_character_ids))
            for character in characters_data:
                char_info = CharacterImageInfo(
                    character_id=character.id,
                    main_image_url=character.character_image_url or None,
                    profile=character,
                )
                character_images[character.id] = char_info
        except:
            pass
    
    return character_images


def current_selected_element_version_ids(
    shot: Any,
    character_images: Dict[str, "CharacterImageInfo"],
) -> List[str]:
    """按 shot.character_ids 取该镜头各视觉元素（人物/物品/场所）当前选中版本 id，跳过无版本者。

    character_images 由 _build_character_images_dict 构建，其中 version_id 已是「当前选中版本」
    （或 forced_cv 指定版本），三类元素统一走同一张表，故本函数覆盖人物/物品/场所。"""
    vids: List[str] = []
    for cid in (getattr(shot, "character_ids", None) or []):
        ci = character_images.get(cid)
        vid = getattr(ci, "version_id", None) if ci else None
        if vid:
            vids.append(vid)
    return vids


def should_reuse_ref_image_urls(
    shot: Any,
    character_images: Dict[str, "CharacterImageInfo"],
    stored_character_version_ids: Optional[List[str]],
) -> bool:
    """判断 regenerate 时是否可复用上一版 reference_image_urls。

    仅当「这一镜所有视觉元素的当前选中版本」== 「关键帧存的 character_version_ids」时才复用（此时确实
    啥都没变，复用可避免重新匹配把 location 等图误丢）；只要任一元素（人物/物品/场所）换了版本或被
    增删，返回 False → 走重新匹配，用 character_images 里的选中版本重取图。"""
    current = sorted(current_selected_element_version_ids(shot, character_images))
    stored = sorted([v for v in (stored_character_version_ids or []) if v])
    return current == stored


def build_character_labels_for_shot(
    shot: Any,
    character_images: Dict[str, Any],
    exclude_location: bool = False,
) -> List[Dict[str, Any]]:
    """根据镜头与角色图信息构建角色 label 列表（与 character_ref_image_urls 一一对应）。
    供 keyframe context、video_generation context、一致性测试 add item 与 worker 共用。
    exclude_location=True 时跳过 type=location 的元素，使 label 数量与「不带场所图」时的 ref 一致。"""
    labels: List[Dict[str, Any]] = []
    for char_id in (getattr(shot, "character_ids", None) or []):
        ci = character_images.get(char_id)
        if not ci or not getattr(ci, "profile", None):
            continue
        char = ci.profile
        if exclude_location and getattr(char, "type", "character") == "location":
            continue
        labels.append({
            "index": len(labels) + 1,
            "name": getattr(char, "name", "") or "未知",
            "type_label": "角色",
            "description": getattr(char, "description", "") or "",
            "appearance": getattr(char, "appearance", "") or "",
            "style": getattr(char, "style", "") or "",
            "body_type": getattr(char, "body_type", "") or "",
            "role": getattr(char, "role", "") or "",
        })
    return labels


def _get_char_names_from_character_images(char_ids: List[str], character_images: Dict[str, CharacterImageInfo]) -> List[str]:
    """从 character_images 的 profile 取名称，统一用 character_images 不拆 characters_data"""
    names = []
    for cid in char_ids:
        ci = character_images.get(cid)
        if ci and getattr(ci, "profile", None) and getattr(ci.profile, "name", None):
            names.append(ci.profile.name)
    return names


async def _get_per_shot_ref_image_urls(
    shots_batch: List[DetailedShot],
    character_images: Dict[str, CharacterImageInfo],
    user_option: Optional[UserOption],
    user_id: str,
    generate_last_frame: bool,
    first_frame_images: Optional[Dict[int, str]],
    pre_fetched_fusions: Optional[List[Any]] = None,
    reuse_ref_image_urls: Optional[List[str]] = None,
) -> List[List[str]]:
    """
    按镜头返回每镜头的参考图 URL 列表（顺序：角色参考图 → 多视角 → 多视角融合 → 首帧）。
    不做全局去重，与 video prompt 一致：每 shot 独立给图，LLM 用本镜头内 image 1, image 2 引用即可。
    
    当 get_character_ref_images 返回 Reference Sheet（带 REFERENCE_SHEET_URL_TAG 前缀）时,
    跳过追加 multiview/multiview_fusion（sheet 已包含所有参考），并将 tag 保留给下游检测。

    regenerate 复用模式（reuse_ref_image_urls 非 None）：直接返回该 url 列表，
    跳过 get_character_ref_images / multiview 追加，避免「重新匹配」导致 location 等参考图被新模型能力规则误丢。
    """
    if reuse_ref_image_urls is not None:
        logger.info(
            f"♻️ regenerate 复用 ref_urls（_get_per_shot_ref_image_urls）：shots={len(shots_batch)} urls/shot={len(reuse_ref_image_urls)}"
        )
        return [list(reuse_ref_image_urls) for _ in shots_batch]

    use_venue_ref_image = resolve_use_venue_ref_image(user_option)

    result: List[List[str]] = []
    for shot in shots_batch:
        character_ref_images = await get_character_ref_images(
            shot=shot,
            character_images=character_images,
            db=None,
            user_id=user_id,
            model_limit=get_model_limit(user_option),
            pre_fetched_fusions=pre_fetched_fusions,
            use_venue_ref_image=use_venue_ref_image,
        )
        urls_ordered: List[str] = list(character_ref_images)
        
        # 检测是否包含 Reference Sheet；如果是，不再追加 multiview 等额外图片
        has_sheet = any(u.startswith(REFERENCE_SHEET_URL_TAG) for u in urls_ordered)
        
        if not has_sheet and shot.character_ids:
            for char_id in shot.character_ids:
                if char_id in character_images:
                    ci = character_images[char_id]
                    if not use_venue_ref_image and getattr(getattr(ci, "profile", None), "type", "character") == "location":
                        continue
                    if ci.multiview_url:
                        urls_ordered.append(ci.multiview_url)
                    if ci.multiview_fusion_url:
                        urls_ordered.append(ci.multiview_fusion_url)
        if generate_last_frame and first_frame_images:
            u = first_frame_images.get(shot.shot_number)
            if u:
                urls_ordered.append(u)
        result.append(urls_ordered)
    return result


async def _build_single_keyframe_tool_call_prompt(
    shot: DetailedShot,
    character_ref_images: List[str],
    t2i_prompt: str,
    tool_name: str,
    user_input: str = "",
    user_regenerate_instruction: Optional[str] = None,
) -> List[BaseMessage]:
    """Facts-only Human JSON + keyframe-tool-director skill."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )

    regen = (user_regenerate_instruction or "").strip() or None
    facts = {
        "tool_name": tool_name,
        "mode": "i2i" if character_ref_images else "t2i",
        "shot_number": shot.shot_number,
        "is_bridge": bool(shot.is_bridge),
        "shot_type": shot.shot_type,
        "camera_position": shot.camera_position or "",
        "camera_angle": shot.camera_angle or "",
        "subject_angle": shot.subject_angle or "",
        "subject_pose": shot.subject_pose or "",
        "scene_description": shot.scene_description or "",
        "camera_movement": shot.camera_movement or "",
        "lighting": shot.lighting or "",
        "visual_effects": shot.visual_effects or "",
        "dialogue": shot.dialogue or "",
        "narration": shot.narration or "",
        "sound_effects": shot.sound_effects or "",
        "transition": shot.transition or "",
        "user_input": user_input or "",
        "t2i_prompt": t2i_prompt or "",
        "user_regenerate_instruction": regen,
        "reference_image_urls": list(character_ref_images or []),
        "prompt_length_range": KEYFRAME_PROMPT_LENGTH_RANGE,
    }
    system = skill_system_message("keyframe-tool-director", lead="Follow keyframe-tool-director.")
    return [SystemMessage(content=system), HumanMessage(content=facts_human_message(facts))]

async def generate_batch_keyframe_prompts(
    shots_batch: List[DetailedShot],
    character_images: Dict[str, CharacterImageInfo],
    prev_shot: Optional[DetailedShot] = None,
    next_shot: Optional[DetailedShot] = None,
    user_option: Optional[UserOption] = None,
    user_input: str = "",
    generate_first_frame: bool = True,
    generate_last_frame: bool = False,
    first_frame_images: Optional[Dict[int, str]] = None,
    first_frame_prompts: Optional[Dict[int, str]] = None,
    user_id: str = None,
    content_category: Optional[str] = None,
    detected_language: Optional[str] = None,
    hidden_style_description: Optional[str] = None,
    pre_fetched_fusions: Optional[List[Any]] = None,
    reuse_ref_image_urls: Optional[List[str]] = None,
) -> Tuple[List[BaseMessage], List[KeyframePromptResult]]:
    """第一步：批量生成关键帧prompts（使用LLM批量生成，确保上下文连贯）
    
    Args:
        generate_first_frame: 是否生成首帧
        generate_last_frame: 是否生成尾帧
        first_frame_images: 首帧图片URL映射 {shot_number: image_url}（生成尾帧时使用）
        first_frame_prompts: 首帧prompt映射 {shot_number: prompt}（生成尾帧时使用）
        user_id: 用户ID（获取多视角图时使用）
    """
    frame_type = "尾帧" if generate_last_frame else "首帧"
    logger.info(f"🎨 第一步：批量生成{len(shots_batch)}个关键帧prompt（{frame_type}）")

    _prompts_input = {
        "shots_batch": [{"shot_number": s.shot_number, "character_ids": s.character_ids} for s in shots_batch],
        "character_images_keys": list(character_images.keys()),
        "generate_first_frame": generate_first_frame,
        "generate_last_frame": generate_last_frame,
        "first_frame_images_keys": list(first_frame_images.keys()) if first_frame_images else None,
        "pre_fetched_fusions_count": len(pre_fetched_fusions) if pre_fetched_fusions else 0,
    }
    try:
        logger.info("[keyframe_input] generate_batch_keyframe_prompts input: %s", json.dumps(_keyframe_input_to_jsonable(_prompts_input), ensure_ascii=False, default=str))
    except Exception as _e:
        logger.debug("keyframe_input generate_batch_keyframe_prompts json skip: %s", _e)

    try:
        logger.info(f"🎨 批量生成{len(shots_batch)}个关键帧prompts（facts brief + keyframe-director）")

        # Resolve image tool option for max_reference_images fact only
        from ....models.tool_enums import ToolMode
        from .per_shot_generation_routing_service import resolve_user_option_for_shot

        has_character_images = any(
            (shot.character_ids and any(cid in character_images for cid in shot.character_ids))
            for shot in shots_batch
        )
        mode = ToolMode.I2I if has_character_images else ToolMode.T2I
        _batch_uo = user_option
        if shots_batch:
            _batch_uo = resolve_user_option_for_shot(user_option, shots_batch[0], "image")
        max_reference_images = ToolService.get_max_reference_images_for_keyframe(_batch_uo)

        from ....models.video_state import ContentCategory
        is_lip_sync_mv = content_category in (
            ContentCategory.LIP_SYNC_MV.value,
            ContentCategory.PRODUCT_LAUNCH.value,
        ) if content_category else False

        # Resolve per-shot ref URLs (I/O only — no prompt craft)
        per_shot_ref_urls = await _get_per_shot_ref_image_urls(
            shots_batch=shots_batch,
            character_images=character_images,
            user_option=user_option,
            user_id=user_id,
            generate_last_frame=generate_last_frame,
            first_frame_images=first_frame_images,
            pre_fetched_fusions=pre_fetched_fusions,
            reuse_ref_image_urls=reuse_ref_image_urls,
        )
        use_venue_ref_image = resolve_use_venue_ref_image(user_option, reuse_ref_image_urls=reuse_ref_image_urls)

        # Facts-only shot rows for keyframe-director (rules live in SKILL.md)
        shot_rows: List[Dict[str, Any]] = []
        ref_images_by_shot: Dict[int, List[RefImageInfo]] = {}
        for i, shot in enumerate(shots_batch):
            raw_ref_urls = per_shot_ref_urls[i] if i < len(per_shot_ref_urls) else []
            ref_urls, shot_has_sheet, shot_sheet_idx = process_ref_urls_with_sheet_detection(raw_ref_urls)
            if shot_has_sheet and shot_sheet_idx is not None:
                sheet_url = ref_urls[shot_sheet_idx]
                individual_urls = [u for j, u in enumerate(ref_urls) if j != shot_sheet_idx]
            else:
                sheet_url = None
                individual_urls = ref_urls
            individual_count = len(individual_urls)

            elements_with_images: List[Dict[str, Any]] = []
            shot_ref_images: List[RefImageInfo] = []
            locations_text_only: List[Dict[str, str]] = []
            overflow_names: List[str] = []
            url_idx = 0

            if shot.character_ids:
                for char_id in shot.character_ids:
                    ci = character_images.get(char_id)
                    if not ci or not getattr(ci, "profile", None):
                        continue
                    char = ci.profile
                    char_type = getattr(char, "type", "character")
                    char_name = getattr(char, "name", "未知元素")
                    char_desc = getattr(char, "description", "") or ""
                    char_appearance = getattr(char, "appearance", "") or ""
                    type_label = {"character": "角色", "object": "物品", "location": "场所"}.get(
                        char_type, "元素"
                    )

                    if char_type == "location" and not use_venue_ref_image:
                        loc_text = (char_desc or "").strip()
                        if char_appearance.strip():
                            loc_text = (
                                f"{loc_text} {char_appearance.strip()}".strip()
                                if loc_text
                                else char_appearance.strip()
                            )
                        locations_text_only.append({
                            "id": char_id,
                            "name": char_name,
                            "type": char_type,
                            "description": loc_text or char_desc,
                            "appearance": char_appearance,
                        })
                        continue

                    if url_idx < individual_count:
                        url = individual_urls[url_idx]
                        image_number = len(elements_with_images) + 1
                        elements_with_images.append({
                            "id": char_id,
                            "name": char_name,
                            "type": char_type,
                            "type_label": type_label,
                            "description": char_desc,
                            "appearance": char_appearance,
                            "image_number": image_number,
                            "url": url,
                            "is_sheet": False,
                        })
                        if url:
                            shot_ref_images.append(
                                RefImageInfo(url=url, role=RefImageRole.CHARACTER, label=char_name)
                            )
                        url_idx += 1
                    else:
                        overflow_names.append(char_name)

            if shot_has_sheet and sheet_url:
                image_number = len(elements_with_images) + 1
                elements_with_images.append({
                    "name": "Reference Sheet",
                    "type": "sheet",
                    "type_label": "参考表",
                    "description": "",
                    "appearance": "",
                    "image_number": image_number,
                    "url": sheet_url,
                    "is_sheet": True,
                    "sheet_element_names": list(overflow_names),
                })
                shot_ref_images.append(
                    RefImageInfo(
                        url=sheet_url,
                        role=RefImageRole.SHEET,
                        label=f"Reference Sheet ({', '.join(overflow_names)})",
                    )
                )
            if generate_last_frame and first_frame_images and shot.shot_number in first_frame_images:
                first_url = first_frame_images.get(shot.shot_number)
                if first_url:
                    image_number = len(elements_with_images) + 1
                    elements_with_images.append({
                        "name": "first_frame",
                        "type": "first_frame",
                        "type_label": "首帧",
                        "description": "",
                        "appearance": "",
                        "image_number": image_number,
                        "url": first_url,
                        "is_sheet": False,
                    })
                    shot_ref_images.append(
                        RefImageInfo(url=first_url, role=RefImageRole.FIRST_FRAME, label="首帧")
                    )

            ref_images_by_shot[shot.shot_number] = shot_ref_images
            ref_image_urls = [e["url"] for e in elements_with_images if e.get("url")]
            shot_rows.append({
                "shot_number": shot.shot_number,
                "character_ids": list(shot.character_ids or []),
                "duration": shot.duration,
                "shot_type": shot.shot_type or "",
                "camera_position": shot.camera_position or "",
                "camera_angle": shot.camera_angle or "",
                "subject_angle": shot.subject_angle or "",
                "subject_pose": shot.subject_pose or "",
                "scene_description": shot.scene_description or "",
                "camera_movement": shot.camera_movement or "",
                "lighting": shot.lighting or "",
                "visual_effects": shot.visual_effects or "",
                "transition": shot.transition or "",
                "dialogue": shot.dialogue or "",
                "sound_effects": shot.sound_effects or "",
                "narration": shot.narration or "",
                "style_guide": shot.style_guide or "",
                "elements_with_images": elements_with_images,
                "locations_text_only": locations_text_only,
                "ref_count": len(elements_with_images),
                "ref_image_urls": ref_image_urls,
            })

        first_frame_prompts_by_shot: Dict[str, str] = {}
        if generate_last_frame and first_frame_prompts:
            for shot in shots_batch:
                fp = first_frame_prompts.get(shot.shot_number)
                if fp:
                    first_frame_prompts_by_shot[str(shot.shot_number)] = fp

        import uuid as _uuid
        from app.services.agent.video.keyframe_stage import (
            export_keyframe_prompt_inputs,
            generate_keyframe_prompts_via_deep_agent,
        )
        tid = f"kf_{_uuid.uuid4().hex[:8]}"
        rid = f"run_{_uuid.uuid4().hex[:8]}"
        _paths = export_keyframe_prompt_inputs(
            thread_id=tid,
            run_id=rid,
            batch_id="b0",
            shots=shot_rows,
            style_guide=(hidden_style_description or ""),
            user_input=user_input or "",
            prev_shot=_shot_to_dict(prev_shot),
            next_shot=_shot_to_dict(next_shot),
            is_lip_sync_mv=is_lip_sync_mv,
            generate_first_frame=generate_first_frame,
            generate_last_frame=generate_last_frame,
            first_frame_prompts_context=json.dumps(first_frame_prompts_by_shot, ensure_ascii=False)
            if first_frame_prompts_by_shot
            else "",
            max_reference_images=max_reference_images,
        )
        art, da_msgs = await generate_keyframe_prompts_via_deep_agent(
            thread_id=tid, run_id=rid, input_paths=_paths, detected_language=detected_language,
        )
        batch_prompt_result = BatchKeyframePromptResultForLLM(
            prompts=[KeyframePromptResultForLLM(shot_number=p.shot_number, t2i_prompt=p.t2i_prompt) for p in art.prompts]
        )
        result = {"structured_response": batch_prompt_result, "messages": list(da_msgs or [])}
        
        # 从 agent 结果中提取 structured_response
        if not result or "structured_response" not in result:
            raise Exception("LLM prompt生成失败：无法解析结果")
        
        batch_prompt_result = result["structured_response"]
        
        frame_index = -1 if generate_last_frame else 0
        ordered_shot_numbers: List[int] = []
        _seen_sn = set()
        for s in shots_batch:
            if s.shot_number not in _seen_sn:
                _seen_sn.add(s.shot_number)
                ordered_shot_numbers.append(s.shot_number)
        expected_set = set(ordered_shot_numbers)

        llm_by_shot: Dict[int, KeyframePromptResult] = {}
        for p in batch_prompt_result.prompts:
            sn = p.shot_number
            if sn not in expected_set:
                logger.warning(
                    "🎨 LLM 返回了未在本批次请求的镜头 %s，已忽略（本批次镜头: %s）",
                    sn,
                    ordered_shot_numbers,
                )
                continue
            if sn in llm_by_shot:
                logger.warning("🎨 镜头%s 出现重复的关键帧 prompt，保留首次结果", sn)
                continue
            t2i = (p.t2i_prompt or "").strip()
            if not t2i:
                logger.warning("🎨 镜头%s 的 t2i_prompt 为空，将用场景描述补齐", sn)
                continue
            llm_by_shot[sn] = KeyframePromptResult(
                shot_number=sn,
                t2i_prompt=t2i,
                frame_index=frame_index,
                ref_images=ref_images_by_shot.get(sn, []),
            )

        generated_prompts = []
        padded_count = 0
        for sn in ordered_shot_numbers:
            if sn in llm_by_shot:
                generated_prompts.append(llm_by_shot[sn])
                continue
            shot = next((s for s in shots_batch if s.shot_number == sn), None)
            fallback = (
                shot.scene_description
                if shot and shot.scene_description
                else f"镜头{sn}的{frame_type}画面"
            )
            logger.warning(
                "🎨 批量关键帧 prompt 遗漏或无效镜头 %s，已用场景描述补齐（长度=%s）",
                sn,
                len(fallback),
            )
            generated_prompts.append(
                KeyframePromptResult(
                    shot_number=sn,
                    t2i_prompt=fallback,
                    frame_index=frame_index,
                    ref_images=ref_images_by_shot.get(sn, []),
                )
            )
            padded_count += 1

        if padded_count:
            logger.info("🎨 本批次为 %s 个镜头补全了缺失/无效的关键帧 prompt", padded_count)
        
        all_messages = list(result.get("messages") or [])
        
        logger.info(f"🎨 批量prompt生成完成：总共获得{len(generated_prompts)}个{frame_type}prompt结果")
        logger.info(f"📨 返回 {len(all_messages)} 条 deep-agent 消息")
        
        return all_messages, generated_prompts
        
    except Exception as e:
        error_msg = f"批量prompt生成异常 - {str(e)}"
        logger.error(error_msg)
        
        # 返回失败结果
        frame_index = -1 if generate_last_frame else 0
        failed_prompts = []
        for shot in shots_batch:
            failed_prompt = KeyframePromptResult(
                shot_number=shot.shot_number,
                t2i_prompt="",  # 空的prompt表示失败
                frame_index=frame_index
            )
            failed_prompts.append(failed_prompt)
        
        return [], failed_prompts


async def resolve_keyframe_prompt_reference_images(
    shots_batch: List[DetailedShot],
    character_images: Dict[str, CharacterImageInfo],
    prompts: List[KeyframePromptResult],
    user_option: Optional[UserOption] = None,
    user_id: Optional[str] = None,
    reuse_ref_image_urls: Optional[List[str]] = None,
) -> List[KeyframePromptResult]:
    """
    在 eval/fix 之后执行：确定 final_prompt；若 ref_images 为空（regenerate 路径）则获取参考图并填充。
    execute 阶段使用 prompt.all_ref_urls 传 tool，顺序与 prompt 中 image 1, image 2 一致。

    regenerate 复用模式（reuse_ref_image_urls 非 None）：直接用该 url 列表填 ref_images，
    跳过 get_character_ref_images，保证「重新生成」沿用上一 version 已匹配过的参考图。
    """
    shot_map = {s.shot_number: s for s in shots_batch}
    for prompt in prompts:
        if prompt.final_prompt:
            continue
        # Pipeline 路径：ref_images 已在 generate 阶段填好
        if prompt.ref_images:
            # Sheet handling craft is in keyframe-director / keyframe-tool-director skills
            prompt.final_prompt = prompt.t2i_prompt
            continue
        # Regenerate 路径：ref_images 为空，需要获取参考图
        shot = shot_map.get(prompt.shot_number)
        if not shot:
            continue
        if reuse_ref_image_urls is not None:
            used_image_urls = list(reuse_ref_image_urls)
            logger.info(
                f"♻️ regenerate 复用 ref_urls（resolve_keyframe_prompt_reference_images）：shot={prompt.shot_number} n={len(used_image_urls)}"
            )
        else:
            use_venue_ref_image = resolve_use_venue_ref_image(user_option)
            used_image_urls = await get_character_ref_images(
                shot=shot,
                character_images=character_images,
                db=None,
                user_id=user_id,
                model_limit=get_model_limit(user_option),
                use_venue_ref_image=use_venue_ref_image,
            )
        if used_image_urls:
            ref_images: List[RefImageInfo] = []
            has_sheet = False
            for url in used_image_urls:
                clean, is_sheet = strip_reference_sheet_tag(url)
                if is_sheet:
                    has_sheet = True
                    ref_images.append(RefImageInfo(url=clean, role=RefImageRole.SHEET, label="Reference Sheet"))
                else:
                    ref_images.append(RefImageInfo(url=clean, role=RefImageRole.CHARACTER))
            prompt.ref_images = ref_images
            prompt.final_prompt = prompt.t2i_prompt
        else:
            prompt.final_prompt = prompt.t2i_prompt
    
    return prompts


async def execute_batch_keyframe_generation(
    shots_batch: List[DetailedShot],
    character_images: Dict[str, CharacterImageInfo],
    prompts: List[KeyframePromptResult],
    user_option: Optional[UserOption] = None,
    shared_semaphore: Optional[asyncio.Semaphore] = None,
    user_input: str = "",
    first_frame_images: Optional[Dict[int, str]] = None,
    user_id: str = None,
    send_event_func: Optional[Any] = None,
    conversation_id: Optional[str] = None,
    detected_language: Optional[str] = None,
    skip_consistency_check: bool = False,
    reuse_ref_image_urls: Optional[List[str]] = None,
) -> Tuple[List[BaseMessage], List[KeyframeVersion]]:
    """第三步：并发执行关键帧生成（使用共享semaphore控制跨批次总并发，LLM调用工具）
    
    Args:
        first_frame_images: 首帧图片URL映射（生成尾帧时使用）
        user_id: 用户ID（获取多视角图时使用）
        send_event_func: 可选，用于发送 IMAGE_MODEL_SWITCHED 等事件
        conversation_id: 可选，与 send_event_func 配合使用
    """
    from ..utils.prompt_utils import get_concurrency_limit, add_random_delay
    
    # 使用共享的信号量（跨批次共享），如果没有则创建新的
    if shared_semaphore is None:
        max_concurrent = get_concurrency_limit("keyframe_generation")
        semaphore = asyncio.Semaphore(max_concurrent)
    else:
        semaphore = shared_semaphore
    
    logger.info(f"🎨 第二步：并发执行关键帧生成 - {len(prompts)}个关键帧（{len(shots_batch)}个镜头）")
    
    _use_venue_ref_image = resolve_use_venue_ref_image(user_option, reuse_ref_image_urls=reuse_ref_image_urls)

    from ....models.image_result import ImageGenerationResult, ImageModelSwitchedMessageKey
    from ....tools.context_schemas import ImageGenerationContext
    from ....models.tool_enums import ToolMode

    from prompts.prompt_config import PromptName, PROMPTS_CONFIG
    from ....services.agent.utils.llm_resilience import (
        StructuredResilienceKind,
        ainvoke_structured_resilient,
    )
    
    async def generate_single_keyframe_with_semaphore(
        shot: DetailedShot, 
        prompt_result: KeyframePromptResult
    ) -> Tuple[KeyframeVersion, List[BaseMessage]]:
        """带信号量限制的单个关键帧生成（LLM调用工具）"""
        # 添加随机延迟避免并发请求过于集中
        await add_random_delay()
        
        async with semaphore:
            shot_number = shot.shot_number
            frame_index = prompt_result.frame_index
            frame_desc = "首帧" if frame_index == 0 else ("尾帧" if frame_index == -1 else f"中间帧{frame_index}")
            
            replaced_prompt = prompt_result.final_prompt if prompt_result.final_prompt is not None else prompt_result.t2i_prompt
            used_image_urls = prompt_result.all_ref_urls
            display_ref_urls = prompt_result.display_ref_urls
            
            # 根据是否有角色参考图片选择工具模式，并提前获取工具信息（供 early return 与后续使用）
            from ....models.tool_enums import ToolMode, AspectRatio, Resolution, DefaultValues
            
            mode = ToolMode.I2I if used_image_urls else ToolMode.T2I
            from .per_shot_generation_routing_service import resolve_user_option_for_shot

            _eff_kf_uo = resolve_user_option_for_shot(user_option, shot, "image")
            tools_info = ToolService.get_image_generation_tools(_eff_kf_uo, mode=mode)
            provider_str = tools_info.primary_tool_name if tools_info else ""
            aspect_ratio_enum = (
                user_option.aspect_ratio
                if user_option and user_option.aspect_ratio
                else DefaultValues.IMAGE_ASPECT_RATIO
            )
            resolution_enum = (
                user_option.resolution
                if user_option and user_option.resolution
                else DefaultValues.IMAGE_RESOLUTION
            )
            # 实际使用的 model 从 tools_info 取，与 get_image_generation_tools 选链一致，不再用 get_nano_banana_model_for_tool
            model_enum = tools_info.tool_type if tools_info and tools_info.tools else None
            
            # 如果 prompt 为空，直接返回失败结果（与其它分支一致：写入 aspect_ratio/resolution/model/image_generation_tool）
            if not prompt_result.t2i_prompt:
                return (KeyframeVersion(
                    shot_number=shot_number,
                    is_bridge=shot.is_bridge,
                    keyframe_url="",
                    t2i_prompt="",
                    provider=provider_str,
                    success=False,
                    error_msg="prompt生成失败",
                    audio_segment_ids=shot.audio_segment_ids,
                    detailed_shot_id=shot.uuid,
                    frame_index=frame_index,
                    reference_image_urls=None,
                    aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
                    resolution=resolution_enum.value if resolution_enum else None,
                    model=model_enum.value if model_enum else None,
                    image_generation_tool=tools_info.user_option_tool if tools_info else None,
                ), [])
            
            # ⭐ 使用 final_prompt（如果存在）或 t2i_prompt（向后兼容）
            stored_prompt = prompt_result.final_prompt if prompt_result.final_prompt else prompt_result.t2i_prompt
            
            try:
                logger.info(f"🎨 镜头{shot_number}: 开始LLM关键帧生成")
                
                image_tools = tools_info.tool_objects
                if not image_tools:
                    raise Exception(f"无法获取{mode.value}模式的图像生成工具")
                
                # ⭐ 使用预加载的 prompt 模板构建 messages
                tool_prompt_messages = await _build_single_keyframe_tool_call_prompt(
                    shot, used_image_urls, replaced_prompt,
                    tools_info.primary_tool_name,
                    user_input,
                    user_regenerate_instruction=getattr(prompt_result, "user_regenerate_instruction", None),
                )
                
                reference_image_labels = None
                if used_image_urls and character_images and (shot.character_ids or []):
                    all_labels = build_character_labels_for_shot(
                        shot, character_images, exclude_location=not _use_venue_ref_image
                    ) or []
                    if len(all_labels) > len(used_image_urls):
                        # 超限场景：最后一个 URL 是 Reference Sheet，labels 需匹配
                        individual_count = len(used_image_urls) - 1
                        individual_labels = all_labels[:individual_count]
                        overflow_labels = all_labels[individual_count:]
                        overflow_names = ", ".join(lb.get("name", "") for lb in overflow_labels)
                        sheet_label = {
                            "index": individual_count + 1,
                            "name": f"Reference Sheet ({overflow_names})",
                            "type_label": "参考表",
                            "description": f"Grid containing {len(overflow_labels)} elements: {overflow_names}",
                        }
                        reference_image_labels = individual_labels + [sheet_label]
                    else:
                        reference_image_labels = all_labels if all_labels else None
                context = ImageGenerationContext(
                    aspect_ratio=aspect_ratio_enum,
                    resolution=resolution_enum,
                    reference_image_urls=used_image_urls if used_image_urls else None,
                    reference_image_labels=reference_image_labels if reference_image_labels else None,
                    model=model_enum,
                    language=detected_language,
                    skip_consistency_check=skip_consistency_check,
                )
                
                if used_image_urls:
                    logger.info(f"🎯 镜头{shot_number}: 通过 context 传递 {len(used_image_urls)} 张参考图片（代码层面保证准确性）")
                
                inputs = {"messages": tool_prompt_messages}
                result = await ainvoke_structured_resilient(
                    kind=StructuredResilienceKind.CREATE_AGENT,
                    prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_KEYFRAME_GENERATION_TOOL_EXECUTION],
                    agent_inputs=inputs,
                    agent_tools=image_tools,
                    context_schema=ImageGenerationContext,
                    agent_invoke_context=context,
                    wrap_agent_parse_fallback=True,
                    log_context={"shot_number": shot_number, "phase": "keyframe_tool_execution"},
                )
                
                # [metrics 排查] agent 返回后立即打日志，确认 structured_response 里是否有 metrics
                _sr = result.get("structured_response")
                _msgs = result.get("messages", [])
                _last_tool = next((m for m in reversed(_msgs) if getattr(m, "type", None) == "tool"), None)
                _content_len = len(getattr(_last_tool, "content", "") or "") if _last_tool else 0
                logger.info(
                    "[keyframe_agent] ainvoke 后 structured_response: tool_duration_sec=%s, tool_cost=%s, has_sr=%s; 最后 ToolMessage content 长度=%s",
                    getattr(_sr, "tool_duration_sec", None) if _sr else None,
                    getattr(_sr, "tool_cost", None) if _sr else None,
                    _sr is not None,
                    _content_len,
                )
                
                # ✅ 获取完整的 agent messages（参考 _generate_character_image_with_llm）
                agent_full_messages = result.get("messages", [])
                input_message_count = len(tool_prompt_messages)
                output_messages = agent_full_messages[input_message_count:] if len(agent_full_messages) > input_message_count else []
                all_messages = tool_prompt_messages + output_messages
                
                # 提取 ai_messages_json
                ai_messages_json = None
                if all_messages:
                    result_for_extraction = {"messages": all_messages if isinstance(all_messages, list) else [all_messages]}
                    ai_messages_json = extract_ai_message_json(result_for_extraction)
                
                # 获取结构化响应
                structured_response: ImageGenerationResult = cast(ImageGenerationResult, result.get("structured_response"))
                structured_response = patch_tool_metrics_from_last_tool_message(
                    result.get("messages", []),
                    structured_response,
                )
                # 若发生模型降级/切换，发送事件通知用户（message_key 供前端 i18n）
                if (
                    send_event_func
                    and conversation_id
                    and structured_response
                    and structured_response.model_switched
                ):
                    msg = structured_response.user_facing_message or "指定的模型因使用量暂时无法调用，已自动重试别的模型完成生成。"
                    await send_event_func(
                        conversation_id=conversation_id,
                        event_type=MessageType.IMAGE_MODEL_SWITCHED,
                        message=msg,
                        extra_data={"message_key": ImageModelSwitchedMessageKey.FALLBACK_DEFAULT.value},
                    )
                
                # 检查生成结果
                if structured_response and structured_response.success and structured_response.image_url:
                    logger.info(f"✅ 镜头{shot_number}({frame_desc}): 关键帧生成成功 - {structured_response.image_url}")
                    _gp = (getattr(structured_response, "generated_prompt", None) or "").strip()
                    _t2i_for_db = _gp if _gp else stored_prompt
                    
                    return (KeyframeVersion(
                        shot_number=shot_number,
                        is_bridge=shot.is_bridge,
                        keyframe_url=structured_response.image_url,
                        t2i_prompt=_t2i_for_db,  # 优先存工具实际使用的 prompt（含 instruction 合并后）
                        provider=structured_response.provider or provider_str,
                        success=True,
                        error_msg=None,
                        audio_segment_ids=shot.audio_segment_ids,
                        detailed_shot_id=shot.uuid,
                        seed=structured_response.seed,
                        aspect_ratio=structured_response.aspect_ratio or (aspect_ratio_enum.value if aspect_ratio_enum else None),
                        resolution=structured_response.resolution or (resolution_enum.value if resolution_enum else None),
                        model=structured_response.model or (model_enum.value if model_enum else None),  # model_enum 来自 tools_info.tool_type
                        reference_image_urls=display_ref_urls if display_ref_urls else None,
                        frame_index=frame_index,
                        ai_messages_json=ai_messages_json,  # ⭐ 添加 ai_messages
                        image_generation_tool=user_option.image_generation_tool.value if user_option and getattr(user_option, "image_generation_tool", None) else None,
                        image_tool_metrics=structured_response.image_tool_metrics,
                        tool_duration_sec=structured_response.tool_duration_sec,
                        tool_cost=structured_response.tool_cost,
                        applied_skill_ids=structured_response.applied_skill_ids,
                        constraint_coverage=structured_response.constraint_coverage,
                        final_prompt=structured_response.final_prompt or _t2i_for_db,
                    ), all_messages)
                else:
                    # 优先取 LLM 生成的脱敏 error_msg（前端展示用），兜底用 message
                    error_msg = (
                        (structured_response.error_msg or structured_response.message)
                        if structured_response and (structured_response.error_msg or structured_response.message)
                        else "关键帧生成失败"
                    )
                    logger.error(f"❌ 镜头{shot_number}({frame_desc}): 关键帧生成失败 - {error_msg}")
                    
                    return (KeyframeVersion(
                        shot_number=shot_number,
                        is_bridge=shot.is_bridge,
                        keyframe_url="",
                        t2i_prompt=stored_prompt,
                        provider=provider_str,
                        success=False,
                        error_msg=error_msg,
                        raw_error_msg=structured_response.raw_error_msg if structured_response else None,
                        audio_segment_ids=shot.audio_segment_ids,
                        detailed_shot_id=shot.uuid,
                        aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
                        resolution=resolution_enum.value if resolution_enum else None,
                        model=model_enum.value if model_enum else None,
                        reference_image_urls=display_ref_urls if display_ref_urls else None,
                        frame_index=frame_index,
                        ai_messages_json=ai_messages_json,  # ⭐ 添加 ai_messages（即使失败也保存）
                        image_generation_tool=user_option.image_generation_tool.value if user_option and getattr(user_option, "image_generation_tool", None) else None,
                        image_tool_metrics=structured_response.image_tool_metrics,
                        tool_duration_sec=structured_response.tool_duration_sec,
                        tool_cost=structured_response.tool_cost,
                    ), all_messages)
                            
            except Exception as e:
                error_msg = f"关键帧生成异常 - {str(e)}"
                logger.error(f"❌ 镜头{shot_number}({frame_desc}): 关键帧生成异常 - {error_msg}")
                
                # ⭐ 使用提前提取的字段创建错误结果（参考 _generate_character_image_with_llm）
                # 异常情况下，可能没有 messages，返回空列表
                return (KeyframeVersion(
                    shot_number=shot_number,
                    is_bridge=shot.is_bridge,
                    keyframe_url="",
                    t2i_prompt=stored_prompt,  # ⭐ 存储最终执行的prompt
                    provider=provider_str,
                    success=False,
                    error_msg=error_msg,
                    raw_error_msg=str(e),
                    audio_segment_ids=shot.audio_segment_ids,
                    detailed_shot_id=shot.uuid,
                    aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
                    resolution=resolution_enum.value if resolution_enum else None,
                    model=model_enum.value if model_enum else None,
                    reference_image_urls=display_ref_urls if display_ref_urls else None,
                    frame_index=frame_index,
                    ai_messages_json=None,  # 异常情况下没有 messages
                    image_generation_tool=user_option.image_generation_tool.value if user_option and getattr(user_option, "image_generation_tool", None) else None,
                    image_tool_metrics=None,
                    tool_duration_sec=None,
                    tool_cost=None,
                ), [])
    
    # 创建shot映射（按shot_number查找）
    shot_map = {shot.shot_number: shot for shot in shots_batch}
    
    # ⭐ 并行执行所有关键帧生成（直接遍历prompts，支持首尾帧）
    tasks = []
    for prompt_result in prompts:
        shot = shot_map.get(prompt_result.shot_number)
        if not shot:
            logger.warning(f"⚠️ 未找到镜头{prompt_result.shot_number}的shot数据，跳过")
            continue
        
        task = generate_single_keyframe_with_semaphore(shot, prompt_result)
        tasks.append(task)
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 协作式取消：本批若因取消而中断，及时抛出退出节点（外层 gather 无 return_exceptions，会向上传播至 task_worker 落 CANCELLED）
    await raise_if_cancelled()
    
    # 处理结果（BaseException 兜底：取消时子任务返回的 CancelledError 不是 Exception，避免按成功结果取值崩溃）
    keyframe_versions = []
    all_messages = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            # 异常情况 - 获取对应的 prompt
            prompt_result = prompts[i] if i < len(prompts) else KeyframePromptResult(shot_number=0, t2i_prompt="", frame_index=0)
            shot = shot_map.get(prompt_result.shot_number)
            
            frame_desc = "首帧" if prompt_result.frame_index == 0 else ("尾帧" if prompt_result.frame_index == -1 else f"中间帧{prompt_result.frame_index}")
            logger.error(f"❌ 镜头{prompt_result.shot_number}({frame_desc}): 执行异常 - {result}")
            
            # ⭐ 使用 final_prompt（如果存在）或 t2i_prompt（向后兼容）
            stored_prompt = prompt_result.final_prompt if prompt_result.final_prompt else prompt_result.t2i_prompt
            
            # ⭐ 提取字段用于错误结果（与正常路径一致：从 tools_info 取 model，不再用 get_nano_banana_model_for_tool）
            from ....models.tool_enums import ToolMode, AspectRatio, Resolution, DefaultValues
            
            # 错误处理时不需要查询融合图，使用同步逻辑
            error_used_image_urls = []
            if shot and shot.character_ids and character_images:
                for char_id in shot.character_ids:
                    if char_id in character_images:
                        image_url = character_images[char_id]
                        if isinstance(image_url, str) and image_url:
                            error_used_image_urls.append(image_url)
                        else:
                            from ....models.video_state import CharacterImageInfo
                            if isinstance(image_url, CharacterImageInfo):
                                img_url = image_url.get_image_for_keyframe(prefer_multiview=False)
                                if img_url:
                                    error_used_image_urls.append(img_url)
                            # ✅ 如果已经是字符串，直接使用（防御性编程）
            error_mode = ToolMode.I2I if error_used_image_urls else ToolMode.T2I
            from .per_shot_generation_routing_service import resolve_user_option_for_shot

            _err_uo = resolve_user_option_for_shot(user_option, shot, "image")
            error_tools_info = ToolService.get_image_generation_tools(_err_uo, mode=error_mode)
            error_provider_str = error_tools_info.primary_tool_name if error_tools_info else ""
            error_aspect_ratio_enum = (
                user_option.aspect_ratio
                if user_option and user_option.aspect_ratio
                else DefaultValues.IMAGE_ASPECT_RATIO
            )
            error_resolution_enum = (
                user_option.resolution
                if user_option and user_option.resolution
                else DefaultValues.IMAGE_RESOLUTION
            )
            error_model_enum = error_tools_info.tool_type if error_tools_info and error_tools_info.tools else None
            
            keyframe_version = KeyframeVersion(
                shot_number=prompt_result.shot_number,
                is_bridge=shot.is_bridge if shot else False,
                keyframe_url="",
                t2i_prompt=stored_prompt,  # ⭐ 存储最终执行的prompt
                provider=error_provider_str,
                success=False,
                error_msg=str(result),
                raw_error_msg=str(result),
                audio_segment_ids=shot.audio_segment_ids if shot else None,
                detailed_shot_id=shot.uuid if shot else "",
                aspect_ratio=error_aspect_ratio_enum.value if error_aspect_ratio_enum else None,
                resolution=error_resolution_enum.value if error_resolution_enum else None,
                model=error_model_enum.value if error_model_enum else None,
                reference_image_urls=error_used_image_urls if error_used_image_urls else None,
                frame_index=prompt_result.frame_index,
                ai_messages_json=None,  # 异常情况下没有 messages
                image_generation_tool=error_tools_info.user_option_tool if error_tools_info else None,
            )
            messages = []  # 异常情况下没有 messages
        else:
            keyframe_version, messages = result  # 解包 (KeyframeVersion, List[BaseMessage])
            all_messages.extend(messages)
        
        keyframe_versions.append(keyframe_version)
    
    logger.info(f"🎨 并发执行完成：{len([v for v in keyframe_versions if v.success])}个成功，{len([v for v in keyframe_versions if not v.success])}个失败")
    return all_messages, keyframe_versions


async def evaluate_and_fix_batch_keyframe_prompts(
    prompts: List[KeyframePromptResult],
    shots_batch: List[DetailedShot],
    user_option: Optional[UserOption] = None,
    user_input: str = "",
    detected_language: Optional[str] = None,
) -> Tuple[List[BaseMessage], List[KeyframePromptResult]]:
    """第二步：评估并修正关键帧 prompts（deep-agent + keyframe-eval-director）。"""
    logger.info(f"🔍 第二步：评估并修正{len(prompts)}个关键帧 prompts（deep-agent）")

    empty_prompts = [p for p in prompts if not p.t2i_prompt.strip()]
    if empty_prompts:
        logger.warning(f"⚠️ 发现{len(empty_prompts)}个空prompt，跳过评估修正")
        return [], prompts

    try:
        import uuid as _uuid
        from app.services.agent.video.keyframe_eval_stage import (
            evaluate_keyframe_prompts_via_deep_agent,
            export_keyframe_eval_inputs,
        )

        prompt_rows: List[Dict[str, Any]] = []
        image_urls: List[str] = []
        for idx, p in enumerate(prompts):
            shot = next((s for s in shots_batch if s.shot_number == p.shot_number), None)
            ref_urls = p.all_ref_urls
            frame_label = (
                "首帧" if p.frame_index == 0 else ("尾帧" if p.frame_index == -1 else f"中间帧{p.frame_index}")
            )
            prompt_rows.append({
                "prompt_index": idx,
                "shot_number": p.shot_number,
                "frame_label": frame_label,
                "frame_index": p.frame_index,
                "t2i_prompt": p.t2i_prompt,
                "scene_description": shot.scene_description if shot else "",
                "ref_count": len(ref_urls),
            })
            image_urls.extend([u for u in ref_urls if u])

        max_reference_images = ToolService.get_max_reference_images_for_keyframe(user_option)
        tid = f"keval_{_uuid.uuid4().hex[:8]}"
        rid = f"run_{_uuid.uuid4().hex[:8]}"
        paths = export_keyframe_eval_inputs(
            thread_id=tid,
            run_id=rid,
            batch_id="b0",
            prompts=prompt_rows,
            keyframe_prompt_length_range=str(KEYFRAME_PROMPT_LENGTH_RANGE),
            max_reference_images=max_reference_images,
            detected_language=detected_language,
            user_input=user_input or "",
            image_urls=image_urls,
        )
        art, da_msgs = await evaluate_keyframe_prompts_via_deep_agent(
            thread_id=tid,
            run_id=rid,
            input_paths=paths,
            detected_language=detected_language,
        )
        all_messages = list(da_msgs or [])
        fix_result = art

        fixed_prompts = []
        fixes_applied = 0

        for original_prompt in prompts:
            fix_data = next(
                (
                    f
                    for f in fix_result.fixes
                    if f.shot_number == original_prompt.shot_number
                    and f.frame_index == original_prompt.frame_index
                ),
                None,
            )

            if fix_data and fix_data.needs_fix:
                fixed_prompt = KeyframePromptResult(
                    shot_number=original_prompt.shot_number,
                    t2i_prompt=fix_data.fixed_prompt,
                    frame_index=original_prompt.frame_index,
                    ref_images=list(original_prompt.ref_images),
                )
                fixed_prompts.append(fixed_prompt)
                fixes_applied += 1

                frame_desc = (
                    "首帧"
                    if original_prompt.frame_index == 0
                    else ("尾帧" if original_prompt.frame_index == -1 else f"中间帧{original_prompt.frame_index}")
                )
                logger.info(f"🔧 镜头{original_prompt.shot_number}({frame_desc}): 修正应用")
                logger.info(f"   问题: {', '.join(fix_data.issues_found)}")
                logger.info(f"   原始: {original_prompt.t2i_prompt[:100]}...")
                logger.info(f"   修正: {fix_data.fixed_prompt[:100]}...")
            else:
                fixed_prompts.append(original_prompt)

        logger.info(f"✅ Prompt修正完成：{fixes_applied}/{len(prompts)}个prompt被修正")
        _kf_trace_parts: List[str] = []
        for original_prompt in prompts:
            fix_data = next(
                (
                    f
                    for f in fix_result.fixes
                    if f.shot_number == original_prompt.shot_number
                    and f.frame_index == original_prompt.frame_index
                ),
                None,
            )
            _kf_trace_parts.append(
                f"\n=== shot {original_prompt.shot_number} frame_index={original_prompt.frame_index} ==="
            )
            _kf_trace_parts.append(f"original_t2i:\n{original_prompt.t2i_prompt}")
            if fix_data is not None:
                _kf_trace_parts.append(f"needs_fix={fix_data.needs_fix} issues={fix_data.issues_found}")
                _kf_trace_parts.append(f"fixed_t2i:\n{fix_data.fixed_prompt}")
            else:
                _kf_trace_parts.append("(no matching fix row; prompt unchanged)")
        log_video_spin_prompt_trace(
            "keyframe_t2i_eval_fix_outcome",
            text="".join(_kf_trace_parts),
            log_context={"fixes_applied": fixes_applied, "prompts_count": len(prompts)},
        )
        return all_messages, fixed_prompts
        
    except Exception as e:
        logger.error(f"❌ Prompt修正过程出错: {e}")
        import traceback
        traceback.print_exc()
        logger.info("📝 使用原始prompts继续执行")
        return [], prompts


async def generate_batch_keyframes(
    shots_batch: List[DetailedShot],
    character_images: Dict[str, CharacterImageInfo],
    prev_shot: Optional[DetailedShot] = None,
    next_shot: Optional[DetailedShot] = None,
    user_option: Optional[UserOption] = None,
    shared_semaphore: Optional[asyncio.Semaphore] = None,
    user_input: str = "",
    user_id: Optional[str] = None,
    send_event_func: Optional[Any] = None,
    conversation_id: Optional[str] = None,
    content_category: Optional[str] = None,
    detected_language: Optional[str] = None,
    hidden_style_description: Optional[str] = None,
    pre_fetched_fusions: Optional[List[Any]] = None,
    skip_consistency_check: bool = False,
    reuse_ref_image_urls: Optional[List[str]] = None,
) -> Tuple[List[BaseMessage], List[KeyframeVersion]]:
    """批量生成关键帧的异步函数（两轮流程：首帧 -> 尾帧）
    
    各步内部按需 load_prompt 获取 prompt+llm，不依赖传入的 llm。
    
    Args:
        shots_batch: 本批镜头列表
        character_images: 角色图片信息字典，key=character_id, value=CharacterImageInfo（主图/多视角/融合图）。
                          与 _build_character_images_dict 返回的同一结构，勿传 Dict[str, str]。
        prev_shot / next_shot: 前后文镜头（可选）
        user_option: 用户选项
        shared_semaphore: 并发控制（可选）
        user_input: 用户输入
        user_id: 用户ID（获取多视角图等时使用）
        character_images 内已含 profile，无需再传 characters_data
        send_event_func: 可选，用于发送 IMAGE_MODEL_SWITCHED 等事件
        conversation_id: 可选，与 send_event_func 配合使用
    """
    logger.info(f"🎨 批量生成{len(shots_batch)}个关键帧（两轮流程）")
    
    enable_continuity_mode = user_option and user_option.enable_continuity_mode
    
    # ========== 第一轮：生成首帧 ==========
    logger.info(f"🎬 第一轮：开始生成{len(shots_batch)}个镜头的首帧...")
    
    # 第一步：生成首帧prompts
    first_frame_prompt_messages, first_frame_prompts = await generate_batch_keyframe_prompts(
        shots_batch=shots_batch,
        character_images=character_images,
        prev_shot=prev_shot,
        next_shot=next_shot,
        user_option=user_option,
        user_input=user_input,
        generate_first_frame=True,
        generate_last_frame=False,
        first_frame_images=None,
        first_frame_prompts=None,
        user_id=user_id,
        content_category=content_category,
        detected_language=detected_language,
        hidden_style_description=hidden_style_description,
        pre_fetched_fusions=pre_fetched_fusions,
        reuse_ref_image_urls=reuse_ref_image_urls,
    )
    # 第二步：评估并修正首帧prompts
    first_frame_fix_messages, fixed_first_frame_prompts = await evaluate_and_fix_batch_keyframe_prompts(
        prompts=first_frame_prompts,
        shots_batch=shots_batch,
        user_option=user_option,
        user_input=user_input,
        detected_language=detected_language,
    )
    
    # 第二步半：确定 final_prompt（pipeline 路径直接用 ref_images；regenerate 路径获取参考图填充 ref_images）
    resolved_first_frame_prompts = await resolve_keyframe_prompt_reference_images(
        shots_batch=shots_batch,
        character_images=character_images,
        prompts=fixed_first_frame_prompts,
        user_option=user_option,
        user_id=user_id,
        reuse_ref_image_urls=reuse_ref_image_urls,
    )
    
    # 第三步：执行首帧图片生成（纯 call tool，使用 prompt.final_*）
    first_frame_messages, first_frame_keyframe_versions = await execute_batch_keyframe_generation(
        shots_batch=shots_batch,
        character_images=character_images,
        prompts=resolved_first_frame_prompts,
        user_option=user_option,
        shared_semaphore=shared_semaphore,
        user_input=user_input,
        first_frame_images=None,
        user_id=user_id,
        send_event_func=send_event_func,
        conversation_id=conversation_id,
        detected_language=detected_language,
        skip_consistency_check=skip_consistency_check,
        reuse_ref_image_urls=reuse_ref_image_urls,
    )
    
    # 合并首帧的 messages
    first_frame_prompt_messages = first_frame_prompt_messages + first_frame_fix_messages + first_frame_messages
    
    logger.info(f"✅ 第一轮完成：生成了{len(first_frame_keyframe_versions)}个首帧")
    
    # ========== 第二轮：生成尾帧 ==========
    last_frame_messages = []
    last_frame_keyframe_versions = []
    
    if enable_continuity_mode and first_frame_keyframe_versions:
        logger.info(f"🎬 第二轮：开始生成尾帧（基于首帧 + 多视角图）...")
        
        # ⭐ 收集首帧图片和首帧prompt
        first_frame_images_dict = {}
        first_frame_prompts_dict = {}
        
        for kf, prompt in zip(first_frame_keyframe_versions, fixed_first_frame_prompts):
            if kf.frame_index == 0 and kf.keyframe_url:
                first_frame_images_dict[kf.shot_number] = kf.keyframe_url
                first_frame_prompts_dict[kf.shot_number] = prompt.t2i_prompt
        
        logger.info(f"📸 收集到{len(first_frame_images_dict)}个首帧图片和prompt")
        
        # 第一步：生成尾帧prompts（参考首帧图片 + 首帧prompt + 多视角图）
        last_frame_prompt_messages, last_frame_prompts = await generate_batch_keyframe_prompts(
            shots_batch=shots_batch,
            character_images=character_images,
            prev_shot=prev_shot,
            next_shot=next_shot,
            user_option=user_option,
            user_input=user_input,
            generate_first_frame=False,
            generate_last_frame=True,
            first_frame_images=first_frame_images_dict,
            first_frame_prompts=first_frame_prompts_dict,
            user_id=user_id,
            content_category=content_category,
            detected_language=detected_language,
            hidden_style_description=hidden_style_description,
            pre_fetched_fusions=pre_fetched_fusions,
        )
        # 第二步：评估并修正尾帧prompts
        last_frame_fix_messages, fixed_last_frame_prompts = await evaluate_and_fix_batch_keyframe_prompts(
            prompts=last_frame_prompts,
            shots_batch=shots_batch,
            user_option=user_option,
            user_input=user_input,
            detected_language=detected_language,
        )
        
        # 第二步半：确定 final_prompt（尾帧，ref_images 含首帧 URL）
        resolved_last_frame_prompts = await resolve_keyframe_prompt_reference_images(
            shots_batch=shots_batch,
            character_images=character_images,
            prompts=fixed_last_frame_prompts,
            user_option=user_option,
            user_id=user_id,
        )
        
        # 第三步：执行尾帧图片生成（纯 call tool，使用 prompt.final_*）
        last_frame_gen_messages, last_frame_keyframe_versions = await execute_batch_keyframe_generation(
            shots_batch=shots_batch,
            character_images=character_images,
            prompts=resolved_last_frame_prompts,
            user_option=user_option,
            shared_semaphore=shared_semaphore,
            user_input=user_input,
            first_frame_images=first_frame_images_dict,
            user_id=user_id,
            send_event_func=send_event_func,
            conversation_id=conversation_id,
            detected_language=detected_language,
            skip_consistency_check=skip_consistency_check,
        )
        
        # 合并尾帧的 messages
        last_frame_messages = last_frame_prompt_messages + last_frame_fix_messages + last_frame_gen_messages
        logger.info(f"✅ 第二轮完成：生成了{len(last_frame_keyframe_versions)}个尾帧")
    
    # 合并结果
    all_messages = first_frame_prompt_messages + last_frame_messages
    all_keyframe_versions = first_frame_keyframe_versions + last_frame_keyframe_versions
    
    logger.info(f"🎬 两轮流程完成：总共生成{len(all_keyframe_versions)}个关键帧")
    return all_messages, all_keyframe_versions




async def keyframe_generation_node(
    state: VideoAgentState, 
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """关键帧生成节点（批量处理，支持上下文）"""
    try:
        from .music_generation_service import should_skip_keyframe_pipeline_from_state

        if should_skip_keyframe_pipeline_from_state(state):
            logger.info(
                "🖼️ 跳过关键帧生成（shot_workflow_mode=reference_t2v，走 Seedance2 T2V+参考图）"
            )
            return {"keyframe_uuids": [], "messages": []}

        # 获取必要的UUID
        story_outline_uuid = state.get("story_outline_uuid")
        shot_uuids = state.get("shot_uuids", [])
        user_input_data = state.get("user_input_data")
        user_input = user_input_data.user_input if user_input_data else ""
        
        if not story_outline_uuid:
            raise BusinessException(
                BusinessExceptionCode.VIDEO_ANALYSIS_UUID_MISSING,
                "缺少故事梗概UUID"
            )
        
        if not shot_uuids:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少详细镜头数据，无法生成关键帧"
            )
        
        # ✅ 修复：使用asyncpg CRUD，不需要数据库连接
        from ....models.video_state import CharacterImageInfo
        from .character_fusion_service import get_model_limit
        
        # 从数据库获取数据（使用asyncpg CRUD）
        shots_data = await get_detailed_shots_from_db(shot_uuids)
        
        logger.info(f"🖼️ 开始基于{len(shots_data)}个详细镜头生成关键帧")
        
        # 收集所有镜头需要的角色ID
        all_character_ids = set()
        for shot in shots_data:
            all_character_ids.update(shot.character_ids)
        
        # 只获取需要的角色数据（含 profile、version_id，统一在 CharacterImageInfo 内）
        character_images = await _build_character_images_dict(all_character_ids, state["user_id"])

        # 融合图一次性拉取，避免每个 batch/shot 重复查库
        pre_fetched_fusions = []
        if all_character_ids:
            try:
                from ....crud.video.video_character import get_character_fusion_images_by_character_ids
                pre_fetched_fusions = await get_character_fusion_images_by_character_ids(character_ids=list(all_character_ids))
            except Exception as e:
                logger.warning(f"预取融合图失败，各 shot 将按需查询: {e}")

        _node_input = {
            "all_character_ids": list(all_character_ids),
            "character_images_count": len(character_images),
            "character_images_summary": {
                cid: {
                    "main_image_url": (ci.main_image_url[:80] + "..." if (getattr(ci, "main_image_url", None) and len(ci.main_image_url or "") > 80) else getattr(ci, "main_image_url", None)),
                    "has_multiview": bool(getattr(ci, "multiview_url", None)),
                    "has_profile": bool(getattr(ci, "profile", None)),
                }
                for cid, ci in character_images.items()
            },
            "pre_fetched_fusions_count": len(pre_fetched_fusions),
            "shots_data_summary": [{"shot_number": s.shot_number, "character_ids": s.character_ids} for s in shots_data],
        }
        try:
            logger.info("[keyframe_input] keyframe_generation_node input (for mock): %s", json.dumps(_keyframe_input_to_jsonable(_node_input), ensure_ascii=False, default=str))
        except Exception as _e:
            logger.debug("keyframe_input node json skip: %s", _e)

        # 获取模型限制
        user_input_data = state.get("user_input_data")
        user_option = user_input_data.user_option if user_input_data else None
        model_limit = get_model_limit(user_option)
        
        # 并发批量处理策略：将所有镜头分批，然后并发处理所有批次
        total_shots = len(shots_data)
        
        # video_analysis 已写入 user_option.content_category（enum），批次里传 str 用 .value
        content_category_for_keyframe = state["user_input_data"].user_option.content_category.value

        # 精选风格描述（电影写实等从 analysis 带出）
        hidden_style_description = None
        analysis_uuid = state.get("analysis_uuid")
        if analysis_uuid:
            try:
                from ....services.agent.utils.database_utils import get_video_analysis_from_db
                _analysis = await get_video_analysis_from_db(analysis_uuid)
                hidden_style_description = _analysis.hidden_style_description
            except Exception:
                pass

        # 从配置中获取批处理大小
        from ..utils.prompt_utils import CONCURRENCY_LIMITS
        batch_size = CONCURRENCY_LIMITS.get("keyframe_batch_size", 4)
        
        logger.info(f"🖼️ 开始并发处理{total_shots}个镜头的关键帧，每批{batch_size}个")
        
        # 🎯 报告初始进度（0%）；带 thread_id 供前端拉 keyframes 接口展示部分结果
        await send_event_func(
            event_type=MessageType.KEYFRAME_GENERATION_PROGRESS,
            conversation_id=state["conversation_id"],
            extra_data={
                "completed": 0,
                "total": total_shots,
                "thread_id": state.get("thread_id"),
            },
            hidden=True,
            save_to_db=False
        )
        
        # ♻️ 断点续跑幂等：跳过本 outline 下「所需帧已全部成功」的镜头，避免取消/恢复后重复生成
        already_done_keyframe_uuids: List[str] = []
        pending_shots_data = shots_data
        try:
            _user_option_for_skip = user_input_data.user_option if user_input_data else None
            _enable_continuity = bool(_user_option_for_skip and _user_option_for_skip.enable_continuity_mode)
            _required_frames = {0, -1} if _enable_continuity else {0}
            _done_by_shot = await get_completed_keyframe_uuids_by_shot(story_outline_uuid, _required_frames)
            if _done_by_shot:
                _pending = []
                for _shot in shots_data:
                    _shot_uuid = getattr(_shot, "uuid", None)
                    if _shot_uuid and _shot_uuid in _done_by_shot:
                        already_done_keyframe_uuids.extend(_done_by_shot[_shot_uuid])
                    else:
                        _pending.append(_shot)
                pending_shots_data = _pending
                if len(pending_shots_data) < len(shots_data):
                    logger.info(f"♻️ 关键帧断点续跑：跳过{len(shots_data) - len(pending_shots_data)}个已完成镜头，剩余{len(pending_shots_data)}个待生成")
        except Exception as _skip_err:
            logger.warning(f"关键帧 skip-if-done 检查失败，按全量生成: {_skip_err}")
            already_done_keyframe_uuids = []
            pending_shots_data = shots_data
        already_done_shot_count = len(shots_data) - len(pending_shots_data)

        # 将所有镜头分批
        batches = []
        for i in range(0, len(pending_shots_data), batch_size):
            current_batch = pending_shots_data[i:i + batch_size]
            
            # 获取上下文镜头（前一个和后一个）
            prev_shot: Optional[DetailedShot] = None
            next_shot: Optional[DetailedShot] = None
            
            # 前一个镜头（用于第一个镜头的连贯性）
            if i > 0:
                prev_shot = pending_shots_data[i - 1]
            
            # 后一个镜头（用于最后一个镜头的连贯性）
            if i + batch_size < len(pending_shots_data):
                next_shot = pending_shots_data[i + batch_size]
            
            batches.append({
                'shots': current_batch,
                'prev_shot': prev_shot,
                'next_shot': next_shot,
                'batch_index': i // batch_size,
                'content_category': content_category_for_keyframe,
                'detected_language': state.get("detected_language"),
                'hidden_style_description': hidden_style_description,
                'pre_fetched_fusions': pre_fetched_fusions,
            })
        
        logger.info(f"🚀 创建了{len(batches)}个批次，开始并发处理")
        
        # 创建共享的信号量用于跨批次并发控制
        from ..utils.prompt_utils import get_concurrency_limit, add_random_delay
        shared_keyframe_semaphore_limit = get_concurrency_limit("keyframe_generation")
        shared_keyframe_semaphore = asyncio.Semaphore(shared_keyframe_semaphore_limit)
        
        logger.info(f"🎨 使用共享semaphore控制总并发：最多{shared_keyframe_semaphore_limit}个关键帧同时生成")
        
        # 创建原子计数器用于跟踪进度（已完成的镜头计入起点，进度条不回退）
        completed_shots = already_done_shot_count
        progress_lock = asyncio.Lock()
        
        # 定义单个批次处理函数
        async def process_batch(batch_info):
            # 添加随机延迟避免并发请求过于集中
            await add_random_delay()
            
            # 批次处理不再需要外层 semaphore，并发控制由共享的 shared_keyframe_semaphore 完成
            from ....models.database import AsyncSessionLocal
            
            batch_index = batch_info['batch_index']
            current_shots_batch = batch_info['shots']
            prev_shot = batch_info['prev_shot']
            next_shot = batch_info['next_shot']
            
            logger.info(f"🎨 批次{batch_index + 1}: 处理{len(current_shots_batch)}个镜头")
            
            # 使用批量生成方法
            user_option: Optional[UserOption] = user_input_data.user_option
            
            # 调用批量生成函数，传入共享的 semaphore
            batch_content_category = batch_info.get("content_category")
            batch_detected_language = batch_info.get("detected_language")
            batch_results = await generate_batch_keyframes(
                shots_batch=current_shots_batch,
                character_images=character_images,
                prev_shot=prev_shot,
                next_shot=next_shot,
                user_option=user_option,
                shared_semaphore=shared_keyframe_semaphore,
                user_input=user_input,
                user_id=state["user_id"],
                send_event_func=send_event_func,
                conversation_id=state.get("conversation_id"),
                content_category=batch_content_category,
                detected_language=batch_detected_language,
                hidden_style_description=batch_info.get("hidden_style_description"),
                pre_fetched_fusions=batch_info.get("pre_fetched_fusions"),
            )
            
            keyframe_messages, keyframe_versions = batch_results
            batch_keyframe_uuids = []
            
            # ⭐ 遍历所有 keyframe_versions（包括首尾帧）
            for keyframe_version in keyframe_versions:
                # 通过 shot_number 找到对应的 shot
                shot = next((s for s in current_shots_batch if s.shot_number == keyframe_version.shot_number), None)
                if not shot:
                    logger.warning(f"⚠️ 未找到镜头{keyframe_version.shot_number}的shot数据，跳过保存")
                    continue
                
                # 获取该镜头使用的角色版本ID列表（从 CharacterImageInfo.version_id）
                character_version_ids = []
                if shot.character_ids:
                    for char_id in shot.character_ids:
                        ci = character_images.get(char_id)
                        if ci and getattr(ci, 'version_id', None):
                            character_version_ids.append(ci.version_id)
                
                # 保存关键帧到数据库（使用asyncpg CRUD）
                keyframe_uuid = await save_keyframe_to_db(
                    keyframe_version=keyframe_version,
                    shot=shot,
                    story_outline_uuid=story_outline_uuid,
                    conversation_id=str(state["conversation_id"]),
                    thread_id=str(state["thread_id"]),
                    run_id=state["run_id"],
                    user_id=state["user_id"],
                    character_version_ids=character_version_ids if character_version_ids else None
                )
                    
                batch_keyframe_uuids.append(keyframe_uuid)
                
                frame_desc = "首帧" if keyframe_version.frame_index == 0 else ("尾帧" if keyframe_version.frame_index == -1 else f"中间帧{keyframe_version.frame_index}")
                if keyframe_version.success:
                    logger.info(f"✅ 批次{batch_index + 1} 镜头{shot.shot_number}({frame_desc}) 关键帧生成成功: {keyframe_uuid}")
                else:
                    logger.warning(f"⚠️ 批次{batch_index + 1} 镜头{shot.shot_number}({frame_desc}) 关键帧生成失败: {keyframe_uuid}")
            
            # 🎯 本批次全部 keyframe_versions 处理完后，更新进度并返回（只执行一次）
            nonlocal completed_shots
            async with progress_lock:
                completed_shots += len(current_shots_batch)
                await send_event_func(
                    event_type=MessageType.KEYFRAME_GENERATION_PROGRESS,
                    conversation_id=state["conversation_id"],
                    extra_data={
                        "completed": completed_shots,
                        "total": total_shots,
                        "thread_id": state.get("thread_id"),
                    },
                    hidden=True,
                    save_to_db=False
                )
            
            return {
                'batch_index': batch_index,
                'keyframe_uuids': batch_keyframe_uuids,
                'keyframe_versions': keyframe_versions,
                'messages': keyframe_messages,
                'shots_count': len(current_shots_batch)
            }
        
        # 并发处理所有批次
        batch_tasks = [process_batch(batch_info) for batch_info in batches]
        batch_results = await asyncio.gather(*batch_tasks)
        
        # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
        await raise_if_cancelled()
        
        # 收集所有结果（已完成镜头的关键帧uuid一并带上，保证下游 video 节点拿到完整列表）
        all_keyframe_uuids = list(already_done_keyframe_uuids)
        all_keyframe_versions = []
        all_messages = []
        processed_shots = 0
        
        for result in batch_results:
            # 成功的批次
            all_keyframe_uuids.extend(result['keyframe_uuids'])
            all_keyframe_versions.extend(result['keyframe_versions'])
            all_messages.extend(result['messages'])
            processed_shots += result['shots_count']
            
            logger.info(f"✅ 批次{result['batch_index'] + 1}完成: {result['shots_count']}个镜头")
        
        logger.info(f"🎨 并发批量处理完成: 总共{len(batches)}个批次，{processed_shots}个镜头")
        
        # 使用LLM根据收集的messages生成完成消息
        from ....services.agent.utils.prompt_utils import generate_completion_message_stream
        
        detected_language = state.get("detected_language")
        user_message, completion_message = await generate_completion_message_stream(
            event_type=MessageType.KEYFRAMES_GENERATED,
            messages=all_messages,  # 传入收集的react agent messages
            send_event_func=send_event_func,
            conversation_id=state.get("conversation_id"),
            lang=detected_language
        )
        
        # 收集completion message
        if completion_message:
            all_messages.append(completion_message)
        
        logger.info(f"📝 生成的用户消息: {user_message}")
        
        # 发送关键帧生成完成事件
        await send_event_func(
            conversation_id=state["conversation_id"],
            event_type=MessageType.KEYFRAMES_GENERATED,
            message=user_message,  # 使用LLM生成的消息
            extra_data={
                "keyframe_uuids": all_keyframe_uuids,
                "total_shots": total_shots,
                "keyframe_count": len(all_keyframe_uuids),
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id")
            }
        )
        
        # 失败兜底：逐帧检测，有失败即暂停并在对话告知（官方原因，不泄密）
        failed_items = [
            {
                "index": getattr(v, "shot_number", None),
                "category": classify_failure(getattr(v, "error_msg", None)).value,
                "user_msg": getattr(v, "error_msg", None),
            }
            for v in all_keyframe_versions
            if not getattr(v, "success", True)
        ]
        stage_failure = detect_stage_failure(
            "keyframe",
            total=len(all_keyframe_versions),
            failed_items=failed_items,
            lang=detected_language,
        )
        if stage_failure:
            await emit_stage_failure_event(send_event_func, stage_failure, conversation_id=state.get("conversation_id"))

        return {
            "keyframe_uuids": all_keyframe_uuids,
            "keyframe_versions": all_keyframe_versions,
            "messages": all_messages,
            "stage_failure": stage_failure,
        }
        
    except BusinessException as e:
        logger.error(f"keyframe_generation_node:业务异常 - {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"keyframe_generation_node:未知异常 - {str(e)}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"关键帧生成失败: {str(e)}"
        )
