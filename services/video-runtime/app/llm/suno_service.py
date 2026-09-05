"""
SunoAPI 音乐生成服务
"""
import asyncio
import json
import aiohttp
import logging
from typing import Any, Dict, Optional
from tenacity import retry, stop_after_delay, wait_fixed, retry_if_exception_type, retry_if_result
from app.models.image_result import MusicGenerationResult, MusicProvider
from app.utils.s3_utils import s3_utils
from app.services.agent.utils.cancellation import raise_if_cancelled
import os

logger = logging.getLogger(__name__)


class SunoRetryException(Exception):
    """Suno API 需要重试的异常基类"""
    
    def __init__(self, message: str, retry_after: int = 10, max_retries: int = 30):
        super().__init__(message)
        self.retry_after = retry_after
        self.max_retries = max_retries


class SunoTaskNotReadyException(SunoRetryException):
    """任务未准备好异常 - 需要重试"""
    
    def __init__(self, message: str = "Task not ready, please wait"):
        super().__init__(message, retry_after=10, max_retries=30)


class SunoTaskPendingException(SunoRetryException):
    """任务待处理异常 - 需要重试"""
    
    def __init__(self, message: str = "Task is pending"):
        super().__init__(message, retry_after=10, max_retries=30)


class SunoTaskRunningException(SunoRetryException):
    """任务运行中异常 - 需要重试"""
    
    def __init__(self, message: str = "Task is running"):
        super().__init__(message, retry_after=10, max_retries=30)


class SunoRateLimitException(SunoRetryException):
    """API 速率限制异常 - 需要重试"""
    
    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60):
        super().__init__(message, retry_after=retry_after, max_retries=10)


class SunoServerErrorException(SunoRetryException):
    """服务器错误异常 - 需要重试"""
    
    def __init__(self, message: str = "Server error", retry_after: int = 30):
        super().__init__(message, retry_after=retry_after, max_retries=5)


class SunoFinalException(Exception):
    """Suno API 最终失败异常 - 不需要重试"""
    pass


def _clip_duration_seconds(raw: Any) -> int:
    """Suno often sends duration:null while audio_url is already ready."""
    if raw is None or raw == "":
        return 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        logger.warning("🎵 无法解析 duration: %s，使用默认值 0", raw)
        return 0


def _suno_wait(retry_state) -> float:
    """从异常的 retry_after 字段获取等待时间，默认 10 秒。"""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, SunoRetryException):
        return float(exc.retry_after)
    return 10.0


