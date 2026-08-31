"""
多模态后处理：将 invoke 后消息中的锚点（__IMG_var__, __IMGLIST_var__, __AUD_var__, __VID_var__, __IMG_BLOCK_var_idx__）
替换为真实的 content 块（image_url / media），供 LLM 多模态输入使用。
"""
from __future__ import annotations

import re
import logging
import base64
import mimetypes
from typing import List, Dict, Any, Optional

import aiohttp
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)
MAX_AUDIO_DOWNLOAD_BYTES = 20 * 1024 * 1024

# 单媒体锚点: __IMG_var__, __AUD_var__, __VID_var__
_ANCHOR_SINGLE = re.compile(r"__(IMG|AUD|VID)_(\w+)__")
# 多图列表锚点（统一写法，调用方只传 list 变量）: __IMGLIST_var__
_ANCHOR_IMG_LIST = re.compile(r"__(IMGLIST)_(\w+)__")
# 多图列表锚点（模板循环写法）: __IMG_BLOCK_var_0__, __IMG_BLOCK_var_1__
_ANCHOR_IMG_BLOCK = re.compile(r"__(IMG_BLOCK)_(\w+)_(\d+)__")


def _content_to_segments(content: str) -> List[tuple]:
    """
    将 content 字符串按锚点拆成 (type, value) 列表。
    type: "text" | "image" | "audio" | "video" | "image_block"
    value: 文本片段 或 变量名 或 (变量名, index)
    """
    segments: List[tuple] = []
    last_end = 0
    # 合并所有锚点，按出现顺序处理
    all_matches: List[tuple] = []
    for m in _ANCHOR_SINGLE.finditer(content):
        all_matches.append((m.start(), m.end(), "single", m.group(1).upper(), m.group(2), None))
    for m in _ANCHOR_IMG_LIST.finditer(content):
        all_matches.append((m.start(), m.end(), "imglist", "IMGLIST", m.group(2), None))
    for m in _ANCHOR_IMG_BLOCK.finditer(content):
        all_matches.append((m.start(), m.end(), "block", "IMG", m.group(2), int(m.group(3))))
    all_matches.sort(key=lambda x: x[0])

    for start, end, kind, media_type, var_name, idx in all_matches:
        if start > last_end:
            text_seg = content[last_end:start]
            if text_seg.strip():
                segments.append(("text", text_seg))
        if kind == "single":
            segments.append((media_type, var_name))
        elif kind == "imglist":
            segments.append(("image_list", var_name))
        else:
            segments.append(("image_block", (var_name, idx)))
        last_end = end

    if last_end < len(content):
        text_seg = content[last_end:]
        if text_seg.strip():
            segments.append(("text", text_seg))
    return segments


def _to_image_block(url: str) -> Dict[str, Any]:
    """生成 image_url content 块（OpenAI/Gemini 通用）。"""
    return {"type": "image_url", "image_url": {"url": url}}


def _is_openai_compatible_model(model_provider: Optional[str]) -> bool:
    mid = (model_provider or "").lower()
    return mid.startswith("gpt-") or "openai" in mid or mid.startswith("o1") or mid.startswith("o3") or mid.startswith("o4")


def _guess_audio_format(mime_type: str) -> str:
    mime = (mime_type or "").lower()
    if "mpeg" in mime or "mp3" in mime:
        return "mp3"
    if "wav" in mime or "wave" in mime:
        return "wav"
    if "m4a" in mime or "mp4" in mime or "aac" in mime:
        return "mp3"
    return "mp3"


def _to_audio_block(
    data: str,
    mime_type: str,
    file_uri: Optional[str] = None,
    model_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """按模型生成音频 content 块。Gemini 使用 media，OpenAI 使用 input_audio。"""
    if _is_openai_compatible_model(model_provider):
        if file_uri:
            logger.warning("OpenAI 兼容模型暂不使用 file_uri 音频块，回退为 inline input_audio")
        return {
            "type": "input_audio",
            "input_audio": {
                "data": data,
                "format": _guess_audio_format(mime_type),
            },
        }
    if file_uri:
        return {"type": "media", "file_uri": file_uri, "mime_type": mime_type}
    return {"type": "media", "data": data, "mime_type": mime_type}


async def _resolve_video_content(value: Any) -> Optional[Dict[str, Any]]:
    """将 video_content（URL 或 path 或已生成的 dict）转为 media content 块。"""
    if value is None:
        return None
    if isinstance(value, dict) and "type" in value and value.get("type") == "media":
        return value
    from app.chat.utils.file_utils import prepare_video_for_llm

    if isinstance(value, str):
        url_or_path = value
    elif isinstance(value, dict):
        url_or_path = value.get("url") or value.get("path")
    else:
        url_or_path = None
    if not url_or_path:
        return None
    try:
        video_content = await prepare_video_for_llm(
            video_url=url_or_path if "://" in str(url_or_path) else None,
            video_path=url_or_path if "://" not in str(url_or_path) else None,
            mime_type="video/mp4",
            max_wait_time=300,
            max_base64_size_mb=20.0,
        )
        return video_content.to_media_content()
    except Exception as e:
        logger.warning("prepare_video_for_llm 失败: %s", e)
        return None


async def _resolve_audio_content(value: Any) -> Optional[Dict[str, Any]]:
    """将 audio_content 转为 media 块。支持 {data, mime_type}（inline）或 {file_uri, mime_type}（大文件上传到 Google）。"""
    if value is None:
        return None
    if isinstance(value, dict) and "mime_type" in value:
        if "file_uri" in value:
            return {"type": "media", "file_uri": value["file_uri"], "mime_type": value["mime_type"]}
        if "data" in value:
            return {"type": "media", "data": value["data"], "mime_type": value["mime_type"]}
    url_or_path = None
    mime_type = "audio/mpeg"
    if isinstance(value, dict):
        url_or_path = value.get("url") or value.get("path")
        mime_type = value.get("mime_type") or mimetypes.guess_type(str(url_or_path or ""))[0] or mime_type
    elif isinstance(value, str) and ("://" in value or value.startswith("/")):
        url_or_path = value
        mime_type = mimetypes.guess_type(value)[0] or mime_type
    if not url_or_path:
        return None
    try:
        if isinstance(url_or_path, str) and "://" in url_or_path:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url_or_path) as response:
                    response.raise_for_status()
                    audio_bytes = await response.read()
                    if len(audio_bytes) > MAX_AUDIO_DOWNLOAD_BYTES:
                        logger.warning("AUDIO URL 下载后超过大小限制，已跳过: %s", url_or_path)
                        return None
                    response_mime = response.headers.get("Content-Type", "").split(";")[0].strip()
                    if response_mime:
                        mime_type = response_mime
        else:
            with open(str(url_or_path), "rb") as file_obj:
                audio_bytes = file_obj.read()
            if len(audio_bytes) > MAX_AUDIO_DOWNLOAD_BYTES:
                logger.warning("AUDIO 本地文件超过大小限制，已跳过: %s", url_or_path)
                return None
        return {
            "type": "media",
            "data": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": mime_type,
        }
    except Exception as exc:
        logger.warning("解析 AUDIO 内容失败: %s", exc)
        return None
    return None


