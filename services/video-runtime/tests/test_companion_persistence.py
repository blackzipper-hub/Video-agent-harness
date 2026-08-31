"""
Companion 消息持久化 + 计费测试 — 直接代码调用（不走 HTTP）。

测试内容：
1. create_conversation_run (companion_chat)
2. add_message_to_conversation (user input)
3. add_message_to_conversation (ai response)
4. update_conversation_run_cost
5. set_billing_pending
6. 验证 DB 中记录正确

用法：
  cd /home/songsong/local/Cuti-VideoAgent
  ENVIRONMENT=local conda run -n cuti-video-local python tests/test_companion_persistence.py <thread_id>
"""
import asyncio
import os
import sys
import uuid as uuid_lib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "local")


async def run_test(thread_id: str):
    from app.models.database import init_asyncpg_pool
    await init_asyncpg_pool()

    from app.crud.conversation import (
        async_get_conversation_by_thread_id,
        async_create_conversation_run,
        async_add_message_to_conversation,
        async_update_conversation_run_cost,
        async_update_conversation_run_status,
        async_set_conversation_run_billing_pending_if_not_completed,
        async_get_conversation_run_by_run_id,
        async_get_conversation_messages,
    )

    print(f"Thread ID: {thread_id}")

    # 1. 查找对话
    print("\n--- Step 1: Find conversation by thread_id ---")
    conversation = await async_get_conversation_by_thread_id(thread_id)
    if not conversation:
        print(f"  ❌ No conversation found for thread_id={thread_id}")
        return False
    print(f"  ✅ Found conversation: id={conversation.id}, uuid={conversation.uuid}")

    # 2. 创建 companion_chat run
    companion_run_id = str(uuid_lib.uuid4())
    print(f"\n--- Step 2: Create companion_chat run (run_id={companion_run_id}) ---")
    await async_create_conversation_run(
        conversation_id=conversation.id,
        thread_id=thread_id,
        run_id=companion_run_id,
        user_id=conversation.user_id,
        agent_type="video",
        run_type="companion_chat",
        status="running",
        conversation_uuid=conversation.uuid,
        user_input="[test] 换个绿色衣服",
    )
    run = await async_get_conversation_run_by_run_id(companion_run_id)
    assert run is not None, "conversation_run not created"
    print(f"  ✅ Created conversation_run: run_type={run.run_type}, status={run.status}")

    # 3. 写用户消息
    print("\n--- Step 3: Write user message ---")
    await async_add_message_to_conversation(
        conversation_id=conversation.id,
        role="human",
        content="[test] 换个绿色衣服",
        event_type="user_input",
        event_data={"run_id": companion_run_id, "source": "companion"},
        run_id=companion_run_id,
        conversation_uuid=conversation.uuid,
    )
    print("  ✅ User message written")

    # 4. 写 AI 消息
    print("\n--- Step 4: Write AI response ---")
    await async_add_message_to_conversation(
        conversation_id=conversation.id,
        role="ai",
        content="[test] 好的，我已经为角色重新生成了绿色衣服的版本。",
        event_type="video_edit_response",
        event_data={
            "run_id": companion_run_id,
            "source": "companion",
            "tools_used": ["get_project_status", "regenerate_characters"],
        },
        run_id=companion_run_id,
        conversation_uuid=conversation.uuid,
    )
    print("  ✅ AI response written")

    # 5. 更新成本
    print("\n--- Step 5: Update cost ---")
    await async_update_conversation_run_cost(companion_run_id, 0.0012)
    print("  ✅ Cost updated to 0.0012")

    # 6. 完成 run + billing pending
    print("\n--- Step 6: Complete run + billing pending ---")
    await async_update_conversation_run_status(
        companion_run_id, "completed", completed_at=datetime.utcnow()
    )
    await async_set_conversation_run_billing_pending_if_not_completed(companion_run_id)
    print("  ✅ Run completed, billing status set to pending")

    # 7. 验证
    print("\n--- Step 7: Verify ---")
    run_after = await async_get_conversation_run_by_run_id(companion_run_id)
    print(f"  run_type: {run_after.run_type}")
    print(f"  status: {run_after.status}")
    print(f"  cost: {run_after.cost}")
    print(f"  billing_status: {run_after.billing_status}")

    messages = await async_get_conversation_messages(conversation.id)
    companion_msgs = [
        m for m in messages
        if m.get("run_id") == companion_run_id
    ]
    print(f"  companion messages count: {len(companion_msgs)}")
    for m in companion_msgs:
        print(f"    - role={m.get('role')}, event_type={m.get('event_type')}, content={str(m.get('content', ''))[:60]}")

    assert run_after.run_type == "companion_chat"
    assert run_after.status == "completed"
    assert len(companion_msgs) == 2

    print("\n🎉 Companion persistence test passed!")
    return True


def main():
    thread_id = sys.argv[1] if len(sys.argv) >= 2 else os.getenv("TEST_THREAD_ID", "")
    if not thread_id:
        thread_id = "2a6aa84c-f696-46b7-afce-37d12ebde9ad"
        print(f"⚠️  Using default thread_id: {thread_id}")

    try:
        result = asyncio.run(run_test(thread_id))
        if not result:
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
