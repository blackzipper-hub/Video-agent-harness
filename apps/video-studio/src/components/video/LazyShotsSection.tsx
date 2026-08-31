import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { VideoWithCleanup } from "@/components/ui/VideoWithCleanup";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Badge } from "@/components/ui/badge";
import { Film, Play, RefreshCw, CheckCircle, XCircle, Video, Loader2, ChevronDown, ChevronUp, Check, X, Sparkles } from "lucide-react";
import { useState, useEffect, useLayoutEffect, useCallback, useRef } from "react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { useLazyLoading } from "@/hooks/useLazyLoading";
import { toast as sonnerToast } from "sonner";
import { buildShotsVideoAssemblyRequestBody, collectShotVideoAssemblySelections } from "@/lib/buildVideoAssemblyRequest";
import { postVideoAssembly } from "@/services/cutiVideoAssembly";
import { VideoArtifactPromptEditorDialog } from "./VideoArtifactPromptEditorDialog";
import { cn } from "@/lib/utils";

interface LazyShotsSectionProps {
  videosData: any;
  onRegenerate?: (shotNumber: number, prompt: string) => Promise<boolean>;
  /** instruction_merge_prompt：返回融合后的 motion_prompt（与关键帧融合弹窗一致） */
  onVideoRefinePromptOnly?: (
    videoUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  selectedVersions?: Map<string, string>;
  onVersionSelection?: (videoUuid: string, versionUuid: string) => void;
  conversationId?: string;
  threadId?: string;
  userOption?: any;
  videoProgress?: {
    completed: number;
    total: number;
    progress_percent: number;
  };
  onSegmentsSynced?: () => void;  // 同步/合成完成后的回调（如刷新 lyrics 区）
  onVideoAssembled?: (assemblyUuid: string) => void | Promise<void>;  // 合成完成后刷新最终视频
  onFullView?: () => void;
  zoomLevel?: number; // ✅ 统一的缩放级别
  totalCount?: number;
  /** post-regenerate regenerate_keyframes 同步下游（实际镜头视频）时与面板 regenerate 的 `${shot}-${vIdx}` 一致 */
  companionDiceKeys?: ReadonlySet<string>;
  /** post-regenerate regenerate_videos→时间线合并：与点「合并视频」同款的按钮 loading，不占镜头 dice */
  companionMergeAssemblyBusy?: boolean;
  /** 嵌入到上层模块时使用：隐藏自身外层卡片与标题 */
  embedded?: boolean;
  /** 外部同步的滚动位置（纵向） */
  syncedScrollTop?: number;
  /** 滚动时回传当前位置，供上层在 Tab 间同步 */
  onSyncedScrollTopChange?: (scrollTop: number) => void;
}

export const LazyShotsSection = ({ videosData, onRegenerate, onVideoRefinePromptOnly, selectedVersions: externalSelectedVersions, onVersionSelection, conversationId, threadId, userOption, videoProgress, onSegmentsSynced, onVideoAssembled, onFullView, zoomLevel = 1, totalCount, companionDiceKeys, companionMergeAssemblyBusy, embedded = false, syncedScrollTop, onSyncedScrollTopChange }: LazyShotsSectionProps) => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());
  const [assembling, setAssembling] = useState(false);
  const [internalSelectedVersions, setInternalSelectedVersions] = useState<Map<string, string>>(new Map());
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());
  const [editingPrompts, setEditingPrompts] = useState<Set<string>>(new Set());
  const [promptValues, setPromptValues] = useState<Map<string, string>>(new Map());
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string } | null>(null);
  const [fusionVideoPromptEditor, setFusionVideoPromptEditor] = useState<{
    videoUuid: string;
    versionUuid: string;
    shotNumber: number;
    originalIndex: number;
    statusKey: string;
    title: string;
    image: string;
    videoUrl: string;
    initialPrompt: string;
  } | null>(null);
  const [hoveredVideoVersionKey, setHoveredVideoVersionKey] = useState<string | null>(null);
  const [expandedVideos, setExpandedVideos] = useState<Set<string>>(new Set()); // 跟踪哪些视频已展开
  const pendingSyncedScrollTopRef = useRef<number | null>(null);
  const mediaContainerRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const promptContainerRefs = useRef<Record<string, HTMLDivElement | null>>({});
  
  const baseCardWidth = 528;
  const baseMediaHeight = 288;
  const scaledCardWidth = baseCardWidth * zoomLevel;
  const scaledMediaHeight = baseMediaHeight * zoomLevel;
  
  // 防抖的高度调整函数
  const adjustTextareaHeight = useCallback((textarea: HTMLTextAreaElement) => {
    requestAnimationFrame(() => {
      const currentHeight = parseInt(textarea.style.height) || 0;
      textarea.style.height = 'auto';
      const newHeight = textarea.scrollHeight;
      
      // 只有高度真正改变时才设置新高度，减少抖动
      if (Math.abs(newHeight - currentHeight) > 1) {
        textarea.style.height = newHeight + 'px';
      } else if (currentHeight > 0) {
        textarea.style.height = currentHeight + 'px';
      } else {
        textarea.style.height = newHeight + 'px';
      }
    });
  }, []);
  
  // 使用外部传入的selectedVersions，如果没有则使用内部状态
  const selectedVersions = externalSelectedVersions || internalSelectedVersions;
  const setSelectedVersions = onVersionSelection ? 
    (videoUuid: string, versionUuid: string) => onVersionSelection(videoUuid, versionUuid) :
    setInternalSelectedVersions;
  
  // API返回格式: { video_generations: [], total: 123 }
  const videos = videosData?.video_generations || [];
  const totalVideos = Number.isFinite(videosData?.total)
    ? videosData.total
    : Number.isFinite(totalCount)
      ? totalCount
      : videos.length;
  const videoMap = new Map<number, any>();
  videos.forEach((video: any, idx: number) => {
    const shotNumber = video?.shot_number ?? idx + 1;
    if (!videoMap.has(shotNumber)) {
      videoMap.set(shotNumber, video);
    }
  });
  const displayVideos = totalVideos > 0
    ? Array.from({ length: totalVideos }, (_, index) => videoMap.get(index + 1) || { shot_number: index + 1, versions: [] })
    : videos;
  const lazyItemsPerPage = embedded ? Math.max(displayVideos.length, 1) : 4;
  // 已生成数：有至少一个 version 带 video_url 的镜头数（与 Storyboards 的 generatedCount 一致，不展示「懒加载可见数」避免误导）
  const generatedVideosCount = displayVideos.filter((v: any) => {
    const vers = v?.versions || [];
    return vers.some((ver: any) => !!ver?.video_url);
  }).length;

  // 使用懒加载 Hook
  const {
    visibleItems: visibleVideos,
    isLoading,
    scrollRef
  } = useLazyLoading(displayVideos, {
    itemsPerPage: lazyItemsPerPage, // 嵌入 Tab 时直接渲染全部，避免切换对齐时闪跳
    threshold: 300,
    preloadPages: 1,
    initialPages: 1,
    autoLoadThrottleMs: 450,
    maxConsecutiveAutoLoads: 2
  });

  // Helper functions for prompt management
  const getPromptKey = (shotNumber: number, versionIndex: number) => `${shotNumber}-${versionIndex}`;
  
  // 展开 prompt 时同步宽度到上方媒体容器，使首次展开即正确换行（与 StoryboardsSection 一致，useLayoutEffect 在绘制前执行避免先出一行再换行）
  useLayoutEffect(() => {
    if (expandedPrompts.size === 0) return;
    const updateWidths = () => {
      expandedPrompts.forEach((key) => {
        const media = mediaContainerRefs.current[key];
        const prompt = promptContainerRefs.current[key];
        if (media && prompt && media.offsetWidth > 0) {
          const parentWidth = prompt.parentElement?.clientWidth;
          const targetWidth = parentWidth ? Math.min(media.offsetWidth, parentWidth) : media.offsetWidth;
          prompt.style.width = `${targetWidth}px`;
        }
      });
    };
    updateWidths();
    const raf1 = requestAnimationFrame(updateWidths);
    const t = setTimeout(updateWidths, 50);
    return () => {
      cancelAnimationFrame(raf1);
      clearTimeout(t);
    };
  }, [expandedPrompts]);

  const togglePromptExpanded = (key: string) => {
    setExpandedPrompts(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const startEditingPrompt = (key: string, currentPrompt: string) => {
    setEditingPrompts(prev => new Set(prev).add(key));
    setPromptValues(prev => new Map(prev).set(key, currentPrompt));
  };
  
  const cancelEditingPrompt = (key: string) => {
    setEditingPrompts(prev => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
    setPromptValues(prev => {
      const next = new Map(prev);
      next.delete(key);
      return next;
    });
  };
  
  const savePromptEdit = (key: string, shotNumber: number) => {
    // 只保存 prompt 编辑，不调用接口
    // 接口调用应该在用户点击 regenerate 按钮时进行
    setEditingPrompts(prev => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
    // 注意：这里不清除 promptValues，保留用户的编辑内容
    // promptValues 会在 regenerate 时被使用
  };

  // 初始化选中状态 - 默认选中当前版本（仅在内部状态时）
  useEffect(() => {
    if (!externalSelectedVersions && videos.length > 0) {
      const initialSelected = new Map<string, string>();
      videos.forEach((video: any) => {
        if (video.versions && video.versions.length > 0) {
          const currentIndex = video.current_version_index || 0;
          const currentVersion = video.versions[currentIndex];
          if (currentVersion) {
            initialSelected.set(video.uuid, currentVersion.uuid);
            // 如果有多个版本，默认折叠（不展开）
            if (video.versions.length > 1) {
              setExpandedVideos(prev => {
                const next = new Set(prev);
                next.delete(video.uuid);
                return next;
              });
            }
          }
        }
      });
      setInternalSelectedVersions(initialSelected);
    }
  }, [videos, externalSelectedVersions]);

  // 处理版本选择，选中后自动折叠
  const handleVersionSelection = (videoUuid: string, versionUuid: string) => {
    const currentSelected = selectedVersions.get(videoUuid);
    const video = videos.find((v: any) => v.uuid === videoUuid);
    const hasMultipleVersions = video && video.versions && video.versions.length > 1;
    const isExpanded = expandedVideos.has(videoUuid);
    
    // 如果点击的是已经选中的版本，且当前是展开状态，则折叠
    if (currentSelected === versionUuid) {
      if (hasMultipleVersions && isExpanded) {
        setExpandedVideos(prev => {
          const next = new Set(prev);
          next.delete(videoUuid);
          return next;
        });
      }
      return;
    }
    
    // 选择新版本
    if (onVersionSelection) {
      onVersionSelection(videoUuid, versionUuid);
    } else {
      const newSelected = new Map(selectedVersions);
      newSelected.set(videoUuid, versionUuid);
      setInternalSelectedVersions(newSelected);
    }
    
    // 选中后折叠（如果有多个版本）
    if (hasMultipleVersions) {
      setExpandedVideos(prev => {
        const next = new Set(prev);
        next.delete(videoUuid);
        return next;
      });
    }
  };

  const handleAssembleVideo = async () => {
    if (!threadId) {
      toast({
        title: t('errorOccurredGeneral'),
        description: "缺少 thread，无法合并视频",
        variant: "destructive"
      });
      return;
    }

    setAssembling(true);
    try {
      const body = buildShotsVideoAssemblyRequestBody(
        threadId,
        userOption,
        collectShotVideoAssemblySelections(videosData, selectedVersions)
      );
      const result = await postVideoAssembly(body);
      if (result.code === 0) {
        toast({
          title: t('assembleVideoSuccess'),
          description: t('videoAssembledSuccessfully'),
        });
        const assemblyUuid = result?.data?.assembly_uuid;
        if (onVideoAssembled && assemblyUuid) {
          await onVideoAssembled(assemblyUuid);
        }
        if (onSegmentsSynced) {
          onSegmentsSynced();
        }
      } else {
        throw new Error(result.message || t('assembleVideoFailed'));
      }
    } catch (error) {
      console.error('合并视频失败:', error);
      toast({
        title: t('errorOccurredGeneral'),
        description: `${t('assembleVideoFailed')}: ${error.message}`,
        variant: "destructive"
      });
    } finally {
      setAssembling(false);
    }
  };

  // 计算累计时间戳
  const calculateTimestamps = () => {
    let cumulativeTime = 0;
    return videos.map((video: any) => {
      const currentVersion = video.versions && video.versions[video.current_version_index || 0];
      const duration = currentVersion?.duration || 0;
      const start = cumulativeTime;
      const end = cumulativeTime + duration;
      cumulativeTime = end;
      return { start, end, duration };
    });
  };

  const timestamps = calculateTimestamps();

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    const secsWhole = Math.floor(secs);
    const secsFrac = secs - secsWhole;
    if (secsFrac > 1e-6) {
      const fracStr = (Math.round(secsFrac * 100) / 100).toFixed(2).slice(2);
      return `${mins.toString().padStart(2, '0')}:${secsWhole.toString().padStart(2, '0')}.${fracStr}`;
    }
    return `${mins.toString().padStart(2, '0')}:${secsWhole.toString().padStart(2, '0')}`;
  };

  // 调试信息
  console.log('🔍 LazyShotsSection Debug:', {
    threadId,
    videosCount: videos.length,
    visibleCount: visibleVideos.length,
    assembling,
    hasThreadId: !!threadId,
    scaledCardWidth,
    scaledMediaHeight,
    totalWidth: visibleVideos.length * (scaledCardWidth + 16)
  });

  // 在所有 Hook 调用之后进行条件判断
  if (displayVideos.length === 0) return null;

  const tryApplySyncedScrollTop = useCallback(() => {
    const pendingTop = pendingSyncedScrollTopRef.current;
    if (typeof pendingTop !== "number") return;
    const el = scrollRef.current;
    if (!el) return;
    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
    const appliedTop = Math.min(Math.max(pendingTop, 0), maxTop);
    if (Math.abs(el.scrollTop - appliedTop) > 1) {
      el.scrollTop = appliedTop;
    }
    if (pendingTop <= maxTop + 1) {
      pendingSyncedScrollTopRef.current = null;
    }
  }, [scrollRef]);

  useLayoutEffect(() => {
    if (typeof syncedScrollTop !== "number") return;
    pendingSyncedScrollTopRef.current = Math.max(0, syncedScrollTop);
    tryApplySyncedScrollTop();
  }, [syncedScrollTop, tryApplySyncedScrollTop]);

  useEffect(() => {
    tryApplySyncedScrollTop();
  }, [visibleVideos.length, tryApplySyncedScrollTop]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      tryApplySyncedScrollTop();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [scrollRef, tryApplySyncedScrollTop]);

  return (
    <Card className={cn(embedded ? "p-0 border-0 shadow-none bg-transparent" : "glass p-6 h-auto")}>
      {!embedded && (
      <div className="flex items-center justify-between mb-4">
        <Tooltip>
          <TooltipTrigger asChild>
            <h3 className="text-lg font-semibold flex items-center cursor-help">
              <Film className="w-5 h-5 mr-2 text-accent-purple" />
              {t('shotsSection')}
              {/* 与 Storyboards 一致：有 videoProgress 时显示后端进度 (completed/total)，否则显示已生成数/总数（有 video_url 的镜头数），不展示懒加载可见数 */}
              <span className="ml-2 text-sm text-muted-foreground">
                {videoProgress && videoProgress.total > 0
                  ? `(${videoProgress.completed} / ${videoProgress.total})`
                  : displayVideos.length > 0
                    ? `(${generatedVideosCount} / ${displayVideos.length})`
                    : null}
              </span>
            </h3>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-xs">
            {t('shotsSectionTooltip')}
          </TooltipContent>
        </Tooltip>
        <div className="flex items-center gap-2">
          {onFullView && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onFullView}
              className="h-8 px-3"
            >
              {t('immersiveMode')}
            </Button>
          )}
        </div>
      </div>
      )}
      {/* 视频生成进度条 */}
      {videoProgress && videoProgress.total > 0 && (
        <div className="mb-4 p-4 glass rounded-lg border border-white/10">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-gradient-to-r from-blue-400 to-purple-400" />
            <span className="text-sm font-medium">{t('videoGenerationProgress')}</span>
          </div>
          
          <div className="space-y-3">
            {/* Progress Info */}
            <div className="flex items-center justify-between text-sm">
              <span>{videoProgress.completed}/{videoProgress.total} {t('clipsUnit')}</span>
              <span>{videoProgress.progress_percent}%</span>
            </div>
            
            {/* Progress Bar */}
            <div className="w-full bg-white/10 rounded-full h-2 overflow-hidden">
              <div 
                className="bg-gradient-to-r from-blue-400 via-purple-400 to-blue-500 h-full rounded-full transition-all duration-500 ease-out"
                style={{ width: `${videoProgress.progress_percent}%` }}
              />
            </div>
          </div>
        </div>
      )}
      {/* 平铺显示容器 - 固定高度网格，内部纵向滚动 */}
      <div
        ref={scrollRef}
        className="h-[640px] overflow-y-auto scrollbar-subtle pr-1"
        onScroll={(e) => {
          onSyncedScrollTopChange?.(e.currentTarget.scrollTop);
        }}
      >
        <div
          className="grid grid-cols-1 gap-4 pb-4 pr-2 md:grid-cols-2 xl:grid-cols-3"
        >
        {visibleVideos.map((video: any, idx: number) => {
          // 获取当前版本的视频URL
          const currentVersion = video.versions && video.versions[video.current_version_index || 0];
          const videoUrl = currentVersion?.video_url;
          const keyframeUrl = currentVersion?.keyframe_url || video.keyframe_url;
          const shotNumber = video.shot_number || idx + 1;
          const timestamp = timestamps[idx];
          const versions = video.versions || [];

          return (
            <Card key={idx} className="glass-bg p-2 flex flex-col items-center group w-full h-fit" style={{ border: 'none', boxShadow: 'none' }}>
              {/* 时间戳信息 */}
              {timestamp && timestamp.duration > 0 && (
                <div className="text-xs text-muted-foreground font-normal mb-3 w-full text-center">
                  {formatTime(timestamp.start)} - {formatTime(timestamp.end)} ({Number(timestamp.duration).toFixed(2)}s)
                </div>
              )}

              {/* 所有版本展示 - 垂直排列 */}
              <div className="space-y-3 flex flex-col items-center w-full relative">
                {(() => {
                  const hasMultipleVersions = versions.length > 1;
                  const selectedVersionUuid = selectedVersions.get(video.uuid);
                  const isExpanded = expandedVideos.has(video.uuid);
                  
                  // 如果有多个版本且已选中但未展开，只显示选中的版本
                  const versionsToShow = hasMultipleVersions && selectedVersionUuid && !isExpanded
                    ? versions.filter((v: any) => v.uuid === selectedVersionUuid)
                    : versions || [];

                  return (
                    <>
                      {versionsToShow.length > 0 ? versionsToShow.map((version: any, vIdx: number) => {
                        const versionVideoUrl = version.video_url;
                        const versionKeyframeUrl = version.keyframe_url;
                        const isSelected = selectedVersionUuid === version.uuid;
                        const originalIndex = versions.findIndex((v: any) => v.uuid === version.uuid);

                        const vidVerHoverKey = `${video.uuid}-${version.uuid}`;
                        return (
                          <div
                            key={version.uuid || vIdx}
                            className="inline-flex w-full flex-col items-center group"
                            onMouseEnter={() => setHoveredVideoVersionKey(vidVerHoverKey)}
                            onMouseLeave={() => setHoveredVideoVersionKey(null)}
                          >
                            <h3 className="mb-3 w-full min-h-[1.25rem] truncate px-1 text-center text-sm text-foreground/80 transition-colors group-hover:text-foreground">
                              {hoveredVideoVersionKey === vidVerHoverKey
                                ? `${t('version')} ${originalIndex + 1}`
                                : `${t('shot')} ${shotNumber}`}
                            </h3>
                      <div
                        className={cn(
                          "relative w-full cursor-pointer overflow-hidden rounded-2xl transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] ring-2 ring-transparent",
                          isSelected && "ring-[hsl(var(--selection-ring))]",
                          hoveredVideoVersionKey === vidVerHoverKey
                            ? "scale-[1.03] -translate-y-1"
                            : "scale-100 translate-y-0",
                        )}
                        style={{
                          boxShadow:
                            hoveredVideoVersionKey === vidVerHoverKey
                              ? "var(--artifact-shadow-card-hover)"
                              : "var(--artifact-shadow-card)",
                        }}
                      >
                      <div
                        ref={(el) => { mediaContainerRefs.current[getPromptKey(shotNumber, originalIndex)] = el; }}
                        className="group/media relative inline-flex items-center justify-center overflow-hidden rounded-xl bg-black/5"
                      >
                        {/* ID序号 - 左上角，hover时显示 */}
                        <div className="absolute top-2 left-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                          <div 
                            className="px-1.5 py-0.5 rounded text-xs font-normal bg-black/30 text-white/70 backdrop-blur-sm"
                            style={{ fontSize: `${Math.max(4, 7 * zoomLevel)}px` }}
                          >
                            {shotNumber}
                          </div>
                        </div>
                        {/* 生成模式（generation_mode）标签 - 右上角，有数据时显示可读文案，hover 显示说明 */}
                        {video.generation_mode && (() => {
                          const mode = video.generation_mode;
                          const label = mode === 'normal' ? t('generationModeNormal') : mode === 'lipsync' ? t('generationModeLipsync') : mode === 'empty_shot' ? t('generationModeEmptyShot') : mode;
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                                  <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-black/40 text-white/90 border-0">
                                    {label}
                                  </Badge>
                                </div>
                              </TooltipTrigger>
                              <TooltipContent side="top">{t('generationModeLabel')}: {label}</TooltipContent>
                            </Tooltip>
                          );
                        })()}
                        {versionVideoUrl ? (
                          <div
                            className={cn(
                              "w-full overflow-hidden rounded-lg transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]",
                              hoveredVideoVersionKey === vidVerHoverKey ? "scale-110" : "scale-100",
                            )}
                          >
                          <VideoWithCleanup
                            className="block object-contain rounded-lg"
                            style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                            controls
                            poster={versionKeyframeUrl}
                            preload="metadata"
                            autoPlay
                            loop
                            muted
                          >
                            <source src={versionVideoUrl} type="video/mp4" />
                          </VideoWithCleanup>
                          </div>
                        ) : versionKeyframeUrl ? (
                          <div className="relative w-full overflow-hidden rounded-lg">
                            <img
                              src={versionKeyframeUrl}
                              alt={`${t('shot')} ${shotNumber} v${originalIndex + 1} Keyframe`}
                              className={cn(
                                "block max-h-full max-w-full cursor-pointer rounded-lg object-contain transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]",
                                hoveredVideoVersionKey === vidVerHoverKey ? "scale-110" : "scale-100",
                              )}
                              style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                              onClick={() => setPreviewImage({ url: versionKeyframeUrl, alt: `${t('shot')} ${shotNumber} v${originalIndex + 1} Keyframe` })}
                              loading="lazy"
                            />
                            <div className="absolute inset-0 flex items-center justify-center bg-black/30 pointer-events-none">
                              <Play className="w-8 h-8 text-white/80" />
                            </div>
                          </div>
                        ) : (
                          <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: '100%' }}>
                            <Play className="w-8 h-8 text-gray-400" />
                          </div>
                        )}
                        {isSelected && (versionVideoUrl || versionKeyframeUrl) && hasMultipleVersions && isExpanded && (
                          <div
                            className="pointer-events-none absolute right-2 top-2 z-40 flex h-7 w-7 items-center justify-center rounded-full bg-[hsl(var(--selection-ring))] text-white shadow-md"
                            aria-hidden
                          >
                            <Check className="h-4 w-4" strokeWidth={3} />
                          </div>
                        )}
                        {hasMultipleVersions && isExpanded && (
                          <button
                            type="button"
                            className="absolute inset-0 z-30 cursor-pointer bg-transparent"
                            aria-label={t('selectVersion')}
                            title={t('selectVersion')}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleVersionSelection(video.uuid, version.uuid);
                            }}
                          />
                        )}

                      </div>
                      </div>

                      {/* 错误消息展示 */}
                      {version.success === false && version.error_msg && (
                        <div className="mt-2 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-700 w-full">
                          <div className="flex items-start gap-1">
                            <XCircle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                            <span className="flex-1">{version.error_msg}</span>
                          </div>
                        </div>
                      )}

                      {/* Edit 模块 - hover时显示在视频下方 */}
                      <div className="w-full flex items-center justify-center gap-2 mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        {/* Regenerate按钮 */}
                        {(() => {
                          const versionKey = `${shotNumber}-${originalIndex}`;
                          const isRegenerating =
                            regenerating.has(versionKey) || (companionDiceKeys?.has(versionKey) ?? false);
                          
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                            <Button
                              size="sm"
                              className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm"
                              disabled={isRegenerating}
                              onClick={async (e) => {
                                e.stopPropagation();
                                if (!onRegenerate) return;

                                // 设置loading状态
                                setRegenerating(prev => new Set(prev).add(versionKey));

                                try {
                                  // 获取用户编辑的 prompt，如果没有则使用原始 prompt
                                  const promptKey = getPromptKey(shotNumber, originalIndex);
                                  const editedPrompt = promptValues.get(promptKey);
                                  const promptToUse = editedPrompt || version.motion_prompt || '';
                                  
                                  // 调用API并等待结果
                                  await onRegenerate(shotNumber, promptToUse);
                                } finally {
                                  // 无论成功或失败，都清除loading状态
                                  setRegenerating(prev => {
                                    const next = new Set(prev);
                                    next.delete(versionKey);
                                    return next;
                                  });
                                }
                              }}
                            >
                              <span className={`text-xl ${isRegenerating ? 'animate-spin' : ''}`} style={{ filter: 'drop-shadow(2px 2px 4px rgba(220, 38, 38, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))', textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)' }}>🎲</span>
                            </Button>
                              </TooltipTrigger>
                              <TooltipContent side="top">{isRegenerating ? t('regenerating') : t('regenerate')}</TooltipContent>
                            </Tooltip>
                          );
                        })()}

                        {/* 展开 Prompt / 融合编辑 motion（与关键帧 ✏️ 行为对齐） */}
                        {(() => {
                          const promptKey = getPromptKey(shotNumber, originalIndex);
                          const fusionImg = versionKeyframeUrl;
                          const openFusion = Boolean(
                            onVideoRefinePromptOnly &&
                              video?.uuid &&
                              version?.uuid &&
                              fusionImg,
                          );
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (openFusion && onVideoRefinePromptOnly) {
                                  setFusionVideoPromptEditor({
                                    videoUuid: video.uuid,
                                    versionUuid: version.uuid,
                                    shotNumber,
                                    originalIndex,
                                    statusKey: `${shotNumber}-${originalIndex}-prompt-edit`,
                                    title: `${t('shotsSection') || 'Shots'} · ${t('versionLabel').replace('{version}', String(originalIndex + 1))}`,
                                    image: fusionImg as string,
                                    videoUrl: String(version?.video_url || ''),
                                    initialPrompt: (version.motion_prompt || '') as string,
                                  });
                                } else {
                                  togglePromptExpanded(promptKey);
                                }
                              }}
                            >
                              <span className="text-xl" style={{ filter: 'drop-shadow(2px 2px 4px rgba(34, 197, 94, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))', textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)' }}>✏️</span>
                            </Button>
                              </TooltipTrigger>
                              <TooltipContent side="top">{t('editPrompt')}</TooltipContent>
                            </Tooltip>
                          );
                        })()}
                      </div>

                      {/* Regenerating 状态提示 */}
                      {(() => {
                        const versionKey = `${shotNumber}-${originalIndex}`;
                        const isRegenerating =
                          regenerating.has(versionKey) || (companionDiceKeys?.has(versionKey) ?? false);
                        return isRegenerating && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t('regenerating')}</div>
                        );
                      })()}
                      {(() => {
                        const promptEditKey = `${shotNumber}-${originalIndex}-prompt-edit`;
                        const isPromptEdit = regenerating.has(promptEditKey);
                        return isPromptEdit && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t("editPromptRegenerating")}</div>
                        );
                      })()}

                      {/* Motion Prompt展示和编辑 - ref 同步上方媒体宽度，首次展开即正确换行 */}
                      {(() => {
                        const promptKey = getPromptKey(shotNumber, originalIndex);
                        const isExpanded = expandedPrompts.has(promptKey);
                        const isEditing = editingPrompts.has(promptKey);
                        const currentPromptValue = promptValues.has(promptKey) ? (promptValues.get(promptKey) as string) : (version.motion_prompt || '');
                        
                        return (
                          <div
                            ref={(el) => { promptContainerRefs.current[promptKey] = el; }}
                            className="mt-1 w-full flex justify-center"
                          >
                            {/* Prompt内容 - 展开时显示，宽度由 useEffect 同步为上方媒体宽度 */}
                            {isExpanded && (
                              <div className="bg-white dark:bg-card border border-gray-200 dark:border-border rounded-lg w-full max-w-full" style={{ width: '100%', maxWidth: '100%' }}>
                                {isEditing ? (
                                  <div className="flex flex-col bg-white dark:bg-card">
                                    <Textarea
                                      ref={(el) => {
                                        if (el) {
                                          // 组件挂载时立即调整高度
                                          setTimeout(() => adjustTextareaHeight(el), 0);
                                        }
                                      }}
                                      value={currentPromptValue}
                                      data-prompt-key={promptKey}
                                      onChange={(e) => {
                                        setPromptValues(prev => new Map(prev).set(promptKey, e.target.value));
                                        // 自动调整高度
                                        adjustTextareaHeight(e.target);
                                      }}
                                      className="w-full resize-none border-0 focus-visible:ring-0 focus-visible:ring-offset-0 text-sm leading-relaxed px-4 py-3 bg-transparent text-gray-900 dark:text-card-foreground placeholder:text-gray-500 dark:placeholder:text-muted-foreground"
                                      placeholder={t('enterMotionPrompt')}
                                      style={{ 
                                        height: 'auto', 
                                        overflow: 'hidden',
                                        transition: 'height 0.1s ease-out',
                                        resize: 'none'
                                      }}
                                      onInput={(e) => adjustTextareaHeight(e.target as HTMLTextAreaElement)}
                                    />
                                    <div className="flex items-center justify-end gap-3 px-4 py-3 border-t border-gray-100 dark:border-border bg-white dark:bg-card">
                                      <Tooltip>
                                        <TooltipTrigger asChild>
                                          <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => cancelEditingPrompt(promptKey)}
                                            className="h-8 w-8 p-0 rounded-full border border-gray-200 dark:border-border hover:bg-gray-100 dark:hover:bg-accent"
                                          >
                                            <X className="w-5 h-5 text-gray-500 dark:text-muted-foreground" />
                                          </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="top">{t('cancel')}</TooltipContent>
                                      </Tooltip>
                                      <Tooltip>
                                        <TooltipTrigger asChild>
                                          <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => savePromptEdit(promptKey, shotNumber)}
                                            className="h-8 w-8 p-0 rounded-full border border-emerald-200 dark:border-emerald-700 hover:bg-emerald-50 dark:hover:bg-emerald-950/50"
                                          >
                                            <Check className="w-5 h-5 text-emerald-500 dark:text-emerald-400" />
                                          </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="top">{t('save')}</TooltipContent>
                                      </Tooltip>
                                    </div>
                                  </div>
                                ) : (
                                  <div 
                                    className="px-4 py-4 text-sm text-gray-700 dark:text-card-foreground leading-relaxed cursor-pointer hover:bg-gray-50 dark:hover:bg-accent transition-colors break-words min-h-[3rem]"
                                    onClick={() => startEditingPrompt(promptKey, currentPromptValue)}
                                  >
                                    {currentPromptValue || <span className="text-muted-foreground">{t('enterMotionPrompt')}</span>}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}

                      {/* 虚线连接到下一个版本 - 加粗加深 */}
                      {originalIndex < versionsToShow.length - 1 && (
                        <div className="flex justify-center my-3">
                          <div className="w-0.5 h-6 border-l-2 border-dashed border-gray-400"></div>
                        </div>
                      )}
                    </div>
                  );
                }) : (
                  (() => {
                    const emptyVideoPromptKey = getPromptKey(shotNumber, 0);
                    const emptyRegenKey = `${shotNumber}-video-empty`;
                    const isRegeneratingEmpty = regenerating.has(emptyRegenKey);
                    return (
                  <div className="inline-flex flex-col items-center w-full group">
                    <div
                      ref={(el) => { mediaContainerRefs.current[emptyVideoPromptKey] = el; }}
                      className="inline-flex flex-shrink-0 rounded-lg relative items-center justify-center overflow-hidden transition-all duration-200 bg-black/5"
                      style={{ width: '100%', height: `${scaledMediaHeight}px` }}
                    >
                      <div className="absolute inset-0 flex items-center justify-center">
                        <div className="w-full h-full rounded-lg bg-gray-100 dark:bg-gray-800 animate-pulse flex items-center justify-center">
                          <span className="text-xs text-gray-400">{t('loading') || 'Loading...'}</span>
                        </div>
                      </div>
                      <div className="absolute top-2 left-2 opacity-0 group-hover:opacity-100 transition-opacity z-10 pointer-events-none">
                        <div
                          className="px-1.5 py-0.5 rounded text-xs font-normal bg-black/30 text-white/70 backdrop-blur-sm"
                          style={{ fontSize: `${Math.max(4, 7 * zoomLevel)}px` }}
                        >
                          {shotNumber}
                        </div>
                      </div>
                    </div>
                    <div className="w-full flex items-center justify-center gap-2 mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            size="sm"
                            className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm"
                            disabled={isRegeneratingEmpty}
                            onClick={async (e) => {
                              e.stopPropagation();
                              if (!onRegenerate) return;
                              setRegenerating((prev) => new Set(prev).add(emptyRegenKey));
                              try {
                                const editedPrompt = promptValues.get(emptyVideoPromptKey);
                                await onRegenerate(shotNumber, editedPrompt || '');
                              } finally {
                                setRegenerating((prev) => {
                                  const next = new Set(prev);
                                  next.delete(emptyRegenKey);
                                  return next;
                                });
                              }
                            }}
                          >
                            <span
                              className={`text-xl ${isRegeneratingEmpty ? 'animate-spin' : ''}`}
                              style={{
                                filter:
                                  'drop-shadow(2px 2px 4px rgba(220, 38, 38, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))',
                                textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)',
                              }}
                            >
                              🎲
                            </span>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top">{isRegeneratingEmpty ? t('regenerating') : t('regenerate')}</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              togglePromptExpanded(emptyVideoPromptKey);
                            }}
                          >
                            <span className="text-xl" style={{ filter: 'drop-shadow(2px 2px 4px rgba(34, 197, 94, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))', textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)' }}>✏️</span>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top">{t('editPrompt')}</TooltipContent>
                      </Tooltip>
                    </div>
                    {isRegeneratingEmpty && (
                      <div className="text-xs text-blue-500 mb-2 text-center">{t('regenerating')}</div>
                    )}
                    {(() => {
                      const isExpanded = expandedPrompts.has(emptyVideoPromptKey);
                      const isEditing = editingPrompts.has(emptyVideoPromptKey);
                      const currentPromptValue = promptValues.has(emptyVideoPromptKey) ? (promptValues.get(emptyVideoPromptKey) as string) : '';
                      return (
                        <div
                          ref={(el) => { promptContainerRefs.current[emptyVideoPromptKey] = el; }}
                          className="mt-1 w-full flex justify-center"
                        >
                          {isExpanded && (
                            <div className="bg-white dark:bg-card border border-gray-200 dark:border-border rounded-lg w-full max-w-full" style={{ width: '100%', maxWidth: '100%' }}>
                              {isEditing ? (
                                <div className="flex flex-col bg-white dark:bg-card">
                                  <Textarea
                                    ref={(el) => {
                                      if (el) {
                                        setTimeout(() => adjustTextareaHeight(el), 0);
                                      }
                                    }}
                                    value={currentPromptValue}
                                    data-prompt-key={emptyVideoPromptKey}
                                    onChange={(e) => {
                                      setPromptValues(prev => new Map(prev).set(emptyVideoPromptKey, e.target.value));
                                      adjustTextareaHeight(e.target);
                                    }}
                                    className="w-full resize-none border-0 focus-visible:ring-0 focus-visible:ring-offset-0 text-sm leading-relaxed px-4 py-3 bg-transparent text-gray-900 dark:text-card-foreground placeholder:text-gray-500 dark:placeholder:text-muted-foreground"
                                    placeholder={t('enterMotionPrompt')}
                                    style={{
                                      height: 'auto',
                                      overflow: 'hidden',
                                      transition: 'height 0.1s ease-out',
                                      resize: 'none'
                                    }}
                                    onInput={(e) => adjustTextareaHeight(e.target as HTMLTextAreaElement)}
                                  />
                                  <div className="flex items-center justify-end gap-3 px-4 py-3 border-t border-gray-100 dark:border-border bg-white dark:bg-card">
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <Button
                                          size="sm"
                                          variant="ghost"
                                          onClick={() => cancelEditingPrompt(emptyVideoPromptKey)}
                                          className="h-8 w-8 p-0 rounded-full border border-gray-200 dark:border-border hover:bg-gray-100 dark:hover:bg-accent"
                                        >
                                          <X className="w-5 h-5 text-gray-500 dark:text-muted-foreground" />
                                        </Button>
                                      </TooltipTrigger>
                                      <TooltipContent side="top">{t('cancel')}</TooltipContent>
                                    </Tooltip>
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <Button
                                          size="sm"
                                          variant="ghost"
                                          onClick={() => savePromptEdit(emptyVideoPromptKey, shotNumber)}
                                          className="h-8 w-8 p-0 rounded-full border border-emerald-200 dark:border-emerald-700 hover:bg-emerald-50 dark:hover:bg-emerald-950/50"
                                        >
                                          <Check className="w-5 h-5 text-emerald-500 dark:text-emerald-400" />
                                        </Button>
                                      </TooltipTrigger>
                                      <TooltipContent side="top">{t('save')}</TooltipContent>
                                    </Tooltip>
                                  </div>
                                </div>
                              ) : (
                                <div
                                  className="px-4 py-4 text-sm text-gray-700 dark:text-card-foreground leading-relaxed cursor-pointer hover:bg-gray-50 dark:hover:bg-accent transition-colors break-words min-h-[3rem]"
                                  onClick={() => startEditingPrompt(emptyVideoPromptKey, currentPromptValue)}
                                >
                                  {currentPromptValue || <span className="text-muted-foreground">{t('enterMotionPrompt')}</span>}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                    );
                  })()
                )}
                    </>
                  );
                })()}
                
                {/* 版本数按钮 - 版本数大于1时显示在视频下方右边 */}
                {versions.length > 1 && (() => {
                  const isExpanded = expandedVideos.has(video.uuid);
                  return (
                    <div className="w-full flex justify-end mt-2">
                      <button
                        className="px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded transition-all duration-200 flex items-center gap-1"
                        onClick={() => {
                          setExpandedVideos(prev => {
                            const next = new Set(prev);
                            if (next.has(video.uuid)) {
                              next.delete(video.uuid);
                            } else {
                              next.add(video.uuid);
                            }
                            return next;
                          });
                        }}
                        title={isExpanded ? t('collapseVersions') : t('expandVersions')}
                      >
                        {t('versionsCount').replace('{count}', String(versions.length))}
                        {isExpanded ? (
                          <ChevronUp className="w-3 h-3" />
                        ) : (
                          <ChevronDown className="w-3 h-3" />
                        )}
                      </button>
                    </div>
                  );
                })()}
              </div>
            </Card>
          );
        })}
        
        {/* 懒加载扩展：加载更多指示器 */}
        {isLoading && (
          <div 
            className="flex items-center justify-center bg-card/30 rounded-lg border-2 border-dashed border-gray-300 min-h-[220px] w-full"
          >
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Loader2 className="w-6 h-6 animate-spin" />
              <span className="text-sm">{t('loadingVideos')}</span>
            </div>
          </div>
        )}
        
        </div>
      </div>

      {/* 合并视频按钮 - 调用 video-assembly（后端先 sync 再合成）；companionMergeAssemblyBusy：post-regenerate 时间线同步与成片合并进行中 */}
      <div className="flex flex-col items-center mt-6 gap-3">
        <Button
          onClick={handleAssembleVideo}
          disabled={assembling || !!companionMergeAssemblyBusy || !threadId}
          className="bg-gradient-to-r from-green-500 to-blue-500 hover:from-green-600 hover:to-blue-600 text-white"
          size="lg"
        >
          {assembling || companionMergeAssemblyBusy ? (
            <>
              <Loader2 className="w-5 h-5 mr-2 animate-spin" />
              {t('syncing')}
            </>
          ) : (
            <>
              <Video className="w-5 h-5 mr-2" />
              {t('assembleVideo')}
            </>
          )}
        </Button>
      </div>
      
      {/* 图片预览对话框 */}
      <Dialog open={!!previewImage} onOpenChange={(open) => !open && setPreviewImage(null)}>
        <DialogContent className="max-w-[95vw] max-h-[95vh] p-2 bg-black/90 border-0">
          {previewImage && (
            <div className="relative w-full h-full flex items-center justify-center">
              <img
                src={previewImage.url}
                alt={previewImage.alt}
                className="max-w-full max-h-[95vh] object-contain rounded-lg"
                onClick={(e) => e.stopPropagation()}
              />
            </div>
          )}
        </DialogContent>
      </Dialog>

      {fusionVideoPromptEditor && onVideoRefinePromptOnly && (
        <VideoArtifactPromptEditorDialog
          open={!!fusionVideoPromptEditor}
          title={fusionVideoPromptEditor.title}
          image={fusionVideoPromptEditor.image}
          videoUrl={fusionVideoPromptEditor.videoUrl}
          initialPrompt={fusionVideoPromptEditor.initialPrompt}
          artifactKind="video"
          threadId={threadId}
          onClose={() => setFusionVideoPromptEditor(null)}
          onRefineInstruction={(instruction) =>
            onVideoRefinePromptOnly(
              fusionVideoPromptEditor.videoUuid,
              fusionVideoPromptEditor.versionUuid,
              instruction,
            )
          }
          onSaveAndRegenerate={async (newPrompt) => {
            if (!onRegenerate) {
              throw new Error("onRegenerate is not available");
            }
            const { statusKey } = fusionVideoPromptEditor;
            setRegenerating((prev) => new Set(prev).add(statusKey));
            try {
              const ok = await onRegenerate(
                fusionVideoPromptEditor.shotNumber,
                newPrompt,
                fusionVideoPromptEditor.originalIndex,
              );
              if (!ok) {
                throw new Error(t("failedRegenerateVideo"));
              }
            } finally {
              setRegenerating((prev) => {
                const next = new Set(prev);
                next.delete(statusKey);
                return next;
              });
            }
          }}
        />
      )}
    </Card>
  );
};
