#!/usr/bin/env python3
"""A/B: soft vs hard structured-output contracts for Short Drama planning.

1) Offline: validate real a8bcb099 cues against soft (current) vs hard schema.
2) Live LLM: same brief, SoftCueSchema vs HardCueSchema — measure machine fields.

Run:
  cd cuti-video-agent/services/agent
  python scripts/ab_soft_vs_hard_structured_cues.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from enum import Enum
from typing import List, Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

load_dotenv(".env.local")

BRIEF = (
    "制作一支30秒、16:9横屏、1080p的轻喜剧爽感短剧，讲述疲惫社畜女主意外穿进恋爱游戏，"
    "成为开局就要被处决的恶毒女配。开场办公室穿越，死亡倒计时，职场话术道歉合作，"
    "冷面王子问罪时递退婚方案，金句：恋爱可以不谈，项目不能烂尾。结尾隐藏事业线开启。"
)


# ---------- current-like (soft) ----------
class SoftCue(BaseModel):
    type: str = Field(default="broll")
    description: str
    timestamp_hint: Optional[str] = None


class SoftChapter(BaseModel):
    title: str
    description: str
    duration: float
    enhancement_cues: Optional[List[SoftCue]] = None


class SoftOutline(BaseModel):
    title: str
    theme: str
    chapters: List[SoftChapter]


# ---------- hard (proposed minimal) ----------
class CueType(str, Enum):
    broll = "broll"
    overlay = "overlay"
    animation = "animation"
    hard_event = "hard_event"


class HardCue(BaseModel):
    type: CueType
    description: str = Field(min_length=8)
    # required relative second inside chapter, e.g. "0-2" or "3"
    timestamp_hint: str = Field(min_length=1)

    @field_validator("description")
    @classmethod
    def no_pure_atmosphere(cls, v: str) -> str:
        soft = ("氛围很好", "很有感觉", "诗意", "治愈满满")
        if any(s in v for s in soft) and len(v) < 20:
            raise ValueError("atmosphere-only description")
        return v


class ShotSize(str, Enum):
    wide = "wide"
    medium = "medium"
    medium_close = "medium_close"
    close_up = "close_up"
    over_shoulder = "over_shoulder"
    insert = "insert"


class CamMove(str, Enum):
    static = "static"
    dolly_in = "dolly_in"
    dolly_out = "dolly_out"
    tracking = "tracking"
    handheld = "handheld"
    crash_zoom = "crash_zoom"
    tilt_up = "tilt_up"


class ShotLanguage(BaseModel):
    shot_size: ShotSize
    camera_movement: CamMove
    lens_mm: Literal[24, 35, 50, 85] = 50


class HardChapter(BaseModel):
    title: str
    description: str
    duration: float
    enhancement_cues: List[HardCue] = Field(min_length=2, max_length=4)
    # machine acting beats — not optional
    action_beats: List[str] = Field(min_length=2, max_length=5)
    shot_language: ShotLanguage
    hero_moment: bool = False

    @field_validator("action_beats")
    @classmethod
    def beats_must_be_contacty(cls, v: List[str]) -> List[str]:
        soft = ("缓缓", "慢慢走", "氛围", "展现气场", "站着")
        hard = ("拍", "按", "敲", "递", "推", "撞", "鞠躬", "炸", "碎", "抵", "抓", "打开", "扔", "撕")
        soft_n = sum(1 for b in v if any(s in b for s in soft) and not any(h in b for h in hard))
        if soft_n == len(v):
            raise ValueError("all action_beats are soft walk/atmosphere")
        return v


class HardOutline(BaseModel):
    title: str
    theme: str
    chapters: List[HardChapter] = Field(min_length=2, max_length=3)


def load_a8_cues():
    sql = r"""
    SELECT json_agg(json_build_object('order',"order",'cues',additional_data->'enhancement_cues'))
    FROM video_chapters WHERE thread_id LIKE '%a8bcb099%';
    """
    r = subprocess.run(
        ["psql", "postgresql://postgres:postgres@localhost:5432/storybook_dev", "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(r.stdout.strip() or "[]")


def score_outline(obj: dict, hard: bool) -> dict:
    chapters = obj.get("chapters") or []
    cues = []
    for ch in chapters:
        cues.extend(ch.get("enhancement_cues") or [])
    types = [c.get("type") for c in cues]
    ts = sum(1 for c in cues if c.get("timestamp_hint") not in (None, ""))
    has_sl = sum(1 for ch in chapters if ch.get("shot_language"))
    has_ab = sum(1 for ch in chapters if ch.get("action_beats"))
    ab_n = sum(len(ch.get("action_beats") or []) for ch in chapters)
    overlayish = sum(1 for t in types if t in ("overlay", "animation", "hard_event", "stat_card"))
    return {
        "chapters": len(chapters),
        "cues": len(cues),
        "timestamped": f"{ts}/{len(cues)}" if cues else "0/0",
        "unique_types": sorted(set(str(t) for t in types)),
        "non_broll_cues": overlayish,
        "shot_language_chapters": f"{has_sl}/{len(chapters)}" if chapters else "0/0",
        "action_beat_chapters": f"{has_ab}/{len(chapters)}" if chapters else "0/0",
        "action_beats_total": ab_n,
        "mode": "hard" if hard else "soft",
    }


def offline_validate_a8():
    print("\n=== OFFLINE: a8bcb099 cues vs Soft/Hard schema ===")
    rows = load_a8_cues()
    soft_ok = hard_ok = 0
    soft_err = hard_err = []
    for row in rows:
        for i, cue in enumerate(row.get("cues") or []):
            try:
                SoftCue.model_validate(cue)
                soft_ok += 1
            except ValidationError as e:
                soft_err.append(f"ch{row['order']}#{i}: {e.errors()[0]['msg']}")
            try:
                HardCue.model_validate(cue)
                hard_ok += 1
            except ValidationError as e:
                hard_err.append(
                    f"ch{row['order']}#{i} type={cue.get('type')} ts={cue.get('timestamp_hint')!r}: {e.errors()[0]['msg']}"
                )
    print(f"SoftCue accept: {soft_ok}  reject: {len(soft_err)}")
    print(f"HardCue accept: {hard_ok}  reject: {len(hard_err)}")
    for line in hard_err[:8]:
        print("  HARD reject:", line)
    return soft_ok, hard_ok, len(hard_err)


async def live_llm_ab():
    print("\n=== LIVE LLM A/B (same brief, soft vs hard schema) ===")
    from langchain_openai import ChatOpenAI

    # Prefer OpenAI-compatible key already in env (gateway may route gemini)
    model = os.getenv("AB_TEST_MODEL", "gpt-4.1-mini")
    llm = ChatOpenAI(model=model, temperature=0.4, timeout=120)

    system = (
        "你是短剧分镜策划。输出严格符合 schema。\n"
        "30秒轻喜剧短剧，2章（各约15秒）。\n"
        "enhancement_cues：每章2-4条可成像线索；禁止空氛围词。\n"
        "若 schema 有 action_beats：每条必须是可碰接触事件（拍/递/敲/鞠躬等），禁止只有缓缓走过。\n"
        "若 schema 有 shot_language：填枚举。\n"
        "若 schema 要求 timestamp_hint：填章内相对秒，如 0-3 / 8 / mid。\n"
        "type 可用 broll/overlay/animation/hard_event；UI倒计时、成就字效用 overlay 或 animation。\n"
        "用中文。"
    )
    human = f"用户需求：\n{BRIEF}\n\n请输出完整结构。"

    soft_llm = llm.with_structured_output(SoftOutline)
    hard_llm = llm.with_structured_output(HardOutline)

    soft = await soft_llm.ainvoke(
        [{"role": "system", "content": system}, {"role": "user", "content": human}]
    )
    hard = await hard_llm.ainvoke(
        [{"role": "system", "content": system}, {"role": "user", "content": human}]
    )

    soft_d = soft.model_dump()
    hard_d = hard.model_dump(mode="json")
    soft_s = score_outline(soft_d, False)
    hard_s = score_outline(hard_d, True)

    print("SOFT score:", json.dumps(soft_s, ensure_ascii=False))
    print("HARD score:", json.dumps(hard_s, ensure_ascii=False))
    print("\nSOFT sample cues:")
    for ch in soft_d["chapters"]:
        for c in ch.get("enhancement_cues") or []:
            print(" ", c)
    print("\nHARD sample chapter[0]:")
    ch0 = hard_d["chapters"][0]
    print(" cues:", ch0.get("enhancement_cues"))
    print(" action_beats:", ch0.get("action_beats"))
    print(" shot_language:", ch0.get("shot_language"))
    print(" hero_moment:", ch0.get("hero_moment"))

    out = {
        "model": model,
        "soft": soft_s,
        "hard": hard_s,
        "soft_outline": soft_d,
        "hard_outline": hard_d,
    }
    path = "scripts/_ab_soft_vs_hard_result.json"
    os.makedirs("scripts", exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}")
    return out


def triage_note():
    print(
        """
=== TRIAGE (necessary vs already covered) ===
ALREADY covered by prompt + seen in a8bcb099:
  - 5-aspect labels in scene description
  - contact events in prose (拍文件夹/鞠躬)
  - delivery/speaker on shots
  - prompt says cues 多为 broll  ← so "all-broll semantic retry" is WRONG / conflicts prompt

STILL soft / worth schema:
  - timestamp_hint optional → 0/7 timed in real thread
  - type is free str + prompt pushes broll → no overlay/animation machine slots
  - shot_language / action_beats not in LLM schema → 0/6 even though additional_data keys exist

OPTIONAL / lower priority:
  - soft-walk semantic gate (a8 already had contacts)
  - full OM narrative_role enums on day one
"""
    )


async def main():
    triage_note()
    offline_validate_a8()
    if not os.getenv("OPENAI_API_KEY"):
        print("No OPENAI_API_KEY; skip live LLM")
        return
    await live_llm_ab()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
