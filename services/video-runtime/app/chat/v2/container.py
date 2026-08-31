from __future__ import annotations

from .capabilities import CapabilityRegistry
from .capability_loader import build_registry, reload_registry
from app.orchestration.context import ContextAssembler
from app.orchestration.skills import SkillResolver
from .coordinator import DeepAgentCoordinator
from .deep_agent_runtime import DeepAgentRuntime
from .executors import CapabilityExecutor
from app.orchestration.task_runtime import DynamicHarness
from .mcp_tool_bridge import McpToolBridge, manifests_from_mcp_servers, parse_mcp_servers_config
from app.orchestration.policy import PlanValidator
from .postgres_repository import PostgresV2Repository
from .repository import InMemoryV2Repository
from app.orchestration.recovery import V2ResultReconciler
from .sandbox_client import SandboxClient
from .skill_catalog import SkillCatalog
from .workflows import WORKFLOWS, configure_workflows
from app.chat.config import Settings
from app.chat.services.agent.workflow_client import create_default_workflow_client

_harness: DynamicHarness | None = None
_repository = None
_reconciler: V2ResultReconciler | None = None
_catalog: SkillCatalog | None = None
_capabilities: CapabilityRegistry | None = None
_mcp_bridge: McpToolBridge | None = None
_settings: Settings | None = None
_runtime: DeepAgentRuntime | None = None

# Only the old fixed master graph is incompatible with dynamic composition.
# The local outline/character/scene/shot services are deliberately atomic
# capabilities and must stay enabled for workflow Skills such as short drama.
_DISABLED_FIXED_PIPELINE_CAPABILITIES = frozenset({
    "video.pipeline.generate",
})


def get_harness() -> DynamicHarness | None:
    return _harness


def get_skill_catalog() -> SkillCatalog | None:
    return _catalog


def get_capability_registry() -> CapabilityRegistry | None:
    return _capabilities


def get_mcp_bridge() -> McpToolBridge | None:
    return _mcp_bridge


def _apply_runtime_policy(registry: CapabilityRegistry, settings: Settings) -> None:
    for capability in registry.list(include_disabled=True):
        # Deep Agent V2 is an atomic composer. The legacy `video` target owns a
        # fixed multi-stage LangGraph with implicit music and approval gates, so
        # it must never be reachable from this runtime (including after hot reload).
        if capability.target_agent == "video":
            capability.enabled = False
        if capability.id in _DISABLED_FIXED_PIPELINE_CAPABILITIES:
            capability.enabled = False
        if (
            capability.executor == "sandbox.run"
            and not settings.DEEP_AGENT_V2_SANDBOX_ENABLED
        ):
            capability.enabled = False


def reload_v2_skills() -> int:
    if _catalog is None or _capabilities is None or _settings is None:
        raise RuntimeError("Deep Agent V2 is not initialized")
    servers = parse_mcp_servers_config(_settings.DEEP_AGENT_V2_MCP_SERVERS)
    reload_registry(
        _capabilities,
        _catalog,
        extra=manifests_from_mcp_servers(servers),
        include_platform=True,
    )
    _apply_runtime_policy(_capabilities, _settings)
    configure_workflows(_catalog, _capabilities)
    if _runtime is not None:
        _runtime.reload_skill_files()
    return len(_catalog.list_metadata())


