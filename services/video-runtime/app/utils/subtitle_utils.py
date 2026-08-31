"""
字幕生成和视频合成工具
提供 ASS 字幕文件创建、视频字幕嵌入等功能

功能模块：
1. 字幕格式: 创建 ASS 格式字幕文件（支持自定义样式）
2. 时间轴映射: 从音频片段创建字幕段
3. 视频合成: 使用 FFmpeg 嵌入字幕到视频
4. 临时文件: 创建临时字幕文件供 FFmpeg 使用

使用场景：
- 视频旁白生成后添加字幕
- 视频合成时嵌入字幕轨道
- 自定义字幕样式和位置
"""

import logging
import os
import tempfile
from typing import List, Optional, Tuple
import pysubs2
import aiofiles
from .text_format_utils import format_text_for_subtitles
from app.schemas.subtitle import SubtitleGroupsResponse
from prompts.prompt_config import PROMPTS_CONFIG, PromptName
from app.services.agent.utils.llm_resilience import (
    StructuredResilienceKind,
    ainvoke_structured_resilient,
)

logger = logging.getLogger(__name__)


class SubtitleSegment:
    """字幕片段数据类"""
    def __init__(self, start_time: float, end_time: float, text: str):
        self.start_time = start_time  # 秒
        self.end_time = end_time      # 秒
        self.text = text


def create_ass_subtitle_file(
    subtitle_segments: List[SubtitleSegment],
    output_path: str,
    style_config: Optional[dict] = None
) -> str:
    """
    创建 ASS 字幕文件
    
    Args:
        subtitle_segments: 字幕片段列表
        output_path: 输出文件路径
        style_config: 字幕样式配置
        
    Returns:
        str: 生成的字幕文件路径
    """
    try:
        # 创建字幕对象
        subs = pysubs2.SSAFile()
        
        # 设置默认样式 - 使用原来的简洁样式
        default_style = {
            'fontname': 'Arial',
            'fontsize': 20,
            'primarycolor': pysubs2.Color(255, 255, 255),  # 白色
            'secondarycolor': pysubs2.Color(0, 0, 0),      # 黑色
            'outlinecolor': pysubs2.Color(0, 0, 0),        # 黑色轮廓
            'backcolor': pysubs2.Color(0, 0, 0),           # 黑色背景
            'bold': True,
            'italic': False,
            'underline': False,
            'strikeout': False,
            'scalex': 100,
            'scaley': 100,
            'spacing': 0,
            'angle': 0,
            'borderstyle': 1,
            'outline': 2,
            'shadow': 0,
            'alignment': 2,  # 底部居中
            'marginl': 10,
            'marginr': 10,
            'marginv': 10,
            'encoding': 1
        }
        
        # 合并用户自定义样式
        if style_config:
            default_style.update(style_config)
        
        # 创建样式
        style = pysubs2.SSAStyle(**default_style)
        subs.styles['Default'] = style
        
        # 添加字幕事件
        for segment in subtitle_segments:
            # 格式化字幕文本
            formatted_text = format_text_for_subtitles(segment.text)
            if not formatted_text:
                continue
            
            # 转换时间为毫秒
            start_ms = int(segment.start_time * 1000)
            end_ms = int(segment.end_time * 1000)
            
            # 创建字幕事件
            event = pysubs2.SSAEvent(
                start=start_ms,
                end=end_ms,
                text=formatted_text,
                style='Default'
            )
            subs.append(event)
        
        # 保存字幕文件
        subs.save(output_path)
        logger.info(f"✅ 字幕文件已生成: {output_path} ({len(subs)} 个字幕)")
        
        return output_path
        
    except Exception as e:
        logger.error(f"❌ 生成字幕文件失败: {e}")
        raise


