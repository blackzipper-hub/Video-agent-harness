"""
I2I 三工具测试：镜头语言（景别 + 相机角度）老牌 vs Diffusion 对照。

- 使用 ENVIRONMENT=development 时加载 .env.development，ENVIRONMENT=production 时加载 .env.production。
- 每个类别跑「老牌示例句子」和「Diffusion 示例句子」两种 prompt，便于对比。
- 输出 test_i2i_results.json 与 test_i2i_report.html（HTML 内嵌 JSON，单文件双击即可打开）。

运行示例：
  ENVIRONMENT=development pytest tests/tools/test_i2i_camera_shot_tools.py -v -s
  ENVIRONMENT=production pytest tests/tools/test_i2i_camera_shot_tools.py -v -s
  pytest tests/tools/test_i2i_camera_shot_tools.py -v -s --limit 1 --dry-run
"""
import os
from pathlib import Path

# 最先加载 dev/prod 配置（与 test_all_songs.py 一致）
_environment = os.getenv("ENVIRONMENT", "development").lower()
if _environment == "production":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.production")
else:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env.development")

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import pytest

# 参考图（i2i 用）
REFERENCE_IMAGE_URLS = [
    "https://cdn-dev.newai.land/images/e1783886-f3b2-47fa-9a2e-8a791f581775.webp",
    "https://cdn-dev.newai.land/images/921b4637-71d7-4c13-9ccf-4e8b67880b1b.webp",
]

# 完整对照表：老牌/正规 prompt、老牌示例句子、Diffusion 有效 prompt、Diffusion 示例句子
CAMERA_SHOT_TABLE = [
    {
        "category": "远景 / Wide Shot",
        "legacy_prompt": "wide shot, long shot, establishing shot",
        "legacy_sentence": "A wide shot of an idol group on a grand concert stage, showing the full stage and surroundings.",
        "diffusion_prompt": "entire stage visible, characters small, lots of empty space",
        "diffusion_sentence": "Entire concert stage visible from above. The idol group appears small at the center with large empty stage space around them. Stage smoke, LED screens, volumetric lighting, highly saturated colors. Same characters as Image 2. Stage identical to Image 1.",
    },
    {
        "category": "中景 / Medium Shot",
        "legacy_prompt": "medium shot, MS, medium composition",
        "legacy_sentence": "Medium shot of the idol group on stage, showing them from waist up with some stage background.",
        "diffusion_prompt": "only central part of stage visible, characters medium-sized, partial background",
        "diffusion_sentence": "Only the central part of the concert stage is visible. The idol group medium-sized and clearly visible in the frame, with some LED background behind them. Stage smoke, volumetric lighting, cool–warm contrast, highly saturated colors. Same characters as Image 2. Stage identical to Image 1.",
    },
    {
        "category": "近景 / Close-up",
        "legacy_prompt": "close-up, CU, portrait shot",
        "legacy_sentence": "Close-up of the idol group, showing upper bodies and facial expressions.",
        "diffusion_prompt": "upper body fills most of the frame, little background",
        "diffusion_sentence": "Upper bodies of the idol group fill most of the frame. Very little stage visible behind them. Dramatic lighting and smoke around the characters. Same characters as Image 2.",
    },
    {
        "category": "特写 / Extreme Close-up",
        "legacy_prompt": "extreme close-up, ECU, macro portrait",
        "legacy_sentence": "Extreme close-up of the idol group's faces, capturing detailed expressions.",
        "diffusion_prompt": "face fills the frame, tight crop, only face visible",
        "diffusion_sentence": "Faces of the idol group fill the frame, only upper part of their bodies visible. Background slightly blurred with stage lights and LED patterns. Same characters as Image 2.",
    },
    {
        "category": "俯视 / Bird's-eye / High-angle",
        "legacy_prompt": "high-angle shot, top-down perspective, overhead camera",
        "legacy_sentence": "High-angle shot of the stage, looking down on the idol group from above, showing the full stage floor.",
        "diffusion_prompt": "viewed from above, looking down, stage floor visible, characters appear small",
        "diffusion_sentence": "Viewed from above, looking down at the stage floor. Only the central part of the stage visible. The idol group medium-sized and centered. Stage smoke, LED lights, volumetric lighting. Same characters as Image 2. Stage identical to Image 1.",
    },
    {
        "category": "平视 / Eye-level",
        "legacy_prompt": "eye-level shot, normal angle",
        "legacy_sentence": "Eye-level view of the idol group standing on stage, showing them at the same height as the camera.",
        "diffusion_prompt": "front view, straight-on view",
        "diffusion_sentence": "Front view of the idol group standing on the stage, the group medium-sized and centered. Stage smoke and LED lights visible behind them. Same characters as Image 2. Stage identical to Image 1.",
    },
    {
        "category": "仰拍 / Low-angle",
        "legacy_prompt": "low-angle shot, worm's-eye view",
        "legacy_sentence": "Low-angle view of the idol group, making them appear larger and more dominant, ceiling and lights visible above.",
        "diffusion_prompt": "viewed from below, looking up, characters appear tall, ceiling/sky visible",
        "diffusion_sentence": "Viewed from below, looking up at the idol group. They appear tall and dominant. Stage lights and LED screens above them. Same characters as Image 2. Stage identical to Image 1.",
    },
    {
        "category": "侧视 / Side view",
        "legacy_prompt": "side angle, profile shot",
        "legacy_sentence": "Side view of the idol group, showing their profile from left to right on stage.",
        "diffusion_prompt": "side profile view, bodies facing left/right",
        "diffusion_sentence": "Side profile view of the idol group on stage, positioned left-to-right. Stage lights and smoke visible behind them. Same characters as Image 2. Stage identical to Image 1.",
    },
    {
        "category": "背面 / Back view",
        "legacy_prompt": "back shot, rear view",
        "legacy_sentence": "Back view of the idol group, showing them from behind, with stage in front.",
        "diffusion_prompt": "viewed from behind, back facing camera",
        "diffusion_sentence": "Viewed from behind the idol group, showing their backs to the camera. LED screens and stage smoke in front. Same characters as Image 2. Stage identical to Image 1.",
    },
]


