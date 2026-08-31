"""
真实 LLM + 结构化输出：per-shot routing 模板（integration）。

需 `GOOGLE_API_KEY`（与仓库其它 `tests/llm/*_real.py` 一致）。

运行:
  cd Cuti-VideoAgent && pytest tests/llm/test_per_shot_generation_routing_structured_real.py -v -m integration
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.mark.integration
def test_per_shot_routing_structured_output_real(dev_env_loaded):
    """Mustache 全量占位 → Gemini 结构化解析为 `PerShotGenerationRoutingOutput`（真实 API）。"""
    try:
        import email_validator  # noqa: F401 — prompts → schemas 链需要
    except ImportError:
        pytest.skip("email-validator 未安装（pip install email-validator），无法加载 prompts")
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    async def _run():
        from prompts.prompt_config import create_llm
        from prompts.prompt_loader import load_local_mustache_template
        from app.models.user_options import get_tool_capabilities
        from app.models.user_options import ImageGenerationTool, UserOption, VideoGenerationTool
        from app.schemas.per_shot_routing import PerShotGenerationRoutingOutput, per_shot_routing_enum_lines_for_prompt

        uo = UserOption(
            image_generation_tool=ImageGenerationTool.AUTO,
            video_generation_tool=VideoGenerationTool.AUTO,
            lipsync_video_tool=VideoGenerationTool.AUTO,
        )
        caps = await get_tool_capabilities(
            image_tool=uo.image_generation_tool,
            video_tool=uo.video_generation_tool,
            lipsync_tool=uo.lipsync_video_tool,
        )
        shots_in = [
            {
                "shot_number": 1,
                "shot_type": "特写",
                "scene_description": "雨中脸部特写，泪水与雨水混合。",
                "visual_effects": "",
                "camera_movement": "",
                "dialogue": "",
                "is_bridge": False,
                "generation_mode": "lipsync",
            }
        ]
        el = per_shot_routing_enum_lines_for_prompt()
        tpl = load_local_mustache_template("video/per_shot_generation_routing/video_per_shot_generation_routing")
        pv = await tpl.ainvoke(
            {
                "detected_language": "zh",
                "shots_json": json.dumps(shots_in, ensure_ascii=False, indent=2),
                "user_image_tool": uo.image_generation_tool.value,
                "user_video_tool": uo.video_generation_tool.value,
                "user_lipsync_tool": uo.lipsync_video_tool.value,
                "user_capabilities_json": json.dumps(
                    {
                        "resolutions": [x["value"] for x in caps["resolutions"]],
                        "aspect_ratios": [x["value"] for x in caps["aspect_ratios"]],
                        "lipsync_video_tools": caps["lipsync_video_tools"],
                        "warnings": caps.get("warnings", []),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                **el,
            }
        )
        msgs = pv.messages
        assert msgs

        llm = create_llm(
            {
                "model": "gemini-2.5-flash",
                "temperature": 0.2,
                "timeout": 120,
                "max_output_tokens": 4096,
            }
        )
        structured = llm.with_structured_output(PerShotGenerationRoutingOutput)
        out = await structured.ainvoke(msgs)
        assert isinstance(out, PerShotGenerationRoutingOutput)
        assert len(out.shots) >= 1
        assert out.shots[0].shot_number == 1

    asyncio.run(_run())
