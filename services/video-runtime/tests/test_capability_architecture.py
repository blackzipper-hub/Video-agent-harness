from pathlib import Path

from app.capabilities.manifests.platform import platform_capabilities
from app.capabilities.models import default_capabilities
from app.chat.v2.skill_catalog import SkillCatalog
from app.integrations.providers.manifests import provider_capabilities
from app.legacy.video_pipeline.capability import legacy_video_pipeline_capability


def test_platform_atomic_capabilities_are_not_skill_packages():
    platform = platform_capabilities()
    assert len(platform) == 33
    assert len({item.id for item in platform}) == 33

    root = Path(__file__).resolve().parents[1] / "skills" / "system"
    skill_names = {item.name for item in SkillCatalog([root]).discover()}
    capability_aliases = {item.skill_name for item in platform}
    assert skill_names.isdisjoint(capability_aliases)

    atomic = [item for item in platform if item.id.startswith("atomic.")]
    assert len(atomic) == 4
    assert all(item.executor == "atomic.direct" for item in atomic)
    assert all(item.target_agent is None for item in atomic)
    assert all(item.parameters_schema.get("required") == ["prompt"] for item in atomic)


def test_provider_and_legacy_capabilities_have_dedicated_sources():
    providers = provider_capabilities()
    assert {item.id for item in providers} == {
        "api.provider.generate",
        "api.ark_protocol.generate",
        "open_montage.tool.invoke",
    }
    legacy = legacy_video_pipeline_capability()
    assert legacy.id == "video.pipeline.generate"
    assert legacy.mode == "master"

    all_items = default_capabilities()
    assert len(all_items) == 37
    assert len({item.id for item in all_items}) == 37
