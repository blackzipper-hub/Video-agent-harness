"""
SunoAPI 音乐生成工具集
"""
import logging
from typing import List, Optional, TYPE_CHECKING, Annotated, Any
from pydantic import BaseModel, Field, SkipValidation
from pydantic.json_schema import SkipJsonSchema
from app.tools.runtime import tool
from app.tools.runtime import ToolRuntime

if TYPE_CHECKING:
    from ...services.tool_service import ToolInfo

from ...llm.suno_service import get_suno_service, SunoService
from ...models.image_result import MusicGenerationResult, MusicProvider
from ...models.tool_enums import ToolName
from ...services.account.account_router import get_account_router

logger = logging.getLogger(__name__)


class SunoMusicInput(BaseModel):
    """Input schema for Suno music generation."""
    prompt: str = Field(
        description="音乐生成提示词。has_lyrics=False时为英文音乐描述（最多400字符），has_lyrics=True时为结构化歌词（需包含[Verse], [Chorus]等标签，最多5000字符）"
    )
    has_lyrics: bool = Field(
        default=False,
        description="是否包含歌词。True=生成带歌词的歌曲（prompt 为歌词文本）；False=纯音乐BGM 或 auto_lyrics 模式"
    )
    auto_lyrics: bool = Field(
        default=False,
        description="是否根据描述自动生成带人声歌曲（歌词由 Suno 生成）。仅当 has_lyrics=False 时有效；True=描述→带人声歌曲，False=纯器乐BGM"
    )
    target_duration: Optional[int] = Field(
        default=None,
        description="目标时长（秒）；有歌词或 auto_lyrics 时传入。Suno 多版本按返回顺序采用，服务端不按时长重排或二次生成"
    )
    tags: Optional[str] = Field(
        default=None,
        description="歌曲风格标签（可选），如 'Fast 160BPM, Brief, Short version'。v4及以下最多200字符，v4.5及以上最多1000字符"
    )
    vocal_gender: Optional[str] = Field(
        default=None,
        description="人声性别（可选，仅 v4-5+）：'f' 女声，'m' 男声。从用户描述或参考图片推断；推断不出则不传"
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="Provider runtime context (internal use only)"
    )


