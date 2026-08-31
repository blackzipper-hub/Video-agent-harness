"""Unit tests for outline stage workspace / artifact contracts (no LLM)."""

from app.contracts.artifacts.analysis_brief import AnalysisBriefArtifact
from app.contracts.artifacts.outline import OutlineAgentDraft, OutlineArtifact
from app.contracts.db.outline import OutlineDB
from app.contracts.llm.outline import OutlineLLMOutput
from app.services.agent.stage_runtime.paths import KIT_ROOT, SKILLS_ROOT
from app.services.agent.stage_runtime.workspace import (
    ensure_run_workspace,
    read_artifact_json,
    write_artifact_json,
    write_input_json,
)


def test_skills_outline_director_exists():
    skill = SKILLS_ROOT / "stages" / "outline" / "outline-director" / "SKILL.md"
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    assert "name: outline-director" in text
    assert "write_outline_artifact" in text


def test_contracts_layers_importable():
    assert OutlineLLMOutput is not None
    assert OutlineDB is not None
    assert OutlineArtifact is not None


def test_run_workspace_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.stage_runtime.workspace.run_workspace_dir",
        lambda tid, rid: tmp_path / tid / rid,
    )

    ensure_run_workspace("th", "run")
    brief = AnalysisBriefArtifact(
        target_duration_seconds=30,
        user_input="cat rain",
        mode="video_driven",
        content_category="short_drama",
    )
    write_input_json("th", "run", "analysis_brief.json", brief.model_dump(mode="json"))
    assert (tmp_path / "th" / "run" / "inputs" / "analysis_brief.json").is_file()

    draft = {
        "title": "t",
        "theme": "th",
        "description": "d",
        "key_message": "k",
        "total_duration": 30,
        "style_guide": "s",
        "chapters": [
            {
                "id": "ch0",
                "order": 0,
                "title": "a",
                "description": "b",
                "duration": 12,
                "enhancement_cues": [
                    {"type": "broll", "timestamp_hint": "0-3", "description": "rain roof wide"},
                    {"type": "broll", "timestamp_hint": "late", "description": "neon puddle CU"},
                ],
            },
            {
                "id": "ch1",
                "order": 1,
                "title": "c",
                "description": "d2",
                "duration": 18,
                "enhancement_cues": [
                    {"type": "hard_event", "timestamp_hint": "0-2", "description": "cat paws butterfly"},
                    {"type": "overlay", "timestamp_hint": "end", "description": "title card"},
                ],
            },
        ],
    }
    OutlineAgentDraft.model_validate(draft)
    art = OutlineArtifact(mode="video_driven", **draft)
    write_artifact_json("th", "run", "outline.json", art.model_dump(mode="json"))
    loaded = OutlineArtifact.model_validate(read_artifact_json("th", "run", "outline.json"))
    assert loaded.total_duration == 30
    assert len(loaded.chapters) == 2


def test_outline_to_llm_mode_bridge():
    art = OutlineArtifact(
        title="t",
        theme="th",
        description="d",
        key_message="k",
        total_duration=10,
        style_guide="s",
        chapters=[
            {
                "id": "ch0",
                "order": 0,
                "title": "a",
                "description": "b",
                "duration": 10,
                "enhancement_cues": [
                    {"type": "broll", "timestamp_hint": "0-3", "description": "shot a"},
                    {"type": "broll", "timestamp_hint": "mid", "description": "shot b"},
                ],
            }
        ],
    )
    llm = art.to_llm_mode()
    assert llm.structure.chapters[0].order == 0
    assert llm.total_duration == 10


def test_kit_json_schemas_exist():
    root = KIT_ROOT / "schemas"
    assert (root / "artifacts" / "outline.schema.json").is_file()
    assert (root / "artifacts" / "analysis_brief.schema.json").is_file()
    assert not (root / "llm").exists() or not any((root / "llm").iterdir())
    assert not (root / "db").exists() or not any((root / "db").iterdir())
