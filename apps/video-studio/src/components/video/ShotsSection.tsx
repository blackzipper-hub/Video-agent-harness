import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { VideoWithCleanup } from "@/components/ui/VideoWithCleanup";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Film, Play, RefreshCw, CheckCircle, XCircle, Video, Loader2, ZoomIn, ZoomOut, ChevronDown, ChevronUp, Check, X } from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { buildShotsVideoAssemblyRequestBody, collectShotVideoAssemblySelections } from "@/lib/buildVideoAssemblyRequest";
import { postVideoAssembly } from "@/services/cutiVideoAssembly";

interface ShotsSectionProps {
  videosData: any;
  onRegenerate?: (shotNumber: number, prompt: string) => Promise<boolean>;
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
  onFullView?: () => void;
  totalCount?: number;
}

export const ShotsSection = ({ videosData, onRegenerate, selectedVersions: externalSelectedVersions, onVersionSelection, conversationId, threadId, userOption, videoProgress, onFullView, totalCount }: ShotsSectionProps) => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());
  const [assembling, setAssembling] = useState(false);
  const [internalSelectedVersions, setInternalSelectedVersions] = useState<Map<string, string>>(new Map());
  const [zoomLevel, setZoomLevel] = useState(1);
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());
  const [editingPrompts, setEditingPrompts] = useState<Set<string>>(new Set());
  const [promptValues, setPromptValues] = useState<Map<string, string>>(new Map());
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string } | null>(null);
  
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

  // Helper functions for prompt management
  const getPromptKey = (shotNumber: number, versionIndex: number) => `${shotNumber}-${versionIndex}`;
  
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
          }
        }
      });
      setInternalSelectedVersions(initialSelected);
    }
  }, [videos, externalSelectedVersions]);

  const handleVideoAssembly = async () => {
    if (!threadId) {
      toast({
        title: "错误",
        description: "缺少 thread，无法进行视频组装",
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
          title: t('success'),
          description: t('videoAssemblySuccess'),
        });
        // 可以在这里触发页面刷新或状态更新
      } else {
        throw new Error(result.message || t('videoAssemblyFailed'));
      }
    } catch (error) {
      console.error('视频组装失败:', error);
      toast({
        title: t('errorOccurredGeneral'),
        description: `${t('videoAssemblyFailed')}: ${error instanceof Error ? error.message : String(error)}`,
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
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  // 调试信息
  console.log('🔍 ShotsSection Debug:', {
    threadId,
    videosCount: videos.length,
    assembling,
    hasThreadId: !!threadId,
    scaledCardWidth,
    scaledMediaHeight,
    totalWidth: displayVideos.length * (scaledCardWidth + 16)
  });

  // 如果没有视频数据，返回null
  if (displayVideos.length === 0) return null;

  return (
    <Card className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <Film className="w-5 h-5 mr-2 text-accent-purple" />
          {t('shotsSection')}
        </h3>
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
          {/* 缩放控制 */}
          <div className="flex items-center gap-1 bg-white/10 rounded-lg p-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setZoomLevel(prev => Math.max(0.1, prev - 0.1))}
              className="h-8 w-8 p-0"
            >
              <ZoomOut className="w-4 h-4" />
            </Button>
            <span className="text-xs px-2 min-w-[3rem] text-center">
              {Math.round(zoomLevel * 100)}%
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setZoomLevel(prev => Math.min(4, prev + 0.1))}
              className="h-8 w-8 p-0"
            >
              <ZoomIn className="w-4 h-4" />
            </Button>
          </div>
          
          <Button
            onClick={handleVideoAssembly}
            disabled={assembling || !threadId}
            className="bg-gradient-to-r from-blue-500 to-purple-500 hover:from-blue-600 hover:to-purple-600 text-white"
          >
            {assembling ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('assembling')}
              </>
            ) : (
              <>
                <Video className="w-4 h-4 mr-2" />
                {t('assembleVideo')}
              </>
            )}
          </Button>
        </div>
      </div>
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
        className="h-[640px] overflow-y-auto scrollbar-subtle pr-1"
      >
        <div
          className="grid grid-cols-1 gap-4 pb-4 pr-2 md:grid-cols-2 xl:grid-cols-3"
        >
        {displayVideos.map((video: any, idx: number) => {
          // 获取当前版本的视频URL
          const currentVersion = video.versions && video.versions[video.current_version_index || 0];
          const videoUrl = currentVersion?.video_url;
          const keyframeUrl = currentVersion?.keyframe_url || video.keyframe_url;
          const shotNumber = video.shot_number || idx + 1;
          const timestamp = timestamps[idx];
          const versions = video.versions || [];

          return (
            <Card key={idx} className="glass-bg p-2 flex flex-col items-center w-full h-fit" style={{ border: 'none', boxShadow: 'none' }}>
              {/* Shot标题 */}
              <div className="flex items-center justify-center mb-3 w-full">
                <span className="text-sm font-semibold">{t('shot')} {shotNumber}</span>
              </div>

              {/* 时间戳信息 */}
              {timestamp && timestamp.duration > 0 && (
                <div className="text-xs text-muted-foreground font-normal mb-3 w-full text-center">
                  {formatTime(timestamp.start)} - {formatTime(timestamp.end)} ({timestamp.duration}s)
                </div>
              )}

              {/* 所有版本展示 - 垂直排列 */}
              <div className="space-y-3 flex flex-col items-center w-full">
                {versions.length > 0 ? versions.map((version: any, vIdx: number) => {
                  const isCurrent = vIdx === (video.current_version_index || 0);
                  const versionVideoUrl = version.video_url;
                  const versionKeyframeUrl = version.keyframe_url;
                  const isSelected = selectedVersions.get(video.uuid) === version.uuid;

                  return (
                    <div key={vIdx} className="inline-flex flex-col items-center w-full">
                      {/* 版本选择圈圈 */}
                      <div className="flex items-center justify-between mb-2 w-full">
                        <div className="text-xs font-medium text-muted-foreground">
                          {t('version')} {vIdx + 1}
                        </div>
                        <div
                          className={`w-5 h-5 rounded-full border-2 cursor-pointer flex items-center justify-center transition-all duration-200 ${
                            isSelected
                              ? 'bg-blue-500 border-blue-500 shadow-lg'
                              : 'bg-white border-gray-300 hover:border-blue-400 hover:shadow-md'
                          }`}
                          onClick={() => {
                            if (onVersionSelection) {
                              onVersionSelection(video.uuid, version.uuid);
                            } else {
                              const newSelected = new Map(selectedVersions);
                              if (isSelected) {
                                newSelected.delete(video.uuid);
                              } else {
                                newSelected.set(video.uuid, version.uuid);
                              }
                              setInternalSelectedVersions(newSelected);
                            }
                          }}
                          title={isSelected ? t('selectedVersion') : t('selectVersion')}
                        >
                          {isSelected && (
                            <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                              <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                            </svg>
                          )}
                        </div>
                      </div>

                      {/* 版本视频 */}
                      <div 
                        data-image-container={`${shotNumber}-${vIdx}`}
                        className="inline-flex rounded-lg relative bg-black/5 items-center justify-center group overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] hover:scale-[1.03] hover:-translate-y-1" 
                        style={{ boxShadow: isCurrent ? '0 8px 16px -4px rgba(0, 0, 0, 0.2), 0 4px 8px -2px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.15)' : '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06), 0 0 0 1px rgba(0, 0, 0, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.1)' }}
                      >
                        {versionVideoUrl ? (
                          <div className="w-full overflow-hidden rounded-lg transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-110">
                          <VideoWithCleanup
                            className="block object-contain rounded-lg"
                            style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                            controls
                            poster={versionKeyframeUrl}
                          >
                            <source src={versionVideoUrl} type="video/mp4" />
                          </VideoWithCleanup>
                          </div>
                        ) : versionKeyframeUrl ? (
                          <div className="relative w-full overflow-hidden rounded-lg">
                            <img
                              src={versionKeyframeUrl}
                              alt={`${t('shot')} ${shotNumber} v${vIdx + 1} Keyframe`}
                              className="block object-contain cursor-pointer rounded-lg transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-110"
                              style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                              onClick={() => setPreviewImage({ url: versionKeyframeUrl, alt: `${t('shot')} ${shotNumber} v${vIdx + 1} Keyframe` })}
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

                        {/* 版本标签 - 左上角，hover时显示 */}
                        <div 
                          className={`absolute top-2 left-2 px-1 py-0.5 rounded text-xs font-semibold shadow-sm opacity-0 group-hover:opacity-100 transition-opacity ${
                            isCurrent
                              ? 'bg-gradient-to-r from-blue-500 to-purple-500 text-white'
                              : 'bg-gradient-to-r from-gray-400 to-gray-500 text-white'
                          }`}
                          style={{ fontSize: `${Math.max(5, 8 * zoomLevel)}px` }}
                        >
                          v{vIdx + 1}
                        </div>

                        {/* 状态标签 - 右上角，hover时显示 */}
                        {version.success === false ? (
                          <div 
                            className="absolute top-2 right-2 flex items-center gap-0.5 px-1 py-0.5 bg-red-50 border border-red-200 rounded text-xs shadow-sm opacity-0 group-hover:opacity-100 transition-opacity"
                            style={{ fontSize: `${Math.max(5, 8 * zoomLevel)}px` }}
                            title={version.error_msg || t('failed')}
                          >
                            <XCircle className="w-2 h-2 text-red-600" />
                            <span className="font-medium text-red-700">{t('failed')}</span>
                          </div>
                        ) : version.success === true && (
                          <div 
                            className="absolute top-2 right-2 flex items-center gap-0.5 px-1 py-0.5 bg-green-50 border border-green-200 rounded text-xs shadow-sm opacity-0 group-hover:opacity-100 transition-opacity"
                            style={{ fontSize: `${Math.max(5, 8 * zoomLevel)}px` }}
                          >
                            <CheckCircle className="w-2 h-2 text-green-600" />
                            <span className="font-medium text-green-700">{t('success')}</span>
                          </div>
                        )}
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

                      {/* 版本控制按钮 - 与 Lazy Shots 一致：再生 / 编辑 */}
                      <div className="flex items-center justify-center gap-2 mt-2 mb-2 w-full">
                        {(() => {
                          const versionKey = `${shotNumber}-${vIdx}`;
                          const isRegenerating = regenerating.has(versionKey);
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  size="sm"
                                  className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white dark:bg-card/90 dark:hover:bg-card shadow-sm flex-shrink-0"
                                  disabled={isRegenerating}
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    if (!onRegenerate) return;
                                    setRegenerating(prev => new Set(prev).add(versionKey));
                                    try {
                                      const promptKey = getPromptKey(shotNumber, vIdx);
                                      const editedPrompt = promptValues.get(promptKey);
                                      const promptToUse = editedPrompt || version.motion_prompt || '';
                                      await onRegenerate(shotNumber, promptToUse);
                                    } finally {
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
                        {(() => {
                          const promptKey = getPromptKey(shotNumber, vIdx);
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white dark:bg-card/90 dark:hover:bg-card shadow-sm"
                                  onClick={() => togglePromptExpanded(promptKey)}
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
                        const versionKey = `${shotNumber}-${vIdx}`;
                        const isRegenerating = regenerating.has(versionKey);
                        return isRegenerating && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t('regenerating')}</div>
                        );
                      })()}

                      {/* Motion Prompt展示和编辑（可无 motion_prompt，先写草稿） */}
                      {(() => {
                        const promptKey = getPromptKey(shotNumber, vIdx);
                        const isExpanded = expandedPrompts.has(promptKey);
                        const isEditing = editingPrompts.has(promptKey);
                        const currentPromptValue = promptValues.has(promptKey) ? (promptValues.get(promptKey) as string) : (version.motion_prompt || '');
                        
                        return (
                          <div 
                            data-prompt-container={promptKey}
                            ref={(el) => {
                              if (el && isExpanded) {
                                const imageContainer = el.closest('.inline-flex.flex-col')?.querySelector(`[data-image-container="${shotNumber}-${vIdx}"]`) as HTMLElement;
                                if (imageContainer) {
                                  const updateWidth = () => {
                                    if (el && imageContainer) {
                                      const parentWidth = el.parentElement?.clientWidth;
                                      const imageWidth = imageContainer.offsetWidth;
                                      const targetWidth = parentWidth ? Math.min(imageWidth, parentWidth) : imageWidth;
                                      el.style.width = `${targetWidth}px`;
                                    }
                                  };
                                  // 立即更新
                                  setTimeout(updateWidth, 0);
                                  // 等待下一帧再更新一次，确保图片已加载
                                  requestAnimationFrame(() => {
                                    requestAnimationFrame(updateWidth);
                                  });
                                }
                              }
                            }}
                            className="mt-1 flex justify-center" 
                            style={{ width: '100%' }}
                          >
                            {/* Prompt内容 - 展开时显示 */}
                            {isExpanded && (
                              <div className="bg-white dark:bg-card border border-gray-200 dark:border-border rounded-lg" style={{ width: '100%', maxWidth: '100%' }}>
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
                      {vIdx < versions.length - 1 && (
                        <div className="flex justify-center my-3">
                          <div className="w-0.5 h-6 border-l-2 border-dashed border-gray-400"></div>
                        </div>
                      )}
                    </div>
                  );
                }) : (
                  <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: '100%' }}>
                    <div className="w-full h-full rounded-lg bg-gray-100 dark:bg-gray-800 animate-pulse flex items-center justify-center">
                      <span className="text-xs text-gray-400">{t('loading') || 'Loading...'}</span>
                    </div>
                  </div>
                )}
              </div>
            </Card>
          );
        })}
        </div>
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
    </Card>
  );
};
