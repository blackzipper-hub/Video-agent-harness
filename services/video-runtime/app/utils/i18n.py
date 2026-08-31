"""
国际化工具函数
提供后端i18n翻译功能和语言上下文管理
"""
import asyncio
import json
from pathlib import Path
from contextvars import ContextVar
from typing import Dict, Any, Optional

# ============================================================================
# 语言上下文管理 (原 language_context.py 的功能)
# ============================================================================

# 创建语言上下文变量，默认为'en'（未识别时）
language_context: ContextVar[str] = ContextVar('language', default='en')

def get_current_language() -> str:
    """
    获取当前请求的语言设置
    
    Returns:
        ISO 639-1语言代码（如en, zh, fr, es, ja等）
    """
    return language_context.get()

def set_current_language(language_code: str) -> None:
    """
    设置当前请求的语言
    
    Args:
        language_code: ISO 639-1语言代码（如en, zh, fr, es, ja等）
    
    注意：直接存储LLM返回的语言代码，不做任何转换或标准化
    """
    language_context.set(language_code.lower())

# ============================================================================
# i18n 翻译功能
# ============================================================================

# 翻译JSON文件路径
I18N_DIR = Path(__file__).parent.parent / "i18n" / "locales"

# 内存缓存的翻译数据
_translations_cache: Dict[str, Dict[str, Any]] = {}


def _load_translations(lang: str) -> Dict[str, Any]:
    """加载指定语言的翻译文件"""
    if lang in _translations_cache:
        return _translations_cache[lang]
    
    try:
        translation_file = I18N_DIR / f"{lang}.json"
        if not translation_file.exists():
            # 如果语言文件不存在，尝试加载英文
            translation_file = I18N_DIR / "en.json"
        
        if translation_file.exists():
            with open(translation_file, 'r', encoding='utf-8') as f:
                translations = json.load(f)
                _translations_cache[lang] = translations
                return translations
    except Exception as e:
        print(f"Failed to load translation file for {lang}: {e}")
    
    # 返回空字典作为回退
    return {}


def get_i18n_message(key: str, default: Optional[str] = None, params: Optional[Dict[str, Any]] = None, lang: Optional[str] = None) -> str:
    """
    根据key获取国际化消息，支持参数替换
    
    Args:
        key: 翻译key，支持嵌套key，如 "common.success"
        default: 如果找不到翻译时的默认值
        params: 参数字典，用于替换消息中的占位符，如 {"count": 5}
        lang: 语言代码，默认为None（自动获取）
        
    Returns:
        str: 翻译后的消息
        
    Examples:
        >>> get_i18n_message("common.success")
        "Success" (for en) or "成功" (for zh)
        
        >>> get_i18n_message("video_segments.processing_completed", params={"count": 5})
        "Video segments processing completed, processed 5 segments"
        
        >>> get_i18n_message("music.generated", default="Music generated", lang="en")
        "Background music generated successfully"
    """
    if lang is None:
        lang = get_current_language()
    
    translations = _load_translations(lang)
    
    # 处理嵌套的key，如 "common.success"
    keys = key.split(".")
    current = translations
    
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            # 如果找不到，尝试加载英文作为回退
            if lang != "en":
                en_translations = _load_translations("en")
                current = en_translations
                for k2 in keys:
                    if isinstance(current, dict) and k2 in current:
                        current = current[k2]
                    else:
                        final_message = default or key
                        # 如果有参数，进行替换
                        if params and isinstance(params, dict):
                            try:
                                final_message = final_message.format(**params)
                            except (KeyError, ValueError) as e:
                                print(f"Parameter substitution failed for key '{key}': {e}")
                        return final_message
                final_message = current if isinstance(current, str) else (default or key)
            else:
                final_message = default or key
    
            # 如果有参数，进行替换
            if params and isinstance(params, dict):
                try:
                    final_message = final_message.format(**params)
                except (KeyError, ValueError) as e:
                    print(f"Parameter substitution failed for key '{key}': {e}")
            return final_message
    
    # 获取最终的消息文本
    final_message = current if isinstance(current, str) else (default or key)
    
    # 如果有参数，进行替换
    if params and isinstance(params, dict):
        try:
            final_message = final_message.format(**params)
        except (KeyError, ValueError) as e:
            # 如果参数替换失败，返回原始消息
            print(f"Parameter substitution failed for key '{key}': {e}")
    
    return final_message


async def get_i18n_message_async(
    key: str,
    default: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    lang: Optional[str] = None,
) -> str:
    """
    异步版本：在线程池中执行 get_i18n_message，避免阻塞事件循环。
    在 async 上下文中应使用本函数并 await。
    """
    return await asyncio.to_thread(get_i18n_message, key, default=default, params=params, lang=lang)