@dataclass
class ToolRunResult:
    tool_name: str
    category: str
    prompt_type: str  # "legacy" | "diffusion"
    success: bool
    image_url: str | None = None
    error_msg: str | None = None
    model_used: str | None = None
    duration_sec: float = 0.0


async def _run_nano_banana_flash_i2i(prompt: str, reference_image_urls: list[str]) -> ToolRunResult:
    from app.models.tool_enums import DefaultValues, ToolType
    from app.tools.image.nano_banana import _generate_image_with_nano_banana

    start = time.perf_counter()
    result = await _generate_image_with_nano_banana(
        prompt=prompt,
        reference_image_urls=reference_image_urls,
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
        model=ToolType.GEMINI_2_5_FLASH_IMAGE,
    )
    duration = time.perf_counter() - start
    return ToolRunResult(
        tool_name="nano_banana_flash_i2i",
        category="",
        prompt_type="",
        success=result.success,
        image_url=result.image_url if result.success else None,
        error_msg=result.error_msg if not result.success else None,
        model_used=result.model if result.success else None,
        duration_sec=round(duration, 2),
    )


async def _run_wrapper_i2i(prompt: str, reference_image_urls: list[str]) -> ToolRunResult:
    from app.models.tool_enums import DefaultValues
    from app.tools.context_schemas import ImageGenerationContext
    from app.tools.image.image_tool_wrapper import generate_image_with_fallback_i2i

    ctx = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
        reference_image_urls=reference_image_urls,
    )
    runtime = SimpleNamespace(context=ctx)
    start = time.perf_counter()
    # StructuredTool 需用 ainvoke，不能直接 await tool(...)
    result = await generate_image_with_fallback_i2i.ainvoke({
        "prompt": prompt,
        "reference_image_urls": reference_image_urls,
        "runtime": runtime,
    })
    duration = time.perf_counter() - start
    return ToolRunResult(
        tool_name="wrapper_i2i",
        category="",
        prompt_type="",
        success=result.success,
        image_url=result.image_url if result.success else None,
        error_msg=result.error_msg if not result.success else None,
        model_used=result.model if result.success else None,
        duration_sec=round(duration, 2),
    )


