"""
MMAudio V2 音效生成工具（WaveSpeed AI 模型）
"""
import logging
from typing import List, TYPE_CHECKING
from langchain_core.tools import tool
from langsmith import traceable

if TYPE_CHECKING:
    from ...services.tool_service import ToolInfo

from ...llm.wavespeed_service import get_wavespeed_service
from ...models.image_result import AudioGenerationResult, AudioProvider
from ...models.tool_enums import ToolName, ToolType
from ...services.tool_service import ToolService

logger = logging.getLogger(__name__)

# 全局服务实例
wavespeed_service = get_wavespeed_service()


@tool(ToolName.MMAUDIO)
@traceable(run_type='llm')
async def generate_audio_with_wavespeed(
    video_url: str,
    prompt: str,
    duration: float = 8.0
) -> AudioGenerationResult:
    """专业的MMAudio V2音效生成工具（WaveSpeed AI模型）
    
    这是一个高质量的AI音效生成工具，使用WaveSpeed AI的MMAudio V2模型为视频生成匹配的音效。
    能够根据视频内容和文本描述生成逼真的音效，完美匹配视频的视觉内容。
    
    🎯 重要提示：
    1. 需要提供视频URL作为输入，工具会分析视频内容
    2. 提示词应该描述期望的音效类型和氛围
    3. 生成的音效会与视频内容同步
    
    参数说明：
    - video_url: 输入视频的URL地址（必需）
    - prompt: 音效描述提示词，描述期望的音效类型、氛围和特征
      例如："Shot in extreme macro perspective, a glowing, heat-rippled, and semi-translucent molten lava cube..."
    - duration: 音效时长（秒），默认8.0秒，范围1-30秒
    
    音效类型示例：
    - 自然音效：风声、雨声、海浪声、鸟鸣等
    - 机械音效：引擎声、金属碰撞、电子设备运行声等
    - 环境音效：城市噪音、森林氛围、室内回响等
    - 特效音效：爆炸声、魔法音效、科幻音效等
    - ASMR音效：轻柔触摸、液体流动、细微摩擦等
    
    返回：包含生成结果的JSON格式数据"""
    try:
        logger.info(f"🎵 MMAudio V2 音效生成开始")
        logger.info(f"🎵 视频URL: {video_url[:100]}...")
        logger.info(f"🎵 音效描述: {prompt[:100]}...")
        logger.info(f"🎵 生成设置: duration={duration}s")
        
        # 调用WaveSpeed音效生成服务（实际使用MMAudio V2模型）
        result = await wavespeed_service.generate_audio(
            video_url=video_url,
            prompt=prompt,
            duration=duration
        )
        
        if result.success:
            logger.info(f"✅ MMAudio V2 音效生成成功: {result.audio_url}")
            cost = ToolService.calculate_cost(ToolType.MMAUDIO_V2, duration=duration)
            if cost > 0:
                ToolService.log_cost(cost)
                logger.info(f"💰 MMAudio 音效成本: ${cost:.6f} ({duration}s)")
                cb = ToolService.get_credit_callback()
                if cb:
                    cb.add_tool_cost(cost, tool_name=ToolName.MMAUDIO.value, tool_type=ToolType.MMAUDIO_V2)
            result = result.model_copy(update={"billing_cost": cost})
            return result
        else:
            logger.error(f"❌ MMAudio V2 音效生成失败: {result.message}")
            return result
            
    except Exception as e:
        logger.error(f"❌ MMAudio V2 音效生成异常: {str(e)}")
        result = AudioGenerationResult.error_result(
            error_message=f"MMAudio V2 音效生成异常: {str(e)}",
            provider=AudioProvider.WAVESPEED,
            video_url=video_url
        )
        return result


def get_mmaudio_tools() -> List['ToolInfo']:
    """获取MMAudio V2音效生成工具信息列表
    
    Returns:
        List[ToolInfo]: 工具信息列表
    """
    from ...models.tool_enums import ToolType, ToolProvider, ToolCategory
    from ...services.tool_service import ToolInfo
    
    tool_type = ToolType.MMAUDIO_V2
    provider = ToolProvider.WAVESPEED
    
    # 创建工具的 ToolInfo
    tool_info = ToolInfo(
        tool=generate_audio_with_wavespeed,
        tool_name=generate_audio_with_wavespeed.name,
        tool_type=tool_type,
        provider=provider,
        category=ToolCategory.AUDIO_EFFECT,
        mode=None
    )
    
    # ✅ 添加metadata到tool对象（让callback能精准获取ToolType）
    try:
        if hasattr(tool_info.tool, 'metadata'):
            if tool_info.tool.metadata is None:
                tool_info.tool.metadata = {}
            tool_info.tool.metadata.update({
                "tool_type": tool_type.value,
                "provider": provider.value,
                "category": ToolCategory.AUDIO_EFFECT.value
            })
    except Exception as e:
        logger.warning(f"添加tool metadata失败: {e}")
    
    return [tool_info]
