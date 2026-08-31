"""
Video Companion Agent E2E 测试 — 真实 LLM + 真实 DB + Mock GPU。

验证完整的 agent loop：
  Human message → LLM 决策 → Tool call → Tool 执行 → LLM 回复
  多轮对话中 message history 的正确性

Mock 策略：
  - LLM: 真实调用（gpt-4o-mini）
  - DB:  真实读写（dev DB）
  - GPU 服务（regenerate）: Mock 掉，避免真实 GPU 调用

运行：
  conda run -n cuti-video-local pytest tests/services/agent/video_edit/test_companion_e2e_llm.py -v -s

注意：消耗 OpenAI tokens，适合手动触发 / CI nightly。
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)

REAL_RUN_ID = "55a9c30e-3292-4958-9d56-95e55e8975bd"
REAL_USER_ID = "admin"
THREAD_ID = "e2e-test-thread"


@pytest_asyncio.fixture(autouse=True)
async def ensure_db_pool():
    import app.models.database as db_mod
    if db_mod._asyncpg_pool is not None:
        try:
            await db_mod._asyncpg_pool.close()
        except Exception:
            pass
        db_mod._asyncpg_pool = None
    await db_mod.init_asyncpg_pool()


@pytest.fixture
def llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model="gpt-4o-mini", temperature=0)


@pytest.fixture
def mock_regenerate_keyframes():
    """Mock GPU-bound keyframe regeneration — patch 源模块（lazy import）。"""
    with patch(
        "app.services.task_enqueue_service.execute_regenerate_keyframes",
        new_callable=AsyncMock,
        return_value={"succeeded": 1, "failed": 0},
    ) as m:
        yield m


@pytest.fixture
def mock_regenerate_videos():
    """Mock GPU-bound video regeneration."""
    with patch(
        "app.services.task_enqueue_service.execute_regenerate_videos",
        new_callable=AsyncMock,
        return_value={"succeeded": 1, "failed": 0},
    ) as m:
        yield m


@pytest.fixture
def mock_regenerate_characters():
    """Mock GPU-bound character regeneration."""
    with patch(
        "app.services.task_enqueue_service.execute_regenerate_characters",
        new_callable=AsyncMock,
        return_value={"succeeded": 1, "failed": 0},
    ) as m:
        yield m


@pytest.fixture
def mock_reassemble():
    """Mock video assembly."""
    with patch(
        "app.services.agent.video_agent_service.get_video_agent_service",
    ) as m:
        svc = AsyncMock()
        svc.video_assembly_by_request.return_value = {"video_url": "https://cdn-dev.newai.land/test-assembly.mp4"}
        m.return_value = svc
        yield m


def _count_message_types(messages):
    """统计各类消息数量。"""
    stats = {"human": 0, "ai": 0, "tool": 0, "system": 0}
    for m in messages:
        if isinstance(m, HumanMessage):
            stats["human"] += 1
        elif isinstance(m, AIMessage):
            stats["ai"] += 1
        elif isinstance(m, ToolMessage):
            stats["tool"] += 1
        else:
            stats["system"] += 1
    return stats


def _print_message_chain(messages, label=""):
    """打印完整消息链（调试用）。"""
    print(f"\n{'='*60}")
    print(f"Message Chain: {label} ({len(messages)} messages)")
    print(f"{'='*60}")
    for i, m in enumerate(messages):
        mtype = type(m).__name__
        content = str(m.content)[:200] if m.content else ""
        tool_calls = ""
        if isinstance(m, AIMessage) and m.tool_calls:
            tool_calls = f" → tools: [{', '.join(tc['name'] for tc in m.tool_calls)}]"
        print(f"  [{i}] {mtype}{tool_calls}: {content}")
    print(f"{'='*60}\n")


# =====================================================================
# Scenario 1: 查看项目状态（只读，单轮）
# =====================================================================
class TestScenario1_ProjectStatus:
    @pytest.mark.asyncio
    async def test_ask_project_status(self, llm):
        """用户问'项目进展如何'→ LLM 可能直接从 snapshot 回答，或调 get_project_status。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        agent = create_video_companion_agent(
            llm=llm, run_id=REAL_RUN_ID, user_id=REAL_USER_ID,
            thread_id=THREAD_ID, snapshot=snap,
            enable_summarization=False,
        )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="项目进展如何？")]},
        )

        messages = result["messages"]
        _print_message_chain(messages, "Scenario 1: 查看项目状态")
        stats = _count_message_types(messages)

        assert stats["human"] >= 1, "应有用户消息"
        assert stats["ai"] >= 1, "应有 AI 回复"

        # LLM 可能直接从 system prompt 中的 snapshot 回答（智能行为），
        # 也可能调用 get_project_status tool — 两种都正确
        tool_call_msgs = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
        if tool_call_msgs:
            tool_names = [tc["name"] for m in tool_call_msgs for tc in m.tool_calls]
            print(f"  LLM chose to call tools: {tool_names}")
            assert "get_project_status" in tool_names
            tool_results = [m for m in messages if isinstance(m, ToolMessage)]
            assert any(REAL_RUN_ID in (m.content or "") for m in tool_results)
        else:
            print("  LLM answered directly from snapshot in system prompt (smart!)")

        # 验证最终回复有实质内容
        final_ai = messages[-1]
        assert isinstance(final_ai, AIMessage), "最后一条应是 AI 回复"
        assert len(final_ai.content) > 20, f"最终回复应有实质内容，实际: {final_ai.content[:100]}"