class SunoService:
    """SunoAPI 音乐生成服务 - 简洁版"""
    
    def __init__(self, api_key: str = None, base_url: str = "https://api.sunoapi.com"):
        self.api_key = api_key or os.getenv("SUNO_API_KEY")
        self.base_url = base_url
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}'
        }
    
    async def generate_music(
        self,
        prompt: Optional[str] = None,
        custom_mode: bool = False,
        make_instrumental: bool = True,
        lyrics: Optional[str] = None,
        mv: str = "chirp-v5-5",
        tags: Optional[str] = None,
        vocal_gender: Optional[str] = None,
        title: Optional[str] = None,
        duration: Optional[int] = None,
        generation_params: Optional[dict] = None
    ) -> MusicGenerationResult:
        """总的生成音乐方法 - 包含创建和轮询
        
        Args:
            prompt: GPT描述提示词（custom_mode=False时使用，最多400字符）
            custom_mode: 是否使用自定义模式
            make_instrumental: 是否生成纯音乐
            lyrics: 歌词内容（custom_mode=True且make_instrumental=False时使用，需包含结构标签，最多5000字符）
            mv: model version，默认 "chirp-v5-5"
            tags: 歌曲风格标签（如 "Fast 160BPM, Brief"），v4及以下最多200字符，v4.5及以上最多1000字符
            vocal_gender: 人声性别（可选，仅 v4-5+）：'f' 女声，'m' 男声
            duration: 目标时长秒数（10–360）。落在附近，不精确
            generation_params: 生成参数（用于记录）
        """
        task_id = await self.create_music_task(
            prompt=prompt,
            custom_mode=custom_mode,
            make_instrumental=make_instrumental,
            lyrics=lyrics,
            mv=mv,
            tags=tags,
            vocal_gender=vocal_gender,
            title=title,
            duration=duration,
        )
        original_prompt = prompt or lyrics or "Custom music"
        return await self.poll_task_until_complete(
            task_id, 
            original_prompt,
            generation_params=generation_params
        )
    
    async def create_music_task(
        self,
        prompt: Optional[str] = None,
        custom_mode: bool = False,
        make_instrumental: bool = True,
        lyrics: Optional[str] = None,
        mv: str = "chirp-v5-5",
        tags: Optional[str] = None,
        vocal_gender: Optional[str] = None,
        title: Optional[str] = None,
        duration: Optional[int] = None,
    ) -> str:
        """创建音乐任务
        
        Args:
            prompt: GPT描述提示词（custom_mode=False时使用，最多400字符）
            custom_mode: 是否使用自定义模式
            make_instrumental: 是否生成纯音乐
            lyrics: 歌词内容（custom_mode=True且make_instrumental=False时使用，需包含结构标签，最多5000字符）
            mv: model version，默认 "chirp-v5-5"
            tags: 歌曲风格标签（如 "Fast 160BPM, Brief"），v4及以下最多200字符，v4.5及以上最多1000字符
            vocal_gender: 人声性别（可选，仅 v4-5+）：'f' 女声，'m' 男声
            duration: 目标时长秒数（10–360）。落在附近，不精确
        """
        # 构建请求 payload（SunoAPI 现要求任意模式均带 mv；此前仅 custom_mode 传 mv 会导致 GPT/auto_lyrics 400）
        payload = {
            "custom_mode": custom_mode,
            "make_instrumental": make_instrumental,
            "mv": mv,
        }
        if vocal_gender in ("f", "m"):
            payload["vocal_gender"] = vocal_gender
        if isinstance(duration, int) and 10 <= duration <= 360:
            payload["duration"] = duration

        if custom_mode:
            # 自定义模式：prompt 字段存歌词或结构标签（器乐轨也可以带 [Intro]/[End]）
            if lyrics:
                payload["prompt"] = lyrics
            if tags:
                payload["tags"] = tags
            if isinstance(title, str) and title.strip():
                payload["title"] = title.strip()[:80]
        else:
            # GPT描述模式：使用 gpt_description_prompt
            if prompt:
                payload["gpt_description_prompt"] = prompt
            # GPT 模式下也可以使用 tags
            if tags:
                payload["tags"] = tags
        
        logger.info(f"🎵 Suno API 请求 payload: {json.dumps(payload, ensure_ascii=False)[:2000]}")
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/api/v1/suno/create", json=payload, headers=self.headers) as response:
                text = await response.text()
                if response.status >= 400:
                    logger.error(
                        "🎵 Suno API create 失败 HTTP %s body=%s",
                        response.status,
                        text[:4000],
                    )
                response.raise_for_status()
                result = json.loads(text) if text.strip() else {}
                task_id = result.get("task_id") or result.get("id")
                if not task_id:
                    raise Exception("未获取到任务ID")
                
                logger.info(f"🎵 创建任务成功: {task_id}")
                return task_id
    
    @retry(
        retry=retry_if_exception_type(SunoRetryException),
        stop=stop_after_delay(300),
        wait=_suno_wait,
        sleep=asyncio.sleep,
    )
    async def poll_task_until_complete(
        self, 
        task_id: str, 
        original_prompt: str,
        generation_params: Optional[dict] = None
    ) -> MusicGenerationResult:
        """单个轮询任务直到完成"""
        # 协作式取消：每次轮询前检查用户是否已取消，及时放弃在跑的音乐生成
        await raise_if_cancelled()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/api/v1/suno/task/{task_id}", headers=self.headers) as response:
                    # 使用 raise_for_status() 处理 HTTP 错误，但先处理特殊情况
                    if response.status == 429:
                        retry_after = int(response.headers.get('Retry-After', 60))
                        logger.warning(f"🎵 API 速率限制，{retry_after}秒后重试")
                        raise SunoRateLimitException(f"Rate limit exceeded, retry after {retry_after}s", retry_after)
                    
                    # 对于其他错误状态码，让 raise_for_status() 处理
                    try:
                        response.raise_for_status()
                    except aiohttp.ClientResponseError as e:
                        if e.status >= 500:
                            logger.error(f"🎵 task_id={task_id} 服务器错误 {e.status}，不重试")
                            raise SunoFinalException(f"Server error: {e.status}")
                        else:
                            logger.error(f"🎵 task_id={task_id} 请求失败: {e.status}")
                            raise SunoFinalException(f"Request failed with status {e.status}")
                    
                    result = await response.json()
                    
                    # 处理 'not_ready' 类型的响应
                    if result.get("type") == "not_ready":
                        error_msg = result.get("error", "Task not ready")
                        logger.info(f"🎵 任务未准备好: {error_msg}")
                        raise SunoTaskNotReadyException(error_msg)
                    
                    # 处理正常的任务状态响应 - 支持多个 clips
                    clips_data = result.get("data", [])
                    if not clips_data:
                        logger.info(f"🎵 任务数据为空，等待中...")
                        raise SunoTaskNotReadyException("Task data is empty, waiting...")
                    
                    # 检查所有 clips 的状态
                    all_states = [clip.get("state", "") for clip in clips_data]
                    logger.info(f"🎵 任务状态: {all_states} (任务ID: {task_id}, {len(clips_data)} clips)")
                    
                    # 如果有任何 clip 失败，整个任务失败
                    failed_clips = [clip for clip in clips_data if clip.get("state") in ["failed", "error"]]
                    if failed_clips:
                        error_msg = failed_clips[0].get('message', 'Unknown error')
                        logger.error(f"🎵 音乐生成失败: {error_msg}")
                        raise SunoFinalException(f"音乐生成失败: {error_msg}")
                    
                    # 如果有任何 clip 还在处理中，继续等待
                    pending_clips = [clip for clip in clips_data if clip.get("state") in ["pending", "running", ""]]
                    if pending_clips:
                        pending_states = [clip.get("state", "unknown") for clip in pending_clips]
                        logger.info(f"🎵 还有 {len(pending_clips)} 个 clips 在处理中: {pending_states}")
                        if "running" in pending_states:
                            raise SunoTaskRunningException("Some clips are still running")
                        else:
                            raise SunoTaskPendingException("Some clips are still pending")
                    
                    # 所有 clips 都成功了
                    succeeded_clips = [clip for clip in clips_data if clip.get("state") == "succeeded" and clip.get("audio_url")]
                    if len(succeeded_clips) == len(clips_data) and succeeded_clips:
                        logger.info(f"🎵 音乐生成成功: {len(succeeded_clips)} 个 clips 全部完成")
                        
                        # 处理所有 clips 的数据并保存到本地
                        processed_clips = []
                        total_duration = 0
                        local_audio_urls = []
                        
                        for i, clip in enumerate(succeeded_clips):
                            if clip.get("duration") is None:
                                logger.warning(
                                    "🎵 clip %s duration 为 null（音频已就绪），先记 0",
                                    clip.get("clip_id"),
                                )
                            duration = _clip_duration_seconds(clip.get("duration"))
                            
                            # 下载并保存音频到S3（带重试机制）
                            clip_id = clip.get("clip_id")
                            original_audio_url = clip["audio_url"]
                            try:
                                generation_id = f"suno_{task_id}_clip_{i}_{clip_id}"
                                local_audio_url = await s3_utils.download_and_upload_audio_to_s3(
                                    original_audio_url, generation_id=generation_id
                                )
                                local_audio_urls.append(local_audio_url)
                                logger.info(f"🎵 保存 clip {i+1} 音频到S3成功: {local_audio_url}")
                            except Exception as e:
                                logger.error(f"🎵 保存 clip {i+1} 音频到S3失败: {e}")
                                # 如果保存失败，使用原始URL
                                local_audio_url = original_audio_url
                                local_audio_urls.append(local_audio_url)
                            
                            processed_clips.append({
                                "clip_id": clip_id,
                                # Keep our storage URL. Internal analyze/Gemini copy
                                # from disk (local) or S3 (dest/prod). Vendor APIs
                                # go through resolve_outbound_media_url at call time.
                                "audio_url": local_audio_url,
                                "video_url": clip.get("video_url"),
                                "title": clip.get("title"),
                                "tags": clip.get("tags"),
                                "lyrics": clip.get("lyrics"),
                                "duration": duration,
                                "image_url": clip.get("image_url"),
                                "created_at": clip.get("created_at"),
                                "mv": clip.get("mv")
                            })
                            total_duration += duration
                        
                        # 有歌词 / auto_lyrics 且带 target_duration 时：按 |duration - target| 升序选 best clip，
                        # 把偏差最小的顶到 [0]，让上游 generate_single_suno_music 默认取的 clips[0] 即最佳版本。
                        # 其余 clip 仍保留在列表内（落 additional_data），便于前端展示备选并切换。
                        has_lyrics = generation_params.get("has_lyrics") if generation_params else False
                        auto_lyrics = generation_params.get("auto_lyrics") if generation_params else False
                        target_duration = generation_params.get("target_duration") if generation_params else None
                        need_duration_selection = (has_lyrics or auto_lyrics) and target_duration

                        message = None
                        if need_duration_selection and processed_clips:
                            mode_label = "有歌词" if has_lyrics else "auto_lyrics"
                            sorted_clips = sorted(
                                processed_clips,
                                key=lambda c: abs((c.get("duration") or 0) - target_duration),
                            )
                            best = sorted_clips[0]
                            best_err = abs((best.get("duration") or 0) - target_duration)
                            # 把 best 顶到 [0]，其余维持原相对顺序
                            processed_clips = [best] + [c for c in processed_clips if c is not best]
                            parts = []
                            for clip in processed_clips:
                                err = abs((clip.get("duration") or 0) - target_duration)
                                parts.append(f"{clip['duration']}秒(偏差{err}秒)")
                                logger.info(
                                    "🎵 Clip %s: 时长 %ss, 与目标偏差 %ss",
                                    (clip.get("clip_id") or "")[:8],
                                    clip["duration"],
                                    err,
                                )
                            message = (
                                f"✅ 音乐生成成功（目标约{target_duration}秒）。"
                                f"共{len(processed_clips)}条：{', '.join(parts)}。"
                                f"已自动选择偏差最小的版本（{best['duration']}秒，偏差{best_err}秒）作为主结果。"
                            )
                            logger.info(
                                "🎵 %s模式：目标时长约 %s秒，已按偏差升序选 best clip=%ss(偏差%ss)。",
                                mode_label, target_duration, best["duration"], best_err,
                            )
                            logger.info(message)
                        
                        return MusicGenerationResult.success_result_with_clips(
                            clips=processed_clips,
                            generated_prompt=original_prompt,
                            provider=MusicProvider.SUNO,
                            task_id=task_id,
                            message=message,
                            generation_params=generation_params
                        )
                    else:
                        # 状态异常，继续等待
                        logger.warning(f"🎵 clips 状态异常，继续等待...")
                        raise SunoTaskNotReadyException("Clips state is abnormal, waiting...")
                        
        except SunoRetryException:
            # 重新抛出重试异常
            raise
        except aiohttp.ClientError as e:
            logger.error(f"🎵 网络请求错误: {e}")
            raise SunoServerErrorException(f"Network error: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"🎵 JSON 解析错误: {e}")
            raise SunoServerErrorException(f"JSON decode error: {e}")
        except Exception as e:
            logger.error(f"🎵 未预期的错误: {e}")
            raise SunoFinalException(f"Unexpected error: {e}")
    


# 全局服务实例
_suno_service = None

def get_suno_service() -> SunoService:
    """获取 SunoAPI 服务实例"""
    global _suno_service
    if _suno_service is None:
        _suno_service = SunoService()
    return _suno_service

