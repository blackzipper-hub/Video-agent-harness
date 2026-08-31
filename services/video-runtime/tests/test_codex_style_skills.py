from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from app.chat.config import Settings
from app.chat.v2.agent_trace import trace_agent_event
from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.capability_loader import build_registry
from app.chat.v2.coordinator import DeepAgentCoordinator, hide_artifact_media_urls
from app.chat.v2.skill_catalog import SkillCatalog
from app.chat.v2.skill_install import install_skill_directory
from app.chat.v2.tools import build_coordinator_tools


def write_instruction_skill(root: Path, name: str = "video-director") -> Path:
    skill = root / name
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "assets").mkdir()
    (skill / "agents").mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: video-director
description: >-
  Direct a multi-stage multimodal video workflow. Use for stories, images,
  music, keyframes, shot video generation, revision, and final assembly.
metadata:
  version: "1.0"
  related_skills:
    - story-review
---

# Video Director

First inspect the project. Generate an outline, then characters and scenes.
Wait for real artifacts between dependent phases. Read references/stages.md.
""",
        encoding="utf-8",
    )
    (skill / "references" / "stages.md").write_text(
        "# Stages\nUse outline.generate before character.generate.\n",
        encoding="utf-8",
    )
    (skill / "scripts" / "inspect.py").write_text("print('ok')\n", encoding="utf-8")
    (skill / "assets" / "prompt.txt").write_text("cinematic\n", encoding="utf-8")
    (skill / "agents" / "openai.yaml").write_text(
        "interface:\n  display_name: Video Director\n",
        encoding="utf-8",
    )
    return skill


def test_pure_markdown_skill_uses_codex_structure_and_no_contract(tmp_path):
    skill_dir = write_instruction_skill(tmp_path)
    catalog = SkillCatalog([tmp_path])
    skill = catalog.load("video-director")

    assert skill.contract is None
    assert skill.metadata.metadata["version"] == "1.0"
    assert skill.metadata.metadata["interface"]["display_name"] == "Video Director"
    assert "multi-stage" in skill.metadata.description
    assert "references/stages.md" in catalog.list_resources("video-director")
    assert "outline.generate" in catalog.read_resource(
        "video-director", "references/stages.md"
    )
    assert build_registry(catalog).list() == []
    assert skill_dir.name == skill.metadata.name


def test_skill_catalog_migrates_stale_legacy_system_skill_path():
    from app.chat.v2.skill_catalog import SkillMetadata

    agent_root = Path(__file__).resolve().parents[1]
    canonical = agent_root / "skills" / "system" / "workflow-short-drama" / "SKILL.md"
    legacy = agent_root / "app" / "chat" / "skills" / ".system" / "workflow-short-drama" / "SKILL.md"
    assert canonical.is_file()
    assert not legacy.exists()
    catalog = SkillCatalog([canonical.parent.parent])
    catalog._metadata["workflow-short-drama"] = SkillMetadata(
        name="workflow-short-drama",
        description="migrated workflow",
        path=legacy,
        trust_level="trusted",
    )

    loaded = catalog.load("workflow-short-drama")

    assert loaded.metadata.path == canonical


def test_pure_markdown_skill_installs_without_becoming_capability(tmp_path):
    source_root = tmp_path / "source"
    source = write_instruction_skill(source_root)
    external = tmp_path / "external"
    external.mkdir()
    catalog = SkillCatalog([external])
    registry = CapabilityRegistry([])

    installed = install_skill_directory(
        source,
        external,
        catalog=catalog,
        registry=registry,
    )

    assert installed == "video-director"
    assert catalog.load(installed).contract is None
    assert registry.list() == []


@pytest.mark.asyncio
async def test_coordinator_tools_progressively_load_skill_and_reference(tmp_path):
    write_instruction_skill(tmp_path)
    catalog = SkillCatalog([tmp_path])

    class Services:
        async def project_snapshot(self, run_id: str) -> dict:
            return {"run": {"id": run_id, "current_revision": 0}}

        async def commit_agent_patch(self, run_id: str, patch) -> dict:
            return {"accepted": True}

    tools = {
        item.name: item
        for item in build_coordinator_tools(
            Services(),
            CapabilityRegistry([]),
            catalog,
        )
    }
    metadata = await tools["list_skills"].ainvoke({})
    loaded = await tools["load_skill"].ainvoke({"name": "video-director"})
    reference = await tools["read_skill_resource"].ainvoke({
        "name": "video-director",
        "path": "references/stages.md",
    })

    assert metadata[0]["name"] == "video-director"
    assert "First inspect the project" in loaded["instructions"]
    assert "scripts/inspect.py" in loaded["resources"]
    assert "outline.generate" in reference["content"]


def test_multimodal_video_director_is_instruction_only_system_skill():
    root = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "system"
    )
    catalog = SkillCatalog([root])
    skill = catalog.load("multimodal-video-director")

    assert skill.contract is None
    assert "iterative creative workflow" in skill.instructions
    assert "references/workflows.md" in catalog.list_resources(skill.metadata.name)
    assert "video.pipeline.generate" in catalog.read_resource(
        skill.metadata.name,
        "references/workflows.md",
    )
    with pytest.raises(LookupError):
        build_registry(catalog).get("multimodal-video-director")


def test_seedance2_skill_is_upstream_instruction_only():
    root = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "external"
    )
    skill = SkillCatalog([root]).load("seedance2")

    assert skill.contract is None
    assert "scripts/seedance.py" in skill.instructions
    assert "ARK_API_KEY" in skill.instructions
    assert "Volcengine Ark" in skill.instructions or "Ark API" in skill.instructions
    # Must remain upstream-compatible: no Cuti platform forks in the Skill body.
    assert "video_gen.generate" not in skill.instructions
    assert "cuti-contract" not in skill.instructions
    assert not (root / "seedance2" / "run" / "__main__.py").exists()


def test_explicit_skill_invocation_injects_instructions(tmp_path):
    write_instruction_skill(tmp_path)
    catalog = SkillCatalog([tmp_path])
    coordinator = DeepAgentCoordinator(
        runtime=object(),
        host=object(),
        capabilities=CapabilityRegistry([]),
        skills=catalog,
    )

    context = coordinator._explicit_skill_context({
        "messages": [
            {"role": "user", "content": "Please use $video-director for this project."}
        ]
    })

    assert "Explicitly activated Skill `video-director`" in context
    assert "First inspect the project" in context
    assert "references/stages.md" in context


def test_confirmed_workflow_is_injected_without_repeating_dollar_command():
    root = Path(__file__).resolve().parents[1]
    catalog = SkillCatalog([
        root / "skills" / "system",
        root / "skills" / "builtin",
        root / "skills" / "external",
    ])
    coordinator = DeepAgentCoordinator(
        runtime=object(),
        host=object(),
        capabilities=CapabilityRegistry([]),
        skills=catalog,
    )

    context = coordinator._explicit_skill_context({
        "run": {"activated_skills": ["workflow-short-drama"]},
        "messages": [{"role": "user", "content": "确认，继续生成"}],
    })

    assert "Explicitly activated Skill `workflow-short-drama`" in context
    assert "Skip" in context and "keyframe.generate" in context


def test_coordinator_streams_only_root_model_events():
    assert DeepAgentCoordinator._is_root_model_event({
        "metadata": {"langgraph_checkpoint_ns": ":root-task"}
    })
    assert not DeepAgentCoordinator._is_root_model_event({
        "metadata": {
            "langgraph_checkpoint_ns": ":root-task|tools:subagent-task|model:call"
        }
    })


def test_coordinator_hides_generated_media_urls_from_chat():
    response = (
        "新的字幕版 MP4 已完成。播放/下载地址："
        "http://127.0.0.1:19004/files/media/run/captioned.mp4 。"
    )

    sanitized = hide_artifact_media_urls(response)

    assert "http://" not in sanitized
    assert "播放/下载地址" not in sanitized
    assert "右侧创作区" in sanitized


@pytest.mark.asyncio
async def test_skill_http_get_returns_rate_limit_to_agent(monkeypatch):
    async def fake_get(self, url, headers):
        return httpx.Response(
            429,
            headers={"retry-after": "10", "content-type": "application/json"},
            content=b'{"message":"rate limited"}',
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    tools = {
        item.name: item
        for item in build_coordinator_tools(object(), CapabilityRegistry([]))
    }

    result = await tools["skill_http_get"].ainvoke({
        "url": "https://api.crossref.org/works",
        "headers": None,
    })

    assert result["status_code"] == 429
    assert result["ok"] is False
    assert result["retry_after"] == "10"


@pytest.mark.asyncio
async def test_skill_http_get_rejects_file_large_tool_results_without_raising():
    from app.chat.v2.tools import build_coordinator_tools, normalize_skill_http_url

    assert normalize_skill_http_url("/files/a.mp4").endswith("/files/a.mp4")
    assert normalize_skill_http_url("/files/a.mp4").startswith("http")

    tools = {
        item.name: item
        for item in build_coordinator_tools(object(), CapabilityRegistry([]))
    }
    result = await tools["skill_http_get"].ainvoke({
        "url": "file:///large_tool_results/call_ZwkbxMcQseDs8lbSynBUE581",
        "headers": None,
    })
    assert result["ok"] is False
    assert "absolute http/https" in result["error"]
    assert "get_project_snapshot" in result["hint"]


def test_agent_trace_is_separate_jsonl_and_redacts_secrets(tmp_path):
    path = tmp_path / "deep-agent-trace.jsonl"
    settings = Settings(
        DEEP_AGENT_V2_TRACE_ENABLED=True,
        DEEP_AGENT_V2_TRACE_LOG_PATH=str(path),
    )

    trace_agent_event(
        settings,
        "tool.started",
        run_id="run-1",
        tool="skill_http_get",
        input={"url": "https://example.com", "authorization": "Bearer secret"},
    )
    for handler in logging.getLogger("cuti.deep_agent_v2.trace").handlers:
        handler.flush()
    content = path.read_text(encoding="utf-8")

    assert '"event":"tool.started"' in content
    assert '"run_id":"run-1"' in content
    assert "Bearer secret" not in content
    assert "[REDACTED]" in content

