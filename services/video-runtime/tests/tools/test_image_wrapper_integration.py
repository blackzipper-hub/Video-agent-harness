"""
Image Wrapper 集成测试 — T2I + I2I，所有模型 + 所有 Wrapper chain 配置。

测试内容：
  1. Individual T2I: Seedream / Nano Banana Flash / Nano Banana Pro
  2. Individual I2I: Seedream / Nano Banana Flash / Nano Banana Pro
  3. Wrapper T2I:   NANO_BANANA / SEEDREAM / NANO_BANANA_PRO(default) 三种 chain
  4. Wrapper I2I:   NANO_BANANA / SEEDREAM / NANO_BANANA_PRO(default) 三种 chain
  共 12 个 case，全部覆盖 image_tool_wrapper.py 的 branch 代码。

运行示例：
  ENVIRONMENT=development pytest tests/tools/test_image_wrapper_integration.py -v -s
  ENVIRONMENT=development pytest tests/tools/test_image_wrapper_integration.py -v -s --dry-run
  ENVIRONMENT=development pytest tests/tools/test_image_wrapper_integration.py -v -s -k "wrapper"
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

REFERENCE_IMAGE_URLS = [
    "https://cdn-dev.newai.land/images/5bc522f7-26ab-4c7c-9a9b-31a4caa9c158.webp",
    "https://cdn-dev.newai.land/images/a2956350-132c-4b9a-bcb5-41a3484adab4.webp",
]

I2I_PROMPT = (
    "近景镜头中，一位角色 from image 1 remains completely unchanged, facial features, "
    "facial details, and accessories preserved 低头查看手机，屏幕泛着微光显示末班车误点，"
    "他的眉心微蹙，脸上流露出一丝疲惫和无奈。一只戴着墨镜的金毛小狗 from image 1 remains "
    "completely unchanged, facial features, facial details, and accessories preserved "
    "安静地坐在角色脚边，头微微上扬，清澈的眼睛透过墨镜向上望着角色，仿佛在无声地给予安慰。"
    "狭窄的东京夜巷在背景中延伸，几盏昏黄的灯光拉长了他们的影子，营造出都市夜幕下独特的疏离感。"
    "斜侧平视机位，平视角度，角色和小狗均为三分之二侧。侧逆光，暖调，高对比度，夜巷灯光与环境光交织。"
    "背景是来自 image 2 remains completely unchanged 的东京夜巷与街景，轻微胶片颗粒感。"
    "Warm yellow tones, high contrast lighting, film grain, Wong Kar-wai style。"
)

T2I_PROMPT = (
    "近景镜头中，一位年轻亚洲男子低头查看手机，屏幕泛着微光显示末班车误点，"
    "他的眉心微蹙，脸上流露出一丝疲惫和无奈。一只戴着墨镜的金毛小狗安静地坐在他脚边，"
    "头微微上扬，清澈的眼睛透过墨镜向上望着他，仿佛在无声地给予安慰。"
    "狭窄的东京夜巷在背景中延伸，几盏昏黄的灯光拉长了他们的影子，"
    "营造出都市夜幕下独特的疏离感。斜侧平视机位，平视角度，角色和小狗均为三分之二侧。"
    "侧逆光，暖调，高对比度，夜巷灯光与环境光交织。轻微胶片颗粒感。"
    "Warm yellow tones, high contrast lighting, film grain, Wong Kar-wai style."
)

# 并发控制
MAX_CONCURRENT = 3


# ==================== 结果记录 ====================


@dataclass
class TestCaseResult:
    """单个测试用例结果"""
    test_id: str
    test_type: str           # "individual" | "wrapper"
    mode: str                # "t2i" | "i2i"
    tool_name: str           # 模型/工具名称
    user_option: str         # wrapper 的 user_option, 如 "NANO_BANANA" / "N/A"
    chain_config: str        # chain 描述, 如 "pro → flash → seedream"
    success: bool = False
    image_url: Optional[str] = None
    error_msg: Optional[str] = None
    raw_error_msg: Optional[str] = None
    model_used: Optional[str] = None
    model_switched: Optional[bool] = None
    requested_model: Optional[str] = None
    actual_model: Optional[str] = None
    aspect_ratio: Optional[str] = None
    resolution: Optional[str] = None
    duration_sec: float = 0.0
    prompt_preview: str = ""
    reference_images: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


# ==================== Individual Tool Runners ====================


async def _run_individual_i2i_seedream(prompt: str, refs: List[str]) -> TestCaseResult:
    from app.models.tool_enums import DefaultValues
    from app.tools.context_schemas import ImageGenerationContext
    from app.tools.image.seedream import edit_image_with_wavespeed_seedream

    ctx = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
        reference_image_urls=refs,
    )
    runtime = SimpleNamespace(context=ctx)
    start = time.perf_counter()
    try:
        result = await edit_image_with_wavespeed_seedream.ainvoke(
            {"prompt": prompt, "images": refs, "runtime": runtime}
        )
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id="individual_i2i_seedream",
            test_type="individual", mode="i2i",
            tool_name="seedream_v4.5 (I2I)", user_option="N/A", chain_config="N/A",
            success=result.success, image_url=result.image_url,
            error_msg=result.error_msg, raw_error_msg=result.raw_error_msg,
            model_used=result.model, aspect_ratio=result.aspect_ratio,
            resolution=result.resolution, duration_sec=round(dur, 2),
            prompt_preview=prompt[:150], reference_images=refs,
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id="individual_i2i_seedream",
            test_type="individual", mode="i2i",
            tool_name="seedream_v4.5 (I2I)", user_option="N/A", chain_config="N/A",
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150], reference_images=refs,
        )


async def _run_individual_i2i_nano_banana(prompt: str, refs: List[str], model_label: str, tool_type) -> TestCaseResult:
    from app.tools.context_schemas import ImageGenerationContext
    from app.models.tool_enums import DefaultValues
    from app.tools.image.nano_banana import generate_image_with_nano_banana_i2i

    ctx = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
        reference_image_urls=refs,
        model=tool_type,
    )
    runtime = SimpleNamespace(context=ctx)
    tid = f"individual_i2i_{model_label.lower().replace(' ', '_')}"
    start = time.perf_counter()
    try:
        result = await generate_image_with_nano_banana_i2i.ainvoke(
            {"prompt": prompt, "reference_image_urls": refs, "runtime": runtime}
        )
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="individual", mode="i2i",
            tool_name=f"{model_label} (I2I)", user_option="N/A", chain_config="N/A",
            success=result.success, image_url=result.image_url,
            error_msg=result.error_msg, raw_error_msg=result.raw_error_msg,
            model_used=result.model, aspect_ratio=result.aspect_ratio,
            resolution=result.resolution, duration_sec=round(dur, 2),
            prompt_preview=prompt[:150], reference_images=refs,
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="individual", mode="i2i",
            tool_name=f"{model_label} (I2I)", user_option="N/A", chain_config="N/A",
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150], reference_images=refs,
        )


async def _run_individual_t2i_seedream(prompt: str) -> TestCaseResult:
    from app.models.tool_enums import DefaultValues
    from app.tools.context_schemas import ImageGenerationContext
    from app.tools.image.seedream import generate_image_with_wavespeed_seedream_t2i

    ctx = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
    )
    runtime = SimpleNamespace(context=ctx)
    start = time.perf_counter()
    try:
        result = await generate_image_with_wavespeed_seedream_t2i.ainvoke(
            {"prompt": prompt, "runtime": runtime}
        )
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id="individual_t2i_seedream",
            test_type="individual", mode="t2i",
            tool_name="seedream_v4.5 (T2I)", user_option="N/A", chain_config="N/A",
            success=result.success, image_url=result.image_url,
            error_msg=result.error_msg, raw_error_msg=result.raw_error_msg,
            model_used=result.model, aspect_ratio=result.aspect_ratio,
            resolution=result.resolution, duration_sec=round(dur, 2),
            prompt_preview=prompt[:150],
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id="individual_t2i_seedream",
            test_type="individual", mode="t2i",
            tool_name="seedream_v4.5 (T2I)", user_option="N/A", chain_config="N/A",
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150],
        )


async def _run_individual_t2i_nano_banana(prompt: str, model_label: str, tool_type) -> TestCaseResult:
    from app.tools.context_schemas import ImageGenerationContext
    from app.models.tool_enums import DefaultValues
    from app.tools.image.nano_banana import generate_image_with_nano_banana_t2i

    ctx = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
        model=tool_type,
    )
    runtime = SimpleNamespace(context=ctx)
    tid = f"individual_t2i_{model_label.lower().replace(' ', '_')}"
    start = time.perf_counter()
    try:
        result = await generate_image_with_nano_banana_t2i.ainvoke(
            {"prompt": prompt, "runtime": runtime}
        )
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="individual", mode="t2i",
            tool_name=f"{model_label} (T2I)", user_option="N/A", chain_config="N/A",
            success=result.success, image_url=result.image_url,
            error_msg=result.error_msg, raw_error_msg=result.raw_error_msg,
            model_used=result.model, aspect_ratio=result.aspect_ratio,
            resolution=result.resolution, duration_sec=round(dur, 2),
            prompt_preview=prompt[:150],
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="individual", mode="t2i",
            tool_name=f"{model_label} (T2I)", user_option="N/A", chain_config="N/A",
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150],
        )


# ==================== Wrapper Runners ====================


async def _run_wrapper_t2i(prompt: str, user_option_enum, option_label: str, chain_desc: str) -> TestCaseResult:
    from app.models.tool_enums import DefaultValues
    from app.tools.context_schemas import ImageGenerationContext
    from app.tools.image.image_tool_wrapper import create_image_wrapper_tools
    from app.models.tool_enums import ToolMode

    mock_option = SimpleNamespace(image_generation_tool=user_option_enum)
    tools_info_list = create_image_wrapper_tools(mode=ToolMode.T2I, user_option=mock_option)
    assert tools_info_list, f"No T2I tool created for option={option_label}"
    wrapper_tool = tools_info_list[0].tool

    ctx = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
    )
    runtime = SimpleNamespace(context=ctx)
    tid = f"wrapper_t2i_{option_label.lower()}"
    start = time.perf_counter()
    try:
        result = await wrapper_tool.ainvoke({"prompt": prompt, "runtime": runtime})
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="wrapper", mode="t2i",
            tool_name=f"Wrapper T2I ({option_label})",
            user_option=option_label, chain_config=chain_desc,
            success=result.success, image_url=result.image_url,
            error_msg=result.error_msg, raw_error_msg=result.raw_error_msg,
            model_used=result.model, model_switched=result.model_switched,
            requested_model=result.requested_model, actual_model=result.actual_model,
            aspect_ratio=result.aspect_ratio, resolution=result.resolution,
            duration_sec=round(dur, 2), prompt_preview=prompt[:150],
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="wrapper", mode="t2i",
            tool_name=f"Wrapper T2I ({option_label})",
            user_option=option_label, chain_config=chain_desc,
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150],
        )


async def _run_wrapper_i2i(prompt: str, refs: List[str], user_option_enum, option_label: str, chain_desc: str) -> TestCaseResult:
    from app.models.tool_enums import DefaultValues
    from app.tools.context_schemas import ImageGenerationContext
    from app.tools.image.image_tool_wrapper import create_image_wrapper_tools
    from app.models.tool_enums import ToolMode

    mock_option = SimpleNamespace(image_generation_tool=user_option_enum)
    tools_info_list = create_image_wrapper_tools(mode=ToolMode.I2I, user_option=mock_option)
    assert tools_info_list, f"No I2I tool created for option={option_label}"
    wrapper_tool = tools_info_list[0].tool

    ctx = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
        reference_image_urls=refs,
    )
    runtime = SimpleNamespace(context=ctx)
    tid = f"wrapper_i2i_{option_label.lower()}"
    start = time.perf_counter()
    try:
        result = await wrapper_tool.ainvoke({
            "prompt": prompt, "reference_image_urls": refs, "runtime": runtime,
        })
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="wrapper", mode="i2i",
            tool_name=f"Wrapper I2I ({option_label})",
            user_option=option_label, chain_config=chain_desc,
            success=result.success, image_url=result.image_url,
            error_msg=result.error_msg, raw_error_msg=result.raw_error_msg,
            model_used=result.model, model_switched=result.model_switched,
            requested_model=result.requested_model, actual_model=result.actual_model,
            aspect_ratio=result.aspect_ratio, resolution=result.resolution,
            duration_sec=round(dur, 2), prompt_preview=prompt[:150],
            reference_images=refs,
        )
    except Exception as e:
        dur = time.perf_counter() - start
        return TestCaseResult(
            test_id=tid, test_type="wrapper", mode="i2i",
            tool_name=f"Wrapper I2I ({option_label})",
            user_option=option_label, chain_config=chain_desc,
            success=False, error_msg=str(e), raw_error_msg=traceback.format_exc(),
            duration_sec=round(dur, 2), prompt_preview=prompt[:150],
            reference_images=refs,
        )


# ==================== 并发执行 ====================


async def _run_all_cases(dry_run: bool = False) -> List[TestCaseResult]:
    """构建所有 test case 并行执行，返回结果列表。"""
    from app.models.tool_enums import ToolType
    from app.models.user_options import ImageGenerationTool

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def guarded(coro):
        async with sem:
            return await coro

    # ---- 定义所有 case ----
    tasks = []

    # 1) Individual T2I
    tasks.append(("individual_t2i_seedream", lambda: _run_individual_t2i_seedream(T2I_PROMPT)))
    tasks.append(("individual_t2i_flash", lambda: _run_individual_t2i_nano_banana(T2I_PROMPT, "Gemini Flash", ToolType.GEMINI_2_5_FLASH_IMAGE)))
    tasks.append(("individual_t2i_pro", lambda: _run_individual_t2i_nano_banana(T2I_PROMPT, "Gemini Pro", ToolType.GEMINI_3_PRO_IMAGE_PREVIEW)))

    # 2) Individual I2I
    tasks.append(("individual_i2i_seedream", lambda: _run_individual_i2i_seedream(I2I_PROMPT, REFERENCE_IMAGE_URLS)))
    tasks.append(("individual_i2i_flash", lambda: _run_individual_i2i_nano_banana(I2I_PROMPT, REFERENCE_IMAGE_URLS, "Gemini Flash", ToolType.GEMINI_2_5_FLASH_IMAGE)))
    tasks.append(("individual_i2i_pro", lambda: _run_individual_i2i_nano_banana(I2I_PROMPT, REFERENCE_IMAGE_URLS, "Gemini Pro", ToolType.GEMINI_3_PRO_IMAGE_PREVIEW)))

    # 3) Wrapper T2I (all 3 user options → different chains)
    wrapper_t2i_configs = [
        (ImageGenerationTool.NANO_BANANA,     "NANO_BANANA",     "flash → seedream"),
        (ImageGenerationTool.SEEDREAM,         "SEEDREAM",        "seedream → flash → pro"),
        (ImageGenerationTool.NANO_BANANA_PRO,  "NANO_BANANA_PRO", "pro → flash → seedream"),
        (ImageGenerationTool.GPT_IMAGE_2,      "GPT_IMAGE_2",     "gpt2 → flash2 → pro → seedream"),
    ]
    for opt_enum, opt_label, chain_desc in wrapper_t2i_configs:
        tasks.append((f"wrapper_t2i_{opt_label}", lambda o=opt_enum, l=opt_label, c=chain_desc: _run_wrapper_t2i(T2I_PROMPT, o, l, c)))

    # 4) Wrapper I2I (all 3 user options → different chains)
    wrapper_i2i_configs = [
        (ImageGenerationTool.NANO_BANANA,     "NANO_BANANA",     "flash → seedream"),
        (ImageGenerationTool.SEEDREAM,         "SEEDREAM",        "seedream → flash → pro"),
        (ImageGenerationTool.NANO_BANANA_PRO,  "NANO_BANANA_PRO", "pro → flash → seedream"),
        (ImageGenerationTool.GPT_IMAGE_2,      "GPT_IMAGE_2",     "gpt2 → flash2 → pro → seedream"),
    ]
    for opt_enum, opt_label, chain_desc in wrapper_i2i_configs:
        tasks.append((f"wrapper_i2i_{opt_label}", lambda o=opt_enum, l=opt_label, c=chain_desc: _run_wrapper_i2i(I2I_PROMPT, REFERENCE_IMAGE_URLS, o, l, c)))

    if dry_run:
        return [
            TestCaseResult(
                test_id=tid, test_type="dry_run", mode="dry_run",
                tool_name=tid, user_option="N/A", chain_config="N/A",
                prompt_preview="(dry run - no API calls)",
            )
            for tid, _ in tasks
        ]

    # ---- 并发执行 ----
    print(f"\n{'='*60}")
    print(f"  Image Wrapper Integration Test")
    print(f"  Total cases: {len(tasks)} | Concurrency: {MAX_CONCURRENT}")
    print(f"{'='*60}\n")

    coros = [guarded(fn()) for _, fn in tasks]
    results = await asyncio.gather(*coros, return_exceptions=True)

    # 将异常转为 TestCaseResult
    final: List[TestCaseResult] = []
    for (tid, _), res in zip(tasks, results):
        if isinstance(res, Exception):
            final.append(TestCaseResult(
                test_id=tid, test_type="error", mode="error",
                tool_name=tid, user_option="N/A", chain_config="N/A",
                success=False, error_msg=str(res), raw_error_msg=traceback.format_exc(),
            ))
        else:
            final.append(res)

    return final


# ==================== 报告生成 ====================


def _write_json_report(results: List[TestCaseResult], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "test_image_wrapper_results.json"
    payload = {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "reference_image_urls": REFERENCE_IMAGE_URLS,
        "test_cases_count": len(results),
        "success_count": sum(1 for r in results if r.success),
        "failure_count": sum(1 for r in results if not r.success),
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "t2i_prompt": T2I_PROMPT[:200],
        "i2i_prompt": I2I_PROMPT[:200],
        "results": [asdict(r) for r in results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote JSON: {path}")
    return path


def _write_html_report(results: List[TestCaseResult], out_dir: Path) -> Path:
    """生成单文件 HTML 报告：所有数据内嵌，双击即可打开。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "test_image_wrapper_report.html"

    payload = {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "reference_image_urls": REFERENCE_IMAGE_URLS,
        "test_cases_count": len(results),
        "success_count": sum(1 for r in results if r.success),
        "failure_count": sum(1 for r in results if not r.success),
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "t2i_prompt": T2I_PROMPT,
        "i2i_prompt": I2I_PROMPT,
        "results": [asdict(r) for r in results],
    }
    json_str = json.dumps(payload, ensure_ascii=False)
    json_escaped = json_str.replace("<", "\\u003c")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Image Wrapper Integration Test Report</title>
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
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    th, td {{ border: 1px solid #334155; padding: 0.6rem 0.8rem; text-align: left; vertical-align: top; font-size: 0.85rem; }}
    th {{ background: #1e293b; color: #94a3b8; font-weight: 600; position: sticky; top: 0; z-index: 1; }}
    tr:nth-child(even) {{ background: #1e293b44; }}
    tr:hover {{ background: #1e293b88; }}
    .ok {{ color: #4ade80; }}
    .err {{ color: #f87171; }}
    .warn {{ color: #fbbf24; }}
    .thumb {{ max-width: 200px; max-height: 140px; object-fit: contain; border-radius: 6px; cursor: pointer; transition: transform 0.2s; }}
    .thumb:hover {{ transform: scale(1.5); z-index: 10; position: relative; }}
    .prompt-cell {{ font-size: 0.75rem; color: #94a3b8; max-width: 300px; word-break: break-all; }}
    .chain {{ font-size: 0.75rem; color: #818cf8; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }}
    .badge-t2i {{ background: #1d4ed8; color: #dbeafe; }}
    .badge-i2i {{ background: #7c3aed; color: #ede9fe; }}
    .badge-individual {{ background: #0f766e; color: #ccfbf1; }}
    .badge-wrapper {{ background: #b45309; color: #fef3c7; }}
    .ref-imgs {{ display: flex; gap: 0.5rem; margin: 0.5rem 0; }}
    .ref-imgs img {{ width: 80px; height: 60px; object-fit: cover; border-radius: 4px; border: 1px solid #334155; }}
    a {{ color: #60a5fa; }}
  </style>
</head>
<body>
  <h1>Image Wrapper Integration Test Report</h1>
  <div class="meta" id="meta"></div>
  <div class="summary" id="summary"></div>
  <h2>Reference Images</h2>
  <div class="ref-imgs" id="refImgs"></div>
  <h2>Individual Tool Results</h2>
  <table id="individualTable">
    <thead><tr><th>#</th><th>Mode</th><th>Tool</th><th>Success</th><th>Model Used</th><th>Aspect Ratio</th><th>Resolution</th><th>Duration</th><th>Output</th><th>Error</th><th>Prompt</th></tr></thead>
    <tbody></tbody>
  </table>
  <h2>Wrapper Results (Fallback Chain)</h2>
  <table id="wrapperTable">
    <thead><tr><th>#</th><th>Mode</th><th>User Option</th><th>Chain</th><th>Success</th><th>Model Used</th><th>Aspect Ratio</th><th>Resolution</th><th>Switched?</th><th>Duration</th><th>Output</th><th>Error</th><th>Prompt</th></tr></thead>
    <tbody></tbody>
  </table>

  <script>window.IMG_RESULTS = {json_escaped};</script>
  <script>
(function(){{
  var data = window.IMG_RESULTS;
  var results = data.results || [];

  // Meta
  var metaEl = document.getElementById("meta");
  metaEl.innerHTML = "Environment: <b>" + data.environment + "</b> | Ran: " + data.ran_at + "<br>" +
    "T2I Prompt: <i>" + (data.t2i_prompt || "").substring(0, 120) + "...</i><br>" +
    "I2I Prompt: <i>" + (data.i2i_prompt || "").substring(0, 120) + "...</i>";

  // Summary
  var sumEl = document.getElementById("summary");
  sumEl.innerHTML =
    '<div class="stat total"><div class="num">' + data.test_cases_count + '</div><div class="label">Total Cases</div></div>' +
    '<div class="stat ok"><div class="num">' + data.success_count + '</div><div class="label">Succeeded</div></div>' +
    '<div class="stat err"><div class="num">' + data.failure_count + '</div><div class="label">Failed</div></div>';

  // Ref images
  var refEl = document.getElementById("refImgs");
  (data.reference_image_urls || []).forEach(function(url, i) {{
    var img = document.createElement("img"); img.src = url; img.alt = "ref " + (i+1); img.title = url;
    var a = document.createElement("a"); a.href = url; a.target = "_blank"; a.appendChild(img);
    refEl.appendChild(a);
  }});

  // Tables
  var indTbody = document.querySelector("#individualTable tbody");
  var wrapTbody = document.querySelector("#wrapperTable tbody");
  var indIdx = 0, wrapIdx = 0;

  results.forEach(function(r) {{
    if (r.test_type === "individual") {{
      indIdx++;
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td>' + indIdx + '</td>' +
        '<td><span class="badge badge-' + r.mode + '">' + r.mode.toUpperCase() + '</span></td>' +
        '<td>' + r.tool_name + '</td>' +
        '<td class="' + (r.success ? 'ok' : 'err') + '">' + (r.success ? 'OK' : 'FAIL') + '</td>' +
        '<td>' + (r.model_used || '-') + '</td>' +
        '<td>' + (r.aspect_ratio || '-') + '</td>' +
        '<td>' + (r.resolution || '-') + '</td>' +
        '<td>' + r.duration_sec + 's</td>' +
        '<td>' + (r.image_url ? '<a href="' + r.image_url + '" target="_blank"><img class="thumb" src="' + r.image_url + '"></a>' : '-') + '</td>' +
        '<td class="err">' + (r.error_msg || '-') + '</td>' +
        '<td class="prompt-cell">' + (r.prompt_preview || '-') + '</td>';
      indTbody.appendChild(tr);
    }} else if (r.test_type === "wrapper") {{
      wrapIdx++;
      var tr = document.createElement("tr");
      var switchedHtml = '-';
      if (r.model_switched) switchedHtml = '<span class="warn">Yes: ' + (r.requested_model||'') + ' → ' + (r.actual_model||r.model_used||'') + '</span>';
      tr.innerHTML =
        '<td>' + wrapIdx + '</td>' +
        '<td><span class="badge badge-' + r.mode + '">' + r.mode.toUpperCase() + '</span></td>' +
        '<td><span class="badge badge-wrapper">' + r.user_option + '</span></td>' +
        '<td class="chain">' + (r.chain_config || '-') + '</td>' +
        '<td class="' + (r.success ? 'ok' : 'err') + '">' + (r.success ? 'OK' : 'FAIL') + '</td>' +
        '<td>' + (r.model_used || '-') + '</td>' +
        '<td>' + (r.aspect_ratio || '-') + '</td>' +
        '<td>' + (r.resolution || '-') + '</td>' +
        '<td>' + switchedHtml + '</td>' +
        '<td>' + r.duration_sec + 's</td>' +
        '<td>' + (r.image_url ? '<a href="' + r.image_url + '" target="_blank"><img class="thumb" src="' + r.image_url + '"></a>' : '-') + '</td>' +
        '<td class="err">' + (r.error_msg || '-') + '</td>' +
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
async def test_image_wrapper_all_models(request):
    """
    Image Wrapper 集成测试：所有模型 + 所有 chain 配置。
    输出 JSON + HTML 报告到 tests/tools/test_image_wrapper_output/。
    """
    dry_run = request.config.getoption("dry_run", default=False)
    out_dir = Path(__file__).resolve().parent / "test_image_wrapper_output"

    results = await _run_all_cases(dry_run=dry_run)

    # 输出报告
    _write_json_report(results, out_dir)
    html_path = _write_html_report(results, out_dir)

    # 打印概览
    ok = sum(1 for r in results if r.success)
    fail = sum(1 for r in results if not r.success)
    print(f"\n{'='*60}")
    print(f"  Results: {ok} OK / {fail} FAIL / {len(results)} Total")
    print(f"  HTML Report: {html_path}")
    print(f"{'='*60}\n")

    for r in results:
        status = "OK" if r.success else "FAIL"
        print(f"  [{status}] {r.test_id}: model={r.model_used}, dur={r.duration_sec}s, url={r.image_url or r.error_msg}")

    # 至少有一条记录
    assert len(results) >= 1
    # 至少一条成功（除 dry-run）
    if not dry_run:
        assert ok >= 1, "All test cases failed!"