async def _run_seedream_i2i(prompt: str, reference_image_urls: list[str]) -> ToolRunResult:
    from app.models.tool_enums import DefaultValues
    from app.tools.context_schemas import ImageGenerationContext
    from app.tools.image.seedream import edit_image_with_wavespeed_seedream

    context = ImageGenerationContext(
        aspect_ratio=DefaultValues.IMAGE_ASPECT_RATIO,
        resolution=DefaultValues.IMAGE_RESOLUTION,
        reference_image_urls=reference_image_urls,
    )
    mock_runtime = SimpleNamespace(context=context)

    start = time.perf_counter()
    result = await edit_image_with_wavespeed_seedream.ainvoke(
        {"prompt": prompt, "images": reference_image_urls, "runtime": mock_runtime}
    )
    duration = time.perf_counter() - start
    return ToolRunResult(
        tool_name="seedream_i2i",
        category="",
        prompt_type="",
        success=result.success,
        image_url=result.image_url if result.success else None,
        error_msg=result.error_msg if not result.success else None,
        model_used=result.model if result.success else None,
        duration_sec=round(duration, 2),
    )


TOOL_RUNNERS = {
    "nano_banana_flash": _run_nano_banana_flash_i2i,
    "wrapper": _run_wrapper_i2i,
    "seedream": _run_seedream_i2i,
}


def _result_to_record(r: ToolRunResult, category: str, prompt_type: str, prompt_preview: str) -> dict:
    return {
        "tool": r.tool_name,
        "category": category,
        "prompt_type": prompt_type,
        "prompt_preview": prompt_preview[:150] + "..." if len(prompt_preview) > 150 else prompt_preview,
        "success": r.success,
        "image_url": r.image_url,
        "error_msg": r.error_msg,
        "model_used": r.model_used,
        "duration_sec": r.duration_sec,
    }


async def run_all(
    tools: list[str],
    limit: int | None,
    dry_run: bool,
) -> list[dict]:
    records = []
    cases = CAMERA_SHOT_TABLE[:limit] if limit else CAMERA_SHOT_TABLE

    for tc in cases:
        category = tc["category"]
        for prompt_type, prompt_key in [("legacy", "legacy_sentence"), ("diffusion", "diffusion_sentence")]:
            prompt = tc[prompt_key]
            for tool_key in tools:
                if tool_key not in TOOL_RUNNERS:
                    continue
                if dry_run:
                    records.append({
                        "tool": tool_key,
                        "category": category,
                        "prompt_type": prompt_type,
                        "prompt_preview": prompt[:150] + "..." if len(prompt) > 150 else prompt,
                        "success": None,
                        "image_url": None,
                        "error_msg": "(dry run)",
                        "model_used": None,
                        "duration_sec": 0,
                    })
                    continue
                runner = TOOL_RUNNERS[tool_key]
                res = await runner(prompt, REFERENCE_IMAGE_URLS)
                res.category = category
                res.prompt_type = prompt_type
                records.append(_result_to_record(res, category, prompt_type, prompt))
    return records


