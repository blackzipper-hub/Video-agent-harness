"""
真实加载 .env.local + prompt 模板，验证 instruction 进入 LLM tool messages（不调用图生/扣费）。

conda: conda run -n cuti-video-local python -u tests/test_regenerate_instruction_prompts.py
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ["ENVIRONMENT"] = "local"
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from dotenv import load_dotenv

load_dotenv(os.path.join(_root, ".env.local"), override=False)

PASS = 0
FAIL = 0


def ok(cond: bool, msg: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"✅ {msg} {detail}")
    else:
        FAIL += 1
        print(f"❌ {msg} {detail}")


async def test_keyframe_tool_messages() -> None:
    from app.models.video_state import DetailedShot
    from app.services.agent.video.keyframe_generation_service import (
        _build_single_keyframe_tool_call_prompt,
    )

    shot = DetailedShot(
        shot_number=1,
        duration=3.0,
        scene_description="测试场景",
        character_ids=[],
    )
    msgs = await _build_single_keyframe_tool_call_prompt(
        shot,
        [],
        "BASE_PROMPT_FOR_RESOLVE",
        "test_image_tool",
        user_input="",
        user_regenerate_instruction="把天空改成晚霞",
    )
    text = "\n".join(getattr(m, "content", str(m)) for m in msgs)
    ok("用户本次修改要求" in text or "把天空改成晚霞" in text, "keyframe tool message 含 instruction 区块或正文")
    ok("BASE_PROMPT_FOR_RESOLVE" in text, "keyframe tool message 含基底 t2i")


async def test_video_tool_messages() -> None:
    from app.models.video_state import DetailedShot, KeyframeVersion
    from app.services.agent.video.video_generation_service import (
        _build_single_video_tool_call_prompt,
    )

    kf = KeyframeVersion(
        shot_number=1,
        t2i_prompt="x",
        keyframe_url="https://example.com/kf.png",
        provider="p",
    )
    shot = DetailedShot(shot_number=1, duration=3.0, scene_description="s", character_ids=[])
    msgs = await _build_single_video_tool_call_prompt(
        kf,
        shot,
        "BASE_I2V_PROMPT",
        "test_video_tool",
        user_option=None,
        end_image_url=None,
        user_input="",
        generation_mode=None,
        user_regenerate_instruction="镜头再慢一点",
    )
    text = "\n".join(getattr(m, "content", str(m)) for m in msgs)
    ok("用户本次修改要求" in text or "镜头再慢一点" in text, "video tool message 含 instruction")
    ok("BASE_I2V_PROMPT" in text, "video tool message 含基底 i2v")


async def test_character_template_invoke() -> None:
    from langchain_core.messages import SystemMessage, HumanMessage
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )

    system = skill_system_message(
        "character-regen-tool-director",
        lead="Follow character-regen-tool-director.",
    )
    human = facts_human_message({
        "tool_name": "t",
        "mode": "t2i",
        "character": {"name": "n"},
        "t2i_prompt": "BASE_CHAR",
        "has_user_regenerate_instruction": True,
        "user_regenerate_instruction": "穿蓝色连衣裙",
        "detected_language": "zh",
    })
    msgs = [SystemMessage(content=system), HumanMessage(content=human)]
    text = "\n".join(getattr(m, "content", str(m)) for m in msgs)
    ok("穿蓝色连衣裙" in text, "character skill message 含 user_regenerate_instruction")
    ok("BASE_CHAR" in text, "character skill message 含基底 t2i")
    ok("zh" in text, "character skill message 含语言")


async def test_pydantic_instruction_field() -> None:
    from app.api.agent.agent_router_endpoints import (
        KeyframeVersionRequest,
        VideoVersionRequest,
        CharacterVersionRequest,
    )
    from app.models.version_regenerate_strategy import (
        CharacterRegenerateStrategy,
        KeyframeRegenerateStrategy,
        VideoRegenerateStrategy,
        parse_keyframe_regenerate_strategy,
    )

    from pydantic import ValidationError

    k = KeyframeVersionRequest(uuid="u1", custom_prompt=None, instruction="改亮一点")
    ok(k.instruction == "改亮一点", "KeyframeVersionRequest.instruction")
    k0 = KeyframeVersionRequest(uuid="u0", instruction="a")
    ok(k0.regenerate_strategy == KeyframeRegenerateStrategy.PROMPT_REGENERATE, "KeyframeVersionRequest 默认 prompt_regenerate")
    k2 = KeyframeVersionRequest(uuid="u2", instruction="x", regenerate_strategy="instruction_merge_prompt")
    ok(k2.regenerate_strategy == KeyframeRegenerateStrategy.INSTRUCTION_MERGE_PROMPT, "KeyframeVersionRequest.regenerate_strategy")
    try:
        KeyframeVersionRequest(uuid="uf", instruction="x", regenerate_strategy="fused")  # type: ignore[arg-type]
        ok(False, "KeyframeVersionRequest 应拒绝 fused")
    except ValidationError:
        ok(True, "KeyframeVersionRequest 拒绝 fused")
    v = VideoVersionRequest(uuid="v1", instruction="慢推")
    ok(v.instruction == "慢推", "VideoVersionRequest.instruction")
    ok(v.regenerate_strategy == VideoRegenerateStrategy.PROMPT_REGENERATE, "VideoVersionRequest 默认 prompt_regenerate")

    try:
        VideoVersionRequest(uuid="v2", instruction="x", regenerate_strategy="instruction_edit_image")  # type: ignore[arg-type]
        ok(False, "VideoVersionRequest 应拒绝 instruction_edit_image")
    except ValidationError:
        ok(True, "VideoVersionRequest 拒绝 instruction_edit_image")
    c = CharacterVersionRequest(uuid="c1", instruction="微笑")
    ok(c.instruction == "微笑", "CharacterVersionRequest.instruction")
    ok(c.regenerate_strategy == CharacterRegenerateStrategy.PROMPT_REGENERATE, "CharacterVersionRequest 默认 prompt_regenerate")

    try:
        parse_keyframe_regenerate_strategy("not_a_valid_strategy_value")
        ok(False, "parse_keyframe_regenerate_strategy 应拒绝非法字面量")
    except ValueError:
        ok(True, "parse_keyframe_regenerate_strategy 拒绝非法值")


async def optional_db_pool_smoke() -> None:
    """若 DATABASE_URL 可用则初始化连接池（不跑再生）。"""
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if not db_url:
        print("(skip) 无 DATABASE_URL，跳过 DB smoke")
        return
    try:
        from app.models.database import init_asyncpg_pool, close_asyncpg_pool

        await init_asyncpg_pool()
        ok(True, "asyncpg pool 初始化成功")
        await close_asyncpg_pool()
    except Exception as e:
        ok(False, "asyncpg pool", str(e))


async def main() -> bool:
    await test_pydantic_instruction_field()
    await test_keyframe_tool_messages()
    await test_video_tool_messages()
    await test_character_template_invoke()
    await optional_db_pool_smoke()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