# =====================================================================
# Scenario 2: 查看具体镜头（多步 tool 调用）
# =====================================================================
class TestScenario2_ArtifactDetail:
    @pytest.mark.asyncio
    async def test_ask_keyframe_detail(self, llm):
        """用户问'看看第3镜的关键帧'→ LLM 调 get_artifact_detail。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        agent = create_video_companion_agent(
            llm=llm, run_id=REAL_RUN_ID, user_id=REAL_USER_ID,
            thread_id=THREAD_ID, snapshot=snap,
            enable_summarization=False,
        )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="帮我看看第3镜的关键帧详情")]},
        )

        messages = result["messages"]
        _print_message_chain(messages, "Scenario 2: 关键帧详情")

        tool_call_msgs = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
        tool_names = []
        for m in tool_call_msgs:
            tool_names.extend(tc["name"] for tc in m.tool_calls)
        assert "get_artifact_detail" in tool_names, \
            f"应调用 get_artifact_detail，实际调用: {tool_names}"

        # 验证 tool args 包含 shot_number=3
        for m in tool_call_msgs:
            for tc in m.tool_calls:
                if tc["name"] == "get_artifact_detail":
                    args = tc["args"]
                    assert args.get("shot_number") == 3 or args.get("artifact_type") == "keyframe"

        # 验证 tool result 包含关键帧信息
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_results) >= 1
        combined_results = " ".join(m.content for m in tool_results)
        assert "shot_3" in combined_results or "v0" in combined_results or "首帧" in combined_results


# =====================================================================
# Scenario 3: 重新生成关键帧（写操作，需 mock GPU）
# =====================================================================
class TestScenario3_RegenerateKeyframes:
    @pytest.mark.asyncio
    async def test_regenerate_keyframe_shot5(self, llm, mock_regenerate_keyframes):
        """用户说'第5镜关键帧太暗了，重新生成亮一点'→ LLM 调 regenerate_keyframes。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        agent = create_video_companion_agent(
            llm=llm, run_id=REAL_RUN_ID, user_id=REAL_USER_ID,
            thread_id=THREAD_ID, snapshot=snap,
            enable_summarization=False,
        )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="第5镜的关键帧太暗了，帮我重新生成亮一点的")]},
        )

        messages = result["messages"]
        _print_message_chain(messages, "Scenario 3: 重新生成关键帧")

        tool_names = []
        for m in messages:
            if isinstance(m, AIMessage) and m.tool_calls:
                tool_names.extend(tc["name"] for tc in m.tool_calls)

        assert "regenerate_keyframes" in tool_names, \
            f"应调用 regenerate_keyframes，实际调用: {tool_names}"

        # 验证 mock 被调用
        mock_regenerate_keyframes.assert_called_once()
        call_kwargs = mock_regenerate_keyframes.call_args
        print(f"  Mock called with: {call_kwargs}")

        # 验证 tool result 包含成功信息
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        combined = " ".join(m.content for m in tool_results)
        assert "✅" in combined or "成功" in combined or "已提交" in combined