async def _generate_music_with_suno_impl(
    prompt: str,
    has_lyrics: bool = False,
    auto_lyrics: bool = False,
    target_duration: Optional[int] = None,
    tags: Optional[str] = None,
    vocal_gender: Optional[str] = None,
    mv: Optional[str] = None,
) -> MusicGenerationResult:
    """SunoAPI音乐生成实现 - 核心逻辑"""
    try:
        logger.info(f"🎵 SunoAPI音乐生成开始")
        logger.info(
            "🎵 工具入参: has_lyrics=%s auto_lyrics=%s target_duration=%s vocal_gender=%r tags=%r prompt_len=%s prompt_head=%r",
            has_lyrics,
            auto_lyrics,
            target_duration,
            vocal_gender,
            tags,
            len(prompt or ""),
            (prompt or "")[:240],
        )
        
        # 三种模式：用户歌词 | auto lyrics（描述→带人声） | 纯BGM
        if has_lyrics:
            # 有歌词：custom_mode=True, make_instrumental=False
            # Custom 模式：最多 5000 字符
            custom_mode = True
            make_instrumental = False
            lyrics = prompt[:5000]  # 截取前5000字符
            gpt_prompt = None
            if len(prompt) > 5000:
                logger.warning(f"🎵 歌词过长，截取前5000字符")
            logger.info(f"🎵 模式: Custom（带歌词，mv={mv or 'chirp-v4-5'}）")
            logger.info(f"🎵 结构化歌词: {lyrics[:100]}...")
            if tags:
                logger.info(f"🎵 风格标签: {tags}")
        elif auto_lyrics:
            # 描述→带人声：custom_mode=False, make_instrumental=False，歌词由 Suno 自动生成
            custom_mode = False
            make_instrumental = False
            lyrics = None
            gpt_prompt = prompt[:400]  # 截取前400字符
            if len(prompt) > 400:
                logger.warning(f"🎵 描述过长，截取前400字符")
            logger.info(f"🎵 模式: GPT + 带人声（auto lyrics）")
            logger.info(f"🎵 音乐描述: {gpt_prompt[:100]}...")
        else:
            # 无歌词且非 auto_lyrics：custom_mode=False, make_instrumental=True
            # GPT 模式纯音乐：最多 400 字符
            custom_mode = False
            make_instrumental = True
            lyrics = None
            gpt_prompt = prompt[:400]  # 截取前400字符
            if len(prompt) > 400:
                logger.warning(f"🎵 描述过长，截取前400字符")
            logger.info(f"🎵 模式: GPT（纯音乐BGM）")
            logger.info(f"🎵 音乐描述: {gpt_prompt[:100]}...")
        
        # 定义实际的请求函数（接收api_key作为第一个参数）
        async def _make_suno_request(api_key: str):
            """实际的Suno API调用"""
            suno_service = SunoService(api_key=api_key)
            
            # 打包生成参数（供 poll 阶段做时长选择等）
            generation_params = {
                "has_lyrics": has_lyrics,
                "auto_lyrics": auto_lyrics,
                "target_duration": target_duration,
                "prompt": prompt,
                "custom_mode": custom_mode,
                "make_instrumental": make_instrumental,
                "tags": tags,
                "vocal_gender": vocal_gender
            }
            _vocal = vocal_gender if vocal_gender in ("f", "m") else None
            return await suno_service.generate_music(
                prompt=gpt_prompt,
                custom_mode=custom_mode,
                make_instrumental=make_instrumental,
                lyrics=lyrics,
                mv=mv or "chirp-v4-5",
                tags=tags,
                vocal_gender=_vocal,
                generation_params=generation_params
            )
        
        # 使用账号路由器
        from ...models.tool_enums import ToolProvider, ToolType
        router = await get_account_router()
        result = await router.route_tool_request(
            provider=ToolProvider.SUNO,
            tool_type=ToolType.CHIRP_V4_5,
            request_func=_make_suno_request
        )
        
        if result.success:
            # ⭐ Sanity check：Suno 偶发「success=True 但 clips 为空 / audio_url 为空」假阳性
            #   常见触发：服务端 500 + 极短贴片（5s/10s），SunoService 把 500 当作可接受、success 仍置 True
            #   只信 result.success 一个字段会让下游拿到一个无音频的「成功」音乐版本，必须以实际 audio_url 为准
            valid_clips = [
                c for c in (result.clips or [])
                if (getattr(c, "audio_url", "") or "").strip()
            ]
            if not valid_clips:
                empty_reason = (
                    "no clips returned"
                    if not result.clips
                    else f"all {len(result.clips)} clips have empty audio_url"
                )
                err_msg = f"SunoAPI 返回 success=True 但实际 {empty_reason}（疑似服务端 500 假阳性）"
                logger.error(
                    f"❌ {err_msg}  task_id={result.task_id} message={result.message!r}"
                )
                return MusicGenerationResult.error_result(
                    error_message=err_msg,
                    provider=MusicProvider.SUNO,
                    task_id=result.task_id,
                )

            # 部分 clips 无 audio_url 时，剔除空 clip 保留有效 clip
            if len(valid_clips) != len(result.clips):
                dropped = len(result.clips) - len(valid_clips)
                logger.warning(
                    f"⚠️ Suno 返回 {len(result.clips)} 个 clips 中 {dropped} 个 audio_url 为空，已剔除"
                )
                result = result.model_copy(
                    update={"clips": valid_clips, "clips_count": len(valid_clips)}
                )

            audio_url = valid_clips[0].audio_url
            logger.info(f"✅ SunoAPI音乐生成成功: {audio_url}")
            
            # ⭐ 计算成本（使用统一的 calculate_cost 方法）
            from ...services.tool_service import ToolService
            from ...models.tool_enums import ToolType
            cost = ToolService.calculate_cost(ToolType.CHIRP_V4_5)
            if cost > 0:
                ToolService.log_cost(cost)
                logger.info(f"💰 Suno 音乐生成成本: ${cost:.6f}")
                cb = ToolService.get_credit_callback()
                if cb:
                    cb.add_tool_cost(cost, tool_name=ToolName.SUNO.value, tool_type=ToolType.CHIRP_V4_5)
            result = result.model_copy(update={"billing_cost": cost})
        else:
            logger.error(f"❌ SunoAPI音乐生成失败: {result.error}")
        
        return result
        
    except Exception as e:
        result = MusicGenerationResult.error_result(
            error_message=f"SunoAPI音乐生成异常: {str(e)}",
            provider=MusicProvider.SUNO
        )
        logger.error(f"❌ SunoAPI音乐生成异常: {str(e)}")
        return result


