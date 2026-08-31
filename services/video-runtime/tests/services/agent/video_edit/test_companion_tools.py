"""
Video Companion Agent 工具 + 快照渲染/构建 的综合测试。

运行：
  conda run -n cuti-video-local pytest tests/services/agent/video_edit/test_companion_tools.py -v

测试覆盖：
  1. ProjectSnapshot / PhaseStatus / StageStats 数据结构
  2. render_snapshot_for_llm 渲染器（纯函数，无 DB）
  3. build_snapshot_from_db（mock DB 层）
  4. 每个 Tool 的 schema 校验、输入输出
  5. get_companion_tools 注册入口
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from types import SimpleNamespace
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.project_snapshot import (
    PhaseStatus,
    PipelinePhase,
    ProjectSnapshot,
    StageStats,
)
from app.services.agent.video_edit.snapshot_renderer import (
    render_snapshot_for_llm,
)
from app.services.agent.video_edit.snapshot_builder import (
    _build_versions_count,
    _compute_simple_stage,
    _compute_versioned_stage,
    _determine_phase,
    _phase_from_pending_gate,
    _resolve_pending_gate,
    _resolve_pipeline_task_status,
    build_snapshot_from_db,
)


# =====================================================================
# Helpers: 构造 mock DB 对象
# =====================================================================

def _make_keyframe(uuid: str, shot_number: int, frame_index: int = 0, current_version_index: int = 0):
    return SimpleNamespace(
        uuid=uuid,
        shot_number=shot_number,
        frame_index=frame_index,
        current_version_index=current_version_index,
    )


def _make_keyframe_version(uuid: str, keyframe_id: str, version_number: int, success: bool = True,
                           t2i_prompt: str = "a girl walking", provider: str = "flux"):
    return SimpleNamespace(
        uuid=uuid,
        keyframe_id=keyframe_id,
        version_number=version_number,
        success=success,
        t2i_prompt=t2i_prompt,
        provider=provider,
        shot_number=0,
    )


def _make_video_gen(uuid: str, shot_number: int, current_version_index: int = 0):
    return SimpleNamespace(
        uuid=uuid,
        shot_number=shot_number,
        current_version_index=current_version_index,
    )


def _make_video_gen_version(uuid: str, video_generation_id: str, version_number: int,
                            success: bool = True, duration: float = 4.0,
                            motion_prompt: str = "smooth pan", provider: str = "kling"):
    return SimpleNamespace(
        uuid=uuid,
        video_generation_id=video_generation_id,
        version_number=version_number,
        success=success,
        duration=duration,
        motion_prompt=motion_prompt,
        provider=provider,
        shot_number=0,
    )


def _make_character(uuid: str, name: str = "Alice", type: str = "character",
                    description: str = "A young girl", appearance: str = "long hair",
                    current_version_index: int = 0, selected_version_id: Optional[str] = None):
    return SimpleNamespace(
        uuid=uuid, name=name, type=type, description=description,
        appearance=appearance, current_version_index=current_version_index,
        selected_version_id=selected_version_id, shot_number=None,
    )


def _make_char_version(uuid: str, video_character_id: str, version_number: int = 0):
    return SimpleNamespace(uuid=uuid, video_character_id=video_character_id, version_number=version_number)


def _make_outline(uuid: str = "outline-001", title: str = "森林故事",
                  description: str = "一个小女孩的冒险", themes: list = None,
                  narrative_structure: str = "三幕式", total_duration: float = 60.0,
                  current_version_index: int = 0, theme: str = "冒险成长",
                  key_message: str = "勇敢前行",
                  style_guide: str = "真实摄影，微电影风格",
                  analysis_id: str = "analysis-001"):
    return SimpleNamespace(
        uuid=uuid, title=title, description=description,
        themes=themes or ["冒险", "成长"],
        narrative_structure=narrative_structure,
        theme=theme,
        key_message=key_message,
        style_guide=style_guide,
        analysis_id=analysis_id,
        total_duration=total_duration,
        current_version_index=current_version_index,
    )


def _make_narration(uuid: str, shot_number: int, has_narration: bool = True, current_version_index: int = 0):
    return SimpleNamespace(
        uuid=uuid, shot_number=shot_number, has_narration=has_narration,
        current_version_index=current_version_index,
    )


def _make_music_gen(uuid: str, is_full_story_music: bool = True, is_instrumental: bool = True,
                    shot_number: Optional[int] = None, current_version_index: int = 0):
    return SimpleNamespace(
        uuid=uuid, is_full_story_music=is_full_story_music,
        is_instrumental=is_instrumental, shot_number=shot_number,
        current_version_index=current_version_index,
    )


# =====================================================================
# 1. PhaseStatus / PipelinePhase / StageStats / ProjectSnapshot 结构测试
# =====================================================================

class TestDataStructures:
    def test_phase_status_values(self):
        assert PhaseStatus.NOT_STARTED.value == "not_started"
        assert PhaseStatus.COMPLETED.value == "completed"
        assert PhaseStatus.PARTIAL.value == "partial"
        assert PhaseStatus.FAILED.value == "failed"
        assert PhaseStatus.IN_PROGRESS.value == "in_progress"

    def test_pipeline_phase_values(self):
        assert PipelinePhase.USER_INPUT.value == "user_input_analysis"
        assert PipelinePhase.KEYFRAME.value == "keyframe_generation"
        assert PipelinePhase.VIDEO.value == "video_generation"
        assert PipelinePhase.COMPLETED.value == "completed"

    def test_stage_stats_typed_dict(self):
        stats = StageStats(
            status="completed",
            total=12,
            succeeded=10,
            failed_items=[5, 7],
            versions_count={3: 2, 5: 3},
        )
        assert stats["status"] == "completed"
        assert stats["total"] == 12
        assert stats["succeeded"] == 10
        assert stats["failed_items"] == [5, 7]
        assert stats["versions_count"] == {3: 2, 5: 3}

    def test_project_snapshot_typed_dict(self):
        snap = ProjectSnapshot(
            run_id="run-123",
            phase="completed",
            task_status="completed",
            total_shots=12,
            total_duration_sec=42.5,
            pending_gate=None,
        )
        assert snap["run_id"] == "run-123"
        assert snap["total_shots"] == 12
        assert snap.get("pending_gate") is None

    def test_stage_stats_partial(self):
        stats = StageStats(status="partial", total=5, succeeded=3)
        assert stats["status"] == "partial"
        assert stats.get("failed_items") is None

    def test_project_snapshot_with_all_stages(self):
        snap = ProjectSnapshot(
            run_id="run-456",
            phase="video_generation",
            task_status="running",
            total_shots=8,
            total_duration_sec=30.0,
            outline=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
            characters=StageStats(status="completed", total=2, succeeded=2, failed_items=[], versions_count={}),
            scenes=StageStats(status="completed", total=3, succeeded=3, failed_items=[], versions_count={}),
            keyframes=StageStats(status="completed", total=8, succeeded=8, failed_items=[], versions_count={3: 2}),
            videos=StageStats(status="partial", total=8, succeeded=6, failed_items=[5, 7], versions_count={}),
            narrations=StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={}),
            music=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
            segments=StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={}),
            assembly=StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={}),
            pending_gate="after_shots",
        )
        assert snap["videos"]["succeeded"] == 6
        assert snap["pending_gate"] == "after_shots"


# =====================================================================
# 2. render_snapshot_for_llm 渲染器测试
# =====================================================================

class TestSnapshotRenderer:
    def test_render_none(self):
        result = render_snapshot_for_llm(None)
        assert "尚无运行中的项目" in result

    def test_render_empty_snapshot(self):
        snap = ProjectSnapshot(
            run_id="run-empty",
            phase="user_input_analysis",
            task_status="queued",
            total_shots=0,
            total_duration_sec=0.0,
        )
        result = render_snapshot_for_llm(snap)
        assert "user_input_analysis" in result
        assert "总镜头数: 0" in result

    def test_render_partial_with_failures(self):
        snap = ProjectSnapshot(
            run_id="run-partial",
            phase="video_generation",
            task_status="running",
            total_shots=12,
            total_duration_sec=42.5,
            outline=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
            characters=StageStats(status="completed", total=2, succeeded=2, failed_items=[], versions_count={}),
            keyframes=StageStats(status="completed", total=12, succeeded=12, failed_items=[], versions_count={3: 2}),
            videos=StageStats(status="partial", total=12, succeeded=10, failed_items=[5, 7], versions_count={}),
            narrations=StageStats(status="completed", total=8, succeeded=8, failed_items=[], versions_count={}),
            music=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
        )
        result = render_snapshot_for_llm(snap)
        assert "✅ 12/12" in result  # keyframes all succeeded
        assert "⚠️ 10/12" in result  # videos partial
        assert "shot_5" in result
        assert "shot_7" in result
        assert "shot_3: 2v" in result  # multi-version

    def test_render_completed(self):
        snap = ProjectSnapshot(
            run_id="run-done",
            phase="completed",
            task_status="completed",
            total_shots=6,
            total_duration_sec=25.0,
            outline=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
            keyframes=StageStats(status="completed", total=6, succeeded=6, failed_items=[], versions_count={}),
            videos=StageStats(status="completed", total=6, succeeded=6, failed_items=[], versions_count={}),
            assembly=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
        )
        result = render_snapshot_for_llm(snap)
        assert "completed" in result
        assert "25.0s" in result

    def test_render_with_pending_gate(self):
        snap = ProjectSnapshot(
            run_id="run-gate",
            phase="gate_after_character",
            task_status="interrupted",
            total_shots=0,
            total_duration_sec=0.0,
            pending_gate="after_character",
        )
        result = render_snapshot_for_llm(snap)
        assert "⏸️" in result
        assert "after_character" in result

    def test_render_with_failed_pending_gate(self):
        snap = ProjectSnapshot(
            run_id="run-fail",
            phase="gate_after_keyframe_reflection",
            task_status="interrupted",
            total_shots=8,
            total_duration_sec=0.0,
            pending_gate="failed_keyframe",
        )
        result = render_snapshot_for_llm(snap)
        assert "阶段失败暂停" in result
        assert "failed_keyframe" in result

    def test_render_all_not_started(self):
        snap = ProjectSnapshot(
            run_id="run-fresh",
            phase="user_input_analysis",
            task_status="running",
            total_shots=0,
            total_duration_sec=0.0,
            outline=StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={}),
            videos=StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={}),
        )
        result = render_snapshot_for_llm(snap)
        assert "⏳ 未开始" in result

    def test_render_failed_stage(self):
        snap = ProjectSnapshot(
            run_id="run-fail",
            phase="video_generation",
            task_status="failed",
            total_shots=3,
            total_duration_sec=10.0,
            videos=StageStats(status="failed", total=3, succeeded=0, failed_items=[1, 2, 3], versions_count={}),
        )
        result = render_snapshot_for_llm(snap)
        assert "❌ 0/3" in result
        assert "shot_1" in result


# =====================================================================
# 3. snapshot_builder 内部函数测试
# =====================================================================

class TestSnapshotBuilderHelpers:
    def test_compute_simple_stage_empty(self):
        stats = _compute_simple_stage([])
        assert stats["status"] == "not_started"
        assert stats["total"] == 0

    def test_compute_simple_stage_with_items(self):
        stats = _compute_simple_stage([1, 2, 3])
        assert stats["status"] == "completed"
        assert stats["total"] == 3
        assert stats["succeeded"] == 3

    def test_compute_versioned_stage_empty(self):
        stats = _compute_versioned_stage([], [], "keyframe_id")
        assert stats["status"] == "not_started"

    def test_compute_versioned_stage_all_success(self):
        kfs = [_make_keyframe("kf-1", 1), _make_keyframe("kf-2", 2)]
        versions = [
            _make_keyframe_version("v-1", "kf-1", 0, success=True),
            _make_keyframe_version("v-2", "kf-2", 0, success=True),
        ]
        stats = _compute_versioned_stage(kfs, versions, "keyframe_id")
        assert stats["status"] == "completed"
        assert stats["total"] == 2
        assert stats["succeeded"] == 2
        assert stats["failed_items"] == []

    def test_compute_versioned_stage_partial(self):
        kfs = [_make_keyframe("kf-1", 1), _make_keyframe("kf-2", 2), _make_keyframe("kf-3", 3)]
        versions = [
            _make_keyframe_version("v-1", "kf-1", 0, success=True),
            _make_keyframe_version("v-2", "kf-2", 0, success=False),
            _make_keyframe_version("v-3", "kf-3", 0, success=True),
        ]
        stats = _compute_versioned_stage(kfs, versions, "keyframe_id")
        assert stats["status"] == "partial"
        assert stats["succeeded"] == 2
        assert stats["failed_items"] == [2]

    def test_compute_versioned_stage_multi_version_uses_latest(self):
        """多版本场景：v0 失败，v1 成功 → 应该算成功。"""
        kfs = [_make_keyframe("kf-1", 1)]
        versions = [
            _make_keyframe_version("v-0", "kf-1", 0, success=False),
            _make_keyframe_version("v-1", "kf-1", 1, success=True),
        ]
        stats = _compute_versioned_stage(kfs, versions, "keyframe_id")
        assert stats["status"] == "completed"
        assert stats["succeeded"] == 1
        assert stats["versions_count"] == {1: 2}

    def test_compute_versioned_stage_all_failed(self):
        kfs = [_make_keyframe("kf-1", 1), _make_keyframe("kf-2", 2)]
        versions = [
            _make_keyframe_version("v-1", "kf-1", 0, success=False),
            _make_keyframe_version("v-2", "kf-2", 0, success=False),
        ]
        stats = _compute_versioned_stage(kfs, versions, "keyframe_id")
        assert stats["status"] == "failed"
        assert stats["succeeded"] == 0
        assert stats["failed_items"] == [1, 2]

    def test_build_versions_count(self):
        parents = [
            SimpleNamespace(uuid="p-1", shot_number=1),
            SimpleNamespace(uuid="p-2", shot_number=2),
            SimpleNamespace(uuid="p-3", shot_number=3),
        ]
        versions = [
            SimpleNamespace(keyframe_id="p-1"),
            SimpleNamespace(keyframe_id="p-2"),
            SimpleNamespace(keyframe_id="p-2"),
            SimpleNamespace(keyframe_id="p-3"),
            SimpleNamespace(keyframe_id="p-3"),
            SimpleNamespace(keyframe_id="p-3"),
        ]
        result = _build_versions_count(versions, "keyframe_id", parents)
        assert result == {2: 2, 3: 3}  # p-1 只有 1 个版本，不记录

    def test_determine_phase_completed(self):
        completed_stats = StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={})
        ns = StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={})
        phase = _determine_phase(completed_stats, completed_stats, completed_stats,
                                 completed_stats, completed_stats, completed_stats, completed_stats, completed_stats, ns)
        assert phase == "completed"

    def test_determine_phase_video(self):
        completed = StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={})
        partial = StageStats(status="partial", total=5, succeeded=3, failed_items=[4, 5], versions_count={})
        ns = StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={})
        phase = _determine_phase(completed, completed, completed, completed, partial, ns, ns, ns, ns)
        assert phase == "video_generation"

    def test_determine_phase_user_input(self):
        ns = StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={})
        phase = _determine_phase(ns, ns, ns, ns, ns, ns, ns, ns, ns)
        assert phase == "user_input_analysis"

    def test_determine_phase_music_before_outline(self):
        completed = StageStats(status="completed", total=8, succeeded=8, failed_items=[], versions_count={})
        ns = StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={})
        phase = _determine_phase(ns, ns, ns, ns, ns, ns, ns, ns, completed)
        assert phase == "music_generation"

    def test_phase_from_pending_gate_after_music(self):
        from app.services.agent.video_edit.snapshot_builder import _phase_from_pending_gate
        assert _phase_from_pending_gate("after_music") == "music_generation"
        assert _phase_from_pending_gate("failed_keyframe") == "gate_after_keyframe_reflection"

    def test_determine_phase_outline(self):
        completed = StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={})
        ns = StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={})
        phase = _determine_phase(completed, ns, ns, ns, ns, ns, ns, ns, ns)
        assert phase == "outline_generation"


class TestResolvePipelineTaskStatus:
    @pytest.mark.asyncio
    async def test_prefers_latest_resume_over_anchor_run_id(self):
        """锚点 run interrupted，thread 最新 resume completed → 返回 completed。"""
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.models.database.get_asyncpg_pool", return_value=mock_pool),
            patch(
                "app.utils.asyncpg_utils.fetch_one",
                new_callable=AsyncMock,
                return_value={
                    "run_id": "resume-completed",
                    "status": "completed",
                    "run_type": "resume",
                },
            ),
            patch(
                "app.crud.conversation.async_get_conversation_run_by_run_id",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(status="interrupted"),
            ),
        ):
            status = await _resolve_pipeline_task_status(
                thread_id="thread-1",
                run_id="anchor-interrupted",
            )
        assert status == "completed"

    @pytest.mark.asyncio
    async def test_ignores_companion_main_when_resume_exists(self):
        """有 resume 链时，不应用 ChatAgent 问状态新建的 main run。"""
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.models.database.get_asyncpg_pool", return_value=mock_pool),
            patch(
                "app.utils.asyncpg_utils.fetch_one",
                new_callable=AsyncMock,
                return_value={
                    "run_id": "2762517c",
                    "status": "completed",
                    "run_type": "resume",
                },
            ),
        ):
            status = await _resolve_pipeline_task_status(
                thread_id="thread-1",
                run_id="anchor-interrupted",
            )
        assert status == "completed"

    @pytest.mark.asyncio
    async def test_falls_back_to_run_id_without_thread(self):
        with patch(
            "app.crud.conversation.async_get_conversation_run_by_run_id",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(status="running"),
        ):
            status = await _resolve_pipeline_task_status(run_id="run-only")
        assert status == "running"


class TestResolvePendingGate:
    @pytest.mark.asyncio
    async def test_returns_latest_uncontinued_interrupt_step(self):
        messages = [
            {
                "event_type": "interrupt",
                "event_data": {
                    "continued": True,
                    "interrupt_data": {"step": "after_character"},
                },
            },
            {
                "event_type": "interrupt",
                "event_data": {
                    "interrupt_data": {"step": "failed_keyframe"},
                },
            },
        ]
        with (
            patch(
                "app.crud.conversation.async_get_conversation_by_thread_id",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(id=1),
            ),
            patch(
                "app.crud.conversation.async_get_conversation_messages",
                new_callable=AsyncMock,
                return_value=messages,
            ),
        ):
            gate = await _resolve_pending_gate("thread-1")
        assert gate == "failed_keyframe"

    @pytest.mark.asyncio
    async def test_returns_none_when_all_continued(self):
        messages = [
            {
                "event_type": "interrupt",
                "event_data": {
                    "continued": True,
                    "interrupt_data": {"step": "after_keyframe_reflection"},
                },
            },
        ]
        with (
            patch(
                "app.crud.conversation.async_get_conversation_by_thread_id",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(id=1),
            ),
            patch(
                "app.crud.conversation.async_get_conversation_messages",
                new_callable=AsyncMock,
                return_value=messages,
            ),
            patch(
                "app.services.agent.video_edit.snapshot_builder.get_asyncpg_pool",
                create=True,
            ),
        ):
            # patch pool via models.database path used inside _resolve_pending_gate
            mock_conn = AsyncMock()
            mock_conn.fetchval = AsyncMock(return_value=None)
            mock_pool = MagicMock()
            mock_pool.acquire = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            ))
            with patch(
                "app.models.database.get_asyncpg_pool",
                return_value=mock_pool,
            ):
                gate = await _resolve_pending_gate("thread-1")
        assert gate is None

    @pytest.mark.asyncio
    async def test_surfaces_gate_when_continued_but_still_interrupted(self):
        messages = [
            {
                "event_type": "interrupt",
                "event_data": {
                    "continued": True,
                    "interrupt_data": {"step": "after_storyboard_detail"},
                },
            },
        ]
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value="run-still-interrupted")
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        with (
            patch(
                "app.crud.conversation.async_get_conversation_by_thread_id",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(id=1),
            ),
            patch(
                "app.crud.conversation.async_get_conversation_messages",
                new_callable=AsyncMock,
                return_value=messages,
            ),
            patch(
                "app.models.database.get_asyncpg_pool",
                return_value=mock_pool,
            ),
        ):
            gate = await _resolve_pending_gate("thread-1")
        assert gate == "after_storyboard_detail"


# =====================================================================
# 4. build_snapshot_from_db (mock DB)
# =====================================================================

class TestBuildSnapshotFromDB:
    @pytest.mark.asyncio
    async def test_build_empty_run(self):
        """run_id 没有任何数据 → 全部 not_started"""
        with (
            patch("app.services.agent.video_edit.snapshot_builder.build_snapshot_from_db.__module__"),
            patch("app.crud.video.video_story.get_video_story_outline_by_run_id", new_callable=AsyncMock, return_value=None),
            patch("app.crud.video.video_story.get_scenes_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_character.get_characters_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_keyframe.get_keyframes_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_generation.get_video_generations_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_audio.get_narrations_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_audio.get_music_generations_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_segment.get_video_segments_with_data_by_run_id", new_callable=AsyncMock, return_value=[]),
        ):
            snap = await build_snapshot_from_db("run-empty", "user-1")
            assert snap["run_id"] == "run-empty"
            assert snap["phase"] == "user_input_analysis"
            assert snap["total_shots"] == 0
            assert snap["outline"]["status"] == "not_started"
            assert snap["videos"]["status"] == "not_started"

    @pytest.mark.asyncio
    async def test_build_partial_run(self):
        """有 outline + characters + keyframes (部分失败) + 没有 video"""
        outline = _make_outline()
        chars = [_make_character("char-1", name="Alice"), _make_character("char-2", name="Bob")]
        kfs = [_make_keyframe("kf-1", 1), _make_keyframe("kf-2", 2), _make_keyframe("kf-3", 3)]
        kf_versions = [
            _make_keyframe_version("v-1", "kf-1", 0, success=True),
            _make_keyframe_version("v-2", "kf-2", 0, success=True),
            _make_keyframe_version("v-3", "kf-3", 0, success=False),
        ]

        with (
            patch("app.crud.video.video_story.get_video_story_outline_by_run_id", new_callable=AsyncMock, return_value=outline),
            patch("app.crud.video.video_story.get_scenes_by_run_id", new_callable=AsyncMock, return_value=[SimpleNamespace(uuid="s-1"), SimpleNamespace(uuid="s-2")]),
            patch("app.crud.video.video_character.get_characters_by_run_id", new_callable=AsyncMock, return_value=chars),
            patch("app.crud.video.video_character.get_character_versions_batch", new_callable=AsyncMock, return_value={"char-1": [_make_char_version("cv-1", "char-1")], "char-2": [_make_char_version("cv-2", "char-2")]}),
            patch("app.crud.video.video_keyframe.get_keyframes_by_run_id", new_callable=AsyncMock, return_value=kfs),
            patch("app.crud.video.video_keyframe.get_keyframe_versions_by_keyframe_ids", new_callable=AsyncMock, return_value=kf_versions),
            patch("app.crud.video.video_generation.get_video_generations_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_audio.get_narrations_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_audio.get_music_generations_by_run_id", new_callable=AsyncMock, return_value=[]),
            patch("app.crud.video.video_segment.get_video_segments_with_data_by_run_id", new_callable=AsyncMock, return_value=[]),
        ):
            snap = await build_snapshot_from_db("run-partial", "user-1")
            assert snap["phase"] == "keyframe_generation"
            assert snap["outline"]["status"] == "completed"
            assert snap["characters"]["total"] == 2
            assert snap["keyframes"]["status"] == "partial"
            assert snap["keyframes"]["failed_items"] == [3]
            assert snap["videos"]["status"] == "not_started"
            assert snap["total_shots"] == 3

    @pytest.mark.asyncio
    async def test_build_completed_run(self):
        """完整流水线完成"""
        outline = _make_outline()
        kfs = [_make_keyframe("kf-1", 1)]
        kf_versions = [_make_keyframe_version("v-1", "kf-1", 0)]
        vgs = [_make_video_gen("vg-1", 1)]
        vg_versions = [_make_video_gen_version("vv-1", "vg-1", 0)]

        with (
            patch("app.crud.video.video_story.get_video_story_outline_by_run_id", new_callable=AsyncMock, return_value=outline),
            patch("app.crud.video.video_story.get_scenes_by_run_id", new_callable=AsyncMock, return_value=[SimpleNamespace(uuid="s-1")]),
            patch("app.crud.video.video_character.get_characters_by_run_id", new_callable=AsyncMock, return_value=[_make_character("c-1")]),
            patch("app.crud.video.video_character.get_character_versions_batch", new_callable=AsyncMock, return_value={"c-1": [_make_char_version("cv-1", "c-1")]}),
            patch("app.crud.video.video_keyframe.get_keyframes_by_run_id", new_callable=AsyncMock, return_value=kfs),
            patch("app.crud.video.video_keyframe.get_keyframe_versions_by_keyframe_ids", new_callable=AsyncMock, return_value=kf_versions),
            patch("app.crud.video.video_generation.get_video_generations_by_run_id", new_callable=AsyncMock, return_value=vgs),
            patch("app.crud.video.video_generation.get_video_generation_versions_by_video_generation_ids", new_callable=AsyncMock, return_value=vg_versions),
            patch("app.crud.video.video_audio.get_narrations_by_run_id", new_callable=AsyncMock, return_value=[_make_narration("n-1", 1)]),
            patch("app.crud.video.video_audio.get_music_generations_by_run_id", new_callable=AsyncMock, return_value=[_make_music_gen("m-1")]),
            patch("app.crud.video.video_segment.get_video_segments_with_data_by_run_id", new_callable=AsyncMock, return_value=[SimpleNamespace(uuid="seg-1")]),
        ):
            snap = await build_snapshot_from_db("run-done", "user-1")
            assert snap["outline"]["status"] == "completed"
            assert snap["keyframes"]["status"] == "completed"
            assert snap["videos"]["status"] == "completed"
            assert snap["narrations"]["status"] == "completed"
            assert snap["music"]["status"] == "completed"
            assert snap["segments"]["status"] == "completed"
            assert snap["total_shots"] == 1


# =====================================================================
# 5. Tool Schema 验证
# =====================================================================

class TestToolSchemas:
    """测试每个 Tool 的 Pydantic input schema 校验。"""

    def test_get_project_status_schema(self):
        from app.services.agent.video_edit.tools.get_project_status import (
            GetProjectStatusInput,
            GetProjectStatusTool,
        )
        tool = GetProjectStatusTool(run_id="r", user_id="u")
        assert tool.name == "get_project_status"
        inp = GetProjectStatusInput()
        assert inp is not None

    def test_get_artifact_detail_schema(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import (
            GetArtifactDetailInput,
            GetArtifactDetailTool,
        )
        tool = GetArtifactDetailTool(run_id="r", user_id="u")
        assert tool.name == "get_artifact_detail"
        inp = GetArtifactDetailInput(artifact_type="keyframe", shot_number=3)
        assert inp.artifact_type == "keyframe"
        assert inp.shot_number == 3

    def test_regenerate_keyframes_schema(self):
        from app.services.agent.video_edit.tools.regenerate_keyframes import (
            RegenerateKeyframesInput,
            RegenerateKeyframesTool,
        )
        tool = RegenerateKeyframesTool(run_id="r", user_id="u", thread_id="t")
        assert tool.name == "regenerate_keyframes"
        inp = RegenerateKeyframesInput(shot_numbers=[1, 3], frame_position="first", instruction="更亮")
        assert inp.shot_numbers == [1, 3]
        assert inp.frame_position == "first"

    def test_regenerate_videos_schema(self):
        from app.services.agent.video_edit.tools.regenerate_videos import (
            RegenerateVideosInput,
            RegenerateVideosTool,
        )
        tool = RegenerateVideosTool(run_id="r", user_id="u", thread_id="t")
        assert tool.name == "regenerate_videos"
        inp = RegenerateVideosInput(shot_numbers=[5, 7])
        assert inp.shot_numbers == [5, 7]

    def test_regenerate_characters_schema(self):
        from app.services.agent.video_edit.tools.regenerate_characters import (
            RegenerateCharactersInput,
            RegenerateCharactersTool,
        )
        tool = RegenerateCharactersTool(run_id="r", user_id="u", thread_id="t")
        assert tool.name == "regenerate_characters"
        inp = RegenerateCharactersInput(character_uuids=["char-1"])
        assert len(inp.character_uuids) == 1

    def test_reassemble_video_schema(self):
        from app.services.agent.video_edit.tools.reassemble_video import (
            ReassembleVideoInput,
            ReassembleVideoTool,
        )
        tool = ReassembleVideoTool(run_id="r", user_id="u", thread_id="t")
        assert tool.name == "reassemble_video"

    def test_continue_pipeline_schema(self):
        from app.services.agent.video_edit.tools.continue_pipeline import (
            ContinuePipelineInput,
            ContinuePipelineTool,
        )
        tool = ContinuePipelineTool(run_id="r", user_id="u", thread_id="t")
        assert tool.name == "continue_pipeline"
        inp = ContinuePipelineInput(gate="after_character")
        assert inp.gate == "after_character"

    def test_select_version_schema(self):
        from app.services.agent.video_edit.tools.select_version import (
            SelectVersionInput,
            SelectVersionTool,
        )
        tool = SelectVersionTool(run_id="r", user_id="u")
        assert tool.name == "select_version"
        inp = SelectVersionInput(entity_type="keyframe", entity_uuid="kf-1", version_uuid="v-2")
        assert inp.entity_type == "keyframe"

    def test_update_music_prompt_schema(self):
        from app.services.agent.video_edit.tools.update_music_prompt import (
            UpdateMusicPromptInput,
            UpdateMusicPromptTool,
        )
        tool = UpdateMusicPromptTool(run_id="r", user_id="u")
        assert tool.name == "update_music_prompt"
        inp = UpdateMusicPromptInput(instruction="upbeat electronic")
        assert inp.instruction == "upbeat electronic"

    def test_modify_outline_schema(self):
        from app.services.agent.video_edit.tools.modify_outline import (
            ModifyOutlineInput,
            ModifyOutlineTool,
        )
        tool = ModifyOutlineTool(run_id="r", user_id="u")
        assert tool.name == "modify_outline"
        inp = ModifyOutlineInput(instruction="改成科技风格", fields=["style", "description"])
        assert inp.instruction == "改成科技风格"
        assert inp.fields == ["style", "description"]
        from app.services.agent.video_edit.modify_outline_service import normalize_outline_fields
        assert normalize_outline_fields(["style_guide", "themes"]) == []


# =====================================================================
# 6. get_companion_tools 统一入口
# =====================================================================

class TestGetCompanionTools:
    def test_returns_all_tools(self):
        from app.services.agent.video_edit.tools import get_companion_tools
        tools = get_companion_tools(run_id="r-1", user_id="u-1", thread_id="t-1")
        assert len(tools) == 13
        names = {t.name for t in tools}
        assert names == {
            "get_project_status",
            "get_artifact_detail",
            "regenerate_keyframes",
            "regenerate_videos",
            "regenerate_characters",
            "reassemble_video",
            "continue_pipeline",
            "select_version",
            "update_music_prompt",
            "modify_outline",
            "update_scene",
            "analyze_image",
            "analyze_video",
        }

    def test_tools_have_bound_ids(self):
        from app.services.agent.video_edit.tools import get_companion_tools
        tools = get_companion_tools(run_id="r-2", user_id="u-2", thread_id="t-2")
        for t in tools:
            assert hasattr(t, "run_id")
            assert t.run_id == "r-2"
            assert t.user_id == "u-2"

    def test_tools_are_base_tool_instances(self):
        from langchain_core.tools import BaseTool
        from app.services.agent.video_edit.tools import get_companion_tools
        tools = get_companion_tools(run_id="r", user_id="u", thread_id="t")
        for t in tools:
            assert isinstance(t, BaseTool)


# =====================================================================
# 7. Tool 异步执行测试（mock DB）
# =====================================================================

class TestToolExecution:
    @pytest.mark.asyncio
    async def test_continue_pipeline_valid_gate(self):
        try:
            import redis  # noqa: F401
        except ImportError:
            sys.modules.setdefault("redis", MagicMock())
            sys.modules.setdefault("redis.asyncio", MagicMock())
        from app.services.agent.video_edit.tools.continue_pipeline import ContinuePipelineTool
        tool = ContinuePipelineTool(run_id="r-1", user_id="u-1", thread_id="t-1")
        m_sqs = MagicMock()
        m_sqs.add_task_to_queue = AsyncMock()
        m_redis = MagicMock()
        m_redis.add_task_index = AsyncMock()
        tqs = importlib.import_module("app.services.task_enqueue_service")
        with (
            patch(
                "app.services.agent.video_edit.tools.continue_pipeline._find_latest_interrupt",
                new_callable=AsyncMock,
                return_value=("msg-99", "run-old", "after_character"),
            ),
            patch.object(
                tqs,
                "prepare_resume_task",
                new_callable=AsyncMock,
                return_value={
                    "run_id": "new-run-1",
                    "thread_id": "t-1",
                    "resume_data": "{}",
                },
            ),
            patch(
                "app.services.aws.sqs_service.SQSTaskService",
                return_value=m_sqs,
            ),
            patch(
                "app.services.redis.connection.get_redis_stream_service",
                new_callable=AsyncMock,
                return_value=m_redis,
            ),
        ):
            result = await tool._arun(gate="after_character")
        assert "resume_submitted" in result
        assert "after_character" in result

    @pytest.mark.asyncio
    async def test_continue_pipeline_invalid_gate(self):
        from app.services.agent.video_edit.tools.continue_pipeline import ContinuePipelineTool
        tool = ContinuePipelineTool(run_id="r-1", user_id="u-1", thread_id="t-1")
        result = await tool._arun(gate="invalid_gate")
        assert "无效" in result

    @pytest.mark.asyncio
    async def test_get_artifact_detail_invalid_type(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id="r-1", user_id="u-1")
        result = await tool._arun(artifact_type="invalid", shot_number=1)
        assert "无效" in result

    @pytest.mark.asyncio
    async def test_get_artifact_detail_outline(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id="r-1", user_id="u-1")
        outline = _make_outline()
        with patch("app.crud.video.video_story.get_video_story_outline_by_run_id",
                    new_callable=AsyncMock, return_value=outline):
            result = await tool._arun(artifact_type="outline")
            assert "森林故事" in result
            assert "outline-001" in result

    @pytest.mark.asyncio
    async def test_get_artifact_detail_keyframe_by_shot(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id="r-1", user_id="u-1")
        kfs = [_make_keyframe("kf-001-aaaa", 3, frame_index=0)]
        kf_versions = [
            _make_keyframe_version("v-001", "kf-001-aaaa", 0, success=True, t2i_prompt="A girl in forest"),
            _make_keyframe_version("v-002", "kf-001-aaaa", 1, success=True, t2i_prompt="A girl in bright forest"),
        ]
        with (
            patch("app.crud.video.video_keyframe.get_keyframes_by_run_id",
                  new_callable=AsyncMock, return_value=kfs),
            patch("app.crud.video.video_keyframe.get_keyframe_versions_by_keyframe_ids",
                  new_callable=AsyncMock, return_value=kf_versions),
        ):
            result = await tool._arun(artifact_type="keyframe", shot_number=3)
            assert "shot_3" in result
            assert "首帧" in result
            assert "v0" in result
            assert "v1" in result

    @pytest.mark.asyncio
    async def test_get_artifact_detail_music_list(self):
        from app.services.agent.video_edit.tools.get_artifact_detail import GetArtifactDetailTool
        tool = GetArtifactDetailTool(run_id="r-1", user_id="u-1")
        mgs = [_make_music_gen("music-aaaa1111")]
        with patch("app.crud.video.video_audio.get_music_generations_by_run_id",
                    new_callable=AsyncMock, return_value=mgs):
            result = await tool._arun(artifact_type="music")
            assert "配乐列表" in result
            assert "全曲BGM" in result

    @pytest.mark.asyncio
    async def test_get_project_status_tool(self):
        from app.services.agent.video_edit.tools.get_project_status import GetProjectStatusTool
        tool = GetProjectStatusTool(run_id="r-1", user_id="u-1")

        fake_snapshot = ProjectSnapshot(
            run_id="r-1", phase="completed", task_status="completed",
            total_shots=6, total_duration_sec=25.0,
            outline=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
            keyframes=StageStats(status="completed", total=6, succeeded=6, failed_items=[], versions_count={}),
            videos=StageStats(status="completed", total=6, succeeded=6, failed_items=[], versions_count={}),
        )
        with patch("app.services.agent.video_edit.snapshot_builder.build_snapshot_from_db",
                    new_callable=AsyncMock, return_value=fake_snapshot):
            result = await tool._arun()
            assert "completed" in result
            assert "总镜头数: 6" in result
            assert "25.0s" in result

    @pytest.mark.asyncio
    async def test_select_version_invalid_type(self):
        from app.services.agent.video_edit.tools.select_version import SelectVersionTool
        tool = SelectVersionTool(run_id="r-1", user_id="u-1")
        result = await tool._arun(entity_type="invalid", entity_uuid="x", version_uuid="y")
        assert "无效" in result

    @pytest.mark.asyncio
    async def test_regenerate_keyframes_no_match(self):
        from app.services.agent.video_edit.tools.regenerate_keyframes import RegenerateKeyframesTool
        tool = RegenerateKeyframesTool(run_id="r-1", user_id="u-1", thread_id="t-1")
        with patch("app.crud.video.video_keyframe.get_keyframes_by_run_id",
                    new_callable=AsyncMock, return_value=[]):
            result = await tool._arun(shot_numbers=[99], frame_position="first")
            assert "未找到" in result

    @pytest.mark.asyncio
    async def test_regenerate_videos_no_match(self):
        from app.services.agent.video_edit.tools.regenerate_videos import RegenerateVideosTool
        tool = RegenerateVideosTool(run_id="r-1", user_id="u-1", thread_id="t-1")
        with patch("app.crud.video.video_generation.get_video_generations_by_run_id",
                    new_callable=AsyncMock, return_value=[]):
            result = await tool._arun(shot_numbers=[99])
            assert "未找到" in result

    @pytest.mark.asyncio
    async def test_update_music_prompt_empty(self):
        from app.services.agent.video_edit.tools.update_music_prompt import UpdateMusicPromptTool
        tool = UpdateMusicPromptTool(run_id="r-1", user_id="u-1")
        result = await tool._arun(instruction="   ")
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_modify_outline_not_found(self):
        from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool
        tool = ModifyOutlineTool(run_id="r-1", user_id="u-1")
        with patch("app.crud.video.video_story.get_video_story_outline_by_run_id",
                    new_callable=AsyncMock, return_value=None):
            result = await tool._arun(instruction="改标题", fields=["title"])
            assert "尚未生成" in result

    @pytest.mark.asyncio
    async def test_modify_outline_success(self):
        from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool
        tool = ModifyOutlineTool(run_id="r-1", user_id="u-1")
        outline = _make_outline()
        with (
            patch("app.crud.video.video_story.get_video_story_outline_by_run_id",
                  new_callable=AsyncMock, return_value=outline),
            patch("app.crud.video.video_other.get_video_analysis_by_uuid",
                  new_callable=AsyncMock, return_value=None),
            patch("app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
                  new_callable=AsyncMock, return_value="新的描述"),
            patch("app.crud.video.video_story.update_video_story_outline",
                  new_callable=AsyncMock, return_value=True),
        ):
            result = await tool._arun(instruction="新的描述", fields=["description"])
            assert "✅" in result
            assert "description" in result

    @pytest.mark.asyncio
    async def test_modify_outline_style_updates_analysis(self):
        from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool

        tool = ModifyOutlineTool(run_id="r-1", user_id="u-1")
        outline = _make_outline(description="青春校园奔跑故事")
        analysis = SimpleNamespace(uuid="analysis-001", style_preferences='["真实摄影","微电影风格"]')

        async def fake_merge(*, base_prompt: str, instruction: str, asset_kind: str, detected_language=None):
            if asset_kind == "视觉风格指南":
                return "未来科技风格，冷色调，高科技校园，透明UI光效。"
            if asset_kind == "风格偏好短标签":
                return "未来科技, 冷色调, 高科技校园"
            if asset_kind == "故事大纲整体描述":
                return "青春校园故事调整为未来科技校园语境，保留奋斗主题。"
            return f"{base_prompt} / {instruction}"

        with (
            patch("app.crud.video.video_story.get_video_story_outline_by_run_id",
                  new_callable=AsyncMock, return_value=outline),
            patch("app.crud.video.video_other.get_video_analysis_by_uuid",
                  new_callable=AsyncMock, return_value=analysis),
            patch("app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
                  new_callable=AsyncMock, side_effect=fake_merge),
            patch("app.crud.video.video_story.update_video_story_outline",
                  new_callable=AsyncMock, return_value=True) as m_update_outline,
            patch("app.crud.video.video_other.update_video_analysis",
                  new_callable=AsyncMock, return_value=True) as m_update_analysis,
        ):
            result = await tool._arun(instruction="故事改成科技风格", fields=["style", "description"])

        assert "✅" in result
        outline_payload = m_update_outline.await_args.args[1]
        assert outline_payload["style_guide"].startswith("未来科技风格")
        assert "description" in outline_payload
        analysis_payload = m_update_analysis.await_args.args[1]
        assert analysis_payload["style_preferences"] == ["未来科技", "冷色调", "高科技校园"]

    @pytest.mark.asyncio
    async def test_modify_outline_style_visual_tuning_does_not_touch_description(self):
        from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool

        tool = ModifyOutlineTool(run_id="r-1", user_id="u-1")
        outline = _make_outline(description="青春校园奔跑故事")
        analysis = SimpleNamespace(uuid="analysis-001", style_preferences='["真实摄影","微电影风格"]')

        async def fake_merge(*, base_prompt: str, instruction: str, asset_kind: str, detected_language=None):
            if asset_kind == "视觉风格指南":
                return "真实摄影，微电影风格，冷色调更明显。"
            if asset_kind == "风格偏好短标签":
                return "真实摄影, 微电影风格, 冷色调"
            return f"{base_prompt} / {instruction}"

        with (
            patch("app.crud.video.video_story.get_video_story_outline_by_run_id",
                  new_callable=AsyncMock, return_value=outline),
            patch("app.crud.video.video_other.get_video_analysis_by_uuid",
                  new_callable=AsyncMock, return_value=analysis),
            patch("app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
                  new_callable=AsyncMock, side_effect=fake_merge),
            patch("app.crud.video.video_story.update_video_story_outline",
                  new_callable=AsyncMock, return_value=True) as m_update_outline,
            patch("app.crud.video.video_other.update_video_analysis",
                  new_callable=AsyncMock, return_value=True),
        ):
            result = await tool._arun(instruction="色调冷一点", fields=["style"])

        assert "✅" in result
        outline_payload = m_update_outline.await_args.args[1]
        assert outline_payload == {"style_guide": "真实摄影，微电影风格，冷色调更明显。"}


# =====================================================================
# 8. 端到端流程测试：snapshot → render → tool output
# =====================================================================

class TestEndToEnd:
    def test_snapshot_roundtrip(self):
        """构造 snapshot → 渲染 → 检查输出包含所有关键信息"""
        snap = ProjectSnapshot(
            run_id="e2e-run",
            phase="video_generation",
            task_status="running",
            total_shots=10,
            total_duration_sec=35.5,
            outline=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
            characters=StageStats(status="completed", total=3, succeeded=3, failed_items=[], versions_count={1: 2}),
            scenes=StageStats(status="completed", total=4, succeeded=4, failed_items=[], versions_count={}),
            keyframes=StageStats(status="completed", total=10, succeeded=10, failed_items=[], versions_count={3: 2, 5: 3}),
            videos=StageStats(status="partial", total=10, succeeded=8, failed_items=[5, 7], versions_count={5: 2}),
            narrations=StageStats(status="completed", total=8, succeeded=8, failed_items=[], versions_count={}),
            music=StageStats(status="completed", total=1, succeeded=1, failed_items=[], versions_count={}),
            segments=StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={}),
            assembly=StageStats(status="not_started", total=0, succeeded=0, failed_items=[], versions_count={}),
            pending_gate="after_shots",
        )
        text = render_snapshot_for_llm(snap)

        # 检查关键信息（渲染输出不含 run_id，见 snapshot_renderer）
        assert "video_generation" in text
        assert "总镜头数: 10" in text
        assert "35.5s" in text

        # 各阶段
        assert "✅ 1/1" in text  # outline
        assert "✅ 3/3" in text  # characters
        assert "✅ 10/10" in text  # keyframes
        assert "⚠️ 8/10" in text  # videos partial
        assert "shot_5" in text
        assert "shot_7" in text
        assert "shot_3: 2v" in text  # keyframe multi-version
        assert "shot_5: 3v" in text or "shot_5: 2v" in text  # versions
        assert "⏸️" in text
        assert "after_shots" in text
