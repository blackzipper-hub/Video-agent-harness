/**
 * 沉浸模式（创作区「沉浸模式」按钮 → /video-check、/canvas、/immersive-video、/immerse-share）。
 * 暂关：已知 bug 较多，恢复时改为 true 即可重新开放路由与入口。
 */
export const IMMERSIVE_MODE_ENABLED = false;

/**
 * Deep Agent V2 migration switch.
 * V2 is the default. Set the flag to "false" only for a temporary legacy rollback.
 */
export const DEEP_AGENT_V2_ENABLED = import.meta.env.VITE_DEEP_AGENT_V2_ENABLED !== "false";

/**
 * Studio Dynamic Graph is opt-in while the legacy creation page remains the
 * rollback path. An empty rollout list enables all users in a flagged env.
 */
export const STUDIO_DYNAMIC_GRAPH_ENABLED =
  import.meta.env.VITE_STUDIO_DYNAMIC_GRAPH_ENABLED === "true";

export const STUDIO_DYNAMIC_GRAPH_ROLLOUT_USERS = new Set(
  String(import.meta.env.VITE_STUDIO_DYNAMIC_GRAPH_ROLLOUT_USERS || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);

/** Empty rollout list means the enabled environment is fully opted in. */
export const isStudioDynamicGraphEnabledFor = (identifiers: Array<string | number | null | undefined>) =>
  STUDIO_DYNAMIC_GRAPH_ENABLED && (
    STUDIO_DYNAMIC_GRAPH_ROLLOUT_USERS.size === 0 ||
    identifiers.some((identifier) => identifier != null && STUDIO_DYNAMIC_GRAPH_ROLLOUT_USERS.has(String(identifier)))
  );
