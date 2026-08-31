import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { VideoWithCleanup } from "@/components/ui/VideoWithCleanup";
import { Mic, Play, CheckCircle, XCircle, Loader2, ZoomIn, ZoomOut, ChevronDown, Clock, Volume2, RefreshCw } from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { useLazyLoading } from "@/hooks/useLazyLoading";
import { videoSegmentsApi } from "@/services/api";

interface LipsyncVersion {
  uuid: string;
  version_number: number;
  segment_number: number;
  video_url: string;
  audio_url: string;
  original_video_url: string;
  provider: string;
  model: string;
  success: boolean;
  error_msg?: string;
  duration?: number;
  created_at: string;
  updated_at: string;
}

interface LipsyncGeneration {
  uuid: string;
  video_segment_id: string;
  music_generation_id: string;
  segment_number: number;
  current_version_index: number;
  versions: LipsyncVersion[];
  created_at: string;
  updated_at: string;
}

interface LipsyncSectionProps {
  conversationId?: string;
  threadId?: string;
  runId?: string;
  lipsyncUuids?: string[];
  onRegenerate?: (lipsyncUuid: string) => Promise<boolean>;
}

export const LipsyncSection = ({ 
  conversationId, 
  threadId, 
  runId, 
  lipsyncUuids = [],
  onRegenerate 
}: LipsyncSectionProps) => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [lipsyncData, setLipsyncData] = useState<LipsyncGeneration[]>([]);
  const [loading, setLoading] = useState(false);
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());
  const [zoomLevel, setZoomLevel] = useState(1);
  
  const baseCardWidth = 300;
  const baseMediaHeight = 180;
  const scaledCardWidth = baseCardWidth * zoomLevel;
  const scaledMediaHeight = baseMediaHeight * zoomLevel;

  // 使用懒加载 Hook
  const {
    visibleItems: visibleLipsyncs,
    isLoading: lazyLoading,
    hasMore,
    loadMore,
    scrollRef,
    currentPage
  } = useLazyLoading(lipsyncData, {
    itemsPerPage: 3,
    threshold: 300,
    preloadPages: 1,
    initialPages: 1
  });

  // 加载 Lipsync 数据
  const loadLipsyncData = useCallback(async () => {
    if (!lipsyncUuids || lipsyncUuids.length === 0) return;

    setLoading(true);
    try {
      const lipsyncPromises = lipsyncUuids.map(async (uuid) => {
        try {
          const result = await videoSegmentsApi.getLipsyncGeneration(uuid);
          return result.data;
        } catch (error) {
          console.warn(`Failed to load lipsync ${uuid}:`, error);
          return null;
        }
      });

      const results = await Promise.all(lipsyncPromises);
      const validResults = results.filter(Boolean) as LipsyncGeneration[];
      
      // 按 segment_number 排序
      validResults.sort((a, b) => a.segment_number - b.segment_number);
      
      setLipsyncData(validResults);
    } catch (error) {
      console.error('Failed to load lipsync data:', error);
      toast({
        title: t('errorOccurredGeneral'),
        description: t('lipsyncLoadFailed').replace('{message}', String((error as any)?.message || '')),
        variant: "destructive"
      });
    } finally {
      setLoading(false);
    }
  }, [lipsyncUuids, t, toast]);

  // 重新生成 Lipsync
  const handleRegenerate = async (lipsyncUuid: string) => {
    if (!onRegenerate) {
      toast({
        title: t('errorOccurredGeneral'),
        description: t('lipsyncRegenerateNotAvailable'),
        variant: "destructive"
      });
      return;
    }

    setRegenerating(prev => new Set(prev).add(lipsyncUuid));
    try {
      const success = await onRegenerate(lipsyncUuid);
      if (success) {
        toast({
          title: t('success'),
          description: t('lipsyncRegenerateStarted'),
        });
        // 重新加载数据
        await loadLipsyncData();
      } else {
        throw new Error("Lipsync regeneration failed");
      }
    } catch (error: any) {
      console.error('Lipsync regeneration failed:', error);
      toast({
        title: t('errorOccurredGeneral'),
        description: t('lipsyncRegenerateFailed').replace('{message}', String(error?.message || '')),
        variant: "destructive"
      });
    } finally {
      setRegenerating(prev => {
        const next = new Set(prev);
        next.delete(lipsyncUuid);
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

  // 当 lipsyncUuids 变化时，清空旧数据并重新加载
  useEffect(() => {
    if (lipsyncUuids && lipsyncUuids.length > 0) {
      // 清空旧数据，避免显示其他对话的数据
      setLipsyncData([]);
      loadLipsyncData();
    } else {
      // 如果没有 lipsyncUuids，清空数据
      setLipsyncData([]);
    }
  }, [lipsyncUuids, loadLipsyncData]);

  if (loading && lipsyncData.length === 0) {
    return (
      <Card className="glass p-6">
        <div className="flex items-center justify-center h-32">
          <Loader2 className="w-6 h-6 animate-spin mr-2" />
          <span>{t('loadingLipsyncData') || 'Loading lipsync data...'}</span>
        </div>
      </Card>
    );
  }

  if (lipsyncData.length === 0) return null;

  return (
    <Card className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <Mic className="w-5 h-5 mr-2 text-accent-purple" />
          {t('lipsyncGenerations')}
          <span className="ml-2 text-sm text-muted-foreground">
            ({visibleLipsyncs.length} / {lipsyncData.length})
          </span>
        </h3>
        <div className="flex items-center gap-2">
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
            onClick={loadLipsyncData}
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
                <span className="text-xl mr-2" style={{ filter: 'drop-shadow(2px 2px 4px rgba(220, 38, 38, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))', textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)' }}>🎲</span>
                {t('refresh')}
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
            width: `${visibleLipsyncs.length * (scaledCardWidth + 16)}px`,
            minWidth: '100%'
          }}
        >
          {visibleLipsyncs.map((lipsync: LipsyncGeneration) => {
            const currentVersion = lipsync.versions && lipsync.versions[lipsync.current_version_index || 0];
            const lipsyncVideoUrl = currentVersion?.video_url;
            const originalVideoUrl = currentVersion?.original_video_url;
            const audioUrl = currentVersion?.audio_url;
            const segmentNumber = lipsync.segment_number;
            const isRegenerating = regenerating.has(lipsync.uuid);

            return (
              <Card key={lipsync.uuid} className="glass-bg p-3 flex-shrink-0" style={{ width: scaledCardWidth }}>
                {/* 片段标题 */}
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-semibold">{t('segmentLipsyncTitle').replace('{segment}', String(segmentNumber))}</span>
                  <div className="flex items-center gap-1 px-2 py-1 bg-purple-100 text-purple-700 rounded-full text-xs">
                    <Mic className="w-3 h-3" />
                    {t('lipsyncLabel')}
                  </div>
                </div>

                {/* 时长信息 */}
                {currentVersion?.duration && (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground mb-3">
                    <Clock className="w-3 h-3" />
                    {t('duration')}: {formatTime(currentVersion.duration)}
                  </div>
                )}

                {/* 视频对比 - 原始 vs Lipsync */}
                <div className="space-y-3 mb-3">
                  {/* Lipsync 视频 */}
                  <div>
                    <div className="text-xs font-medium text-muted-foreground mb-1">{t('lipsyncResult')}</div>
                    <div className="rounded-lg overflow-hidden relative bg-black/5 flex items-center justify-center border-2 border-purple-200" style={{ height: scaledMediaHeight }}>
                      {lipsyncVideoUrl ? (
                        <VideoWithCleanup
                          className="max-w-full max-h-full object-contain"
                          controls
                          preload="metadata"
                        >
                          <source src={lipsyncVideoUrl} type="video/mp4" />
                        </VideoWithCleanup>
                      ) : (
                        <div className="flex items-center justify-center w-full h-full">
                          <Play className="w-8 h-8 text-gray-400" />
                        </div>
                      )}

                      {/* 状态标签 */}
                      {currentVersion?.success === false ? (
                        <div className="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 bg-red-50 border border-red-200 rounded text-xs">
                          <XCircle className="w-3 h-3 text-red-600" />
                          <span className="font-medium text-red-700">{t('failed')}</span>
                        </div>
                      ) : currentVersion?.success === true && (
                        <div className="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 bg-green-50 border border-green-200 rounded text-xs">
                          <CheckCircle className="w-3 h-3 text-green-600" />
                          <span className="font-medium text-green-700">{t('success')}</span>
                        </div>
                      )}

                      {/* 版本信息 */}
                      <div className="absolute top-2 left-2 px-2 py-1 bg-purple-500 text-white rounded text-xs font-semibold">
                        v{(currentVersion?.version_number || 1)}
                      </div>
                    </div>
                  </div>

                  {/* 原始视频 - 较小尺寸 */}
                  {originalVideoUrl && (
                    <div>
                      <div className="text-xs font-medium text-muted-foreground mb-1">{t('originalVideo')}</div>
                      <div className="rounded-lg overflow-hidden relative bg-black/5 flex items-center justify-center border border-gray-200" style={{ height: scaledMediaHeight * 0.6 }}>
                        <VideoWithCleanup
                          className="max-w-full max-h-full object-contain"
                          controls
                          preload="metadata"
                        >
                          <source src={originalVideoUrl} type="video/mp4" />
                        </VideoWithCleanup>
                      </div>
                    </div>
                  )}
                </div>

                {/* 音频文件 */}
                {audioUrl && (
                  <div className="mb-3">
                    <div className="text-xs font-medium text-muted-foreground mb-1">{t('audioSource')}</div>
                    <div className="flex items-center gap-2 p-2 bg-blue-50 rounded-lg border border-blue-200">
                      <Volume2 className="w-4 h-4 text-blue-600" />
                      <audio controls className="flex-1 h-8">
                        <source src={audioUrl} type="audio/mpeg" />
                      </audio>
                    </div>
                  </div>
                )}

                {/* 版本列表 */}
                {lipsync.versions && lipsync.versions.length > 1 && (
                  <div className="mb-3">
                    <div className="text-xs text-muted-foreground mb-2">
                      {t('versionsAvailable').replace('{count}', String(lipsync.versions.length))}
                    </div>
                    <div className="flex gap-1">
                      {lipsync.versions.map((version, vIdx) => {
                        const isCurrent = vIdx === (lipsync.current_version_index || 0);
                        return (
                          <div
                            key={version.uuid}
                            className={`w-6 h-6 rounded border-2 flex items-center justify-center text-xs font-semibold cursor-pointer ${
                              isCurrent
                                ? 'bg-purple-500 border-purple-500 text-white'
                                : 'bg-white border-gray-300 text-gray-600 hover:border-purple-400'
                            }`}
                            title={`${t('version')} ${version.version_number} - ${version.success ? t('success') : t('failed')}`}
                          >
                            {version.version_number}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}


                {/* 重新生成按钮 */}
                {onRegenerate && (
                  <Button
                    size="sm"
                    onClick={() => handleRegenerate(lipsync.uuid)}
                    disabled={isRegenerating}
                    className="w-full bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white"
                  >
                    {isRegenerating ? (
                      <>
                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        {t('regenerating')}
                      </>
                    ) : (
                      <>
                        <span className="text-xl mr-2" style={{ filter: 'drop-shadow(2px 2px 4px rgba(220, 38, 38, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))', textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)' }}>🎲</span>
                        {t('regenerate')}
                      </>
                    )}
                  </Button>
                )}
              </Card>
            );
          })}

          {/* 懒加载指示器 */}
          {lazyLoading && (
            <div 
              className="flex-shrink-0 flex items-center justify-center bg-card/30 rounded-lg border-2 border-dashed border-gray-300"
              style={{ width: scaledCardWidth, height: scaledMediaHeight + 160 }}
            >
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="w-6 h-6 animate-spin" />
                <span className="text-sm">{t('loadingLipsync')}</span>
              </div>
            </div>
          )}

          {/* 加载更多按钮 */}
          {hasMore && !lazyLoading && (
            <div 
              className="flex-shrink-0 flex items-center justify-center bg-card/30 rounded-lg border-2 border-dashed border-gray-300 hover:border-primary/50 transition-colors cursor-pointer"
              style={{ width: scaledCardWidth, height: scaledMediaHeight + 160 }}
              onClick={loadMore}
            >
              <div className="flex flex-col items-center gap-2 text-muted-foreground hover:text-primary transition-colors">
                <ChevronDown className="w-6 h-6" />
              <span className="text-sm">{t('loadMore')}</span>
              <span className="text-xs">{t('remainingCount').replace('{count}', String(lipsyncData.length - visibleLipsyncs.length))}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 页面指示器 */}
      {lipsyncData.length > 3 && (
        <div className="flex items-center justify-center mt-4 gap-2 text-sm text-muted-foreground">
          <span>{t('pageIndicator').replace('{page}', String(currentPage))}</span>
          <span>•</span>
          <span>{t('lipsyncGenerationsCount').replace('{visible}', String(visibleLipsyncs.length)).replace('{total}', String(lipsyncData.length))}</span>
          {hasMore && (
            <>
              <span>•</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={loadMore}
                disabled={lazyLoading}
                className="h-6 px-2 text-xs"
              >
                {lazyLoading ? (t('loading') || 'Loading...') : t('loadMore')}
              </Button>
            </>
          )}
        </div>
      )}
    </Card>
  );
};
