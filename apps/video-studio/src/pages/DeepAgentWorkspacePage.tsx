import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, AlertTriangle, BrainCircuit, ChevronDown, Loader2, PanelLeft, Play, RotateCcw, WandSparkles } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ChatSidebar } from "@/components/video/ChatSidebar";
import { MessageArea } from "@/components/video/MessageArea";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { useAuth } from "@/contexts/AuthContext";
import { useIsMobile } from "@/hooks/use-mobile";
import { useLanguage } from "@/i18n/LanguageContext";
import {
  DEFAULT_IMAGE_GENERATION_TOOL,
  DEFAULT_VIDEO_OPTIONS,
  type ImageGenerationToolType,
} from "@/constants/defaults";
import { deepAgentV2Client } from "@/features/deep-agent-v2/client";
import { DeepAgentArtifacts } from "@/features/deep-agent-v2/DeepAgentArtifacts";
import {
  AgentProductionProgress,
  type RuntimeProductionProgress,
} from "@/features/deep-agent-v2/AgentProductionProgress";
import { DeepAgentTracePanel } from "@/features/deep-agent-v2/DeepAgentTracePanel";
import type { DeepAgentSkill, DeepAgentSkillLock, DeepAgentTokenUsage } from "@/features/deep-agent-v2/types";
import { useDeepAgentWorkspace } from "@/features/deep-agent-v2/useDeepAgentWorkspace";
import { unpinConversation } from "@/utils/pinnedConversations";
import { resolveUserOptionDurationSec } from "@/utils/targetVideoDuration";

const activeTaskStatuses = new Set(["proposed", "blocked", "ready", "running", "waiting_external"]);
const CREATE_DEFAULT_DURATION_SECONDS = 15;
const videoModelValues: Record<string, string> = {
  Auto: "auto",
  "Seedance 1.0 Pro Fast": "pollo_seedance",
  "Seedance 1.5 Pro Fast": "pollo_seedance_v1_5",
  "Seedance 2.0": "seedance_2_i2v",
  "Seedance 2.0 Turbo": "seedance_2_i2v_turbo",
  "Seedance 2.0 Fast": "seedance_2_fast_i2v",
  "Seedance 2.0 Fast Turbo": "seedance_2_fast_i2v_turbo",
  "Kling v3 Std": "kling_v3_std",
  "HappyHorse 1.0": "happyhorse_1_0_i2v",
  "HappyHorse 1.1": "happyhorse_1_1_i2v",
  Sora2: "openai_sora",
  "Sora2 Pro": "openai_sora_pro",
};

