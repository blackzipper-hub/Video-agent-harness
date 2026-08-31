"""
Video Wrapper 集成测试 — I2V，所有模型 + 所有 Wrapper chain 配置。

测试内容：
  1. Individual I2V: Seedance v1 / v1.5 / Seedance 2.0 Fast Turbo / Kling v3 / Wan 2.5 / Wan 2.6 / Sora 2 / Sora 2 Pro
  2. Wrapper I2V:   AUTO / POLLO_SEEDANCE / SEEDANCE_V1_5 / SEEDANCE_2_FAST_I2V_TURBO / KLING_V3_STD / WAN_2_5 / WAN_2_6 / OPENAI_SORA / OPENAI_SORA_PRO
  共 17 个 case，覆盖 video_tool_wrapper.py 分支。

运行示例：
  ENVIRONMENT=development pytest tests/tools/test_video_wrapper_integration.py -v -s
  ENVIRONMENT=development pytest tests/tools/test_video_wrapper_integration.py -v -s --dry-run
  ENVIRONMENT=development pytest tests/tools/test_video_wrapper_integration.py -v -s -k "wrapper"
"""
import os
from pathlib import Path

# 最先加载 dev/prod 配置
_environment = os.getenv("ENVIRONMENT", "development").lower()
from dotenv import load_dotenv
if _environment == "production":
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.production")
else:
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.development")

import asyncio
import json
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from types import SimpleNamespace
from typing import Optional, List, Dict, Any

import pytest

# ==================== 测试常量 ====================

# 首帧图 — 用于 I2V 测试
START_IMAGE_URL = "https://cdn-dev.newai.land/images/5bc522f7-26ab-4c7c-9a9b-31a4caa9c158.webp"

# 尾帧图 — 仅 Seedance 等支持 end_image 的模型使用
END_IMAGE_URL = "https://cdn-dev.newai.land/images/a2956350-132c-4b9a-bcb5-41a3484adab4.webp"

I2V_PROMPT = (
    "The character slowly looks up from his phone with a weary expression, "
    "his brow slightly furrowed. The golden retriever wearing sunglasses tilts "
    "its head slightly, looking up at the character with warm eyes. "
    "A gentle breeze rustles through the narrow Tokyo night alley, "
    "casting shifting shadows from the warm yellow street lamps. "
    "Subtle camera dolly in. Warm yellow tones, cinematic lighting, film grain."
)

# 默认视频时长（秒）
VIDEO_DURATION = 5

# 并发控制 — 视频生成较慢且贵，控制并发
MAX_CONCURRENT = 2


# ==================== 结果记录 ====================


@dataclass
class TestCaseResult:
    """单个测试用例结果"""
    test_id: str
    test_type: str           # "individual" | "wrapper"
    mode: str                # "i2v"
    tool_name: str           # 模型/工具名称
    user_option: str         # wrapper 的 user_option, 如 "POLLO_SEEDANCE" / "N/A"
    chain_config: str        # chain 描述, 如 "v1 → w26"
    success: bool = False
    video_url: Optional[str] = None
    error_msg: Optional[str] = None
    raw_error_msg: Optional[str] = None
    model_used: Optional[str] = None
    duration_sec: float = 0.0
    video_duration: Optional[float] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    prompt_preview: str = ""
    start_image: str = ""
    end_image: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ==================== Individual Tool Runners ====================


