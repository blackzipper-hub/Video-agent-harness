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
import { CharactersSection } from "./CharactersSection";
import { ScenesSection } from "./ScenesSection";
import { StoryboardsSection } from "./StoryboardsSection";
import { ShotsSection } from "./ShotsSection";
import { NarrationSection } from "./NarrationSection";
import { MusicSection } from "./MusicSection";
import { VideoAssemblySection } from "./VideoAssemblySection";
import MusicPlayerSection from "./MusicPlayerSection";
import { useLanguage } from "@/i18n/LanguageContext";
import { IMMERSIVE_MODE_ENABLED } from "@/constants/featureFlags";
import { api } from "@/services/api";
import { toast } from "sonner";
import {
  FALLBACK_VIDEO_RESULTS_EVENT_ORDER,
  getExpectedEventOrderFromMessages,
  hasRenderableStoryOutlineData,
  conversationHasVideoWorkflowStatePath,
  isMusicDeferredInWorkflowPath,
  hasWorkflowPathInMessages,
} from "./videoResultsEventOrder";
import { getMasterDurationSecFromStoryOutline } from "@/lib/videoTimelinePlacements";

interface VideoResultsPanelProps {
  messages?: any[]; // 消息列表，用于确定渲染顺序
  storyOutlineData: any;
  analysisData: any;
  charactersData: any;
  scenesData: any;
  keyframesData: any;
  videosData: any;
  narrationData: any;
  musicData: any;
  videoAssemblyData: any;
  lipsyncData?: any;
  userOption?: any;
  onRegenerateKeyframe?: (shotNumber: number, customPrompt?: string) => Promise<boolean>;
  onRegenerateVideo?: (shotNumber: number, customPrompt?: string) => Promise<boolean>;
  onRegenerateCharacter?: (characterUuid: string, customPrompt?: string) => Promise<boolean>;
  onLipsyncGenerate?: (segmentUuid: string) => Promise<boolean>;
  onLipsyncRegenerate?: (lipsyncUuid: string) => Promise<boolean>;
  onLipsyncSuccess?: () => void | Promise<void>;
  /** 视频合成（video-assembly）完成后回调，传入 assembly_uuid 用于刷新 getVideoAssemblyData */
  onVideoAssembled?: (assemblyUuid: string) => void | Promise<void>;
  /** 场景双击编辑保存后回调，用于合并更新后的场景到 state */
  onSceneUpdated?: (updatedScene: any) => void;
  /** 场景保存成功后刷新整块场景数据（按 thread 重新拉取） */
  onAfterSceneUpdate?: () => void | Promise<void>;
  /** 章节双击编辑保存后回调，用于合并更新后的章节到 storyOutlineData.structure */
  onChapterUpdated?: (updatedChapter: any) => void;
  onMentionClick?: (type: 'image' | 'prompt', keyframe: any) => void;
  onMusicPromptUpdate?: (musicUuid: string, versionUuid: string, newPrompt: string) => Promise<void>;
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

export const VideoResultsPanel = forwardRef<HTMLDivElement, VideoResultsPanelProps>(({
  messages = [],
  storyOutlineData,
  analysisData,
  charactersData,
  scenesData,
  keyframesData,
  videosData,
  narrationData,
  musicData,
  videoAssemblyData,
  lipsyncData,
  userOption,
  onRegenerateKeyframe,
  onRegenerateVideo,
  onRegenerateCharacter,
  onLipsyncGenerate,
  onLipsyncRegenerate,
  onLipsyncSuccess,
  onVideoAssembled,
  onSceneUpdated,
  onAfterSceneUpdate,
  onChapterUpdated,
  onMentionClick,
  onMusicPromptUpdate,
  conversationId,
  threadId,
  videoProgress,
  zoomLevel,
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
      narrationData,
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
    setSelectedVersions(prev => {
      const newSelected = new Map(prev);
      newSelected.set(videoUuid, versionUuid);
      return newSelected;
    });
    try {
      const res = await api.videoEditing.selectVideoVersion(videoUuid, versionUuid);
      if (res.code !== 0) {
        setSelectedVersions(prev => { const m = new Map(prev); m.delete(videoUuid); if (prevSelected) m.set(videoUuid, prevSelected); return m; });
        toast.error(res.message || t('updateFailed') || '更新失败');
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
    setSelectedKeyframeVersions(prev => {
      const newSelected = new Map(prev);
      newSelected.set(keyframeUuid, versionUuid);
      return newSelected;
    });
    try {
      const res = await api.videoEditing.selectKeyframeVersion(keyframeUuid, versionUuid);
      if (res.code !== 0) {
        setSelectedKeyframeVersions(prev => { const m = new Map(prev); m.delete(keyframeUuid); if (prevSelected) m.set(keyframeUuid, prevSelected); return m; });
        toast.error(res.message || t('updateFailed') || '更新失败');
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

  // ✅ 步骤顺序：优先与会话中最新 workflow_state.path 一致，否则使用 FALLBACK（音乐在故事/风格之前）
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

  const masterTargetDurationSec = useMemo(() => {
    const fromOutline = getMasterDurationSecFromStoryOutline(storyOutlineData);
    if (fromOutline != null && fromOutline > 0.05) {
      return fromOutline;
    }
    const d = userOption?.duration;
    return typeof d === "number" && Number.isFinite(d) && d > 0.05 ? d : undefined;
  }, [storyOutlineData, userOption?.duration]);

  // ✅ 创建占位组件
  const createPlaceholder = (title: string, icon: React.ReactNode) => (
    <Card className="glass p-6">
      <div className="flex items-center gap-3 mb-4">
        {icon}
        <h3 className="text-lg font-semibold font-inter">{title}</h3>
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground ml-auto" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </Card>
  );

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
        if (!prereqsReady) {
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

  // ✅ 确定要渲染的事件顺序
  // 如果任务已开始但未完成，只显示已完成的步骤 + 当前正在生成的步骤（占位）
  // 如果任务已完成，按照 expectedEventOrder 的顺序显示已发生的事件
  const renderableEventOrder = [
    ...expectedEventOrder,
    ...FALLBACK_VIDEO_RESULTS_EVENT_ORDER.filter(
      (eventType) => materializedEvents.has(eventType) && !expectedEventOrder.includes(eventType),
    ),
  ];
  const eventOrderToRender = (hasTaskStarted && !isTaskCompleted
    ? renderableEventOrder.filter(eventType => {
        // 已发生的事件，总是显示
        if (materializedEvents.has(eventType)) {
          return true;
        }
        // 只显示当前正在生成的步骤（作为占位）
        if (eventType === currentGeneratingStep) {
          return true;
        }
        // 其他未开始的步骤不显示
        return false;
      })
    : renderableEventOrder.filter(eventType => materializedEvents.has(eventType)))
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
    console.log('📊 VideoResultsPanel - Event order to render:', eventOrderForRender);
    console.log('📊 VideoResultsPanel - Has task started:', hasTaskStarted, 'Is completed:', isTaskCompleted);
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
            <StorySection storyOutlineData={storyOutlineData} onChapterUpdated={onChapterUpdated} />
            <StyleSection analysisData={analysisData} storyOutlineData={storyOutlineData} />
          </>
        );
      }
      return null;
    },
    characters_designed: () => {
      const hasData = charactersData?.characters?.length > 0;
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return createPlaceholder(t('characterSection') || 'Visual Elements', <User className="w-5 h-5 text-accent-pink" />);
      }
      if (hasData) {
        return <CharactersSection charactersData={charactersData} onFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('overview') : undefined} />;
      }
      return null;
    },
    scenes_generated: () => {
      const hasData = scenesData?.scenes?.length > 0;
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return createPlaceholder(t('sceneSection') || 'Scenes', <MapPin className="w-5 h-5 text-accent-white" />);
      }
      if (hasData) {
        return <ScenesSection scenesData={scenesData} contentCategory={userOption?.content_category} onFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('overview') : undefined} onSceneUpdated={onSceneUpdated} onAfterSceneUpdate={onAfterSceneUpdate} />;
      }
      return null;
    },
    keyframes_generated: () => {
      const totalFromScenes = scenesData?.scenes?.length;
      const hasData = (keyframesData?.keyframes?.length || 0) > 0
        || (Number.isFinite(keyframesData?.total) && keyframesData.total > 0)
        || (Number.isFinite((keyframesData as any)?.shot_total) && (keyframesData as any).shot_total > 0)
        || (Number.isFinite(totalFromScenes) && totalFromScenes > 0);
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return createPlaceholder(t('storyboardsSection') || 'Storyboards', <Film className="w-5 h-5 text-accent-blue" />);
      }
      if (hasData) {
        return (
          <StoryboardsSection 
            keyframesData={keyframesData} 
            onRegenerate={onRegenerateKeyframe} 
            onMentionClick={onMentionClick}
            onFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('storyboard') : undefined}
            zoomLevel={zoomLevel}
            totalCount={totalFromScenes}
            selectedKeyframeVersions={selectedKeyframeVersions}
            onKeyframeVersionSelection={handleKeyframeVersionSelection}
          />
        );
      }
      return null;
    },
    narrations_generated: () => {
      // 旁白文案/音频已并入 ScenesSection（按场景展示），此处不再单独占栏
      return null;
    },
    video_segments_generated: () => {
      const scenesCountForShots = scenesData?.scenes?.length;
      // shots 卡片总数 fallback：scenes.length → videoProgress.total。两者都没有时退化为 undefined。
      const totalForShots = Number.isFinite(scenesCountForShots) && scenesCountForShots > 0
        ? scenesCountForShots
        : (videoProgress?.total != null && videoProgress.total > 0 ? videoProgress.total : undefined);
      // 与 keyframes_generated 一致：videos 还没下发也认数据"在路上"——
      // 只要 scenes 已生成 / videoProgress.total 已通报，就先用 ShotsSection 渲染 N 张
      // loading 骨架卡片，而不是只显示顶部空 placeholder card。
      const hasData = (videosData?.video_generations?.length || 0) > 0
        || (Number.isFinite(videosData?.total) && videosData.total > 0)
        || (Number.isFinite(totalForShots) && (totalForShots as number) > 0);
      if (!hasData && hasTaskStarted && !isTaskCompleted) {
        return createPlaceholder(t('shotsSection') || 'Shots', <Video className="w-5 h-5 text-accent-green" />);
      }
      if (hasData) {
        console.log('🔍 VideoResultsPanel passing to ShotsSection:', {
          conversationId,
          threadId,
          hasVideosData: !!videosData,
          videosCount: videosData?.video_generations?.length || 0
        });
        return (
          <ShotsSection 
            videosData={videosData} 
            onRegenerate={onRegenerateVideo}
            selectedVersions={selectedVersions}
            onVersionSelection={handleVersionSelection}
            conversationId={conversationId}
            threadId={threadId}
            userOption={userOption}
            videoProgress={videoProgress}
            onFullView={IMMERSIVE_MODE_ENABLED ? () => openVideoCheck('shots') : undefined}
            totalCount={totalForShots}
          />
        );
      }
      return null;
    },
    // 不展示 lyrics section，此步骤不再渲染
    video_segments_assembled: () => null,
    music_generated: () => {
      const hasData = !!(musicData?.music_generations?.length);
      const mayShowMusicPlaceholder =
        hasTaskStarted &&
        !isTaskCompleted &&
        (!musicDeferred || materializedEvents.has("scenes_generated"));
      if (!hasData && mayShowMusicPlaceholder && (hasWorkflowPath || !musicDeferred)) {
        return createPlaceholder(
          t("backgroundMusicSection") || t("workflow.node.music") || "Background Music",
          <Music className="w-5 h-5 text-accent-purple" />
        );
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
    <div
      ref={ref}
      className={hideVideoCreationTabs ? "min-w-0 max-w-full" : "flex-1 min-h-0 overflow-y-auto"}
    >
      <div className="px-4 py-6 space-y-8">
        {isMusicOnlyConversation() ? (
          <MusicPlayerSection messages={messages} />
        ) : (
          hideVideoCreationTabs ? (
            <div className="space-y-8">
              {/* Deep Agent workspace hides tabs; still show creative stages so
                  outline/characters/keyframes remain visible before final video. */}
              {renderEventList(creativeEventOrder)}
              {renderEventList(finalEventOrder)}
              {finalEventOrder.length === 0 && creativeEventOrder.length === 0 && hasTaskStarted && !isTaskCompleted && (
                <Card className="glass p-6">
                  <p className="text-sm text-muted-foreground">
                    {t("videoBeingAssembled")}
                  </p>
                </Card>
              )}
            </div>
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

VideoResultsPanel.displayName = "VideoResultsPanel";


