"""
Video Companion Agent 真实 DB 集成测试。

直连开发环境 DB，验证 snapshot_builder + renderer + tools + agent 创建。

运行：
  conda run -n cuti-video-local pytest tests/services/agent/video_edit/test_companion_real_db.py -v -s

使用 run_id=55a9c30e-... (admin, 107 kfs, 107 vgs, 7 chars, 107 scenes, 43 music, 43 segments)
"""
from __future__ import annotations

import logging
import pytest

logger = logging.getLogger(__name__)

REAL_RUN_ID = "55a9c30e-3292-4958-9d56-95e55e8975bd"
REAL_USER_ID = "admin"

# DB 初始化见 conftest.py (同步 fixture)


# =====================================================================
# 1. build_snapshot_from_db — 真实 DB
# =====================================================================

class TestBuildSnapshotRealDB:
    @pytest.mark.asyncio
    async def test_build_snapshot_real_run(self):
        """从真实 DB 构建快照，验证各阶段数据。"""
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)

        print(f"\n=== ProjectSnapshot for {REAL_RUN_ID} ===")
        for k, v in snap.items():
            print(f"  {k}: {v}")

        assert snap["run_id"] == REAL_RUN_ID
        assert snap["total_shots"] > 0

        assert snap["outline"]["status"] == "completed"
        assert snap["outline"]["total"] == 1

        assert snap["characters"]["total"] == 7
        assert snap["characters"]["status"] == "completed"

        assert snap["scenes"]["total"] == 107

        assert snap["keyframes"]["total"] > 0
        assert snap["keyframes"]["status"] in ("completed", "partial")

        assert snap["videos"]["total"] > 0

        assert snap["music"]["total"] > 0

        assert snap["segments"]["total"] > 0

    @pytest.mark.asyncio
    async def test_build_snapshot_nonexistent_run(self):
        """不存在的 run_id 应返回全部 not_started。"""
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

        snap = await build_snapshot_from_db("nonexistent-run-id", "fake-user")
        assert snap["phase"] == "user_input_analysis"
        assert snap["total_shots"] == 0
        assert snap["outline"]["status"] == "not_started"


# =====================================================================
# 2. render_snapshot_for_llm — 用真实数据渲染
# =====================================================================

class TestRenderSnapshotRealDB:
    @pytest.mark.asyncio
    async def test_render_real_snapshot(self):
        """真实快照渲染输出应包含关键信息。"""
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db
        from app.services.agent.video_edit.snapshot_renderer import render_snapshot_for_llm

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        text = render_snapshot_for_llm(snap)

        print(f"\n=== Rendered Snapshot ===\n{text}")

        assert REAL_RUN_ID in text
        assert "大纲" in text
        assert "角色" in text
        assert "关键帧" in text
        assert "视频" in text
        assert "配乐" in text
        assert "片段" in text
        assert "✅" in text
        assert "/" in text


# =====================================================================
# 3. GetProjectStatusTool — 真实 DB
# =====================================================================

class TestGetProjectStatusRealDB:
    @pytest.mark.asyncio
    async def test_get_project_status_real(self):
        """GetProjectStatusTool 真实调用。"""
        from app.services.agent.video_edit.tools.get_project_status import GetProjectStatusTool

        tool = GetProjectStatusTool(run_id=REAL_RUN_ID, user_id=REAL_USER_ID)
        result = await tool._arun()

        print(f"\n=== GetProjectStatus output ===\n{result}")

        assert REAL_RUN_ID in result
        assert "大纲" in result
        assert "关键帧" in result


# =====================================================================
# 4. GetArtifactDetailTool — 真实 DB
# =====================================================================

