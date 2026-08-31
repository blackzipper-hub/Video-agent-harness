#!/usr/bin/env python3
"""手动真实 DB 验证 pending_gate + snapshot 渲染 + regenerate_videos 引导文案。"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("ENVIRONMENT", "local")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


THREAD_ID = "thread_admin_ec76fe72-3820-49c4-beac-bbb9233214b3"
ANCHOR_RUN_ID = "7b317479-6f6c-4e3f-a28a-7c74084a60ba"
USER_ID = "admin"


async def main() -> None:
    from app.models.database import init_asyncpg_pool
    from app.services.agent.video_edit.snapshot_builder import (
        _resolve_pending_gate,
        build_snapshot_from_db,
    )
    from app.services.agent.video_edit.snapshot_renderer import render_snapshot_for_llm
    from app.services.agent.video_edit.tools.continue_pipeline import ContinuePipelineTool
    from app.services.agent.video_edit.tools.regenerate_videos import RegenerateVideosTool

    await init_asyncpg_pool()

    print("=" * 60)
    print("1. _resolve_pending_gate (真实 thread)")
    print("=" * 60)
    gate = await _resolve_pending_gate(THREAD_ID)
    print(f"  thread_id={THREAD_ID}")
    print(f"  pending_gate={gate!r}")
    assert gate == "failed_keyframe", f"expected failed_keyframe, got {gate!r}"
    print("  ✅ pending_gate 正确")

    print("\n" + "=" * 60)
    print("2. build_snapshot_from_db + render (真实 thread)")
    print("=" * 60)
    snap = await build_snapshot_from_db(
        run_id=ANCHOR_RUN_ID,
        user_id=USER_ID,
        thread_id=THREAD_ID,
    )
    print(f"  phase={snap.get('phase')}")
    print(f"  task_status={snap.get('task_status')}")
    print(f"  pending_gate={snap.get('pending_gate')!r}")
    print(f"  videos.status={snap.get('videos', {}).get('status')}")
    print(f"  keyframes.status={snap.get('keyframes', {}).get('status')}")
    assert snap.get("pending_gate") == "failed_keyframe"
    assert snap.get("videos", {}).get("status") == "not_started"
    print("  ✅ snapshot pending_gate + videos 未开始")

    rendered = render_snapshot_for_llm(snap)
    print("\n--- 渲染给 LLM 的快照片段 ---")
    for line in rendered.splitlines():
        if "⏸️" in line or "视频" in line or "关键帧" in line or "任务状态" in line:
            print(f"  {line}")
    assert "阶段失败暂停" in rendered
    assert "failed_keyframe" in rendered
    print("  ✅ 渲染含失败暂停提示")

    print("\n" + "=" * 60)
    print("3. regenerate_videos 无记录时的引导文案")
    print("=" * 60)
    reg_tool = RegenerateVideosTool(
        run_id=ANCHOR_RUN_ID,
        user_id=USER_ID,
        thread_id=THREAD_ID,
    )
    reg_msg = await reg_tool._arun(shot_numbers=[1, 2, 3, 4, 5, 6, 7, 8], instruction="")
    print(f"  {reg_msg[:200]}...")
    assert "continue_pipeline" in reg_msg
    assert "after_keyframe_reflection" in reg_msg
    print("  ✅ regenerate_videos 正确引导到 continue_pipeline")

    print("\n" + "=" * 60)
    print("4. continue_pipeline 能否找到 interrupt（不真实提交 SQS）")
    print("=" * 60)
    from app.services.agent.video_edit.tools.continue_pipeline import _find_latest_interrupt

    msg_id, run_id = await _find_latest_interrupt(THREAD_ID)
    print(f"  interrupt_msgid={msg_id}")
    print(f"  interrupt_run_id={run_id}")
    assert msg_id is not None, "应找到未 continued 的 interrupt"
    assert run_id == ANCHOR_RUN_ID or run_id, "应有 run_id"
    print("  ✅ 能找到待恢复 interrupt")

    print("\n" + "=" * 60)
    print("全部真实 DB 检查通过")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
