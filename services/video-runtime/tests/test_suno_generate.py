"""Native suno.generate path — keep the old music-agent Suno tool untouched."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.chat.v2.capability_loader import build_registry
from app.chat.v2.executors import CapabilityExecutor
from app.chat.v2.models import AgentRun, Task
from app.chat.v2.skill_catalog import SkillCatalog
from app.chat.v2.workflows import is_workflow_skill
from app.integrations.providers.suno_bridge import generate_suno_native
from app.tools.music.suno import _generate_music_with_suno_impl


def test_old_suno_tool_still_uses_has_lyrics_remap():
    assert "has_lyrics" in _generate_music_with_suno_impl.__code__.co_varnames
    assert "auto_lyrics" in _generate_music_with_suno_impl.__code__.co_varnames


def test_suno_generate_is_registered_local_service():
    registry = build_registry(include_platform=True)
    manifest = registry.get("suno.generate")
    assert manifest.executor == "local.service"
    assert manifest.service_target == "suno_generate"
    assert manifest.output_type == "music"
    assert registry.canonical_id("suno-generate") == "suno.generate"


def test_suno_song_is_instruction_helper_not_workflow():
    root = Path(__file__).resolve().parents[1] / "skills" / "external" / "suno-song"
    catalog = SkillCatalog([root.parent])
    catalog.discover()
    skill = catalog.load("suno-song")
    assert skill.contract is None
    assert not is_workflow_skill("suno-song")
    text = skill.instructions
    assert "suno.generate" in text
    assert "lyric-writer" in text
    assert "native-params" not in text
    refs = root / "references"
    assert not (refs / "native-params.md").is_file()
    assert (refs / "SOURCES.md").is_file()
    bitwize = refs / "bitwize"
    assert (bitwize / "skills" / "lyric-writer" / "UPSTREAM.md").is_file()
    assert (bitwize / "skills" / "lyric-refiner" / "UPSTREAM.md").is_file()
    assert (bitwize / "skills" / "pronunciation-specialist" / "UPSTREAM.md").is_file()
    assert (bitwize / "skills" / "lyric-reviewer" / "UPSTREAM.md").is_file()
    assert (bitwize / "skills" / "pre-generation-check" / "UPSTREAM.md").is_file()
    assert (bitwize / "skills" / "voice-checker" / "UPSTREAM.md").is_file()
    assert (bitwize / "skills" / "suno-engineer" / "UPSTREAM.md").is_file()
    assert (bitwize / "reference" / "suno" / "structure-tags.md").is_file()
    assert (bitwize / "reference" / "suno" / "v5-best-practices.md").is_file()
    engineer = (bitwize / "skills" / "suno-engineer" / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "Vocals First" in engineer or "vocals FIRST" in engineer
    writer = (bitwize / "skills" / "lyric-writer" / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "Don't rhyme a word with itself" in writer or "self-rhyme" in writer.lower()
    pron = (bitwize / "skills" / "pronunciation-specialist" / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "homograph" in pron.lower()
    qc = (bitwize / "skills" / "voice-checker" / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "Abstract Noun Stacking" in qc
    assert not (refs / "community").exists()
    sources = (refs / "SOURCES.md").read_text(encoding="utf-8")
    for blob in (text, sources):
        assert "prompt-crafter" not in blob
        assert "suno-song-creator" not in blob
        assert "NuNaught" not in blob


def test_suno_song_bundle_is_bitwize_only():
    """A bundled file is reachable via read_skill_resource whether SKILL.md links it or not."""
    root = Path(__file__).resolve().parents[1] / "skills" / "external" / "suno-song"
    catalog = SkillCatalog([root.parent])
    catalog.discover()
    resources = catalog.list_resources("suno-song")
    assert "references/bitwize/skills/suno-engineer/UPSTREAM.md" in resources
    assert not any("acestep" in path for path in resources)
    assert not (root / "references" / "acestep").exists()


class _FakeSunoService:
    last_kwargs: dict = {}

    def __init__(self, api_key=None):
        self.api_key = api_key

    async def generate_music(self, **kwargs):
        type(self).last_kwargs = dict(kwargs)
        return SimpleNamespace(
            success=True,
            clips=[SimpleNamespace(
                audio_url="http://localhost/files/song.mp3",
                title="Neon",
                clip_id="clip-from-suno",
                lyrics="[Verse 1 - close]\nCity lights\n[End]",
                duration=162,
            )],
            task_id="task-1",
            error=None,
        )


class _FakeRouter:
    async def route_tool_request(self, provider, tool_type, request_func):
        return await request_func("sk-test")


@pytest.fixture
def suno_native_mocks(monkeypatch):
    _FakeSunoService.last_kwargs = {}
    monkeypatch.setattr(
        "app.integrations.providers.suno_bridge.SunoService",
        _FakeSunoService,
    )

    async def _router():
        return _FakeRouter()

    monkeypatch.setattr(
        "app.integrations.providers.suno_bridge.get_account_router",
        _router,
    )
    monkeypatch.setattr(
        "app.integrations.providers.suno_bridge.ToolService.calculate_cost",
        lambda *a, **k: 0,
    )
    return _FakeSunoService


@pytest.mark.asyncio
async def test_generate_suno_native_passes_api_fields(suno_native_mocks):
    result = await generate_suno_native({
        "custom_mode": True,
        "lyrics": "[Verse 1 - close]\nCity lights\n[End]",
        "tags": "Female alto, intimate. Synth pop.",
        "title": "Neon Heart",
        "vocal_gender": "female",
        "exclude_styles": ["choir", "no autotune"],
        "target_duration": 45,
    })
    kwargs = suno_native_mocks.last_kwargs
    assert kwargs["custom_mode"] is True
    assert kwargs["make_instrumental"] is False
    assert kwargs["lyrics"].startswith("[Verse 1")
    assert kwargs["prompt"] is None
    assert kwargs["title"] == "Neon Heart"
    assert kwargs["vocal_gender"] == "f"
    assert kwargs["mv"] == "chirp-v5-5"
    assert "no choir" in kwargs["tags"]
    assert "no autotune" in kwargs["tags"]
    assert kwargs["generation_params"]["target_duration"] == 45
    assert result["audio_url"].endswith("song.mp3")
    assert result["uri"] == result["audio_url"]
    assert result["title"] == "Neon Heart"
    assert result["clip_id"] == "clip-from-suno"
    assert result["lyrics"].startswith("[Verse 1")
    assert result["duration"] == 162


@pytest.mark.asyncio
async def test_generate_suno_native_reads_dest_instrumental_field(suno_native_mocks):
    await generate_suno_native({
        "prompt": "dark pulse",
        "instrumental": True,
    })
    assert suno_native_mocks.last_kwargs["make_instrumental"] is True


@pytest.mark.asyncio
async def test_generate_suno_native_simple_mode_requires_prompt(suno_native_mocks):
    with pytest.raises(ValueError, match="custom_mode=false requires prompt"):
        await generate_suno_native({"custom_mode": False})


@pytest.mark.asyncio
async def test_generate_suno_native_custom_mode_requires_lyrics(suno_native_mocks):
    with pytest.raises(ValueError, match="custom_mode=true requires lyrics"):
        await generate_suno_native({"custom_mode": True})


@pytest.mark.asyncio
async def test_executor_suno_generate_returns_music_uri(monkeypatch):
    async def _fake(profile):
        assert profile["custom_mode"] is True
        return {
            "audio_url": "http://localhost/files/out.mp3",
            "uri": "http://localhost/files/out.mp3",
            "title": "Out",
            "summary": "Suno generated music via chirp-v5-5",
        }

    monkeypatch.setattr(
        "app.integrations.providers.suno_bridge.generate_suno_native",
        _fake,
    )
    registry = build_registry(include_platform=True)
    executor = CapabilityExecutor(object(), registry)
    run = AgentRun(
        thread_id="t1", project_id="p1", user_id="u1",
        objective="mv", idempotency_key="k1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="suno",
        capability_id="suno.generate", objective="sing",
        parameters={
            "custom_mode": True,
            "lyrics": "[Chorus - lift]\nGo\n[End]",
            "tags": "Male baritone. Indie rock.",
        },
    )
    result = await executor._run_local_service(
        run, task, [], f"idem-{uuid4()}",
        registry.get("suno.generate"),
    )
    assert result.status == "completed"
    assert result.artifact["uri"].endswith("out.mp3")
    assert result.artifact["title"] == "Out"
