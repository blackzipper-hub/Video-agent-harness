"""
基准 Tool 测试执行器 - 对给定 prompt 列表 × 选中的 tool 类型并发执行，收集结果并生成 HTML 报告。

供 admin 智能测试「场景一」使用；不依赖 Agent，直接调用底层 image/video tool。
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models.tool_enums import ToolType, ToolCategory, AspectRatio, Resolution, DefaultValues
from ..models.user_options import UserOption, ImageGenerationTool, VideoGenerationTool
from ..tools.context_schemas import ImageGenerationContext, VideoGenerationContext

logger = logging.getLogger(__name__)

# 全局限流：基准测试最多同时执行 N 个 tool 调用，避免占满资源影响其他请求
_BASELINE_GLOBAL_CONCURRENCY = 8
_global_baseline_semaphore: Optional[asyncio.Semaphore] = None


def _get_global_baseline_semaphore() -> asyncio.Semaphore:
    global _global_baseline_semaphore
    if _global_baseline_semaphore is None:
        _global_baseline_semaphore = asyncio.Semaphore(_BASELINE_GLOBAL_CONCURRENCY)
    return _global_baseline_semaphore


# ToolType (string) -> 用于取 chain 的 UserOption
_IMAGE_TOOL_TYPE_TO_OPTION = {
    ToolType.GEMINI_2_5_FLASH_IMAGE.value: ImageGenerationTool.NANO_BANANA,
    ToolType.GEMINI_3_PRO_IMAGE_PREVIEW.value: ImageGenerationTool.NANO_BANANA_PRO,
    ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW.value: ImageGenerationTool.NANO_BANANA_2,
    ToolType.SEEDREAM_V4_5.value: ImageGenerationTool.SEEDREAM,
    ToolType.GPT_IMAGE_2.value: ImageGenerationTool.GPT_IMAGE_2,
}
_VIDEO_TOOL_TYPE_TO_OPTION = {
    ToolType.SEEDANCE_V1_PRO_FAST.value: VideoGenerationTool.POLLO_SEEDANCE,
    ToolType.SEEDANCE_V1_5_PRO_FAST.value: VideoGenerationTool.SEEDANCE_V1_5,
    ToolType.SEEDANCE_2_I2V.value: VideoGenerationTool.SEEDANCE_2_I2V,
    ToolType.SEEDANCE_2_I2V_TURBO.value: VideoGenerationTool.SEEDANCE_2_I2V_TURBO,
    ToolType.SEEDANCE_2_FAST_I2V.value: VideoGenerationTool.SEEDANCE_2_FAST_I2V,
    ToolType.SEEDANCE_2_FAST_I2V_TURBO.value: VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO,
    ToolType.WAN_2_5_I2V.value: VideoGenerationTool.WAN_2_5,
    ToolType.WAN_2_6_FLASH_I2V.value: VideoGenerationTool.WAN_2_6,
    ToolType.LTX_2_3_LIPSYNC.value: VideoGenerationTool.LTX_2_3,
    ToolType.KLING_V2_AI_AVATAR_PRO.value: VideoGenerationTool.KLING_V2_AI_AVATAR_PRO,
    ToolType.WAN_2_2_SPEECH_TO_VIDEO.value: VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO,
    ToolType.KLING_V3_STD.value: VideoGenerationTool.KLING_V3_STD,
    ToolType.HAPPYHORSE_1_0_I2V.value: VideoGenerationTool.HAPPYHORSE_1_0_I2V,
    ToolType.HAPPYHORSE_1_1_I2V.value: VideoGenerationTool.HAPPYHORSE_1_1_I2V,
    ToolType.SORA_2.value: VideoGenerationTool.OPENAI_SORA,
    ToolType.SORA_2_PRO.value: VideoGenerationTool.OPENAI_SORA_PRO,
}


def _image_tool_types() -> List[str]:
    return list(_IMAGE_TOOL_TYPE_TO_OPTION.keys())


def _video_tool_types() -> List[str]:
    return list(_VIDEO_TOOL_TYPE_TO_OPTION.keys())


def _is_image_tool_type(t: str) -> bool:
    return t in _IMAGE_TOOL_TYPE_TO_OPTION


def _is_video_tool_type(t: str) -> bool:
    return t in _VIDEO_TOOL_TYPE_TO_OPTION


@dataclass
class EvalRuntime:
    """Minimal runtime for tool ainvoke: only .context is used."""

    context: Any


def _get_single_image_tool_info(tool_type_str: str):
    """Return (ToolInfo,) for the given image ToolType value, or None."""
    from ..tools.image.image_tool_wrapper import _get_t2i_chain
    opt = _IMAGE_TOOL_TYPE_TO_OPTION.get(tool_type_str)
    if not opt:
        return None
    chain = _get_t2i_chain(opt)
    for info in chain:
        if info.tool_type and info.tool_type.value == tool_type_str:
            return info
    return chain[0] if chain else None


def _get_single_video_tool_info(tool_type_str: str):
    """Return (ToolInfo,) for the given video ToolType value, or None."""
    from ..tools.video.video_tool_wrapper import _get_video_chain
    opt = _VIDEO_TOOL_TYPE_TO_OPTION.get(tool_type_str)
    if not opt:
        return None
    chain = _get_video_chain(opt)
    for info in chain:
        if info.tool_type and info.tool_type.value == tool_type_str:
            return info
    return chain[0] if chain else None


async def _run_one_image(
    prompt: str,
    tool_type_str: str,
    reference_image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run one image tool (T2I or I2I). Returns dict with success, image_url, error_msg, tool_type, prompt."""
    info = _get_single_image_tool_info(tool_type_str)
    if not info:
        return {
            "success": False,
            "tool_type": tool_type_str,
            "prompt": prompt[:200],
            "error_msg": f"Unknown image tool type: {tool_type_str}",
            "image_url": None,
        }
    try:
        from ..models.tool_enums import AspectRatio, Resolution
        user_option = UserOption.default()
        context = ImageGenerationContext(
            aspect_ratio=AspectRatio(user_option.aspect_ratio.value) if user_option.aspect_ratio else DefaultValues.IMAGE_ASPECT_RATIO,
            resolution=Resolution(user_option.resolution.value) if user_option.resolution else DefaultValues.IMAGE_RESOLUTION,
            reference_image_urls=reference_image_urls or None,
            model=info.tool_type,
        )
        runtime = EvalRuntime(context=context)
        result = await info.tool.ainvoke({"prompt": prompt, "runtime": runtime})
        if getattr(result, "success", False):
            image_url = getattr(result, "image_url", None) or getattr(result, "url", None)
            return {
                "success": True,
                "tool_type": tool_type_str,
                "prompt": prompt[:500],
                "image_url": image_url,
                "error_msg": None,
            }
        return {
            "success": False,
            "tool_type": tool_type_str,
            "prompt": prompt[:200],
            "error_msg": getattr(result, "error_msg", None) or getattr(result, "message", "") or "Unknown failure",
            "image_url": None,
        }
    except Exception as e:
        logger.exception("eval image run failed: tool=%s", tool_type_str)
        return {
            "success": False,
            "tool_type": tool_type_str,
            "prompt": prompt[:200],
            "error_msg": str(e),
            "image_url": None,
        }


