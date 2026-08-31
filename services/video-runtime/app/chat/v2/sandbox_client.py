from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import textwrap
import time
from pathlib import Path
from typing import Any

import httpx
from jsonschema.validators import validator_for

from app.chat.config import Settings

from .capabilities import CapabilityManifest
from .models import AgentRun, ArtifactVersion, Task


class SandboxClient:
    def _skill_environment(self) -> dict[str, str]:
        return {
            name: value
            for name in self.skill_env_allowlist
            if (value := os.environ.get(name))
        }

    def _host_gateway_url(self) -> str:
        configured = getattr(self, "_host_gateway_public_url", "") or ""
        if configured:
            return configured.rstrip("/")
        # Lazy: settings may expose PUBLIC via env
        public = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:19004").rstrip("/")
        return f"{public}/chat-v1/service/v2/internal/host/dispatch"

    def _host_token(self) -> str:
        return self.token or ""

    def __init__(self, settings: Settings):
        self.enabled = settings.DEEP_AGENT_V2_SANDBOX_ENABLED
        self.base_url = settings.DEEP_AGENT_V2_SANDBOX_WORKER_URL.rstrip("/")
        self.token = (
            settings.DEEP_AGENT_V2_SANDBOX_TOKEN
            or settings.DEEP_AGENT_V2_INTERNAL_EVENT_TOKEN
            or settings.CUTI_SERVICE_TOKEN
        )
        self.timeout = settings.DEEP_AGENT_V2_SANDBOX_REQUEST_TIMEOUT_SECONDS
        self.skill_env_allowlist = tuple(
            name
            for item in settings.DEEP_AGENT_V2_SKILL_ENV_ALLOWLIST.split(",")
            if (name := item.strip())
            and re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
        )
        self._host_gateway_public_url = (
            getattr(settings, "DEEP_AGENT_V2_HOST_GATEWAY_PUBLIC_URL", "") or ""
        )
        self._ark_protocol_bridge_enabled = bool(
            getattr(settings, "DEEP_AGENT_V2_ARK_PROTOCOL_BRIDGE", True)
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _host_client_module(self) -> bytes:
        return textwrap.dedent(
            '''\
            """Injected host client for sandbox Skills."""
            from __future__ import annotations
            import json
            import os
            import urllib.request

            def host_dispatch(capability: str, payload: dict | None = None, **kwargs):
                url = os.environ["CUTI_HOST_GATEWAY_URL"]
                token = os.environ.get("CUTI_HOST_TOKEN", "")
                allowed_capabilities = json.loads(
                    os.environ.get("CUTI_HOST_ALLOWED_CAPABILITIES", "[]")
                )
                allowed_domains = json.loads(
                    os.environ.get("CUTI_HOST_ALLOWED_DOMAINS", "[]")
                )
                body = {
                    "capability": capability,
                    "payload": payload or {},
                    "allowed_capabilities": allowed_capabilities,
                    "allowed_domains": allowed_domains,
                    "skill_name": os.environ.get("CUTI_SKILL_NAME"),
                }
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=float(kwargs.get("timeout", 120))) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
                return data
            '''
        ).encode("utf-8")

    async def run(
        self,
        *,
        run: AgentRun,
        task: Task,
        capability: CapabilityManifest,
        artifacts: list[ArtifactVersion],
        idempotency_key: str,
    ) -> tuple[str, dict[str, Any]]:
        if not self.enabled:
            raise RuntimeError("sandbox execution is disabled")
        if capability.executor != "sandbox.run" or not capability.sandbox:
            raise ValueError("capability is not a configured sandbox skill")
        if not capability.skill_name or not capability.bundle_digest:
            raise ValueError("sandbox capability is missing bundle identity")
        sandbox_run_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
        bundle_root = Path(capability.bundle_path or "").resolve()
        inputs = []
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file() and not path.name.startswith(".cuti-"):
                inputs.append({
                    "path": f"skill/{path.relative_to(bundle_root).as_posix()}",
                    "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                })
        input_document = {
            "protocol_version": 1,
            "run_id": sandbox_run_id,
            "idempotency_key": idempotency_key,
            "skill_name": capability.skill_name,
            "bundle_digest": capability.bundle_digest,
            "output_type": capability.output_type,
            "objective": task.objective,
            "parameters": task.parameters,
            "context": {
                "agent_run_id": run.id,
                "thread_id": run.thread_id,
                "project_id": run.project_id,
                "user_id": run.user_id,
            },
            "artifacts": [
                {
                    "id": item.id,
                    "type": item.type,
                    "title": item.title,
                    "summary": item.summary,
                    "uri": item.uri,
                    "metadata": item.metadata,
                }
                for item in artifacts
            ],
        }
        inputs.append({
            "path": "input.json",
            "content_base64": base64.b64encode(
                json.dumps(input_document, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
        })
        inputs.append({
            "path": "skill/_cuti_host.py",
            "content_base64": base64.b64encode(self._host_client_module()).decode("ascii"),
        })
        sandbox_policy = capability.sandbox or {}
        allowed_caps = list(
            sandbox_policy.get("allowed_host_capabilities")
            or ["artifact.read", "artifact.write", "log", "progress", "http.request", "media.concat", "media.extract_frame", "provider.generate"]
        )
        allowed_domains = list(sandbox_policy.get("allowed_domains") or [])
        payload = {
            "image": capability.sandbox["base_image"],
            "command": [
                "python",
                f"/workspace/skill/{capability.sandbox['entrypoint']}",
            ],
            "inputs": inputs,
            "artifacts": ["output/result.json"],
            "timeout_seconds": capability.sandbox["timeout_seconds"],
            "network": {
                "mode": capability.sandbox.get("network") or "none",
                "allowed_domains": allowed_domains,
            },
            "environment": {
                **self._skill_environment(),
                "CUTI_INPUT_JSON": "/workspace/input.json",
                "CUTI_OUTPUT_DIR": "/workspace/output",
                "CUTI_PROTOCOL_VERSION": "1",
                "CUTI_SKILL_NAME": capability.skill_name or "",
                "CUTI_HOST_GATEWAY_URL": self._host_gateway_url(),
                "CUTI_HOST_TOKEN": self._host_token(),
                "CUTI_HOST_ALLOWED_CAPABILITIES": json.dumps(allowed_caps),
                "CUTI_HOST_ALLOWED_DOMAINS": json.dumps(allowed_domains),
            },
        }
        body = await self._submit_payload(
            payload,
            cancel_seed=sandbox_run_id,
            timeout_seconds=capability.sandbox["timeout_seconds"],
        )
        worker_run_id = body["run_id"]
        encoded = next(
            (
                item["content_base64"]
                for item in body.get("artifacts", [])
                if item.get("path") == "output/result.json"
            ),
            None,
        )
        if not encoded:
            raise RuntimeError("sandbox skill did not write output/result.json")
        artifact = json.loads(base64.b64decode(encoded, validate=True))
        if isinstance(artifact, dict) and isinstance(artifact.get("artifact"), dict):
            artifact = artifact["artifact"]
        if not isinstance(artifact, dict):
            raise RuntimeError("sandbox result must be a JSON object")
        if capability.output_schema:
            validator_for(capability.output_schema)(capability.output_schema).validate(artifact)
        return worker_run_id, artifact

    async def run_skill_script(
        self,
        *,
        skill_name: str,
        skill_root: Path,
        script_path: str,
        arguments: list[str],
        input_data: dict[str, Any],
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Skill script execution is disabled")
        relative = Path(script_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or relative.parts[0] != "scripts"
        ):
            raise ValueError("Skill scripts must be safe paths under scripts/")
        root = skill_root.resolve()
        script = (root / relative).resolve()
        try:
            script.relative_to(root)
        except ValueError as exc:
            raise ValueError("Skill script escapes its bundle") from exc
        if not script.is_file():
            raise LookupError(f"Unknown Skill script: {script_path}")
        if len(arguments) > 32 or any(len(item) > 2048 for item in arguments):
            raise ValueError("Skill script arguments exceed limits")
        inputs = [
            {
                "path": f"skill/{path.relative_to(root).as_posix()}",
                "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.name.startswith(".cuti-")
        ]
        inputs.append({
            "path": "input.json",
            "content_base64": base64.b64encode(
                json.dumps(input_data, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
        })
        if relative.suffix == ".py":
            command = ["python", f"/workspace/skill/{relative.as_posix()}"]
        elif relative.suffix in {".sh", ".bash"}:
            command = ["sh", f"/workspace/skill/{relative.as_posix()}"]
        else:
            raise ValueError("Only Python and shell Skill scripts are executable")

        from app.chat.v2.ark_protocol_bridge import (
            ark_redirect_sitecustomize,
            should_enable_ark_http_bridge,
        )

        environment = {
            **self._skill_environment(),
            "CUTI_INPUT_JSON": "/workspace/input.json",
            "CUTI_SKILL_ROOT": "/workspace/skill",
            "CUTI_SKILL_NAME": skill_name,
            "CUTI_HOST_GATEWAY_URL": self._host_gateway_url(),
            "CUTI_HOST_TOKEN": self._host_token(),
            "CUTI_HOST_ALLOWED_CAPABILITIES": json.dumps([
                "http.fetch", "http.request",
                "media.concat", "media.extract_frame", "media.trim", "media.speed_adjust",
                "media.audio_trim", "media.audio_analyze", "media.mix_audio",
                "provider.generate",
                "artifact.read", "artifact.write", "log", "progress",
            ]),
            "CUTI_HOST_ALLOWED_DOMAINS": json.dumps([]),
        }
        bridge_force = bool(
            getattr(self, "_ark_protocol_bridge_enabled", True)
        )
        # Prefer settings flag when present on the client.
        enable_bridge = should_enable_ark_http_bridge(
            skill_name=skill_name,
            script_path=relative.as_posix(),
            force=False,
        )
        if not bridge_force:
            enable_bridge = False
        if enable_bridge:
            public = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:19004").rstrip("/")
            configured = getattr(self, "_host_gateway_public_url", "") or ""
            if configured:
                # configured points at .../host/dispatch → strip to .../host/ark
                base = configured.rstrip("/")
                if base.endswith("/dispatch"):
                    base = base[: -len("/dispatch")]
                ark_base = f"{base}/ark"
            else:
                ark_base = f"{public}/chat-v1/service/v2/internal/host/ark"
            inputs.append({
                "path": "sitecustomize.py",
                "content_base64": base64.b64encode(
                    ark_redirect_sitecustomize()
                ).decode("ascii"),
            })
            environment["PYTHONPATH"] = "/workspace"
            environment["CUTI_ARK_PROTOCOL_BRIDGE_BASE"] = ark_base
            # seedance.py requires ARK_API_KEY; inject placeholder accepted by bridge auth.
            if not environment.get("ARK_API_KEY"):
                environment["ARK_API_KEY"] = (
                    self._host_token() or "cuti-ark-bridge-local"
                )
                if not environment["ARK_API_KEY"].startswith("cuti-ark-bridge"):
                    # Service token works for auth; keep as-is for Authorization header.
                    pass

        payload = {
            "image": "python:3.11-slim",
            "command": [*command, *arguments],
            "inputs": inputs,
            "artifacts": [],
            "timeout_seconds": timeout_seconds,
            "environment": environment,
        }
        result = await self._submit_payload(
            payload,
            cancel_seed=hashlib.sha256(
                f"{skill_name}:{script_path}:{time.time_ns()}".encode()
            ).hexdigest()[:32],
            timeout_seconds=timeout_seconds,
        )
        return {
            "run_id": result["run_id"],
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "artifacts": result.get("artifacts", []),
        }

    async def _submit_payload(
        self,
        payload: dict[str, Any],
        *,
        cancel_seed: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/v1/runs",
                    json=payload,
                    headers=self._headers(),
                )
                response.raise_for_status()
                body = response.json()
                worker_run_id = body["run_id"]
                deadline = time.monotonic() + timeout_seconds + 30
                while body.get("status") in {"queued", "running"}:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("sandbox worker result polling timed out")
                    await asyncio.sleep(0.25)
                    response = await client.get(
                        f"{self.base_url}/v1/runs/{worker_run_id}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    body = response.json()
        except asyncio.CancelledError:
            await self.cancel(locals().get("worker_run_id", cancel_seed))
            raise
        if body.get("status") != "succeeded":
            raise RuntimeError(
                body.get("error")
                or body.get("stderr")
                or f"sandbox execution ended as {body.get('status')}"
            )
        return body

    async def cancel(self, sandbox_run_id: str) -> None:
        if not self.enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{self.base_url}/v1/runs/{sandbox_run_id}/cancel",
                    headers=self._headers(),
                )
        except Exception:
            return

