import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { User, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronUp, X, Check, Grid3x3, Loader2 } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useState, useCallback, useRef, useLayoutEffect } from "react";
import { useToast } from "@/hooks/use-toast";
import { processCharactersData } from "@/utils/dataCompatibility";
import { api } from "@/services/api";
import { toast as sonnerToast } from "sonner";
import { VideoArtifactPromptEditorDialog } from "./VideoArtifactPromptEditorDialog";
import { cn } from "@/lib/utils";

interface CharactersVersionSectionProps {
  charactersData: any;
  onVersionSelection?: (characterUuid: string, versionUuid: string) => Promise<boolean | void>;
  onRegenerate?: (characterUuid: string, customPrompt?: string, versionIndex?: number) => Promise<boolean>;
  /** 与关键帧 🖼️ 一致：基于当前成图 + regenerate_strategy=instruction_edit_image */
  onCharacterImageEditWithInstruction?: (
    characterUuid: string,
    instruction: string,
    versionIndex: number,
  ) => Promise<boolean>;
  /** instruction_merge_prompt：返回融合后的完整 t2i_prompt（与关键帧融合编辑弹窗一致） */
  onCharacterRefinePromptOnly?: (
    characterUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  onRegenerateMultiView?: (characterUuid: string, versionUuid: string, customPrompt?: string) => Promise<boolean>;
  onFullView?: () => void;
  onDataUpdate?: () => void;  // ⭐ 数据更新回调
  zoomLevel?: number; // ✅ 统一的缩放级别
  threadId?: string;
}

export const CharactersVersionSection = ({ charactersData, onVersionSelection, onRegenerate, onCharacterImageEditWithInstruction, onCharacterRefinePromptOnly, onRegenerateMultiView, onFullView, onDataUpdate, zoomLevel = 1, threadId }: CharactersVersionSectionProps) => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());
  const [regeneratingMultiView, setRegeneratingMultiView] = useState<Set<string>>(new Set());
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());
  const [expandedCharacters, setExpandedCharacters] = useState<Set<string>>(new Set());
  const [editingPrompts, setEditingPrompts] = useState<Set<string>>(new Set());
  const [promptValues, setPromptValues] = useState<Map<string, string>>(new Map());
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string } | null>(null);
  const [multiViewSelector, setMultiViewSelector] = useState<{ 
    characterUuid: string; 
    versionUuid: string; 
    versions: any[]; 
    selectedId: string | null;
  } | null>(null);
  const [hoveredMultiViewStack, setHoveredMultiViewStack] = useState<string | null>(null);  // ⭐ 用于多视角图重叠显示
  const [expandedImageEditPanels, setExpandedImageEditPanels] = useState<Set<string>>(new Set());
  const [instructionDrafts, setInstructionDrafts] = useState<Map<string, string>>(new Map());
  const [fusionPromptEditor, setFusionPromptEditor] = useState<{
    characterUuid: string;
    versionUuid: string;
    versionIndex: number;
    statusKey: string;
    title: string;
    image: string;
    initialPrompt: string;
  } | null>(null);
  const [hoveredCharacterVersionKey, setHoveredCharacterVersionKey] = useState<string | null>(null);
  const mediaContainerRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const promptContainerRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const imageEditPanelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  
  // 计算缩放后的尺寸 - 与keyframe保持一致
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


  // Helper functions for prompt management
  const getPromptKey = (characterId: string, versionIndex: number) => `${characterId}-${versionIndex}`;
  
  // 展开 prompt 时同步宽度到上方角色图片容器，首次展开即正确换行（与 keyframe / shot 一致）
  useLayoutEffect(() => {
    if (expandedPrompts.size === 0 && expandedImageEditPanels.size === 0) return;
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
      const syncImageEditPanel = (imgEditKey: string) => {
        if (!imgEditKey.endsWith("-image-edit")) return;
        const base = imgEditKey.slice(0, -"-image-edit".length);
        const lastDash = base.lastIndexOf("-");
        if (lastDash < 0) return;
        const vIdxPart = base.slice(lastDash + 1);
        const vIdx = parseInt(vIdxPart, 10);
        if (!Number.isFinite(vIdx)) return;
        const characterId = base.slice(0, lastDash);
        const mediaKey = getPromptKey(characterId, vIdx);
        const media = mediaContainerRefs.current[mediaKey];
        const panel = imageEditPanelRefs.current[imgEditKey];
        if (media && panel && media.offsetWidth > 0) {
          const parentWidth = panel.parentElement?.clientWidth;
          const targetWidth = parentWidth ? Math.min(media.offsetWidth, parentWidth) : media.offsetWidth;
          panel.style.width = `${targetWidth}px`;
        }
      };
      expandedImageEditPanels.forEach((imgEditKey) => syncImageEditPanel(imgEditKey));
    };
    updateWidths();
    const raf1 = requestAnimationFrame(updateWidths);
    const t = setTimeout(updateWidths, 50);
    return () => {
      cancelAnimationFrame(raf1);
      clearTimeout(t);
    };
  }, [expandedPrompts, expandedImageEditPanels]);

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

  const toggleImageEditPanel = (imgKey: string) => {
    setExpandedImageEditPanels((prev) => {
      if (prev.has(imgKey)) return new Set();
      return new Set([imgKey]);
    });
  };

  const closeImageEditPanel = (imgKey: string) => {
    setExpandedImageEditPanels((prev) => {
      const next = new Set(prev);
      next.delete(imgKey);
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
  
  const savePromptEdit = (key: string, characterUuid: string) => {
    setEditingPrompts(prev => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
    // 这里可以添加保存逻辑，比如更新到后端
  };

  // ⭐ 选中角色版本
  const [selectingVersion, setSelectingVersion] = useState<Set<string>>(new Set());
  const handleSelectCharacterVersion = async (characterUuid: string, versionUuid: string) => {
    const selectionKey = `character-${characterUuid}-${versionUuid}`;
    setSelectingVersion(prev => new Set(prev).add(selectionKey));
    
    try {
      if (onVersionSelection) {
        await onVersionSelection(characterUuid, versionUuid);
      } else {
        const response = await api.videoEditing.selectCharacterVersion(characterUuid, versionUuid);
        console.log('✅ 选中角色版本成功:', response);
      }
      
      toast({
        title: t('success') || 'Success',
        description: '角色版本已选中',
      });
      
      // 更新本地状态（优化：只刷新角色数据）
      if (!onVersionSelection && onDataUpdate) {
        // 如果有回调，调用回调刷新数据
        onDataUpdate();
      } else {
        // 如果没有回调，只更新本地状态（不刷新页面）
        // 注意：这里需要父组件支持状态更新
        console.log('⚠️ onDataUpdate callback not provided, local state update only');
      }
    } catch (error: any) {
      console.error('❌ 选中角色版本失败:', error);
      toast({
        title: t('error') || 'Error',
        description: error.message || error.response?.data?.message || '选中角色版本失败',
        variant: "destructive"
      });
    } finally {
      setSelectingVersion(prev => {
        const next = new Set(prev);
        next.delete(selectionKey);
        return next;
      });
    }
  };

  // ⭐ 选中多视角图版本
  const [selectingMultiView, setSelectingMultiView] = useState<Set<string>>(new Set());
  const handleSelectMultiViewVersion = async (characterUuid: string, versionUuid: string, multiViewVersionUuid: string) => {
    const selectionKey = `multiview-${characterUuid}-${versionUuid}-${multiViewVersionUuid}`;
    setSelectingMultiView(prev => new Set(prev).add(selectionKey));
    
    try {
      const response = await api.videoEditing.selectMultiViewVersion(characterUuid, versionUuid, multiViewVersionUuid);
      console.log('✅ 选中多视角图版本成功:', response);
      
      toast({
        title: t('success') || 'Success',
        description: '多视角图版本已选中',
      });
      
      // 更新本地状态（优化：只刷新角色数据）
      if (onDataUpdate) {
        // 如果有回调，调用回调刷新数据
        onDataUpdate();
      } else {
        // 如果没有回调，只更新本地状态（不刷新页面）
        // 注意：这里需要父组件支持状态更新
        console.log('⚠️ onDataUpdate callback not provided, local state update only');
      }
    } catch (error: any) {
      console.error('❌ 选中多视角图版本失败:', error);
      toast({
        title: t('error') || 'Error',
        description: error.message || error.response?.data?.message || '选中多视角图版本失败',
        variant: "destructive"
      });
    } finally {
      setSelectingMultiView(prev => {
        const next = new Set(prev);
        next.delete(selectionKey);
        return next;
      });
    }
  };

  // API返回格式: { characters: [], total: 123 }
  if (!charactersData || !charactersData.characters || charactersData.characters.length === 0) return null;

  // 🔥 兼容性处理：确保老数据也能正确显示
  const processedCharactersData = processCharactersData(charactersData);
  const characters = processedCharactersData.characters;

  return (
    <Card className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <User className="w-5 h-5 mr-2 text-accent-green" />
          {t('characterSection')} ({characters.length})
        </h3>
        
        <div className="flex items-center gap-2">
          {onFullView && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onFullView}
              className="h-8 px-3"
            >
              {t('immersiveMode')}
            </Button>
          )}
        </div>
      </div>
      
      {/* 平铺显示容器 - 与 Storyboards 一致：滚动容器自身 pl-10，内层 flex 左对齐 + minWidth:100%，避免第一个图被裁切 */}
      <div
        className="overflow-x-auto scrollbar-subtle pl-10"
        style={{ maxWidth: '100%' }}
      >
        <div
          className="flex gap-4 pb-4 pr-4 items-start"
          style={{ minWidth: '100%' }}
        >
        {characters.map((character: any, idx: number) => {
          const isLegacyData = character.isLegacyData || (!character.versions || character.versions.length === 0);
          
            return (
            /* ⭐ 每个角色占两列：角色版本列 + 多视角列 */
            <div key={idx} className="flex gap-3 flex-shrink-0 items-start">
              {/* 左侧：角色版本列 */}
              <Card className="glass-bg p-2 !border-0 shadow-none flex flex-col items-center">
                {isLegacyData ? (
                  <div className="flex items-center justify-center mb-3 w-full" style={{ minHeight: '24px' }}>
                    <span className="text-sm font-semibold">{character.name}</span>
                  </div>
                ) : (
                  <div className="mb-3 w-full shrink-0" style={{ minHeight: '24px' }} aria-hidden />
                )}

              {/* 老数据：只显示名字和图片 */}
              {isLegacyData ? (
                <div className="flex justify-center w-full">
                  {/* 角色图片 */}
                  <div className="inline-flex rounded-lg relative bg-black/5 items-center justify-center overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] group/legacy hover:scale-[1.03] hover:-translate-y-1" style={{ boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06), 0 0 0 1px rgba(0, 0, 0, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.1)' }}>
                    {character.image_url ? (
                      <img
                        src={character.image_url}
                        alt={character.name}
                        className="block object-contain cursor-pointer rounded-lg transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover/legacy:scale-110"
                        style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                        onClick={() => setPreviewImage({ url: character.image_url, alt: character.name })}
                      />
                    ) : (
                      <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: `${scaledCardWidth}px` }}>
                        <span className="text-xs text-gray-400">{t('noImage')}</span>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                /* 新数据：角色版本列（垂直排列） */
                <div className="flex flex-col gap-3 items-center w-full">
                      {(() => {
                        const versions = Array.isArray(character.versions) ? character.versions : [];
                        const hasMultipleVersions = versions.length > 1;
                        const characterKey = String(character.uuid || character.id || idx);
                        const isExpandedCharacter = expandedCharacters.has(characterKey);
                        const selectedVersionId = character.selected_version_id;
                        const currentVersion = versions[character.current_version_index || 0] || versions[0];
                        const preferredVersion = versions.find((v: any) => {
                          if (selectedVersionId) return v?.id === selectedVersionId || v?.uuid === selectedVersionId;
                          return (
                            (!!currentVersion?.id && v?.id === currentVersion.id) ||
                            (!!currentVersion?.uuid && v?.uuid === currentVersion.uuid)
                          );
                        }) || currentVersion;
                        const versionsToShow = hasMultipleVersions && !isExpandedCharacter && preferredVersion
                          ? [preferredVersion]
                          : versions;

                        return versionsToShow.map((version: any, showIdx: number) => {
                        const sourceVersionIndex = versions.findIndex((v: any) =>
                          (version?.id && v?.id === version.id) || (version?.uuid && v?.uuid === version.uuid)
                        );
                        const vIdx = sourceVersionIndex >= 0 ? sourceVersionIndex : showIdx;
                        const isSelected = character.selected_version_id === version.id;
                        const versionImageUrl = version.character_image_url;

                        const versionUuidForApi = String(version.id || version.uuid || "");
                        const hoverRowKey = `${characterKey}-${vIdx}`;
                        return (
                          <div
                            key={`char-${character.id}-${version.id}`}
                            className="flex flex-col items-center w-full group"
                            style={{ minHeight: '0' }}
                            onMouseEnter={() => setHoveredCharacterVersionKey(hoverRowKey)}
                            onMouseLeave={() => setHoveredCharacterVersionKey(null)}
                          >
                            <h3 className="mb-3 w-full min-h-[1.25rem] truncate px-1 text-center text-sm text-foreground/80 transition-colors group-hover:text-foreground">
                              {hoveredCharacterVersionKey === hoverRowKey
                                ? `${t('version')} ${vIdx + 1}`
                                : character.name}
                            </h3>

                            <div
                              className={cn(
                                "relative w-full cursor-pointer overflow-hidden rounded-2xl transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] ring-2 ring-transparent",
                                isSelected && "ring-[hsl(var(--selection-ring))]",
                                hoveredCharacterVersionKey === hoverRowKey
                                  ? "scale-[1.03] -translate-y-1"
                                  : "scale-100 translate-y-0",
                              )}
                              style={{
                                boxShadow:
                                  hoveredCharacterVersionKey === hoverRowKey
                                    ? "var(--artifact-shadow-card-hover)"
                                    : "var(--artifact-shadow-card)",
                              }}
                              onClick={() => {
                                const characterUuid = character.uuid || character.id;
                                if (hasMultipleVersions && isExpandedCharacter) {
                                  setExpandedCharacters((prev) => {
                                    const next = new Set(prev);
                                    next.delete(characterKey);
                                    return next;
                                  });
                                }
                                handleSelectCharacterVersion(characterUuid, version.id);
                              }}
                              title={isSelected ? '已选中（点击切换）' : '点击选中此版本'}
                            >
                              <div
                                ref={(el) => {
                                  mediaContainerRefs.current[getPromptKey(character.id, vIdx)] = el;
                                }}
                                className="relative inline-flex items-center justify-center overflow-hidden rounded-xl bg-black/5"
                              >
                                {versionImageUrl ? (
                                  <img
                                    src={versionImageUrl}
                                    alt={`${character.name} v${vIdx + 1}`}
                                    title="双击预览大图"
                                    className={cn(
                                      "block max-h-full max-w-full cursor-pointer rounded-lg object-contain transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]",
                                      hoveredCharacterVersionKey === hoverRowKey ? "scale-110" : "scale-100",
                                    )}
                                    style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                                    onDoubleClick={(e) => {
                                      e.stopPropagation();
                                      setPreviewImage({ url: versionImageUrl, alt: `${character.name} v${vIdx + 1}` });
                                    }}
                                  />
                                ) : (
                                  <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: `${scaledCardWidth}px` }}>
                                    <span className="text-xs text-gray-400">{t('noImage')}</span>
                                  </div>
                                )}
                                {/* 仅「展开多版本」时显示角标勾；折叠后只留一条时不再展示 */}
                                {isSelected && versionImageUrl && hasMultipleVersions && isExpandedCharacter && (
                                  <div
                                    className="pointer-events-none absolute right-2 top-2 z-20 flex h-7 w-7 items-center justify-center rounded-full bg-[hsl(var(--selection-ring))] text-white shadow-md"
                                    aria-hidden
                                  >
                                    <Check className="h-4 w-4" strokeWidth={3} />
                                  </div>
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

                      {/* Edit 模块 - hover时显示在图片下方 */}
                      <div className="w-full flex items-center justify-center gap-2 mt-2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none group-hover:pointer-events-auto">
                        {/* Regenerate按钮 */}
                        {(() => {
                          const versionKey = `${character.id}-${vIdx}`;
                          const isRegenerating = regenerating.has(versionKey);
                          
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  size="sm"
                                  className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm pointer-events-auto"
                                  disabled={isRegenerating}
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    if (!onRegenerate) return;

                                    // 设置loading状态
                                    setRegenerating(prev => new Set(prev).add(versionKey));

                                    try {
                                      // 获取用户编辑的 prompt，如果没有则使用原始 prompt
                                      const promptKey = getPromptKey(character.id, vIdx);
                                      const editedPrompt = promptValues.get(promptKey);
                                      const promptToUse = editedPrompt || version.t2i_prompt || '';
                                      
                                      // 调用API并等待结果，传递versionIndex - 优先使用 uuid，如果没有则使用 id
                                      const characterIdentifier = character.uuid || character.id;
                                      await onRegenerate(characterIdentifier, promptToUse, vIdx);
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

                        {onCharacterImageEditWithInstruction && versionImageUrl && (() => {
                          const imgEditKey = `${character.id}-${vIdx}-image-edit`;
                          const isImgBusy = regenerating.has(imgEditKey);
                          const iconShadow =
                            "drop-shadow(2px 2px 4px rgba(59, 130, 246, 0.5)) drop-shadow(-1px -1px 2px rgba(255, 255, 255, 0.25))";
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white dark:bg-card/90 dark:hover:bg-card shadow-sm pointer-events-auto"
                                  disabled={isImgBusy}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    toggleImageEditPanel(imgEditKey);
                                  }}
                                >
                                  {isImgBusy ? (
                                    <Loader2 className="h-5 w-5 animate-spin shrink-0" style={{ filter: iconShadow }} />
                                  ) : (
                                    <span className="text-xl leading-none" style={{ filter: iconShadow, textShadow: "1px 1px 2px rgba(0, 0, 0, 0.2)" }}>🖼️</span>
                                  )}
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent side="top">{isImgBusy ? t("regenerating") : t("editImage")}</TooltipContent>
                            </Tooltip>
                          );
                        })()}
                        
                        {/* 编辑提示词：与关键帧一致，优先打开融合编辑弹窗 */}
                        {version.t2i_prompt && (() => {
                          const promptKey = getPromptKey(character.id, vIdx);
                          const characterUuid = character.uuid || character.id;
                          const openFusion = Boolean(
                            onCharacterRefinePromptOnly &&
                              characterUuid &&
                              versionUuidForApi &&
                              versionImageUrl,
                          );
                          return (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white dark:bg-card/90 dark:hover:bg-card shadow-sm pointer-events-auto"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (openFusion && onCharacterRefinePromptOnly && versionImageUrl) {
                                      setFusionPromptEditor({
                                        characterUuid,
                                        versionUuid: versionUuidForApi,
                                        versionIndex: vIdx,
                                        statusKey: `${character.id}-${vIdx}-prompt-edit`,
                                        title: `${character.name} · ${t('versionLabel').replace('{version}', String(vIdx + 1))}`,
                                        image: versionImageUrl,
                                        initialPrompt: (version.t2i_prompt || '') as string,
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
                        const versionKey = `${character.id}-${vIdx}`;
                        const isRegenerating = regenerating.has(versionKey);
                        return isRegenerating && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t('regenerating')}</div>
                        );
                      })()}
                      {(() => {
                        const imgEditKey = `${character.id}-${vIdx}-image-edit`;
                        const isImg = regenerating.has(imgEditKey);
                        return isImg && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t("regenerating")}</div>
                        );
                      })()}
                      {(() => {
                        const promptEditKey = `${character.id}-${vIdx}-prompt-edit`;
                        const isPromptEdit = regenerating.has(promptEditKey);
                        return isPromptEdit && (
                          <div className="text-xs text-blue-500 mb-2 text-center">{t("editPromptRegenerating")}</div>
                        );
                      })()}

                      {onCharacterImageEditWithInstruction && versionImageUrl && (() => {
                        const imgEditKey = `${character.id}-${vIdx}-image-edit`;
                        if (!expandedImageEditPanels.has(imgEditKey)) return null;
                        const isBusy = regenerating.has(imgEditKey);
                        const characterUuid = character.uuid || character.id;
                        return (
                          <div
                            ref={(el) => { imageEditPanelRefs.current[imgEditKey] = el; }}
                            className="mt-1 w-full min-w-0 flex justify-center"
                          >
                            <div className="bg-white dark:bg-card border border-blue-200/80 dark:border-blue-800/80 rounded-lg w-full min-w-0 max-w-full overflow-hidden">
                              <div className="px-3 pt-2 pb-1 text-xs font-medium text-muted-foreground">{t("imageEditInstructionTitle")}</div>
                              <div className="px-3 pb-2 min-w-0 box-border">
                                <Textarea
                                  value={instructionDrafts.get(imgEditKey) ?? ""}
                                  onChange={(e) => {
                                    const v = e.target.value;
                                    setInstructionDrafts((prev) => new Map(prev).set(imgEditKey, v));
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
                                    closeImageEditPanel(imgEditKey);
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
                                    if (!onCharacterImageEditWithInstruction) return;
                                    const ins = (instructionDrafts.get(imgEditKey) ?? "").trim();
                                    if (!ins) {
                                      toast({ title: t("imageEditInstructionEmpty"), variant: "destructive" });
                                      return;
                                    }
                                    const ch = charactersData?.characters?.find(
                                      (c: any) => (c.uuid || c.id) === characterUuid,
                                    );
                                    const nm = (ch?.name as string) || "";
                                    sonnerToast.info(
                                      nm
                                        ? t("regeneratingCharacterToast").replace("{characterName}", nm)
                                        : t("regenerating"),
                                    );
                                    setRegenerating((prev) => new Set(prev).add(imgEditKey));
                                    try {
                                      await onCharacterImageEditWithInstruction(characterUuid, ins, vIdx);
                                      closeImageEditPanel(imgEditKey);
                                    } finally {
                                      setRegenerating((prev) => {
                                        const next = new Set(prev);
                                        next.delete(imgEditKey);
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

                      {/* Prompt展示和编辑 - ref 同步上方角色图片宽度，首次展开即正确换行 */}
                      {version.t2i_prompt && (() => {
                        const promptKey = getPromptKey(character.id, vIdx);
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
                                            onClick={() => savePromptEdit(promptKey, character.id)}
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
                      });
                    })()}
                    {Array.isArray(character.versions) && character.versions.length > 1 && (
                      <div className="w-full flex justify-end mt-2">
                        <button
                          type="button"
                          className="px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded transition-all duration-200 flex items-center gap-1"
                          onClick={() => {
                            const characterKey = String(character.uuid || character.id || idx);
                            setExpandedCharacters(prev => {
                              const next = new Set(prev);
                              if (next.has(characterKey)) next.delete(characterKey);
                              else next.add(characterKey);
                              return next;
                            });
                          }}
                          title={expandedCharacters.has(String(character.uuid || character.id || idx)) ? t('collapseVersions') : t('expandVersions')}
                        >
                          {t('versionsCount').replace('{count}', String(character.versions.length))}
                          {expandedCharacters.has(String(character.uuid || character.id || idx)) ? (
                            <ChevronUp className="w-3 h-3" />
                          ) : (
                            <ChevronDown className="w-3 h-3" />
                          )}
                        </button>
                      </div>
                    )}
                </div>
              )}
              </Card>

              {/* 右侧：多视角图列（重叠显示） - 只在有图片时显示 */}
              {(() => {
                // 检查是否有任何版本包含 multi-view 图片
                const hasMultiViewImages = !isLegacyData && character.versions && character.versions.some((version: any) => {
                  const multiViewVersions = version.multi_view_versions || 
                    (version.multi_view_image_version ? [version.multi_view_image_version] : []);
                  return multiViewVersions.length > 0 && multiViewVersions.some((mv: any) => mv.multi_view_image_url);
                });
                
                if (!hasMultiViewImages) return null;
                
                return (
                  <Card className="glass-bg p-2 !border-0 shadow-none flex flex-col items-center">
                {/* Multi-view标题 - 与角色标题对齐 */}
                <div className="flex items-center justify-center mb-3 w-full" style={{ minHeight: '24px' }}>
                  <span className="text-xs text-gray-500 font-medium flex items-center gap-1">
                    <Grid3x3 className="w-3 h-3" />
                    Multi-view
                  </span>
                </div>

                {/* 多视角图版本（垂直排列，每个版本的多视角图重叠显示） */}
                <div className="flex flex-col gap-3 items-center w-full">
                  {character.versions && character.versions.map((version: any, vIdx: number) => {
                    // 获取所有多视角图版本
                    const multiViewVersions = version.multi_view_versions || 
                      (version.multi_view_image_version ? [version.multi_view_image_version] : []);
                    
                    const stackKey = `${character.id}-${version.id}`;
                    const isHovered = hoveredMultiViewStack === stackKey;
                    const selectedMultiViewId = version.selected_multi_view_version_id || version.multi_view_image_version?.id;
                    
                    // 获取选中的多视角图版本的 prompt
                    const selectedMultiViewVersion = multiViewVersions.find((mv: any) => mv.id === selectedMultiViewId);
                    const multiViewPrompt = selectedMultiViewVersion?.multi_view_prompt;
                    
                    return (
                      <div key={`mv-${character.id}-${version.id}`} className="flex flex-col items-center w-full group" style={{ minHeight: '0' }}>
                        {multiViewVersions.length > 0 ? (
                          <>
                            {/* 版本选择圈圈和标题 - 参考 shot 的实现，与角色版本对齐 */}
                            <div className="flex items-center justify-between mb-2 w-full" style={{ minHeight: '20px' }}>
                              <div className="text-xs font-medium text-muted-foreground">
                                {/* 不显示版本标签，与角色版本保持一致 */}
                              </div>
                              {/* 多视角图版本选择圈圈 */}
                              {selectedMultiViewId && (
                                <div
                                  className={`w-5 h-5 rounded-full border-2 cursor-pointer flex items-center justify-center transition-all duration-200 ${
                                    true  // 总是显示选中状态
                                      ? 'bg-blue-500 border-blue-500 shadow-lg'
                                      : 'bg-white border-gray-300 hover:border-blue-400 hover:shadow-md'
                                  }`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    // 如果有多个版本，点击圈圈可以选择下一个
                                    if (multiViewVersions.length > 1) {
                                      // 找到当前选中版本的索引
                                      const currentIndex = multiViewVersions.findIndex((mv: any) => mv.id === selectedMultiViewId);
                                      // 选择下一个版本（循环）
                                      const nextIndex = (currentIndex + 1) % multiViewVersions.length;
                                      const nextVersion = multiViewVersions[nextIndex];
                                      
                                      const characterUuid = character.uuid || character.id;
                                      const versionUuid = version.id;
                                      handleSelectMultiViewVersion(characterUuid, versionUuid, nextVersion.id);
                                    }
                                  }}
                                  title={multiViewVersions.length > 1 ? t('selectedVersion') + ' (' + t('clickToSwitchNextVersion') + ')' : t('selectedVersion')}
                                >
                                  <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                                  </svg>
                                </div>
                              )}
                            </div>
                            
                            {/* ⭐ 重叠显示的多视角图版本 - 卡片堆叠效果 */}
                            <div 
                              ref={(el) => { mediaContainerRefs.current[`multi-view-${character.id}-${vIdx}`] = el; }}
                              className="relative inline-flex rounded-lg bg-black/5 items-center justify-center"
                              style={{ 
                                maxHeight: `${scaledMediaHeight}px`,
                                boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06), 0 0 0 1px rgba(0, 0, 0, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.1)'
                              }}
                              onMouseEnter={() => setHoveredMultiViewStack(stackKey)}
                              onMouseLeave={() => setHoveredMultiViewStack(null)}
                            >
                              {multiViewVersions.map((mvVersion: any, mvIdx: number) => {
                                const isSelectedMv = selectedMultiViewId === mvVersion.id;
                                const totalVersions = multiViewVersions.length;
                                
                                // ⭐ 卡片堆叠效果：计算偏移和旋转
                                // 选中的版本在中心，其他版本围绕它堆叠
                                const baseOffset = isSelectedMv ? 0 : ((mvIdx - (totalVersions - 1) / 2) * 12);
                                const baseRotation = isSelectedMv ? 0 : ((mvIdx - (totalVersions - 1) / 2) * 5);
                                const baseZIndex = isSelectedMv ? totalVersions + 10 : (totalVersions - Math.abs(mvIdx - (totalVersions - 1) / 2));
                                
                                // hover 时大幅展开并放大显示所有版本
                                const hoverOffset = isHovered && !isSelectedMv ? baseOffset * 3 : baseOffset;
                                const hoverRotation = isHovered && !isSelectedMv ? baseRotation * 1.2 : baseRotation;
                                const hoverScale = isHovered && !isSelectedMv ? 1.15 : (isSelectedMv ? 1 : 0.95);
                                const hoverZIndex = isHovered && !isSelectedMv ? baseZIndex + 10 : baseZIndex;
                                
                                // 透明度：选中的完全显示，hover 时其他版本清晰显示，不 hover 时其他版本稍微可见（显示堆叠效果）
                                const opacity = isSelectedMv ? 1 : (isHovered ? 0.95 : 0.5);
                                
                                return (
                                  <div
                                    key={mvVersion.id || mvIdx}
                                    className={`absolute rounded-lg transition-all duration-300 cursor-pointer ${
                                      isSelectedMv ? 'ring-2 ring-blue-500 ring-offset-1' : ''
                                    }`}
                                    style={{
                                      maxHeight: `${scaledMediaHeight}px`,
                                      transform: `translate(${hoverOffset}px, ${hoverOffset * 0.3}px) rotate(${hoverRotation}deg) scale(${hoverScale})`,
                                      transformOrigin: 'center center',
                                      opacity: opacity,
                                      zIndex: hoverZIndex,
                                      pointerEvents: 'auto',
                                      boxShadow: isSelectedMv 
                                        ? '0 8px 16px -4px rgba(59, 130, 246, 0.4), 0 4px 8px -2px rgba(59, 130, 246, 0.3)' 
                                        : isHovered 
                                          ? '0 8px 16px -4px rgba(0, 0, 0, 0.3), 0 4px 8px -2px rgba(0, 0, 0, 0.2)' 
                                          : '0 2px 4px -1px rgba(0, 0, 0, 0.1)'
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const characterUuid = character.uuid || character.id;
                                      const versionUuid = version.id;
                                      if (mvVersion.id) {
                                        void handleSelectMultiViewVersion(characterUuid, versionUuid, mvVersion.id);
                                      }
                                    }}
                                    onDoubleClick={(e) => {
                                      e.stopPropagation();
                                      if (mvVersion.multi_view_image_url) {
                                        setPreviewImage({
                                          url: mvVersion.multi_view_image_url,
                                          alt: `${character.name} multi-view v${mvVersion.version_number}`,
                                        });
                                      }
                                    }}
                                    title={isSelectedMv ? `${t('selectedVersion')} v${mvVersion.version_number} (${t('clickToViewAndSelect')})` : `v${mvVersion.version_number} (${t('clickToViewAndSelect')})`}
                                  >
                                    <img
                                      src={mvVersion.multi_view_image_url}
                                      alt={`${character.name} multi-view v${mvVersion.version_number}`}
                                      className="block object-contain rounded-lg pointer-events-none"
                                      style={{ maxHeight: `${scaledMediaHeight}px`, maxWidth: '100%' }}
                                    />
                                  </div>
                                );
                              })}
                            </div>

                            {/* Edit 模块 - hover时显示在图片下方 */}
                            <div className="w-full flex items-center justify-center gap-2 mt-2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none group-hover:pointer-events-auto">
                              {/* Regenerate按钮 */}
                              {onRegenerateMultiView && (() => {
                                const multiViewKey = `multi-view-${character.id}-${vIdx}`;
                                const isRegenerating = regeneratingMultiView.has(multiViewKey);
                                
                                return (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        size="sm"
                                        className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white shadow-sm pointer-events-auto"
                                        disabled={isRegenerating}
                                        onClick={async (e) => {
                                          e.stopPropagation();
                                          const multiViewKey = `multi-view-${character.id}-${vIdx}`;
                                          setRegeneratingMultiView(prev => new Set(prev).add(multiViewKey));
                                          
                                          try {
                                            const characterIdentifier = character.uuid || character.id;
                                            const versionIdentifier = version.id || version.uuid;
                                            
                                            // 获取编辑的 prompt（如果有）
                                            const promptKey = `multi-view-${character.id}-${vIdx}`;
                                            const editedPrompt = promptValues.get(promptKey);
                                            const promptToUse = editedPrompt || multiViewPrompt || '';
                                            
                                            // 调用 regenerate，传递 custom_prompt
                                            await onRegenerateMultiView(characterIdentifier, versionIdentifier, promptToUse);
                                          } finally {
                                            setRegeneratingMultiView(prev => {
                                              const next = new Set(prev);
                                              next.delete(multiViewKey);
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
                              
                              {/* 展开 Prompt 按钮 - 右边 */}
                              {multiViewPrompt && (() => {
                                const promptKey = `multi-view-${character.id}-${vIdx}`;
                                const isExpanded = expandedPrompts.has(promptKey);
                                
                                return (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        size="sm"
                                        variant="ghost"
                                        className="h-7 w-7 p-0 rounded-full bg-white/90 hover:bg-white dark:bg-card/90 dark:hover:bg-card shadow-sm pointer-events-auto"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          togglePromptExpanded(promptKey);
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
                              const multiViewKey = `multi-view-${character.id}-${vIdx}`;
                              const isRegenerating = regeneratingMultiView.has(multiViewKey);
                              return isRegenerating && (
                                <div className="text-xs text-blue-500 mb-2 text-center">{t('regenerating')}</div>
                              );
                            })()}
                            
                            {/* ⭐ Prompt展示和编辑（显示选中版本的 prompt） */}
                            {multiViewPrompt && (() => {
                              const promptKey = `multi-view-${character.id}-${vIdx}`;
                              const isExpanded = expandedPrompts.has(promptKey);
                              const isEditing = editingPrompts.has(promptKey);
                              const currentPromptValue = promptValues.has(promptKey) ? promptValues.get(promptKey) : multiViewPrompt;
                              
                              return (
                                <div ref={(el) => { promptContainerRefs.current[promptKey] = el; }} className="mt-1 w-full flex justify-center">
                                  {/* Prompt内容 - 展开时显示 */}
                                  {isExpanded && (
                                    <div className="bg-white dark:bg-card border border-gray-200 dark:border-border rounded-lg" style={{ width: '100%', maxWidth: '100%' }}>
                                      {isEditing ? (
                                        <div className="flex flex-col bg-white dark:bg-card">
                                          <Textarea
                                            ref={(el) => {
                                              if (el) {
                                                setTimeout(() => adjustTextareaHeight(el), 0);
                                              }
                                            }}
                                            value={currentPromptValue}
                                            data-prompt-key={promptKey}
                                            onChange={(e) => {
                                              setPromptValues(prev => new Map(prev).set(promptKey, e.target.value));
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
                                                  onClick={() => savePromptEdit(promptKey, character.id)}
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
                          </>
                        ) : (
                          /* 没有多视角图时显示占位 - 保持与角色版本对齐 */
                          <>
                            {/* 版本选择圈圈和标题 - 与角色版本对齐 */}
                            <div className="flex items-center justify-between mb-2 w-full" style={{ minHeight: '20px' }}>
                              <div className="text-xs font-medium text-muted-foreground">
                                {/* 不显示版本标签，与角色版本保持一致 */}
                              </div>
                              {/* 没有多视角图，不显示选择圈圈 */}
                            </div>
                            {/* 占位图片区域 - 与角色版本图片高度一致 */}
                            <div className="flex items-center justify-center" style={{ height: `${scaledMediaHeight}px`, width: `${scaledCardWidth}px` }}>
                              <span className="text-xs text-gray-400">-</span>
                            </div>
                            {/* 占位：版本控制按钮区域 - 与角色版本对齐 */}
                            <div className="flex items-center justify-between mt-2 mb-2 w-full" style={{ minHeight: '28px' }}>
                              {/* 占位：左侧按钮区域 */}
                              <div className="w-7 h-7"></div>
                              {/* 占位：右侧按钮区域 */}
                              <div className="w-7 h-7"></div>
                            </div>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
                  </Card>
                );
              })()}
            </div>
          );
        })}
        </div>
      </div>
      
      {fusionPromptEditor && onCharacterRefinePromptOnly && (
        <VideoArtifactPromptEditorDialog
          open={!!fusionPromptEditor}
          title={fusionPromptEditor.title}
          image={fusionPromptEditor.image}
          initialPrompt={fusionPromptEditor.initialPrompt}
          artifactKind="character"
          threadId={threadId}
          onClose={() => setFusionPromptEditor(null)}
          onRefineInstruction={(instruction) =>
            onCharacterRefinePromptOnly(
              fusionPromptEditor.characterUuid,
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
                fusionPromptEditor.characterUuid,
                newPrompt,
                fusionPromptEditor.versionIndex,
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

      {/* 多视角图选择对话框 */}
      <Dialog open={!!multiViewSelector} onOpenChange={(open) => !open && setMultiViewSelector(null)}>
        <DialogContent className="max-w-6xl max-h-[90vh] overflow-y-auto">
          <div className="space-y-4">
            <h3 className="text-lg font-semibold">{t('selectMultiViewVersion')}</h3>
            <div className="grid grid-cols-2 gap-4">
              {multiViewSelector?.versions.map((mvVersion: any, idx: number) => {
                const isSelected = multiViewSelector.selectedId === mvVersion.id;
                return (
                  <div
                    key={mvVersion.id || idx}
                    className={`relative rounded-lg border-2 cursor-pointer transition-all ${
                      isSelected 
                        ? 'border-blue-500 ring-2 ring-blue-500 ring-offset-2' 
                        : 'border-gray-300 hover:border-blue-400'
                    }`}
                    onClick={() => {
                      if (multiViewSelector) {
                        const characterUuid = multiViewSelector.characterUuid;
                        const versionUuid = multiViewSelector.versionUuid;
                        handleSelectMultiViewVersion(characterUuid, versionUuid, mvVersion.id);
                        setMultiViewSelector(null);
                        toast({
                          title: t('selected'),
                          description: t('versionSelected').replace('{version}', mvVersion.version_number.toString()),
                        });
                      }
                    }}
                  >
                    <img
                      src={mvVersion.multi_view_image_url}
                      alt={`Multi-view v${mvVersion.version_number}`}
                      className="w-full h-auto rounded-lg"
                    />
                    <div className="absolute top-2 right-2 flex items-center gap-2">
                      {isSelected && (
                        <div className="bg-blue-500 text-white rounded-full p-1">
                          <Check className="w-4 h-4" />
                        </div>
                      )}
                      <span className="bg-black/70 text-white px-2 py-1 rounded text-xs">
                        v{mvVersion.version_number}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </DialogContent>
      </Dialog>

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
    </Card>
  );
};
