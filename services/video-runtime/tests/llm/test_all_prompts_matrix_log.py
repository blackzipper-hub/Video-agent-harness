"""
遍历 PROMPTS_CONFIG 中**每一个** prompt：固定 2 模型 × 2 策略（仅对有 schema 的条目）。

- **有 schema**：gemini-2.5-flash / gpt-4.1-mini × ProviderStrategy / ToolStrategy（create_agent, tools=[]）
- **无 schema**：同上 2 模型 × **仅 chat**（llm.ainvoke，无 provider/tool 维度），记 mode=chat

不做业务断言；仅记录模板是否渲染成功、每次 API 调用是否抛错，以及结果摘要（截断）。
标准输出需加 **pytest -s** 才能实时看到 print。

环境：tests/llm/conftest.py 已关闭 LangSmith tracing。

运行（耗时长、调用量大）:
  cd Cuti-VideoAgent && conda activate cuti-video-local
  # 先抽样（按名称排序后的前 N 个）:
  PROMPT_MATRIX_LOG_LIMIT=5 poetry run pytest tests/llm/test_all_prompts_matrix_log.py::test_log_all_prompts_matrix -s -m integration
  # 全量（不设环境变量或设为空）:
  poetry run pytest tests/llm/test_all_prompts_matrix_log.py::test_log_all_prompts_matrix -s -m integration
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompts.prompt_config import PROMPTS_CONFIG, create_llm  # noqa: E402
from prompts.prompt_loader import load_local_mustache_template  # noqa: E402

_PROMPTS_DIR = ROOT / "prompts"

_GEMINI_MC: Dict[str, Any] = {
    "model": "gemini-2.5-flash",
    "temperature": 0.2,
    "timeout": 300,
    "max_output_tokens": 8192,
}
_GPT_MC: Dict[str, Any] = {
    "model": "gpt-4.1-mini",
    "temperature": 0.2,
    "timeout": 240,
    "max_tokens": 4096,
    "stream_usage": True,
}


def _mustache_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for m in re.finditer(r"\{\{([#^/]?)\s*([\w_.]+)\s*\}\}", text):
        _kind, name = m.group(1), m.group(2)
        if name.startswith("/"):
            continue
        keys.add(name.split(".")[0])
    for m in re.finditer(r"\{\{(IMAGE|IMAGES|AUDIO|VIDEO):(\w+)\}\}", text, re.I):
        keys.add(m.group(2))
    for m in re.finditer(r"\{\{\{\s*([\w_]+)\s*\}\}\}", text):
        keys.add(m.group(1))
    return keys


def _default_for_key(key: str) -> Any:
    k = key
    kl = k.lower()
    if k == "is_bridge":
        return False
    if k in ("has_characters", "has_character_ref_images", "has_reference_sheet"):
        return True
    if k in ("has_objects", "has_venues"):
        return False
    if k in ("batch_size", "character_ref_images_count", "reference_images_count"):
        return 1
    if k == "shot_number":
        return 27
    if k in ("duration", "target_duration"):
        return 5.0
    if "count" in kl and k not in ("reference_images_count", "character_ref_images_count"):
        return 1
    if "url" in kl or kl.endswith("_url") or "urls" in kl:
        if "video" in kl:
            return "https://cdn.cuti.land/videos/integration-test.mp4"
        return "https://cdn.cuti.land/images/integration-test.webp"
    if k in ("mode", "generation_mode"):
        return "I2I"
    if k in ("t2i_prompt", "i2v_prompt", "optimized_prompt", "motion_prompt", "prompt"):
        return "Integration test prompt for matrix log."
    if k in ("user_input", "dialogue", "sound_effects", "transition"):
        return ""
    if k in ("narration",):
        return "Integration test narration line."
    if k in ("story_outline", "outline", "context_info", "batch_info"):
        return (
            "<outline>Integration test: hero in city, morning, conflict with rival.</outline>"
        )
    if k in ("transcript", "audio_transcript", "lyrics"):
        return "Line one of test transcript. Line two."
    if k in ("style_guidance_for_visual", "hidden_style_description"):
        return "cinematic, soft light"
    if k in ("visual_element_references", "character_info", "character_names"):
        return "<refs><char name='Test'>description</char></refs>"
    if k in ("detected_language",):
        return "zh"
    if k in ("content_category",):
        return "general"
    if k in ("end_image_mode_text",):
        return ""
    if k in ("tool_name",):
        return "integration_test_tool"
    if k in ("keyframe_prompt_length_range", "video_prompt_length_range"):
        return "400-500"
    if k in ("bridge_indicator",):
        return "main"
    if k in ("shot_uuid",):
        return "00000000-0000-0000-0000-000000000001"
    if k in ("shot_type",):
        return "中景"
    if k in ("camera_position", "camera_angle", "subject_angle", "subject_pose"):
        return "未指定"
    if k in ("scene_description",):
        return "湖边晨光，远山"
    if k in ("camera_movement", "lighting", "visual_effects"):
        return "缓慢横移"
    if k in ("character_ref_images_list",):
        return str(["https://cdn.cuti.land/images/ref1.webp"])
    if k in ("words_text",):
        return "ID:0 | Word:'hello'\nID:1 | Word:'world'\nID:2 | Word:'test'"
    return "[integration-test]"


def _heavy_template_defaults() -> Dict[str, Any]:
    shot = {
        "shot_number": 1,
        "duration": 5.0,
        "scene_description": "test scene",
        "character_ids": ["c1"],
        "is_bridge": False,
        "shot_type": "中景",
        "camera_position": "侧",
        "camera_angle": "平视",
        "subject_angle": "正面",
        "subject_pose": "站立",
        "camera_movement": "静止",
        "lighting": "柔光",
        "visual_effects": "无",
        "dialogue": "",
        "narration": "",
        "sound_effects": "",
        "transition": "",
    }
    return {
        "shots": [shot, {**shot, "shot_number": 2}],
        "shots_batch": [shot],
        "characters": [{"name": "Hero", "description": "test character"}],
        "words": [{"text": "hello", "start": 0.0, "end": 0.5}, {"text": "world", "start": 0.5, "end": 1.0}],
        "conversation_history": "User: hi\nAssistant: hello",
    }


def _build_universal_template_data() -> Dict[str, Any]:
    keys: set[str] = set()
    for path in _PROMPTS_DIR.rglob("*.mustache"):
        try:
            keys |= _mustache_keys(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    data = {k: _default_for_key(k) for k in keys}
    data.update(_heavy_template_defaults())
    return data


_UNIVERSAL_DATA = _build_universal_template_data()


def _summarize_output(obj: Any, limit: int = 600) -> str:
    if obj is None:
        return "None"
    if hasattr(obj, "model_dump"):
        try:
            return json.dumps(obj.model_dump(), ensure_ascii=False, default=str)[:limit]
        except Exception:
            return repr(obj)[:limit]
    if hasattr(obj, "content"):
        c = getattr(obj, "content", "")
        if isinstance(c, list):
            return str(c)[:limit]
        return str(c)[:limit]
    return str(obj)[:limit]


async def _render_messages(local_template_stem: str) -> Tuple[bool, Optional[List[Any]], Optional[str]]:
    """local_template_stem: e.g. video/keyframe_generation/... without .mustache"""
    try:
        tpl = load_local_mustache_template(local_template_stem)
        msgs = (await tpl.ainvoke(_UNIVERSAL_DATA)).messages
        return True, msgs, None
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


async def _run_structured(
    messages: List[Any],
    schema: type[BaseModel],
    model_cfg: Dict[str, Any],
    strategy: str,
) -> Tuple[bool, str]:
    try:
        llm = create_llm(model_cfg)
        if strategy == "provider":
            rf: Any = ProviderStrategy(schema)
        else:
            rf = ToolStrategy(schema, handle_errors=True)
        agent = await asyncio.to_thread(
            create_agent,
            model=llm,
            tools=[],
            response_format=rf,
        )
        out = await agent.ainvoke({"messages": messages})
        sr = out.get("structured_response")
        if sr is None:
            return False, "structured_response is None"
        return True, _summarize_output(sr)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _run_chat(messages: List[Any], model_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        llm = create_llm(model_cfg)
        out = await llm.ainvoke(messages)
        return True, _summarize_output(out)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.prompt_matrix_log
@pytest.mark.asyncio
async def test_log_all_prompts_matrix(dev_env_loaded):
    """
    全量 PROMPTS_CONFIG × 2 模型 ×（有 schema 时 ×2 策略，无 schema 时 chat）。
    仅日志，不 assert 业务正确性。
    """
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY 未配置")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY 未配置")

    results: List[Dict[str, Any]] = []
    ordered = sorted(PROMPTS_CONFIG.items(), key=lambda x: x[0].value)
    limit_raw = os.environ.get("PROMPT_MATRIX_LOG_LIMIT", "").strip()
    if limit_raw:
        try:
            lim = int(limit_raw)
            if lim > 0:
                ordered = ordered[:lim]
        except ValueError:
            print(f"WARN: invalid PROMPT_MATRIX_LOG_LIMIT={limit_raw!r}, ignored", flush=True)

    print("\n========== ALL PROMPTS MATRIX LOG START ==========\n", flush=True)
    print(f"prompts_dir_keys_universal={len(_UNIVERSAL_DATA)}", flush=True)
    print(f"prompts_this_run={len(ordered)} (PROMPT_MATRIX_LOG_LIMIT={limit_raw or 'all'})", flush=True)

    for prompt_name, cfg in ordered:
        name = prompt_name.value
        rel_file = cfg.get("file") or ""
        stem = rel_file.replace(".mustache", "").strip()
        desc = cfg.get("description", "")
        schema = cfg.get("schema")
        row: Dict[str, Any] = {
            "prompt_name": name,
            "template_stem": stem,
            "description": desc,
            "schema": getattr(schema, "__name__", None) if schema else None,
            "render_ok": False,
            "render_error": None,
            "calls": [],
        }

        if not stem:
            row["render_error"] = "missing file in PROMPTS_CONFIG"
            results.append(row)
            print(f"[SKIP] {name}: no template stem", flush=True)
            continue

        ok, msgs, err = await _render_messages(stem)
        row["render_ok"] = ok
        row["render_error"] = err
        if not ok or not msgs:
            results.append(row)
            print(f"[RENDER_FAIL] {name}: {err}", flush=True)
            continue

        print(f"[RENDER_OK] {name} messages={len(msgs)}", flush=True)

        if schema is not None:
            for model_label, mc in (("gemini", _GEMINI_MC), ("gpt", _GPT_MC)):
                for strat in ("provider", "tool"):
                    tag = f"{name} | {model_label} | {strat}"
                    print(f"  -> {tag} ...", flush=True)
                    success, detail = await _run_structured(msgs, schema, mc, strat)
                    row["calls"].append(
                        {
                            "model": model_label,
                            "strategy": strat,
                            "ok": success,
                            "detail": detail,
                        }
                    )
                    print(f"     {'OK' if success else 'FAIL'} {detail[:200]}...", flush=True)
                    await asyncio.sleep(0.15)
        else:
            for model_label, mc in (("gemini", _GEMINI_MC), ("gpt", _GPT_MC)):
                tag = f"{name} | {model_label} | chat"
                print(f"  -> {tag} ...", flush=True)
                success, detail = await _run_chat(msgs, mc)
                row["calls"].append(
                    {
                        "model": model_label,
                        "strategy": "chat",
                        "ok": success,
                        "detail": detail,
                    }
                )
                print(f"     {'OK' if success else 'FAIL'} {detail[:200]}...", flush=True)
                await asyncio.sleep(0.15)

        results.append(row)

    # 汇总统计
    total_calls = sum(len(r["calls"]) for r in results)
    ok_calls = sum(1 for r in results for c in r["calls"] if c.get("ok"))
    render_ok_n = sum(1 for r in results if r.get("render_ok"))
    summary = {
        "prompts_total": len(results),
        "render_ok": render_ok_n,
        "render_fail": len(results) - render_ok_n,
        "api_calls_total": total_calls,
        "api_calls_ok": ok_calls,
        "api_calls_fail": total_calls - ok_calls,
    }
    print("\n========== SUMMARY ==========", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("\n========== FULL JSON (truncated per-call detail in memory ok) ==========", flush=True)
    # 控制单文件体积：detail 已在上面截断
    print(json.dumps(results, ensure_ascii=False, indent=2)[:500000], flush=True)
    if len(json.dumps(results)) > 500000:
        print("\n... [JSON 输出超过 500k 字符，已截断；需要完整请写文件自行改测试] ...", flush=True)

    print("\n========== ALL PROMPTS MATRIX LOG END ==========\n", flush=True)
    # 不失败 pytest：仅记录；若需「至少一次成功」可改为 assert
    assert isinstance(results, list)
