from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.video_runtime.api import _logical_artifact_id, router, set_runtime
from app.video_runtime.models import MediaArtifactVersion
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.skill_workflows import load_workflow_skills


class VideoRuntimeApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = VideoBuildRuntime()
        asyncio.run(self.runtime.plugins.load_directories([
            Path(__file__).resolve().parents[2] / "plugins",
        ]))
        asyncio.run(load_workflow_skills(self.runtime.plugins, self.runtime.skills))
        set_runtime(self.runtime)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.headers = {
            "X-Video-User-Id": "user-1",
            "X-Video-Session-Id": "session-1",
        }

    def tearDown(self) -> None:
        self.client.close()

    def test_legacy_assembled_video_projects_to_stable_final_video_id(self) -> None:
        artifact = SimpleNamespace(
            artifact_id="project-1:assembled-video",
            metadata={},
        )
        self.assertEqual(_logical_artifact_id("project-1", artifact), "video:final")

    def test_project_binding_preview_idempotency_and_conflict(self) -> None:
        response = self.client.post(
            "/api/video/projects",
            headers={**self.headers, "Idempotency-Key": "create-film"},
            json={"title": "Film"},
        )
        self.assertEqual(response.status_code, 200)
        project = response.json()["data"]
        project_id = project["projectId"]
        repeated_project = self.client.post(
            "/api/video/projects",
            headers={**self.headers, "Idempotency-Key": "create-film"},
            json={"title": "Film"},
        ).json()["data"]
        self.assertEqual(repeated_project["projectId"], project_id)
        artifact = asyncio.run(self.runtime.add_artifact(MediaArtifactVersion(
            project_id=project_id,
            type="character",
            title="Hero",
        )))

        first_get = self.client.get(f"/api/video/projects/{project_id}", headers=self.headers)
        second_get = self.client.get(f"/api/video/projects/{project_id}", headers=self.headers)
        self.assertEqual(first_get.status_code, 200)
        self.assertEqual(second_get.status_code, 200)
        self.assertEqual(
            self.client.get(
                f"/api/video/projects/{project_id}",
                headers={"X-Video-User-Id": "user-2"},
            ).status_code,
            403,
        )

        preview_response = self.client.post(
            f"/api/video/projects/{project_id}/changes/preview",
            headers={**self.headers, "Idempotency-Key": "preview-coat"},
            json={"change": "new coat", "targetArtifactVersionIds": [artifact.id]},
        )
        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.json()["data"]
        repeated_preview = self.client.post(
            f"/api/video/projects/{project_id}/changes/preview",
            headers={**self.headers, "Idempotency-Key": "preview-coat"},
            json={"change": "new coat", "targetArtifactVersionIds": [artifact.id]},
        ).json()["data"]
        self.assertEqual(repeated_preview["planId"], preview["planId"])
        rebuild_body = {
            "planId": preview["planId"],
            "baseProjectVersionId": preview["baseProjectVersionId"],
            "idempotencyKey": "same-build",
        }
        first = self.client.post(
            f"/api/video/projects/{project_id}/rebuilds",
            headers=self.headers,
            json=rebuild_body,
        )
        second = self.client.post(
            f"/api/video/projects/{project_id}/rebuilds",
            headers=self.headers,
            json=rebuild_body,
        )
        self.assertEqual(first.json()["data"]["buildId"], second.json()["data"]["buildId"])

        stale = self.client.post(
            f"/api/video/projects/{project_id}/rebuilds",
            headers=self.headers,
            json={**rebuild_body, "baseProjectVersionId": "stale", "idempotencyKey": "stale"},
        )
        self.assertEqual(stale.status_code, 409)

        events = self.client.get(
            f"/api/video/projects/{project_id}/events",
            headers=self.headers,
        ).json()["data"]
        self.assertEqual(
            sum(event["type"] == "project.session_bound" for event in events),
            1,
        )

    def test_initial_plan_build_and_step_status_api(self) -> None:
        project = self.client.post(
            "/api/video/projects", headers=self.headers, json={"title": "New film"},
        ).json()["data"]
        spec = {
            "title": "New film", "language": "zh-CN", "target_duration_seconds": 10,
            "aspect_ratio": "16:9", "resolution": "1080p",
            "workflow_id": "cuti.seedance-story", "style_id": "cuti.cinematic",
            "characters": [{"id": "hero", "name": "Hero", "appearance": "black hair"}],
            "shots": [
                {"id": "1", "order": 1, "duration_seconds": 5, "beat": "start", "visual_prompt": "arrives", "character_ids": ["hero"]},
                {"id": "2", "order": 2, "duration_seconds": 5, "beat": "end", "visual_prompt": "leaves", "character_ids": ["hero"]},
            ],
            "audio": {"narration_voice": "Wise_Woman", "bgm_prompt": "piano", "subtitles": True},
            "providers": {"video": "seedance-2.0", "image": "gpt-image-2", "music": "suno"},
            "automation": {"mode": "automatic", "max_artifact_retries": 1},
        }
        planned = self.client.post(
            f"/api/video/projects/{project['projectId']}/plans", headers=self.headers,
            json={
                "baseProjectVersionId": project["currentVersionId"],
                "idempotencyKey": "first-plan", "videoSpec": spec,
            },
        )
        self.assertEqual(planned.status_code, 200, planned.text)
        plan = planned.json()["data"]
        self.assertEqual(plan["kind"], "initial")
        self.assertEqual(plan["shotCount"], 2)
        started = self.client.post(
            f"/api/video/projects/{project['projectId']}/builds", headers=self.headers,
            json={
                "planId": plan["planId"],
                "baseProjectVersionId": plan["baseProjectVersionId"],
                "idempotencyKey": "first-build",
            },
        )
        self.assertEqual(started.status_code, 200, started.text)
        build = started.json()["data"]
        self.assertEqual(build["kind"], "initial")
        steps = self.client.get(
            f"/api/video/projects/{project['projectId']}/builds/{build['buildId']}/steps",
            headers=self.headers,
        ).json()["data"]
        self.assertEqual(len(steps), len(plan["steps"]))

        workspace = self.client.get(
            f"/api/video/projects/{project['projectId']}/workspace",
            headers=self.headers,
        )
        self.assertEqual(workspace.status_code, 200, workspace.text)
        payload = workspace.json()["data"]
        self.assertEqual(payload["project"]["id"], project["projectId"])
        self.assertEqual(payload["builds"][0]["buildId"], build["buildId"])
        self.assertEqual(len(payload["builds"][0]["steps"]), len(plan["steps"]))

    def test_staged_project_intent_plan_exposes_revisions_and_checkpoint(self) -> None:
        self.runtime.staged_planning_enabled = True
        project = self.client.post(
            "/api/video/projects", headers=self.headers, json={"title": "Staged film"},
        ).json()["data"]
        response = self.client.post(
            f"/api/video/projects/{project['projectId']}/plans",
            headers=self.headers,
            json={
                "baseProjectVersionId": project["currentVersionId"],
                "idempotencyKey": "staged-plan",
                "projectIntent": {
                    "title": "Staged film",
                    "brief": "A short railway story",
                    "workflow_id": "workflow-keyframe-pipeline",
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()["data"]
        self.assertEqual(plan["schemaVersion"], 2)
        self.assertEqual(plan["planRevision"], 1)
        self.assertEqual(plan["currentPhase"], "story_intent")
        self.assertEqual(plan["nextCheckpoint"]["id"], "story_ready")
        self.assertEqual(
            [item["step_id"] for item in plan["steps"]],
            ["intent", "story-draft"],
        )
