from __future__ import annotations

import json
import logging
from typing import Any

from .capabilities import CapabilityInputs, CapabilityManifest
from .executors import DelegateResult
from .models import AgentRun, ArtifactVersion, Task

logger = logging.getLogger(__name__)


def parse_mcp_servers_config(raw: str) -> list[dict[str, Any]]:
    """Parse DEEP_AGENT_V2_MCP_SERVERS as JSON list or empty."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Invalid DEEP_AGENT_V2_MCP_SERVERS JSON; ignoring")
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("name")]


def manifests_from_mcp_servers(servers: list[dict[str, Any]]) -> list[CapabilityManifest]:
    """Project configured MCP tools into disabled-by-default capability stubs when tools listed."""
    manifests: list[CapabilityManifest] = []
    for server in servers:
        server_name = str(server["name"])
        tools = server.get("tools") or []
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if isinstance(tool, str):
                tool_name = tool
                description = f"MCP tool {tool_name} on {server_name}"
                enabled = True
            elif isinstance(tool, dict) and tool.get("name"):
                tool_name = str(tool["name"])
                description = str(tool.get("description") or f"MCP tool {tool_name}")
                enabled = bool(tool.get("enabled", True))
            else:
                continue
            if isinstance(tool, dict) and tool.get("capability_id"):
                capability_id = str(tool["capability_id"])
            else:
                capability_id = f"mcp.{server_name}.{tool_name}"
            manifests.append(CapabilityManifest(
                id=capability_id,
                description=description,
                executor="mcp.call",
                skill_name=None,
                inputs=CapabilityInputs(
                    optional=list((tool.get("accepts") if isinstance(tool, dict) else None) or []),
                ),
                output_type=str(
                    (tool.get("produces") if isinstance(tool, dict) else None) or "mcp_result"
                ),
                terminal_events=["mcp_tool_completed"],
                enabled=enabled,
                mcp_server=server_name,
                mcp_tool=tool_name,
            ))
    return manifests


class McpToolBridge:
    """Skeleton bridge for executor=mcp.call. Requires explicit server config."""

    def __init__(self, servers: list[dict[str, Any]] | None = None):
        self.servers = {
            str(item["name"]): item for item in (servers or []) if item.get("name")
        }

    async def call(
        self,
        *,
        run: AgentRun,
        task: Task,
        selected: list[ArtifactVersion],
        idempotency_key: str,
        capability: CapabilityManifest,
    ) -> DelegateResult:
        server_name = capability.mcp_server
        tool_name = capability.mcp_tool
        if not server_name or not tool_name:
            raise ValueError(f"mcp.call capability {capability.id} missing mcp_server/mcp_tool")
        server = self.servers.get(server_name)
        if not server:
            raise ValueError(
                f"MCP server {server_name!r} is not configured in DEEP_AGENT_V2_MCP_SERVERS"
            )
        # Intentionally not auto-connecting arbitrary remotes yet: keep a clear extension point.
        transport = server.get("transport") or server.get("url")
        if not transport:
            raise ValueError(
                f"MCP server {server_name!r} has no transport/url; "
                "configure it before enabling mcp.call capabilities"
            )
        logger.info(
            "mcp.call stub invoked capability=%s server=%s tool=%s run=%s task=%s key=%s selected=%s",
            capability.id, server_name, tool_name, run.id, task.id, idempotency_key, len(selected),
        )
        raise NotImplementedError(
            f"MCP tool invocation is scaffolded but not connected yet "
            f"({server_name}/{tool_name}). Provide a transport client integration next."
        )