def create_subtitle_segments_from_audio_mapping(
    shot_audio_mapping: dict,
    music_data: dict
) -> List[SubtitleSegment]:
    """
    从音频映射创建字幕片段
    
    Args:
        shot_audio_mapping: 镜头音频映射 {audio_segment_id: {'videos': [], 'music_url': str, 'duration': float, 'shot_numbers': []}}
        music_data: 音乐数据 {audio_segment_id: {'version': MusicVersion, ...}}
        
    Returns:
        List[SubtitleSegment]: 字幕片段列表
    """
    subtitle_segments = []
    current_time = 0.0
    
    # 按音频片段顺序处理
    sorted_segments = sorted(shot_audio_mapping.items(), 
                           key=lambda x: min(x[1]['shot_numbers']) if x[1]['shot_numbers'] else 0)
    
    for audio_segment_id, mapping_info in sorted_segments:
        duration = mapping_info['duration']
        
        # 从 music_data 获取文本信息（防御：值可能是字符串，如 DB 未解析的 JSON）
        music_info = music_data.get(audio_segment_id) or {}
        if not isinstance(music_info, dict):
            music_info = {}
        music_version = music_info.get('version')
        
        if music_version:
            # 直接使用音乐提示词
            music_prompt = getattr(music_version, 'music_prompt', None)
            unified_prompt = music_prompt
            if unified_prompt:
                # 只有用户原始文本才用于字幕
                formatted_text = format_text_for_subtitles(unified_prompt)
                if formatted_text:
                    segment = SubtitleSegment(
                        start_time=current_time,
                        end_time=current_time + duration,
                        text=formatted_text
                    )
                    subtitle_segments.append(segment)
                    logger.info(f"📝 添加字幕: {current_time:.2f}s-{current_time + duration:.2f}s: {formatted_text[:50]}...")
        
        current_time += duration
    
    logger.info(f"🎬 生成了 {len(subtitle_segments)} 个字幕片段")
    return subtitle_segments


def _narration_subtitle_text(version) -> str:
    """字幕应与 TTS 实际朗读文本一致，优先 enhanced_prompt（LLM 优化后送入合成的文本）。"""
    enhanced = getattr(version, "enhanced_prompt", None) or ""
    if enhanced.strip():
        return enhanced.strip()
    return (getattr(version, "narration_text", None) or "").strip()


def create_subtitle_segments_from_narrations(
    narrations_data: dict,
    shot_timeline: Optional[List[Tuple[int, float, float]]] = None,
) -> List[SubtitleSegment]:
    """
    从 per-shot TTS 旁白创建字幕片段（Product Launch / narration-driven 成片）。

    Args:
        narrations_data: {shot_number: {'narration': ..., 'version': VideoNarrationVersionDB}}
        shot_timeline: 可选，成片时间轴 [(shot_number, start_time, segment_duration), ...]；
            传入时字幕与 concat 后的视频/音频对齐（含无旁白镜头占位）。

    Returns:
        List[SubtitleSegment]: 按镜头顺序的字幕片段
    """
    if not narrations_data:
        return []

    subtitle_segments: List[SubtitleSegment] = []

    if shot_timeline:
        for shot_number, start_time, seg_dur in shot_timeline:
            entry = narrations_data.get(shot_number) or {}
            if not isinstance(entry, dict):
                continue
            version = entry.get("version")
            if not version or not getattr(version, "success", True):
                continue
            text = _narration_subtitle_text(version)
            formatted_text = format_text_for_subtitles(text)
            if not formatted_text or seg_dur <= 0:
                continue
            segment = SubtitleSegment(
                start_time=start_time,
                end_time=start_time + seg_dur,
                text=formatted_text,
            )
            subtitle_segments.append(segment)
            logger.info(
                "📝 [narration subtitle] shot %s: %.2fs-%.2fs: %s...",
                shot_number,
                start_time,
                start_time + seg_dur,
                formatted_text[:50],
            )
        logger.info(f"🎬 从旁白生成了 {len(subtitle_segments)} 个字幕片段（成片时间轴）")
        return subtitle_segments

    current_time = 0.0
    for shot_number in sorted(narrations_data.keys()):
        entry = narrations_data.get(shot_number) or {}
        if not isinstance(entry, dict):
            continue
        version = entry.get("version")
        if not version:
            continue
        if not getattr(version, "success", True):
            continue

        text = _narration_subtitle_text(version)
        formatted_text = format_text_for_subtitles(text)
        if not formatted_text:
            dur = float(getattr(version, "duration", None) or 0.0)
            current_time += dur
            continue

        dur = float(getattr(version, "duration", None) or 0.0)
        if dur <= 0:
            dur = max(1.0, len(formatted_text) * 0.08)

        segment = SubtitleSegment(
            start_time=current_time,
            end_time=current_time + dur,
            text=formatted_text,
        )
        subtitle_segments.append(segment)
        logger.info(
            "📝 [narration subtitle] shot %s: %.2fs-%.2fs: %s...",
            shot_number,
            current_time,
            current_time + dur,
            formatted_text[:50],
        )
        current_time += dur

    logger.info(f"🎬 从旁白生成了 {len(subtitle_segments)} 个字幕片段")
    return subtitle_segments