def _build_content_list_from_segments(
    segments: List[tuple],
    template_data: Dict[str, Any],
    resolved_video: Dict[str, Any],
    resolved_audio: Dict[str, Any],
    model_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    根据 segments 和 template_data 组装 content list。
    resolved_video / resolved_audio 由上层在异步中填好（按变量名缓存）。
    """
    result: List[Dict[str, Any]] = []
    for seg in segments:
        kind, val = seg[0], seg[1]
        if kind == "text":
            result.append({"type": "text", "text": val})
        elif kind == "IMG":
            url = template_data.get(val)
            if isinstance(url, dict):
                url = url.get("url")
            if url:
                result.append(_to_image_block(url))
        elif kind == "image_block":
            var_name, idx = val
            arr = template_data.get(var_name)
            if isinstance(arr, list) and 0 <= idx < len(arr):
                item = arr[idx]
                url = item.get("url") if isinstance(item, dict) else item
                if url:
                    result.append(_to_image_block(url))
        elif kind == "image_list":
            arr = template_data.get(val)
            if isinstance(arr, list):
                for item in arr:
                    url = item.get("url") if isinstance(item, dict) else item
                    if url:
                        result.append(_to_image_block(url))
        elif kind == "AUD":
            # 使用上层传入的 resolved_audio[var_name]
            block = resolved_audio.get(val) if isinstance(resolved_audio, dict) else resolved_audio
            if block:
                if block.get("type") == "media" and "data" in block and "mime_type" in block:
                    result.append(
                        _to_audio_block(
                            data=block["data"],
                            mime_type=block["mime_type"],
                            file_uri=block.get("file_uri"),
                            model_provider=model_provider,
                        )
                    )
                else:
                    result.append(block)
        elif kind == "VID":
            block = resolved_video.get(val) if isinstance(resolved_video, dict) else resolved_video
            if block:
                result.append(block)
    return result


async def process(
    messages: List[BaseMessage],
    template_data: Dict[str, Any],
    model_provider: Optional[str] = None,
) -> List[BaseMessage]:
    """
    遍历 messages，将 content 中的多模态锚点替换为真实 content 块。
    - __IMG_var__ -> template_data[var] 作为 image_url
    - __AUD_var__ -> template_data[var] 需为 {data, mime_type}，转为 media
    - __VID_var__ -> template_data[var] 为 URL/path 时调用 prepare_video_for_llm，转为 media
    - __IMG_BLOCK_var_idx__ -> template_data[var][idx] 作为 image_url
    - __IMGLIST_var__ -> template_data[var] 为列表时，按顺序每项作为 image_url（项可为 URL 或 {url} dict）
    """
    result: List[BaseMessage] = []
    for msg in messages:
        if not isinstance(msg, HumanMessage):
            result.append(msg)
            continue
        content = getattr(msg, "content", None)
        if content is None:
            result.append(msg)
            continue
        if isinstance(content, list):
            result.append(msg)
            continue
        if not isinstance(content, str):
            result.append(msg)
            continue
        if "__IMG_" not in content and "__IMGLIST_" not in content and "__AUD_" not in content and "__VID_" not in content:
            result.append(msg)
            continue

        segments = _content_to_segments(content)
        if not segments:
            result.append(msg)
            continue

        # 预解析 VIDEO / AUDIO（异步）
        resolved_video: Dict[str, Optional[Dict[str, Any]]] = {}
        resolved_audio: Dict[str, Optional[Dict[str, Any]]] = {}
        for seg in segments:
            if seg[0] == "VID":
                var_name = seg[1]
                if var_name not in resolved_video:
                    resolved_video[var_name] = await _resolve_video_content(template_data.get(var_name))
            elif seg[0] == "AUD":
                var_name = seg[1]
                if var_name not in resolved_audio:
                    resolved_audio[var_name] = await _resolve_audio_content(template_data.get(var_name))

        new_content = _build_content_list_from_segments(
            segments, template_data, resolved_video, resolved_audio, model_provider
        )
        if not new_content:
            result.append(msg)
            continue
        result.append(HumanMessage(content=new_content))
    return result