class TestGetArtifactDetailRealDB:
    @pytest.mark.asyncio
    async def test_detail_outline(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id=REAL_RUN_ID, user_id=REAL_USER_ID)
        result = await tool._arun(artifact_type="outline")
        print(f"\n=== Outline detail ===\n{result}")
        assert "大纲" in result

    @pytest.mark.asyncio
    async def test_detail_keyframe_by_shot(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id=REAL_RUN_ID, user_id=REAL_USER_ID)
        result = await tool._arun(artifact_type="keyframe", shot_number=1)
        print(f"\n=== Keyframe shot_1 ===\n{result}")
        assert "shot_1" in result or "首帧" in result

    @pytest.mark.asyncio
    async def test_detail_video_by_shot(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id=REAL_RUN_ID, user_id=REAL_USER_ID)
        result = await tool._arun(artifact_type="video", shot_number=1)
        print(f"\n=== Video shot_1 ===\n{result}")
        assert "shot_1" in result or "视频" in result

    @pytest.mark.asyncio
    async def test_detail_character_list(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id=REAL_RUN_ID, user_id=REAL_USER_ID)
        result = await tool._arun(artifact_type="character")
        print(f"\n=== Characters ===\n{result}")
        assert "角色列表" in result

    @pytest.mark.asyncio
    async def test_detail_music_list(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id=REAL_RUN_ID, user_id=REAL_USER_ID)
        result = await tool._arun(artifact_type="music")
        print(f"\n=== Music ===\n{result}")
        assert "配乐列表" in result

    @pytest.mark.asyncio
    async def test_detail_shot_comprehensive(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id=REAL_RUN_ID, user_id=REAL_USER_ID)
        result = await tool._arun(artifact_type="shot", shot_number=1)
        print(f"\n=== Shot 1 comprehensive ===\n{result}")
        assert "Shot" in result


# =====================================================================
# 5. Agent 创建 + system prompt 验证
# =====================================================================

class TestCompanionAgentCreation:
    @pytest.mark.asyncio
    async def test_create_agent_compiles(self):
        """create_video_companion_agent 能成功编译图。"""
        from app.services.agent.video_edit.agent import create_video_companion_agent
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db
        from langchain_openai import ChatOpenAI

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

        agent = create_video_companion_agent(
            llm=llm,
            run_id=REAL_RUN_ID,
            user_id=REAL_USER_ID,
            thread_id="test-thread",
            snapshot=snap,
            checkpointer=None,
            enable_summarization=False,
        )

        print(f"\n=== Agent created: {type(agent).__name__} ===")
        assert agent is not None

    @pytest.mark.asyncio
    async def test_system_prompt_contains_all_sections(self):
        """system prompt 包含静态段 + 动态段。"""
        from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db
        from app.services.agent.video_edit.system_prompt import build_system_prompt

        snap = await build_snapshot_from_db(REAL_RUN_ID, REAL_USER_ID)
        prompt = build_system_prompt(snap)

        print(f"\n=== System prompt ({len(prompt)} chars) ===")
        print(prompt[:300])
        print("...")
        print(prompt[-300:])

        # 静态段
        assert "Cuti Video Companion" in prompt
        assert "get_project_status" in prompt
        assert "regenerate_keyframes" in prompt
        assert "select_version" in prompt
        assert "update_music_prompt" in prompt

        # 动态段
        assert REAL_RUN_ID in prompt
        assert "大纲" in prompt
        assert "✅" in prompt

    @pytest.mark.asyncio
    async def test_all_tools_registered(self):
        """工具注册后绑定正确。"""
        from app.services.agent.video_edit.tools import get_companion_tools

        tools = get_companion_tools(
            run_id=REAL_RUN_ID, user_id=REAL_USER_ID, thread_id="test-thread",
        )
        assert len(tools) == 13

        tool_names = sorted(t.name for t in tools)
        print(f"\n=== {len(tools)} tools: {tool_names} ===")

        expected = sorted([
            "get_project_status", "get_artifact_detail",
            "regenerate_keyframes", "regenerate_videos", "regenerate_characters",
            "reassemble_video", "continue_pipeline",
            "select_version", "update_music_prompt", "modify_outline",
            "update_scene", "analyze_image", "analyze_video",
        ])
        assert tool_names == expected

        for t in tools:
            assert t.run_id == REAL_RUN_ID