async def _run_one_video(
    prompt: str,
    tool_type_str: str,
    start_image_url: str,
    duration: int = 5,
    end_image_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one video tool (I2V). Returns dict with success, video_url, error_msg, tool_type, prompt."""
    info = _get_single_video_tool_info(tool_type_str)
    if not info:
        return {
            "success": False,
            "tool_type": tool_type_str,
            "prompt": prompt[:200],
            "error_msg": f"Unknown video tool type: {tool_type_str}",
            "video_url": None,
        }
    try:
        user_option = UserOption.default()
        context = VideoGenerationContext(
            aspect_ratio=DefaultValues.VIDEO_ASPECT_RATIO,
            resolution=Resolution(user_option.resolution.value) if user_option.resolution else DefaultValues.VIDEO_RESOLUTION,
            start_image_url=start_image_url,
            end_image_url=end_image_url,
            duration=duration,
        )
        runtime = EvalRuntime(context=context)
        result = await info.tool.ainvoke({
            "i2v_prompt": prompt,
            "start_image_url": start_image_url,
            "duration": duration,
            "runtime": runtime,
        })
        if getattr(result, "success", False):
            video_url = getattr(result, "video_url", None) or getattr(result, "url", None)
            return {
                "success": True,
                "tool_type": tool_type_str,
                "prompt": prompt[:500],
                "video_url": video_url,
                "error_msg": None,
            }
        return {
            "success": False,
            "tool_type": tool_type_str,
            "prompt": prompt[:200],
            "error_msg": getattr(result, "error_msg", None) or getattr(result, "message", "") or "Unknown failure",
            "video_url": None,
        }
    except Exception as e:
        logger.exception("eval video run failed: tool=%s", tool_type_str)
        return {
            "success": False,
            "tool_type": tool_type_str,
            "prompt": prompt[:200],
            "error_msg": str(e),
            "video_url": None,
        }


async def run_baseline_tasks(
    prompts: List[str],
    tool_types: List[str],
    start_image_url: Optional[str] = None,
    duration: int = 5,
    reference_image_urls_per_prompt: Optional[List[Optional[List[str]]]] = None,
    max_concurrency: int = 4,
) -> List[Dict[str, Any]]:
    """
    并发执行所有 (prompt × tool_type) 组合。
    - Image tool: 使用 prompt + 可选的 reference_image_urls（按 prompt 下标取）。
    - Video tool: 使用 prompt + start_image_url + duration（必填 start_image_url）。

    Returns list of result dicts (each has prompt_index, tool_type, success, image_url or video_url, error_msg).
    """
    if not prompts or not tool_types:
        return []

    refs = reference_image_urls_per_prompt if reference_image_urls_per_prompt is not None else [None] * len(prompts)
    if len(refs) < len(prompts):
        refs = refs + [None] * (len(prompts) - len(refs))

    image_types = [t for t in tool_types if _is_image_tool_type(t)]
    video_types = [t for t in tool_types if _is_video_tool_type(t)]
    if not start_image_url and video_types:
        logger.warning("Video tools selected but start_image_url is empty; video tasks will fail.")

    sem = asyncio.Semaphore(max_concurrency)
    global_sem = _get_global_baseline_semaphore()
    results: List[Dict[str, Any]] = []

    async def run_image(prompt_idx: int, prompt: str, tt: str):
        async with global_sem:
            async with sem:
                r = await _run_one_image(prompt, tt, reference_image_urls=refs[prompt_idx] if prompt_idx < len(refs) else None)
                r["prompt_index"] = prompt_idx
                return r

    async def run_video(prompt_idx: int, prompt: str, tt: str):
        async with global_sem:
            async with sem:
                per_prompt_refs = refs[prompt_idx] if prompt_idx < len(refs) else None
                frame_url = (per_prompt_refs[0] if per_prompt_refs and len(per_prompt_refs) > 0 else None) or start_image_url or ""
                r = await _run_one_video(prompt, tt, frame_url, duration)
                r["prompt_index"] = prompt_idx
                return r

    tasks = []
    for i, prompt in enumerate(prompts):
        for tt in image_types:
            tasks.append(run_image(i, prompt, tt))
        for tt in video_types:
            tasks.append(run_video(i, prompt, tt))

    if tasks:
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for x in done:
            if isinstance(x, Exception):
                results.append({"success": False, "error_msg": str(x), "prompt_index": -1, "tool_type": ""})
            else:
                results.append(x)

    return results


def build_baseline_report_html(
    run_id: str,
    config: Dict[str, Any],
    results: List[Dict[str, Any]],
    report_cdn_url: str,
) -> str:
    """Generate a single HTML report page for baseline run (with full params and prompt/URL per row)."""
    import html as html_module
    h = html_module.escape
    prompts = config.get("prompts", [])
    tool_types = config.get("tool_types", [])
    refs_per_prompt = config.get("reference_image_urls_per_prompt") or []
    start_image_url = (config.get("start_image_url") or "").strip()
    duration = config.get("duration", 5)
    created_at = config.get("created_at", "")
    status = config.get("status", "")

    # 运行参数区块（首帧图展示缩略图 + 链接）
    if start_image_url:
        start_url_cell = (
            f'<img src="{h(start_image_url)}" alt="首帧" style="max-width:200px;max-height:150px;object-fit:contain;display:block;margin-bottom:4px;" />'
            f'<a href="{h(start_image_url)}" target="_blank" rel="noopener">{h(start_image_url[:80] + ("..." if len(start_image_url) > 80 else ""))}</a>'
        )
    else:
        start_url_cell = "—"
    params_lines = [
        f"<tr><td>Run ID</td><td><code>{h(run_id)}</code></td></tr>",
        f"<tr><td>状态</td><td>{h(status)}</td></tr>",
        f"<tr><td>创建时间</td><td>{h(created_at) if created_at else '—'}</td></tr>",
        f"<tr><td>Prompt 条数</td><td>{len(prompts)}</td></tr>",
        f"<tr><td>Tools</td><td>{h(', '.join(tool_types))}</td></tr>",
        f"<tr><td>视频首帧图 URL</td><td>{start_url_cell}</td></tr>",
        f"<tr><td>视频时长(秒)</td><td>{duration}</td></tr>",
    ]
    params_table = "\n".join(params_lines)

    # 每条 Prompt 的输入（完整 prompt + 参考图/首帧展示为缩略图；I2V 无 refs 时用全局首帧）
    _img_thumb_style = "max-width:120px;max-height:90px;object-fit:contain;vertical-align:middle;margin:2px;"
    input_rows = []
    for i, p in enumerate(prompts):
        refs = refs_per_prompt[i] if i < len(refs_per_prompt) else None
        ref_cell = "—"
        if refs and isinstance(refs, list):
            imgs = [
                f'<a href="{h(u)}" target="_blank" rel="noopener"><img src="{h(u)}" alt="参考图" style="{_img_thumb_style}" /></a>'
                for u in refs if u
            ]
            ref_cell = " ".join(imgs) if imgs else "—"
        if ref_cell == "—" and start_image_url:
            ref_cell = f'<a href="{h(start_image_url)}" target="_blank" rel="noopener"><img src="{h(start_image_url)}" alt="首帧" style="{_img_thumb_style}" /></a>'
        input_rows.append(
            f"<tr><td>{i}</td><td><pre style=\"margin:0;white-space:pre-wrap;word-break:break-word;\">{h(p)}</pre></td><td>{ref_cell}</td></tr>"
        )
    input_table_body = "\n".join(input_rows)

    # 结果明细表：Prompt#, Tool, 完整 Prompt, 输入(参考图/首帧), 成功, 结果, 错误
    def _is_video_tool(t: str) -> bool:
        return t in (
            "seedance-v1-pro-fast", "seedance-v1.5-pro-fast", "wan-2.5-i2v", "wan-2.6-flash-i2v",
            "kling-v3.0-std", "sora-2", "sora-2-pro",
        )

    rows = []
    for r in results:
        idx = r.get("prompt_index", -1)
        full_prompt = (prompts[idx] if 0 <= idx < len(prompts) else "")
        tt = r.get("tool_type", "")
        success = r.get("success", False)
        # 本行输入：图像=参考图缩略图，I2V/视频=该条首帧缩略图（per-prompt 或全局）
        if _is_video_tool(tt):
            per_refs = refs_per_prompt[idx] if idx < len(refs_per_prompt) and refs_per_prompt[idx] else None
            frame_url = (per_refs[0] if per_refs and isinstance(per_refs, list) and len(per_refs) > 0 else None) or start_image_url or ""
            if frame_url:
                input_desc = f'<a href="{h(frame_url)}" target="_blank" rel="noopener"><img src="{h(frame_url)}" alt="首帧" style="max-width:160px;max-height:120px;object-fit:contain;" /></a>'
            else:
                input_desc = "—"
        else:
            refs = refs_per_prompt[idx] if idx < len(refs_per_prompt) and refs_per_prompt[idx] else None
            if refs and isinstance(refs, list):
                imgs = [
                    f'<a href="{h(u)}" target="_blank" rel="noopener"><img src="{h(u)}" alt="参考图" style="max-width:120px;max-height:90px;object-fit:contain;margin:2px;" /></a>'
                    for u in refs if u
                ]
                input_desc = " ".join(imgs) if imgs else "—"
            else:
                input_desc = "—"
        if r.get("image_url"):
            media = f'<img src="{h(r["image_url"])}" alt="result" style="max-width:320px;max-height:240px;object-fit:contain;" />'
        elif r.get("video_url"):
            media = f'<video src="{h(r["video_url"])}" controls style="max-width:320px;max-height:240px;"></video>'
        else:
            media = "<span>—</span>"
        err = r.get("error_msg") or ""
        rows.append(
            f"<tr><td>{idx}</td><td>{h(tt)}</td>"
            f"<td><pre style=\"margin:0;white-space:pre-wrap;word-break:break-word;max-width:320px;\">{h(full_prompt)}</pre></td>"
            f"<td>{input_desc}</td>"
            f"<td>{'✅' if success else '❌'}</td><td>{media}</td><td><small>{h(err)}</small></td></tr>"
        )
    table_body = "\n".join(rows)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>Baseline Run {run_id}</title>
  <style>
    body {{ font-family: sans-serif; margin: 16px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f5f5; }}
    pre {{ font-size: 12px; }}
    .section {{ margin-top: 24px; }}
    h2 {{ font-size: 1.1em; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>基准 Tool 测试报告</h1>

  <div class="section">
    <h2>运行参数</h2>
    <table style="max-width: 720px;">
      <tbody>
{params_table}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>每条 Prompt 的输入（完整文案 + 参考图）</h2>
    <table>
      <thead><tr><th>Prompt#</th><th>Prompt 全文</th><th>参考图</th></tr></thead>
      <tbody>
{input_table_body}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>结果明细（每行：Tool + 输入 + 成功/结果/错误）</h2>
    <table>
      <thead><tr><th>Prompt#</th><th>Tool</th><th>Prompt 全文</th><th>输入(参考图/首帧)</th><th>成功</th><th>结果</th><th>错误</th></tr></thead>
      <tbody>
{table_body}
      </tbody>
    </table>
  </div>

  <p><small>Report URL: {h(report_cdn_url)}</small></p>
</body>
</html>"""
    return html


def list_supported_baseline_tool_types() -> Dict[str, List[str]]:
    """Return { t2i, i2i, i2v, t2v } 按场景分组的 tool 类型。t2i/i2i 同图工具列表，i2v 为图生视频，t2v 暂空。"""
    image_list = _image_tool_types()
    video_list = _video_tool_types()
    return {
        "t2i": image_list,
        "i2i": image_list,
        "i2v": video_list,
        "t2v": [],  # 暂无文生视频 tool，后续可加 sora-2 / sora-2-pro 的 T2V 模式
    }