export default function DeepAgentWorkspacePage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { threadId: routeThreadId } = useParams<{ threadId?: string }>();
  const initialRequest = location.state as {
    initialPrompt?: string;
    uploadedFiles?: File[];
    userOption?: Record<string, unknown>;
    shouldAutoSend?: boolean;
  } | null;
  const shouldStartFromHome = Boolean(initialRequest?.shouldAutoSend && initialRequest.initialPrompt?.trim());
  const isMobile = useIsMobile();
  const { language } = useLanguage();
  const { isLoggedIn, isLoading, user, logout } = useAuth();
  const workspaceBase = location.pathname.includes("/deep-agent-v2")
    ? "deep-agent-v2"
    : "create";
  const workspacePath = useCallback((threadId?: string | null) => {
    const base = `/${language}/${workspaceBase}`;
    const normalized = (threadId || "").trim();
    return normalized ? `${base}/${encodeURIComponent(normalized)}` : base;
  }, [language, workspaceBase]);
  const workspace = useDeepAgentWorkspace({
    // `/create` is a product entry, so a bare route always starts a fresh
    // project. The general Deep Agent workspace keeps its recent-run behavior.
    autoSelect: workspaceBase !== "create" && !shouldStartFromHome && !routeThreadId,
    routeThreadId: routeThreadId || null,
  });
  const { state } = workspace;
  const sendWorkspaceMessage = workspace.sendMessage;
  const initialRequestHandled = useRef(false);
  const [message, setMessage] = useState("");
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const defaultDuration = workspaceBase === "create"
    ? CREATE_DEFAULT_DURATION_SECONDS
    : DEFAULT_VIDEO_OPTIONS.duration;
  const [duration, setDuration] = useState([defaultDuration]);
  const [aspectRatio, setAspectRatio] = useState(DEFAULT_VIDEO_OPTIONS.aspectRatio);
  const [resolution, setResolution] = useState(DEFAULT_VIDEO_OPTIONS.resolution);
  const [selectedModel, setSelectedModel] = useState(DEFAULT_VIDEO_OPTIONS.selectedModel);
  const [imageGenerationTool, setImageGenerationTool] = useState<ImageGenerationToolType>(
    DEFAULT_IMAGE_GENERATION_TOOL,
  );
  const [lipsyncCoverage, setLipsyncCoverage] = useState(DEFAULT_VIDEO_OPTIONS.lipsyncCoverage);
  const [lipsyncVideoModel, setLipsyncVideoModel] = useState(DEFAULT_VIDEO_OPTIONS.lipsyncVideoModel);
  const [enableContinuityMode, setEnableContinuityMode] = useState(
    DEFAULT_VIDEO_OPTIONS.enableContinuityMode,
  );
  const [enableKeyframeReflection, setEnableKeyframeReflection] = useState(
    DEFAULT_VIDEO_OPTIONS.enableKeyframeReflection,
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [mobileTab, setMobileTab] = useState<"chat" | "artifacts">("chat");
  const [isUploading, setIsUploading] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [tokenUsage, setTokenUsage] = useState<DeepAgentTokenUsage | null>(null);
  const [skills, setSkills] = useState<DeepAgentSkill[]>([]);
  const [selectedSkillName, setSelectedSkillName] = useState("");
  const [projectSkillLocks, setProjectSkillLocks] = useState<DeepAgentSkillLock[]>([]);
  const [isInstallingSkill, setIsInstallingSkill] = useState(false);
  const [runtimeProgress, setRuntimeProgress] = useState<RuntimeProductionProgress | null>(null);
  const skillUploadRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setRuntimeProgress(null);
  }, [state.selectedRunId]);

  useEffect(() => {
    if (!traceOpen || !state.selectedRunId) {
      if (!state.selectedRunId) setTokenUsage(null);
      return;
    }
    const controller = new AbortController();
    void deepAgentV2Client.getTokenUsage(state.selectedRunId, controller.signal)
      .then(setTokenUsage)
      .catch(() => undefined);
    return () => controller.abort();
  }, [traceOpen, state.selectedRunId, state.traceEvents.length]);

  const status = state.snapshot?.run.status;
  const isWaitingInput = status === "waiting_input";
  const hasActiveTask = Boolean(state.snapshot?.tasks.some((task) =>
    activeTaskStatuses.has(task.status)
  ));
  // Downstream interrupt keeps the task as waiting_external while the run is
  // waiting_input — do not treat that as "still generating".
  const isRunning =
    !isWaitingInput && (
      hasActiveTask ||
      state.isSending ||
      state.isStreaming ||
      status === "planning" ||
      status === "running" ||
      status === "waiting_external" ||
      isUploading
    );
  const selectedSkill = skills.find((skill) => skill.name === selectedSkillName);
  const activeThreadId = workspace.sessionThreadId;
  const enabledProjectSkillIds = new Set(
    projectSkillLocks.filter((lock) => lock.enabled).map((lock) => lock.skill_id),
  );
  const waitingInputPrompt = language === "zh"
    ? [
        state.notice?.whatHappened && `现在：${state.notice.whatHappened}`,
        state.notice?.whyInterrupted && `原因：${state.notice.whyInterrupted}`,
        state.notice?.whyConfirm && `接下来：${state.notice.whyConfirm}`,
        state.notice?.skillName && `触发 Skill：${state.notice.skillName}`,
        state.notice?.skillResource && `规则来源：${state.notice.skillResource}`,
        state.notice?.skillPolicy && `确认规则：${state.notice.skillPolicy}`,
      ].filter(Boolean).join("\n")
      || (
        state.notice?.willAutoResume
          ? `配乐/分镜等中间步骤做完后，系统会稍停一下，大约 ${state.notice.autoResumeSeconds || 15} 秒后自动继续。你也可以马上点「继续生成」。`
          : "中间步骤已经完成，系统先停一下。请点「继续生成」进入下一步；右侧工作区可查看已生成的大纲、角色图等内容。"
      )
    : [
        state.notice?.whatHappened && `Now: ${state.notice.whatHappened}`,
        state.notice?.whyInterrupted && `Why: ${state.notice.whyInterrupted}`,
        state.notice?.whyConfirm && `Next: ${state.notice.whyConfirm}`,
        state.notice?.skillName && `Skill: ${state.notice.skillName}`,
        state.notice?.skillResource && `Policy source: ${state.notice.skillResource}`,
        state.notice?.skillPolicy && `Policy: ${state.notice.skillPolicy}`,
      ].filter(Boolean).join("\n")
      || (
        state.notice?.willAutoResume
          ? `A production step finished. Auto-continues in ~${state.notice.autoResumeSeconds || 15}s, or tap Continue generation.`
          : "A production step finished. Tap Continue generation to proceed. Check the workspace for outline/characters already made."
      );
  const chatMessages = useMemo(
    () => {
      const mapped = state.messages.map((item) => {
        const metadataFiles = Array.isArray(item.metadata?.input_files)
          ? item.metadata.input_files.filter((file): file is Record<string, unknown> =>
              Boolean(file) && typeof file === "object",
            )
          : [];
        const attachmentData = metadataFiles.reduce<{
          images: Record<string, unknown>[];
          audio_files: Record<string, unknown>[];
          video_files: Record<string, unknown>[];
        }>((result, file) => {
          if (typeof file.url !== "string" || !file.url) return result;
          if (file.type === "image") result.images.push(file);
          if (file.type === "audio" || file.type === "music") result.audio_files.push(file);
          if (file.type === "video") result.video_files.push(file);
          return result;
        }, { images: [], audio_files: [], video_files: [] });
        const hasAttachments = metadataFiles.length > 0;
        return {
          id: item.id,
          message_id: item.id,
          run_id: item.run_id,
          role: item.role === "assistant" ? "ai" : item.role,
          content: item.content,
          timestamp: item.created_at,
          event_type: item.event_type
            || (item.role === "assistant" ? "assistant_message" : "user_input"),
          event_data: {
            ...(item.event_data || {}),
            ...(hasAttachments ? attachmentData : {}),
          },
        };
      });
      if (!isWaitingInput || !state.selectedRunId) return mapped;
      return [
        ...mapped,
        {
          id: `waiting-input-hint-${state.selectedRunId}`,
          message_id: `waiting-input-hint-${state.selectedRunId}`,
          run_id: state.selectedRunId,
          role: "ai",
          content: waitingInputPrompt,
          timestamp: new Date().toISOString(),
          event_type: "assistant_message",
          event_data: { waiting_input: true },
        },
      ];
    },
    [isWaitingInput, state.messages, state.selectedRunId, waitingInputPrompt],
  );

  const resetComposer = () => {
    setMessage("");
    setUploadedFiles([]);
    setDuration([defaultDuration]);
    setAspectRatio(DEFAULT_VIDEO_OPTIONS.aspectRatio);
    setResolution(DEFAULT_VIDEO_OPTIONS.resolution);
    setSelectedModel(DEFAULT_VIDEO_OPTIONS.selectedModel);
    setImageGenerationTool(DEFAULT_IMAGE_GENERATION_TOOL);
    setLipsyncCoverage(DEFAULT_VIDEO_OPTIONS.lipsyncCoverage);
    setLipsyncVideoModel(DEFAULT_VIDEO_OPTIONS.lipsyncVideoModel);
    setEnableContinuityMode(DEFAULT_VIDEO_OPTIONS.enableContinuityMode);
    setEnableKeyframeReflection(DEFAULT_VIDEO_OPTIONS.enableKeyframeReflection);
  };

  const startNewSession = () => {
    resetComposer();
    setMobileTab("chat");
    const threadId = workspace.newSession();
    // Give every draft its own route immediately. A bare workspace route is
    // allowed to auto-select the latest run and would overwrite this draft.
    navigate(workspacePath(threadId), { replace: false, state: null });
  };

  const selectSession = (runId: string) => {
    setMessage("");
    setUploadedFiles([]);
    setMobileTab("chat");
    if (runId === workspace.pendingThreadId) {
      navigate(workspacePath(runId), { replace: true, state: null });
      return;
    }
    const threadId = state.runs.find((run) => run.id === runId)?.thread_id;
    workspace.selectRun(runId);
    if (threadId) {
      navigate(workspacePath(threadId), { replace: true, state: null });
    }
  };

  const deleteSession = async (runId: string, event: React.MouseEvent) => {
    event.stopPropagation();
    const run = state.runs.find((item) => item.id === runId);
    if (!run) return;
    const confirmed = window.confirm(
      language === "zh"
        ? "确定永久删除此对话及其任务、日志和数据库记录吗？此操作不可撤销。已生成的媒体文件不会从磁盘删除。"
        : "Permanently delete this conversation, its tasks, logs, and database records? This cannot be undone. Generated media files will remain on disk.",
    );
    if (!confirmed) return;
    try {
      const deletingSelected = state.selectedRunId === runId;
      await workspace.deleteRun(runId);
      unpinConversation(runId);
      toast.success(language === "zh" ? "对话和任务已永久删除" : "Conversation and tasks deleted");
      if (deletingSelected) startNewSession();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  // Keep the address bar aligned with the active conversation thread.
  useEffect(() => {
    // Draft conversations have a pending thread id but no durable run yet.
    // Sync only durable runs; the first successful send selects the new run
    // and this effect then attaches its thread id exactly once.
    if (!state.selectedRunId || !activeThreadId) return;
    const expected = workspacePath(activeThreadId);
    if (location.pathname === expected) return;
    // Avoid fighting an intentional bare /create while a draft without thread is impossible
    // (newSession always allocates one). Sync whenever we know the thread.
    navigate(expected, { replace: true, state: location.state });
  }, [activeThreadId, location.pathname, location.state, navigate, state.selectedRunId, workspacePath]);

  useEffect(() => {
    if (
      !isLoggedIn ||
      initialRequestHandled.current ||
      !shouldStartFromHome ||
      !initialRequest?.initialPrompt
    ) {
      return;
    }
    initialRequestHandled.current = true;
    const submitInitialRequest = async () => {
      const files = initialRequest.uploadedFiles || [];
      const inputFiles = files.length ? await deepAgentV2Client.uploadFiles(files) : [];
      await sendWorkspaceMessage(initialRequest.initialPrompt!, {
        user_option: initialRequest.userOption,
        input_files: inputFiles,
      });
    };
    void submitInitialRequest().catch((error) => {
      setMessage(initialRequest.initialPrompt || "");
      setUploadedFiles(initialRequest.uploadedFiles || []);
      toast.error(error instanceof Error ? error.message : String(error));
    });
  }, [
    initialRequest,
    isLoggedIn,
    language,
    location.pathname,
    navigate,
    shouldStartFromHome,
    sendWorkspaceMessage,
  ]);

  useEffect(() => {
    if (!isLoggedIn) return;
    const controller = new AbortController();
    void deepAgentV2Client.listSkills(controller.signal)
      .then((items) => setSkills(items.filter((item) => item.enabled)))
      .catch((error) => {
        if (!controller.signal.aborted) {
          toast.error(error instanceof Error ? error.message : String(error));
        }
      });
    return () => controller.abort();
  }, [isLoggedIn]);

  useEffect(() => {
    if (!isLoggedIn || !activeThreadId || !state.snapshot?.run.id) {
      setProjectSkillLocks([]);
      return;
    }
    const controller = new AbortController();
    void deepAgentV2Client.listProjectSkills(activeThreadId, controller.signal)
      .then((locks) => {
        if (!controller.signal.aborted) setProjectSkillLocks(locks);
      })
      .catch((error) => {
        // A draft URL has no project yet. Do not turn that into a workspace error.
        if (!controller.signal.aborted && !(error instanceof Error && /not found/i.test(error.message))) {
          toast.error(error instanceof Error ? error.message : String(error));
        }
      });
    return () => controller.abort();
  }, [activeThreadId, isLoggedIn, state.snapshot?.run.id]);

  const setProjectSkill = async (skill: DeepAgentSkill, enabled: boolean) => {
    if (!activeThreadId || !state.snapshot?.run.id) {
      toast.error(language === "zh" ? "请先发送第一条消息以创建项目" : "Send the first message to create this project first.");
      return;
    }
    try {
      const lock = await deepAgentV2Client.setProjectSkillEnabled(activeThreadId, skill.name, enabled);
      setProjectSkillLocks((previous) => [
        ...previous.filter((item) => item.skill_id !== lock.skill_id),
        lock,
      ]);
      if (enabled) setSelectedSkillName(skill.name);
      if (!enabled && selectedSkillName === skill.name) setSelectedSkillName("");
      toast.success(enabled
        ? (language === "zh" ? `已为当前项目启用 ${skill.name}` : `${skill.name} enabled for this project`)
        : (language === "zh" ? `已为当前项目停用 ${skill.name}` : `${skill.name} disabled for this project`));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  const installSkillBundle = async (file?: File | null) => {
    if (!file || isInstallingSkill) return;
    if (!/\.zip$/i.test(file.name)) {
      toast.error(language === "zh" ? "请上传 .zip 格式的 Skill 包" : "Upload a .zip Skill bundle.");
      return;
    }
    setIsInstallingSkill(true);
    try {
      const installed = await deepAgentV2Client.installProjectSkill(file);
      const refreshed = await deepAgentV2Client.listSkills();
      setSkills(refreshed.filter((item) => item.enabled));
      setSelectedSkillName(installed.name);
      toast.success(language === "zh" ? `已安装 Skill：${installed.name}` : `Installed Skill: ${installed.name}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setIsInstallingSkill(false);
      if (skillUploadRef.current) skillUploadRef.current.value = "";
    }
  };

  useEffect(() => {
    const option = state.snapshot?.run.user_option;
    if (!option) return;
    if (typeof option.duration === "number") setDuration([option.duration]);
    if (option.aspect_ratio === "16:9" || option.aspect_ratio === "1:1" || option.aspect_ratio === "9:16") {
      setAspectRatio(option.aspect_ratio);
    }
    if (option.resolution === "480p" || option.resolution === "720p" || option.resolution === "1080p") {
      setResolution(option.resolution);
    }
    if (typeof option.video_generation_tool === "string") {
      const label = Object.entries(videoModelValues).find(
        ([, value]) => value === option.video_generation_tool,
      )?.[0];
      if (label) setSelectedModel(label);
    }
    if (
      option.image_generation_tool === "nano_banana" ||
      option.image_generation_tool === "nano_banana_2" ||
      option.image_generation_tool === "nano_banana_pro" ||
      option.image_generation_tool === "seedream" ||
      option.image_generation_tool === "gpt_image_2"
    ) {
      setImageGenerationTool(option.image_generation_tool);
    }
    if (typeof option.lipsync_coverage === "number") setLipsyncCoverage(option.lipsync_coverage);
    if (typeof option.lipsync_video_tool === "string") setLipsyncVideoModel(option.lipsync_video_tool);
    if (typeof option.enable_continuity_mode === "boolean") {
      setEnableContinuityMode(option.enable_continuity_mode);
    }
    if (typeof option.enable_keyframe_reflection === "boolean") {
      setEnableKeyframeReflection(option.enable_keyframe_reflection);
    }
  }, [state.snapshot?.run.id, state.snapshot?.run.user_option]);

  const persistedChats = state.runs.map((run) => ({
    id: run.id,
    title: run.title || (language === "zh" ? "未命名任务" : "Untitled task"),
    thread_id: run.thread_id,
    conversation_id: 0,
    preview: run.last_response,
    created_at: run.created_at,
    last_active_at: run.updated_at,
    agent_type: "auto",
  }));
  const chats = workspace.pendingThreadId && !state.selectedRunId
    ? [{
        id: workspace.pendingThreadId,
        title: language === "zh" ? "新对话" : "New conversation",
        thread_id: workspace.pendingThreadId,
        conversation_id: 0,
        preview: "",
        created_at: new Date().toISOString(),
        last_active_at: new Date().toISOString(),
        agent_type: "auto",
      }, ...persistedChats]
    : persistedChats;

  const currentUserOption = (requestText?: string) => ({
    duration: resolveUserOptionDurationSec(duration[0], requestText),
    aspect_ratio: aspectRatio,
    resolution,
    video_generation_tool: videoModelValues[selectedModel] || "auto",
    image_generation_tool: imageGenerationTool,
    lipsync_coverage: lipsyncCoverage,
    lipsync_video_tool: lipsyncVideoModel,
    enable_continuity_mode: enableContinuityMode,
    enable_keyframe_reflection: enableKeyframeReflection,
  });

  const send = async (content = message) => {
    const hasText = Boolean(content.trim());
    const hasFiles = uploadedFiles.length > 0;
    if ((!hasText && !hasFiles) || isUploading || state.isSending) return;
    const normalizedContent = content.trim() || (
      language === "zh" ? "请分析并使用我上传的附件。" : "Please analyze and use the uploaded attachment."
    );
    setMessage("");
    setIsUploading(true);
    try {
      const selectedKind = selectedSkill?.kind || selectedSkill?.metadata?.kind;
      const skillOptions = selectedSkill
        ? selectedKind === "workflow"
          ? { workflow_id: selectedSkill.name }
          : { activated_skill_ids: [selectedSkill.name] }
        : {};
      const inputFiles = hasFiles
        ? await deepAgentV2Client.uploadFiles(uploadedFiles)
        : [];
      if (hasFiles && inputFiles.length !== uploadedFiles.length) {
        throw new Error(language === "zh" ? "部分附件上传失败，请重试。" : "Some attachments failed to upload. Please try again.");
      }
      const userOption = currentUserOption(normalizedContent);
      if (userOption.duration !== duration[0]) setDuration([userOption.duration]);
      if (isWaitingInput) {
        await workspace.resume(normalizedContent, {
          user_option: userOption,
          input_files: inputFiles,
          ...skillOptions,
        });
        setUploadedFiles([]);
        return;
      }
      await workspace.sendMessage(normalizedContent, {
        user_option: userOption,
        input_files: inputFiles,
        ...skillOptions,
      });
      setUploadedFiles([]);
    } catch (error) {
      setMessage(content);
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setIsUploading(false);
    }
  };

  useEffect(() => {
    if (!isLoading && !isLoggedIn) {
      navigate("/auth", { replace: true, state: { returnTo: `/${language}/create` } });
    }
  }, [isLoading, isLoggedIn, language, navigate]);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-accent-purple" />
      </div>
    );
  }

  if (!isLoggedIn) {
    return null;
  }

  const messageArea = (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/50 px-3">
        <div className="flex items-center gap-2">
          {isMobile && (
            <Button variant="ghost" size="icon" onClick={() => setMobileSidebarOpen(true)}>
              <PanelLeft className="h-4 w-4" />
            </Button>
          )}
          <span className="max-w-56 truncate text-sm font-medium">
            {state.snapshot?.run.title || (language === "zh" ? "新任务" : "New task")}
          </span>
          {status && (
            <span className="text-xs capitalize text-muted-foreground">
              {status.replace("_", " ")}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={skillUploadRef}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={(event) => void installSkillBundle(event.target.files?.[0])}
          />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant={selectedSkill ? "secondary" : "ghost"}>
                <WandSparkles className="mr-1.5 h-3.5 w-3.5" />
                <span className="max-w-28 truncate">
                  {selectedSkill?.name || (language === "zh" ? "选择 Skill" : "Select Skill")}
                </span>
                <ChevronDown className="ml-1 h-3.5 w-3.5 opacity-60" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-80">
              <DropdownMenuLabel>
                {language === "zh" ? "显式激活 Skill" : "Explicitly activate a Skill"}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <div className="max-h-[60vh] overflow-y-auto pr-1">
                <DropdownMenuRadioGroup
                  value={selectedSkillName || "__auto__"}
                  onValueChange={(value) => setSelectedSkillName(value === "__auto__" ? "" : value)}
                >
                  <DropdownMenuRadioItem value="__auto__">
                    <div>
                      <div className="font-medium">{language === "zh" ? "自动选择" : "Automatic"}</div>
                      <div className="text-xs text-muted-foreground">
                        {language === "zh" ? "由 Agent 根据任务选择 Skill" : "Let the Agent choose for the task"}
                      </div>
                    </div>
                  </DropdownMenuRadioItem>
                  {skills.map((skill) => (
                    <DropdownMenuRadioItem key={skill.name} value={skill.name}>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 truncate font-medium">
                          <span>${skill.name}</span>
                          {enabledProjectSkillIds.has(skill.name) && (
                            <span className="rounded bg-accent-purple/15 px-1 py-0.5 text-[9px] font-medium text-accent-purple">
                              {language === "zh" ? "项目已锁定" : "Project locked"}
                            </span>
                          )}
                        </div>
                        <div className="line-clamp-2 text-xs text-muted-foreground">{skill.description}</div>
                      </div>
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </div>
              {state.snapshot?.run.id && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuLabel className="text-xs">
                    {language === "zh" ? "项目 Skill 锁定" : "Project Skill locks"}
                  </DropdownMenuLabel>
                  <div className="max-h-40 overflow-y-auto">
                    {skills.map((skill) => {
                      const locked = projectSkillLocks.find((item) => item.skill_id === skill.name);
                      const enabled = Boolean(locked?.enabled);
                      return (
                        <DropdownMenuItem
                          key={`lock-${skill.name}`}
                          className="flex items-center justify-between gap-3"
                          onSelect={(event) => {
                            event.preventDefault();
                            void setProjectSkill(skill, !enabled);
                          }}
                        >
                          <span className="truncate text-xs">${skill.name}</span>
                          <span className={enabled ? "text-[10px] text-emerald-600" : "text-[10px] text-muted-foreground"}>
                            {enabled ? (language === "zh" ? "已启用" : "Enabled") : (language === "zh" ? "启用" : "Enable")}
                          </span>
                        </DropdownMenuItem>
                      );
                    })}
                  </div>
                </>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem
                disabled={isInstallingSkill}
                onSelect={(event) => {
                  event.preventDefault();
                  skillUploadRef.current?.click();
                }}
              >
                {isInstallingSkill
                  ? (language === "zh" ? "正在安装 Skill…" : "Installing Skill…")
                  : (language === "zh" ? "上传外部 Skill 包 (.zip)" : "Upload external Skill (.zip)")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            size="sm"
            variant={traceOpen ? "secondary" : "ghost"}
            onClick={() => setTraceOpen((value) => !value)}
          >
            <BrainCircuit className="mr-1.5 h-3.5 w-3.5" />
            {language === "zh" ? "轨迹" : "Trace"}
            {state.traceEvents.length > 0 && (
              <span className="ml-1.5 rounded-full bg-accent-purple/15 px-1.5 text-[10px] text-accent-purple">
                {state.traceEvents.length}
              </span>
            )}
          </Button>
          {isWaitingInput && (
            <Button
              size="sm"
              variant="default"
              onClick={() => void workspace.resume(language === "zh" ? "继续生成" : "Continue generation")}
            >
              <Play className="mr-1.5 h-3.5 w-3.5" />
              {language === "zh" ? "继续生成" : "Continue generation"}
            </Button>
          )}
          {(status === "cancelled" || status === "failed") && (
            <Button size="sm" variant="outline" onClick={() => void workspace.resume(language === "zh" ? "恢复并继续" : "Resume and continue")}>
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              {language === "zh" ? "恢复" : "Resume"}
            </Button>
          )}
        </div>
      </div>
      {isWaitingInput && (
        <div className={
          state.notice?.willAutoResume
            ? "flex flex-wrap items-start gap-3 border-b border-sky-500/40 bg-sky-500/10 px-4 py-3 text-sm text-sky-950 dark:text-sky-100"
            : "flex flex-wrap items-start gap-3 border-b border-amber-500/40 bg-amber-500/15 px-4 py-3 text-sm text-amber-950 dark:text-amber-100"
        }>
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <p className="font-medium">
              {state.notice?.confirmationRequired
                ? (language === "zh" ? "此中断需要你确认" : "This interruption needs confirmation")
                : state.notice?.willAutoResume
                ? (language === "zh" ? "先停一下 · 很快会自动继续" : "Paused briefly · auto-continue soon")
                : (language === "zh" ? "执行已暂停" : "Execution paused")}
            </p>
            <p className="text-xs opacity-95">
              <span className="font-semibold">{language === "zh" ? "现在：" : "Now: "}</span>
              {state.notice?.whatHappened
                || (language === "zh" ? "视频制作做到一半先停住了" : "Video production paused mid-way")}
            </p>
            <p className="text-xs opacity-95">
              <span className="font-semibold">{language === "zh" ? "原因：" : "Why: "}</span>
              {state.notice?.whyInterrupted
                || (language === "zh"
                  ? "做完当前这一步后会稍停，方便你确认结果。"
                  : "The system pauses after this step so you can confirm the result.")}
            </p>
            <p className="text-xs opacity-95">
              <span className="font-semibold">{language === "zh" ? "接下来：" : "Next: "}</span>
              {state.notice?.whyConfirm
                || waitingInputPrompt}
            </p>
            {(state.notice?.skillName || state.notice?.capabilityId || state.notice?.stage) && (
              <p className="text-xs opacity-95">
                <span className="font-semibold">{language === "zh" ? "触发来源：" : "Triggered by: "}</span>
                {[
                  state.notice?.skillName && `Skill: ${state.notice.skillName}`,
                  state.notice?.skillResource,
                  state.notice?.capabilityId && `Capability: ${state.notice.capabilityId}`,
                  state.notice?.stage && `Stage: ${state.notice.stage}`,
                  state.notice?.taskId && `Task: ${state.notice.taskId}`,
                ].filter(Boolean).join(" · ")}
              </p>
            )}
            {state.notice?.skillPolicy && (
              <p className="rounded bg-background/40 px-2 py-1 text-[11px] opacity-90">
                <span className="font-semibold">{language === "zh" ? "Skill 确认规则：" : "Skill policy: "}</span>
                {state.notice.skillPolicy}
              </p>
            )}
            <p className="text-[11px] opacity-80">
              {state.notice?.confirmationRequired
                ? (language === "zh"
                  ? "这是已验证来源的确认请求；检查结果后点「继续生成」。"
                  : "This confirmation request has a verified source; review and continue.")
                : (language === "zh" ? "不需要人工确认，任务会自动继续。" : "No confirmation is required.")}
            </p>          </div>
          <Button
            size="sm"
            className="shrink-0"
            onClick={() => void workspace.resume(language === "zh" ? "继续生成" : "Continue generation")}
          >
            <Play className="mr-1.5 h-3.5 w-3.5" />
            {language === "zh" ? "继续生成" : "Continue generation"}
          </Button>
        </div>
      )}
      {state.notice && !isWaitingInput && (
        <div
          className={
            state.notice.severity === "error"
              ? "flex items-center gap-2 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive"
              : state.notice.severity === "warning"
                ? "flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-800 dark:text-amber-200"
                : "flex items-center gap-2 border-b border-border/60 bg-muted/40 px-4 py-2 text-xs text-muted-foreground"
          }
        >
          {state.notice.severity === "error" ? (
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          ) : (
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          )}
          <div className="min-w-0 flex-1">
            <p className="whitespace-pre-line">{state.notice.message}</p>
            {(state.notice.skillName || state.notice.capabilityId) && (
              <p className="mt-1 text-[10px] opacity-75">
                {[state.notice.skillName && `Skill: ${state.notice.skillName}`, state.notice.capabilityId]
                  .filter(Boolean).join(" · ")}
              </p>
            )}
          </div>
          {state.notice.severity !== "error" && (
            <span className="shrink-0 text-[10px] opacity-70">
              {language === "zh" ? "任务仍在继续" : "Run still in progress"}
            </span>
          )}
        </div>
      )}
      <div className="relative min-h-0 flex-1">
        {state.isHydrating && !state.snapshot && (
          <div className="absolute inset-0 z-30 flex items-center justify-center bg-background">
            <Loader2 className="h-5 w-5 animate-spin text-accent-purple" />
          </div>
        )}
        <div className="flex h-full min-h-0 flex-col">
          <AgentProductionProgress
            status={status}
            isSending={state.isSending || isUploading}
            isRunning={isRunning}
            events={state.traceEvents}
            tasks={state.snapshot?.tasks || []}
            runtime={runtimeProgress}
            language={language}
          />
          <div className="min-h-0 flex-1">
        <MessageArea
          chatTitle={state.snapshot?.run.title || (language === "zh" ? "新任务" : "New task")}
          messages={chatMessages}
          message={message}
          uploadedFiles={uploadedFiles}
          isGenerating={isRunning}
          isInFlight={isRunning}
          showAssistantThinking={status === "planning" || state.isSending}
          hideHeader
          threadId={activeThreadId}
          conversationId={state.selectedRunId || activeThreadId}
          onMessageChange={setMessage}
          onFileUpload={(files) => setUploadedFiles((current) => [...current, ...files])}
          onFileRemove={(index) => setUploadedFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}
          onFileReplace={(index, file) => setUploadedFiles((current) => current.map((item, itemIndex) => itemIndex === index ? file : item))}
          onSendMessage={() => void send()}
          onSendWithPrompt={(prompt) => void send(prompt)}
          onActionSuggestionClick={(suggestion) => {
            void workspace.sendSuggestion(suggestion, {
              user_option: currentUserOption(),
            }).catch((error) => toast.error(error.message));
          }}
          onCancelGeneration={() => void workspace.cancel()}
          showTodoList={false}
          showActionSuggestions
          streamedActionSuggestions={state.suggestions}
          agentType="auto"
          duration={duration}
          aspectRatio={aspectRatio}
          resolution={resolution}
          selectedModel={selectedModel}
          imageGenerationTool={imageGenerationTool}
          lipsyncCoverage={lipsyncCoverage}
          lipsyncVideoModel={lipsyncVideoModel}
          enableContinuityMode={enableContinuityMode}
          enableKeyframeReflection={enableKeyframeReflection}
          onDurationChange={setDuration}
          onAspectRatioChange={setAspectRatio}
          onResolutionChange={setResolution}
          onModelChange={setSelectedModel}
          onImageGenerationToolChange={setImageGenerationTool}
          onLipsyncCoverageChange={setLipsyncCoverage}
          onLipsyncVideoModelChange={setLipsyncVideoModel}
          onEnableContinuityModeChange={setEnableContinuityMode}
          onEnableKeyframeReflectionChange={setEnableKeyframeReflection}
          isInputDisabled={state.isHydrating && !state.snapshot}
        />
          </div>
        </div>
      </div>
    </div>
  );

  const artifactsArea = state.snapshot ? (
    <DeepAgentArtifacts
      snapshot={state.snapshot}
      onSelectArtifact={(id) => void workspace.selectArtifact(id)}
      onExtractFrame={(options) => workspace.extractFrame(options)}
      onRefresh={() => void workspace.refresh()}
      onRuntimeProgress={setRuntimeProgress}
    />
  ) : (
    <div className="flex h-full items-center justify-center bg-white px-8 text-center text-sm text-muted-foreground dark:bg-black">
      {state.isHydrating
        ? <Loader2 className="h-5 w-5 animate-spin" />
        : language === "zh"
          ? "发送消息开始新的创作任务"
          : "Send a message to begin a new creation task."}
    </div>
  );

  return (
    <div className="flex h-dvh min-h-0 overflow-hidden bg-background">
      {!isMobile && (
        <ChatSidebar
          collapsed={sidebarCollapsed}
          selectedChat={state.selectedRunId || workspace.pendingThreadId}
          chats={chats}
          userInfo={{ email: user?.email || "" }}
          userCredits={{ balance: user?.credits || 0 }}
          isLoadingChats={state.isLoadingRuns}
          hasMoreChats={false}
          showConversationActions={false}
          showDeleteAction
          onToggleCollapse={() => setSidebarCollapsed((value) => !value)}
          onNewTask={startNewSession}
          onSelectChat={selectSession}
          onDeleteChat={(id, event) => void deleteSession(id, event)}
          onTogglePin={(_, event) => event.stopPropagation()}
          onLogout={() => void logout().then(() => navigate("/auth"))}
        />
      )}

      {isMobile ? (
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <div className={mobileTab === "chat" ? "h-full min-h-0 pb-16" : "hidden"}>{messageArea}</div>
          <div className={mobileTab === "artifacts" ? "h-full min-h-0 pb-16" : "hidden"}>{artifactsArea}</div>
          <div className="fixed bottom-3 left-1/2 z-40 flex -translate-x-1/2 rounded-full border border-border/60 bg-background/90 p-1 shadow-lg backdrop-blur">
            <Button size="sm" variant={mobileTab === "chat" ? "secondary" : "ghost"} onClick={() => setMobileTab("chat")}>
              {language === "zh" ? "对话" : "Chat"}
            </Button>
            <Button size="sm" variant={mobileTab === "artifacts" ? "secondary" : "ghost"} onClick={() => setMobileTab("artifacts")}>
              {language === "zh" ? "创作" : "Create"}
            </Button>
          </div>
          {mobileSidebarOpen && (
            <div className="fixed inset-0 z-50 flex">
              <div className="w-80 max-w-[86vw] bg-background">
                <ChatSidebar
                  collapsed={false}
                  selectedChat={state.selectedRunId || workspace.pendingThreadId}
                  chats={chats}
                  userInfo={{ email: user?.email || "" }}
                  userCredits={{ balance: user?.credits || 0 }}
                  isLoadingChats={state.isLoadingRuns}
                  hasMoreChats={false}
                  showConversationActions={false}
                  showDeleteAction
                  onToggleCollapse={() => setMobileSidebarOpen(false)}
                  onNewTask={() => { startNewSession(); setMobileSidebarOpen(false); }}
                  onSelectChat={(id) => { selectSession(id); setMobileSidebarOpen(false); }}
                  onDeleteChat={(id, event) => void deleteSession(id, event)}
                  onTogglePin={(_, event) => event.stopPropagation()}
                  onLogout={() => void logout().then(() => navigate("/auth"))}
                  isMobileFullScreen
                />
              </div>
              <button className="flex-1 bg-black/50" aria-label="Close sidebar" onClick={() => setMobileSidebarOpen(false)} />
            </div>
          )}
        </div>
      ) : (
        <ResizablePanelGroup direction="horizontal" className="min-w-0 flex-1">
          <ResizablePanel defaultSize={38} minSize={25} maxSize={52} className="min-w-0">
            {messageArea}
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={62} minSize={48} className="min-w-0">
            {artifactsArea}
          </ResizablePanel>
        </ResizablePanelGroup>
      )}
      <DeepAgentTracePanel
        events={state.traceEvents}
        tokenUsage={tokenUsage}
        open={traceOpen}
        language={language}
        onClose={() => setTraceOpen(false)}
      />
    </div>
  );
}
