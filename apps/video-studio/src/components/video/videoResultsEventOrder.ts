/** 与后端 workflow_state.path[].id、MessageArea 对齐 → VideoResultsPanel 用的 event_type 序列（不含 analysis：图里有该步但不单独占一栏） */
export const WORKFLOW_PATH_ID_TO_PANEL_EVENT: Record<string, string> = {
  music: "music_generated",
  story_style: "story_outline_generated",
  visual: "characters_designed",
  scenes: "scenes_generated",
  storyboards: "keyframes_generated",
  narration: "narrations_generated",
  shots: "video_segments_generated",
  final: "video_completed",
};

/** 无 workflow_state 时的默认顺序（与 _build_default_video_workflow_path 一致：音乐在故事/风格之前） */
export const FALLBACK_VIDEO_RESULTS_EVENT_ORDER = [
  "music_generated",
  "story_outline_generated",
  "characters_designed",
  "scenes_generated",
  "keyframes_generated",
  "narrations_generated",
  "video_segments_generated",
  "video_completed",
];

export function getExpectedEventOrderFromMessages(messages: any[]): string[] {
  const wfList = [...messages].reverse().filter((m: any) => m?.event_type === "workflow_state");
  const latest = wfList[0] as any;
  const raw = latest?.event_data ?? latest;
  const path = raw?.path;
  if (!Array.isArray(path) || path.length === 0) {
    // workflow_state 未到前勿默认「音乐首位」，避免 BGM 并行路径闪一下音乐占位
    return FALLBACK_VIDEO_RESULTS_EVENT_ORDER.filter((e) => e !== "music_generated");
  }
  const mapped = path
    .filter((entry: any) => String(entry?.id || "") !== "analysis")
    .map((entry: any) => WORKFLOW_PATH_ID_TO_PANEL_EVENT[String(entry?.id || "")])
    .filter((x): x is string => typeof x === "string" && x.length > 0);
  const order = mapped.length > 0 ? mapped : FALLBACK_VIDEO_RESULTS_EVENT_ORDER;
  if (!order.includes("video_completed")) {
    const rawIds = path.map((e: any) => String(e?.id || ""));
    const pathHintsFinal =
      rawIds.includes("shots") ||
      rawIds.includes("storyboards") ||
      rawIds.includes("final") ||
      order.includes("video_segments_generated") ||
      order.includes("keyframes_generated");
    if (pathHintsFinal) {
      return [...order, "video_completed"];
    }
  }
  return order;
}

/** 任意一条 workflow_state.path 含视频主链路 id 时，右侧结果区应按视频会话渲染（勿判为仅音乐） */
export function conversationHasVideoWorkflowStatePath(messages: any[] | undefined): boolean {
  if (!messages?.length) {
    return false;
  }
  for (const m of messages) {
    if (m?.event_type !== "workflow_state") {
      continue;
    }
    const raw = (m as any)?.event_data ?? m;
    const p = raw?.path;
    if (!Array.isArray(p) || p.length === 0) {
      continue;
    }
    for (const e of p) {
      const id = String((e as any)?.id || "");
      if (
        id === "analysis" ||
        id === "story_style" ||
        id === "visual" ||
        id === "scenes" ||
        id === "storyboards" ||
        id === "narration" ||
        id === "shots" ||
        id === "final"
      ) {
        return true;
      }
    }
  }
  return false;
}

/** 避免 storyOutlineData 仅为 {} 或占位时误判「故事已完成」，从而在背景音乐阶段仍渲染故事+风格占位 */
/** BGM 并行 path：music 在 story/visual/scenes 之后，未就绪前勿占位/轮询音乐区 */
export function isMusicDeferredInWorkflowPath(messages: any[] | undefined): boolean {
  if (!messages?.length) return false;
  const wfList = [...messages].reverse().filter((m: any) => m?.event_type === "workflow_state");
  const latest = wfList[0] as any;
  if (!latest) return false;
  const raw = latest?.event_data ?? latest;
  const mode = String(raw?.music_mode ?? raw?.music_workflow_mode ?? "").trim();
  if (mode === "bgm_parallel") return true;
  const path = raw?.path;
  if (!Array.isArray(path) || path.length === 0) return false;
  const ids = path.map((e: any) => String(e?.id || ""));
  const musicIdx = ids.indexOf("music");
  if (musicIdx <= 0) return false;
  const before = ids.slice(0, musicIdx).filter((id) => id && id !== "analysis");
  return before.length > 0;
}

export function hasWorkflowPathInMessages(messages: any[] | undefined): boolean {
  if (!messages?.length) return false;
  return messages.some((m: any) => {
    if (m?.event_type !== "workflow_state") return false;
    const raw = m?.event_data ?? m;
    return Array.isArray(raw?.path) && raw.path.length > 0;
  });
}

export function hasRenderableStoryOutlineData(storyOutlineData: any): boolean {
  if (!storyOutlineData || typeof storyOutlineData !== "object") return false;
  return !!(
    storyOutlineData.uuid ||
    storyOutlineData.story_title ||
    storyOutlineData.title ||
    storyOutlineData.description ||
    storyOutlineData.story ||
    (Array.isArray(storyOutlineData.structure) && storyOutlineData.structure.length > 0)
  );
}
