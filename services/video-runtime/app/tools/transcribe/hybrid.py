"""
Hybrid 音频转录：分段与文本全权交给 Gemini，Suno 数据作为 prompt 上下文与审计字段。

执行流程（串行）：
  1. Suno aligned-lyrics 拿到词级时间戳（每个字/词的 start/end）
  2. 把 Suno 词按换气间隙拼回歌词文本 + 词级 MM:SS.mmm 锚点，作为 `generated_lyrics` 和
     `suno_alignment_context` 注入 Gemini prompt → Gemini 看着真实歌词做分段、文本、
     emotion、tempo、sections、vocal_gender 标注
  3. 把 Gemini 输出原样作为最终结果，仅做 gemini.py 共用的 4 步段后处理
     postprocess_transcription（fill_gaps → split_section → merge → 删无 segment 重叠的 section）

设计契约（"以 Gemini 为唯一权威输出，Suno 仅作上下文"）：
  - 分段 + 文本 + 时间戳：全部由 Gemini 决定，不做任何"用 Suno 数据回头修正 Gemini"的后处理
  - Suno 数据：仅 (a) 通过 prompt 给 Gemini 提供时间/语义上下文；(b) 写入
    `additional_data` 供下游查询使用（`words` / `suno_section_hints` / `suno_vocal_gender_hint`
    / `suno_vocal_mask` / `suno_song_header` / `suno_raw_alignment`）

历史包袱（已删）：先前有 Step 2 端点重算 / Step 4 vocal_presence 校正 / Step 4.5 微段合并
  / Step 5 segment.text 锚定 等"双源比对修正"逻辑。实测发现 Suno 自己也有偶发错误
  （phantom 重复同一句、边界字漂移），机械覆盖会把 Gemini 正确的歌词改坏。结论：双源
  修正不可行，完全交给 Gemini（context-augmented）输出，Suno 数据保留作为旁证 + 诊断 log。

优雅降级（生产必需）：Suno 任何环节失败（upload 500 / aligned-lyrics 超时 / 网络 / 版权风控）
  → SunoReshaped 为空 → Gemini 不带 generated_lyrics 跑 → 等同纯 Gemini 输出。

调用入口：`transcribe_audio_with_hybrid()`，签名是 `transcribe_audio_with_gemini` 的超集（多个 `clip_id`），
可在 `music_generation_service` 里按 `DefaultValues.TRANSCRIPTION_METHOD` 灰度切换。

设计依据（已在 scripts/transcribe_hybrid_prototype_smoke.py 上验证 7 首歌：
中文/英文 + 人声/纯音乐 + Suno generated/upload 路径全跑通）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import aiohttp

from ...models.video_state import AudioTranscription, AudioSegment, AudioWord, UserOption
from ...models.tool_enums import AudioSegmentGranularity, DefaultValues
from ...utils.time_format import format_sec_to_mmss
from .gemini import (
    transcribe_audio_with_gemini,
    postprocess_transcription,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Suno API 调用层（aimusicapi.ai / sunoapi.com sonic namespace）
# ============================================================================

SUNO_BASE_URL = "https://api.sunoapi.com"
SUNO_NAMESPACE = "sonic"  # 新 Sonic API
SUNO_ALIGNED_LYRICS_MAX_WAIT_SEC = 180  # aligned-lyrics 等待上限
SUNO_ALIGNED_LYRICS_POLL_SEC = 6        # 轮询间隔
SUNO_UPLOAD_TIMEOUT_SEC = 60            # 单次 upload HTTP 超时
SUNO_UPLOAD_MAX_ATTEMPTS = 3            # upload 总尝试次数（首次 + 2 次重试），仅对 5xx / 网络错重试
SUNO_UPLOAD_RETRY_BASE_SEC = 2.0        # 指数退避基准：2s, 4s


def _suno_headers() -> dict:
    api_key = os.getenv("SUNO_API_KEY")
    if not api_key:
        raise RuntimeError("SUNO_API_KEY 未配置")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _suno_upload_for_clip_id(
    session: aiohttp.ClientSession,
    audio_url: str,
    *,
    base_url: str = SUNO_BASE_URL,
    namespace: str = SUNO_NAMESPACE,
    max_attempts: int = SUNO_UPLOAD_MAX_ATTEMPTS,
    retry_base_sec: float = SUNO_UPLOAD_RETRY_BASE_SEC,
) -> str:
    """POST /api/v1/sonic/upload，返回 clip_id。
    
    对 5xx / 网络异常做指数退避重试（默认 3 次尝试，2s/4s）；4xx 立即抛错不重试。
    所有重试耗尽后抛 RuntimeError，由上层 try/except 转化为优雅降级。
    """
    endpoint = f"{base_url.rstrip('/')}/api/v1/{namespace}/upload"
    # 本地存储时把 localhost 音频换成公网 URL，否则 Suno 拉不到（对象存储会自动透传）
    from app.utils.media_egress import resolve_outbound_media_url
    audio_url = await resolve_outbound_media_url(audio_url)
    last_err: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with session.post(endpoint, json={"url": audio_url}, headers=_suno_headers()) as resp:
                text = await resp.text()
                status = resp.status
            if status >= 500:
                last_err = f"HTTP {status}: {text[:300]}"
                if attempt < max_attempts:
                    delay = retry_base_sec * (2 ** (attempt - 1))
                    logger.warning(
                        "🔁 [hybrid/suno] upload %s 第 %d/%d 次失败（5xx），%.1fs 后重试: %s",
                        endpoint, attempt, max_attempts, delay, last_err,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError(f"Suno upload HTTP {status}: {text[:300]}")
            if status >= 400:
                raise RuntimeError(f"Suno upload HTTP {status}: {text[:300]}")
            data = json.loads(text) if text.strip() else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as net_err:
            last_err = repr(net_err)[:300]
            if attempt < max_attempts:
                delay = retry_base_sec * (2 ** (attempt - 1))
                logger.warning(
                    "🔁 [hybrid/suno] upload 网络错（第 %d/%d 次），%.1fs 后重试: %s",
                    attempt, max_attempts, delay, last_err,
                )
                await asyncio.sleep(delay)
                continue
            raise RuntimeError(f"Suno upload 网络错（已重试 {max_attempts} 次）: {last_err}")
        clip_id = data.get("clip_id") or (data.get("data") or {}).get("clip_id")
        if not clip_id:
            raise RuntimeError(f"Suno upload 未拿到 clip_id: {str(data)[:200]}")
        return clip_id
    raise RuntimeError(f"Suno upload 重试耗尽: {last_err}")


async def _suno_fetch_aligned_lyrics(
    session: aiohttp.ClientSession,
    clip_id: str,
    *,
    base_url: str = SUNO_BASE_URL,
    namespace: str = SUNO_NAMESPACE,
    max_wait_sec: int = SUNO_ALIGNED_LYRICS_MAX_WAIT_SEC,
    poll_interval_sec: int = SUNO_ALIGNED_LYRICS_POLL_SEC,
) -> dict:
    """POST /api/v1/sonic/aligned-lyrics，轮询到 alignment 拿到为止。

    返回完整 payload；上层用 reshape_suno_alignment(payload) 提取干净 words/mask/hints。
    超时或 401/403/404 抛错；其它 5xx/暂未就绪会继续 poll 到 max_wait_sec。
    """
    endpoint = f"{base_url.rstrip('/')}/api/v1/{namespace}/aligned-lyrics"
    started = time.monotonic()
    last_err: Optional[str] = None
    while True:
        async with session.post(endpoint, json={"clip_id": clip_id}, headers=_suno_headers()) as resp:
            text = await resp.text()
            try:
                parsed = json.loads(text) if text.strip() else None
            except json.JSONDecodeError:
                parsed = None
            if resp.status == 200 and parsed is not None:
                code = parsed.get("code")
                alignment = (parsed.get("data") or {}).get("alignment")
                if code in (200, None) and alignment is not None:
                    return parsed
                last_err = parsed.get("message") or text[:200]
            elif resp.status in (401, 403, 404):
                raise RuntimeError(f"Suno aligned-lyrics HTTP {resp.status}: {text[:300]}")
            else:
                last_err = f"HTTP {resp.status}: {text[:200]}"

        if time.monotonic() - started >= max_wait_sec:
            raise TimeoutError(f"Suno aligned-lyrics 超时；最后错误：{last_err}")
        await asyncio.sleep(poll_interval_sec)


# ============================================================================
# Suno raw alignment 清洗（reshape_suno_alignment）
# ============================================================================

# Suno docs 列出的 8 个 Structure Tags（顺便包含 Refrain）
_STRUCTURE_TAG_NAMES = (
    "Verse", "Chorus", "Pre-Chorus", "Bridge", "Outro", "Intro", "Hook", "Break", "Refrain",
)
_SECTION_TAG_PATTERN = re.compile(
    r"\[(" + "|".join(re.escape(s) for s in _STRUCTURE_TAG_NAMES) + r")(?:\s*\d+)?\]",
    re.IGNORECASE,
)
# 任意单 token 内成对方括号（[piano stabs] / [male tenor vocals] / [Verse 1] 等）
_ANY_BRACKET_PATTERN = re.compile(r"\[[^\]]*\]")
# vocal_gender 提示：[male vocals] / [female tenor vocals] 等
_GENDER_HINT_PATTERN = re.compile(r"\[(male|female)\b[^\]]*vocals?\]", re.IGNORECASE)


@dataclass
class SunoReshaped:
    """Suno raw alignment 清洗后的产物。空对象（默认值）表示 Suno 未提供任何可用数据，
    此时 merge_to_hybrid 会自然降级到纯 Gemini 输出。"""
    clean_words: List[AudioWord] = field(default_factory=list)
    section_hints: List[dict] = field(default_factory=list)        # [{type:"Verse 1", start: 0.239}, ...]
    vocal_gender_hint: Optional[str] = None                        # 'f' / 'm' / None
    vocal_mask: List[Tuple[float, float]] = field(default_factory=list)  # 合并后的"有人声"区间
    song_header: str = ""                                          # 首 token 抽出的元数据全文
    raw: dict = field(default_factory=dict)                        # 原始返回，留 additional_data 供回归


def reshape_suno_alignment(raw_payload: dict, *, mask_gap_threshold: float = 0.3) -> SunoReshaped:
    """5 阶段清洗 Suno raw alignment（详见 prototype + _hybrid_gemini_vs_hybrid_compare.md 的回归证据）。

    Phase 1 - 在 **原始** raw_word 上扫所有 [Section]/[gender vocals] 标签
              （这一步必须在拆 header / 剔标签之前做，否则首 token 里的 [Intro]/[Verse 1]/[male tenor vocals] 会丢）
    Phase 2 - 单 token 处理：
              · 首 token：取最后一个 \\n 之前的部分当 song_header
              · 用 _ANY_BRACKET_PATTERN 剔除单 token 内**成对**的 [xxx]
              · trim \\n + 多余空白
    Phase 3 - **跨 token 不成对方括号状态机**：Suno 会把 `[piano stabs]` 拆成 ` [piano`/` stabs`/`] 冰`
              这种情况 Phase 2 的 regex 匹配不到，靠括号深度状态机处理：
              · 遇到含 `[` 不含 `]` → 进入"吞噬模式"，本 token 留 `[` 之前的部分
              · 吞噬模式中：含 `]` → 退出，留 `]` 之后；否则整 drop
    Phase 4 - 重编号 id 成 clean_words。
    Phase 5 - 用 mask_gap_threshold 合并相邻 word [start, end] 成 vocal_mask。
    """
    out = SunoReshaped(raw=raw_payload)
    alignment = (raw_payload.get("data") or {}).get("alignment") or []
    if not alignment:
        return out

    # ===== Phase 1: 在原 raw_word 上扫 section/gender 标签（拆 header 之前） =====
    for item in alignment:
        raw_w = str(item.get("word") or "")
        start = float(item.get("start_s") or 0.0)
        if not out.vocal_gender_hint:
            gm = _GENDER_HINT_PATTERN.search(raw_w)
            if gm:
                out.vocal_gender_hint = gm.group(1).lower()[0]  # 'm' / 'f'
        for sm in _SECTION_TAG_PATTERN.finditer(raw_w):
            tag = sm.group(0).strip("[]").strip()
            # 去重：同 token 内同名标签或极近时间的重复标签合并
            if not any(h["type"].lower() == tag.lower() and abs(h["start"] - start) < 0.5
                       for h in out.section_hints):
                out.section_hints.append({"type": tag, "start": start})

    # ===== Phase 2: 单 token 处理 - 拆 header / 剔除成对 [xxx] / trim =====
    phase2: List[Tuple[str, float, float]] = []
    for i, item in enumerate(alignment):
        raw_word = str(item.get("word") or "")
        start = float(item.get("start_s") or 0.0)
        end = float(item.get("end_s") or start)

        word = raw_word
        if i == 0 and "\n" in word:
            head_part, _, tail_part = word.rpartition("\n")
            if head_part.strip():
                out.song_header = head_part
            word = tail_part

        word = _ANY_BRACKET_PATTERN.sub("", word)
        word = word.replace("\n", " ").strip()
        word = re.sub(r"\s+", " ", word)
        phase2.append((word, start, end))

    # ===== Phase 3: 跨 token 不成对方括号状态机 =====
    phase3: List[Tuple[str, float, float]] = []
    in_bracket = False
    for w, s, e in phase2:
        has_open = "[" in w
        has_close = "]" in w
        if in_bracket:
            if has_close:
                tail = w.rpartition("]")[2].strip()
                in_bracket = False
                if "[" in tail:
                    head = tail.partition("[")[0].strip()
                    in_bracket = True
                    if head:
                        phase3.append((head, s, e))
                else:
                    if tail:
                        phase3.append((tail, s, e))
            else:
                continue
        else:
            if has_open and not has_close:
                head = w.partition("[")[0].strip()
                in_bracket = True
                if head:
                    phase3.append((head, s, e))
            elif has_close and not has_open:
                tail = w.rpartition("]")[2].strip()
                if tail:
                    phase3.append((tail, s, e))
            else:
                if w.strip():
                    phase3.append((w, s, e))

    # ===== Phase 4: 重编号成 clean_words =====
    for idx, (w, s, e) in enumerate(phase3):
        out.clean_words.append(AudioWord(id=idx, word=w, start=s, end=e))

    # ===== Phase 5: vocal_mask =====
    if out.clean_words:
        runs: List[Tuple[float, float]] = []
        cur_s = out.clean_words[0].start
        cur_e = out.clean_words[0].end
        for w in out.clean_words[1:]:
            if w.start - cur_e <= mask_gap_threshold:
                cur_e = max(cur_e, w.end)
            else:
                runs.append((cur_s, cur_e))
                cur_s, cur_e = w.start, w.end
        runs.append((cur_s, cur_e))
        out.vocal_mask = runs

    return out


# ============================================================================
# Gemini AudioTranscription + SunoReshaped → 融合（merge_to_hybrid）
# ============================================================================

def _merge_to_hybrid(
    gemini_tr: AudioTranscription,
    suno: SunoReshaped,
) -> AudioTranscription:
    """以 Gemini AudioTranscription 为唯一权威输出，Suno 数据仅作为：
      (a) prompt context 喂给 Gemini（在调用 Gemini 前完成，见 transcribe_audio_with_hybrid）
      (b) additional_data 审计字段（words/section_hints/vocal_mask/gender_hint）下游消费

    历史上这里做过"Step 2 端点重算 / Step 4 vocal_presence 校正 / Step 4.5 微段合并 /
    Step 5 segment.text 锚定覆盖" 这些"用 Suno 数据修正 Gemini 输出"的后处理。但实测
    发现 Suno API 自己也有偶发错误（phantom 重复同一句、边界字漂移到下段），机械覆盖
    会把 Gemini 正确的歌词改坏。结论是双源比对修正不可行 —— **完全以 Gemini 为准**，
    Suno 仅在 prompt 阶段作为强 hint 输入。

    本函数现在只做两件事：
      Step 1: additional_data 注入（Suno 元数据 + transcription_method 标签）
      Step 3: 复用 gemini.py postprocess_transcription_segments（与纯 Gemini 路径一致）

    Suno 没数据时（空 SunoReshaped），additional_data 中 Suno 字段为空，其余不变。
    """
    hybrid = gemini_tr.model_copy(deep=True)

    # ============ Step 1: additional_data 注入（Suno 元数据作为审计与下游消费字段） ============
    extra = dict(hybrid.additional_data or {})
    extra["transcription_method"] = "hybrid"
    extra["words"] = [w.model_dump() for w in suno.clean_words]
    extra["suno_raw_alignment"] = suno.raw                  # 留底
    extra["suno_section_hints"] = suno.section_hints
    extra["suno_vocal_gender_hint"] = suno.vocal_gender_hint
    extra["suno_song_header"] = suno.song_header
    extra["suno_vocal_mask"] = [list(r) for r in suno.vocal_mask]
    hybrid.additional_data = extra

    # ============ Step 3: 复用 gemini.py 4 步后处理（与 Gemini 路径完全对齐） ============
    actual_duration = float(extra.get("actual_duration_seconds") or hybrid.duration or 0.0)
    full_sections: List[dict] = list(extra.get("sections") or [])

    n0 = len(hybrid.segments)
    hybrid.segments, full_sections = postprocess_transcription(
        hybrid.segments,
        full_sections or None,
        total_duration=actual_duration,
        fill_gaps_enabled=actual_duration > 0,
    )
    if full_sections:
        extra["sections"] = full_sections
        hybrid.additional_data = extra
    n4 = len(hybrid.segments)

    # ============ 诊断 log：Gemini 段内 text 与 Suno 段内字符集的差异（不修改，仅观测） ============
    # Suno 数据虽然不再用于覆盖 Gemini text，但保留作为"Gemini 是否漏字"的旁证。
    # 当 Gemini text 缺失 ≥ 2 个 Suno 段内 CJK 字符时打 warning，方便后续 prompt 调优。
    if suno.clean_words:
        for seg in hybrid.segments:
            if not seg.vocal_presence or not (seg.text or "").strip():
                continue
            seg_words = [w for w in suno.clean_words if seg.start <= w.start < seg.end]
            if not seg_words:
                continue
            suno_cjk = {c for w in seg_words for c in (w.word or "")
                        if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf"}
            gemini_cjk = {c for c in (seg.text or "")
                          if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf"}
            missing_in_gemini = suno_cjk - gemini_cjk
            if len(missing_in_gemini) >= 2:
                logger.warning(
                    "⚠️  [hybrid/diag] seg %s [%.3f,%.3f] Gemini 可能漏字 %s（不修改，仅观测）: gemini=%r",
                    getattr(seg, "id", "?"), seg.start, seg.end,
                    sorted(missing_in_gemini), (seg.text or "")[:80],
                )

    if n0 != n4:
        logger.info(
            "🔧 [hybrid] 后处理 segments 数量变化: in=%d → postprocess=%d",
            n0, n4,
        )

    return hybrid


# ============================================================================
# 主入口：transcribe_audio_with_hybrid
# ============================================================================

async def _run_suno_path(
    audio_url: str,
    clip_id: Optional[str],
    *,
    mask_gap_threshold: float = 0.3,
) -> SunoReshaped:
    """Suno 路径：根据是否有现成 clip_id 决定是否走 upload。
    
    任何环节异常（upload 500 / aligned-lyrics 超时 / 网络）都返回空 SunoReshaped，
    让 _merge_to_hybrid 自然降级到纯 Gemini —— 这是 user upload 场景下流量歌曲被 Suno 风控时
    唯一正确的产品形态。
    """
    timeout = aiohttp.ClientTimeout(total=300, connect=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            cid = clip_id
            if not cid:
                logger.info("⬆️  [hybrid/suno] upload %s", audio_url[:120])
                t0 = time.monotonic()
                try:
                    cid = await _suno_upload_for_clip_id(session, audio_url)
                    logger.info("✅ [hybrid/suno] upload OK clip_id=%s (%.2fs)",
                                cid, time.monotonic() - t0)
                except Exception as e:
                    logger.warning("⚠️  [hybrid/suno] upload 失败，降级到纯 Gemini: %s",
                                   repr(e)[:200])
                    return SunoReshaped()
            else:
                logger.info("ℹ️  [hybrid/suno] 复用 clip_id=%s", cid)
            logger.info("🎤 [hybrid/suno] aligned-lyrics...")
            t0 = time.monotonic()
            payload = await _suno_fetch_aligned_lyrics(session, cid)
            n_tokens = len((payload.get("data") or {}).get("alignment") or [])
            logger.info("✅ [hybrid/suno] aligned-lyrics %d raw token (%.2fs)",
                        n_tokens, time.monotonic() - t0)
        reshaped = reshape_suno_alignment(payload, mask_gap_threshold=mask_gap_threshold)
        logger.info("✅ [hybrid/suno] reshape: %d raw → %d clean words; section_hints=%d; gender=%s",
                    n_tokens, len(reshaped.clean_words),
                    len(reshaped.section_hints), reshaped.vocal_gender_hint or "-")
        return reshaped
    except Exception as e:
        logger.warning("⚠️  [hybrid/suno] aligned-lyrics/reshape 失败，降级到纯 Gemini: %s",
                       repr(e)[:200])
        return SunoReshaped()


# ============================================================================
# Mureka recognize-song（WaveSpeed namespace）—— 与 Suno aligned-lyrics 平级的对齐 provider
# ============================================================================

WAVESPEED_BASE_URL = "https://api.wavespeed.ai/api/v3"
MUREKA_RECOGNIZE_MODEL = "mureka-ai/mureka-v7.6/recognize-song"
MUREKA_MAX_WAIT_SEC = 180          # recognize-song 结果等待上限
MUREKA_POLL_SEC = 2                # 轮询间隔
MUREKA_SUBMIT_TIMEOUT_SEC = 60     # 单次 submit HTTP 超时


def _wavespeed_headers() -> dict:
    api_key = os.getenv("WAVESPEED_API_KEY")
    if not api_key:
        raise RuntimeError("WAVESPEED_API_KEY 未配置")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _mureka_submit(
    session: aiohttp.ClientSession,
    audio_url: str,
    *,
    base_url: str = WAVESPEED_BASE_URL,
    model: str = MUREKA_RECOGNIZE_MODEL,
) -> str:
    """POST /{model}，提交识别任务，返回 WaveSpeed task id。4xx/5xx 立即抛错由上层降级。"""
    endpoint = f"{base_url.rstrip('/')}/{model}"
    # 本地存储时把 localhost 音频换成公网 URL，否则 Mureka(WaveSpeed) 拉不到（对象存储会自动透传）
    from app.utils.media_egress import resolve_outbound_media_url
    audio_url = await resolve_outbound_media_url(audio_url)
    async with session.post(endpoint, json={"audio": audio_url}, headers=_wavespeed_headers()) as resp:
        text = await resp.text()
        status = resp.status
    if status >= 400:
        raise RuntimeError(f"Mureka recognize-song 提交 HTTP {status}: {text[:300]}")
    data = json.loads(text) if text.strip() else {}
    task_id = (data.get("data") or {}).get("id") or data.get("id")
    if not task_id:
        raise RuntimeError(f"Mureka recognize-song 未拿到 task id: {str(data)[:200]}")
    return task_id


async def _mureka_fetch_output(session: aiohttp.ClientSession, output) -> dict:
    """outputs[0] 既可能是 JSON 文件 URL，也可能是内联 JSON（dict / str），统一解析为 dict。"""
    if isinstance(output, dict):
        return output
    s = str(output).strip()
    if s.startswith("{"):
        return json.loads(s)
    async with session.get(output) as resp:
        resp.raise_for_status()
        text = await resp.text()
    return json.loads(text)


async def _mureka_poll_result(
    session: aiohttp.ClientSession,
    task_id: str,
    *,
    base_url: str = WAVESPEED_BASE_URL,
    max_wait_sec: int = MUREKA_MAX_WAIT_SEC,
    poll_interval_sec: int = MUREKA_POLL_SEC,
) -> dict:
    """GET /predictions/{task_id}/result，轮询到 completed 拿 outputs[0] 的识别 JSON。

    completed → 解析 outputs[0] 返回；failed/401/403/404 抛错；其余继续 poll 到 max_wait_sec。
    """
    endpoint = f"{base_url.rstrip('/')}/predictions/{task_id}/result"
    started = time.monotonic()
    last_err: Optional[str] = None
    while True:
        async with session.get(endpoint, headers=_wavespeed_headers()) as resp:
            text = await resp.text()
            try:
                parsed = json.loads(text) if text.strip() else None
            except json.JSONDecodeError:
                parsed = None
            if resp.status == 200 and parsed is not None:
                data = parsed.get("data") or {}
                status = data.get("status")
                if status == "completed":
                    outputs = data.get("outputs") or []
                    if not outputs:
                        raise RuntimeError("Mureka recognize-song 完成但无 outputs")
                    return await _mureka_fetch_output(session, outputs[0])
                if status == "failed":
                    raise RuntimeError(f"Mureka recognize-song 失败: {data.get('error')}")
                last_err = f"status={status}"
            elif resp.status in (401, 403, 404):
                raise RuntimeError(f"Mureka recognize-song HTTP {resp.status}: {text[:300]}")
            else:
                last_err = f"HTTP {resp.status}: {text[:200]}"

        if time.monotonic() - started >= max_wait_sec:
            raise TimeoutError(f"Mureka recognize-song 超时；最后错误：{last_err}")
        await asyncio.sleep(poll_interval_sec)


def reshape_mureka_recognition(raw_payload: dict, *, mask_gap_threshold: float = 0.3) -> SunoReshaped:
    """把 Mureka recognize-song 输出清洗成与 Suno 同构的 SunoReshaped，下游 prompt 拼装/merge 零改动。

    Mureka 输出（毫秒）：lyrics_sections[].lines[].words[] = {start, end, text}
      - 展平所有词，ms→秒，trim 文本（Mureka 偶有 "地 "/"￥ " 这类尾随空格/噪声 token）
      - vocal_mask 与 Suno 同算法（相邻词 gap ≤ mask_gap_threshold 合并）
      - Mureka 不提供曲式标签 / 人声性别 → section_hints / vocal_gender_hint 留空（不影响主流程）
    空 payload / 无词时返回空 SunoReshaped，让 hybrid 自然降级到纯 Gemini。
    """
    out = SunoReshaped(raw=raw_payload)
    sections = raw_payload.get("lyrics_sections") or []
    idx = 0
    for sec in sections:
        for line in (sec.get("lines") or []):
            for w in (line.get("words") or []):
                text = str(w.get("text") or "").strip()
                if not text:
                    continue
                start = float(w.get("start") or 0) / 1000.0
                raw_end = w.get("end")
                end = float(raw_end) / 1000.0 if raw_end is not None else start
                if end < start:
                    end = start
                out.clean_words.append(AudioWord(id=idx, word=text, start=start, end=end))
                idx += 1

    if out.clean_words:
        runs: List[Tuple[float, float]] = []
        cur_s = out.clean_words[0].start
        cur_e = out.clean_words[0].end
        for w in out.clean_words[1:]:
            if w.start - cur_e <= mask_gap_threshold:
                cur_e = max(cur_e, w.end)
            else:
                runs.append((cur_s, cur_e))
                cur_s, cur_e = w.start, w.end
        runs.append((cur_s, cur_e))
        out.vocal_mask = runs

    return out


async def _run_mureka_path(
    audio_url: str,
    *,
    mask_gap_threshold: float = 0.3,
) -> SunoReshaped:
    """Mureka 路径：submit → poll → reshape。无 upload 步骤（直接传 audio_url）。

    任何环节异常（提交 4xx/5xx / 轮询超时 / 网络）都返回空 SunoReshaped，
    让 transcribe_audio_with_hybrid 自然降级到纯 Gemini（与 Suno 路径一致的优雅降级契约）。
    """
    timeout = aiohttp.ClientTimeout(total=300, connect=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            logger.info("🎤 [hybrid/mureka] recognize-song submit %s", audio_url[:120])
            t0 = time.monotonic()
            task_id = await _mureka_submit(session, audio_url)
            logger.info("✅ [hybrid/mureka] submit OK task_id=%s (%.2fs)",
                        task_id, time.monotonic() - t0)
            t0 = time.monotonic()
            payload = await _mureka_poll_result(session, task_id)
            logger.info("✅ [hybrid/mureka] recognize-song 完成 (%.2fs)", time.monotonic() - t0)
        reshaped = reshape_mureka_recognition(payload, mask_gap_threshold=mask_gap_threshold)
        logger.info("✅ [hybrid/mureka] reshape: %d clean words", len(reshaped.clean_words))
        return reshaped
    except Exception as e:
        logger.warning("⚠️  [hybrid/mureka] recognize-song 失败，降级到纯 Gemini: %s",
                       repr(e)[:200])
        return SunoReshaped()


def _word_has_ascii_letter(w: str) -> bool:
    """判断一个词是否包含英文字母 ASCII letter（用于决定相邻拼接时是否插入空格）。"""
    return any(c.isascii() and c.isalpha() for c in w)


def _seconds_to_mmss(sec: float) -> str:
    """转 MM:SS.mmm，跟 prompt 输出格式契约一致，避免 Gemini 同时面对秒/MM:SS 两种格式。

    内部直接复用全局 `format_sec_to_mmss`，保持 hybrid / database_utils /
    main_character_design / smart_clip 各处展示统一。
    """
    return format_sec_to_mmss(sec)


_SUNO_LONG_SILENCE_SEC = 0.6  # 词间 ≥ 0.6s 视为换气/停顿，在 word 列表中显式标注


def _build_suno_alignment_context(
    suno: SunoReshaped,
    *,
    granularity: Optional[AudioSegmentGranularity] = None,
) -> Optional[str]:
    """Assemble Suno alignment **facts only** for Gemini (no craft guidance essays).

    Data blocks:
      1. section_hints (type + start MM:SS.mmm)
      2. vocal_gender_hint (f|m)
      3. word list with timestamps; gaps ≥ 0.6s as `(silence X.XXXs)` lines

    Craft (how to use words / silence for phrase|sentence|beat) lives in
    ``audio-transcription-director`` skill — Suno word-alignment mode.

    Returns None when Suno has no clean_words (hybrid falls back to lyrics-only).
    ``granularity`` kept for call-site compat; unused (skill reads facts.granularity).
    """
    _ = granularity
    if not suno.clean_words:
        return None

    parts: List[str] = ["# suno_alignment_facts", ""]

    if suno.section_hints:
        parts.append("## section_hints")
        for h in suno.section_hints:
            parts.append(f"- type={h['type']} start={_seconds_to_mmss(float(h['start']))}")
        parts.append("")

    if suno.vocal_gender_hint in ("f", "m"):
        parts.append(f"## vocal_gender_hint")
        parts.append(f"- {suno.vocal_gender_hint}")
        parts.append("")

    parts.append("## words")
    parts.append(f"# silence_gap_threshold_sec={_SUNO_LONG_SILENCE_SEC}")
    words = suno.clean_words
    prev_end = words[0].start
    for w in words:
        gap = w.start - prev_end
        if gap >= _SUNO_LONG_SILENCE_SEC:
            parts.append(f"  (silence {gap:.3f}s)")
        start = _seconds_to_mmss(w.start)
        end = _seconds_to_mmss(w.end)
        parts.append(f"- [{start} - {end}] {w.word}")
        prev_end = w.end
    parts.append("")

    return "\n".join(parts)


def _assemble_lyrics_from_suno_words(
    words: List[AudioWord],
    *,
    line_break_gap_sec: float = 0.6,
) -> str:
    """把 Suno aligned-lyrics 拿到的干净词按时间顺序拼回歌词文本，
    供 Gemini 的 prompt `generated_lyrics` 字段使用。

    分行策略：相邻词之间 gap > line_break_gap_sec（默认 600ms）视为一句结束，换行。
    这是观察 Suno 实际输出后的经验值——中文/英文歌词的"换行"几乎都对应一个明显的换气停顿。

    词间空格策略（逐对判断，不再全局开关）：
      - 当前词或前一个词含 ASCII 字母（英文/音节）→ 中间插空格："Ha"+"跟" → "Ha 跟"
      - 两个词都是纯 CJK / 标点 → 不插空格："跟"+"上" → "跟上"

    这样中英混合歌（Suno 把汉字按字切，英文按音节切）能拼回最贴近正常歌词的形态，
    Gemini 看到 "Ha ah ah 跟上节拍" 而不是 "Ha ah ah 跟 上 节 拍"，分段语义更准。
    """
    if not words:
        return ""

    lines: List[List[str]] = [[]]
    prev_end = words[0].start
    for w in words:
        if w.start - prev_end > line_break_gap_sec and lines[-1]:
            lines.append([])
        lines[-1].append(w.word)
        prev_end = w.end

    def _join_line(tokens: List[str]) -> str:
        if not tokens:
            return ""
        out = tokens[0]
        for tok in tokens[1:]:
            need_space = _word_has_ascii_letter(tok) or _word_has_ascii_letter(out[-1] if out else "")
            out += (" " + tok) if need_space else tok
        return out

    return "\n".join(_join_line(line) for line in lines if line)


def _build_alignment_fallback_transcription(
    audio_url: str,
    filename: Optional[str],
    alignment: SunoReshaped,
    *,
    generated_lyrics: Optional[str],
    alignment_provider: str,
    granularity: AudioSegmentGranularity,
    split_gap_sec: float = 0.8,
    max_segment_sec: float = 10.0,
) -> Optional[AudioTranscription]:
    """Gemini 不可用时，用已有词级对齐结果构造最小可用转录。

    该降级仅保证主流程可继续，不尝试替代 Gemini 的曲式、情绪和视觉分析。
    分段优先使用明显停顿，并限制单段长度，确保 lipsync 下游不会收到超长片段。
    """
    words = sorted(alignment.clean_words, key=lambda item: (item.start, item.end))
    if not words:
        return None

    groups: List[List[AudioWord]] = []
    current: List[AudioWord] = []
    for word in words:
        if current:
            gap = float(word.start) - float(current[-1].end)
            current_duration = float(current[-1].end) - float(current[0].start)
            if gap >= split_gap_sec or current_duration >= max_segment_sec:
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)

    segments: List[AudioSegment] = []
    for group in groups:
        start = max(0.0, float(group[0].start))
        end = max(start, float(group[-1].end))
        text = _assemble_lyrics_from_suno_words(group, line_break_gap_sec=split_gap_sec).replace("\n", " ").strip()
        segments.append(
            AudioSegment(
                id=len(segments),
                start=start,
                end=end,
                duration=end - start,
                text=text,
                emotion=None,
                tempo=None,
                vocal_presence=bool(text),
                vocal_gender=alignment.vocal_gender_hint,
            )
        )

    duration = max(float(words[-1].end), max((segment.end for segment in segments), default=0.0))
    if duration <= 0 or not segments:
        return None

    assembled_text = (generated_lyrics or _assemble_lyrics_from_suno_words(words)).strip()
    ascii_letters = sum(1 for char in assembled_text if char.isascii() and char.isalpha())
    cjk_chars = sum(1 for char in assembled_text if "\u3400" <= char <= "\u9fff")
    language = "en" if ascii_letters >= cjk_chars else "zh"
    additional_data = {
        "transcription_method": "hybrid_alignment_fallback",
        "transcription_outcome": "fallback_alignment_only",
        "alignment_provider": alignment_provider,
        "audio_segment_granularity": granularity.value,
        "actual_duration_seconds": duration,
        "words": [word.model_dump() for word in words],
        "suno_raw_alignment": alignment.raw,
        "suno_section_hints": alignment.section_hints,
        "suno_vocal_gender_hint": alignment.vocal_gender_hint,
        "suno_song_header": alignment.song_header,
        "suno_vocal_mask": [list(item) for item in alignment.vocal_mask],
        "sections": [],
        "fallback_reason": "gemini_unavailable",
    }
    return AudioTranscription(
        task="transcribe",
        language=language,
        duration=duration,
        text=assembled_text,
        segments=segments,
        audio_url=audio_url,
        filename=filename,
        is_instrumental=False,
        additional_data=additional_data,
    )


async def transcribe_audio_with_hybrid(
    audio_url: str,
    *,
    clip_id: Optional[str] = None,
    user_option: Optional[UserOption] = None,
    fill_gaps: bool = True,
    user_input: Optional[str] = None,
    filename: Optional[str] = None,
    generated_lyrics: Optional[str] = None,
    granularity: Optional[AudioSegmentGranularity] = None,
    music_intent: Optional[str] = None,
    music_workflow_mode: Optional[str] = None,
) -> Optional[AudioTranscription]:
    """混合转录入口（与 `transcribe_audio_with_gemini` 完全兼容的超集签名）。

    执行模式：**Suno 先 → Gemini 用 Suno 歌词转录 → 合并**（串行）。
      1. 先跑 Suno aligned-lyrics 拿到词级时间戳 + section_hints + vocal_gender_hint
      2. 把 Suno 词按换气间隙拼回歌词文本（`_assemble_lyrics_from_suno_words`），
         作为 `generated_lyrics` 注入 Gemini 的 prompt —— Gemini 因此"看着真实歌词做转录"，
         段落语义/sections/vocal_gender 标注更贴歌词结构（实测 vocal_gender 翻倍 / segments +39%）
      3. 合并：按"段内 Suno 词集合"重算每段 start/end + vocal_presence，gender 缺失时兜底，
         走 gemini.py 4 步后处理保证全覆盖契约；**时间以 Suno 为准，分段以 Gemini 为准**

    Suno 失败时自动优雅降级：Suno upload/aligned-lyrics 失败 → SunoReshaped 为空 →
    Gemini 不带歌词跑（行为等同纯 Gemini），下游零感知。

    路由：
      - clip_id 提供（Suno generated 路径）：直接拉 aligned-lyrics，不走 upload，延迟 ≈ 87s
      - clip_id=None（user upload 路径）：先 Suno upload 拿 clip_id 再拉 aligned-lyrics，延迟 ≈ 127s

    返回 schema 与 `transcribe_audio_with_gemini` **完全一致**。下游消费 AudioTranscription
    的代码完全无需改动。
    """
    # 对齐 provider 由 DefaultValues.ALIGNMENT_PROVIDER 灰度切换（仅 hybrid 生效）：
    #   - "suno":   Suno upload + aligned-lyrics（历史默认）
    #   - "mureka": Mureka V7.6 recognize-song（WaveSpeed，无 upload；无曲式/性别 hint）
    # 两者都返回 SunoReshaped，下游 generated_lyrics + alignment_context 注入完全一致。
    alignment_provider = (DefaultValues.ALIGNMENT_PROVIDER or "suno").lower()
    logger.info(
        "🎬 [hybrid] ===== 开始 hybrid 转录（串行：%s→Gemini）===== | audio_url=%s | clip_id=%s | filename=%s",
        alignment_provider, audio_url[:120], clip_id or "(none)", filename or "-",
    )
    t0 = time.monotonic()

    # ===== Step 1: 对齐 provider 路径，拿词级时间戳（Suno 还附带 section_hints + gender_hint）=====
    if alignment_provider == "mureka":
        suno_result = await _run_mureka_path(audio_url)
    else:
        suno_result = await _run_suno_path(audio_url, clip_id)

    # ===== Step 2: 构造给 Gemini 的两份 Suno 参考材料 =====
    #   2a) generated_lyrics：传统的歌词文本（兼容调用方已有 lyrics 的场景，如 Suno generate 路径）
    #   2b) suno_alignment_context：Suno 的"对齐真相"，含词级时间戳分行歌词 + section_hints + gender_hint
    #       —— 关键约束："同一行歌词必须落在同一个 segment 里"，让 Gemini 不要把"下/来吧"切两段
    gemini_generated_lyrics = generated_lyrics
    lyrics_source = "user_provided" if generated_lyrics else None
    if not gemini_generated_lyrics and suno_result.clean_words:
        gemini_generated_lyrics = _assemble_lyrics_from_suno_words(suno_result.clean_words)
        lyrics_source = f"{alignment_provider}_aligned_lyrics"

    # granularity 由 caller（music_generation_service）按 TRANSCRIPTION_METHOD_CONFIG 解析后显式传入；
    # 未传时退回 DefaultValues 兜底（与 gemini._resolve_granularity 保持同一来源）。
    _granularity = AudioSegmentGranularity.from_value(
        granularity if granularity is not None else DefaultValues.AUDIO_SEGMENT_GRANULARITY
    )
    suno_alignment_context = _build_suno_alignment_context(suno_result, granularity=_granularity)

    if gemini_generated_lyrics:
        n_lines = gemini_generated_lyrics.count("\n") + 1
        n_chars = len(gemini_generated_lyrics)
        logger.info(
            "🎵 [hybrid] Gemini 将使用 generated_lyrics 做参考转录 | source=%s | lines=%d | chars=%d | preview=%r",
            lyrics_source, n_lines, n_chars,
            (gemini_generated_lyrics[:80] + "...") if n_chars > 80 else gemini_generated_lyrics,
        )
    else:
        logger.info(
            "ℹ️  [hybrid] Suno 未提供歌词（失败/纯音乐），Gemini 不带参考歌词跑（等同纯 Gemini 行为）"
        )
    if suno_alignment_context:
        logger.info(
            "🧭 [hybrid] 已附加 Suno 对齐真相上下文给 Gemini prompt | chars=%d | section_hints=%d | gender_hint=%s",
            len(suno_alignment_context),
            len(suno_result.section_hints),
            suno_result.vocal_gender_hint or "-",
        )

    # ===== Step 3: Gemini 路径，吃 Suno 歌词 + 对齐真相 =====
    t_g = time.monotonic()
    gemini_result = await transcribe_audio_with_gemini(
        audio_url,
        user_option=user_option,
        fill_gaps=fill_gaps,
        user_input=user_input,
        filename=filename,
        generated_lyrics=gemini_generated_lyrics,
        suno_alignment_context=suno_alignment_context,
        granularity=_granularity,
        music_intent=music_intent,
        music_workflow_mode=music_workflow_mode,
    )
    logger.info(
        "⏱️  [hybrid] 串行耗时 总=%.2fs；其中 Gemini=%.2fs；Suno words=%d",
        time.monotonic() - t0, time.monotonic() - t_g, len(suno_result.clean_words),
    )

    if not gemini_result:
        fallback = _build_alignment_fallback_transcription(
            audio_url,
            filename,
            suno_result,
            generated_lyrics=gemini_generated_lyrics,
            alignment_provider=alignment_provider,
            granularity=_granularity,
        )
        if fallback:
            logger.warning(
                "⚠️  [hybrid] Gemini 返回 None，使用 %s 对齐数据降级继续 | segments=%d words=%d",
                alignment_provider,
                len(fallback.segments),
                len(suno_result.clean_words),
            )
            return fallback
        logger.error("❌ [hybrid] Gemini 返回 None 且无可用对齐数据，hybrid 无法继续")
        return None

    # ===== Step 4: 合并 —— 段内 Suno 词集合重算端点 + vocal_presence + 4 步后处理 =====
    merged = _merge_to_hybrid(gemini_result, suno_result)

    suno_used = bool(suno_result.clean_words) or bool(suno_result.vocal_mask)
    outcome = "hybrid(suno→gemini, lyrics_fed)" if suno_used else "fallback_pure_gemini(suno_unavailable)"
    logger.info(
        "🎬 [hybrid] ===== hybrid 转录完成 ===== | "
        "outcome=%s | segments=%d | words=%d | sections=%d | total_elapsed=%.2fs",
        outcome,
        len(merged.segments),
        len((merged.additional_data or {}).get("words") or []),
        len((merged.additional_data or {}).get("sections") or []),
        time.monotonic() - t0,
    )
    # additional_data 标记方法 / 结果，便于下游审计 / 数据回溯
    merged.additional_data = dict(merged.additional_data or {})
    merged.additional_data["transcription_method"] = "hybrid"
    merged.additional_data["alignment_provider"] = alignment_provider
    merged.additional_data["transcription_outcome"] = outcome
    merged.additional_data["audio_segment_granularity"] = _granularity.value
    return merged