async def _run_individual_video(
    tool_fn,
    tool_label: str,
    prompt: str,
    start_image_url: str,
    duration: int,
    context_kwargs: Optional[Dict[str, Any]] = None,
) -> TestCaseResult:
    """通用 individual video tool runner."""
    from app.tools.context_schemas import VideoGenerationContext
    from app.models.tool_enums import DefaultValues

    ctx_args = {
        "start_image_url": start_image_url,
    }
    if context_kwargs:
        ctx_args.update(context_kwargs)
    ctx = VideoGenerationContext(**ctx_args)
    runtime = SimpleNamespace(context=ctx)

    tid = f"individual_i2v_{tool_label.lower().replace(' ', '_').replace('.', '_')}"
    start = time.perf_counter()
    try:
        result = await tool_fn.ainvoke({
            "i2v_prompt": prompt,
            "start_image_url": start_image_url,
            "duration": duration,
            "runtime": runtime,
        })
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="individual", mode="i2v",
            tool_name=f"{tool_label} (I2V)", user_option="N/A", chain_config="N/A",
            success=result.success, video_url=result.video_url,
            error_msg=result.error_msg or result.message if not result.success else None,
            raw_error_msg=result.raw_error_msg,
            model_used=result.model, duration_sec=round(dur, 2),
            video_duration=result.duration, resolution=result.resolution,
            aspect_ratio=result.aspect_ratio,
            prompt_preview=prompt[:150], start_image=start_image_url,
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="individual", mode="i2v",
            tool_name=f"{tool_label} (I2V)", user_option="N/A", chain_config="N/A",
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150], start_image=start_image_url,
        )


# ==================== Wrapper Runners ====================


async def _run_wrapper_i2v(
    prompt: str,
    start_image_url: str,
    duration: int,
    user_option_enum,
    option_label: str,
    chain_desc: str,
    end_image_url: Optional[str] = None,
) -> TestCaseResult:
    from app.tools.context_schemas import VideoGenerationContext
    from app.tools.video.video_tool_wrapper import create_video_wrapper_tools
    from app.models.tool_enums import ToolMode

    mock_option = SimpleNamespace(video_generation_tool=user_option_enum)
    tools_info_list = create_video_wrapper_tools(mode=ToolMode.I2V, user_option=mock_option)
    assert tools_info_list, f"No I2V tool created for option={option_label}"
    wrapper_tool = tools_info_list[0].tool

    ctx = VideoGenerationContext(
        start_image_url=start_image_url,
    )
    if end_image_url:
        ctx.end_image_url = end_image_url
    runtime = SimpleNamespace(context=ctx)

    tid = f"wrapper_i2v_{option_label.lower()}"
    start = time.perf_counter()
    try:
        invoke_args = {
            "i2v_prompt": prompt,
            "start_image_url": start_image_url,
            "duration": duration,
            "runtime": runtime,
        }
        if end_image_url:
            invoke_args["end_image_url"] = end_image_url
        result = await wrapper_tool.ainvoke(invoke_args)
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="wrapper", mode="i2v",
            tool_name=f"Wrapper I2V ({option_label})",
            user_option=option_label, chain_config=chain_desc,
            success=result.success, video_url=result.video_url,
            error_msg=result.error_msg or result.message if not result.success else None,
            raw_error_msg=result.raw_error_msg,
            model_used=result.model, duration_sec=round(dur, 2),
            video_duration=result.duration, resolution=result.resolution,
            aspect_ratio=result.aspect_ratio,
            prompt_preview=prompt[:150], start_image=start_image_url,
            end_image=end_image_url,
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="wrapper", mode="i2v",
            tool_name=f"Wrapper I2V ({option_label})",
            user_option=option_label, chain_config=chain_desc,
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150],
            start_image=start_image_url, end_image=end_image_url,
        )


# ==================== 并发执行 ====================


