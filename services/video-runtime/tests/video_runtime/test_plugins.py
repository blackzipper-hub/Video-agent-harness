from __future__ import annotations

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.video_runtime.plugins import PluginDependencyError, VideoPluginRegistry
from app.video_runtime.capabilities import RuntimeCapabilityRegistry
from app.video_runtime.execution import CapabilityExecutionGateway
from app.video_runtime.security import CapabilityGrant, CapabilityGrantSigner
from app.video_runtime.models import MediaArtifactVersion
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.builtin_plugins.cuti_provider import CutiAtomicProviderPlugin
from app.orchestration.skills import ResolvedSkillRef, SkillContext
from app.video_runtime.builtin_plugins.media_core import resolve_tts_voice_id
from app.models.image_result import VoiceID


class RecordingPlugin:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def on_load(self, _context): self.events.append("load")
    async def before_plan(self, _context): self.events.append("before_plan")
    async def after_plan(self, _context, plan): return plan
    async def before_execute(self, _context, _envelope): self.events.append("before_execute")
    async def after_execute(self, _context, result): return result
    async def validate_artifact(self, _context, _artifact): return []
    async def on_artifact_committed(self, _context, _artifact): self.events.append("committed")
    async def on_unload(self, _context): self.events.append("unload")
    async def migrate(self, _context, from_version, to_version): self.events.append(f"migrate:{from_version}:{to_version}")


class ExecutableProviderPlugin(RecordingPlugin):
    def capability_handlers(self):
        async def generate(_envelope, payload):
            source = payload["source"]
            return MediaArtifactVersion(
                project_id=source["project_id"],
                type=source["type"],
                uri=f"generated-{source['id']}",
                metadata=dict(source.get("metadata") or {}),
            )
        return {"video.generate": generate}


class PluginRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = RecordingPlugin()
        module = types.ModuleType("cuti_test_plugin")
        module.plugin = self.plugin
        sys.modules[module.__name__] = module

    async def test_load_migrate_and_unload_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "video-plugin.yaml"
            manifest.write_text("""
id: test.provider
version: 1.0.0
api_version: v1
runtime_entrypoint: cuti_test_plugin:plugin
trusted: true
migration_version: 2
contributions:
  capabilities: [video.generate]
permissions:
  network_domains: [api.example.test]
  max_cost_usd: 3
""", encoding="utf-8")
            registry = VideoPluginRegistry()
            loaded = await registry.load(manifest)
            self.assertEqual(loaded.id, "test.provider")
            await registry.migrate("test.provider", 1)
            await registry.unload("test.provider")
            self.assertEqual(self.plugin.events, ["load", "migrate:1:2", "unload"])

    async def test_untrusted_plugin_is_denied_in_process(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "video-plugin.yaml"
            manifest.write_text("""
id: test.untrusted
version: 1.0.0
runtime_entrypoint: cuti_test_plugin:plugin
trusted: false
""", encoding="utf-8")
            with self.assertRaises(PermissionError):
                await VideoPluginRegistry().load(manifest)

    async def test_upgrade_runs_migration_before_replacing_plugin(self):
        replacement = RecordingPlugin()
        replacement_module = types.ModuleType("cuti_test_plugin_v2")
        replacement_module.plugin = replacement
        sys.modules[replacement_module.__name__] = replacement_module
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.yaml"
            first.write_text("""
id: test.provider
version: 1.0.0
runtime_entrypoint: cuti_test_plugin:plugin
trusted: true
migration_version: 1
""", encoding="utf-8")
            second = Path(directory) / "second.yaml"
            second.write_text("""
id: test.provider
version: 2.0.0
runtime_entrypoint: cuti_test_plugin_v2:plugin
trusted: true
migration_version: 2
""", encoding="utf-8")
            registry = VideoPluginRegistry()
            await registry.load(first)
            upgraded = await registry.upgrade(second)
            self.assertEqual(upgraded.version, "2.0.0")
            self.assertIs(registry.get("test.provider").implementation, replacement)
            self.assertEqual(replacement.events, ["migrate:1:2", "load"])
            self.assertEqual(self.plugin.events, ["load", "unload"])

    async def test_missing_dependency_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "video-plugin.yaml"
            manifest.write_text("""
id: test.dependent
version: 1.0.0
runtime_entrypoint: cuti_test_plugin:plugin
trusted: true
dependencies: [missing.provider]
""", encoding="utf-8")
            with self.assertRaises(PluginDependencyError):
                await VideoPluginRegistry().load(manifest)

    async def test_registered_capability_uses_signed_execution_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "video-plugin.yaml"
            manifest.write_text("""
id: test.provider
version: 1.0.0
runtime_entrypoint: cuti_test_plugin:plugin
trusted: true
contributions:
  capabilities: [video.generate]
permissions:
  network_domains: [api.example.test]
  max_cost_usd: 3
""", encoding="utf-8")
            plugins = VideoPluginRegistry()
            await plugins.load(manifest)
            signer = CapabilityGrantSigner(b"a sufficiently long server-owned secret")
            registry = RuntimeCapabilityRegistry(
                CapabilityExecutionGateway(signer, plugins),
            )

            async def operation(envelope, payload):
                envelope.assert_domain("api.example.test")
                return {"artifact": payload["prompt"]}

            dispose = registry.register(
                plugin_id="test.provider",
                capability="video.generate",
                operation=operation,
            )
            token = signer.issue(CapabilityGrant(
                project_id="project-1",
                session_id="session-1",
                user_id="user-1",
                plugin_id="test.provider",
                capability="video.generate",
                allowed_capabilities=["video.generate"],
                allowed_domains=["api.example.test"],
                max_cost_usd=2,
                timeout_seconds=10,
                idempotency_key="request-1",
                audit_id="audit-1",
                nonce="nonce-1",
                expires_at=int(time.time()) + 60,
            ))
            self.assertEqual(
                await registry.execute(
                    grant_token=token,
                    project_id="project-1",
                    session_id="session-1",
                    user_id="user-1",
                    capability="video.generate",
                    payload={"prompt": "a coat"},
                ),
                {"artifact": "a coat"},
            )
            dispose()
            with self.assertRaises(LookupError):
                await registry.execute(
                    grant_token=token,
                    project_id="project-1",
                    session_id="session-1",
                    user_id="user-1",
                    capability="video.generate",
                    payload={},
                )

    async def test_loaded_provider_handler_executes_queued_incremental_build(self):
        executable = ExecutableProviderPlugin()
        module = types.ModuleType("cuti_executable_plugin")
        module.plugin = executable
        sys.modules[module.__name__] = module
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "video-plugin.yaml"
            manifest.write_text("""
id: test.executable
version: 1.0.0
runtime_entrypoint: cuti_executable_plugin:plugin
trusted: true
contributions:
  capabilities: [video.generate]
permissions:
  max_cost_usd: 1
""", encoding="utf-8")
            registry = VideoPluginRegistry()
            await registry.load(manifest)
            runtime = VideoBuildRuntime(plugins=registry)
            runtime.configure_plugin_execution(
                CapabilityGrantSigner(b"a sufficiently long server-owned secret"),
            )
            project, _version = await runtime.create_project(
                user_id="user-1", title="Plugin build",
            )
            artifact = await runtime.add_artifact(MediaArtifactVersion(
                project_id=project.id,
                type="video",
                metadata={"rebuild_capability": "video.generate"},
            ))
            current = await runtime.repo.get_project(project.id)
            plan = await runtime.preview_change(
                project_id=project.id,
                description="rebuild video",
                target_artifact_version_ids=[artifact.id],
            )
            build = await runtime.apply_rebuild(
                project_id=project.id,
                plan_id=plan.id,
                base_project_version_id=current.current_version_id,
                idempotency_key="plugin-build-1",
                session_id="session-1",
                user_id="user-1",
            )
            while (await runtime.repo.get_build(project.id, build.id)).status in {
                "queued", "running",
            }:
                await __import__("asyncio").sleep(0)
            completed = await runtime.repo.get_build(project.id, build.id)
            self.assertEqual(completed.status, "completed")
            selected = await runtime.repo.current_artifacts(project.id)
            self.assertEqual(selected[0].uri, f"generated-{artifact.id}")
            self.assertIn("before_execute", executable.events)
            self.assertIn("committed", executable.events)
            await runtime.close()

    def test_runtime_tts_maps_descriptive_voice_to_supported_provider_id(self):
        self.assertEqual(
            resolve_tts_voice_id(
                "young-adult-neutral-english-male",
                "At the Academy of Bloodlines, every student waited.",
            ),
            VoiceID.ENGLISH_MAGNETIC_MALE_2.value,
        )
        self.assertEqual(
            resolve_tts_voice_id("温和中文男声", "龙族已经灭绝。"),
            VoiceID.CHINESE_MALE_ANNOUNCER.value,
        )

    async def test_cuti_atomic_provider_adapter_reuses_existing_executor_contract(self):
        captured = {}

        async def fake_atomic(**kwargs):
            captured.update(kwargs)
            return "provider-task-1", {
                "title": "Rebuilt shot",
                "summary": "done",
                "uri": "https://cdn.example.test/shot.mp4",
                "metadata": {"provider_used": "mock", "model": "mock-video-v1"},
            }

        plugin = CutiAtomicProviderPlugin(fake_atomic)
        source = MediaArtifactVersion(
            project_id="project-1",
            artifact_id="shot-1",
            type="video",
            title="Shot one",
            metadata={
                "prompt": "A hero enters",
                "rebuild_capability": "atomic.video.generate",
                "skill_context": SkillContext(
                    instructions="Keep the hero identity stable.",
                    applied_skills=[ResolvedSkillRef(
                        skill_id="video-director",
                        version="1.0.0",
                        content_hash="abc123",
                        source="declared",
                    )],
                ).model_dump(mode="json"),
            },
        )
        envelope = CapabilityGrant(
            project_id="project-1",
            session_id="session-1",
            user_id="user-1",
            plugin_id="cuti.atomic-providers",
            capability="atomic.video.generate",
            allowed_capabilities=["atomic.video.generate"],
            max_cost_usd=1,
            timeout_seconds=30,
            idempotency_key="rebuild-shot-1",
            audit_id="audit-1",
            nonce="nonce-1",
            expires_at=int(time.time()) + 60,
        )
        from app.video_runtime.security import CapabilityExecutionEnvelope
        result = await plugin.capability_handlers()["atomic.video.generate"](
            CapabilityExecutionEnvelope(envelope),
            {
                "build": {"id": "build-1"},
                "source": source.model_dump(mode="json"),
                "completed_replacements": {},
            },
        )
        self.assertEqual(captured["task"].capability_id, "atomic.video.generate")
        self.assertIn("A hero enters", captured["task"].parameters["prompt"])
        self.assertIn("Keep the hero identity stable", captured["task"].parameters["prompt"])
        self.assertEqual(captured["task"].resolved_skills[0].skill_id, "video-director")
        self.assertEqual(result.artifact_id, "shot-1")
        self.assertEqual(result.provenance["remote_operation_id"], "provider-task-1")
        self.assertEqual(result.provenance["skills"][0]["skill_id"], "video-director")