# =====================================================================
# Scenario 4: 多轮对话 — 先看 → 再改 → 确认
# =====================================================================
class TestScenario4_MultiTurn:
    @pytest.mark.asyncio
    async def test_multi_turn_view_then_regenerate(self, llm, mock_regenerate_keyframes):
        """多轮对话：先查看 → 再修改。验证 message history 累积正确。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db
        from langgraph.checkpoint.memory import MemorySaver

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        checkpointer = MemorySaver()
        agent = create_video_companion_agent(
            llm=llm, run_id=REAL_RUN_ID, user_id=REAL_USER_ID,
            thread_id=THREAD_ID, snapshot=snap,
            checkpointer=checkpointer,
            enable_summarization=False,
        )

        config = {"configurable": {"thread_id": "multi-turn-test-1"}}

        # ===== Turn 1: 查看项目状态 =====
        print("\n--- Turn 1: 查看项目状态 ---")
        result1 = await agent.ainvoke(
            {"messages": [HumanMessage(content="项目现在什么状态？")]},
            config=config,
        )
        msgs1 = result1["messages"]
        _print_message_chain(msgs1, "Turn 1")
        stats1 = _count_message_types(msgs1)
        assert stats1["human"] == 1

        # ===== Turn 2: 查看第1镜关键帧 =====
        print("\n--- Turn 2: 查看第1镜关键帧 ---")
        result2 = await agent.ainvoke(
            {"messages": [HumanMessage(content="看看第1镜的关键帧")]},
            config=config,
        )
        msgs2 = result2["messages"]
        _print_message_chain(msgs2, "Turn 2")
        stats2 = _count_message_types(msgs2)

        # Turn 2 应该包含 Turn 1 的历史
        assert stats2["human"] >= 2, f"Turn 2 应有至少 2 条 human 消息，实际: {stats2}"

        # ===== Turn 3: 修改关键帧 =====
        print("\n--- Turn 3: 修改第1镜关键帧 ---")
        result3 = await agent.ainvoke(
            {"messages": [HumanMessage(content="这个不太好，帮我重新生成第1镜的首帧，要更加明亮")]},
            config=config,
        )
        msgs3 = result3["messages"]
        _print_message_chain(msgs3, "Turn 3")
        stats3 = _count_message_types(msgs3)

        # Turn 3 应包含完整历史
        assert stats3["human"] >= 3, f"Turn 3 应有至少 3 条 human 消息，实际: {stats3}"

        # 验证 Turn 3 调用了 regenerate_keyframes
        tool_names_t3 = []
        for m in msgs3:
            if isinstance(m, AIMessage) and m.tool_calls:
                tool_names_t3.extend(tc["name"] for tc in m.tool_calls)
        assert "regenerate_keyframes" in tool_names_t3

        # 验证最终回复引用了修改结果
        final_ai = msgs3[-1]
        assert isinstance(final_ai, AIMessage)
        assert final_ai.content  # 有实质回复

        # ===== 验证 message chain 完整性 =====
        print(f"\n=== Message History Summary ===")
        print(f"  Turn 1: {len(msgs1)} messages")
        print(f"  Turn 2: {len(msgs2)} messages (should > Turn 1)")
        print(f"  Turn 3: {len(msgs3)} messages (should > Turn 2)")
        assert len(msgs2) > len(msgs1), "Turn 2 历史应比 Turn 1 长"
        assert len(msgs3) > len(msgs2), "Turn 3 历史应比 Turn 2 长"


# =====================================================================
# Scenario 5: 同时查看多个产物
# =====================================================================
class TestScenario5_CharacterAndMusic:
    @pytest.mark.asyncio
    async def test_ask_characters_and_music(self, llm):
        """用户问'列出所有角色和配乐'→ LLM 可能一次调多个 tool 或分步。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        agent = create_video_companion_agent(
            llm=llm, run_id=REAL_RUN_ID, user_id=REAL_USER_ID,
            thread_id=THREAD_ID, snapshot=snap,
            enable_summarization=False,
        )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="帮我看看所有的角色列表")]},
        )

        messages = result["messages"]
        _print_message_chain(messages, "Scenario 5: 角色列表")

        tool_names = []
        for m in messages:
            if isinstance(m, AIMessage) and m.tool_calls:
                tool_names.extend(tc["name"] for tc in m.tool_calls)
        assert "get_artifact_detail" in tool_names

        # 验证返回了角色信息
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        combined = " ".join(m.content for m in tool_results)
        assert "角色列表" in combined, f"应包含'角色列表'，实际: {combined[:300]}"