async def _run_all_cases(dry_run: bool = False) -> List[TestCaseResult]:
    """构建所有 test case 并行执行，返回结果列表。"""
    from app.tools.video.seedance import (
        generate_video_with_wavespeed_seedance,
        generate_video_with_wavespeed_seedance_v1_5,
    )
    from app.tools.video.seedance_2_i2v import (
        generate_video_with_wavespeed_seedance_2_i2v,
    )
    from app.tools.video.seedance_2_i2v_turbo import (
        generate_video_with_wavespeed_seedance_2_i2v_turbo,
    )
    from app.tools.video.seedance_2_fast_i2v import (
        generate_video_with_wavespeed_seedance_2_fast_i2v,
    )
    from app.tools.video.seedance_2_fast_turbo import (
        generate_video_with_wavespeed_seedance_2_fast_turbo,
    )
    from app.tools.video.kling import generate_video_with_wavespeed_kling
    from app.tools.video.happyhorse_1_0_i2v import generate_video_with_wavespeed_happyhorse_1_0_i2v
    from app.tools.video.happyhorse_1_1_i2v import generate_video_with_wavespeed_happyhorse_1_1_i2v
    from app.tools.video.wan25 import generate_video_with_wavespeed_wan25
    from app.tools.video.wan26_flash import generate_video_with_wavespeed_wan26
    from app.tools.video.sora import (
        generate_video_with_sora_2_i2v,
        generate_video_with_sora_2_pro_i2v,
    )
    from app.models.user_options import VideoGenerationTool

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def guarded(coro):
        async with sem:
            return await coro

    tasks = []

    # ---- 1) Individual I2V tools ----
    individual_tools = [
        (generate_video_with_wavespeed_seedance,     "Seedance v1.0"),
        (generate_video_with_wavespeed_seedance_v1_5, "Seedance v1.5"),
        (generate_video_with_wavespeed_seedance_2_i2v, "Seedance 2.0 I2V"),
        (generate_video_with_wavespeed_seedance_2_i2v_turbo, "Seedance 2.0 Turbo I2V"),
        (generate_video_with_wavespeed_seedance_2_fast_i2v, "Seedance 2.0 Fast I2V"),
        (generate_video_with_wavespeed_seedance_2_fast_turbo, "Seedance 2.0 Turbo"),
        (generate_video_with_wavespeed_kling,         "Kling v3"),
        (generate_video_with_wavespeed_happyhorse_1_0_i2v, "HappyHorse 1.0"),
        (generate_video_with_wavespeed_happyhorse_1_1_i2v, "HappyHorse 1.1"),
        (generate_video_with_wavespeed_wan25,         "Wan 2.5"),
        (generate_video_with_wavespeed_wan26,         "Wan 2.6 Flash"),
        (generate_video_with_sora_2_i2v,              "Sora 2"),
        (generate_video_with_sora_2_pro_i2v,          "Sora 2 Pro"),
    ]
    for tool_fn, label in individual_tools:
        tasks.append((
            f"individual_i2v_{label.lower().replace(' ', '_').replace('.', '_')}",
            lambda fn=tool_fn, lb=label: _run_individual_video(
                fn, lb, I2V_PROMPT, START_IMAGE_URL, VIDEO_DURATION,
            ),
        ))

    # ---- 2) Wrapper I2V — all VideoGenerationTool options ----
    wrapper_configs = [
        (None,                                 "AUTO",              "v1 → w26"),
        (VideoGenerationTool.POLLO_SEEDANCE,   "POLLO_SEEDANCE",   "v1 → w26"),
        (VideoGenerationTool.SEEDANCE_V1_5,    "SEEDANCE_V1_5",    "v1.5 → v1 → w26"),
        (VideoGenerationTool.SEEDANCE_2_I2V, "SEEDANCE_2", "sd20 → sd2t → sd2 → sd2ft → v1 → w26"),
        (VideoGenerationTool.SEEDANCE_2_I2V_TURBO, "SEEDANCE_2_TURBO", "sd2t → sd2ft → sd20 → sd2 → v1 → w26"),
        (VideoGenerationTool.SEEDANCE_2_FAST_I2V, "SEEDANCE_2_FAST", "sd2 → sd2ft → sd2t → sd20 → v1 → w26"),
        (VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO, "SEEDANCE_2_FAST_TURBO", "sd2ft → sd2t → sd2 → sd20 → v1 → w26"),
        (VideoGenerationTool.KLING_V3_STD,     "KLING_V3_STD",     "kling → v1 → w26"),
        (VideoGenerationTool.HAPPYHORSE_1_0_I2V, "HAPPYHORSE_1_0", "happyhorse1.0 → v1 → w26"),
        (VideoGenerationTool.HAPPYHORSE_1_1_I2V, "HAPPYHORSE_1_1", "happyhorse → v1 → w26"),
        (VideoGenerationTool.WAN_2_5,          "WAN_2_5",          "w25 → v1 → w26"),
        (VideoGenerationTool.WAN_2_6,          "WAN_2_6",          "w26 → v1"),
        (VideoGenerationTool.OPENAI_SORA,      "OPENAI_SORA",      "sora2 → v1 → w26"),
        (VideoGenerationTool.OPENAI_SORA_PRO,  "OPENAI_SORA_PRO",  "sora2pro → v1 → w26"),
    ]
    for opt_enum, opt_label, chain_desc in wrapper_configs:
        tasks.append((
            f"wrapper_i2v_{opt_label.lower()}",
            lambda o=opt_enum, l=opt_label, c=chain_desc: _run_wrapper_i2v(
                I2V_PROMPT, START_IMAGE_URL, VIDEO_DURATION, o, l, c,
            ),
        ))

    if dry_run:
        return [
            TestCaseResult(
                test_id=tid, test_type="dry_run", mode="i2v",
                tool_name=tid, user_option="N/A", chain_config="N/A",
                prompt_preview="(dry run - no API calls)",
                start_image=START_IMAGE_URL,
            )
            for tid, _ in tasks
        ]

    # ---- 并发执行 ----
    print(f"\n{'='*60}")
    print(f"  Video Wrapper Integration Test")
    print(f"  Total cases: {len(tasks)} | Concurrency: {MAX_CONCURRENT}")
    print(f"{'='*60}\n")

    coros = [guarded(fn()) for _, fn in tasks]
    results = await asyncio.gather(*coros, return_exceptions=True)

    # 将异常转为 TestCaseResult
    final: List[TestCaseResult] = []
    for (tid, _), res in zip(tasks, results):
        if isinstance(res, Exception):
            final.append(TestCaseResult(
                test_id=tid, test_type="error", mode="i2v",
                tool_name=tid, user_option="N/A", chain_config="N/A",
                success=False, error_msg=str(res), raw_error_msg=traceback.format_exc(),
                start_image=START_IMAGE_URL,
            ))
        else:
            final.append(res)

    return final


