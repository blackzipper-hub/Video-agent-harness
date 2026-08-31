import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { VideoWithCleanup } from "@/components/ui/VideoWithCleanup";
import { Film, Play, XCircle, Video, Loader2, ChevronDown, ChevronUp, Mic, Music, Eye, Clock } from "lucide-react";
import { useState, useEffect, useCallback, useRef } from "react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { videoSegmentsApi } from "@/services/api";
import { buildSegmentsVideoAssemblyRequestBody } from "@/lib/buildVideoAssemblyRequest";
import { postVideoAssembly } from "@/services/cutiVideoAssembly";
import { useLazyLoading } from "@/hooks/useLazyLoading";

interface VideoSegment {
  uuid: string;
  segment_number: number;
  video_generation_ids: string[];
  narration_ids: string[];
  keyframe_ids: string[];
  scene_ids: string[];
  storyboard_detail_ids: string[];
  music_generation_id: string;
  lipsync_id?: string;
  current_version_index: number;
  versions: VideoSegmentVersion[];
  created_at: string;
  updated_at: string;
}

interface VideoSegmentVersion {
  uuid: string;
  version_number: number;
  segment_number: number;
  video_url: string;
  lipsync_video_url?: string;  // 新增：lipsync 后的视频 URL
  success: boolean;
  error_msg?: string;
  duration?: number;
  fps?: number;
  lipsync_version_id?: string;
  music_generation_version_id?: string;
  created_at: string;
  updated_at: string;
}

interface VideoSegmentsSectionProps {
  conversationId?: string;
  threadId?: string;
  runId?: string;
  onLipsyncGenerate?: (segmentUuid: string) => Promise<boolean>;
  onLipsyncSuccess?: () => void | Promise<void>;  // lipsync 按钮成功后回调（用于刷新最终视频等）
  lipsyncCompleted?: boolean;  // 新增：当 lipsync 完成时触发刷新
  refreshTrigger?: number;  // 新增：当此值变化时触发刷新
  onVideoAssembled?: (assemblyUuid: string) => void | Promise<void>;  // 视频合成完成回调，传入 assembly_uuid 供父组件刷新 getVideoAssemblyData
  zoomLevel?: number; // ✅ 统一的缩放级别
}

