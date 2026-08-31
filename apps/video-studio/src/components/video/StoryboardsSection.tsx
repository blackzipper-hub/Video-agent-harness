import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Clock, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronUp, Check, X } from "lucide-react";
import { useState, useEffect, useCallback, useRef } from "react";
import { useLanguage } from "@/i18n/LanguageContext";
import {
  keyframeDisplayReactKey,
  resolveKeyframeDisplayMetrics,
} from "./keyframeDisplayUtils";

interface StoryboardsSectionProps {
  keyframesData: any;
  onRegenerate?: (shotNumber: number, prompt: string) => Promise<boolean>;
  onMentionClick?: (type: 'image' | 'prompt', keyframe: any) => void;
  onFullView?: () => void;
  zoomLevel?: number; // ✅ 统一的缩放级别
  totalCount?: number;
  /** 关键帧选中的版本（keyframe.uuid -> version.uuid），与视频版本选择样式一致 */
  selectedKeyframeVersions?: Map<string, string>;
  onKeyframeVersionSelection?: (keyframeUuid: string, versionUuid: string) => void;
}

export const StoryboardsSection = ({ keyframesData, onRegenerate, onMentionClick, onFullView, zoomLevel = 1, totalCount, selectedKeyframeVersions, onKeyframeVersionSelection }: StoryboardsSectionProps) => {
  const { t } = useLanguage();
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());
  const [expandedKeyframes, setExpandedKeyframes] = useState<Set<string>>(new Set()); // 多版本时折叠，与 Shots 一致
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
  
  // API: { keyframes, shot_total, total（首尾帧模式下 total≈2×shot_total）}
  const keyframes = keyframesData?.keyframes || [];
  const shotSlotTotal =
    Number.isFinite(keyframesData?.shot_total) && (keyframesData as any).shot_total > 0
      ? (keyframesData as any).shot_total
      : Number.isFinite(totalCount) && (totalCount as number) > 0
        ? totalCount
        : 0;
  const {
    displayKeyframes,
    expectedRecordTotal: expectedKeyframeRecordTotal,
  } = resolveKeyframeDisplayMetrics(keyframesData, totalCount);
  
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

  // 调试信息
  const generatedCount = keyframes.reduce((count: number, keyframe: any) => {
    const currentVersion = keyframe?.versions?.[keyframe?.current_version_index || 0];
    return currentVersion?.keyframe_url ? count + 1 : count;
  }, 0);
  
  console.log('🔍 StoryboardsSection Debug:', {
    keyframesCount: keyframes.length,
    shotSlotTotal,
    expectedKeyframeRecordTotal,
    displayCount: displayKeyframes.length,
    generatedCount,
    scaledCardWidth,
    scaledMediaHeight,
    totalWidth: displayKeyframes.length * (scaledCardWidth + 16)
  });
  
  if (displayKeyframes.length === 0) return null;

  return (
    <Card className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <Clock className="w-5 h-5 mr-2 text-accent-cyan" />
          {t('storyboardsSection')}
          <span className="ml-2 text-sm text-muted-foreground">
            ({generatedCount} / {expectedKeyframeRecordTotal})
          </span>
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
        </div>
      </div>
      
      {/* 平铺显示容器 - 横向滚动，滚动容器自身 pl-10，内层 flex 左对齐，避免第一个图被裁切 */}
      <div
        className="overflow-x-auto scrollbar-subtle pl-10"
        style={{ maxWidth: '100%' }}
      >
        <div
          className="flex gap-4 pb-4 pr-4 items-start"
          style={{
            minWidth: '100%'
          }}
        >
        {displayKeyframes.map((keyframe: any, idx: number) => {
          // 获取当前版本的图片URL
          const currentVersion = keyframe.versions && keyframe.versions[keyframe.current_version_index || 0];
          const imageUrl = currentVersion?.keyframe_url;
          const shotNumber = keyframe.shot_number || idx + 1;
          const versions = keyframe.versions || [];

          return (
            <Card key={keyframeDisplayReactKey(keyframe, idx)} className="glass-bg p-2 flex-shrink-0 flex flex-col items-center" style={{ border: 'none', boxShadow: 'none' }}>
              {/* Shot标题 */}
              <div className="flex items-center justify-center mb-3 w-full">
                <span className="text-sm font-semibold">{t('shot')} {shotNumber}</span>
              </div>

              {/* 所有版本展示 - 多版本时未展开只显示选中版本，与 Shots 一致 */}
              <div className="space-y-3 flex flex-col items-center w-full">
                {(() => {
                  const hasMultipleVersions = versions.length > 1;
                  const selectedVersionUuid = keyframe.uuid != null ? selectedKeyframeVersions?.get(keyframe.uuid) : undefined;
                  const isExpanded = keyframe.uuid != null && expandedKeyframes.has(keyframe.uuid);
                  const versionsToShow = hasMultipleVersions && selectedVersionUuid && !isExpanded
                    ? versions.filter((v: any) => v.uuid === selectedVersionUuid)
                    : versions;
                  return versionsToShow.length > 0 ? versionsToShow.map((version: any, showIdx: number) => {
                    const vIdx = versions.findIndex((v: any) => v.uuid === version.uuid);
                    const versionImageUrl = version.keyframe_url;
                    const isSelected = selectedVersionUuid === version.uuid;
                    const isCurrent = isSelected || (vIdx === (keyframe.current_version_index || 0) && !selectedKeyframeVersions?.has(keyframe.uuid));

                    return (
                      <div key={version.uuid || vIdx} className="inline-flex flex-col items-center w-full">
                        {/* 版本选择 - 仅展开时显示「保留此版本」，与 Shots 一致 */}
                        {onKeyframeVersionSelection && keyframe.uuid && hasMultipleVersions && isExpanded && (
                          <div className="flex items-center justify-between mb-2 w-full">
                            <div className="text-xs font-medium text-muted-foreground">
                              {t('version')} {vIdx + 1}
                            </div>
                            {isSelected ? (
                              <button
                                type="button"
                                className="px-2 py-1 text-xs font-medium text-white bg-purple-500 hover:bg-purple-600 rounded transition-all duration-200"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onKeyframeVersionSelection(keyframe.uuid, version.uuid);
                                  setExpandedKeyframes(prev => { const next = new Set(prev); next.delete(keyframe.uuid); return next; });
                                }}
                                title={t('selected')}
                              >
                                {t('keepThis')}
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded transition-all duration-200"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onKeyframeVersionSelection(keyframe.uuid, version.uuid);
                                  setExpandedKeyframes(prev => { const next = new Set(prev); next.delete(keyframe.uuid); return next; });
                                }}
                                title={t('selectVersion')}
                              >
                                {t('keepThis')}
                              </button>
                            )}
                          </div>
                        )}
                        <VersionItem version={version} vIdx={vIdx} shotNumber={shotNumber} idx={idx} isCurrent={isCurrent} versionImageUrl={versionImageUrl} scaledMediaHeight={scaledMediaHeight} scaledCardWidth={scaledCardWidth} zoomLevel={zoomLevel} t={t} setPreviewImage={setPreviewImage} expandedPrompts={expandedPrompts} editingPrompts={editingPrompts} promptValues={promptValues} regenerating={regenerating} onRegenerate={onRegenerate} togglePromptExpanded={togglePromptExpanded} startEditingPrompt={startEditingPrompt} cancelEditingPrompt={cancelEditingPrompt} savePromptEdit={savePromptEdit} adjustTextareaHeight={adjustTextareaHeight} getPromptKey={getPromptKey} setRegenerating={setRegenerating} setPromptValues={setPromptValues} />
                        {showIdx < versionsToShow.length - 1 && (
                          <div className="flex justify-center my-3">
                            <div className="w-0.5 h-6 border-l-2 border-dashed border-gray-400"></div>
                          </div>
                        )}
                      </div>
                    );
                  }) : (
                    <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: `${scaledCardWidth}px` }}>
                      <div className="w-full h-full rounded-lg bg-gray-100 dark:bg-gray-800 animate-pulse flex items-center justify-center">
                        <span className="text-xs text-gray-400">{t('loading') || 'Loading...'}</span>
                      </div>
                    </div>
                  );
                })()}
                {/* 版本数按钮 - 多版本时与 Shots 一致，点击展开/收起 */}
                {versions.length > 1 && (
                  <div className="w-full flex justify-end mt-2">
                    <button
                      type="button"
                      className="px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded transition-all duration-200 flex items-center gap-1"
                      onClick={() => {
                        if (keyframe.uuid == null) return;
                        setExpandedKeyframes(prev => {
                          const next = new Set(prev);
                          if (next.has(keyframe.uuid)) next.delete(keyframe.uuid);
                          else next.add(keyframe.uuid);
                          return next;
                        });
                      }}
                      title={keyframe.uuid != null && expandedKeyframes.has(keyframe.uuid) ? t('collapseVersions') : t('expandVersions')}
                    >
                      {t('versionsCount').replace('{count}', String(versions.length))}
                      {keyframe.uuid != null && expandedKeyframes.has(keyframe.uuid) ? (
                        <ChevronUp className="w-3 h-3" />
                      ) : (
                        <ChevronDown className="w-3 h-3" />
                      )}
                    </button>
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

// 单独的版本项组件，用于管理 ref
const VersionItem = ({ version, vIdx, shotNumber, idx, isCurrent, versionImageUrl, scaledMediaHeight, scaledCardWidth, zoomLevel, t, setPreviewImage, expandedPrompts, editingPrompts, promptValues, regenerating, onRegenerate, togglePromptExpanded, startEditingPrompt, cancelEditingPrompt, savePromptEdit, adjustTextareaHeight, getPromptKey, setRegenerating, setPromptValues }: any) => {
  const imageContainerRef = useRef<HTMLDivElement>(null);
  const promptContainerRef = useRef<HTMLDivElement>(null);
  const promptKey = getPromptKey(shotNumber, vIdx);
  const isExpanded = expandedPrompts.has(promptKey);
  
  // 当展开状态改变或缩放级别改变时，更新 prompt 宽度
  useEffect(() => {
    if (isExpanded) {
      const updateWidth = () => {
        // prompt 容器是条件渲染的，需要等待它渲染到 DOM 后再查找
        if (imageContainerRef.current && promptContainerRef.current) {
          const imageWidth = imageContainerRef.current.offsetWidth;
          if (imageWidth > 0) {
            const parentWidth = promptContainerRef.current.parentElement?.clientWidth;
            const targetWidth = parentWidth ? Math.min(imageWidth, parentWidth) : imageWidth;
            promptContainerRef.current.style.width = `${targetWidth}px`;
          }
        }
      };
      
      // 使用多个 requestAnimationFrame 确保在 DOM 完全更新后执行
      let rafId1: number | undefined;
      let rafId2: number | undefined;
      let rafId3: number | undefined;
      
      rafId1 = requestAnimationFrame(() => {
        rafId2 = requestAnimationFrame(() => {
          rafId3 = requestAnimationFrame(() => {
            updateWidth();
          });
        });
      });
      
      // 使用 setTimeout 作为备用方案
      const timeoutId = setTimeout(() => {
        updateWidth();
      }, 0);
      
      // 等待图片加载完成后再更新一次
      if (versionImageUrl) {
        const img = new Image();
        img.onload = () => {
          requestAnimationFrame(() => {
            updateWidth();
          });
        };
        img.src = versionImageUrl;
      }
      
      // 使用 ResizeObserver 监听图片容器宽度变化
      let resizeObserver: ResizeObserver | null = null;
      if (imageContainerRef.current) {
        resizeObserver = new ResizeObserver(() => {
          requestAnimationFrame(updateWidth);
        });
        resizeObserver.observe(imageContainerRef.current);
      }
      
      return () => {
        if (rafId1 !== undefined) cancelAnimationFrame(rafId1);
        if (rafId2 !== undefined) cancelAnimationFrame(rafId2);
        if (rafId3 !== undefined) cancelAnimationFrame(rafId3);
        clearTimeout(timeoutId);
        if (resizeObserver) {
          resizeObserver.disconnect();
        }
      };
    } else if (promptContainerRef.current) {
      // 收起时清除宽度限制
      promptContainerRef.current.style.width = '';
    }
  }, [isExpanded, zoomLevel, versionImageUrl]);
  
  return (
    <div className="inline-flex flex-col items-center w-full">
      {/* 版本图片 */}
      <div 
        ref={imageContainerRef}
        className="inline-flex rounded-lg relative bg-black/5 items-center justify-center group overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] hover:scale-[1.03] hover:-translate-y-1" 
        style={{ boxShadow: isCurrent ? '0 8px 16px -4px rgba(0, 0, 0, 0.2), 0 4px 8px -2px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.15)' : '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06), 0 0 0 1px rgba(0, 0, 0, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.1)' }}
      >
                        {versionImageUrl ? (
                          <img
                            src={versionImageUrl}
                            alt={`${t('shot')} ${shotNumber} v${vIdx + 1}`}
                            className="block object-contain cursor-pointer rounded-lg transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-110"
                            style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                            onClick={() => setPreviewImage({ url: versionImageUrl, alt: `${t('shot')} ${shotNumber} v${vIdx + 1}` })}
                          />
                        ) : version.success === false ? (
                          <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: `${scaledCardWidth}px` }}>
                            <span className="text-xs text-red-500">{t('failed')}</span>
                          </div>
                        ) : (
                          <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: `${scaledCardWidth}px` }}>
                            <div className="w-full h-full rounded-lg bg-gray-100 dark:bg-gray-800 animate-pulse flex items-center justify-center">
                              <span className="text-xs text-gray-400">{t('loading') || 'Loading...'}</span>
                            </div>
                          </div>
                        )}

                        {/* 版本标签 - 左上角，hover时显示 */}
                        <div className="absolute top-2 left-2 flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          {/* 版本号标签 */}
                          <div 
                            className={`px-1 py-0.5 rounded text-xs font-semibold shadow-sm ${
                            isCurrent
                              ? 'bg-gradient-to-r from-blue-500 to-purple-500 text-white'
                              : 'bg-gradient-to-r from-gray-400 to-gray-500 text-white'
                          }`}
                            style={{ fontSize: `${Math.max(5, 8 * zoomLevel)}px` }}
                          >
                            v{vIdx + 1}
                          </div>
                          
                          {/* 首尾帧标签 */}
                          {version.frame_index === 0 && (
                            <div 
                              className="px-1 py-0.5 rounded text-xs font-semibold shadow-sm bg-green-500 text-white"
                              style={{ fontSize: `${Math.max(5, 8 * zoomLevel)}px` }}
                            >
                              {t('startFrame')}
                            </div>
                          )}
                          {version.frame_index === -1 && (
                            <div 
                              className="px-1 py-0.5 rounded text-xs font-semibold shadow-sm bg-orange-500 text-white"
                              style={{ fontSize: `${Math.max(5, 8 * zoomLevel)}px` }}
                            >
                              {t('endFrame')}
                            </div>
                          )}
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

                      {/* 参考图（reference_image_urls）- 有则展示，不影响现有布局 */}
                      {(version.reference_image_urls && version.reference_image_urls.length > 0) && (
                        <div className="mt-2 w-full">
                          <span className="text-xs text-muted-foreground mr-1">{t('referenceImages') || '参考图'}:</span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {version.reference_image_urls.map((refUrl: string, refIdx: number) => (
                              <button
                                key={refIdx}
                                type="button"
                                className="rounded border border-gray-200 dark:border-border overflow-hidden hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-primary"
                                style={{ width: 48, height: 48 }}
                                onClick={() => setPreviewImage({ url: refUrl, alt: `参考图 ${refIdx + 1}` })}
                              >
                                <img src={refUrl} alt="" className="w-full h-full object-cover" />
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* 版本控制按钮 - 下拉（左）和 Regenerate（右） */}
                      <div className="flex items-center justify-between mt-2 mb-2 w-full">
                        {/* 下拉按钮 - 左边 */}
                        {version.t2i_prompt && (() => {
                          const promptKey = getPromptKey(shotNumber, vIdx);
                          const isExpanded = expandedPrompts.has(promptKey);
                          
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
                        
                        {/* Regenerate按钮 - 右边 */}
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

                                    // 设置loading状态
                                    setRegenerating(prev => new Set(prev).add(versionKey));

                                    try {
                                      // 获取用户编辑的 prompt，如果没有则使用原始 prompt
                                      const promptKey = getPromptKey(shotNumber, vIdx);
                                      const editedPrompt = promptValues.get(promptKey);
                                      const promptToUse = editedPrompt || version.t2i_prompt || '';
                                      
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
                      </div>

                      {/* Regenerating 状态提示 */}
                      {(() => {
                        const versionKey = `${shotNumber}-${vIdx}`;
                        const isRegenerating = regenerating.has(versionKey);
                        return isRegenerating && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t('regenerating')}</div>
                        );
                      })()}

                      {/* Prompt展示和编辑 */}
                      {version.t2i_prompt && (() => {
                        const promptKey = getPromptKey(shotNumber, vIdx);
                        const isExpanded = expandedPrompts.has(promptKey);
                        const isEditing = editingPrompts.has(promptKey);
                        const currentPromptValue = promptValues.has(promptKey) ? promptValues.get(promptKey) : version.t2i_prompt;
                        
                        return (
                          <div ref={promptContainerRef} className="mt-1 flex justify-center" style={{ width: '100%' }}>
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
                                      placeholder={t('enterPrompt')}
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
                                    className="px-4 py-4 text-sm text-gray-700 dark:text-card-foreground leading-relaxed cursor-pointer hover:bg-gray-50 dark:hover:bg-accent transition-colors break-words"
                                    onClick={() => startEditingPrompt(promptKey, currentPromptValue)}
                                  >
                                    {currentPromptValue}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}

                    </div>
  );
};
