import { forwardRef, useState, useEffect, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Video, Loader2, MessageSquare, Zap, User, MapPin, Film, Mic, Music } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StorySection } from "./StorySection";
import { StyleSection } from "./StyleSection";
import { LazyCharactersSection } from "./LazyCharactersSection";
import { CharactersVersionSection } from "./CharactersVersionSection";
import { ScenesSection } from "./ScenesSection";
import { LazyStoryboardShotsSection } from "./LazyStoryboardShotsSection";
import { NarrationSection } from "./NarrationSection";
import { MusicSection } from "./MusicSection";
import { VideoAssemblySection } from "./VideoAssemblySection";
import MusicPlayerSection from "./MusicPlayerSection";
import { useLanguage } from "@/i18n/LanguageContext";
import { videoAnalysisApi, api } from "@/services/api";
import { processCharactersData } from "@/utils/dataCompatibility";
import { toast } from "sonner";
import { IMMERSIVE_MODE_ENABLED } from "@/constants/featureFlags";
import {
  getExpectedEventOrderFromMessages,
  hasRenderableStoryOutlineData,
  conversationHasVideoWorkflowStatePath,
  isMusicDeferredInWorkflowPath,
  hasWorkflowPathInMessages,
} from "./videoResultsEventOrder";
import { getMasterDurationSecFromStoryOutline } from "@/lib/videoTimelinePlacements";

