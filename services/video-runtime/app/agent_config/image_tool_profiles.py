"""
Per-tool 图像工具配置（占位，P2 从 user_options.get_tool_capabilities 数据化迁入）。

键：ImageGenerationTool
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from app.models.user_options import ImageGenerationTool


@dataclass(frozen=True)
class ImageToolPlanningProfile:
    """图像工具产品与能力展示（预留）。"""

    allowed_resolutions: Optional[List[str]] = None
    allowed_aspect_ratios: Optional[List[str]] = None


IMAGE_TOOL_PLANNING_PROFILES: Dict[ImageGenerationTool, ImageToolPlanningProfile] = {}


def get_image_tool_planning_profile(tool: ImageGenerationTool) -> ImageToolPlanningProfile:
    return IMAGE_TOOL_PLANNING_PROFILES.get(tool, ImageToolPlanningProfile())