export const VideoSegmentsSection = ({ 
  conversationId, 
  threadId, 
  runId, 
  onLipsyncGenerate,
  onLipsyncSuccess,
  lipsyncCompleted,
  refreshTrigger,
  onVideoAssembled,
  zoomLevel = 1
}: VideoSegmentsSectionProps) => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [allSegments, setAllSegments] = useState<VideoSegment[]>([]);
  const [loading, setLoading] = useState(false);
  const [lipsyncGenerating, setLipsyncGenerating] = useState<Set<string>>(new Set());
  const [assembling, setAssembling] = useState(false);
  const [selectedVersions, setSelectedVersions] = useState<Map<string, string>>(new Map());
  const [expandedSegments, setExpandedSegments] = useState<Set<string>>(new Set()); // 跟踪哪些片段已展开
  
  const baseCardWidth = 528;
  const baseMediaHeight = 288;
  const scaledCardWidth = baseCardWidth * zoomLevel;
  const scaledMediaHeight = baseMediaHeight * zoomLevel;

  // 使用懒加载 Hook（客户端懒加载）
  const {
    visibleItems: visibleSegments,
    isLoading: lazyLoading,
    hasMore,
    loadMore,
    scrollRef,
    currentPage
  } = useLazyLoading(allSegments, {
    itemsPerPage: 4, // 每次加载4个片段
    threshold: 300,
    preloadPages: 1,
    initialPages: 1
  });

  // 加载所有视频片段数据
  const loadVideoSegments = useCallback(async () => {
    if (!runId) return;

    setLoading(true);
    
    try {
      // 获取所有数据（不分页）
      const result = await videoSegmentsApi.getVideoSegmentsByRunId(runId, 1000, 0);
      const segments = result.data.segments || [];
      
      setAllSegments(segments);
      
      // 初始化选中状态 - 默认选中当前版本
      if (segments.length > 0) {
        const initialSelected = new Map<string, string>();
        segments.forEach((segment: VideoSegment) => {
          if (segment.versions && segment.versions.length > 0) {
            const currentIndex = segment.current_version_index || 0;
            const currentVersion = segment.versions[currentIndex];
            if (currentVersion) {
              initialSelected.set(segment.uuid, currentVersion.uuid);
            }
          }
        });
        setSelectedVersions(initialSelected);
      }
    } catch (error) {
      console.error('Failed to load video segments:', error);
      toast({
        title: t('errorOccurredGeneral'),
        description: `Failed to load video segments: ${error.message}`,
        variant: "destructive"
      });
    } finally {
      setLoading(false);
    }
  }, [runId, t, toast]);

  // 视频合成
  const handleVideoAssembly = async () => {
    if (!threadId || allSegments.length === 0) {
      toast({
        title: t('error'),
        description: !threadId ? t("errorOccurredGeneral") : t('noSegmentsAvailable'),
        variant: "destructive"
      });
      return;
    }

    setAssembling(true);
    try {
      const pairList: { segmentUuid: string; versionUuid: string }[] = [];
      allSegments.forEach(segment => {
        // 优先使用用户选择的版本
        const selectedVersionUuid = selectedVersions.get(segment.uuid);
        let targetVersion = null;
        
        if (selectedVersionUuid) {
          // 找到用户选择的版本
          targetVersion = segment.versions.find(v => v.uuid === selectedVersionUuid);
        }
        
        // 如果没找到选择的版本，使用当前版本
        if (!targetVersion) {
          targetVersion = segment.versions[segment.current_version_index || 0];
        }
        
        if (targetVersion) {
          pairList.push({
            segmentUuid: segment.uuid,
            versionUuid: targetVersion.uuid
          });
        }
      });
      const body = buildSegmentsVideoAssemblyRequestBody(threadId, pairList);
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
      } else {
        throw new Error(result?.message || t("assembleVideoFailed"));
      }
    } catch (error) {
      console.error('Video assembly failed:', error);
      toast({
        title: t('assembleVideoFailed'),
        description: error instanceof Error ? error.message : String(error),
        variant: "destructive"
      });
    } finally {
      setAssembling(false);
    }
  };

  // 生成 Lipsync
  const handleLipsyncGenerate = async (segmentUuid: string) => {
    if (!onLipsyncGenerate) {
      toast({
        title: "Error",
        description: "Lipsync generation not available",
        variant: "destructive"
      });
      return;
    }

    setLipsyncGenerating(prev => new Set(prev).add(segmentUuid));
    try {
      const success = await onLipsyncGenerate(segmentUuid);
      if (success) {
        toast({
          title: t('success'),
          description: "Lipsync generation started successfully",
        });
        // 重新加载数据
        await loadVideoSegments();
        // 刷新最终视频（lipsync 成功后可能更新了 assembly）
        await onLipsyncSuccess?.();
      } else {
        throw new Error("Lipsync generation failed");
      }
    } catch (error) {
      console.error('Lipsync generation failed:', error);
      toast({
        title: t('errorOccurredGeneral'),
        description: `Lipsync generation failed: ${error.message}`,
        variant: "destructive"
      });
    } finally {
      setLipsyncGenerating(prev => {
        const next = new Set(prev);
        next.delete(segmentUuid);
        return next;
      });
    }
  };

  // 格式化时间
  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  // 计算累计时间戳
  const calculateTimestamps = () => {
    let cumulativeTime = 0;
    return visibleSegments.map((segment) => {
      const currentVersion = segment.versions && segment.versions[segment.current_version_index || 0];
      const duration = currentVersion?.duration || 0;
      const start = cumulativeTime;
      const end = cumulativeTime + duration;
      cumulativeTime = end;
      return { start, end, duration };
    });
  };

  const timestamps = calculateTimestamps();

  // 初始加载
  useEffect(() => {
    loadVideoSegments();
  }, [loadVideoSegments]);

  // 当 lipsync 完成时自动刷新
  useEffect(() => {
    if (lipsyncCompleted) {
      console.log('🎭 Lipsync completed, refreshing video segments...');
      loadVideoSegments();
    }
  }, [lipsyncCompleted, loadVideoSegments]);

  // 当 refreshTrigger 变化时自动刷新
  useEffect(() => {
    if (refreshTrigger && refreshTrigger > 0) {
      console.log('🔄 Refresh trigger changed, refreshing video segments...');
      setAllSegments([]);
      loadVideoSegments();
    }
  }, [refreshTrigger, loadVideoSegments]);

  // 只在初始加载时显示 loading
  if (loading && allSegments.length === 0 && !runId) {
    return (
      <Card className="glass p-6">
        <div className="flex items-center justify-center h-32">
          <Loader2 className="w-6 h-6 animate-spin mr-2" />
          <span>{t('loadingVideoSegments') || 'Loading video segments...'}</span>
        </div>
      </Card>
    );
  }

  // 如果没有 runId，不显示
  if (!runId) return null;

  return (
    <Card className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <Music className="w-5 h-5 mr-2 text-accent-purple" />
          {t('lyricsSyncTitle') || 'Lyrics Sync'}
          <span className="ml-2 text-muted-foreground">
            ({visibleSegments.length} / {allSegments.length})
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <Button
            onClick={() => {
              setAllSegments([]);
              loadVideoSegments();
            }}
            disabled={loading}
            variant="outline"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('refreshing') || 'Refreshing'}
              </>
            ) : (
              <>
                <Eye className="w-4 h-4 mr-2" />
                {t('refresh') || 'Refresh'}
              </>
            )}
          </Button>
        </div>
      </div>

      {/* 横向滚动容器 */}
      <div
        ref={scrollRef}
        className="overflow-x-auto scrollbar-subtle"
        style={{ maxWidth: '100%' }}
      >
        <div
          className="flex gap-4 pb-4 pr-4"
          style={{
            width: `${visibleSegments.length * (scaledCardWidth + 16)}px`,
            minWidth: '100%'
          }}
        >
          {visibleSegments.length === 0 && !loading ? (
            <div className="w-full flex items-center justify-center py-12">
              <div className="text-center text-muted-foreground">
                <Music className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p className="text-sm">{t('noLyricsSyncYet') || 'No lyrics sync yet'}</p>
                <p className="text-xs mt-1">{t('clickUpdateVideoInShots') || 'Click "Merge Video" in the Shots section to create segments'}</p>
              </div>
            </div>
          ) : (
            visibleSegments.map((segment: VideoSegment, idx: number) => {
            const currentVersion = segment.versions && segment.versions[segment.current_version_index || 0];
            // 优先使用 lipsync 视频 URL，如果没有则使用原始视频 URL
            const videoUrl = currentVersion?.lipsync_video_url || currentVersion?.video_url;
            const hasLipsync = !!currentVersion?.lipsync_video_url;  // 通过是否有 lipsync_video_url 判断
            const segmentNumber = segment.segment_number;
            const timestamp = timestamps[idx];
            const isLipsyncGenerating = lipsyncGenerating.has(segment.uuid);

            return (
              <Card key={segment.uuid} className="glass-bg p-2 flex-shrink-0 !border-0 shadow-none flex flex-col items-center">
                {/* 时间戳信息 */}
                {timestamp && timestamp.duration > 0 && (
                  <div className="text-xs text-muted-foreground font-normal mb-3 w-full text-center">
                    {formatTime(timestamp.start)} - {formatTime(timestamp.end)} ({timestamp.duration}s)
                  </div>
                )}

                {/* 所有版本展示 - 垂直排列 */}
                <div className="space-y-3 flex flex-col items-center w-full">
                  {(() => {
                    const hasMultipleVersions = segment.versions && segment.versions.length > 1;
                    const selectedVersionUuid = selectedVersions.get(segment.uuid);
                    const isExpanded = expandedSegments.has(segment.uuid);
                    
                    // 如果有多个版本且已选中但未展开，只显示选中的版本
                    const versionsToShow = hasMultipleVersions && selectedVersionUuid && !isExpanded
                      ? segment.versions.filter((v: VideoSegmentVersion) => v.uuid === selectedVersionUuid)
                      : segment.versions || [];

                    return (
                      <>
                        {versionsToShow.map((version: VideoSegmentVersion, vIdx: number) => {
                          const originalIndex = segment.versions.findIndex((v: VideoSegmentVersion) => v.uuid === version.uuid);
                          const isCurrent = originalIndex === (segment.current_version_index || 0);
                          const versionVideoUrl = version.lipsync_video_url || version.video_url;
                          const hasLipsync = !!version.lipsync_video_url;
                          const isSelected = selectedVersionUuid === version.uuid;

                          return (
                            <div key={version.uuid || vIdx} className="inline-flex flex-col items-center w-full">
                              {/* 版本选择按钮和标题 - 只有多个版本且展开时才显示 */}
                              {hasMultipleVersions && isExpanded && (
                                <div className="flex items-center justify-between mb-2 w-full">
                                  <div className="text-xs font-medium text-muted-foreground">
                                    {t('versionLabel', { version: originalIndex + 1 })}
                                  </div>
                                  <div className="flex items-center gap-2">
                                    {hasLipsync && (
                                      <div className="flex items-center gap-1 px-1 py-0.5 bg-green-100 text-green-700 rounded text-xs">
                                        <Mic className="w-3 h-3" />
                                      </div>
                                    )}
                                    {/* Keep this 按钮 - 只有多个版本时才显示 */}
                                    {isSelected ? (
                                      <button
                                        className="px-2 py-1 text-xs font-medium text-white bg-purple-500 hover:bg-purple-600 rounded transition-all duration-200"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          const newSelected = new Map(selectedVersions);
                                          newSelected.set(segment.uuid, version.uuid);
                                          setSelectedVersions(newSelected);
                                          // 点击选中后折叠
                                          setExpandedSegments(prev => {
                                            const next = new Set(prev);
                                            next.delete(segment.uuid);
                                            return next;
                                          });
                                        }}
                                        title={t('selected')}
                                      >
                                        {t('keepThis')}
                                      </button>
                                    ) : (
                                      <button
                                        className="px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded transition-all duration-200"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          const newSelected = new Map(selectedVersions);
                                          newSelected.set(segment.uuid, version.uuid);
                                          setSelectedVersions(newSelected);
                                          // 点击选中后折叠
                                          setExpandedSegments(prev => {
                                            const next = new Set(prev);
                                            next.delete(segment.uuid);
                                            return next;
                                          });
                                        }}
                                        title={t('selectVersion')}
                                      >
                                        {t('keepThis')}
                                      </button>
                                    )}
                                  </div>
                                </div>
                              )}

                        {/* 版本视频 */}
                        <div className="inline-flex rounded-lg relative bg-black/5 items-center justify-center group" style={{ boxShadow: isCurrent ? '0 8px 16px -4px rgba(0, 0, 0, 0.2), 0 4px 8px -2px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.15)' : '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06), 0 0 0 1px rgba(0, 0, 0, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.1)' }}>
                          {/* ID序号 - 左上角，hover时显示 */}
                          <div className="absolute top-2 left-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                            <div 
                              className="px-1.5 py-0.5 rounded text-xs font-normal bg-black/30 text-white/70 backdrop-blur-sm"
                              style={{ fontSize: `${Math.max(4, 7 * zoomLevel)}px` }}
                            >
                              {segmentNumber}
                            </div>
                          </div>
                          
                          {versionVideoUrl ? (
                            <VideoWithCleanup
                              className="block object-contain rounded-lg"
                              style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                              controls
                              preload="metadata"
                              autoPlay
                              loop
                              muted
                            >
                              <source src={versionVideoUrl} type="video/mp4" />
                            </VideoWithCleanup>
                          ) : (
                            <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: `${scaledCardWidth}px` }}>
                              <Play className="w-8 h-8 text-gray-400" />
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

                        {/* Lipsync按钮 */}
                        {!hasLipsync && onLipsyncGenerate && isCurrent && (
                          <Button
                            size="sm"
                            onClick={() => handleLipsyncGenerate(segment.uuid)}
                            disabled={isLipsyncGenerating}
                            className="w-full mt-2 bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white h-7 text-xs"
                          >
                            {isLipsyncGenerating ? (
                              <>
                                <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                                Generating
                              </>
                            ) : (
                              <>
                                <Mic className="w-3 h-3 mr-1" />
                                Lipsync
                              </>
                            )}
                          </Button>
                        )}

                              {/* 虚线连接到下一个版本 */}
                              {originalIndex < segment.versions.length - 1 && isExpanded && (
                                <div className="flex justify-center my-3">
                                  <div className="w-0.5 h-6 border-l-2 border-dashed border-gray-400"></div>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </>
                    );
                  })()}
                  
                  {/* 版本数按钮 - 版本数大于1时显示在视频下方右边 */}
                  {segment.versions && segment.versions.length > 1 && (() => {
                    const isExpanded = expandedSegments.has(segment.uuid);
                    return (
                      <div className="w-full flex justify-end mt-2">
                        <button
                          className="px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded transition-all duration-200 flex items-center gap-1"
                          onClick={() => {
                            setExpandedSegments(prev => {
                              const next = new Set(prev);
                              if (next.has(segment.uuid)) {
                                next.delete(segment.uuid);
                              } else {
                                next.add(segment.uuid);
                              }
                              return next;
                            });
                          }}
                          title={isExpanded ? t('collapseVersions') : t('expandVersions')}
                        >
                          {t('versionsCount', { count: segment.versions.length })}
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
          })
          )}

          {/* 懒加载指示器 */}
          {lazyLoading && (
            <div 
              className="flex-shrink-0 flex items-center justify-center bg-card/30 rounded-lg border-2 border-dashed border-gray-300"
              style={{ width: scaledCardWidth, height: scaledMediaHeight + 120 }}
            >
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="w-6 h-6 animate-spin" />
                <span className="text-sm">{t('loadingSegments') || 'Loading segments...'}</span>
              </div>
            </div>
          )}

        </div>
      </div>

      {/* Assemble Video 按钮 */}
      {allSegments.length > 0 && (
        <div className="mt-4 flex justify-center">
          <Button
            onClick={handleVideoAssembly}
            disabled={assembling || loading}
            className="bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white font-medium px-6 py-2 rounded-lg shadow-lg transition-all duration-200"
          >
            {assembling ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {t('assembling')}
              </>
            ) : (
              <>
                <Film className="mr-2 h-4 w-4" />
                {t('assembleVideo')}
              </>
            )}
          </Button>
        </div>
      )}
    </Card>
  );
};
