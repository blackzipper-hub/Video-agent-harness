"""Generic host capabilities for sandbox / external Skills.

Platform-stable surface: http.request, artifact.read/write, media.concat,
media.extract_frame, media.trim, media.speed_adjust, media.audio_trim,
media.audio_analyze, media.mix_audio, provider.generate.
No vendor-specific branching belongs here.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

SUPPORTED_HOST_CAPABILITIES = frozenset({
    "http.fetch",
    "http.request",
    "artifact.read",
    "artifact.write",
    "log",
    "progress",
    "media.concat",
    "media.extract_frame",
    "media.trim",
    "media.speed_adjust",
    "media.audio_trim",
    "media.audio_analyze",
    "media.mix_audio",
    "provider.generate",
})

_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)


def normalize_hostname(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if ":" in host and not host.count(":") > 1:
        host = host.split(":", 1)[0]
    return host


def host_allowed(hostname: str, allowed_domains: tuple[str, ...] | list[str]) -> bool:
    host = normalize_hostname(hostname)
    if not host or host in {"localhost", "127.0.0.1", "::1"}:
        return False
    allowed = {normalize_hostname(item) for item in allowed_domains if item}
    # Exact hostname match only (no implicit subdomain expansion).
    return host in allowed


class HostGatewayError(ValueError):
    pass


class HostGateway:
    """Execute declared host capabilities with allowlist enforcement."""

    def __init__(self, *, default_timeout: float = 60.0):
        self.default_timeout = default_timeout

    async def dispatch(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        allowed_capabilities: tuple[str, ...] | list[str],
        allowed_domains: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        if capability not in SUPPORTED_HOST_CAPABILITIES:
            raise HostGatewayError(f"unsupported host capability: {capability}")
        # http.fetch is an alias of http.request GET-only.
        permitted = set(allowed_capabilities)
        if "http.fetch" in permitted:
            permitted.add("http.request")
        if "http.request" in permitted:
            permitted.add("http.fetch")
        if capability not in permitted:
            raise HostGatewayError(f"host capability not granted: {capability}")

        if capability in {"http.fetch", "http.request"}:
            return await self.http_request(payload, allowed_domains=allowed_domains)
        if capability == "media.concat":
            return await self.media_concat(payload)
        if capability == "media.extract_frame":
            return await self.media_extract_frame(payload)
        if capability == "media.trim":
            return await self.media_trim(payload)
        if capability == "media.speed_adjust":
            return await self.media_speed_adjust(payload)
        if capability == "media.audio_trim":
            return await self.media_audio_trim(payload)
        if capability == "media.audio_analyze":
            return await self.media_audio_analyze(payload)
        if capability == "media.mix_audio":
            return await self.media_mix_audio(payload)
        if capability == "provider.generate":
            return await self.provider_generate(payload)
        if capability == "artifact.read":
            return self.artifact_read(payload)
        if capability == "artifact.write":
            return self.artifact_write(payload)
        if capability in {"log", "progress"}:
            message = str(payload.get("message") or payload.get("text") or "")
            logger.info("sandbox host.%s: %s", capability, message[:500])
            return {"ok": True, "capability": capability}
        raise HostGatewayError(f"unhandled host capability: {capability}")

    async def http_request(
        self,
        payload: dict[str, Any],
        *,
        allowed_domains: tuple[str, ...] | list[str],
    ) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        method = str(payload.get("method") or "GET").upper()
        if method == "GET" and not payload.get("method"):
            method = "GET"
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise HostGatewayError(f"unsupported HTTP method: {method}")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HostGatewayError("url must be absolute http/https")
        if not host_allowed(parsed.hostname or "", allowed_domains):
            raise HostGatewayError(
                f"host not in allowlist: {parsed.hostname}; "
                f"allowed={list(allowed_domains)}"
            )
        headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
        timeout = float(payload.get("timeout") or self.default_timeout)
        body = payload.get("json")
        data = payload.get("data")
        max_bytes = int(payload.get("max_bytes") or 256 * 1024)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(
                method,
                url,
                headers={str(k): str(v) for k, v in headers.items()},
                json=body if body is not None else None,
                content=data if body is None and data is not None else None,
            )
        content = response.content[: max(0, max_bytes)]
        truncated = len(response.content) > max_bytes
        text: str | None
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        result: dict[str, Any] = {
            "ok": response.is_success,
            "status_code": response.status_code,
            "url": str(response.url),
            "headers": {
                "content-type": response.headers.get("content-type", ""),
                "retry-after": response.headers.get("retry-after"),
            },
            "truncated": truncated,
        }
        if text is not None:
            result["text"] = text
            try:
                result["json"] = json.loads(text)
            except json.JSONDecodeError:
                pass
        else:
            result["content_base64"] = __import__("base64").b64encode(content).decode("ascii")
        return result

    async def media_concat(self, payload: dict[str, Any]) -> dict[str, Any]:
        urls = payload.get("video_urls") or payload.get("urls") or []
        if not isinstance(urls, list) or len(urls) < 1:
            raise HostGatewayError("media.concat requires video_urls: string[]")
        ordered: list[str] = []
        seen: set[str] = set()
        for item in urls:
            if not isinstance(item, str) or not item.strip():
                continue
            url = item.strip()
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
        if not ordered:
            raise HostGatewayError("media.concat requires at least one video URL")
        if len(ordered) == 1:
            return {
                "result_url": ordered[0],
                "uri": ordered[0],
                "video_count": 1,
                "assembly_mode": "passthrough",
            }
        from app.utils import media_service_client as msc

        run_id = str(payload.get("run_id") or f"host-concat-{uuid4().hex[:12]}")
        normalize = bool(payload.get("normalize", True))
        try:
            transition_duration = float(payload.get("transition_duration") or 0.0)
        except (TypeError, ValueError) as exc:
            raise HostGatewayError("media.concat transition_duration must be numeric") from exc
        if not 0.0 <= transition_duration <= 1.0:
            raise HostGatewayError("media.concat transition_duration must be between 0 and 1 second")
        concat = await msc.video_concat(
            ordered,
            run_id=run_id,
            normalize=normalize,
            transition_duration=transition_duration,
        )
        return {
            "result_url": concat["result_url"],
            "uri": concat["result_url"],
            "duration": concat.get("duration"),
            "video_count": len(ordered),
            "source_video_urls": ordered,
            "transition_duration": transition_duration,
            "assembly_mode": "host_media_concat",
        }

    async def media_extract_frame(self, payload: dict[str, Any]) -> dict[str, Any]:
        video_url = (
            payload.get("video_url")
            or payload.get("uri")
            or payload.get("url")
            or ""
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise HostGatewayError("media.extract_frame requires video_url")
        position = str(payload.get("position") or "timestamp").strip().lower()
        if position not in {"timestamp", "last"}:
            raise HostGatewayError("media.extract_frame position must be timestamp or last")
        timestamp = None
        if position == "timestamp":
            try:
                timestamp = float(payload.get("timestamp", 0))
            except (TypeError, ValueError) as exc:
                raise HostGatewayError("media.extract_frame requires numeric timestamp") from exc
            if timestamp < 0:
                raise HostGatewayError("media.extract_frame timestamp must be >= 0")
        image_format = str(payload.get("format") or "jpeg").strip().lower()
        if image_format in {"jpg", "jpeg"}:
            image_format = "jpeg"
        elif image_format != "png":
            raise HostGatewayError("media.extract_frame format must be jpeg or png")

        from app.utils import media_service_client as msc

        run_id = str(payload.get("run_id") or f"host-frame-{uuid4().hex[:12]}")
        result = await msc.video_extract_frame(
            video_url.strip(),
            timestamp,
            run_id=run_id,
            image_format=image_format,
            position=position,
        )
        uri = result.get("result_url")
        if not uri:
            raise HostGatewayError("media.extract_frame returned no result_url")
        return {
            "result_url": uri,
            "uri": uri,
            "image_url": uri,
            "timestamp": result.get("timestamp", timestamp or 0.0),
            "position": position,
            "width": result.get("width"),
            "height": result.get("height"),
            "format": result.get("format") or image_format,
            "source_video_url": video_url.strip(),
            "title": f"Frame @ {float(result.get('timestamp', timestamp or 0.0)):.2f}s",
            "summary": (
                f"Extracted frame at {float(result.get('timestamp', timestamp or 0.0)):.2f}s "
                f"from video"
            ),
        }

    async def media_trim(self, payload: dict[str, Any]) -> dict[str, Any]:
        video_url = (
            payload.get("video_url")
            or payload.get("uri")
            or payload.get("url")
            or ""
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise HostGatewayError("media.trim requires video_url")
        try:
            target_duration = float(payload.get("target_duration"))
        except (TypeError, ValueError) as exc:
            raise HostGatewayError("media.trim requires numeric target_duration") from exc
        if target_duration <= 0:
            raise HostGatewayError("media.trim target_duration must be > 0")
        mode = str(payload.get("mode") or "trim_only").strip() or "trim_only"
        from app.utils import media_service_client as msc

        run_id = str(payload.get("run_id") or f"host-trim-{uuid4().hex[:12]}")
        result = await msc.video_trim(
            video_url.strip(),
            target_duration,
            run_id=run_id,
            mode=mode,
            tolerance=payload.get("tolerance"),
        )
        uri = result.get("result_url")
        if not uri:
            raise HostGatewayError("media.trim returned no result_url")
        return {
            "result_url": uri,
            "uri": uri,
            "video_url": uri,
            "duration": result.get("duration"),
            "target_duration": target_duration,
            "source_video_url": video_url.strip(),
            "summary": f"Trimmed video to ~{target_duration:.2f}s",
        }

    async def media_speed_adjust(self, payload: dict[str, Any]) -> dict[str, Any]:
        video_url = (
            payload.get("video_url")
            or payload.get("uri")
            or payload.get("url")
            or ""
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise HostGatewayError("media.speed_adjust requires video_url")
        try:
            target_duration = float(payload.get("target_duration"))
        except (TypeError, ValueError) as exc:
            raise HostGatewayError(
                "media.speed_adjust requires numeric target_duration"
            ) from exc
        if target_duration <= 0:
            raise HostGatewayError("media.speed_adjust target_duration must be > 0")
        from app.utils import media_service_client as msc

        run_id = str(payload.get("run_id") or f"host-speed-{uuid4().hex[:12]}")
        result = await msc.video_speed_adjust(
            video_url.strip(),
            target_duration,
            run_id=run_id,
        )
        uri = result.get("result_url")
        if not uri:
            raise HostGatewayError("media.speed_adjust returned no result_url")
        return {
            "result_url": uri,
            "uri": uri,
            "video_url": uri,
            "duration": result.get("duration"),
            "target_duration": target_duration,
            "source_video_url": video_url.strip(),
            "summary": f"Speed-adjusted video to ~{target_duration:.2f}s",
        }

    async def media_audio_trim(self, payload: dict[str, Any]) -> dict[str, Any]:
        audio_url = (
            payload.get("audio_url")
            or payload.get("uri")
            or payload.get("url")
            or ""
        )
        if not isinstance(audio_url, str) or not audio_url.strip():
            raise HostGatewayError("media.audio_trim requires audio_url")
        try:
            start = float(payload.get("start", payload.get("start_sec", 0.0)))
            duration = float(payload.get("duration", payload.get("duration_sec")))
        except (TypeError, ValueError) as exc:
            raise HostGatewayError(
                "media.audio_trim requires numeric start and duration"
            ) from exc
        if start < 0:
            raise HostGatewayError("media.audio_trim start must be >= 0")
        if duration <= 0:
            raise HostGatewayError("media.audio_trim duration must be > 0")

        fade_in = payload.get("fade_in_sec")
        fade_out = payload.get("fade_out_sec")
        use_fade = fade_in is not None or fade_out is not None
        from app.utils import media_service_client as msc

        run_id = str(payload.get("run_id") or f"host-audio-trim-{uuid4().hex[:12]}")
        if use_fade:
            try:
                fade_in_sec = float(fade_in or 0.0)
                fade_out_sec = float(fade_out or 0.0)
            except (TypeError, ValueError) as exc:
                raise HostGatewayError(
                    "media.audio_trim fade_in_sec/fade_out_sec must be numeric"
                ) from exc
            result = await msc.audio_trim_with_fade(
                audio_url.strip(),
                start,
                duration,
                fade_in_sec,
                fade_out_sec,
                run_id=run_id,
            )
        else:
            result = await msc.audio_trim(
                audio_url.strip(),
                start,
                duration,
                run_id=run_id,
            )
        uri = result.get("result_url")
        if not uri:
            raise HostGatewayError("media.audio_trim returned no result_url")
        return {
            "result_url": uri,
            "uri": uri,
            "audio_url": uri,
            "start_sec": start,
            "duration_sec": duration,
            "source_audio_url": audio_url.strip(),
            "summary": f"Trimmed audio {start:.2f}s+{duration:.2f}s",
        }

    async def media_audio_analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.chat.v2.mv_audio import (
            SEEDANCE_MAX_AUDIO_SEC,
            build_audiomap,
            plan_seedance_segments,
            resolve_master_window,
        )
        from app.utils import media_service_client as msc

        audio_url = (
            payload.get("audio_url")
            or payload.get("uri")
            or payload.get("url")
            or ""
        )
        if not isinstance(audio_url, str) or not audio_url.strip():
            raise HostGatewayError("media.audio_analyze requires audio_url")

        run_id = str(payload.get("run_id") or f"host-audio-analyze-{uuid4().hex[:12]}")
        info = await msc.audio_info(audio_url.strip())
        try:
            audio_duration = float(info.get("duration") or 0.0)
        except (TypeError, ValueError) as exc:
            raise HostGatewayError("media.audio_analyze: invalid audio duration") from exc
        if audio_duration <= 0:
            raise HostGatewayError("media.audio_analyze: audio duration must be > 0")

        start = payload.get("start_sec", payload.get("start"))
        end = payload.get("end_sec", payload.get("end"))
        target = payload.get("target_duration_sec", payload.get("target_duration"))
        try:
            start_f = float(start) if start is not None else None
            end_f = float(end) if end is not None else None
            target_f = float(target) if target is not None else None
        except (TypeError, ValueError) as exc:
            raise HostGatewayError(
                "media.audio_analyze start/end/target_duration must be numeric"
            ) from exc

        method = "full_track"
        smart_clip_payload: dict[str, Any] | None = None
        transcription = payload.get("transcription")
        if (
            target_f is not None
            and transcription is not None
            and audio_duration > target_f + 1.0
        ):
            try:
                from app.services.agent.video.music_smart_clip_service import (
                    analyze_music_smart_clip,
                )

                analysis = await analyze_music_smart_clip(
                    audio_url.strip(),
                    target_f,
                    transcription,
                    audio_duration_sec=audio_duration,
                )
                start_f = float(analysis.recommended.start_sec)
                end_f = float(analysis.recommended.end_sec)
                method = f"smart_clip:{analysis.method}"
                smart_clip_payload = analysis.model_dump()
            except Exception as exc:  # noqa: BLE001 — fall back to center window
                logger.warning("media.audio_analyze smart_clip failed: %s", exc)
                method = "center_window_fallback"

        try:
            window_start, window_end = resolve_master_window(
                audio_duration,
                start_sec=start_f,
                end_sec=end_f,
                target_duration_sec=target_f if start_f is None and end_f is None else None,
            )
        except ValueError as exc:
            raise HostGatewayError(str(exc)) from exc

        if method == "full_track" and target_f is not None and (window_end - window_start) < audio_duration - 1e-6:
            method = "center_window"

        try:
            max_seg = float(payload.get("max_segment_sec") or SEEDANCE_MAX_AUDIO_SEC)
        except (TypeError, ValueError) as exc:
            raise HostGatewayError("max_segment_sec must be numeric") from exc

        try:
            segments = plan_seedance_segments(
                window_end - window_start,
                window_start_sec=window_start,
                max_segment_sec=max_seg,
            )
        except ValueError as exc:
            raise HostGatewayError(str(exc)) from exc

        audiomap = build_audiomap(
            audio_url=audio_url.strip(),
            audio_duration_sec=audio_duration,
            window_start_sec=window_start,
            window_end_sec=window_end,
            segments=segments,
            method=method,
            smart_clip=smart_clip_payload,
        )
        audiomap["run_id"] = run_id
        audiomap["summary"] = (
            f"Analyzed audio into {len(segments)} Seedance segment(s); "
            f"master {audiomap['master']['duration_sec']:.2f}s"
        )
        audiomap["title"] = "MV audiomap"
        return audiomap

    async def media_mix_audio(self, payload: dict[str, Any]) -> dict[str, Any]:
        video_url = (
            payload.get("video_url")
            or payload.get("uri")
            or ""
        )
        audio_url = (
            payload.get("audio_url")
            or payload.get("music_url")
            or ""
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise HostGatewayError("media.mix_audio requires video_url")
        if not isinstance(audio_url, str) or not audio_url.strip():
            raise HostGatewayError("media.mix_audio requires audio_url")

        mode = str(payload.get("mode") or "replace").strip().lower() or "replace"
        if mode not in {"replace", "overlay"}:
            raise HostGatewayError("media.mix_audio mode must be replace or overlay")

        from app.utils import media_service_client as msc

        run_id = str(payload.get("run_id") or f"host-mix-audio-{uuid4().hex[:12]}")
        video_url = video_url.strip()
        audio_url = audio_url.strip()

        if mode == "replace":
            # Map master audio over picture; drops in-clip Seedance audio.
            result = await msc.video_add_audio(
                video_url,
                [{"audio_url": audio_url}],
                run_id=run_id,
            )
            audio_volume = 1.0
            loop_audio = False
        else:
            try:
                audio_volume = float(payload.get("audio_volume", 0.35))
            except (TypeError, ValueError) as exc:
                raise HostGatewayError("audio_volume must be numeric") from exc
            loop_audio = bool(payload.get("loop_audio", False))
            result = await msc.video_mix_audio(
                video_url,
                audio_url,
                run_id=run_id,
                video_volume=float(payload.get("video_volume", 1.0) or 1.0),
                audio_volume=audio_volume,
                loop_audio=loop_audio,
            )

        uri = result.get("result_url")
        if not uri:
            raise HostGatewayError("media.mix_audio returned no result_url")
        return {
            "result_url": uri,
            "uri": uri,
            "video_url": uri,
            "source_video_url": video_url,
            "source_audio_url": audio_url,
            "mode": mode,
            "audio_volume": audio_volume if mode == "overlay" else 1.0,
            "loop_audio": loop_audio,
            "assembly_mode": f"host_media_mix_{mode}",
            "summary": f"Mixed audio onto video (mode={mode})",
        }

    async def provider_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.chat.config import get_settings
        from app.chat.v2.provider_bridge import generate_video

        return await generate_video(
            payload,
            fallbacks_json=get_settings().DEEP_AGENT_V2_PROVIDER_FALLBACKS_JSON,
        )

    @staticmethod
    def artifact_read(payload: dict[str, Any]) -> dict[str, Any]:
        uri = payload.get("uri") or payload.get("url")
        if not isinstance(uri, str) or not uri.strip():
            raise HostGatewayError("artifact.read requires uri")
        return {
            "uri": uri.strip(),
            "type": payload.get("type") or "unknown",
            "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        }

    @staticmethod
    def artifact_write(payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize an artifact dict for sandbox result.json."""
        if not isinstance(payload, dict):
            raise HostGatewayError("artifact.write requires an object payload")
        uri = payload.get("uri") or payload.get("url") or payload.get("video_url")
        artifact = {
            "title": payload.get("title") or "skill-output",
            "summary": payload.get("summary") or payload.get("message") or "",
            "uri": uri,
            "type": payload.get("type") or "json",
            "metadata": payload.get("metadata")
            if isinstance(payload.get("metadata"), dict)
            else {k: v for k, v in payload.items() if k not in {"title", "summary", "uri", "type"}},
        }
        return {"artifact": artifact}
