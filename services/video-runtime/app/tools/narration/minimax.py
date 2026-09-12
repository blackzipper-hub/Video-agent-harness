"""
Minimax Speech 2.5 语音合成工具（通过 WaveSpeed API）
"""
import logging
from typing import List, TYPE_CHECKING
from app.tools.runtime import tool
from langsmith import traceable

if TYPE_CHECKING:
    from ...services.tool_service import ToolInfo

from ...llm.wavespeed_service import get_wavespeed_service
from ...models.image_result import (
    SpeechGenerationResult, SpeechProvider, VoiceID, Emotion
)
from ...models.tool_enums import ToolName, ToolType
from ...services.tool_service import ToolService

logger = logging.getLogger(__name__)

# 全局服务实例
wavespeed_service = get_wavespeed_service()


@tool(ToolName.MINIMAX_SPEECH)
@traceable(run_type='llm')
async def generate_speech_with_wavespeed(
    text: str,
    voice_id: str = VoiceID.WISE_WOMAN.value,
    emotion: str = Emotion.HAPPY.value,
    speed: float = 1.0,
    pitch: int = 0,
    volume: float = 1.0
) -> SpeechGenerationResult:
    """专业的Minimax Speech 2.5语音合成工具（通过WaveSpeed API）
    
    这是一个高质量的AI语音合成工具，使用Minimax Speech 2.5模型进行文本转语音。
    能够根据输入的文本生成自然流畅的语音，支持多种语音角色、情感和音频参数调节。
    
    🎯 重要提示：
    1. 支持中英文混合文本，自动处理语言识别
    2. 提供150+种语音角色选择，包括中英文专业播音员、角色扮演声音等
    3. 支持7种情感模式和精细的音频参数调节
    4. 生成高质量48kHz采样率的音频文件
    
    参数说明：
    - text: 要转换为语音的文本内容（支持中英文混合）
    - voice_id: 语音角色ID，默认"Wise_Woman"
      * 通用角色: Wise_Woman, Friendly_Person, Deep_Voice_Man, Calm_Woman等
      * 英语角色: English_expressive_narrator, English_radiant_girl等100+种
      * 中文角色: Chinese (Mandarin)_News_Anchor, Chinese (Mandarin)_Gentleman等30+种
    - emotion: 语音情感，默认"happy"
      * 可选: happy, sad, angry, fearful, disgusted, surprised, neutral
    - speed: 语音速度，默认1.0（正常语速）
      * 范围: 0.5-2.0，0.5=慢速，1.0=正常，2.0=快速
    - pitch: 音调调节，默认0（正常音调）
      * 范围: -12到12，负值=低音调，正值=高音调
    - volume: 音量大小，默认1.0（正常音量）
      * 范围: 0.1-2.0，0.1=很小声，1.0=正常，2.0=很大声
    
    🎵 语音角色推荐：
    - 新闻播报: English_captivating_female1, Chinese (Mandarin)_News_Anchor
    - 教育内容: English_GentleTeacher, Chinese (Mandarin)_Wise_Women
    - 故事叙述: English_CaptivatingStoryteller, Chinese (Mandarin)_Radio_Host
    - 商务内容: English_Trustworth_Man, Chinese (Mandarin)_Reliable_Executive
    - 儿童内容: English_PlayfulGirl, Chinese (Mandarin)_Cute_Spirit
    
    返回：包含生成结果的JSON格式数据，包括音频URL、提供商信息等"""
    try:
        logger.info(f"🔊 Minimax Speech 2.5 语音合成开始")
        logger.info(f"🔊 文本内容: {text[:100]}...")
        logger.info(f"🔊 语音设置: voice_id={voice_id}, emotion={emotion}, speed={speed}, pitch={pitch}, volume={volume}")
        
        # 调用WaveSpeed语音合成服务（实际使用Minimax Speech 2.5模型）
        result = await wavespeed_service.generate_speech(
            text=text,
            voice_id=voice_id,
            emotion=emotion,
            speed=speed,
            pitch=pitch,
            volume=volume
        )
        
        if result.success:
            logger.info(f"✅ Minimax Speech 2.5 语音合成成功: {result.audio_url}")
            cost = ToolService.calculate_cost(ToolType.MINIMAX_SPEECH_2_5, text=text)
            if cost > 0:
                ToolService.log_cost(cost)
                logger.info(f"💰 Minimax Speech 成本: ${cost:.6f} ({len(text)} chars)")
                cb = ToolService.get_credit_callback()
                if cb:
                    cb.add_tool_cost(cost, tool_name=ToolName.MINIMAX_SPEECH.value, tool_type=ToolType.MINIMAX_SPEECH_2_5)
            result = result.model_copy(update={"billing_cost": cost})
            return result
        else:
            logger.error(f"❌ Minimax Speech 2.5 语音合成失败: {result.message}")
            return result
            
    except Exception as e:
        logger.error(f"❌ Minimax Speech 2.5 语音合成异常: {str(e)}")
        result = SpeechGenerationResult.error_result(
            error_message=f"Minimax Speech 2.5 语音合成异常: {str(e)}",
            provider=SpeechProvider.WAVESPEED
        )
        return result


def get_minimax_tools() -> List['ToolInfo']:
    """获取Minimax语音合成工具信息列表
    
    Returns:
        List[ToolInfo]: 工具信息列表
    """
    from ...models.tool_enums import ToolType, ToolProvider, ToolCategory
    from ...services.tool_service import ToolInfo
    
    tool_type = ToolType.MINIMAX_SPEECH_2_5
    provider = ToolProvider.WAVESPEED
    
    # 创建工具的 ToolInfo
    tool_info = ToolInfo(
        tool=generate_speech_with_wavespeed,
        tool_name=generate_speech_with_wavespeed.name,
        tool_type=tool_type,
        provider=provider,
        category=ToolCategory.SPEECH_SYNTHESIS,
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
                "category": ToolCategory.SPEECH_SYNTHESIS.value
            })
    except Exception as e:
        logger.warning(f"添加tool metadata失败: {e}")
    
    return [tool_info]