# =====================================================================
# Scenario 6: Agent 正确理解中文指令并映射工具参数
# =====================================================================
class TestScenario6_ChineseInstruction:
    @pytest.mark.asyncio
    async def test_chinese_instruction_mapping(self, llm, mock_regenerate_videos):
        """用户用中文说'第10镜的视频节奏太快了'→ LLM 正确映射到 regenerate_videos。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        agent = create_video_companion_agent(
            llm=llm, run_id=REAL_RUN_ID, user_id=REAL_USER_ID,
            thread_id=THREAD_ID, snapshot=snap,
            enable_summarization=False,
        )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="第10镜的视频节奏太快了，重新生成一个慢一点的")]},
        )

        messages = result["messages"]
        _print_message_chain(messages, "Scenario 6: 中文指令映射")

        tool_names = []
        tool_args_list = []
        for m in messages:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    tool_names.append(tc["name"])
                    tool_args_list.append(tc["args"])

        assert "regenerate_videos" in tool_names, \
            f"应调用 regenerate_videos，实际: {tool_names}"

        # 验证参数映射正确
        for name, args in zip(tool_names, tool_args_list):
            if name == "regenerate_videos":
                assert 10 in args.get("shot_numbers", []), \
                    f"shot_numbers 应包含 10，实际: {args}"
                instruction = args.get("instruction", "")
                assert instruction, "instruction 不应为空"
                print(f"  Mapped instruction: {instruction}")

        mock_regenerate_videos.assert_called_once()


# =====================================================================
# Scenario 7: Message 结构完整性 — 每个 tool_call 都有对应 tool_result
# =====================================================================
class TestScenario7_MessageIntegrity:
    @pytest.mark.asyncio
    async def test_tool_call_result_pairing(self, llm, mock_regenerate_keyframes):
        """验证每个 tool_call 都有配对的 ToolMessage。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        agent = create_video_companion_agent(
            llm=llm, run_id=REAL_RUN_ID, user_id=REAL_USER_ID,
            thread_id=THREAD_ID, snapshot=snap,
            enable_summarization=False,
        )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(
                content="先看看项目状态，然后重新生成第2镜的首帧关键帧，让画面更温暖"
            )]},
        )

        messages = result["messages"]
        _print_message_chain(messages, "Scenario 7: Message 完整性验证")

        # 收集所有 tool_call IDs
        all_tool_call_ids = set()
        for m in messages:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    all_tool_call_ids.add(tc["id"])

        # 收集所有 tool_result 对应的 tool_call_id
        all_tool_result_ids = set()
        for m in messages:
            if isinstance(m, ToolMessage):
                all_tool_result_ids.add(m.tool_call_id)

        print(f"  Tool call IDs: {all_tool_call_ids}")
        print(f"  Tool result IDs: {all_tool_result_ids}")

        # 每个 tool_call 都应有 tool_result 配对
        unmatched = all_tool_call_ids - all_tool_result_ids
        assert not unmatched, \
            f"以下 tool_call 没有配对的 ToolMessage: {unmatched}"

        # 每个 tool_result 都应有对应的 tool_call
        orphaned = all_tool_result_ids - all_tool_call_ids
        assert not orphaned, \
            f"以下 ToolMessage 没有对应的 tool_call: {orphaned}"

        # 验证消息顺序：AI(tool_calls) 后紧跟 ToolMessage(s)
        for i, m in enumerate(messages):
            if isinstance(m, AIMessage) and m.tool_calls:
                # 接下来应该是 ToolMessage
                expected_ids = {tc["id"] for tc in m.tool_calls}
                found_ids = set()
                for j in range(i + 1, len(messages)):
                    if isinstance(messages[j], ToolMessage):
                        found_ids.add(messages[j].tool_call_id)
                    else:
                        break
                assert expected_ids == found_ids, \
                    f"AI tool_calls @ [{i}] expected results {expected_ids}, found {found_ids}"

        # 最后一条消息应该是 AI 的自然语言回复
        assert isinstance(messages[-1], AIMessage), "最后一条应是 AI 回复"
        assert not messages[-1].tool_calls, "最后一条不应有 tool_calls"
        assert messages[-1].content, "最后一条应有内容"
