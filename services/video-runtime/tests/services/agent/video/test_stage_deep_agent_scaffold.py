"""Smoke tests for multi-stage deep-agent scaffolding."""
from __future__ import annotations

from pathlib import Path

from app.services.agent.stage_runtime.paths import AGENT_SERVICE_ROOT, virtual_skills_stage
from app.services.agent.stage_runtime.registry import CREATIVE_STAGES


def test_virtual_skills_paths():
    assert virtual_skills_stage("outline") == "/kit/skills/stages/outline"
    assert virtual_skills_stage("scene") == "/kit/skills/stages/scene"


def test_all_stage_skills_exist():
    for spec in CREATIVE_STAGES:
        skill_root = AGENT_SERVICE_ROOT / "kit" / "skills" / "stages" / spec.skill_dir
        assert skill_root.is_dir(), f"missing {skill_root}"
        skills = list(skill_root.glob("*/SKILL.md"))
        assert skills, f"no SKILL.md under {skill_root}"


def test_wired_stage_packages_importable():
    from app.services.agent.video import analysis_stage, character_stage, outline_stage, scene_stage

    assert outline_stage.export_outline_inputs
    assert analysis_stage.export_analysis_inputs
    assert character_stage.export_character_inputs
    assert scene_stage.export_chapter_scene_inputs