interface LazyVideoResultsPanelProps {
  messages?: any[]; // 消息列表，用于确定渲染顺序
  storyOutlineData: any;
  analysisData: any;
  charactersData: any;
  scenesData: any;
  keyframesData: any;
  videosData: any;
  musicData: any;
  videoAssemblyData: any;
  lipsyncData?: any;
  userOption?: any;
  onRegenerateKeyframe?: (shotNumber: number, customPrompt?: string, frameIndex?: number) => Promise<boolean>;
  onKeyframeVersionSelection?: (keyframeUuid: string, versionUuid: string) => Promise<boolean | void>;
  /** 关键帧成图编辑（instruction + regenerate_strategy=instruction_edit_image） */
  onKeyframeImageEditWithInstruction?: (
    shotNumber: number,
    instruction: string,
    versionIndex: number,
    frameIndex?: number,
  ) => Promise<boolean>;
  /** instruction_merge_prompt：返回融合后的完整 t2i_prompt（供融合编辑弹窗） */
  onKeyframeRefinePromptOnly?: (
    keyframeUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  /** 与面板内点击 regenerate 的 dice key 一致；故事板 companion（regenerate_characters） */
  companionDiceKeys?: ReadonlySet<string>;
  /** 镜头区视频 dice；regenerate_keyframes 同步下游（实际 regenerate_videos）时转圈 */
  companionVideoDiceKeys?: ReadonlySet<string>;
  /** regenerate_videos 同步下游（时间线/成片合并）时，镜头区底部「合并视频」按钮同款 loading */
  companionMergeAssemblyBusy?: boolean;
  /** AI Improve 成功后只刷新关键帧/视频数据，不再次调用 regenerate API */
  onRefreshKeyframes?: () => Promise<void>;
  onRegenerateVideo?: (shotNumber: number, customPrompt?: string) => Promise<boolean>;
  onVideoVersionSelection?: (videoUuid: string, versionUuid: string) => Promise<boolean | void>;
  /** 与关键帧融合编辑一致：instruction_merge_prompt 返回 motion_prompt */
  onVideoRefinePromptOnly?: (
    videoUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  onRegenerateCharacter?: (characterUuid: string, customPrompt?: string) => Promise<boolean>;
  onCharacterVersionSelection?: (characterUuid: string, versionUuid: string) => Promise<boolean | void>;
  onCharacterImageEditWithInstruction?: (
    characterUuid: string,
    instruction: string,
    versionIndex: number,
  ) => Promise<boolean>;
  /** 角色：instruction_merge_prompt，返回融合后的完整 t2i_prompt（与关键帧融合编辑弹窗一致） */
  onCharacterRefinePromptOnly?: (
    characterUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  onRegenerateMultiView?: (characterUuid: string, versionUuid: string, customPrompt?: string) => Promise<boolean>;
  onLipsyncGenerate?: (segmentUuid: string) => Promise<boolean>;
  onLipsyncRegenerate?: (lipsyncUuid: string) => Promise<boolean>;
  onLipsyncSuccess?: () => void | Promise<void>;
  /** 视频合成（video-assembly）完成后回调，传入 assembly_uuid 用于刷新 getVideoAssemblyData */
  onVideoAssembled?: (assemblyUuid: string) => void | Promise<void>;
  /** 场景双击编辑保存后回调，用于合并更新后的场景到 state */
  onScenePatch?: (sceneId: string, patch: Record<string, string>) => Promise<boolean | void>;
  onSceneUpdated?: (updatedScene: any) => void;
  /** 场景保存成功后刷新整块场景数据（按 thread 重新拉取） */
  onAfterSceneUpdate?: () => void | Promise<void>;
  /** 章节双击编辑保存后回调，用于合并更新后的章节到 storyOutlineData.structure */
  onChapterPatch?: (chapterId: string, patch: Record<string, string>) => Promise<boolean | void>;
  onChapterUpdated?: (updatedChapter: any) => void;
  onMentionClick?: (type: 'image' | 'prompt', keyframe: any) => void;
  onMusicPromptUpdate?: (musicUuid: string, versionUuid: string, newPrompt: string) => Promise<void>;
  onCharactersDataUpdate?: (updatedData: any) => void;
  conversationId?: string;
  threadId?: string;
  videoProgress?: {
    completed: number;
    total: number;
    progress_percent: number;
  };
  zoomLevel?: number; // ✅ 统一的缩放级别
  blockedEventType?: string | null;
  videoCreationTab?: "final" | "creative";
  onVideoCreationTabChange?: (tab: "final" | "creative") => void;
  hideVideoCreationTabs?: boolean;
}

export const LazyVideoResultsPanel = forwardRef<HTMLDivElement, LazyVideoResultsPanelProps>(({
  messages = [],
  storyOutlineData,
  analysisData,
  charactersData,
  scenesData,
  keyframesData,
  videosData,
  musicData,
  videoAssemblyData,
  lipsyncData,
  userOption,
  onRegenerateKeyframe,
  onKeyframeVersionSelection,
  onKeyframeImageEditWithInstruction,
  onKeyframeRefinePromptOnly,
  companionDiceKeys,
  companionVideoDiceKeys,
  companionMergeAssemblyBusy,
  onRefreshKeyframes,
  onRegenerateVideo,
  onVideoVersionSelection,
  onVideoRefinePromptOnly,
  onRegenerateCharacter,
  onCharacterVersionSelection,
  onCharacterImageEditWithInstruction,
  onCharacterRefinePromptOnly,
  onRegenerateMultiView,
  onLipsyncGenerate,
  onLipsyncRegenerate,
  onLipsyncSuccess,
  onVideoAssembled,
  onScenePatch,
  onSceneUpdated,
  onAfterSceneUpdate,
  onChapterPatch,
  onChapterUpdated,
  onMentionClick,
  onMusicPromptUpdate,
  onCharactersDataUpdate,
  conversationId,
  threadId,
  videoProgress,
  zoomLevel = 1, // ✅ 默认缩放级别为1
  blockedEventType,
  videoCreationTab,
  onVideoCreationTabChange,
  hideVideoCreationTabs = false,
}, ref) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useLanguage();
  const [selectedVersions, setSelectedVersions] = useState<Map<string, string>>(new Map());
  const [selectedKeyframeVersions, setSelectedKeyframeVersions] = useState<Map<string, string>>(new Map());
  const [segmentsRefreshTrigger, setSegmentsRefreshTrigger] = useState(0);
  const [internalVideoCreationTab, setInternalVideoCreationTab] = useState<"final" | "creative">("final");

  const getLangPrefix = () => {
    const match = location.pathname.match(/^\/(en|zh)(?:\/|$)/);
    return match ? `/${match[1]}` : "";
  };

  const openVideoCheck = (section: 'overview' | 'storyboard' | 'shots' | 'final') => {
    if (!IMMERSIVE_MODE_ENABLED) return;
    const payload = {
      storyOutlineData,
      analysisData,
      charactersData,
      scenesData,
      keyframesData,
      videosData,
      musicData,
      videoAssemblyData,
      lipsyncData,
      userOption,
      conversationId,
      threadId,
      videoProgress,
    };

    sessionStorage.setItem('cuti_video_check_payload', JSON.stringify(payload));
    navigate(`${getLangPrefix()}/video-check?section=${section}&agenttype=video`);
  };

  // 初始化默认选中当前版本（视频）
  useEffect(() => {
    if (videosData?.video_generations) {
      const initialSelected = new Map<string, string>();
      videosData.video_generations.forEach((video: any) => {
        if (video.versions && video.versions.length > 0) {
          const currentIndex = video.current_version_index || 0;
          const currentVersion = video.versions[currentIndex];
          if (currentVersion) {
            initialSelected.set(video.uuid, currentVersion.uuid);
          }
        }
      });
      setSelectedVersions(initialSelected);
    }
  }, [videosData]);

  // 初始化默认选中当前版本（关键帧）
  useEffect(() => {
    if (keyframesData?.keyframes) {
      const initialSelected = new Map<string, string>();
      keyframesData.keyframes.forEach((keyframe: any) => {
        if (keyframe.versions && keyframe.versions.length > 0 && keyframe.uuid) {
          const currentIndex = keyframe.current_version_index ?? 0;
          const currentVersion = keyframe.versions[currentIndex];
          if (currentVersion) {
            initialSelected.set(keyframe.uuid, currentVersion.uuid);
          }
        }
      });
      setSelectedKeyframeVersions(initialSelected);
    }
  }, [keyframesData]);

  // 处理视频版本选择变化（调用后端接口持久化）
  const handleVersionSelection = async (videoUuid: string, versionUuid: string) => {
    const prevSelected = selectedVersions.get(videoUuid);
    if (prevSelected === versionUuid) return;
    setSelectedVersions(prev => { const m = new Map(prev); m.set(videoUuid, versionUuid); return m; });
    try {
      if (onVideoVersionSelection) {
        await onVideoVersionSelection(videoUuid, versionUuid);
      } else {
        const res = await api.videoEditing.selectVideoVersion(videoUuid, versionUuid);
        if (res.code !== 0) {
          setSelectedVersions(prev => { const m = new Map(prev); m.delete(videoUuid); if (prevSelected) m.set(videoUuid, prevSelected); return m; });
          toast.error(res.message || t('updateFailed') || '更新失败');
        }
      }
    } catch (e: any) {
      setSelectedVersions(prev => { const m = new Map(prev); m.delete(videoUuid); if (prevSelected) m.set(videoUuid, prevSelected); return m; });
      toast.error(e?.message || t('updateFailed') || '更新失败');
    }
  };

  // 处理关键帧版本选择变化（调用后端接口持久化）
  const handleKeyframeVersionSelection = async (keyframeUuid: string, versionUuid: string) => {
    const prevSelected = selectedKeyframeVersions.get(keyframeUuid);
    if (prevSelected === versionUuid) return;
    setSelectedKeyframeVersions(prev => { const m = new Map(prev); m.set(keyframeUuid, versionUuid); return m; });
    try {
      if (onKeyframeVersionSelection) {
        await onKeyframeVersionSelection(keyframeUuid, versionUuid);
      } else {
        const res = await api.videoEditing.selectKeyframeVersion(keyframeUuid, versionUuid);
        if (res.code !== 0) {
          setSelectedKeyframeVersions(prev => { const m = new Map(prev); m.delete(keyframeUuid); if (prevSelected) m.set(keyframeUuid, prevSelected); return m; });
          toast.error(res.message || t('updateFailed') || '更新失败');
        }
      }
    } catch (e: any) {
      setSelectedKeyframeVersions(prev => { const m = new Map(prev); m.delete(keyframeUuid); if (prevSelected) m.set(keyframeUuid, prevSelected); return m; });
      toast.error(e?.message || t('updateFailed') || '更新失败');
    }
  };

  // 根据消息列表提取事件类型的顺序（每个类型只保留最后一个）
  const aiMessages = messages.filter((msg) => msg.event_type && msg.role === "ai");

  const isMusicOnlyConversation = () => {
    if (conversationHasVideoWorkflowStatePath(messages)) {
      return false;
    }
    if (
      (keyframesData?.keyframes?.length || 0) > 0 ||
      (videosData?.video_generations?.length || 0) > 0
    ) {
      return false;
    }
    const eventTypes = aiMessages.map(msg => msg.event_type);
    const videoRelatedEvents = [
      'video_analysis',
      'story_outline_generated',
      'characters_designed',
      'scenes_generated',
      'keyframes_generated',
      'keyframes_reflection_completed',
      'video_segments_generated',
      'video_segments_assembled',
      'video_completed'
    ];

    const hasVideoEvents = eventTypes.some(type => videoRelatedEvents.includes(type));
    const hasMusicEvents =
      eventTypes.includes('music_agent_generated') || eventTypes.includes('music_generated');

    return hasMusicEvents && !hasVideoEvents;
  };

  // ✅ 与 VideoResultsPanel / workflow_state.path 一致（Lazy 默认也走 create，音乐在故事+风格之前）
  const expectedEventOrder = getExpectedEventOrderFromMessages(messages);

  // 获取已发生的事件类型
  const occurredEvents = new Set(
    aiMessages
      .reverse()
      .reduce((acc, msg) => {
        if (!acc.seen.has(msg.event_type)) {
          acc.seen.add(msg.event_type);
          acc.order.push(msg.event_type);
        }
        return acc;
      }, { seen: new Set<string>(), order: [] as string[] })
      .order
  );

  // 接口数据一旦到达，也视为对应步骤已 materialize，不必等待 stream 事件
  const materializedEvents = new Set(occurredEvents);
  if (hasRenderableStoryOutlineData(storyOutlineData)) materializedEvents.add('story_outline_generated');
  if (charactersData?.characters?.length > 0) materializedEvents.add('characters_designed');
  if (scenesData?.scenes?.length > 0) materializedEvents.add('scenes_generated');
  if (
    scenesData?.scenes?.some((s: any) => Array.isArray(s.narrations) && s.narrations.some((n: any) => n?.audio_url || n?.narration_text))
  ) {
    materializedEvents.add('narrations_generated');
  }
  if (
    (keyframesData?.keyframes?.length || 0) > 0
    || (Number.isFinite(keyframesData?.total) && keyframesData.total > 0)
    || (Number.isFinite((keyframesData as any)?.shot_total) && (keyframesData as any).shot_total > 0)
  ) {
    materializedEvents.add('keyframes_generated');
  }
  if ((videosData?.video_generations?.length || 0) > 0) {
    materializedEvents.add('video_segments_generated');
  }
  if ((musicData?.music_generations?.length || 0) > 0 || musicData?._noMusic) materializedEvents.add('music_generated');
  if (videoAssemblyData) materializedEvents.add('video_completed');

  // ✅ 检查任务是否已开始（有 generation_todo 且不是 rebuildGenerationTodo 插入的"等待 user_input"占位）
  // todo_pending_user_input=true 表示后端已运行但 user_input 还未到达，此时不算真正开始生成
  const latestUserInput = [...messages].reverse().find((msg) => msg.event_type === "user_input");
  const latestUserInputHasConfirmed = latestUserInput?.event_data?.has_confirmed === true;
  const hasTaskStarted = messages.some(
    (msg) =>
      latestUserInputHasConfirmed &&
      msg.event_type === "generation_todo" &&
      !msg.event_data?.todo_pending_user_input &&
      ["running", "queued", "resume_queued", "pending", "interrupted"].includes(String(msg.event_data?.status || "").toLowerCase())
  );
  
  // ✅ 检查任务是否已完成
  const isTaskCompleted = materializedEvents.has('video_completed');
  const isTaskCancelled = messages.some((msg) => {
    let ed: any = msg?.event_data;
    if (typeof ed === "string") {
      try {
        ed = JSON.parse(ed);
      } catch {
        ed = {};
      }
    }
    const st = ed && typeof ed === "object" ? (ed as { status?: string }).status : undefined;
    return (
      msg?.event_type === "generation_cancelled" ||
      msg?.event_type === "cancelled" ||
      st === "cancelled"
    );
  });
  const shouldShowTaskTerminated = hasTaskStarted && isTaskCancelled && !isTaskCompleted && !videoAssemblyData;

  // ✅ 创建占位组件
  // ✅ 从 messages 中提取最新的 generation_todo 进度信息
  const getProgressFromMessages = (progressKey: string): number | null => {
    const latestTodo = [...messages].reverse().find((msg) => msg.event_type === 'generation_todo');
    if (!latestTodo || !latestTodo.event_data) return null;
    const value = latestTodo.event_data[progressKey];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };

  const masterTargetDurationSec = useMemo(() => {
    const fromOutline = getMasterDurationSecFromStoryOutline(storyOutlineData);
    if (fromOutline != null && fromOutline > 0.05) {
      return fromOutline;
    }
    const d = userOption?.duration;
    return typeof d === "number" && Number.isFinite(d) && d > 0.05 ? d : undefined;
  }, [storyOutlineData, userOption?.duration]);

  const createPlaceholder = (title: string, icon: React.ReactNode, progressKey?: string, completedKey?: string, totalKey?: string) => {
    const progress = progressKey ? getProgressFromMessages(progressKey) : null;
    const completed = completedKey ? getProgressFromMessages(completedKey) : null;
    const total = totalKey ? getProgressFromMessages(totalKey) : null;
    
    return (
      <Card className="glass p-6">
        <div className="flex items-center gap-3 mb-4">
          {icon}
          <h3 className="text-lg font-semibold font-inter">{title}</h3>
          {total != null && <span className="text-sm text-muted-foreground ml-1">({completed ?? 0} / {total})</span>}
          <Loader2 className="w-4 h-4 animate-spin text-muted-foreground ml-auto" />
        </div>
        {progress !== null || (total != null && total > 0) ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm text-muted-foreground">
              <span>{t('generating' as any)}</span>
              <span className="font-mono">{progress != null ? `${Math.round(progress)}%` : '0%'}</span>
            </div>
            <div className="h-2 bg-gray-200/20 rounded-full overflow-hidden">
              <div 
                className="h-full bg-accent-purple/60 transition-all duration-300 ease-out"
                style={{ width: `${Math.min(100, Math.max(0, progress ?? 0))}%` }}
              />
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
      </Card>
    );
  };

  // ✅ 确定当前正在生成的步骤
  // 找到第一个未完成的事件类型
  const musicDeferred = isMusicDeferredInWorkflowPath(messages);
  const hasWorkflowPath = hasWorkflowPathInMessages(messages);

  const getCurrentGeneratingStep = () => {
    for (const eventType of expectedEventOrder) {
      if (
        eventType === "music_generated" &&
        musicDeferred &&
        !materializedEvents.has("music_generated")
      ) {
        const musicPrereqs = ["scenes_generated", "story_outline_generated", "characters_designed"];
        const prereqsReady = musicPrereqs.some((et) => materializedEvents.has(et));
        const musicProgress = getProgressFromMessages("music_progress_percent");
        if (!prereqsReady && (musicProgress == null || musicProgress <= 0)) {
          continue;
        }
      }
      if (!materializedEvents.has(eventType)) {
        return eventType;
      }
    }
    return null; // 所有步骤都已完成
  };

  const currentGeneratingStep = getCurrentGeneratingStep();

  // ✅ 从 generation_todo 中检测已有 progress 数据的阶段（有数据就展示，不等 event）
  const progressPhases = new Set<string>();
  const latestTodoForPhase = [...messages].reverse().find((msg) => msg.event_type === 'generation_todo');
  if (latestTodoForPhase?.event_data) {
    const td = latestTodoForPhase.event_data;
    if (Number(td.keyframe_completed) > 0) {
      progressPhases.add('keyframes_generated');
    }
    if (Number(td.keyframe_reflection_completed) > 0) {
      progressPhases.add('keyframes_generated');
      progressPhases.add('keyframes_reflection_completed');
    }
    const hasVideoProgressMsg = messages.some(
      (m: any) => m?.event_type === "video_generation_progress"
    );
    const hasVideoTodoWithDenom =
      Number(td.total) > 0 &&
      td.completed != null &&
      Number.isFinite(Number(td.completed));
    if (Number(td.completed) > 0 || hasVideoProgressMsg || hasVideoTodoWithDenom) {
      progressPhases.add('keyframes_generated');
      progressPhases.add('video_segments_generated');
    }
  }

  // ✅ 确定要渲染的事件顺序
  // 如果任务已开始但未完成，只显示已完成的步骤 + 当前正在生成的步骤（占位）+ progress 数据已有的步骤
  // 如果任务已完成，按照 expectedEventOrder 的顺序显示已发生的事件
  // ✅ keyframes / video_segments 只在收到对应 progress event 后才展示（progressPhases）
  // 其他步骤（story, characters, scenes）可以用 currentGeneratingStep 提前占位
  const progressGatedEvents = new Set(['keyframes_generated', 'keyframes_reflection_completed', 'video_segments_generated']);
  const eventOrderToRender = hasTaskStarted && !isTaskCompleted
    ? expectedEventOrder.filter(eventType => {
        // 已发生的事件，总是显示
        if (materializedEvents.has(eventType)) {
          return true;
        }
        // progress 数据已有的阶段（从 generation_todo 的 keyframe_total / total 等字段检测）
        if (progressPhases.has(eventType)) {
          return true;
        }
        // keyframes / video 必须等 progress event，不提前展示
        if (progressGatedEvents.has(eventType)) {
          return false;
        }
        // 其他步骤：只显示当前正在生成的步骤（作为占位）
        if (eventType === currentGeneratingStep) {
          return true;
        }
        return false;
      })
    : expectedEventOrder.filter(eventType => materializedEvents.has(eventType))
    .filter((eventType) => eventType !== blockedEventType);
  const hasKeyframeRowsForFinal =
    Array.isArray(keyframesData?.keyframes) && keyframesData.keyframes.length > 0;
  const hasVideoGenRowsForFinal = (videosData?.video_generations?.length || 0) > 0;
  const hasKeyframeGenProgressMessage = messages.some(
    (m: any) => m?.event_type === "keyframe_generation_progress"
  );
  const hasVideoGenProgressMessage = messages.some(
    (m: any) => m?.event_type === "video_generation_progress"
  );
  const latestTodoEd = [...messages].reverse().find((m: any) => m?.event_type === "generation_todo")?.event_data;
  const hasKeyframeTodoProgressDenom =
    latestTodoEd != null &&
    Number(latestTodoEd.keyframe_total) > 0 &&
    latestTodoEd.keyframe_completed != null &&
    Number.isFinite(Number(latestTodoEd.keyframe_completed));
  /** 待定成片：已组装；或已有镜头行；或关键帧根记录已落库；或关键帧/镜头生成进度已写入（含 0/N，与分镜区 0/22 一致） */
  const showFinalTimelineByArtifacts =
    !!videoAssemblyData ||
    hasVideoGenRowsForFinal ||
    hasKeyframeRowsForFinal ||
    hasKeyframeGenProgressMessage ||
    hasVideoGenProgressMessage ||
    hasKeyframeTodoProgressDenom;
  const orderSetForPanel = new Set<string>(eventOrderToRender);
  if (
    showFinalTimelineByArtifacts &&
    expectedEventOrder.includes("video_completed") &&
    !orderSetForPanel.has("video_completed")
  ) {
    orderSetForPanel.add("video_completed");
  }
  const eventOrderForRender = expectedEventOrder
    .filter((et) => orderSetForPanel.has(et) && et !== blockedEventType);
  // 调试日志
  if (eventOrderForRender.length > 0) {
    console.log('📊 LazyVideoResultsPanel - Event order to render:', eventOrderForRender);
    console.log('📊 LazyVideoResultsPanel - Has task started:', hasTaskStarted, 'Is completed:', isTaskCompleted);
  }

  // ✅ 定义事件类型到组件的映射（包含占位逻辑）
  const componentMap: Record<string, () => JSX.Element | null> = {
    story_outline_generated: () => {
      const hasOutline = hasRenderableStoryOutlineData(storyOutlineData);
      const hasStyle = !!(analysisData?.style_preferences?.length);
      const hasData = hasOutline || hasStyle;
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return (
          <>
            {createPlaceholder(t('storySection') || 'Story', <MessageSquare className="w-5 h-5 text-accent-cyan" />)}
            {createPlaceholder(t('styleSection') || 'Style', <Zap className="w-5 h-5 text-accent-purple" />)}
          </>
        );
      }
      if (hasData) {
        return (
          <>
            <StorySection storyOutlineData={storyOutlineData} onChapterPatch={onChapterPatch} onChapterUpdated={onChapterUpdated} />
            <StyleSection analysisData={analysisData} storyOutlineData={storyOutlineData} />
          </>
        );
      }
      return null;
    },
    characters_designed: () => {
      const hasData = charactersData?.characters?.length > 0;
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return createPlaceholder(t('characterSection') || 'Visual Elements', <User className="w-5 h-5 text-accent-pink" />, 'image_progress_percent');
      }
      if (hasData) {
        return (
          <CharactersVersionSection 
            charactersData={charactersData} 
            onVersionSelection={onCharacterVersionSelection}
            onRegenerate={onRegenerateCharacter}
            onCharacterImageEditWithInstruction={onCharacterImageEditWithInstruction}
            onCharacterRefinePromptOnly={onCharacterRefinePromptOnly}
            onRegenerateMultiView={onRegenerateMultiView}
            onFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('overview') : undefined}
            zoomLevel={zoomLevel}
            threadId={threadId}
            onDataUpdate={async () => {
              if (threadId) {
                try {
                  const updatedCharacters = await videoAnalysisApi.getCharactersDataByThreadId(threadId, true);
                  if (updatedCharacters.code === 0) {
                    const processedData = processCharactersData(updatedCharacters.data);
                    if (onCharactersDataUpdate) {
                      onCharactersDataUpdate(processedData);
                    }
                  }
                } catch (error) {
                  console.error('❌ Failed to refresh character data:', error);
                }
              }
            }}
          />
        );
      }
      return null;
    },
    scenes_generated: () => {
      const hasData = scenesData?.scenes?.length > 0;
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return createPlaceholder(t('sceneSection') || 'Scenes', <MapPin className="w-5 h-5 text-accent-white" />);
      }
      if (hasData) {
        return <ScenesSection scenesData={scenesData} contentCategory={userOption?.content_category} onFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('overview') : undefined} onScenePatch={onScenePatch} onSceneUpdated={onSceneUpdated} onAfterSceneUpdate={onAfterSceneUpdate} />;
      }
      return null;
    },
    keyframes_generated: () => {
      const totalFromScenes = scenesData?.scenes?.length;
      const scenesCountForShots = scenesData?.scenes?.length;
      const totalForShots = Number.isFinite(scenesCountForShots) && scenesCountForShots > 0
        ? scenesCountForShots
        : (videoProgress?.total != null && videoProgress.total > 0 ? videoProgress.total : undefined);
      const hasStoryboardsData = (keyframesData?.keyframes?.length || 0) > 0
        || (Number.isFinite(keyframesData?.total) && keyframesData.total > 0)
        || (Number.isFinite((keyframesData as any)?.shot_total) && (keyframesData as any).shot_total > 0)
        || (Number.isFinite(totalFromScenes) && totalFromScenes > 0);
      const hasShotsData = (videosData?.video_generations?.length || 0) > 0
        || (Number.isFinite(videosData?.total) && videosData.total > 0)
        || (Number.isFinite(totalForShots) && (totalForShots as number) > 0);
      const hasData = hasStoryboardsData || hasShotsData;
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return createPlaceholder(t('storyboardsSection') || 'Storyboards', <Film className="w-5 h-5 text-accent-blue" />, 'keyframe_progress_percent', 'keyframe_completed', 'keyframe_total');
      }
      if (hasData) {
        return (
          <LazyStoryboardShotsSection
            keyframesData={keyframesData}
            videosData={videosData}
            videoProgress={videoProgress}
            keyframesTotalCount={totalFromScenes}
            shotsTotalCount={totalForShots}
            selectedKeyframeVersions={selectedKeyframeVersions}
            onKeyframeVersionSelection={handleKeyframeVersionSelection}
            selectedVersions={selectedVersions}
            onVersionSelection={handleVersionSelection}
            onRegenerateKeyframe={onRegenerateKeyframe}
            onKeyframeImageEditWithInstruction={onKeyframeImageEditWithInstruction}
            onRefreshKeyframes={onRefreshKeyframes}
            onMentionClick={onMentionClick}
            zoomLevel={zoomLevel}
            userOption={userOption}
            threadId={threadId}
            onKeyframeRefinePromptOnly={onKeyframeRefinePromptOnly}
            companionDiceKeys={companionDiceKeys}
            onRegenerateVideo={onRegenerateVideo}
            onVideoRefinePromptOnly={onVideoRefinePromptOnly}
            conversationId={conversationId}
            onSegmentsSynced={() => setSegmentsRefreshTrigger(prev => prev + 1)}
            onVideoAssembled={onVideoAssembled}
            companionVideoDiceKeys={companionVideoDiceKeys}
            companionMergeAssemblyBusy={companionMergeAssemblyBusy}
            onStoryboardsFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('storyboard') : undefined}
            onShotsFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('shots') : undefined}
          />
        );
      }
      return null;
    },
    video_segments_generated: () => {
      // Shots 已合并到 keyframes_generated 对应的 Storyboards/Shots Tab 模块中。
      return null;
    },
    // 不展示 lyrics section，此步骤不再渲染
    video_segments_assembled: () => null,
    music_generated: () => {
      const musicGenerations = musicData?.music_generations || [];
      const hasData = musicGenerations.length > 0;
      const musicProgress = getProgressFromMessages("music_progress_percent");
      const mayShowMusicPlaceholder =
        hasTaskStarted &&
        !isTaskCompleted &&
        (!musicDeferred || materializedEvents.has("scenes_generated") || (musicProgress != null && musicProgress > 0));
      if (!hasData && mayShowMusicPlaceholder && (hasWorkflowPath || !musicDeferred)) {
        return createPlaceholder(t('backgroundMusicSection') || 'Music', <Music className="w-5 h-5 text-accent-purple" />, 'music_progress_percent');
      }
      if (hasData) {
        return <MusicSection musicData={musicData} onMusicPromptUpdate={onMusicPromptUpdate} />;
      }
      return null;
    },
    video_lipsync_completed: () => null,
    video_completed: () => {
      const hasData = !!videoAssemblyData;
      if (hasData) {
        return (
          <VideoAssemblySection
            key={videoAssemblyData?.uuid || 'no-data'}
            videoAssemblyData={videoAssemblyData}
            videosData={videosData}
            keyframesData={keyframesData}
            scenesData={scenesData}
            selectedKeyframeVersions={selectedKeyframeVersions}
            selectedVersionsForShots={selectedVersions}
            masterTargetDurationSec={masterTargetDurationSec}
            previewAspectRatio={userOption?.aspect_ratio}
            musicData={musicData}
            threadId={threadId}
            contentCategory={userOption?.content_category}
          />
        );
      }
      if (showFinalTimelineByArtifacts) {
        return (
          <VideoAssemblySection
            key="final-pending"
            videoAssemblyData={null}
            videosData={videosData}
            keyframesData={keyframesData}
            scenesData={scenesData}
            selectedKeyframeVersions={selectedKeyframeVersions}
            selectedVersionsForShots={selectedVersions}
            masterTargetDurationSec={masterTargetDurationSec}
            previewAspectRatio={userOption?.aspect_ratio}
            musicData={musicData}
            threadId={threadId}
            contentCategory={userOption?.content_category}
          />
        );
      }
      return null;
    },
  };

  const renderEventList = (eventTypes: string[]) =>
    eventTypes.map((eventType, index) => {
      const Component = componentMap[eventType];
      if (!Component) {
        return null;
      }
      const rendered = Component();
      if (!rendered) {
        return null;
      }
      return (
        <div key={`${eventType}-${index}`}>
          {rendered}
        </div>
      );
    });

  const finalEventOrder = eventOrderForRender.filter((eventType) => eventType === "video_completed");
  const creativeEventOrder = eventOrderForRender.filter((eventType) => eventType !== "video_completed");
  const activeVideoCreationTab = videoCreationTab ?? internalVideoCreationTab;
  const handleVideoCreationTabChange = (tab: "final" | "creative") => {
    if (videoCreationTab === undefined) {
      setInternalVideoCreationTab(tab);
    }
    onVideoCreationTabChange?.(tab);
  };

  return (
    <div ref={ref} className="flex-1 min-h-0 overflow-y-auto">
      <div className="px-4 py-6 space-y-8">
        {isMusicOnlyConversation() ? (
          <MusicPlayerSection messages={messages} />
        ) : (
          hideVideoCreationTabs ? (
            activeVideoCreationTab === "final" ? (
              <div className="space-y-8">
                {renderEventList(finalEventOrder)}
                {finalEventOrder.length === 0 && hasTaskStarted && !isTaskCompleted && (
                  <Card className="glass p-6">
                    <p className="text-sm text-muted-foreground">
                      {t("videoBeingAssembled")}
                    </p>
                  </Card>
                )}
              </div>
            ) : (
              <div className="space-y-8">
                {renderEventList(creativeEventOrder)}
              </div>
            )
          ) : (
            <Tabs value={activeVideoCreationTab} onValueChange={(value) => handleVideoCreationTabChange(value as "final" | "creative")}>
              <TabsList className="grid w-full max-w-md grid-cols-2">
                <TabsTrigger value="final">{t("finalVideo") || "Final Video"}</TabsTrigger>
                <TabsTrigger value="creative">{t("creativeProcess") || "Creative Process"}</TabsTrigger>
              </TabsList>
              <TabsContent value="final" className="space-y-8">
                {renderEventList(finalEventOrder)}
                {finalEventOrder.length === 0 && hasTaskStarted && !isTaskCompleted && (
                  <Card className="glass p-6">
                    <p className="text-sm text-muted-foreground">
                      {t("videoBeingAssembled")}
                    </p>
                  </Card>
                )}
              </TabsContent>
              <TabsContent value="creative" className="space-y-8">
                {renderEventList(creativeEventOrder)}
              </TabsContent>
            </Tabs>
          )
        )}
        {shouldShowTaskTerminated && (
          <Card className="glass p-6">
            <p className="text-sm text-muted-foreground font-inter">
              {t("taskTerminated")}
            </p>
          </Card>
        )}

      </div>
    </div>
  );
});

LazyVideoResultsPanel.displayName = "LazyVideoResultsPanel";