def add_subtitles_to_video_command(
    video_path: str,
    subtitle_path: str,
    output_path: str
) -> List[str]:
    """
    生成添加字幕到视频的 FFmpeg 命令。

    使用 -vf ass= 烧录字幕会触发整段视频重新编码（无法 -c:v copy）。
    通过 -preset ultrafast 与 -threads 限制每进程线程数；音频 -c:a copy 不重编。
    """
    _ffmpeg_threads = 2  # 多 agent/多并发时避免单进程占满 CPU（可改为从配置读取）
    return [
        'ffmpeg',
        '-y',
        '-i', video_path,
        '-vf', f"ass='{subtitle_path}'",
        '-c:v', 'libx264',
        '-preset', 'ultrafast',  # 最快编码，显著缩短加字幕耗时（体积略大）
        '-crf', '23',
        '-threads', str(_ffmpeg_threads),
        '-c:a', 'copy',
        output_path
    ]


def create_temp_subtitle_file(subtitle_segments: List[SubtitleSegment]) -> str:
    """
    创建临时字幕文件
    
    Args:
        subtitle_segments: 字幕片段列表
        
    Returns:
        str: 临时字幕文件路径
    """
    # 创建临时文件
    temp_fd, temp_path = tempfile.mkstemp(suffix='.ass', prefix='subtitle_')
    os.close(temp_fd)
    
    try:
        create_ass_subtitle_file(subtitle_segments, temp_path)
        return temp_path
    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise e




def get_cinematic_subtitle_style() -> dict:
    """
    获取电影级字幕样式 - 专业影院效果
    
    Returns:
        dict: 电影级字幕样式
    """
    return {
        'fontname': 'Microsoft YaHei UI',
        'fontsize': 18,                    # 缩小字体
        'primarycolor': pysubs2.Color(255, 255, 255),    # 纯白
        'secondarycolor': pysubs2.Color(255, 255, 255),
        'outlinecolor': pysubs2.Color(0, 0, 0),          # 纯黑轮廓
        'backcolor': pysubs2.Color(0, 0, 0, 120),        # 半透明背景
        'bold': True,
        'italic': False,
        'outline': 2,                      # 适中的轮廓
        'shadow': 1,                       # 轻微阴影
        'alignment': 2,                    # 底部居中
        'marginl': 20,                     # 适中边距
        'marginr': 20,
        'marginv': 15,                     # 底部边距，确保在屏幕底部
        'borderstyle': 1,
        'scalex': 100,
        'scaley': 100,
        'spacing': 0,                      # 正常间距
        'angle': 0,
    }




async def create_ass_subtitle_from_words_with_llm(words: List, audio_url: str) -> Optional[str]:
    """
    使用 LLM 智能组装基于 words 的 ASS 字幕文件
    
    Args:
        words: AudioWord 对象列表，包含 id, word, start, end
        audio_url: 音频文件URL
        
    Returns:
        字幕文件的S3 URL，如果失败返回None
    """
    if not words:
        logger.warning("🎬 words 列表为空，无法生成字幕")
        return None
    
    try:
        import pysubs2
        from pathlib import Path
        import tempfile
        from ..utils.s3_utils import s3_utils
        
        logger.info(f"🎬 开始使用LLM生成字幕，words数量: {len(words)}")
        
        # 准备 LLM 输入数据
        words_data = []
        for word in words:
            words_data.append({
                "id": word.id,
                "word": word.word,
                "start": word.start,
                "end": word.end
            })
        
        # 调用 LLM 组装字幕
        subtitle_lines = await _generate_subtitle_lines_with_llm(words_data)
        
        if not subtitle_lines:
            logger.warning("🎬 LLM 未生成任何字幕行")
            return None
        
        # 创建 ASS 字幕文件
        subs = pysubs2.SSAFile()
        style = get_cinematic_subtitle_style()
        subs.styles["Default"] = pysubs2.SSAStyle(**style)
        
        # 添加字幕事件
        for line in subtitle_lines:
            event = pysubs2.SSAEvent(
                start=int(line['start'] * 1000),
                end=int(line['end'] * 1000),
                text=line['text'].strip()
            )
            subs.events.append(event)
        
        logger.info(f"🎬 生成了 {len(subs.events)} 个字幕事件")
        
        # 检查是否有字幕事件
        if len(subs.events) == 0:
            logger.warning("🎬 没有生成任何字幕事件，跳过字幕文件生成")
            return None
        
        # 生成文件名，放到 subtitle/ 目录下
        audio_filename = Path(audio_url).stem
        subtitle_filename = f"subtitle/{audio_filename}_llm_words_subtitle.ass"
        
        # 保存到临时文件并上传
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ass', delete=True, encoding='utf-8') as temp_file:
            # 保存字幕内容到临时文件
            subs.save(temp_file.name)
            
            # 验证生成的文件（异步读，不阻塞事件循环）
            async with aiofiles.open(temp_file.name, 'r', encoding='utf-8') as f:
                content = await f.read()
            if len(content.strip()) == 0:
                logger.error("🎬 生成的字幕文件为空")
                return None
            logger.info(f"🎬 字幕文件内容长度: {len(content)} 字符")

            # 读取文件内容并上传到S3
            async with aiofiles.open(temp_file.name, 'rb') as f:
                file_data = await f.read()

            subtitle_url = await s3_utils.upload_file(file_data, subtitle_filename, 'text/plain; charset=utf-8')
            if subtitle_url:
                logger.info(f"🎬 LLM字幕文件上传成功: {subtitle_url}")
                return subtitle_url
            else:
                logger.error("🎬 LLM字幕文件上传失败")
                return None
        
    except Exception as e:
        logger.error(f"🎬 生成LLM字幕文件失败: {e}")
        return None