@tool(ToolName.SUNO, args_schema=SunoMusicInput)
async def generate_music_with_suno(
    prompt: str,
    has_lyrics: bool = False,
    auto_lyrics: bool = False,
    target_duration: Optional[int] = None,
    tags: Optional[str] = None,
    vocal_gender: Optional[str] = None,
    runtime: ToolRuntime[Any] = None
) -> MusicGenerationResult:
    """专业的 SunoAPI AI音乐生成工具（使用 chirp-v4-5 模型）
    
    🎵 **根据用户输入自动判断生成模式**：
    - has_lyrics=False → 生成纯音乐BGM（prompt 为英文音乐描述，最多400字符）
    - has_lyrics=True → 生成带歌词的歌曲（prompt 为结构化歌词，最多5000字符）
    
    🎯 **参数说明**：
    - prompt: 音乐生成提示词
      * has_lyrics=False: 英文音乐描述（如 "A calm piano melody"，最多400字符）
      * has_lyrics=True: 结构化歌词（需包含 [Verse], [Chorus] 等标签，最多5000字符）
    - has_lyrics: 是否包含歌词（True=歌曲，False=纯音乐BGM），默认False
    - target_duration: 目标时长（秒），用于有歌词时的时长控制。Suno 会生成 2 个版本；服务端按返回顺序采用，不因时长偏差再次生成或重排
    - tags: 歌曲风格标签（可选），如 "Fast 160BPM, Brief, Short version"
    - vocal_gender: 人声性别（可选，仅 v4-5+ 带人声时）：'f' 女声，'m' 男声。从用户描述或参考图片推断；推断不出则不传
      * v4及以下：最多200字符
      * v4.5及以上：最多1000字符
      * 用于控制节奏、风格、时长等
    
    🎼 **歌词结构标签**（has_lyrics=True 时使用）：
    - [Intro] - 前奏
    - [Verse] - 主歌
    - [Chorus] - 副歌
    - [Bridge] - 过渡
    - [Outro] - 尾奏
    
    ⚠️ **字符限制**：
    - 纯音乐BGM（has_lyrics=False）: prompt 最多 400 字符
    - 带歌词歌曲（has_lyrics=True）: prompt 最多 5000 字符
    - 超出部分会被自动截断
    
    ⏱️ **时长**（has_lyrics=True 或 auto_lyrics=True 且传 target_duration 时）：
    - Suno 可能返回多个版本；按 API 返回顺序采用，不因与 target_duration 的偏差再次调用工具
    
    📋 **使用模型**：chirp-v4-5
    
    💰 **成本**：$0.08 per song
    
    返回：MusicGenerationResult 对象
    """
    return await _generate_music_with_suno_impl(
        prompt=prompt,
        has_lyrics=has_lyrics,
        auto_lyrics=auto_lyrics,
        target_duration=target_duration,
        tags=tags,
        vocal_gender=vocal_gender
    )


def get_suno_tools() -> List['ToolInfo']:
    """获取SunoAPI工具信息列表
    
    Returns:
        List[ToolInfo]: 工具信息列表
    """
    from ...models.tool_enums import ToolType, ToolProvider, ToolCategory
    from ...services.tool_service import ToolInfo
    
    tool_type = ToolType.CHIRP_V4_5
    provider = ToolProvider.SUNO
    
    # 创建工具的 ToolInfo
    tool_info = ToolInfo(
        tool=generate_music_with_suno,
        tool_name=generate_music_with_suno.name,
        tool_type=tool_type,
        provider=provider,
        category=ToolCategory.MUSIC_GENERATION,
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
                "category": ToolCategory.MUSIC_GENERATION.value
            })
    except Exception as e:
        logger.warning(f"添加tool metadata失败: {e}")
    
    return [tool_info]



