import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Badge } from "@/components/ui/badge";
import { Clock, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronUp, Check, X, Loader2 } from "lucide-react";
import { useState, useEffect, useLayoutEffect, useCallback, useRef } from "react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useLazyLoading } from "@/hooks/useLazyLoading";
import { api } from "@/services/api";
import { toast } from "sonner";
import { VideoArtifactPromptEditorDialog } from "./VideoArtifactPromptEditorDialog";
import { cn } from "@/lib/utils";
import type { TranslationKey } from "@/i18n/translations";
import {
  keyframeDisplayReactKey,
  resolveKeyframeDisplayMetrics,
} from "./keyframeDisplayUtils";

function keyframeReflectionIssueTypeLabel(
  t: (key: TranslationKey) => string,
  issueType: string,
): string {
  return t(`video.keyframeReflection.issueType.${issueType}` as TranslationKey);
}

function keyframeReflectionSeverityLabel(
  t: (key: TranslationKey) => string,
  severity: string,
): string {
  return t(`video.keyframeReflection.severity.${severity}` as TranslationKey);
}

interface LazyStoryboardsSectionProps {
  keyframesData: any;
  onRegenerate?: (shotNumber: number, prompt: string, frameIndex?: number) => Promise<boolean>;
  /** 基于当前关键帧成图的 Seedream 编辑（与「编辑图片」入口一致） */
  onKeyframeImageEditWithInstruction?: (
    shotNumber: number,
    instruction: string,
    versionIndex: number,
    frameIndex?: number,
  ) => Promise<boolean>;
  /** AI Improve 成功后只刷新数据，不再次调用 regenerate API（避免重复请求） */
  onRefreshKeyframes?: () => Promise<void>;
  onMentionClick?: (type: 'image' | 'prompt', keyframe: any) => void;
  onFullView?: () => void;
  zoomLevel?: number; // ✅ 统一的缩放级别
  userOption?: any; // 用户选项，用于 AI improve
  threadId?: string; // 线程ID，用于 AI improve
  totalCount?: number;
  /** 关键帧选中的版本（keyframe.uuid -> version.uuid），与视频版本选择样式一致 */
  selectedKeyframeVersions?: Map<string, string>;
  onKeyframeVersionSelection?: (keyframeUuid: string, versionUuid: string) => void;
  /** instruction_merge_prompt：返回融合后的完整 t2i_prompt（与 paint-show PromptEditorDialog 的「编辑提示词」一致） */
  onKeyframeRefinePromptOnly?: (
    keyframeUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  /** 外部触发的 regenerate 槽位（与内部 `${shotNumber}-${vIdx}` 一致），用于 post-regenerate 等路径的 dice 转圈 */
  companionDiceKeys?: ReadonlySet<string>;
  /** 嵌入到上层模块时使用：隐藏自身外层卡片与标题 */
  embedded?: boolean;
  /** 外部同步的滚动位置（纵向） */
  syncedScrollTop?: number;
  /** 滚动时回传当前位置，供上层在 Tab 间同步 */
  onSyncedScrollTopChange?: (scrollTop: number) => void;
}

export const LazyStoryboardsSection = ({ keyframesData, onRegenerate, onKeyframeImageEditWithInstruction, onRefreshKeyframes, onMentionClick, onFullView, zoomLevel = 1, userOption, threadId, totalCount, selectedKeyframeVersions, onKeyframeVersionSelection, onKeyframeRefinePromptOnly, companionDiceKeys, embedded = false, syncedScrollTop, onSyncedScrollTopChange }: LazyStoryboardsSectionProps) => {
  const { t } = useLanguage();
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());
  const [expandedKeyframes, setExpandedKeyframes] = useState<Set<string>>(new Set()); // 多版本时折叠，与 Shots 一致
  const [editingPrompts, setEditingPrompts] = useState<Set<string>>(new Set());
  const [promptValues, setPromptValues] = useState<Map<string, string>>(new Map());
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string } | null>(null);
  const [expandedInstructionPanels, setExpandedInstructionPanels] = useState<Set<string>>(new Set());
  const [instructionDrafts, setInstructionDrafts] = useState<Map<string, string>>(new Map());
  const [fusionPromptEditor, setFusionPromptEditor] = useState<{
    shotNumber: number;
    versionIndex: number;
    frameIndex: number;
    statusKey: string;
    title: string;
    image: string;
    initialPrompt: string;
    keyframeUuid: string;
    versionUuid: string;
  } | null>(null);
  const [hoveredKeyframeVersionKey, setHoveredKeyframeVersionKey] = useState<string | null>(null);
  const pendingSyncedScrollTopRef = useRef<number | null>(null);
  const mediaContainerRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const promptContainerRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const instructionPanelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  
  const baseCardWidth = 528;
  const baseMediaHeight = 288;
  const scaledCardWidth = baseCardWidth * zoomLevel;
  const scaledMediaHeight = baseMediaHeight * zoomLevel;
  
  // API: { keyframes, shot_total: 镜头数, total: 期望关键帧根记录数（首尾帧模式下为 2×shot_total）}
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
  const lazyItemsPerPage = embedded ? Math.max(displayKeyframes.length, 1) : 6;
  
  // 使用懒加载 Hook
  const {
    visibleItems: visibleKeyframes,
    isLoading,
    scrollRef
  } = useLazyLoading(displayKeyframes, {
    itemsPerPage: lazyItemsPerPage, // 嵌入 Tab 时直接渲染全部，避免切换对齐时闪跳
    threshold: 300,
    preloadPages: 1,
    initialPages: 1,
    direction: 'vertical',
    autoLoadThrottleMs: 450,
    maxConsecutiveAutoLoads: 2
  });

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
  
  // Helper functions for prompt management
  const getPromptKey = (shotNumber: number, versionIndex: number) => `${shotNumber}-${versionIndex}`;
  
  // 展开 prompt 时同步宽度到上方 keyframe 图片容器，首次展开即正确换行（与 LazyShotsSection 一致）
  useLayoutEffect(() => {
    if (expandedPrompts.size === 0 && expandedInstructionPanels.size === 0) return;
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
      expandedInstructionPanels.forEach((instrKey) => {
        const m = /^(\d+)-(\d+)-(\d+)-instruction$/.exec(instrKey);
        if (!m) return;
        const mediaKey = `${m[1]}-${m[2]}`;
        const media = mediaContainerRefs.current[mediaKey];
        const panel = instructionPanelRefs.current[instrKey];
        if (media && panel && media.offsetWidth > 0) {
          const parentWidth = panel.parentElement?.clientWidth;
          const targetWidth = parentWidth ? Math.min(media.offsetWidth, parentWidth) : media.offsetWidth;
          panel.style.width = `${targetWidth}px`;
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
  }, [expandedPrompts, expandedInstructionPanels]);

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

  const toggleInstructionPanel = (instrKey: string) => {
    setExpandedInstructionPanels((prev) => {
      if (prev.has(instrKey)) return new Set();
      return new Set([instrKey]);
    });
  };

  const closeInstructionPanel = (instrKey: string) => {
    setExpandedInstructionPanels((prev) => {
      const next = new Set(prev);
      next.delete(instrKey);
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

  const generatedCount = keyframes.reduce((count: number, keyframe: any) => {
    const currentVersion = keyframe?.versions?.[keyframe?.current_version_index || 0];
    return currentVersion?.keyframe_url ? count + 1 : count;
  }, 0);
  
  // 调试信息
  console.log('🔍 LazyStoryboardsSection Debug:', {
    keyframesCount: keyframes.length,
    shotSlotTotal,
    expectedKeyframeRecordTotal,
    visibleCount: visibleKeyframes.length,
    generatedCount,
    scaledCardWidth,
    scaledMediaHeight,
    totalWidth: visibleKeyframes.length * (scaledCardWidth + 16)
  });
  
  // 在所有 Hook 调用之后进行条件判断
  if (displayKeyframes.length === 0) return null;

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
  }, [visibleKeyframes.length, tryApplySyncedScrollTop]);

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
              <Clock className="w-5 h-5 mr-2 text-accent-cyan" />
              {t('storyboardsSection')}
              <span className="ml-2 text-sm text-muted-foreground">
                ({generatedCount} / {expectedKeyframeRecordTotal})
              </span>
            </h3>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-xs">
            {t('storyboardsSectionTooltip')}
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
      
      {/* 平铺显示容器 - 固定高度网格，内部纵向滚动 */}
      <div
        ref={scrollRef}
        className="h-[640px] overflow-y-auto scrollbar-subtle pr-1"
        onScroll={(e) => {
          onSyncedScrollTopChange?.(e.currentTarget.scrollTop);
        }}
      >
        <div className="grid grid-cols-1 gap-4 pb-4 pr-2 md:grid-cols-2 xl:grid-cols-3">
        {visibleKeyframes.map((keyframe: any, idx: number) => {
          // 获取当前版本的图片URL
          const currentVersion = keyframe.versions && keyframe.versions[keyframe.current_version_index || 0];
          const imageUrl = currentVersion?.keyframe_url;
          const shotNumber = keyframe.shot_number || idx + 1;
          const versions = keyframe.versions || [];

          return (
            <Card
              key={keyframeDisplayReactKey(keyframe, idx)}
              className="glass-bg p-2 flex flex-col items-center group w-full h-fit"
              style={{ border: "none", boxShadow: "none" }}
            >
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
                    const kfVerHoverKey = `${keyframe?.uuid ?? shotNumber}-${version.uuid ?? vIdx}`;
                    const defaultKfTitle =
                      version.frame_index === -1
                        ? `${t('shot')} ${shotNumber} · ${t('endFrame')}`
                        : version.frame_index === 0
                          ? `${t('shot')} ${shotNumber} · ${t('startFrame')}`
                          : `${t('shot')} ${shotNumber}`;
                    return (
                    <div
                      key={version.uuid || vIdx}
                      className="inline-flex w-full flex-col items-center group"
                      onMouseEnter={() => setHoveredKeyframeVersionKey(kfVerHoverKey)}
                      onMouseLeave={() => setHoveredKeyframeVersionKey(null)}
                    >
                      <h3 className="mb-3 w-full min-h-[1.25rem] truncate px-1 text-center text-sm text-foreground/80 transition-colors group-hover:text-foreground">
                        {hoveredKeyframeVersionKey === kfVerHoverKey ? `${t('version')} ${vIdx + 1}` : defaultKfTitle}
                      </h3>
                      <div
                        className={cn(
                          "relative mx-auto w-fit max-w-full cursor-pointer overflow-hidden rounded-2xl transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] ring-2 ring-transparent",
                          isSelected && "ring-[hsl(var(--selection-ring))]",
                          hoveredKeyframeVersionKey === kfVerHoverKey
                            ? "scale-[1.03] -translate-y-1"
                            : "scale-100 translate-y-0",
                        )}
                        style={{
                          boxShadow:
                            hoveredKeyframeVersionKey === kfVerHoverKey
                              ? "var(--artifact-shadow-card-hover)"
                              : "var(--artifact-shadow-card)",
                        }}
                      >
                        <div
                          ref={(el) => {
                            mediaContainerRefs.current[getPromptKey(shotNumber, vIdx)] = el;
                          }}
                          className="relative inline-flex flex-shrink-0 items-center justify-center overflow-hidden rounded-xl bg-black/5"
                        >
                        {versionImageUrl ? (
                          <img
                            src={versionImageUrl}
                            alt={`${t('shot')} ${shotNumber} v${vIdx + 1}`}
                            className={cn(
                              "block max-h-full max-w-full cursor-pointer rounded-lg object-contain transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]",
                              hoveredKeyframeVersionKey === kfVerHoverKey ? "scale-110" : "scale-100",
                            )}
                            style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: "100%", objectFit: "contain" }}
                            onClick={(e) => {
                              if (
                                onKeyframeVersionSelection &&
                                keyframe.uuid &&
                                hasMultipleVersions &&
                                isExpanded
                              ) {
                                e.preventDefault();
                                e.stopPropagation();
                                onKeyframeVersionSelection(keyframe.uuid, version.uuid);
                                setExpandedKeyframes((prev) => {
                                  const next = new Set(prev);
                                  next.delete(keyframe.uuid);
                                  return next;
                                });
                                return;
                              }
                              setPreviewImage({ url: versionImageUrl, alt: `${t('shot')} ${shotNumber} v${vIdx + 1}` });
                            }}
                            loading="lazy"
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
                        {isSelected && versionImageUrl && hasMultipleVersions && isExpanded && (
                          <div
                            className="pointer-events-none absolute right-2 top-2 z-20 flex h-7 w-7 items-center justify-center rounded-full bg-[hsl(var(--selection-ring))] text-white shadow-md"
                            aria-hidden
                          >
                            <Check className="h-4 w-4" strokeWidth={3} />
                          </div>
                        )}
                        </div>

                        {/* 生成模式（generation_mode）标签 - 右上角，有数据时显示可读文案，hover 显示说明 */}
                        {keyframe.generation_mode && (() => {
                          const mode = keyframe.generation_mode;
                          const label = mode === 'normal' ? t('generationModeNormal') : mode === 'lipsync' ? t('generationModeLipsync') : mode === 'empty_shot' ? t('generationModeEmptyShot') : mode;
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                  <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal bg-black/40 text-white/90 border-0">
                                    {label}
                                  </Badge>
                                </div>
                              </TooltipTrigger>
                              <TooltipContent side="top">{t('generationModeLabel')}: {label}</TooltipContent>
                            </Tooltip>
                          );
                        })()}
                        {/* 首尾帧标签和ID序号 - 左上角，hover时显示 */}
                        {(version.frame_index === 0 || version.frame_index === -1) && (
                          <div className="absolute top-2 left-2 flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            {version.frame_index === 0 && (
                              <div 
                                className="px-1.5 py-0.5 rounded text-xs font-normal bg-black/30 text-white/70 backdrop-blur-sm"
                                style={{ fontSize: `${Math.max(4, 7 * zoomLevel)}px` }}
                              >
                                {shotNumber} • {t('startFrame')}
                              </div>
                            )}
                            {version.frame_index === -1 && (
                              <div 
                                className="px-1.5 py-0.5 rounded text-xs font-normal bg-black/30 text-white/70 backdrop-blur-sm"
                                style={{ fontSize: `${Math.max(4, 7 * zoomLevel)}px` }}
                              >
                                {shotNumber} • {t('endFrame')}
                              </div>
                            )}
                          </div>
                        )}
                        </div>

                      {/* 参考图（reference_image_urls）- 有则展示 */}
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

                      {/* Edit 模块 - hover时显示在图片下方 */}
                      <div className="w-full flex items-center justify-center gap-2 mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        {/* Regenerate按钮 */}
                        {(() => {
                          const versionKey = `${shotNumber}-${vIdx}`;
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
                                      const promptKey = getPromptKey(shotNumber, vIdx);
                                      const editedPrompt = promptValues.get(promptKey);
                                      const promptToUse = editedPrompt || version.t2i_prompt || '';
                                      
                                      // 调用API并等待结果
                                      await onRegenerate(shotNumber, promptToUse, keyframe.frame_index ?? version.frame_index ?? 0);
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

                        {onKeyframeImageEditWithInstruction && keyframe?.uuid && version.keyframe_url && (() => {
                          const fi = keyframe.frame_index ?? version.frame_index ?? 0;
                          const instrKey = `${shotNumber}-${vIdx}-${fi}-instruction`;
                          const isInstrBusy = regenerating.has(instrKey);
                          const iconShadow =
                            "drop-shadow(2px 2px 4px rgba(59, 130, 246, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.25))";
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm"
                                  disabled={isInstrBusy}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    toggleInstructionPanel(instrKey);
                                  }}
                                >
                                  {isInstrBusy ? (
                                    <Loader2 className="h-5 w-5 animate-spin shrink-0" style={{ filter: iconShadow }} />
                                  ) : (
                                    <span className="text-xl leading-none" style={{ filter: iconShadow, textShadow: "1px 1px 2px rgba(0, 0, 0, 0.2)" }}>🖼️</span>
                                  )}
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent side="top">{isInstrBusy ? t("regenerating") : t("editImage")}</TooltipContent>
                            </Tooltip>
                          );
                        })()}
                        
                        {/* AI Improve按钮 - 使用 Radix Tooltip 加快提示显示 */}
                        {(() => {
                          const versionKey = `${shotNumber}-${vIdx}-ai-improve`;
                          const isImproving = regenerating.has(versionKey);
                          
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm"
                              disabled={isImproving}
                              onClick={async (e) => {
                                e.stopPropagation();
                                
                                // 设置loading状态
                                setRegenerating(prev => new Set(prev).add(versionKey));
                                toast.info(t('aiImprovingToast').replace('{shotNumber}', String(shotNumber)));
                                try {
                                  // 调用 AI improve API（使用 reflection）
                                  const response = await api.videoEditing.regenerateKeyframes({
                                    keyframes: [{
                                      uuid: keyframe.uuid,
                                      versions: [{
                                        uuid: version.uuid,
                                        use_reflection: true,  // 启用 AI reflection
                                      }]
                                    }],
                                    user_option: userOption || {},
                                    thread_id: threadId || undefined,
                                  });
                                  
                                  if (response.code === 0) {
                                    toast.success(t('aiImproveSuccess'));
                                    // 只刷新数据，不再次调用 regenerate API（避免与「重新生成」重复请求）
                                    if (onRefreshKeyframes) {
                                      await onRefreshKeyframes();
                                    }
                                  } else {
                                    toast.error(response.message || t('aiImproveFailed'));
                                  }
                                } catch (error: any) {
                                  console.error('AI Improve error:', error);
                                  toast.error(error?.message || t('aiImproveFailed'));
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
                              <span className={`text-xl ${isImproving ? 'animate-spin' : ''}`} style={{ filter: 'drop-shadow(2px 2px 4px rgba(59, 130, 246, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))', textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)' }}>🚀</span>
                            </Button>
                              </TooltipTrigger>
                              <TooltipContent side="top">{isImproving ? t('aiImproving') : t('aiImprove')}</TooltipContent>
                            </Tooltip>
                          );
                        })()}
                        
                        {/* 编辑提示词：唯一入口，打开与 paint-show 一致的融合编辑弹窗（无则退回展开内联） */}
                        {version.t2i_prompt && (() => {
                          const promptKey = getPromptKey(shotNumber, vIdx);
                          const imgUrl = (version.keyframe_url || version.image_url) as string | undefined;
                          const openFusion =
                            Boolean(
                              onKeyframeRefinePromptOnly &&
                                keyframe?.uuid &&
                                version?.uuid &&
                                imgUrl,
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
                                if (openFusion && onKeyframeRefinePromptOnly && keyframe?.uuid && version?.uuid && imgUrl) {
                                  setFusionPromptEditor({
                                    shotNumber,
                                    versionIndex: vIdx,
                                    frameIndex: keyframe.frame_index ?? version.frame_index ?? 0,
                                    statusKey: `${shotNumber}-${vIdx}-prompt-edit`,
                                    title: `${t('storyboardsSection') || 'Storyboard'} · ${t('versionLabel').replace('{version}', String(vIdx + 1))}`,
                                    image: imgUrl,
                                    initialPrompt: (version.t2i_prompt || '') as string,
                                    keyframeUuid: keyframe.uuid,
                                    versionUuid: version.uuid,
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

                      {/* 错误消息展示 */}
                      {version.success === false && version.error_msg && (
                        <div className="mt-2 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-700 w-full">
                          <div className="flex items-start gap-1">
                            <XCircle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                            <span className="flex-1">{version.error_msg}</span>
                          </div>
                        </div>
                      )}

                      {/* Video Keyframe Reflection issues：单行+ Tooltip 详情，不占高度、不撑大布局 */}
                      {version.reflection_issues && Array.isArray(version.reflection_issues) && version.reflection_issues.length > 0 && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300 cursor-help w-full truncate border-b border-amber-200/50 dark:border-amber-800/50 pb-0.5">
                              {t('video.keyframeReflection.issues') || 'Reflection issues'} ({version.reflection_issues.length})
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="max-w-sm text-xs font-normal whitespace-pre-wrap">
                            {version.reflection_issues.map((issue: any, issueIdx: number) => (
                              <div key={issueIdx} className="mb-1 last:mb-0">
                                {issue.issue_type && (
                                  <span className="font-medium">
                                    {keyframeReflectionIssueTypeLabel(t, issue.issue_type)}:{" "}
                                  </span>
                                )}
                                {issue.description}
                                {issue.severity && (
                                  <span className="opacity-80">
                                    {" "}({keyframeReflectionSeverityLabel(t, issue.severity)})
                                  </span>
                                )}
                                {issue.suggestion && <div className="mt-0.5 text-amber-600 dark:text-amber-400">{issue.suggestion}</div>}
                              </div>
                            ))}
                          </TooltipContent>
                        </Tooltip>
                      )}

                      {/* Regenerating 状态提示 */}
                      {(() => {
                        const versionKey = `${shotNumber}-${vIdx}`;
                        const isRegenerating =
                          regenerating.has(versionKey) || (companionDiceKeys?.has(versionKey) ?? false);
                        return isRegenerating && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t('regenerating')}</div>
                        );
                      })()}
                      {/* AI Improve 状态提示（与重新生成一致，点击后显示「AI 优化中...」） */}
                      {(() => {
                        const versionKey = `${shotNumber}-${vIdx}-ai-improve`;
                        const isImproving = regenerating.has(versionKey);
                        return isImproving && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t('aiImproving')}</div>
                        );
                      })()}
                      {(() => {
                        const promptEditKey = `${shotNumber}-${vIdx}-prompt-edit`;
                        const isPromptEdit = regenerating.has(promptEditKey);
                        return isPromptEdit && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t("editPromptRegenerating")}</div>
                        );
                      })()}
                      {(() => {
                        const fi = keyframe.frame_index ?? version.frame_index ?? 0;
                        const instrKey = `${shotNumber}-${vIdx}-${fi}-instruction`;
                        const isInstr = regenerating.has(instrKey);
                        return isInstr && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t("regenerating")}</div>
                        );
                      })()}

                      {onKeyframeImageEditWithInstruction && keyframe?.uuid && version.keyframe_url && (() => {
                        const fi = keyframe.frame_index ?? version.frame_index ?? 0;
                        const instrKey = `${shotNumber}-${vIdx}-${fi}-instruction`;
                        if (!expandedInstructionPanels.has(instrKey)) return null;
                        const isBusy = regenerating.has(instrKey);
                        return (
                          <div
                            ref={(el) => { instructionPanelRefs.current[instrKey] = el; }}
                            className="mt-1 w-full min-w-0 flex justify-center"
                          >
                            <div className="bg-white dark:bg-card border border-blue-200/80 dark:border-blue-800/80 rounded-lg w-full min-w-0 max-w-full overflow-hidden">
                              <div className="px-3 pt-2 pb-1 text-xs font-medium text-muted-foreground">{t("imageEditInstructionTitle")}</div>
                              <div className="px-3 pb-2 min-w-0 box-border">
                                <Textarea
                                  value={instructionDrafts.get(instrKey) ?? ""}
                                  onChange={(e) => {
                                    const v = e.target.value;
                                    setInstructionDrafts((prev) => new Map(prev).set(instrKey, v));
                                  }}
                                  placeholder={t("imageEditInstructionPlaceholder")}
                                  className="min-h-[88px] w-full min-w-0 max-w-full box-border resize-none text-sm border border-input rounded-md"
                                  disabled={isBusy}
                                />
                              </div>
                              <div className="flex justify-end gap-2 px-3 py-2 border-t border-gray-100 dark:border-border bg-white dark:bg-card rounded-b-lg">
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="sm"
                                  disabled={isBusy}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    closeInstructionPanel(instrKey);
                                  }}
                                >
                                  {t("cancel")}
                                </Button>
                                <Button
                                  type="button"
                                  size="sm"
                                  disabled={isBusy}
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    if (!onKeyframeImageEditWithInstruction) return;
                                    const ins = (instructionDrafts.get(instrKey) ?? "").trim();
                                    if (!ins) {
                                      toast.error(t("imageEditInstructionEmpty"));
                                      return;
                                    }
                                    toast.info(t("regeneratingKeyframeToast").replace("{shotNumber}", String(shotNumber)));
                                    setRegenerating((prev) => new Set(prev).add(instrKey));
                                    try {
                                      await onKeyframeImageEditWithInstruction(shotNumber, ins, vIdx, fi);
                                      closeInstructionPanel(instrKey);
                                    } finally {
                                      setRegenerating((prev) => {
                                        const next = new Set(prev);
                                        next.delete(instrKey);
                                        return next;
                                      });
                                    }
                                  }}
                                >
                                  {isBusy ? (
                                    <>
                                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                      {t("regenerating")}
                                    </>
                                  ) : (
                                    t("imageEditSubmit")
                                  )}
                                </Button>
                              </div>
                            </div>
                          </div>
                        );
                      })()}

                      {/* Prompt展示和编辑 - ref 同步上方 keyframe 图片宽度，首次展开即正确换行 */}
                      {version.t2i_prompt && (() => {
                        const promptKey = getPromptKey(shotNumber, vIdx);
                        const isExpanded = expandedPrompts.has(promptKey);
                        const isEditing = editingPrompts.has(promptKey);
                        const currentPromptValue = promptValues.has(promptKey) ? promptValues.get(promptKey) : version.t2i_prompt;
                        
                        return (
                          <div
                            ref={(el) => { promptContainerRefs.current[promptKey] = el; }}
                            className="mt-1 w-full flex justify-center"
                          >
                            {/* Prompt内容 - 展开时显示，宽度由 useLayoutEffect 同步 */}
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

                      {/* 虚线连接到下一个版本 */}
                      {showIdx < versionsToShow.length - 1 && (
                        <div className="flex justify-center my-3">
                          <div className="w-0.5 h-6 border-l-2 border-dashed border-gray-400"></div>
                        </div>
                      )}
                    </div>
                  );
                  }) : (
                    (() => {
                      const fi = keyframe.frame_index ?? 0;
                      const emptySlotPromptKey = getPromptKey(shotNumber, fi);
                      const emptyRegenKey = `${shotNumber}-empty-slot-${fi}`;
                      const isRegeneratingEmpty = regenerating.has(emptyRegenKey);
                      return (
                    <div className="inline-flex flex-col items-center w-full group">
                      <div
                        ref={(el) => { mediaContainerRefs.current[emptySlotPromptKey] = el; }}
                        className="inline-flex flex-shrink-0 rounded-lg relative bg-black/5 items-center justify-center overflow-hidden"
                        style={{
                          width: `${scaledCardWidth}px`,
                          height: `${scaledMediaHeight}px`,
                        }}
                      >
                        <div className="absolute inset-0 flex items-center justify-center">
                          <div className="w-full h-full rounded-lg bg-gray-100 dark:bg-gray-800 animate-pulse flex items-center justify-center">
                            <span className="text-xs text-gray-400">{t('loading') || 'Loading...'}</span>
                          </div>
                        </div>
                        <div className="absolute top-2 left-2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
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
                                  const editedPrompt = promptValues.get(emptySlotPromptKey);
                                  await onRegenerate(shotNumber, editedPrompt || '', fi);
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
                            <span className="inline-flex cursor-not-allowed">
                              <Button
                                size="sm"
                                variant="ghost"
                                type="button"
                                className="h-7 w-7 p-0 rounded-full bg-white/90 shadow-sm opacity-50 pointer-events-none"
                                disabled
                                aria-disabled
                              >
                                <span className="text-xl" style={{ filter: 'drop-shadow(2px 2px 4px rgba(59, 130, 246, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.3))', textShadow: '1px 1px 2px rgba(0, 0, 0, 0.3)' }}>🚀</span>
                              </Button>
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="top">{t('aiImproveRequiresKeyframeVersion')}</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                togglePromptExpanded(emptySlotPromptKey);
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
                        const isExpanded = expandedPrompts.has(emptySlotPromptKey);
                        const isEditing = editingPrompts.has(emptySlotPromptKey);
                        const currentPromptValue = promptValues.has(emptySlotPromptKey) ? (promptValues.get(emptySlotPromptKey) as string) : '';
                        return (
                          <div
                            ref={(el) => { promptContainerRefs.current[emptySlotPromptKey] = el; }}
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
                                      data-prompt-key={emptySlotPromptKey}
                                      onChange={(e) => {
                                        setPromptValues(prev => new Map(prev).set(emptySlotPromptKey, e.target.value));
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
                                            onClick={() => cancelEditingPrompt(emptySlotPromptKey)}
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
                                            onClick={() => savePromptEdit(emptySlotPromptKey, shotNumber)}
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
                                    onClick={() => startEditingPrompt(emptySlotPromptKey, currentPromptValue)}
                                  >
                                    {currentPromptValue || <span className="text-muted-foreground">{t('enterPrompt')}</span>}
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
                  );
                })()}
                {/* 版本数按钮 - 多版本时与 Shots 一致 */}
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
        
        {/* 懒加载扩展：加载更多指示器 */}
        {isLoading && (
          <div 
            className="flex items-center justify-center bg-card/30 rounded-lg border-2 border-dashed border-gray-300 min-h-[220px] w-full"
          >
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Loader2 className="w-6 h-6 animate-spin" />
              <span className="text-sm">{t('loading') || 'Loading...'}</span>
            </div>
          </div>
        )}
        
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
                className="max-w-full max-h-[95vh] object-contain rounded-lg cursor-pointer"
                onClick={() => setPreviewImage(null)}
              />
            </div>
          )}
        </DialogContent>
      </Dialog>

      {fusionPromptEditor && onKeyframeRefinePromptOnly && (
        <VideoArtifactPromptEditorDialog
          open={!!fusionPromptEditor}
          title={fusionPromptEditor.title}
          image={fusionPromptEditor.image}
          initialPrompt={fusionPromptEditor.initialPrompt}
          artifactKind="keyframe"
          threadId={threadId}
          onClose={() => setFusionPromptEditor(null)}
          onRefineInstruction={(instruction) =>
            onKeyframeRefinePromptOnly(
              fusionPromptEditor.keyframeUuid,
              fusionPromptEditor.versionUuid,
              instruction,
            )
          }
          onSaveAndRegenerate={async (newPrompt) => {
            if (!onRegenerate) {
              throw new Error("onRegenerate is not available");
            }
            const { statusKey } = fusionPromptEditor;
            setRegenerating((prev) => new Set(prev).add(statusKey));
            try {
              const ok = await onRegenerate(
                fusionPromptEditor.shotNumber,
                newPrompt,
                fusionPromptEditor.versionIndex,
                fusionPromptEditor.frameIndex,
              );
              if (!ok) {
                throw new Error(t("failedRegenerateKeyframe"));
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
