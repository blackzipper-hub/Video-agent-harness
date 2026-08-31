"""
文本格式化工具
处理字幕文本的格式化和清理

功能模块：
1. 文本提取: 从合并段落中提取原始文本（去除内部标记）
2. 字幕格式化: 为字幕系统格式化文本

使用场景：
- 字幕文件生成时的文本处理
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_original_text_from_merged_segment(text: Optional[str]) -> Optional[str]:
    """
    从合并后的音频片段文本中提取原始用户文本
    
    处理场景：
    1. 纯衔接文本：'[衔接片段：...]' -> None
    2. 纯用户文本：'Hello world' -> 'Hello world'  
    3. 合并文本：'[衔接片段：...] Hello world' -> 'Hello world'
    4. 多段合并：'[衔接片段：...] Text1 [衔接片段：...] Text2' -> 'Text1 Text2'
    
    Args:
        text: 原始文本（可能包含衔接片段和用户文本）
        
    Returns:
        Optional[str]: 提取出的用户原始文本，如果没有用户文本则返回 None
    """
    if not text or not isinstance(text, str):
        return None
    
    # 使用正则表达式移除所有衔接片段标识
    gap_pattern = r'\[衔接片段：[^\]]*\]'
    
    # 移除衔接片段，保留用户文本
    cleaned_text = re.sub(gap_pattern, '', text)
    
    # 清理多余的空白字符
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    # 如果清理后没有内容，返回 None
    if not cleaned_text:
        return None
    
    logger.debug(f"提取原始文本: '{text[:50]}...' -> '{cleaned_text[:50]}...'")
    return cleaned_text


def format_text_for_subtitles(text: Optional[str]) -> Optional[str]:
    """
    格式化文本用于字幕生成
    - 从合并文本中提取用户原始文本
    - 对用户文本进行字幕格式化处理
    
    Args:
        text: 原始文本（可能包含衔接片段）
        
    Returns:
        Optional[str]: 格式化后的字幕文本，如果不适合作为字幕则返回 None
    """
    # 首先提取原始用户文本
    original_text = extract_original_text_from_merged_segment(text)
    if not original_text:
        return None
    
    # 字幕文本处理
    subtitle_text = original_text.strip()
    
    # 移除过长的文本（字幕应该简洁）
    if len(subtitle_text) > 100:  # 字幕长度限制
        subtitle_text = subtitle_text[:97] + "..."
    
    # 清理特殊字符（保留基本标点）
    subtitle_text = re.sub(r'[^\w\s\u4e00-\u9fff.,!?;:\'"-]', '', subtitle_text)
    
    if not subtitle_text.strip():
        return None
    
    return subtitle_text
