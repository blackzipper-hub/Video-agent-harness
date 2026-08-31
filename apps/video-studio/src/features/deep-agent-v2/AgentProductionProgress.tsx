import { AlertTriangle, CheckCircle2, Circle, Loader2, Wrench } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import type { DeepAgentEvent, DeepAgentRunStatus, DeepAgentTask } from "./types";

export interface RuntimeProductionProgress {
  buildId: string;
  status: string;
  progress: number;
  message: string;
  error?: string;
  steps: Array<{ id: string; name: string; status: string; error?: string; skills: string[] }>;
}

const toolLabels: Record<string, { zh: string; en: string }> = {
  video_project_create: { zh: "创建视频项目", en: "Create video project" },
  video_project_open: { zh: "打开视频项目", en: "Open video project" },
  video_project_inspect: { zh: "读取项目状态", en: "Inspect project" },
  video_project_plan: { zh: "生成视频制作计划", en: "Compile production plan" },
  video_project_build: { zh: "启动视频制作", en: "Start video build" },
  video_change_preview: { zh: "分析修改影响", en: "Preview change impact" },
  video_rebuild_apply: { zh: "执行局部重建", en: "Apply incremental rebuild" },
  video_build_status: { zh: "读取制作进度", en: "Check build status" },
  video_export: { zh: "导出成片", en: "Export final video" },
};

const stepLabels: Record<string, { zh: string; en: string }> = {
  "persist-spec": { zh: "保存剧本与分镜", en: "Save script and shots" },
  "character-reference": { zh: "生成角色参考图", en: "Generate character reference" },
  "keyframe": { zh: "生成镜头关键帧", en: "Generate shot keyframes" },
  "video": { zh: "生成视频片段", en: "Generate video clips" },
  "tail": { zh: "提取连续性尾帧", en: "Extract continuity tail frame" },
  "narration": { zh: "生成旁白", en: "Generate narration" },
  "bgm": { zh: "生成背景音乐", en: "Generate background music" },
  "subtitle": { zh: "生成字幕", en: "Generate subtitles" },
  "timeline": { zh: "编排时间线", en: "Compose timeline" },
  "export": { zh: "合成并导出成片", en: "Compose and export final video" },
  "validate": { zh: "检查质量与连续性", en: "Validate quality and continuity" },
};

function nestedCallId(payload: Record<string, unknown>): string {
  const message = payload.message;
  if (!message || typeof message !== "object") return "";
  const source = (message as Record<string, unknown>).source;
  return source && typeof source === "object"
    ? String((source as Record<string, unknown>).callId || "")
    : "";
}

function readableStepName(name: string, zh: boolean): string {
  const normalized = name.toLowerCase();
  const match = Object.entries(stepLabels).find(([key]) => normalized.includes(key));
  if (match) return zh ? match[1].zh : match[1].en;
  return name.replace(/[-_]/g, " ");
}

export function AgentProductionProgress({
  status,
  isSending,
  isRunning,
  events,
  tasks,
  runtime,
  language,
}: {
  status?: DeepAgentRunStatus;
  isSending: boolean;
  isRunning: boolean;
  events: DeepAgentEvent[];
  tasks: DeepAgentTask[];
  runtime: RuntimeProductionProgress | null;
  language: "zh" | "en";
}) {
  const zh = language === "zh";
  const completedCallIds = new Set(
    events.filter((event) => event.type === "agent.tool.completed")
      .map((event) => nestedCallId(event.payload))
      .filter(Boolean),
  );
  const tools = events.filter((event) => event.type === "agent.tool.started")
    .map((event) => ({
      id: String(event.payload.callId || event.id),
      name: String(event.payload.name || "tool"),
    }))
    .slice(-6);
  const hasBuildTool = tools.some((tool) => tool.name === "video_project_build");
  const hasVisibleActivity = isSending || isRunning || Boolean(runtime) || tools.length > 0 || tasks.length > 0;
  if (!hasVisibleActivity) return null;

  const runtimeActive = runtime && ["queued", "running", "waiting_external"].includes(runtime.status);
  const stoppedWithoutBuild = status === "completed" && !runtime && !hasBuildTool;
  const failed = status === "failed" || status === "cancelled" || runtime?.status === "failed";
  const heading = failed
    ? (zh ? "制作未完成" : "Production did not finish")
    : stoppedWithoutBuild
      ? (zh ? "Agent 已结束，但未启动视频制作" : "Agent finished without starting video production")
      : runtimeActive
        ? (zh ? "正在制作视频" : "Producing video")
        : runtime?.status === "completed"
          ? (zh ? "视频制作完成" : "Video production completed")
          : isSending
            ? (zh ? "正在提交你的需求" : "Submitting your request")
            : (zh ? "Agent 正在理解需求并执行" : "Agent is interpreting and executing your request");
  const progressValue = runtime
    ? Math.max(0, Math.min(100, runtime.progress * 100))
    : tools.length > 0 ? (hasBuildTool ? 20 : 10) : 4;

  return (
    <div
      className={`mx-4 mt-3 rounded-xl border p-3 text-sm shadow-sm ${
        stoppedWithoutBuild || failed
          ? "border-amber-500/40 bg-amber-500/10"
          : "border-accent-purple/30 bg-accent-purple/[0.06]"
      }`}
      data-testid="agent-production-progress"
    >
      <div className="flex items-start gap-2">
        {stoppedWithoutBuild || failed ? (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
        ) : runtime?.status === "completed" ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
        ) : (
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-accent-purple" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="font-medium">{heading}</p>
            <span className="shrink-0 text-[11px] text-muted-foreground">
              {Math.round(progressValue)}%
            </span>
          </div>
          <Progress value={progressValue} className="mt-2 h-1.5" />
          <p className="mt-2 text-xs text-muted-foreground">
            {stoppedWithoutBuild
              ? (zh
                  ? "本轮只读取或说明了项目，没有调用计划与构建；请重新发送制作需求，系统会按 plan → build 执行。"
                  : "This turn only inspected or described the project. Send the production request again to run plan → build.")
              : runtime?.message || (zh
                  ? "这里展示阶段、工具调用和可验证结果，不展示模型私有思维链。"
                  : "Shows phases, tool calls, and verifiable results—not private chain-of-thought.")}
          </p>

          {tools.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {tools.map((tool) => {
                const done = completedCallIds.has(tool.id);
                const label = toolLabels[tool.name];
                return (
                  <div key={tool.id} className="flex items-center gap-2 text-xs">
                    {done ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                    ) : (
                      <Wrench className="h-3.5 w-3.5 text-accent-purple" />
                    )}
                    <span>{label ? (zh ? label.zh : label.en) : tool.name}</span>
                    <span className="ml-auto text-[10px] text-muted-foreground">
                      {done ? (zh ? "已完成" : "Completed") : (zh ? "执行中" : "Running")}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {runtime && runtime.steps.length > 0 && (
            <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
              {runtime.steps.map((step) => (
                <div key={step.id} className="min-w-0 text-xs">
                  <div className="flex min-w-0 items-center gap-2">
                  {step.status === "completed" ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                  ) : ["running", "waiting_external"].includes(step.status) ? (
                    <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent-purple" />
                  ) : (
                    <Circle className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <span className="truncate" title={step.error || step.name}>
                    {readableStepName(step.name, zh)}
                  </span>
                  </div>
                  {step.skills.length > 0 && (
                    <p
                      className="ml-5 mt-0.5 truncate text-[10px] text-muted-foreground"
                      title={step.skills.join(", ")}
                    >
                      Skill: {step.skills.join(", ")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
