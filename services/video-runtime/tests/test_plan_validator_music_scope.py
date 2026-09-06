"""Scope validation must allow MV music spine (确认生成原创配乐)."""
from __future__ import annotations

from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.models import AgentRun, ChatMessage, PlanPatch, PlannedTask, RunSnapshot
from app.orchestration.policy.plan_validator import PlanValidator


def _validator() -> PlanValidator:
    return PlanValidator(
        CapabilityRegistry(),
        max_revisions=20,
        max_tasks=50,
        max_parallel_generation_tasks=3,
    )


def _music_patch(base_revision: int = 0) -> PlanPatch:
    return PlanPatch(
        base_revision=base_revision,
        reason="music spine",
        add_tasks=[
            PlannedTask(
                capability_id="suno.generate",
                objective="generate 30s original BGM",
                parameters={
                    "prompt": "original instrumental score",
                    "thread_id": "t",
                    "duration": 30,
                },
            )
        ],
    )


def test_mv_goal_allows_music_generate():
    run = AgentRun(
        thread_id="t",
        project_id="t",
        user_id="u",
        objective="$mv\n\n生成个30s mv",
        idempotency_key="k-mv",
        activated_skills=["mv"],
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[ChatMessage(role="user", content=run.objective, run_id=run.id)],
    )
    _validator().validate(snapshot, _music_patch())


def test_confirm_original_score_not_wiped_by_continuation_marker():
    """'确认' used to rewrite scope back to the original goal and drop 配乐."""
    run = AgentRun(
        thread_id="t",
        project_id="t",
        user_id="u",
        objective="做个30秒视频",
        idempotency_key="k-confirm",
        activated_skills=["mv"],
        current_revision=2,
        goal_started_revision=0,
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[
            ChatMessage(role="user", content="做个30秒视频", run_id=run.id),
            ChatMessage(role="assistant", content="请确认生成原创配乐", run_id=run.id),
            ChatMessage(role="user", content="确认生成原创配乐", run_id=run.id),
        ],
    )
    _validator().validate(snapshot, _music_patch(base_revision=2))


def test_plain_video_still_blocks_unsolicited_music():
    run = AgentRun(
        thread_id="t",
        project_id="t",
        user_id="u",
        objective="做个30秒短片",
        idempotency_key="k-plain",
        activated_skills=["workflow-keyframe-pipeline"],
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[ChatMessage(role="user", content=run.objective, run_id=run.id)],
    )
    import pytest

    with pytest.raises(ValueError, match="exceeds the latest user request scope"):
        _validator().validate(snapshot, _music_patch())


def test_add_captions_authorizes_captioned_video_output():
    run = AgentRun(
        thread_id="t-caption",
        project_id="t-caption",
        user_id="u",
        objective="增加字幕",
        idempotency_key="k-caption",
        activated_skills=["cuti-product-workflow"],
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[ChatMessage(role="user", content="增加字幕", run_id=run.id)],
    )
    patch = PlanPatch(
        base_revision=0,
        reason="burn validated subtitles into the selected video",
        add_tasks=[PlannedTask(
            capability_id="media.subtitle_burn",
            objective="render a captioned MP4",
            parameters={
                "style_preset": "clean",
                "position": "bottom-safe",
            },
        )],
    )

    _validator()._validate_user_scope(snapshot, patch)


def test_add_english_subtitles_authorizes_captioned_video_output():
    run = AgentRun(
        thread_id="t-subtitle",
        project_id="t-subtitle",
        user_id="u",
        objective="Add English subtitles",
        idempotency_key="k-subtitle",
    )
    snapshot = RunSnapshot(
        run=run,
        messages=[ChatMessage(
            role="user", content="Add English subtitles", run_id=run.id,
        )],
    )
    patch = PlanPatch(
        base_revision=0,
        reason="burn subtitles",
        add_tasks=[PlannedTask(
            capability_id="media.subtitle_burn",
            objective="render a captioned MP4",
        )],
    )

    _validator()._validate_user_scope(snapshot, patch)
