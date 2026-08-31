from __future__ import annotations

import os
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from langchain.tools import ToolRuntime, tool

from .capabilities import CapabilityRegistry
from .deep_agent_runtime import V2AgentContext
from .models import PlanPatch
from .skill_catalog import SkillCatalog


class CoordinatorServices(Protocol):
    async def project_snapshot(self, run_id: str) -> dict: ...
    async def commit_agent_patch(self, run_id: str, patch: PlanPatch) -> dict: ...


def normalize_skill_http_url(url: str) -> str:
    """Normalize common non-absolute forms into http(s) when possible."""
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return raw
    # App-relative media paths produced in snapshots.
    if raw.startswith("/"):
        base = (os.getenv("PUBLIC_BASE_URL") or "http://localhost:19004").rstrip("/")
        return f"{base}{raw}"
    # Scheme-less host/path, e.g. localhost:19004/files/...
    if "://" not in raw and "/" in raw:
        return f"http://{raw}"
    return raw


def skill_http_url_error(url: str, *, normalized: str | None = None) -> dict[str, Any]:
    hint = (
        "Pass an absolute http(s) URL. "
        "Do not fetch file:// or large_tool_results handles — "
        "use get_project_snapshot for run/artifact state instead."
    )
    return {
        "ok": False,
        "error": "Skill HTTP URL must be absolute http/https",
        "url": url,
        "normalized_url": normalized,
        "hint": hint,
        "status_code": None,
        "content": "",
        "truncated": False,
    }


async def commit_normalized_plan_patch(
    services: CoordinatorServices,
    capabilities: CapabilityRegistry,
    run_id: str,
    patch: PlanPatch,
) -> dict:
    patch = patch.model_copy(update={
        "add_tasks": [
            item.model_copy(update={
                "capability_id": capabilities.canonical_id(item.capability_id)
            }) for item in patch.add_tasks
        ]
    })
    try:
        return await services.commit_agent_patch(run_id, patch)
    except (LookupError, ValueError) as exc:
        snapshot = await services.project_snapshot(run_id)
        return {
            "accepted": False, "error": str(exc),
            "current_revision": snapshot["run"]["current_revision"],
            "available_capability_ids": [
                item["id"] for item in capabilities.prompt_view()
            ],
        }


def build_coordinator_tools(
    services: CoordinatorServices,
    capabilities: CapabilityRegistry,
    skills: SkillCatalog | None = None,
    script_runner: Any | None = None,
) -> list:
    @tool
    async def get_project_snapshot(runtime: ToolRuntime[V2AgentContext]) -> dict:
        """Get authoritative durable run, message, task, option, file, and artifact state."""
        return await services.project_snapshot(runtime.context.run_id)

    @tool
    async def list_capabilities() -> list[dict]:
        """List enabled canonical capabilities."""
        return capabilities.prompt_view()

    @tool
    async def list_skills() -> list[dict]:
        """List available Skill metadata. Use descriptions to select relevant workflows."""
        return skills.prompt_view() if skills is not None else []

    @tool
    async def load_skill(name: str) -> dict:
        """Load one Skill's full Markdown instructions after it is selected or explicitly named."""
        if skills is None:
            raise LookupError("Skill catalog is not configured")
        skill = skills.load(name)
        if not skill.metadata.enabled:
            raise ValueError(f"Skill is disabled: {name}")
        return {
            "name": skill.metadata.name,
            "description": skill.metadata.description,
            "instructions": skill.instructions,
            "resources": skills.list_resources(name),
            "has_executable_contract": skill.contract is not None,
        }

    @tool
    async def list_skill_resources(name: str) -> list[str]:
        """List scripts, references, assets, and other bundled files for an activated Skill."""
        if skills is None:
            raise LookupError("Skill catalog is not configured")
        return skills.list_resources(name)

    @tool
    async def read_skill_resource(name: str, path: str) -> dict:
        """Read one text resource from an activated Skill when its instructions require it."""
        if skills is None:
            raise LookupError("Skill catalog is not configured")
        return {
            "name": name,
            "path": path,
            "content": skills.read_resource(name, path),
        }

    @tool
    async def run_skill_script(
        name: str,
        path: str,
        arguments: list[str],
        input_data: dict[str, Any],
        runtime: ToolRuntime[V2AgentContext],
    ) -> dict:
        """Run a bundled scripts/*.py or scripts/*.sh resource for an activated Skill."""
        if skills is None or script_runner is None:
            raise RuntimeError("Skill script execution is not configured")
        skill = skills.load(name)
        if path not in skills.list_resources(name):
            raise LookupError(f"Unknown Skill script: {path}")
        return await script_runner.run_skill_script(
            skill_name=name,
            skill_root=skill.metadata.path.parent,
            script_path=path,
            arguments=arguments,
            input_data={
                "input": input_data,
                "context": {
                    "run_id": runtime.context.run_id,
                    "thread_id": runtime.context.thread_id,
                    "project_id": runtime.context.project_id,
                    "user_id": runtime.context.user_id,
                },
            },
        )

    @tool
    async def skill_http_get(
        url: str,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """Fetch public HTTP content required by an activated Skill or its references.

        Only absolute http/https URLs are supported. Relative /files/... paths are
        resolved against PUBLIC_BASE_URL. Never use this for file:// handles or
        large_tool_results URIs — call get_project_snapshot instead.
        """
        normalized = normalize_skill_http_url(url)
        parsed = urlparse(normalized)
        if parsed.scheme == "file" or "large_tool_results" in (url or ""):
            return skill_http_url_error(url, normalized=normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return skill_http_url_error(url, normalized=normalized)
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
            ) as client:
                response = await client.get(normalized, headers=headers or {})
        except Exception as exc:  # noqa: BLE001 - return to agent, don't crash coordination
            return {
                "ok": False,
                "error": f"skill_http_get request failed: {exc}",
                "url": url,
                "normalized_url": normalized,
                "status_code": None,
                "content": "",
                "truncated": False,
            }
        content = response.content
        truncated = len(content) > 256 * 1024
        return {
            "url": str(response.url),
            "status_code": response.status_code,
            "ok": response.is_success,
            "retry_after": response.headers.get("retry-after"),
            "content_type": response.headers.get("content-type", ""),
            "content": content[: 256 * 1024].decode("utf-8", errors="replace"),
            "truncated": truncated,
        }

    @tool
    async def propose_plan_patch(
        patch: PlanPatch, runtime: ToolRuntime[V2AgentContext],
    ) -> dict:
        """Validate and commit an append-only dynamic plan patch."""
        return await commit_normalized_plan_patch(
            services, capabilities, runtime.context.run_id, patch,
        )

    @tool
    async def mark_goal_satisfied(
        response: str, runtime: ToolRuntime[V2AgentContext],
    ) -> dict:
        """Mark the current goal complete only after snapshot verification."""
        snapshot = await services.project_snapshot(runtime.context.run_id)
        return await commit_normalized_plan_patch(
            services, capabilities, runtime.context.run_id,
            PlanPatch(
                base_revision=snapshot["run"]["current_revision"],
                reason="Coordinator verified the current goal is satisfied.",
                goal_satisfied=True, response=response,
            ),
        )

    return [
        get_project_snapshot,
        list_capabilities,
        list_skills,
        load_skill,
        list_skill_resources,
        read_skill_resource,
        run_skill_script,
        skill_http_get,
        propose_plan_patch,
        mark_goal_satisfied,
    ]
