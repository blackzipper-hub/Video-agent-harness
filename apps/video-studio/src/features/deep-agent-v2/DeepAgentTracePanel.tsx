import { useEffect, useMemo, useRef } from "react";
import {
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  Copy,
  Info,
  ListTree,
  Wrench,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { DeepAgentEvent } from "./types";
import type { DeepAgentTokenUsage } from "./types";

interface DeepAgentTracePanelProps {
  events: DeepAgentEvent[];
  open: boolean;
  language: string;
  onClose: () => void;
  tokenUsage?: DeepAgentTokenUsage | null;
}

type TraceTone = "neutral" | "success" | "warning" | "error";

const eventTone = (type: string): TraceTone => {
  if (
    type === "run.failed" ||
    type === "agent.failed" ||
    type === "agent.coordination_failed"
  ) {
    return "error";
  }
  if (type === "task.failed") return "warning";
  if (type === "agent.tool.completed") return "neutral";
  if (type === "run.waiting_input" || type === "run.interruption")
    return "warning";
  if (type.includes("succeeded") || type === "run.completed") return "success";
  return "neutral";
};

const eventCallId = (event: DeepAgentEvent): string => {
  const direct = event.payload?.call_id || event.payload?.callId;
  if (direct) return String(direct);
  const message = event.payload?.message;
  if (!message || typeof message !== "object") return "";
  const source = (message as Record<string, unknown>).source;
  return source && typeof source === "object"
    ? String((source as Record<string, unknown>).callId || "")
    : "";
};

const eventTitle = (
  event: DeepAgentEvent,
  zh: boolean,
  toolNames: Map<string, string>,
): string => {
  const payload = event.payload || {};
  if (event.type === "agent.started")
    return zh ? "协调开始" : "Coordination started";
  if (event.type === "llm.request.started")
    return `${zh ? "模型调用" : "LLM call"} · ${String(payload.model || "unknown")}`;
  if (event.type === "llm.context.ready")
    return zh ? "模型上下文已准备" : "Model context ready";
  if (event.type === "llm.usage")
    return zh ? "Token 用量入账" : "Token usage recorded";
  if (event.type === "llm.attempt")
    return zh ? "模型重试/切换" : "LLM retry/failover";
  if (event.type === "agent.tool.started") {
    return `${zh ? "调用工具" : "Tool call"} · ${String(payload.tool || payload.name || "unknown")}`;
  }
  if (event.type === "agent.tool.completed") {
    const tool = payload.tool || toolNames.get(eventCallId(event)) || "unknown";
    return `${payload.is_error ? (zh ? "工具失败" : "Tool failed") : (zh ? "工具完成" : "Tool completed")} · ${String(tool)}`;
  }
  if (event.type === "agent.step.started")
    return `${zh ? "决策步骤开始" : "Decision step started"} · #${String(payload.step || "")}`;
  if (event.type === "agent.step.completed")
    return `${zh ? "决策步骤完成" : "Decision step completed"} · #${String(payload.step || "")}`;
  if (event.type === "agent.context.updated")
    return zh ? "Agent 上下文已更新" : "Agent context updated";
  if (event.type === "deepseek.permission/preset")
    return zh ? "执行权限预设" : "Execution permission preset";
  if (event.type === "deepseek.sandbox/mode")
    return zh ? "沙箱模式" : "Sandbox mode";
  if (event.type === "deepseek.approval/policy")
    return zh ? "审批策略" : "Approval policy";
  if (event.type === "deepseek.session/title")
    return zh ? "会话标题生成" : "Session title generated";
  if (event.type === "plan.revised") {
    return `${zh ? "计划更新" : "Plan revised"} · #${String(payload.revision || "")}`;
  }
  if (event.type === "task.started") return zh ? "任务开始" : "Task started";
  if (event.type === "task.succeeded")
    return zh ? "任务成功" : "Task succeeded";
  if (event.type === "task.failed") {
    return zh
      ? "生成尝试失败（可重试）"
      : "Generation attempt failed (retryable)";
  }
  if (event.type === "artifact.created")
    return zh ? "产物创建" : "Artifact created";
  if (
    event.type === "agent.failed" ||
    event.type === "agent.coordination_failed"
  ) {
    return zh ? "运行终止失败" : "Run terminated with failure";
  }
  if (event.type === "run.failed") return zh ? "运行失败" : "Run failed";
  if (event.type === "run.completed") return zh ? "运行完成" : "Run completed";
  if (event.type === "run.waiting_input")
    return zh ? "等待用户输入" : "Waiting for input";
  if (event.type === "run.interruption")
    return zh ? "中断已解释并自动处理" : "Interrupt explained and handled";
  if (event.type === "chat.message.created")
    return zh ? "模型响应" : "Model response";
  return event.type;
};

const eventIcon = (type: string) => {
  const tone = eventTone(type);
  if (type === "plan.revised") return ListTree;
  if (type.includes("tool")) return Wrench;
  if (tone === "error" || tone === "warning") return AlertTriangle;
  if (tone === "success") return CheckCircle2;
  return BrainCircuit;
};

const toneClass = (tone: TraceTone) => {
  if (tone === "error") return "text-destructive";
  if (tone === "warning") return "text-amber-600 dark:text-amber-400";
  if (tone === "success") return "text-emerald-500";
  return "text-accent-purple";
};

const eventSummary = (event: DeepAgentEvent, zh: boolean): string => {
  const payload = event.payload || {};
  if (event.type === "run.waiting_input" || event.type === "run.interruption") {
    return [
      payload.what_happened && `Now: ${String(payload.what_happened)}`,
      payload.why_interrupted && `Why: ${String(payload.why_interrupted)}`,
      payload.why_confirm && `Next: ${String(payload.why_confirm)}`,
      payload.skill_name && `Skill: ${String(payload.skill_name)}`,
      payload.skill_resource && `Resource: ${String(payload.skill_resource)}`,
      payload.skill_policy && `Policy: ${String(payload.skill_policy)}`,
      payload.capability_id && `Capability: ${String(payload.capability_id)}`,
      payload.task_id && `Task: ${String(payload.task_id)}`,
    ]
      .filter(Boolean)
      .join("\n");
  }
  if (event.type === "plan.revised") {
    const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
    return [
      payload.reason ? String(payload.reason) : "",
      ...tasks.map((task) => {
        const value = task as Record<string, unknown>;
        return `${String(value.capability_id || "task")}: ${String(value.objective || "")}`;
      }),
      payload.response ? String(payload.response) : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
  if (event.type === "agent.tool.started") {
    return JSON.stringify(payload.input || payload.arguments || {}, null, 2);
  }
  if (event.type === "agent.tool.completed") {
    if (payload.output) return String(payload.output);
    if (payload.error) return typeof payload.error === "string"
      ? payload.error
      : JSON.stringify(payload.error, null, 2);
    return zh ? "工具已返回结果。" : "The tool returned a result.";
  }
  if (event.type === "agent.started") return String(payload.observation || "");
  if (event.type === "llm.request.started") {
    return [
      `${zh ? "Provider" : "Provider"}: ${String(payload.provider || "unknown")}`,
      `${zh ? "模型" : "Model"}: ${String(payload.model || "unknown")}`,
      `${zh ? "推理强度" : "Reasoning effort"}: ${String(payload.reasoning_effort || "default")}`,
      `${zh ? "可用工具" : "Available tools"}: ${String(payload.tool_count || 0)}`,
    ].join("\n");
  }
  if (event.type === "llm.context.ready") {
    return [
      `${zh ? "模型" : "Model"}: ${String(payload.model || "unknown")}`,
      `${zh ? "上下文窗口" : "Context window"}: ${Number(payload.context_window || 0).toLocaleString()} tokens`,
    ].join("\n");
  }
  if (event.type === "llm.usage") {
    return [
      `scope: ${String(payload.scope || "unknown")}`,
      payload.capability_id && `stage: ${String(payload.capability_id)}`,
      `input: ${Number(payload.input_tokens || 0).toLocaleString()}`,
      `output: ${Number(payload.output_tokens || 0).toLocaleString()}`,
      `cached: ${Number(payload.cached_tokens || 0).toLocaleString()}`,
      `reasoning: ${Number(payload.reasoning_tokens || 0).toLocaleString()}`,
      `total: ${Number(payload.total_tokens || 0).toLocaleString()}`,
      payload.requested_tokens && `requested (failed call): ${Number(payload.requested_tokens).toLocaleString()}`,
      `status: ${String(payload.status || "unknown")}`,
    ].filter(Boolean).join("\n");
  }
  if (event.type === "llm.attempt") return JSON.stringify(payload, null, 2);
  if (event.type === "agent.step.started") {
    return zh
      ? "模型正在根据当前上下文决定下一项动作；具体依据以随后记录的工具参数、工具结果和公开回复为准。"
      : "The model is selecting the next action from current context; the following tool input, result, or public answer is the auditable record.";
  }
  if (event.type === "agent.step.completed") {
    return `${zh ? "回合" : "Turn"} ${String(payload.turn || "-")} · ${zh ? "步骤" : "Step"} ${String(payload.step || "-")}`;
  }
  if (event.type === "agent.context.updated") {
    return `${zh ? "新增" : "Inserted"}: ${String(payload.inserted_count || 0)} · ${zh ? "移除" : "Removed"}: ${String(payload.removed_count || 0)}`;
  }
  if (event.type === "deepseek.permission/preset") return String(payload.preset || "");
  if (event.type === "deepseek.sandbox/mode") return String(payload.mode || "");
  if (event.type === "deepseek.approval/policy") return String(payload.policy || "");
  if (event.type === "deepseek.session/title") return String(payload.title || "");
  if (event.type === "chat.message.created") {
    const message = payload.message as Record<string, unknown> | undefined;
    return message?.role === "assistant" ? String(message.content || "") : "";
  }
  if (payload.error) return String(payload.error);
  if (payload.response) return String(payload.response);
  if (payload.task_id) return `task_id: ${String(payload.task_id)}`;
  return "";
};

export function DeepAgentTracePanel({
  events,
  open,
  language,
  onClose,
  tokenUsage,
}: DeepAgentTracePanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const zh = language === "zh";
  const { visibleEvents, toolNames, executionIssueCount } = useMemo(() => {
    const names = new Map<string, string>();
    events.forEach((event) => {
      if (event.type !== "agent.tool.started") return;
      const callId = eventCallId(event);
      if (callId) names.set(callId, String(event.payload.tool || event.payload.name || "unknown"));
    });
    return {
      toolNames: names,
      executionIssueCount: events.filter(
        (event) =>
          (event.type === "agent.tool.completed" && event.payload.is_error === true) ||
          event.type === "task.failed",
      ).length,
      visibleEvents: events.filter((event) => {
        if (event.type === "chat.message.created") {
          const message = event.payload?.message as
            Record<string, unknown> | undefined;
          return message?.role === "assistant";
        }
        return true;
      }),
    };
  }, [events]);

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [open, visibleEvents.length]);

  if (!open) return null;

  const copyTrace = async () => {
    await navigator.clipboard.writeText(
      visibleEvents.map((event) => JSON.stringify(event)).join("\n"),
    );
    toast.success(zh ? "日志已复制" : "Trace copied");
  };

  return (
    <div className="fixed bottom-4 right-4 z-[70] flex h-[min(560px,72vh)] w-[min(460px,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border border-border/70 bg-background/95 shadow-2xl backdrop-blur">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 px-3">
        <div className="flex min-w-0 items-center gap-2">
          <BrainCircuit className="h-4 w-4 text-accent-purple" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">
              {zh ? "Agent 实时轨迹" : "Live Agent trace"}
            </div>
            <div className="text-[10px] text-muted-foreground">
              {zh
                ? "可审计决策与工具调用（非隐藏思维链）"
                : "Auditable decisions and tool calls"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => void copyTrace()}
          >
            <Copy className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-2 p-3">
          {tokenUsage && (
            <div className="grid grid-cols-3 gap-2 rounded-lg border border-accent-purple/25 bg-accent-purple/5 p-2.5 text-center">
              <div><div className="text-sm font-semibold">{tokenUsage.total_tokens.toLocaleString()}</div><div className="text-[10px] text-muted-foreground">{zh ? "总 Token" : "Total tokens"}</div></div>
              <div><div className="text-sm font-semibold">{tokenUsage.calls}</div><div className="text-[10px] text-muted-foreground">{zh ? "模型调用" : "LLM calls"}</div></div>
              <div><div className="text-sm font-semibold">{tokenUsage.failed_calls + tokenUsage.retry_attempts + executionIssueCount}</div><div className="text-[10px] text-muted-foreground">{zh ? "失败/重试" : "Failed/retried"}</div></div>
              <div className="col-span-3 text-left text-[10px] text-muted-foreground">
                {zh ? "输入" : "Input"}: {tokenUsage.input_tokens.toLocaleString()} · {zh ? "输出" : "Output"}: {tokenUsage.output_tokens.toLocaleString()} · {zh ? "缓存命中" : "Cached"}: {tokenUsage.cached_tokens.toLocaleString()}
              </div>
            </div>
          )}
          {visibleEvents.length === 0 && (
            <div className="rounded-lg border border-dashed p-6 text-center text-xs text-muted-foreground">
              {zh
                ? "发送消息后，规划和工具调用会实时显示在这里。"
                : "Planning and tool calls will appear here."}
            </div>
          )}
          {visibleEvents.map((event) => {
            const Icon = eventIcon(event.type);
            const summary = eventSummary(event, zh);
            const tone = event.type === "agent.tool.completed" && event.payload.is_error
              ? "warning"
              : eventTone(event.type);
            return (
              <div
                key={event.id}
                className={
                  tone === "warning"
                    ? "rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5"
                    : tone === "error"
                      ? "rounded-lg border border-destructive/30 bg-destructive/5 p-2.5"
                      : "rounded-lg border border-border/60 bg-muted/20 p-2.5"
                }
              >
                <div className="flex items-start gap-2">
                  <Icon
                    className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${toneClass(tone)}`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-medium">
                        {eventTitle(event, zh, toolNames)}
                      </span>
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        {new Date(event.created_at).toLocaleTimeString()}
                      </span>
                    </div>
                    {summary && (
                      <pre className="mt-1.5 max-h-36 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-muted-foreground">
                        {summary}
                      </pre>
                    )}
                    {tone === "warning" && (
                      <div className="mt-1 flex items-center gap-1 text-[10px] text-amber-700 dark:text-amber-300">
                        <Info className="h-3 w-3" />
                        {zh
                          ? "非终态失败，协调器可能继续重试"
                          : "Non-terminal failure; coordinator may retry"}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>
    </div>
  );
}
