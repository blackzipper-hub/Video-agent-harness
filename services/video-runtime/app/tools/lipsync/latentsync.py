"""
唇形同步工具
支持多种唇形同步模型的视频生成
"""

import logging
from typing import Optional, List, TYPE_CHECKING
from app.tools.runtime import tool
from langsmith import traceable

if TYPE_CHECKING:
    from ...services.tool_service import ToolInfo

from app.llm.wavespeed_service import get_wavespeed_service
from app.models.image_result import VideoGenerationResult, VideoProvider
from app.utils.video_utils import get_audio_duration_from_url
from app.services.tool_service import ToolService
from app.models.tool_enums import ToolType, ToolName

logger = logging.getLogger(__name__)


@tool(ToolName.LIPSYNC)
@traceable(run_type='tool')
async def generate_lipsync_with_wavespeed(
    audio_url: str,
    video_url: str,
    model: str = "sync/lipsync-2-pro"
) -> str:
    """
    使用 WaveSpeed AI 生成唇形同步视频（Lipsync 2 Pro）
    
    Args:
        audio_url: 音频文件URL
        video_url: 视频文件URL
        model: 使用的模型，默认 sync/lipsync-2-pro
    
    Returns:
        生成结果的JSON字符串
    """
    try:
        logger.info(f"🎭 开始唇形同步生成: model={model}")
        
        # 调用核心实现函数
        result = await _generate_lipsync_with_wavespeed_impl(audio_url, video_url, model)
        
        # 返回JSON格式结果
        import json
        return json.dumps({
            "success": result.success,
            "video_url": result.video_url,
            "provider": result.provider if result.provider else None,
            "model": model,
            "message": result.message,
            "error_message": result.message if not result.success else None
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.error(f"🎭 唇形同步工具调用失败: {e}")
        import json
        return json.dumps({
            "success": False,
            "video_url": None,
            "provider": VideoProvider.WAVESPEED.value,
            "model": model,
            "message": f"唇形同步工具调用失败: {str(e)}",
            "error_message": f"唇形同步工具调用失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


async def _generate_lipsync_with_wavespeed_impl(
    audio_url: str,
    video_url: str,
    model: str = "sync/lipsync-2-pro"
) -> VideoGenerationResult:
    """
    WaveSpeed 唇形同步生成的核心实现（sync/lipsync-2-pro）
    
    Args:
        audio_url: 音频文件URL
        video_url: 视频文件URL
        model: 使用的模型
        
    Returns:
        VideoGenerationResult对象
    """
    try:
        logger.info(f"🎭 WaveSpeed 唇形同步开始: model={model}")
        logger.info(f"📹 视频URL: {video_url}")
        logger.info(f"🎵 音频URL: {audio_url}")
        
        if not audio_url or not video_url:
            return VideoGenerationResult.error_result(
                error_message="音频URL和视频URL都不能为空",
                provider=VideoProvider.WAVESPEED.value
            )
        
        supported_models = ["sync/lipsync-2-pro"]
        if model not in supported_models:
            return VideoGenerationResult.error_result(
                error_message=f"不支持的模型: {model}，支持的模型: {supported_models}",
                provider=VideoProvider.WAVESPEED.value
            )
        
        # 获取 WaveSpeed 服务
        wavespeed_service = get_wavespeed_service()
        
        # 调用唇形同步生成
        result = await wavespeed_service.generate_lipsync(
            audio_url=audio_url,
            video_url=video_url,
            model=model
        )
        
        if result.success:
            logger.info(f"✅ WaveSpeed 唇形同步生成成功")
            logger.info(f"🎬 生成的视频URL: {result.video_url}")
            # 按音频时长计费并记录到 LangSmith（与 suno/seedance/sora 等 tool 一致）
            audio_duration: float = 0.0
            duration_val = await get_audio_duration_from_url(audio_url)
            if duration_val and duration_val > 0:
                audio_duration = float(duration_val)
            cost = 0.0
            if audio_duration > 0:
                cost = ToolService.calculate_cost(ToolType.LIPSYNC_2_PRO, duration=audio_duration)
                if cost > 0:
                    ToolService.log_cost(cost)
                    logger.info(f"💰 Lipsync 计费: 音频时长 {audio_duration:.2f}s, 成本 ${cost:.4f}")
                    cb = ToolService.get_credit_callback()
                    if cb:
                        cb.add_tool_cost(cost, tool_name=ToolName.LIPSYNC.value, tool_type=ToolType.LIPSYNC_2_PRO)
            else:
                logger.warning("⚠️ 无法获取音频时长，lipsync 未计费")
            result = result.model_copy(update={"billing_cost": cost})
        else:
            logger.error(f"❌ WaveSpeed 唇形同步生成失败: {result.message}")
        
        return result
        
    except Exception as e:
        logger.error(f"🎭 WaveSpeed 唇形同步生成异常: {e}")
        return VideoGenerationResult.error_result(
            error_message=f"唇形同步生成异常: {str(e)}",
            provider=VideoProvider.WAVESPEED.value
        )


def get_lipsync_tools() -> List['ToolInfo']:
    """获取所有唇形同步工具信息列表
    
    Returns:
        List[ToolInfo]: 工具信息列表
    """
    from ...models.tool_enums import ToolType, ToolProvider, ToolCategory
    from ...services.tool_service import ToolInfo
    
    tool_type = ToolType.LIPSYNC_2_PRO
    provider = ToolProvider.WAVESPEED
    
    tool_info = ToolInfo(
        tool=generate_lipsync_with_wavespeed,
        tool_name=generate_lipsync_with_wavespeed.name,
        tool_type=tool_type,
        provider=provider,
        category=ToolCategory.LIPSYNC,
        mode=None
    )
    try:
        if hasattr(tool_info.tool, 'metadata'):
            if tool_info.tool.metadata is None:
                tool_info.tool.metadata = {}
            tool_info.tool.metadata.update({
                "tool_type": tool_type.value,
                "provider": provider.value,
                "category": ToolCategory.LIPSYNC.value
            })
    except Exception as e:
        logger.warning(f"添加tool metadata失败: {e}")
    
    return [tool_info]


# 支持的唇形同步模型（WaveSpeed Lipsync 2 Pro）
SUPPORTED_LIPSYNC_MODELS = {
    "wavespeed": {
        "sync/lipsync-2-pro": {
            "name": "Lipsync 2 Pro",
            "description": "唇形同步，按音频时长计费 $0.08/秒",
            "provider": "WaveSpeed AI",
            "max_duration": 60,
            "supported_formats": ["mp4", "mov", "avi"],
            "audio_formats": ["mp3", "wav", "m4a"]
        }
    }
}


def get_supported_models() -> dict:
    """获取支持的唇形同步模型信息"""
    return SUPPORTED_LIPSYNC_MODELS


def validate_lipsync_inputs(audio_url: str, video_url: str, model: str) -> tuple[bool, str]:
    """
    验证唇形同步输入参数
    
    Returns:
        (is_valid, error_message)
    """
    if not audio_url:
        return False, "音频URL不能为空"
    
    if not video_url:
        return False, "视频URL不能为空"
    
    if model not in [m for provider in SUPPORTED_LIPSYNC_MODELS.values() for m in provider.keys()]:
        supported = [m for provider in SUPPORTED_LIPSYNC_MODELS.values() for m in provider.keys()]
        return False, f"不支持的模型: {model}，支持的模型: {supported}"
    
    return True, ""