def _write_json(records: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "test_i2i_results.json"
    payload = {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "reference_image_urls": REFERENCE_IMAGE_URLS,
        "test_cases_count": len(CAMERA_SHOT_TABLE),
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "results": records,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote JSON: {path}")


def _write_html(records: list[dict], out_dir: Path) -> None:
    """生成单文件 HTML：将 JSON 内嵌到页面，双击即可打开，无需同目录或本地服务器。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "test_i2i_report.html"
    payload = {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "reference_image_urls": REFERENCE_IMAGE_URLS,
        "test_cases_count": len(CAMERA_SHOT_TABLE),
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "results": records,
    }
    json_str = json.dumps(payload, ensure_ascii=False)
    json_escaped = json_str.replace("<", "\\u003c")  # 避免 </script> 破坏 HTML

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>I2I 老牌 vs Diffusion 对照测试</title>
  <style>
    :root {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; }}
    body {{ max-width: 1600px; margin: 0 auto; padding: 1rem; }}
    h1 {{ font-size: 1.4rem; }}
    .meta {{ color: #888; font-size: 0.9rem; margin-bottom: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #333; padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; }}
    th {{ background: #16213e; }}
    tr:nth-child(even) {{ background: #16213e33; }}
    .cat {{ font-weight: 600; }}
    .thumb {{ max-width: 180px; max-height: 120px; object-fit: contain; border-radius: 4px; }}
    .ok {{ color: #6ee7b7; }}
    .err {{ color: #fca5a5; }}
    .prompt {{ font-size: 0.75rem; color: #aaa; max-width: 260px; }}
  </style>
</head>
<body>
  <h1>I2I 三工具：老牌示例句子 vs Diffusion 示例句子 对照</h1>
  <div class="meta" id="meta"></div>
  <table>
    <thead><tr id="theadRow"><th>类别 — prompt 类型</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <p class="meta" id="refs"></p>
  <script>window.I2I_RESULTS = {json_escaped};</script>
  <script>
(function(){{
  var data = window.I2I_RESULTS;
  var results = data.results || [];
  var metaEl = document.getElementById("meta");
  var theadRow = document.getElementById("theadRow");
  var tbody = document.getElementById("tbody");
  var refsEl = document.getElementById("refs");
  metaEl.textContent = "ENVIRONMENT: " + (data.environment || "") + " | 参考图: " + (data.reference_image_urls || []).length + " 张 | 结果条数: " + results.length + " | 生成时间: " + (data.ran_at || "");
  refsEl.textContent = "Reference: " + (data.reference_image_urls || []).join(" | ");
  var byKey = {{}};
  var toolsSet = {{}};
  var categoriesSet = {{}};
  results.forEach(function(r){{
    var k = r.category + "\\u0000" + r.prompt_type + "\\u0000" + r.tool;
    byKey[k] = r;
    toolsSet[r.tool] = true;
    categoriesSet[r.category] = true;
  }});
  var toolsOrder = Object.keys(toolsSet).sort();
  var categoriesOrder = Object.keys(categoriesSet).sort();
  var promptTypes = ["legacy", "diffusion"];
  toolsOrder.forEach(function(t){{ var th = document.createElement("th"); th.textContent = t; theadRow.appendChild(th); }});
  categoriesOrder.forEach(function(category){{
    promptTypes.forEach(function(promptType){{
      var label = category + " — " + promptType;
      var tr = document.createElement("tr");
      var tdCat = document.createElement("td"); tdCat.className = "cat"; tdCat.textContent = label; tr.appendChild(tdCat);
      toolsOrder.forEach(function(t){{
        var k = category + "\\u0000" + promptType + "\\u0000" + t;
        var r = byKey[k];
        var td = document.createElement("td");
        if(!r){{ td.textContent = "—"; tr.appendChild(td); return; }}
        td.className = r.success ? "ok" : "err";
        if(r.image_url){{ var img = document.createElement("img"); img.className = "thumb"; img.src = r.image_url; img.alt = ""; td.appendChild(img); td.appendChild(document.createElement("br")); }}
        if(!r.success && r.error_msg){{ var err = document.createElement("span"); err.className = "err"; err.textContent = r.error_msg; td.appendChild(err); td.appendChild(document.createElement("br")); }}
        var prompt = document.createElement("span"); prompt.className = "prompt"; prompt.textContent = r.prompt_preview || ""; td.appendChild(prompt);
        tr.appendChild(td);
      }});
      tbody.appendChild(tr);
    }});
  }});
}})();
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote HTML: {path} (数据内嵌，双击即可打开)")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_i2i_camera_shot_legacy_vs_diffusion(request):
    """
    对每个类别分别用「老牌示例句子」和「Diffusion 示例句子」跑三工具 I2I，输出 JSON + HTML 便于对比。
    """
    limit = request.config.getoption("limit", default=None)
    dry_run = request.config.getoption("dry_run", default=False)
    tools_opt = request.config.getoption("tools", default="nano_banana_flash,wrapper,seedream") or "nano_banana_flash,wrapper,seedream"
    tools = [t.strip() for t in tools_opt.split(",") if t.strip()]
    out_dir = Path(__file__).resolve().parent / "test_i2i_output"

    records = await run_all(tools=tools, limit=limit, dry_run=dry_run)

    if not dry_run:
        _write_json(records, out_dir)
        _write_html(records, out_dir)

    # 至少有一条记录（dry-run 或真实跑）
    assert len(records) >= 1