async def initialize_v2(settings: Settings) -> DynamicHarness | None:
    global _harness, _repository, _reconciler, _catalog, _capabilities, _mcp_bridge, _settings, _runtime
    if not settings.DEEP_AGENT_V2_ENABLED:
        return None
    _settings = settings
    database_url = settings.DEEP_AGENT_V2_DATABASE_URL or settings.DATABASE_URL
    if database_url:
        _repository = await PostgresV2Repository.connect(
            database_url,
            settings.DEEP_AGENT_V2_DATABASE_SCHEMA,
        )
    else:
        _repository = InMemoryV2Repository()

    roots = settings.deep_agent_skill_root_paths()
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    _catalog = SkillCatalog(roots)
    _catalog.discover()
    servers = parse_mcp_servers_config(settings.DEEP_AGENT_V2_MCP_SERVERS)
    _mcp_bridge = McpToolBridge(servers)
    _capabilities = build_registry(
        _catalog,
        extra=manifests_from_mcp_servers(servers),
        include_platform=True,
    )
    _apply_runtime_policy(_capabilities, settings)
    configure_workflows(_catalog, _capabilities)
    for capability_id, target, mode in (
        ("video.pipeline.generate", settings.DEEP_AGENT_V2_PIPELINE_TARGET, settings.DEEP_AGENT_V2_PIPELINE_MODE),
        ("video.generate", settings.DEEP_AGENT_V2_SINGLE_VIDEO_TARGET, settings.DEEP_AGENT_V2_SINGLE_VIDEO_MODE),
        ("video.edit", settings.DEEP_AGENT_V2_VIDEO_EDIT_TARGET, settings.DEEP_AGENT_V2_VIDEO_EDIT_MODE),
    ):
        try:
            item = _capabilities.get(capability_id, require_enabled=False)
            item.target_agent = target
            item.mode = mode
        except LookupError:
            pass

    sandbox_client = SandboxClient(settings)
    harness = DynamicHarness(
        _repository, _capabilities,
        CapabilityExecutor(
            create_default_workflow_client(),
            _capabilities,
            mcp_bridge=_mcp_bridge,
            sandbox_client=sandbox_client,
        ),
        PlanValidator(
            _capabilities,
            max_revisions=settings.DEEP_AGENT_V2_MAX_PLAN_REVISIONS,
            max_tasks=settings.DEEP_AGENT_V2_MAX_TASKS,
            max_parallel_generation_tasks=settings.DEEP_AGENT_V2_MAX_PARALLEL_GENERATION_TASKS,
            skill_catalog=_catalog,
        ),
        ContextAssembler(
            settings.DEEP_AGENT_V2_RECENT_MESSAGE_LIMIT,
            max_parallel_generation_tasks=settings.DEEP_AGENT_V2_MAX_PARALLEL_GENERATION_TASKS,
        ),
        skill_resolver=SkillResolver(_catalog, _capabilities, WORKFLOWS),
        max_parallel_generation_tasks=settings.DEEP_AGENT_V2_MAX_PARALLEL_GENERATION_TASKS,
        max_active_runs_per_user=(
            settings.DEEP_AGENT_V2_MAX_ACTIVE_RUNS_PER_USER
            if settings.DEEP_AGENT_V2_ACTIVE_RUN_LIMIT_ENABLED
            else 0
        ),
    )
    _runtime = DeepAgentRuntime(settings)
    coordinator = DeepAgentCoordinator(
        _runtime,
        harness,
        _capabilities,
        _catalog,
        sandbox_client,
    )
    await coordinator.initialize()
    harness.set_coordinator(coordinator)
    _harness = harness
    await harness.resume_active_runs()
    results_database_url = (
        settings.DEEP_AGENT_V2_RESULTS_DATABASE_URL or settings.DATABASE_URL
    )
    if settings.DEEP_AGENT_V2_RESULT_RECONCILE_ENABLED and results_database_url:
        _reconciler = V2ResultReconciler(
            results_database_url,
            schema=settings.DEEP_AGENT_V2_RESULTS_SCHEMA,
            interval_seconds=settings.DEEP_AGENT_V2_RESULT_RECONCILE_INTERVAL_SECONDS,
        )
        await _reconciler.start(harness)
    return harness


async def close_v2() -> None:
    global _harness, _repository, _reconciler, _catalog, _capabilities, _mcp_bridge, _settings, _runtime
    if _reconciler:
        await _reconciler.stop()
    if _harness:
        await _harness.close()
    if isinstance(_repository, PostgresV2Repository):
        await _repository.close()
    _harness = None
    _repository = None
    _reconciler = None
    _catalog = None
    _capabilities = None
    _mcp_bridge = None
    _settings = None
    _runtime = None
