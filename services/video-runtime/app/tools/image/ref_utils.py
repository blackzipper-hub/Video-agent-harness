"""
图像参考图工具：按模型能力截断、人物优先排序后保人物。
供 image_tool_wrapper、nano_banana、seedream 共用。
"""
import logging
from typing import Dict, List, Optional

from app.models.tool_enums import ToolType

logger = logging.getLogger(__name__)

# 各模型支持的参考图数量上限（与 ToolService.get_max_reference_images_for_keyframe 对齐）
_MAX_REFERENCE_IMAGES_BY_MODEL = {
    ToolType.GEMINI_3_PRO_IMAGE_PREVIEW: 5,
    ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW: 4,  # 文档 4 张
    ToolType.GEMINI_2_5_FLASH_IMAGE: 3,
    ToolType.SEEDREAM_V4_5: 6,
    ToolType.GPT_IMAGE_2: 6,
}

# 各图像模型对场所(location)参考图的支持。仅 GPT Image 2 实测能利用 location 图而不出"巨人"问题；
# 其他模型默认 False，避免远景下人物比例失真。新增图像模型必须在此显式登记，否则按 False 兜底。
_SUPPORTS_VENUE_REF_IMAGE_BY_MODEL: Dict[ToolType, bool] = {
    ToolType.GEMINI_2_5_FLASH_IMAGE: False,
    ToolType.GEMINI_3_PRO_IMAGE_PREVIEW: False,
    ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW: False,
    ToolType.SEEDREAM_V4_5: False,
    ToolType.GPT_IMAGE_2: True,
}


def get_max_reference_images_for_model(tool_type: ToolType) -> int:
    """按 ToolType 返回该模型支持的参考图数量上限。用于 I2I 调用前截断。"""
    return _MAX_REFERENCE_IMAGES_BY_MODEL.get(tool_type, 3)


def supports_venue_ref_image(tool_type: Optional[ToolType]) -> bool:
    """按 ToolType 返回该图像模型是否支持把 location(场所) 参考图喂入而不出现远景"巨人"比例失真。
    未登记的模型按 False 保守处理。新增模型务必同步在 _SUPPORTS_VENUE_REF_IMAGE_BY_MODEL 中登记。"""
    if tool_type is None:
        return False
    return _SUPPORTS_VENUE_REF_IMAGE_BY_MODEL.get(tool_type, False)


def truncate_reference_urls_for_model(
    urls: Optional[List[str]], tool_type: ToolType
) -> Optional[List[str]]:
    """按模型能力截断参考图列表（上游应已人物优先排序，截断即保留前 N 张）。"""
    if not urls:
        return urls
    max_n = get_max_reference_images_for_model(tool_type)
    if len(urls) <= max_n:
        return urls
    logger.info(f"📐 按模型 {tool_type.value} 上限截断参考图: {len(urls)} -> {max_n}")
    return urls[:max_n]
