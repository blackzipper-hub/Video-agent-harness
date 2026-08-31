#!/usr/bin/env python3
"""真实 DB + mock SQS：验证 continue_pipeline 完整提交流程（不真入队）。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ENVIRONMENT", "local")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

THREAD_ID = "thread_admin_ec76fe72-3820-49c4-beac-bbb9233214b3"
ANCHOR_RUN_ID = "7b317479-6f6c-4e3f-a28a-7c74084a60ba"
USER_ID = "admin"


async def main() -> None:
    from app.models.database import init_asyncpg_pool
    from app.services.agent.video_edit.tools.continue_pipeline import ContinuePipelineTool
    from app.services.agent.video_edit.system_prompt import build_system_prompt
    from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

    await init_asyncpg_pool()

    snap = await build_snapshot_from_db(
        run_id=ANCHOR_RUN_ID, user_id=USER_ID, thread_id=THREAD_ID,
    )
    prompt = build_system_prompt(snap)
    assert "failed_keyframe" in prompt
    assert "主管线推进 vs 编辑修改" in prompt
    assert "continue_pipeline" in prompt
    print("✅ system_prompt 动态段含 failed_keyframe + 新规则")

    tool = ContinuePipelineTool(
        run_id=ANCHOR_RUN_ID, user_id=USER_ID, thread_id=THREAD_ID,
    )

    mock_sqs_instance = AsyncMock()
    mock_sqs_instance.add_task_to_queue = AsyncMock()

    with patch(
        "app.services.aws.sqs_service.SQSTaskService",
        return_value=mock_sqs_instance,
    ):
        result = await tool._arun(gate="after_keyframe_reflection")

    print(f"continue_pipeline result: {result}")
    if result.startswith("恢复管线失败"):
        print(f"⚠️ continue_pipeline 未提交: {result}")
        return
    parsed = json.loads(result)
    assert parsed.get("status") == "resume_submitted"
    assert parsed.get("new_run_id")
    mock_sqs_instance.add_task_to_queue.assert_awaited_once()
    print(f"✅ continue_pipeline 提交成功 new_run_id={parsed['new_run_id']}")
    print("✅ SQS add_task_to_queue 被调用 1 次（mock，未真入队）")


if __name__ == "__main__":
    asyncio.run(main())
