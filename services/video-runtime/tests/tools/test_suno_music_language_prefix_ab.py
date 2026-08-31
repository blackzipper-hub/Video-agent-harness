"""
Suno 音乐生成：模板内「目标语言」信号 A/B（真实 LLM + 真实 Suno）。

语言由 `detected_language` 传入 mustache（`has_detected_language` / `detected_language`），
由 LLM 自行组织「生成中文歌 / 英文歌」类表述写入 Suno prompt；**不在 Python 里拼接固定前缀**。

- 集成测试（`pytest.mark.integration`）：每种目标语言 ×「不传 detected_language / 传入语言码」× 每格若干次。
- 默认每格 1 次、间隔 15s（可用 `SUNO_AB_TRIALS`、`SUNO_AB_SLEEP_SEC` 覆盖）；无 `SUNO_API_KEY` 时 skip。

在仓库根目录直接跑（会自动 `load_dotenv` 加载 `.env.development` 与 `.env.local`，与 `scripts/suno_duration_grid_experiment.py` 一致）：

  conda run -n cuti-video-local pytest tests/tools/test_suno_music_language_prefix_ab.py -v -s

全量重复（例如每格 3 次）：

  SUNO_AB_TRIALS=3 SUNO_AB_SLEEP_SEC=3 conda run -n cuti-video-local pytest tests/tools/test_suno_music_language_prefix_ab.py -v -s
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from dotenv import load_dotenv

from app.services.agent.video.music_generation_service import generate_single_suno_music

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_repo_dotenv() -> None:
    """与 scripts/suno_auto_lyrics_llm_tier_smoke.py、suno_duration_grid_experiment.py 一致。"""
    load_dotenv(_REPO_ROOT / ".env.development", override=False)
    load_dotenv(_REPO_ROOT / ".env.local", override=False)


def _strip_bracket_tags(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", " ", text)


def lyrics_heuristic_matches_language(lyrics: Optional[str], expected: str) -> bool:
    """用字符占比粗判 Suno 返回的 lyrics 是否偏中文或英文（非 ASR，仅供实验报表）。"""
    if not lyrics or len(lyrics.strip()) < 5:
        return False
    body = _strip_bracket_tags(lyrics)
    cjk = sum(1 for c in body if "\u4e00" <= c <= "\u9fff")
    latin = sum(1 for c in body if "a" <= c.lower() <= "z")
    if expected == "zh":
        return cjk >= 10 and cjk >= max(1, latin) * 0.35
    if expected == "en":
        return latin >= 30 and cjk <= 14
    return False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_suno_auto_lyrics_language_prefix_ab_report() -> None:
    _load_repo_dotenv()
    if not os.getenv("SUNO_API_KEY"):
        pytest.skip("需要 SUNO_API_KEY（已尝试加载仓库 .env.development / .env.local）")

    scenarios: Dict[str, Dict[str, Any]] = {
        "zh": {
            "user_input": (
                "校园操场晨跑，阳光轻快，适合短视频的带人声歌曲，情绪积极。"
            ),
            "expected": "zh",
        },
        "en": {
            "user_input": (
                "Morning jog on a school track, upbeat and bright, short vocal song for a social clip."
            ),
            "expected": "en",
        },
    }

    trials = max(1, int(os.getenv("SUNO_AB_TRIALS", "1")))
    sleep_sec = max(0, int(os.getenv("SUNO_AB_SLEEP_SEC", "15")))
    target_duration = 45
    rows: List[Dict[str, Any]] = []

    for pass_lang_to_template in (False, True):
        for lang_key, spec in scenarios.items():
            for trial in range(trials):
                kwargs: Dict[str, Any] = {
                    "user_input": spec["user_input"],
                    "target_duration": target_duration,
                    "needs_lyrics": False,
                    "prioritize_duration": True,
                    "images": [],
                    "send_event_func": None,
                    "conversation_id": None,
                    "use_auto_lyrics": True,
                }
                if pass_lang_to_template:
                    kwargs["detected_language"] = lang_key

                _, mv = await generate_single_suno_music(**kwargs)
                lyrics: Optional[str] = None
                if mv.success and mv.additional_data and mv.additional_data.get("clips"):
                    lyrics = (mv.additional_data["clips"][0] or {}).get("lyrics")

                lang_ok = lyrics_heuristic_matches_language(lyrics, spec["expected"])
                rows.append(
                    {
                        "template_lang": pass_lang_to_template,
                        "lang_key": lang_key,
                        "trial": trial + 1,
                        "api_ok": bool(mv.success),
                        "lang_ok": lang_ok,
                        "lyrics_preview": (lyrics or "")[:120].replace("\n", " "),
                    }
                )
                if sleep_sec:
                    await asyncio.sleep(sleep_sec)

    def _rate() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for pass_lang in (False, True):
            for lang_key in scenarios:
                sub = [r for r in rows if r["template_lang"] is pass_lang and r["lang_key"] == lang_key]
                n = len(sub)
                api_n = sum(1 for r in sub if r["api_ok"])
                lang_n = sum(1 for r in sub if r["lang_ok"])
                out.append(
                    {
                        "template_lang_on": pass_lang,
                        "lang": lang_key,
                        "api_rate": api_n / n if n else 0.0,
                        "lang_match_rate": lang_n / n if n else 0.0,
                    }
                )
        return out

    summary = _rate()
    print(f"\n### Suno auto_lyrics：detected_language A/B（启发式判词，每格 n={trials}，间隔 {sleep_sec}s）\n")
    for s in summary:
        tl = "on" if s["template_lang_on"] else "off"
        print(
            f"  template_lang={tl:3} lang={s['lang']}  "
            f"API成功率={s['api_rate']:.2f}  歌词语言匹配率={s['lang_match_rate']:.2f}"
        )
    print("\n明细（前 120 字歌词）:")
    for r in rows:
        tlang = "on" if r["template_lang"] else "off"
        print(
            f"  tmpl_lang={tlang:3} {r['lang_key']} trial={r['trial']} "
            f"api={r['api_ok']} lang_ok={r['lang_ok']} | {r['lyrics_preview']!r}"
        )

    assert len(rows) == 2 * len(scenarios) * trials
    api_successes = sum(1 for r in rows if r["api_ok"])
    if api_successes == 0:
        pytest.skip(
            "本轮无 Suno 成功返回（常见：账户 rate limit、create 400、LLM 网络）。"
            "可设 SUNO_AB_TRIALS=1、SUNO_AB_SLEEP_SEC=60 后重试；模板见 video_music_generation.mustache 中 vocal_lyrics_target_language。"
        )