def _build_words_text_for_subtitle_prompt(words_data: List[dict]) -> str:
    """构建词汇列表文本（ID | Word），供 prompt 模板使用。"""
    return "\n".join([
        f"ID:{w['id']} | Word:'{w['word']}'"
        for w in words_data
    ])


def _calculate_time_from_word_ids(word_ids: List[int], words_data: List[dict]) -> tuple:
    """
    根据词汇ID列表计算开始和结束时间
    
    Args:
        word_ids: 词汇ID列表
        words_data: 包含 id, word, start, end 的词汇数据列表
        
    Returns:
        tuple: (start_time, end_time)
    """
    if not word_ids or not words_data:
        return 0.0, 0.0
    
    # 创建 ID 到词汇数据的映射
    word_map = {w['id']: w for w in words_data}
    
    # 获取对应的词汇数据
    valid_words = [word_map[wid] for wid in word_ids if wid in word_map]
    
    if not valid_words:
        return 0.0, 0.0
    
    # 计算开始时间（第一个词的开始时间）和结束时间（最后一个词的结束时间）
    start_time = min(w['start'] for w in valid_words)
    end_time = max(w['end'] for w in valid_words)
    
    return start_time, end_time


async def _generate_subtitle_lines_with_llm(words_data: List[dict]) -> List[dict]:
    """
    使用 LLM 智能组装字幕行
    
    Args:
        words_data: 包含 id, word, start, end 的词汇数据列表
    
    Returns:
        字幕行列表，每行包含 text, start, end
    """
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.orchestration.skills.prompt_context import (
            facts_human_message,
            skill_system_message,
        )

        logger.info("🎬 调用LLM进行字幕分组...")

        words_text = _build_words_text_for_subtitle_prompt(words_data)
        system = skill_system_message(
            "subtitle-line-grouping-director",
            lead="Follow subtitle-line-grouping-director.",
        )
        facts = {
            "words": words_text,
            "word_count": len(words_data) if words_data else 0,
        }
        messages = [
            SystemMessage(content=system),
            HumanMessage(content=facts_human_message(facts)),
        ]
        # 强制语言：按当前请求语言输出字幕分组文案
        from app.services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from app.utils.i18n import get_current_language
        _lang = get_current_language() or "en"
        apply_language_suffix_to_system_message_in_messages(messages, _lang)
        parsed_result = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_SUBTITLE_LINE_GROUPING],
            kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
            structured_chat_messages=messages,
            include_raw=False,
        )
        if not isinstance(parsed_result, SubtitleGroupsResponse):
            parsed_result = SubtitleGroupsResponse.model_validate(parsed_result)
        subtitle_groups = parsed_result.subtitle_groups
        
        logger.info(f"🎬 LLM生成了 {len(subtitle_groups)} 个字幕分组")
        
        # 根据分组和词汇ID计算时间，生成最终字幕行
        subtitle_lines = []
        for idx, group in enumerate(subtitle_groups):
            start_time, end_time = _calculate_time_from_word_ids(group.word_ids, words_data)
            
            subtitle_lines.append({
                'id': idx,  # 添加 id 字段，从 0 开始
                'text': group.text,
                'start': start_time,
                'end': end_time
            })
        
        logger.info(f"🎬 字幕时间计算完成，共 {len(subtitle_lines)} 行字幕")
        return subtitle_lines
        
    except Exception as e:
        logger.error(f"🎬 LLM生成字幕行失败: {e}")
        return []
