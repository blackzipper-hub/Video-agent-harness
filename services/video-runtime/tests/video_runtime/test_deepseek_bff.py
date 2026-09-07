from __future__ import annotations

import asyncio
import sys
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.video_runtime.api import set_runtime
from app.video_runtime.deepseek_bff import (
    _compat_event,
    _load_run_context,
    _sign_uploaded_file,
    _UI_DEFAULTS_SEPARATOR,
    router,
    set_deepseek_client,
    studio_router,
)
from app.video_runtime.deepseek_client import DeepSeekHarnessError
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.models import Build, ProjectIntent, RebuildPlan
from app.video_runtime.skills import VideoSkillRuntime


class FakeDeepSeekClient:
    def __init__(self) -> None:
        self.events: dict[str, list[dict]] = {}
        self.prompts: list[tuple[str, str]] = []
        self.missing_sessions: set[str] = set()
        self.session_count = 0

    async def list_sessions(self) -> list[dict]:
        return [{"sessionId": session_id} for session_id in self.events]

    async def create_session(self, **options) -> str:
        self.session_count += 1
        session_id = options.get("session_id") or f"dsh-session-{self.session_count}"
        self.events.setdefault(session_id, [])
        return session_id

    async def prompt(self, session_id: str, text: str, **_options) -> None:
        self.prompts.append((session_id, text))
        self.events[session_id] = [
            {"type": "turn/start", "seq": 0, "time": 1000, "data": {"turn": 1}},
            {"type": "user/message", "seq": 1, "time": 1001, "data": {
                "content": [{"type": "text", "text": text}],
                "source": {"kind": "user"},
            }},
            {"type": "user/message", "seq": 2, "time": 1002, "data": {
                "content": [{"type": "text", "text": "internal context"}],
                "source": {"kind": "plugin", "plugin": "test-context"},
            }},
            {"type": "assistant/message", "seq": 3, "time": 1003, "data": {
                "message": {"content": [{"type": "text", "text": "Project opened"}]},
            }},
            {"type": "turn/end", "seq": 4, "time": 1004, "data": {
                "turn": 1, "reason": {"kind": "complete"},
            }},
        ]

    async def history(self, session_id: str, **_options) -> dict:
        if session_id in self.missing_sessions:
            raise DeepSeekHarnessError(
                f'session.history failed: session-not-found: session "{session_id}" not found',
            )
        return {
            "events": [{"event": item} for item in self.events[session_id]],
            "hasMore": False,
        }

    async def cancel(self, _session_id: str) -> None:
        return None


class DeepSeekCompatibilityBffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = VideoBuildRuntime()
        self.deepseek = FakeDeepSeekClient()
        set_runtime(self.runtime)
        set_deepseek_client(self.deepseek)
        subapp = FastAPI()
        subapp.include_router(router)
        subapp.include_router(studio_router)
        app = FastAPI()
        app.mount("/chat-v1/service", subapp)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        set_deepseek_client(None)

    def test_stop_cancels_build_and_resume_requires_explicit_confirmation(self) -> None:
        response = self.client.post("/chat-v1/service/v2/runs", json={
            "objective": "Make a video", "idempotency_key": "stop-create",
        })
        project_id = response.json()["data"]["project_id"]
        project = asyncio.run(self.runtime.repo.get_project(project_id))
        build = Build(project_id=project_id, plan_id="test-plan",
                      base_project_version_id=project.current_version_id,
                      idempotency_key="test-build", status="waiting_agent")
        self.runtime.repo.builds[build.id] = build
        base = f"/chat-v1/service/v2/runs/{project_id}"
        stopped = self.client.post(base + "/cancel")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["data"]["status"], "cancelled")
        self.assertEqual(self.runtime.repo.builds[build.id].status, "cancelled")
        denied = self.client.post(base + "/messages", json={
            "content": "continue", "idempotency_key": "unconfirmed",
        })
        self.assertEqual(denied.status_code, 409)
        resumed = self.client.post(base + "/resume", json={
            "content": "continue", "idempotency_key": "confirmed",
        })
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(self.runtime.repo.builds[build.id].status, "queued")
        self.assertIn("Do not create a replacement build", self.deepseek.prompts[-1][1])

    def test_old_v2_paths_drive_deepseek_session_and_video_project(self) -> None:
        uploaded_url = "https://example.test/hero.png"
        created = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "Make a trailer",
                "idempotency_key": "request-1",
                "user_option": {
                    "duration": 15,
                    "aspect_ratio": "9:16",
                    "resolution": "720p",
                    "video_generation_tool": "seedance_2_i2v",
                },
                "input_files": [{
                    "type": "image",
                    "url": uploaded_url,
                    "metadata": {
                        "upload_receipt": _sign_uploaded_file("local-user", "image", uploaded_url),
                    },
                }],
            },
        )
        self.assertEqual(created.status_code, 200)
        run = created.json()["data"]
        self.assertEqual(run["thread_id"], "dsh-session-1")
        self.assertIn(run["project_id"], self.deepseek.prompts[0][1])
        prompt = self.deepseek.prompts[0][1]
        self.assertIn("video_project_plan", prompt)
        self.assertIn("video_project_build", prompt)
        self.assertIn("video_workflow_list", prompt)
        self.assertIn("video_workflow_load", prompt)
        self.assertIn("video_skill_load", prompt)
        self.assertIn("video_skill_read_resource", prompt)
        self.assertNotIn("load_skill", prompt)
        self.assertNotIn("Set VideoSpec.workflow_id exactly to \"cuti.seedance-story\"", prompt)
        self.assertIn("Do not create another project", prompt)
        self.assertIn('"duration": 15', prompt)
        self.assertNotIn(uploaded_url, prompt)
        self.assertIn("source:", prompt)
        self.assertIn("never normalize Seedance 2.5 to Seedance 2.0", prompt)
        self.assertIn("never send JSON null", prompt)

        snapshot = self.client.get(
            f"/chat-v1/service/v2/runs/{run['id']}",
        ).json()["data"]
        self.assertEqual(snapshot["run"]["status"], "completed")
        self.assertEqual(snapshot["run"]["user_option"]["duration"], 15)
        self.assertEqual(
            snapshot["run"]["input_files"][0]["url"],
            "https://example.test/hero.png",
        )
        self.assertEqual(len(snapshot["messages"]), 2)
        self.assertEqual(snapshot["messages"][0]["content"], "Make a trailer")
        self.assertEqual(snapshot["messages"][-1]["content"], "Project opened")
        self.assertEqual(
            self.client.get("/chat-v1/service/v2/runs").json()["data"][0]["id"],
            run["id"],
        )
        repeated = self.client.post(
            "/chat-v1/service/v2/runs",
            json={"objective": "Make a trailer", "idempotency_key": "request-1"},
        ).json()["data"]
        self.assertEqual(repeated["id"], run["id"])
        self.assertEqual(repeated["user_option"]["duration"], 15)
        self.assertEqual(len(self.deepseek.prompts), 1)

    def test_list_runs_keeps_project_when_deepseek_session_was_removed(self) -> None:
        created = self.client.post(
            "/chat-v1/service/v2/runs",
            json={"objective": "Make a trailer", "idempotency_key": "missing-session-1"},
        ).json()["data"]
        self.deepseek.missing_sessions.add(created["thread_id"])

        response = self.client.get("/chat-v1/service/v2/runs")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"][0]["id"], created["id"])
        self.assertEqual(response.json()["data"][0]["thread_id"], created["thread_id"])

    def test_orphaned_create_route_gets_a_fresh_deepseek_session(self) -> None:
        replacement = FakeDeepSeekClient()
        replacement.events["orphaned-route-session"] = [{
            "type": "assistant/message",
            "seq": 0,
            "time": 1000,
            "data": {"message": {"content": [{"type": "text", "text": "stale"}]}},
        }]
        self.deepseek = replacement
        set_deepseek_client(replacement)

        response = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "Restart an orphaned Create route",
                "idempotency_key": "orphaned-route-request",
                "thread_id": "orphaned-route-session",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()["data"]
        self.assertEqual(run["thread_id"], "dsh-session-1")
        self.assertNotEqual(run["thread_id"], "orphaned-route-session")
        self.assertEqual(replacement.prompts[0][0], run["thread_id"])

    def test_unused_create_route_keeps_its_requested_session_id(self) -> None:
        response = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "Start from a fresh Create route",
                "idempotency_key": "fresh-route-request",
                "thread_id": "fresh-route-session",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()["data"]
        self.assertEqual(run["thread_id"], "fresh-route-session")
        self.assertEqual(self.deepseek.prompts[0][0], "fresh-route-session")

    def test_old_studio_paths_keep_thread_based_navigation(self) -> None:
        created = self.client.post(
            "/chat-v1/service/studio/projects",
            json={"objective": "Make a studio trailer"},
        )
        self.assertEqual(created.status_code, 200)
        project = created.json()["data"]["project"]
        thread_id = project["thread_id"]

        snapshot = self.client.get(
            f"/chat-v1/service/studio/projects/{thread_id}",
        ).json()["data"]
        self.assertEqual(snapshot["run"]["id"], project["id"])

        skills = self.client.get(
            f"/chat-v1/service/studio/projects/{thread_id}/skills",
        )
        self.assertEqual(skills.status_code, 200)
        self.assertEqual(skills.json()["data"], [])

        updated = self.client.post(
            f"/chat-v1/service/studio/projects/{thread_id}/commands",
            json={"objective": "Make it shorter"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["data"]["project"]["thread_id"], thread_id)
        self.assertEqual(len(self.deepseek.prompts), 2)

    def test_skill_catalog_structured_selection_and_project_locks(self) -> None:
        catalog = self.client.get("/chat-v1/service/v2/skills")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        names = {item["name"] for item in catalog.json()["data"]}
        self.assertIn("character-director", names)
        self.assertNotIn("cuti.atomic-providers", names)

        created = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "Make a Skill-driven trailer",
                "idempotency_key": "skill-selection-1",
                "workflow_id": "product-ad-video",
                "activated_skill_ids": ["character-director"],
            },
        ).json()["data"]
        prompt = self.deepseek.prompts[-1][1]
        self.assertIn('"product-ad-video"', prompt)
        self.assertIn("Call video_workflow_load", prompt)
        self.assertIn('VideoSpec.activated_skill_ids exactly to ["character-director"]', prompt)
        self.assertNotIn("$product-ad-video", prompt)

        lock_response = self.client.post(
            f"/chat-v1/service/studio/projects/{created['thread_id']}"
            "/skills/character-director/enable",
            json={"enabled": True},
        )
        self.assertEqual(lock_response.status_code, 200, lock_response.text)
        lock = lock_response.json()["data"]
        self.assertEqual(lock["project_id"], created["project_id"])
        self.assertEqual(lock["skill_id"], "character-director")
        self.assertTrue(lock["enabled"])
        listed = self.client.get(
            f"/chat-v1/service/studio/projects/{created['thread_id']}/skills",
        ).json()["data"]
        self.assertEqual([item["skill_id"] for item in listed], ["character-director"])

        self.client.post(
            f"/chat-v1/service/v2/runs/{created['id']}/messages",
            json={"content": "Make the hero warmer", "idempotency_key": "skill-edit-1"},
        )
        self.assertIn(
            'VideoSpec.activated_skill_ids exactly to ["character-director"]',
            self.deepseek.prompts[-1][1],
        )

    def test_seedance2_dollar_activation_loads_original_cuti_instructions(self) -> None:
        created = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "$seedance2 make a connected one minute film",
                "idempotency_key": "seedance2-original-1",
                "user_option": {"duration": 60},
            },
        )

        self.assertEqual(created.status_code, 200, created.text)
        run = created.json()["data"]
        self.assertEqual(run["workflow_id"], "seedance2")
        prompt = self.deepseek.prompts[-1][1]
        self.assertIn('"seedance2"', prompt)
        self.assertIn("Call video_workflow_load", prompt)
        self.assertNotIn("BEGIN seedance2/SKILL.md", prompt)

        snapshot = self.client.get(
            f"/chat-v1/service/v2/runs/{run['id']}",
        ).json()["data"]
        self.assertEqual(snapshot["run"]["workflow_id"], "seedance2")

    def test_workflow_selection_priority_and_unknown_rejection(self) -> None:
        explicit = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "$seedance2 make a product spot",
                "idempotency_key": "workflow-priority-1",
                "workflow_id": "product-ad-video",
            },
        )
        self.assertEqual(explicit.status_code, 200, explicit.text)
        self.assertEqual(explicit.json()["data"]["workflow_id"], "product-ad-video")

        unavailable = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "Use the Ink Press template",
                "idempotency_key": "workflow-unavailable-1",
                "workflow_id": "ink-press-product-workflow",
            },
        )
        self.assertEqual(unavailable.status_code, 422, unavailable.text)
        self.assertIn("not installed", unavailable.json()["detail"].lower())

        ambiguous = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "$seedance2 or $mv",
                "idempotency_key": "workflow-ambiguous-1",
            },
        )
        self.assertEqual(ambiguous.status_code, 422, ambiguous.text)

    def test_empty_project_follow_up_reapplies_create_contract(self) -> None:
        created = self.client.post(
            "/chat-v1/service/v2/runs",
            json={"objective": "Inspect only", "idempotency_key": "draft-1"},
        ).json()["data"]

        response = self.client.post(
            f"/chat-v1/service/v2/runs/{created['id']}/messages",
            json={
                "content": "Make a 15 second product video",
                "idempotency_key": "follow-up-1",
                "user_option": {"duration": 15},
            },
        )

        self.assertEqual(response.status_code, 200)
        prompt = self.deepseek.prompts[-1][1]
        self.assertTrue(prompt.startswith("CUTI_VIDEO_CREATE_V1\n"))
        self.assertIn("video_project_plan", prompt)
        self.assertIn("video_project_build", prompt)
        self.assertIn("Make a 15 second product video", prompt)

    def test_zip_install_reloads_the_runtime_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "external"
            runtime = VideoBuildRuntime(skill_runtime=VideoSkillRuntime([root]))
            set_runtime(runtime)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("test-helper/SKILL.md", """---
name: test-helper
description: Test helper guidance
metadata:
  roles: [guidance]
---
Keep the requested visual tone consistent.
""")

            response = self.client.post(
                "/chat-v1/service/studio/skills/install",
                files={"bundle": ("test-helper.zip", archive.getvalue(), "application/zip")},
                data={"overwrite": "false"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["data"]["name"], "test-helper")
            catalog = self.client.get("/chat-v1/service/v2/skills").json()["data"]
            installed = next(item for item in catalog if item["name"] == "test-helper")
            self.assertEqual(installed["description"], "Test helper guidance")
            self.assertTrue(installed["installed_at"])

    def test_compat_events_match_reducer_contract_and_stream_text(self) -> None:
        message = _compat_event("project-1", {
            "type": "assistant/message",
            "seq": 7,
            "time": 1000,
            "data": {"message": {"content": [{"type": "text", "text": "Done"}]}},
        })
        self.assertEqual(message["type"], "chat.message.created")
        self.assertEqual(message["payload"]["message"]["content"], "Done")
        self.assertEqual(message["payload"]["message"]["id"], "dsh-project-1-7")

        delta = _compat_event("project-1", {
            "type": "assistant/chunk",
            "seq": 8,
            "time": 1001,
            "data": {"turn": 2, "chunk": {"type": "text-delta", "text": "Working"}},
        })
        self.assertEqual(delta["type"], "agent.message.delta")
        self.assertEqual(delta["payload"]["delta"], "Working")
        usage = _compat_event("project-1", {
            "type": "assistant/chunk",
            "seq": 9,
            "time": 1002,
            "data": {"turn": 2, "step": 1, "chunk": {
                "type": "usage",
                "usage": {
                    "inputTokens": 12,
                    "outputTokens": 8,
                    "cacheReadTokens": 100,
                    "cacheWriteTokens": 5,
                },
            }},
        })
        self.assertEqual(usage["type"], "llm.usage")
        self.assertEqual(usage["payload"]["total_tokens"], 125)
        self.assertIsNone(_compat_event("project-1", {
            "type": "assistant/chunk",
            "seq": 10,
            "time": 1003,
            "data": {"turn": 2, "chunk": {"type": "thinking-delta", "text": "private"}},
        }))

        user = _compat_event("project-1", {
            "type": "user/message",
            "seq": 11,
            "time": 1004,
            "data": {
                "source": {"kind": "user"},
                "content": [{
                    "type": "text",
                    "text": "Make a video\n\nCurrent creation controls and inputs:\n{\"duration\": 15}",
                }],
            },
        })
        self.assertEqual(user["payload"]["message"]["content"], "Make a video")

        user_defaults = _compat_event("project-1", {
            "type": "user/message",
            "seq": 12,
            "time": 1005,
            "data": {
                "source": {"kind": "user"},
                "content": [{
                    "type": "text",
                    "text": (
                        "Make a video"
                        + _UI_DEFAULTS_SEPARATOR
                        + "{\"duration\": 15}"
                    ),
                }],
            },
        })
        self.assertEqual(user_defaults["payload"]["message"]["content"], "Make a video")

        tool = _compat_event("project-1", {
            "type": "tool/call",
            "seq": 12,
            "time": 1005,
            "data": {
                "turn": 2,
                "step": 2,
                "callId": "call-1",
                "name": "video_project_build",
                "arguments": "{\"plan_id\":\"plan-1\"}",
            },
        })
        self.assertEqual(tool["payload"]["tool"], "video_project_build")
        self.assertEqual(tool["payload"]["input"]["plan_id"], "plan-1")

        result = _compat_event("project-1", {
            "type": "tool/result",
            "seq": 13,
            "time": 1006,
            "data": {"turn": 2, "step": 2, "message": {
                "source": {"kind": "tool", "callId": "call-1"},
                "content": [{
                    "type": "tool-result",
                    "toolCallId": "call-1",
                    "content": [{"type": "text", "text": "Build queued"}],
                    "isError": False,
                }],
            }},
        })
        self.assertEqual(result["payload"]["call_id"], "call-1")
        self.assertEqual(result["payload"]["output"], "Build queued")

    def test_token_usage_is_aggregated_from_deepseek_usage_chunks(self) -> None:
        created = self.client.post(
            "/chat-v1/service/v2/runs",
            json={"objective": "Measure usage", "idempotency_key": "usage-1"},
        ).json()["data"]
        session_id = created["thread_id"]
        self.deepseek.events[session_id].insert(-1, {
            "type": "assistant/chunk",
            "seq": 4,
            "time": 1004,
            "data": {"turn": 1, "step": 1, "chunk": {
                "type": "usage",
                "usage": {
                    "inputTokens": 10,
                    "outputTokens": 20,
                    "cacheReadTokens": 30,
                    "cacheWriteTokens": 4,
                },
            }},
        })

        usage = self.client.get(
            f"/chat-v1/service/v2/runs/{created['id']}/token-usage",
        ).json()["data"]

        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["cached_tokens"], 30)
        self.assertEqual(usage["total_tokens"], 64)

    def test_create_prompt_follows_named_skills_without_workflow_hardcodes(self):
        from app.video_runtime.deepseek_bff import _initial_video_build_prompt

        prompt = _initial_video_build_prompt(
            objective="做一首 MV",
            project_id="p1",
            base_project_version_id="v1",
            idempotency_key="k1",
            user_option=None,
            input_files=[],
            workflow_id="mv",
            activated_skill_ids=[],
        )
        self.assertIn("video_workflow_load", prompt)
        self.assertIn('"mv"', prompt)
        self.assertIn("video_skill_load", prompt)
        self.assertIn("video_skill_read_resource", prompt)
        self.assertIn("UI defaults (creation controls)", prompt)
        self.assertIn("highest priority", prompt)
        self.assertIn("only to fields the user did not mention", prompt)
        self.assertNotIn("6smv", prompt)
        self.assertNotIn(
            "Respect duration, aspect ratio, resolution, selected image/video providers",
            prompt,
        )
        self.assertNotIn("load_skill", prompt)
        self.assertNotIn("read_skill_resource", prompt)
        self.assertNotIn("suno-song", prompt)
        self.assertNotIn("video-research", prompt)
        self.assertNotIn("hyperframes-captions", prompt)
        self.assertNotIn("Bitwize", prompt)
        self.assertNotIn("minimax-h3", prompt)
        self.assertNotIn("Call video_skill_load for every activated_skill_id", prompt)

        created = self.client.post(
            "/chat-v1/service/v2/runs",
            json={
                "objective": "做一首 MV",
                "idempotency_key": "mv-no-inject-1",
                "workflow_id": "mv",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        run = created.json()["data"]
        self.assertEqual(run["workflow_id"], "mv")
        self.assertEqual(run["activated_skill_ids"], [])

    def test_create_persists_independent_ui_and_content_languages(self):
        created = self.client.post(
            "/chat-v1/service/v2/runs",
            headers={"X-App-Language": "zh"},
            json={
                "objective": "请用英文展示全部策划，人物对白使用中文，并配英文字幕",
                "idempotency_key": "language-contract-1",
                "workflow_id": "seedance2",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        run = created.json()["data"]
        self.assertEqual(run["output_language"], "en")
        self.assertEqual(run["language_contract"], {
            "ui_locale": "zh-CN",
            "content_language": "en-US",
            "spoken_language": "zh-CN",
            "subtitle_language": "en-US",
            "provider_prompt_language": "auto",
        })
        prompt = self.deepseek.prompts[-1][1]
        self.assertIn("content_language=en-US", prompt)
        self.assertIn("spoken_language=zh-CN", prompt)

    def test_legacy_run_context_recovers_dialogue_language_from_project_intent(self):
        project, version = asyncio.run(self.runtime.create_project(
            user_id="local-user", title="Legacy English dialogue",
        ))
        plan = asyncio.run(self.runtime.repo.save_plan(RebuildPlan(
            project_id=project.id,
            kind="initial",
            base_project_version_id=version.id,
            workflow_id="seedance2",
            project_intent=ProjectIntent(
                title="中文策划",
                brief="制作中文策划，人物对白保持英文。",
                language="zh-CN",
                workflow_id="seedance2",
                style_id="cinematic",
                constraints={"dialogue_language": "English"},
            ),
            items=[],
        ), "legacy-language-plan"))
        build = Build(
            project_id=project.id,
            plan_id=plan.id,
            base_project_version_id=version.id,
            idempotency_key="legacy-language-build",
        )
        self.runtime.repo.builds[build.id] = build
        context = asyncio.run(_load_run_context(self.runtime, project.id))
        self.assertEqual(context["language_contract"]["content_language"], "zh-CN")
        self.assertEqual(context["language_contract"]["spoken_language"], "en-US")
        self.assertEqual(context["language_contract_version"], 1)