# ==================== 报告生成 ====================


def _write_json_report(results: List[TestCaseResult], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "test_video_wrapper_results.json"
    payload = {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "start_image_url": START_IMAGE_URL,
        "end_image_url": END_IMAGE_URL,
        "test_cases_count": len(results),
        "success_count": sum(1 for r in results if r.success),
        "failure_count": sum(1 for r in results if not r.success),
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "i2v_prompt": I2V_PROMPT,
        "results": [asdict(r) for r in results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote JSON: {path}")
    return path


def _write_html_report(results: List[TestCaseResult], out_dir: Path) -> Path:
    """生成单文件 HTML 报告：所有数据内嵌，双击即可打开。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "test_video_wrapper_report.html"

    payload = {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "start_image_url": START_IMAGE_URL,
        "end_image_url": END_IMAGE_URL,
        "test_cases_count": len(results),
        "success_count": sum(1 for r in results if r.success),
        "failure_count": sum(1 for r in results if not r.success),
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "i2v_prompt": I2V_PROMPT,
        "results": [asdict(r) for r in results],
    }
    json_str = json.dumps(payload, ensure_ascii=False)
    json_escaped = json_str.replace("<", "\\u003c")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Video Wrapper Integration Test Report</title>
  <style>
    :root {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; }}
    body {{ max-width: 1800px; margin: 0 auto; padding: 1.5rem; }}
    h1 {{ font-size: 1.6rem; color: #f1f5f9; border-bottom: 2px solid #334155; padding-bottom: 0.5rem; }}
    h2 {{ font-size: 1.2rem; color: #94a3b8; margin-top: 2rem; }}
    .meta {{ color: #64748b; font-size: 0.85rem; margin-bottom: 1rem; line-height: 1.6; }}
    .summary {{ display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }}
    .stat {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 1rem 1.5rem; min-width: 120px; text-align: center; }}
    .stat .num {{ font-size: 2rem; font-weight: 700; }}
    .stat .label {{ font-size: 0.8rem; color: #94a3b8; margin-top: 0.25rem; }}
    .stat.ok .num {{ color: #4ade80; }}
    .stat.err .num {{ color: #f87171; }}
    .stat.total .num {{ color: #60a5fa; }}
    .stat.time .num {{ color: #fbbf24; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    th, td {{ border: 1px solid #334155; padding: 0.6rem 0.8rem; text-align: left; vertical-align: top; font-size: 0.85rem; }}
    th {{ background: #1e293b; color: #94a3b8; font-weight: 600; position: sticky; top: 0; z-index: 1; }}
    tr:nth-child(even) {{ background: #1e293b44; }}
    tr:hover {{ background: #1e293b88; }}
    .ok {{ color: #4ade80; }}
    .err {{ color: #f87171; }}
    .warn {{ color: #fbbf24; }}
    .chain {{ font-size: 0.75rem; color: #818cf8; }}
    .prompt-cell {{ font-size: 0.75rem; color: #94a3b8; max-width: 280px; word-break: break-all; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }}
    .badge-i2v {{ background: #be185d; color: #fce7f3; }}
    .badge-individual {{ background: #0f766e; color: #ccfbf1; }}
    .badge-wrapper {{ background: #b45309; color: #fef3c7; }}
    .start-img {{ width: 100px; height: 60px; object-fit: cover; border-radius: 4px; border: 1px solid #334155; }}
    .video-link {{ color: #60a5fa; text-decoration: underline; word-break: break-all; max-width: 250px; display: inline-block; }}
    .video-cell video {{ max-width: 280px; max-height: 200px; border-radius: 6px; cursor: pointer; }}
    a {{ color: #60a5fa; }}
  </style>
</head>
<body>
  <h1>Video Wrapper Integration Test Report</h1>
  <div class="meta" id="meta"></div>
  <div class="summary" id="summary"></div>

  <h2>Start / End Images</h2>
  <div style="display:flex;gap:1rem;margin:0.5rem 0;">
    <div>
      <div style="color:#94a3b8;font-size:0.8rem;margin-bottom:4px;">Start Image</div>
      <a href="" id="startImgLink" target="_blank"><img id="startImg" class="start-img" style="width:160px;height:100px;" src="" /></a>
    </div>
    <div>
      <div style="color:#94a3b8;font-size:0.8rem;margin-bottom:4px;">End Image (optional)</div>
      <a href="" id="endImgLink" target="_blank"><img id="endImg" class="start-img" style="width:160px;height:100px;" src="" /></a>
    </div>
  </div>

  <h2>Individual Tool Results</h2>
  <table id="individualTable">
    <thead><tr><th>#</th><th>Tool</th><th>Success</th><th>Model</th><th>Duration</th><th>Video Duration</th><th>Resolution</th><th>Aspect Ratio</th><th>Output</th><th>Error</th><th>Prompt</th></tr></thead>
    <tbody></tbody>
  </table>

  <h2>Wrapper Results (Fallback Chain)</h2>
  <table id="wrapperTable">
    <thead><tr><th>#</th><th>User Option</th><th>Chain</th><th>Success</th><th>Model</th><th>Duration</th><th>Video Duration</th><th>Resolution</th><th>Aspect Ratio</th><th>Output</th><th>Error</th><th>Prompt</th></tr></thead>
    <tbody></tbody>
  </table>

  <script>window.VID_RESULTS = {json_escaped};</script>
  <script>
(function(){{
  var data = window.VID_RESULTS;
  var results = data.results || [];

  // Meta
  var metaEl = document.getElementById("meta");
  metaEl.innerHTML = "Environment: <b>" + data.environment + "</b> | Ran: " + data.ran_at + "<br>" +
    "I2V Prompt: <i>" + (data.i2v_prompt || "").substring(0, 150) + "...</i>";

  // Start/End images
  document.getElementById("startImg").src = data.start_image_url || "";
  document.getElementById("startImgLink").href = data.start_image_url || "";
  document.getElementById("endImg").src = data.end_image_url || "";
  document.getElementById("endImgLink").href = data.end_image_url || "";

  // Summary
  var totalTime = results.reduce(function(s, r) {{ return s + (r.duration_sec || 0); }}, 0);
  var sumEl = document.getElementById("summary");
  sumEl.innerHTML =
    '<div class="stat total"><div class="num">' + data.test_cases_count + '</div><div class="label">Total Cases</div></div>' +
    '<div class="stat ok"><div class="num">' + data.success_count + '</div><div class="label">Succeeded</div></div>' +
    '<div class="stat err"><div class="num">' + data.failure_count + '</div><div class="label">Failed</div></div>' +
    '<div class="stat time"><div class="num">' + totalTime.toFixed(0) + 's</div><div class="label">Total Wall Time</div></div>';

  // Tables
  var indTbody = document.querySelector("#individualTable tbody");
  var wrapTbody = document.querySelector("#wrapperTable tbody");
  var indIdx = 0, wrapIdx = 0;

  function videoCell(url) {{
    if (!url) return '-';
    return '<div class="video-cell"><video controls preload="metadata" src="' + url + '"></video><br><a href="' + url + '" target="_blank" style="font-size:0.7rem;">Open</a></div>';
  }}

  results.forEach(function(r) {{
    if (r.test_type === "individual") {{
      indIdx++;
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td>' + indIdx + '</td>' +
        '<td>' + r.tool_name + '</td>' +
        '<td class="' + (r.success ? 'ok' : 'err') + '">' + (r.success ? 'OK' : 'FAIL') + '</td>' +
        '<td>' + (r.model_used || '-') + '</td>' +
        '<td>' + r.duration_sec + 's</td>' +
        '<td>' + (r.video_duration ? r.video_duration + 's' : '-') + '</td>' +
        '<td>' + (r.resolution || '-') + '</td>' +
        '<td>' + (r.aspect_ratio || '-') + '</td>' +
        '<td>' + videoCell(r.video_url) + '</td>' +
        '<td class="err" style="max-width:300px;word-break:break-all;">' + (r.error_msg || '-') + '</td>' +
        '<td class="prompt-cell">' + (r.prompt_preview || '-') + '</td>';
      indTbody.appendChild(tr);
    }} else if (r.test_type === "wrapper") {{
      wrapIdx++;
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td>' + wrapIdx + '</td>' +
        '<td><span class="badge badge-wrapper">' + r.user_option + '</span></td>' +
        '<td class="chain">' + (r.chain_config || '-') + '</td>' +
        '<td class="' + (r.success ? 'ok' : 'err') + '">' + (r.success ? 'OK' : 'FAIL') + '</td>' +
        '<td>' + (r.model_used || '-') + '</td>' +
        '<td>' + r.duration_sec + 's</td>' +
        '<td>' + (r.video_duration ? r.video_duration + 's' : '-') + '</td>' +
        '<td>' + (r.resolution || '-') + '</td>' +
        '<td>' + (r.aspect_ratio || '-') + '</td>' +
        '<td>' + videoCell(r.video_url) + '</td>' +
        '<td class="err" style="max-width:300px;word-break:break-all;">' + (r.error_msg || '-') + '</td>' +
        '<td class="prompt-cell">' + (r.prompt_preview || '-') + '</td>';
      wrapTbody.appendChild(tr);
    }}
  }});
}})();
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote HTML: {path}")
    return path


# ==================== Pytest ====================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_video_wrapper_all_models(request):
    """
    Video Wrapper 集成测试：所有模型 + 所有 chain 配置。
    输出 JSON + HTML 报告到 tests/tools/test_video_wrapper_output/。
    """
    dry_run = request.config.getoption("dry_run", default=False)
    out_dir = Path(__file__).resolve().parent / "test_video_wrapper_output"

    results = await _run_all_cases(dry_run=dry_run)

    # 输出报告
    _write_json_report(results, out_dir)
    html_path = _write_html_report(results, out_dir)

    # 打印概览
    ok = sum(1 for r in results if r.success)
    fail = sum(1 for r in results if not r.success)
    total_time = sum(r.duration_sec for r in results)
    print(f"\n{'='*60}")
    print(f"  Results: {ok} OK / {fail} FAIL / {len(results)} Total")
    print(f"  Total wall time: {total_time:.0f}s")
    print(f"  HTML Report: {html_path}")
    print(f"{'='*60}\n")

    for r in results:
        status = "OK" if r.success else "FAIL"
        output = r.video_url or r.error_msg
        print(f"  [{status}] {r.test_id}: model={r.model_used}, dur={r.duration_sec}s, output={output}")

    # 至少有一条记录
    assert len(results) >= 1
    # 至少一条成功（除 dry-run）
    if not dry_run:
        assert ok >= 1, "All video test cases failed!"
