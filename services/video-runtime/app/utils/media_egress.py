"""外接厂商媒体出口解析：把本地存储的 URL 换成厂商可拉取的公网形态。

同一份代码适配多部署，靠 STORAGE_BACKEND + MEDIA_EGRESS_MODE 区分（无需改代码）：

  - STORAGE_BACKEND=s3（AWS / MinIO 公网 / R2 …）
        媒体 URL 本就是公网 CDN → 直接透传，什么都不做。

  - STORAGE_BACKEND=local（自托管，文件在本机 http://localhost:8000/files/...）
        远程厂商拉不到 localhost，按 MEDIA_EGRESS_MODE 处理：
          auto / provider_upload（默认）：上传到 WaveSpeed /media/upload/binary 换回公网 URL；
          base64：仅对「声明支持 base64 入参」的调用内联为 data URI（多数厂商不支持，故非默认）；
          public_base：把 /files/<key> 前缀改写成 MEDIA_PUBLIC_BASE_URL（给用隧道/反代暴露服务的人）。

非本地 URL / 已是 data: / 找不到文件 / 处理失败 → 一律原样返回，绝不阻断主流程（交由厂商自身报错）。
"""
import asyncio
import base64
import logging
import os
import pathlib
from typing import Optional
from urllib.parse import unquote, urlparse

import aiohttp

logger = logging.getLogger(__name__)

_WAVESPEED_UPLOAD_URL = "https://api.wavespeed.ai/api/v3/media/upload/binary"

_MIME_BY_EXT = {
    ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".tiff": "image/tiff",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg",
}


def _egress_mode() -> str:
    return (os.getenv("MEDIA_EGRESS_MODE", "auto") or "auto").strip().lower()


def _guess_mime(path: str) -> str:
    return _MIME_BY_EXT.get(pathlib.Path(path).suffix.lower(), "application/octet-stream")


def _local_path_for(url: str) -> Optional[str]:
    """本地存储且是我们自己的 URL 时，返回磁盘绝对路径；否则 None（表示无需/无法出口处理）。"""
    from app.utils.s3_utils import s3_utils, is_our_cdn_url, _storage_is_local

    if not _storage_is_local() and not (
        (urlparse(url.strip()).hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
        and urlparse(url.strip()).path.startswith("/files/")
    ):
        return None  # 对象存储：URL 已是公网，透传
    parsed = urlparse(url.strip())
    local_host = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
    shared_media_url = local_host and parsed.path.startswith("/files/")
    if not is_our_cdn_url(url) and not shared_media_url:
        return None  # 已是外部公网 URL
    file_key = (unquote(parsed.path[len("/files/"):]).lstrip("/")
                if shared_media_url else s3_utils.cdn_url_to_s3_key(url))
    if not file_key:
        return None
    repository_store = pathlib.Path(__file__).resolve().parents[4] / "data" / "uploads"
    configured_store = pathlib.Path(getattr(s3_utils, "_local_dir", repository_store))
    roots = (configured_store.resolve(), repository_store.resolve())
    for candidate in (configured_store / file_key, repository_store / file_key):
        resolved = candidate.resolve()
        if any(root == resolved or root in resolved.parents for root in roots) and resolved.is_file():
            return str(resolved)
    return None


async def resolve_outbound_media_url(url: str, *, accepts_base64: bool = False) -> str:
    """把单个媒体 URL 解析成远程厂商可拉取的形态。见模块 docstring。"""
    if not url or not isinstance(url, str):
        return url
    u = url.strip()
    if not u or u.startswith("data:"):
        return url

    try:
        local_path = _local_path_for(u)
        if not local_path:
            return url  # 公网 / 非本机 / 文件缺失 → 透传

        mode = _egress_mode()
        if mode == "public_base":
            return _rewrite_public_base(u)
        if mode == "base64" and accepts_base64:
            return await _to_data_uri(local_path)
        # auto / provider_upload（默认）
        uploaded = await _upload_to_wavespeed(local_path)
        return uploaded or url
    except Exception as e:
        logger.warning("media egress 解析失败 url=%s err=%s", u[:120], e)
        return url


def _rewrite_public_base(url: str) -> str:
    base = (os.getenv("MEDIA_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    idx = url.find("/files/")
    if not base or idx < 0:
        logger.warning("media egress public_base：未配置 MEDIA_PUBLIC_BASE_URL 或 URL 无 /files/ 前缀")
        return url
    return base + url[idx:]


async def _to_data_uri(local_path: str) -> str:
    raw = await asyncio.to_thread(pathlib.Path(local_path).read_bytes)
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{_guess_mime(local_path)};base64,{b64}"


async def _upload_to_wavespeed(local_path: str) -> Optional[str]:
    """上传本地文件到 WaveSpeed，成功返回公网 URL（data.url），失败返回 None。"""
    api_key = os.getenv("WAVESPEED_API_KEY")
    if not api_key:
        logger.warning("media egress provider_upload：未配置 WAVESPEED_API_KEY")
        return None
    raw = await asyncio.to_thread(pathlib.Path(local_path).read_bytes)
    form = aiohttp.FormData()
    form.add_field("file", raw, filename=os.path.basename(local_path), content_type=_guess_mime(local_path))
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            _WAVESPEED_UPLOAD_URL, headers={"Authorization": f"Bearer {api_key}"}, data=form
        ) as resp:
            body = await resp.text()
            if resp.status != 200:
                logger.warning("media egress WaveSpeed 上传失败 %s: %s", resp.status, body[:200])
                return None
            import json as _json

            data = (_json.loads(body).get("data") or {})
            # 实测返回 data.download_url（文档写的是 data.url，两者都兜住）
            public_url = (data.get("download_url") or data.get("url")) if isinstance(data, dict) else None
            if not public_url:
                logger.warning("media egress WaveSpeed 上传返回无 url: %s", body[:200])
                return None
            logger.info("media egress: 已上传 %s → %s", os.path.basename(local_path), public_url)
            return public_url
