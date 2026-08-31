import React, { forwardRef, useState, useEffect, useRef, useCallback } from 'react';
import { useLanguage } from '@/i18n/LanguageContext';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Image as ImageIcon, Download, ZoomIn, CheckCircle, Loader2, Scissors, ArrowLeft, Plus, Minus, Share2, X, Copy } from 'lucide-react';
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { splitCollage, downloadBlob, downloadAllTiles, addLogoToImage, type TileInfo } from '@/utils/collageSplitter';
import { toast } from "sonner";
import { MobileSplitCard } from "./MobileSplitCard";

interface Message {
  role: string;
  content: string;
  timestamp?: string;
  event_type?: string;
  event_data?: any;
}

interface ImageResultsPanelProps {
  messages: Message[];
  /** 当前对话是否有任务在 running/queued，有则优先显示「生成中」而非「已完成」（多轮时上一轮已完成仍显示本轮进度） */
  isGenerating?: boolean;
}

interface ImageItem {
  title: string;
  url: string;
  description?: string;
  timestamp?: string;
}

export const ImageResultsPanel = forwardRef<HTMLDivElement, ImageResultsPanelProps>(
  ({ messages, isGenerating: isGeneratingProp }, ref) => {
    const { t, language } = useLanguage();
    const isMobile = useIsMobile();
    const [selectedImage, setSelectedImage] = useState<ImageItem | null>(null);

    // Extract images from messages
    const extractImages = (): ImageItem[] => {
      const images: ImageItem[] = [];

      // Find all image_agent_generated messages
      const imageMessages = messages.filter(
        msg => msg.event_type === 'image_agent_generated' && msg.event_data?.image_content
      );

      imageMessages.forEach(msg => {
        const imageContent = msg.event_data.image_content;
        const timestamp = msg.timestamp;

        // Parse markdown-style image links: ![title](url) or [title](url)
        // Supports both absolute (https://...) and relative (/path/...) URLs
        const regex = /!?\[([^\]]*)\]\(([^\s)]+\.(jpg|jpeg|png|gif|webp)[^\s)]*)\)/gi;
        let match;

        while ((match = regex.exec(imageContent)) !== null) {
          const [, title, url] = match;
          images.push({
            title: title.trim() || t('generatedImage' as any) || 'Generated Image',
            url: url.trim(),
            timestamp
          });
        }

        // Also try to extract direct URLs if no markdown format found
        if (images.length === 0) {
          const urlRegex = /((?:https?:\/\/)?[^\s]+\.(jpg|jpeg|png|gif|webp))/gi;
          let urlMatch;
          let imageIndex = 1;

          while ((urlMatch = urlRegex.exec(imageContent)) !== null) {
            images.push({
              title: `${t('generatedImage' as any) || 'Generated Image'} ${imageIndex}`,
              url: urlMatch[1].trim(),
              timestamp
            });
            imageIndex++;
          }
        }
      });

      return images;
    };

    const imageItems = extractImages();

    // 优先用父组件传入的 isGenerating（按 detail 最新任务 running/queued），避免多轮对话里上一轮已完成却仍显示「已完成」
    const latestTodoMessage = [...messages].reverse().find((msg) => msg.event_type === 'generation_todo');
    const latestUserInput = [...messages].reverse().find((msg) => msg.event_type === 'user_input');
    const latestUserInputHasConfirmed = latestUserInput?.event_data?.has_confirmed === true;
    const imageProgressPercent = Number(latestTodoMessage?.event_data?.image_progress_percent);
    const derivedGenerating = messages.some(
      msg => latestUserInputHasConfirmed &&
             msg.event_type === 'generation_todo' && 
             msg.event_data?.status !== 'cancelled' && 
             msg.event_data?.status !== 'failed'
    ) && !messages.some(msg => msg.event_type === 'image_agent_generated');
    const isGenerating = isGeneratingProp ?? derivedGenerating;

    const hasCompleted = messages.some(msg => msg.event_type === 'image_agent_generated');

    const handleDownload = async (image: ImageItem) => {
      try {
        // 添加 logo 水印后下载
        const { blob } = await addLogoToImage(image.url);
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${image.title.replace(/\s+/g, '_')}.png`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } catch (error) {
        console.error('Failed to download image:', error);
      }
    };

    const handleCopyLink = async () => {
      if (!selectedImage) return;
      try {
        const basePath = (import.meta.env.BASE_URL || '').replace(/\/$/, '');
        const shareUrl = `${window.location.origin}${basePath}/#/${language}/share/image?mode=normal&image=${encodeURIComponent(selectedImage.url)}&title=${encodeURIComponent(selectedImage.title)}`;
        await navigator.clipboard.writeText(shareUrl);
        toast.success(t('copyLinkSuccess'));
      } catch (e) {
        toast.error(String(e));
      }
    };

    const handleShareDirectly = async () => {
      if (!selectedImage) return;
      const shareTitle = selectedImage.title;
      const shareText = t('shareImageDescription') || 'Check out this image';
      const basePath = (import.meta.env.BASE_URL || '').replace(/\/$/, '');
      const shareUrl = `${window.location.origin}${basePath}/#/${language}/share/image?mode=normal&image=${encodeURIComponent(selectedImage.url)}&title=${encodeURIComponent(shareTitle)}`;
      if (navigator.share) {
        try {
          await navigator.share({ title: shareTitle, text: shareText, url: shareUrl });
        } catch (e) {
          if ((e as Error).name !== 'AbortError') {
            try { await navigator.clipboard.writeText(shareUrl); toast.success(t('copyLinkSuccess')); } catch { toast.error(String(e)); }
          }
        }
      } else {
        try { await navigator.clipboard.writeText(shareUrl); toast.success(t('copyLinkSuccess')); } catch (e) { toast.error(String(e)); }
      }
    };

    // ── Split Collage State ──
    const [splitMode, setSplitMode] = useState(false);
    const [splitRows, setSplitRows] = useState(3);
    const [splitCols, setSplitCols] = useState(4);
    const [splitPadding] = useState(0);
    const [previewImgSize, setPreviewImgSize] = useState<{ w: number; h: number } | null>(null);
    const [splitRowDividers, setSplitRowDividers] = useState<number[]>([]);
    const [splitColDividers, setSplitColDividers] = useState<number[]>([]);
    const [splitCropRect, setSplitCropRect] = useState<{ left: number; top: number; right: number; bottom: number }>({ left: 0, top: 0, right: 1, bottom: 1 });
    const [splitCropAspectRatio, setSplitCropAspectRatio] = useState<string | null>(null);
    const [splitTiles, setSplitTiles] = useState<TileInfo[] | null>(null);
    const [previewTile, setPreviewTile] = useState<TileInfo | null>(null);
    const [showShareDialog, setShowShareDialog] = useState(false);
    const [shareMode, setShareMode] = useState<'normal' | 'gift'>('normal');
    const [shareImageUrl, setShareImageUrl] = useState<string | null>(null);
    const [shareImageTitle, setShareImageTitle] = useState<string>('');
    /** 带 logo 水印的分享图 dataUrl */
    const [shareWatermarkedUrl, setShareWatermarkedUrl] = useState<string | null>(null);
    /** 带 logo 水印的分享图 blob，用于 navigator.share files */
    const [shareWatermarkedBlob, setShareWatermarkedBlob] = useState<Blob | null>(null);
    const gridOverlayRef = useRef<HTMLDivElement>(null);
    const [dragging, setDragging] = useState<{ type: 'v' | 'h' | 'left' | 'right' | 'top' | 'bottom' | 'move'; index: number } | null>(null);
    const cropRectMoveStartRef = useRef<{ left: number; top: number; right: number; bottom: number; startX: number; startY: number } | null>(null);
    const [isSplitting, setIsSplitting] = useState(false);
    const [splitError, setSplitError] = useState<string | null>(null);

    const resetSplitState = () => {
      setSplitMode(false);
      setSplitTiles(null);
      setPreviewTile(null);
      setSplitError(null);
      setIsSplitting(false);
      setPreviewImgSize(null);
      setSplitCropRect({ left: 0, top: 0, right: 1, bottom: 1 });
      setSplitCropAspectRatio(null);
      setSplitRowDividers([]);
      setSplitColDividers([]);
      setDragging(null);
    };

    // 分享弹窗打开时，为原图 / tile 生成带 logo 水印的图片
    useEffect(() => {
      if (!showShareDialog) {
        setShareWatermarkedUrl(null);
        setShareWatermarkedBlob(null);
        return;
      }
      let cancelled = false;
      (async () => {
        try {
          if (previewTile) {
            // tile 已经在 splitCollage 时带了水印，直接复用
            if (!cancelled) {
              setShareWatermarkedUrl(previewTile.dataUrl);
              setShareWatermarkedBlob(previewTile.blob);
            }
          } else if (shareImageUrl) {
            const { blob, dataUrl } = await addLogoToImage(shareImageUrl);
            if (!cancelled) {
              setShareWatermarkedUrl(dataUrl);
              setShareWatermarkedBlob(blob);
            }
          }
        } catch (e) {
          console.error('Failed to generate watermarked share image:', e);
        }
      })();
      return () => { cancelled = true; };
    }, [showShareDialog, shareImageUrl, previewTile]);

    useEffect(() => {
      if (!selectedImage) setPreviewImgSize(null);
    }, [selectedImage?.url]);

    useEffect(() => {
      if (splitRows <= 1 && splitCols <= 1) {
        setSplitCropRect({ left: 0, top: 0, right: 1, bottom: 1 });
        setSplitCropAspectRatio(null);
        setSplitRowDividers([]);
        setSplitColDividers([]);
      } else {
        setSplitRowDividers(splitRows > 1 ? Array.from({ length: splitRows - 1 }, (_, i) => (i + 1) / splitRows) : []);
        setSplitColDividers(splitCols > 1 ? Array.from({ length: splitCols - 1 }, (_, i) => (i + 1) / splitCols) : []);
      }
    }, [splitRows, splitCols]);

    const handleDragMove = useCallback((e: MouseEvent) => {
      if (!dragging || !gridOverlayRef.current) return;
      const rect = gridOverlayRef.current.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
      if (dragging.type === 'v') {
        setSplitColDividers((prev) => {
          const next = [...prev];
          next[dragging.index] = x;
          next.sort((a, b) => a - b);
          return next;
        });
      } else if (dragging.type === 'h') {
        setSplitRowDividers((prev) => {
          const next = [...prev];
          next[dragging.index] = y;
          next.sort((a, b) => a - b);
          return next;
        });
      } else if (dragging.type === 'left') setSplitCropRect((r) => ({ ...r, left: Math.min(x, r.right - 0.05) }));
      else if (dragging.type === 'right') setSplitCropRect((r) => ({ ...r, right: Math.max(x, r.left + 0.05) }));
      else if (dragging.type === 'top') setSplitCropRect((r) => ({ ...r, top: Math.min(y, r.bottom - 0.05) }));
      else if (dragging.type === 'bottom') setSplitCropRect((r) => ({ ...r, bottom: Math.max(y, r.top + 0.05) }));
      else if (dragging.type === 'move') {
        const start = cropRectMoveStartRef.current;
        if (!start) return;
        const w = start.right - start.left;
        const h = start.bottom - start.top;
        let newLeft = start.left + (x - start.startX);
        let newTop = start.top + (y - start.startY);
        let newRight = newLeft + w;
        let newBottom = newTop + h;
        if (newLeft < 0) {
          newRight -= newLeft;
          newLeft = 0;
        }
        if (newRight > 1) {
          newLeft -= newRight - 1;
          newRight = 1;
        }
        if (newTop < 0) {
          newBottom -= newTop;
          newTop = 0;
        }
        if (newBottom > 1) {
          newTop -= newBottom - 1;
          newBottom = 1;
        }
        setSplitCropRect({ left: newLeft, top: newTop, right: newRight, bottom: newBottom });
      }
    }, [dragging]);

    const handleDragEnd = useCallback(() => {
      setDragging(null);
      cropRectMoveStartRef.current = null;
    }, []);

    useEffect(() => {
      if (!dragging) return;
      window.addEventListener('mousemove', handleDragMove);
      window.addEventListener('mouseup', handleDragEnd);
      return () => {
        window.removeEventListener('mousemove', handleDragMove);
        window.removeEventListener('mouseup', handleDragEnd);
      };
    }, [dragging, handleDragMove, handleDragEnd]);

    const handleSplitCollage = async () => {
      if (!selectedImage) return;
      setIsSplitting(true);
      setSplitError(null);
      try {
        const opts: Parameters<typeof splitCollage>[1] = {
          rows: splitRows,
          cols: splitCols,
          padding: splitPadding,
          watermark: {
            url: 'watermark.png',
            scale: 0.2,
            margin: 10,
            opacity: 0.8,
          },
        };
        if (splitRows === 1 && splitCols === 1) {
          opts.cropRect = splitCropRect;
        } else if (splitRowDividers.length === splitRows - 1 && splitColDividers.length === splitCols - 1) {
          opts.rowDividers = [...splitRowDividers];
          opts.colDividers = [...splitColDividers];
        }
        const tiles = await splitCollage(selectedImage.url, opts);
        setSplitTiles(tiles);
      } catch (error) {
        console.error('Failed to split collage:', error);
        setSplitError(
          error instanceof Error && error.message.includes('cross-origin')
            ? (t('splitCorsError' as any) || 'Image does not support cross-origin access. Try downloading the image first.')
            : (t('splitError' as any) || 'Failed to split image. Please try again.')
        );
      } finally {
        setIsSplitting(false);
      }
    };

    const handleDownloadTile = (tile: TileInfo) => {
      const baseName = selectedImage?.title.replace(/\s+/g, '_') || 'image';
      downloadBlob(tile.blob, `${baseName}_${tile.index + 1}.png`);
    };

    const handleDownloadAllTiles = async () => {
      if (!splitTiles) return;
      const baseName = selectedImage?.title.replace(/\s+/g, '_') || 'image';
      await downloadAllTiles(splitTiles, baseName);
    };

    const GRID_PRESETS = [
      { label: '1×1', rows: 1, cols: 1 },
      { label: '2×3', rows: 2, cols: 3 },
      { label: '3×5', rows: 3, cols: 5 },
    ];

    const CROP_ASPECT_RATIOS: { label: string; value: number | null }[] = [
      { label: '自由', value: null },
      { label: '1:1', value: 1 },
      { label: '2:3', value: 2 / 3 },
      { label: '3:2', value: 3 / 2 },
      { label: '16:9', value: 16 / 9 },
      { label: '9:16', value: 9 / 16 },
    ];

    const applyCropAspectRatio = (ratioValue: number | null) => {
      if (ratioValue === null) {
        setSplitCropRect({ left: 0, top: 0, right: 1, bottom: 1 });
        return;
      }
      const imgW = previewImgSize?.w ?? 1;
      const imgH = previewImgSize?.h ?? 1;
      const r = ratioValue * imgH / imgW;
      let w: number, h: number;
      if (r >= 1) {
        w = 1;
        h = Math.min(1, 1 / r);
      } else {
        h = 1;
        w = Math.min(1, r);
      }
      const left = Math.max(0, 0.5 - w / 2);
      const right = Math.min(1, 0.5 + w / 2);
      const top = Math.max(0, 0.5 - h / 2);
      const bottom = Math.min(1, 0.5 + h / 2);
      setSplitCropRect({ left, top, right, bottom });
    };

    return (
      <ScrollArea ref={ref} className="flex-1 min-h-0 h-full">
        <div className="p-6 space-y-6">
          {/* Image Gallery */}
          {imageItems.length > 0 && (
            <Card className="border-white/20 dark:border-gray-700/50 bg-white/50 dark:bg-gray-800/50 backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <ImageIcon className="w-5 h-5 text-accent-purple" />
                  {t('generatedImages') || 'Generated Images'}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 gap-6">
                  {imageItems.map((image, index) => (
                    <div 
                      key={index} 
                      className="group relative space-y-3 animate-fade-in"
                      style={{ animationDelay: `${index * 100}ms` }}
                    >
                      <div className="relative flex max-h-[calc(100dvh-14rem)] w-full min-w-0 items-center justify-center overflow-hidden rounded-lg border border-white/20 bg-black/5 shadow-lg transition-shadow duration-300 hover:shadow-xl dark:border-gray-700/50 dark:bg-black/20">
                        <img
                          src={image.url}
                          alt={image.title}
                          className="block h-auto max-h-[calc(100dvh-14rem)] max-w-full object-contain"
                          loading="lazy"
                        />
                        
                        {/* Overlay with actions */}
                        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-all duration-300 flex items-center justify-center gap-2 opacity-0 group-hover:opacity-100">
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setSelectedImage(image)}
                            className="bg-white/90 hover:bg-white text-black backdrop-blur-sm"
                          >
                            <ZoomIn className="w-4 h-4 mr-1" />
                            {t('view' as any) || 'View'}
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleDownload(image)}
                            className="bg-white/90 hover:bg-white text-black backdrop-blur-sm"
                          >
                            <Download className="w-4 h-4 mr-1" />
                            {t('download') || 'Download'}
                          </Button>
                        </div>
                      </div>
                      
                      <div className="space-y-1">
                        <h3 className="text-sm font-medium text-foreground dark:text-gray-200 line-clamp-1">
                          {image.title}
                        </h3>
                        {image.timestamp && (
                          <p className="text-xs text-muted-foreground">
                            {new Date(image.timestamp).toLocaleString()}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Placeholder when no images yet */}
          {!isGenerating && imageItems.length === 0 && (
            <Card className="border-white/20 dark:border-gray-700/50 bg-white/50 dark:bg-gray-800/50 backdrop-blur-sm">
              <CardContent className="flex flex-col items-center justify-center py-12">
                <ImageIcon className="w-16 h-16 text-muted-foreground/30 mb-4" />
                <p className="text-sm text-muted-foreground text-center">
                  {t('noImagesYet') || 'No images generated yet'}
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Image Preview Dialog */}
        <Dialog open={!!selectedImage} onOpenChange={() => { setSelectedImage(null); resetSplitState(); setShowShareDialog(false); setShareImageUrl(null); setShareImageTitle(''); }}>
          <DialogContent className={isMobile ? "max-w-none w-full h-full sm:max-w-4xl sm:h-auto sm:max-h-[90vh] p-0 overflow-hidden rounded-none sm:rounded-lg flex flex-col bg-black" : "max-w-4xl w-full p-0 max-h-[90vh] overflow-y-auto"} hideCloseButton={splitMode || isMobile}>
            {isMobile && selectedImage && <DialogTitle className="sr-only">{selectedImage.title}</DialogTitle>}
            {isMobile && selectedImage ? (
              /* 移动端：全屏风格 - 顶部返回按钮、大图、右侧操作列、底部双按钮 */
              <>
                {/* 顶部返回按钮（图片上方） */}
                <div className="shrink-0 flex items-center px-4 py-3 border-b border-white/10">
                  <button type="button" onClick={() => { if (splitMode) resetSplitState(); else { setSelectedImage(null); resetSplitState(); } }} className="p-2 rounded-full hover:bg-white/10 text-white flex items-center justify-center" aria-label={t('back' as any) || 'Back'}>
                    <ArrowLeft className="w-5 h-5" />
                  </button>
                </div>
                {/* 主内容区：大图 + 右侧操作列 */}
                <div className="flex-1 min-h-0 flex relative">
                  {!splitMode ? (
                    <>
                      <div className="absolute inset-0 flex items-center justify-center bg-black">
                        <img src={selectedImage.url} alt={selectedImage.title} className="max-w-full max-h-full w-auto h-auto object-contain" />
                      </div>
                      {/* 右侧竖排操作：图标+文字，与关闭按钮横向居中对齐 */}
                      <div className="absolute right-3 top-[34px] flex flex-col gap-5 z-10">
                        <button type="button" onClick={() => setSplitMode(true)} className="flex flex-col items-center gap-1 text-white hover:opacity-80">
                          <Scissors className="w-5 h-5" />
                          <span className="text-xs">{t('splitCollage')}</span>
                        </button>
                      </div>
                    </>
                  ) : (
                    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
                      <div className="flex justify-center rounded-lg bg-black/20 overflow-hidden relative">
                        <div className="relative inline-block">
                          <img src={selectedImage.url} alt={selectedImage.title} className="max-w-full max-h-[40vh] block" />
                          {/* 分割线预览覆盖层 */}
                          {splitRows > 1 || splitCols > 1 ? (
                            <div className="absolute inset-0 pointer-events-none">
                              {Array.from({ length: splitCols - 1 }).map((_, i) => (
                                <div key={`v-${i}`} className="absolute top-0 bottom-0 flex items-center flex-shrink-0" style={{ left: `${((i + 1) / splitCols) * 100}%` }}>
                                  <div className="absolute left-0 w-[3px] sm:w-[2px] min-w-[3px] sm:min-w-[2px] h-full bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.6)] shrink-0" style={{ marginLeft: '-1.5px', transform: 'translateZ(0)' }} />
                                </div>
                              ))}
                              {Array.from({ length: splitRows - 1 }).map((_, i) => (
                                <div key={`h-${i}`} className="absolute left-0 right-0 flex justify-center flex-shrink-0" style={{ top: `${((i + 1) / splitRows) * 100}%` }}>
                                  <div className="absolute top-0 w-full h-[3px] sm:h-[2px] min-h-[3px] sm:min-h-[2px] bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.6)] shrink-0" style={{ marginTop: '-1.5px', transform: 'translateZ(0)' }} />
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      </div>
                      <MobileSplitCard
                        gridPresets={GRID_PRESETS}
                        splitRows={splitRows}
                        splitCols={splitCols}
                        onRowsChange={(rows) => setSplitRows(rows)}
                        onColsChange={(cols) => setSplitCols(cols)}
                        onPresetSelect={(rows, cols) => { setSplitRows(rows); setSplitCols(cols); }}
                        onSplit={handleSplitCollage}
                        isSplitting={isSplitting}
                        splitError={splitError}
                        splitTiles={splitTiles}
                        onTileClick={setPreviewTile}
                        imageTitle={selectedImage.title}
                        onBack={resetSplitState}
                        hideBackButton
                      />
                    </div>
                  )}
                </div>
                {/* 底部双按钮：保存(白色)、分享给好友(粉紫色) */}
                {!splitMode && (
                  <div className="shrink-0 flex gap-3 px-4 py-4 pb-6 border-t border-white/10">
                    <Button type="button" className="flex-1 h-12 bg-white text-gray-900 hover:bg-gray-50 rounded-lg flex items-center justify-center gap-2" onClick={() => handleDownload(selectedImage)}>
                      <Download className="w-4 h-4" />
                      <span>{t('save')}</span>
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button type="button" className="flex-1 h-12 bg-gradient-to-r from-pink-500 to-purple-500 text-white hover:from-pink-600 hover:to-purple-600 rounded-lg flex items-center justify-center gap-2">
                          <Share2 className="w-4 h-4" />
                          <span>{t('shareToFriends')}</span>
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent 
                        align="end" 
                        side="top"
                        className="w-56 bg-white/95 dark:bg-gray-900/95 backdrop-blur-md border-0 shadow-2xl rounded-xl p-2 min-w-[200px]"
                        sideOffset={8}
                      >
                        <DropdownMenuItem 
                          onClick={handleCopyLink} 
                          className="cursor-pointer rounded-lg px-4 py-3 text-base hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                        >
                          <Copy className="w-5 h-5 mr-3 text-gray-600 dark:text-gray-400" />
                          <span className="font-medium">{t('copyLink')}</span>
                        </DropdownMenuItem>
                        <DropdownMenuItem 
                          onClick={handleShareDirectly} 
                          className="cursor-pointer rounded-lg px-4 py-3 text-base hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                        >
                          <Share2 className="w-5 h-5 mr-3 text-gray-600 dark:text-gray-400" />
                          <span className="font-medium">{t('shareDirectly')}</span>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                )}
              </>
            ) : (
              <>
                {!isMobile && (
                  <DialogHeader className="p-6 pb-4 relative">
                    {splitMode && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                        onClick={() => resetSplitState()}
                      >
                        <ArrowLeft className="h-4 w-4" />
                        <span className="sr-only">{t('back' as any) || 'Back'}</span>
                      </Button>
                    )}
                    <DialogTitle className="text-lg font-semibold flex items-center gap-2">
                      {splitMode ? (
                        <>
                          <Scissors className="w-5 h-5" />
                          {splitTiles
                            ? (t('splitResults' as any) || 'Split Results')
                            : (t('splitCollage' as any) || 'Split Collage')}
                        </>
                      ) : (
                        <>
                          <ImageIcon className="w-5 h-5" />
                          {selectedImage?.title}
                        </>
                      )}
                    </DialogTitle>
                  </DialogHeader>
                )}
            
            {selectedImage && !isMobile && (
              <div className="px-6 pb-6 space-y-4">

                {/* ── Image Preview (with grid overlay in split config mode) ── */}
                {!splitTiles && (
                  <div className="flex justify-center rounded-lg bg-black/5 dark:bg-black/20 overflow-hidden">
                    <div ref={gridOverlayRef} className="relative inline-flex">
                      <img
                        src={selectedImage.url}
                        alt={selectedImage.title}
                        className="max-w-full max-h-[55vh] block"
                        onLoad={(e) => {
                          const img = e.currentTarget;
                          if (img.naturalWidth && img.naturalHeight) {
                            setPreviewImgSize({ w: img.naturalWidth, h: img.naturalHeight });
                          }
                        }}
                      />
                      {/* Grid overlay：1×1 为 cropRect 四边可拖，N×M 为分割线可拖 */}
                      {splitMode && (() => {
                        const is1x1 = splitRows === 1 && splitCols === 1;
                        const rowBounds = [0, ...splitRowDividers.slice().sort((a, b) => a - b), 1];
                        const colBounds = [0, ...splitColDividers.slice().sort((a, b) => a - b), 1];
                        const handleStyleV = 'cursor-col-resize';
                        const handleStyleH = 'cursor-row-resize';
                        return (
                          <div className="absolute inset-0">
                            {is1x1 ? (
                              <>
                                <div className="absolute border-2 border-red-500 dark:border-red-400 shadow-[0_0_0_1px_rgba(0,0,0,0.1)] dark:shadow-[0_0_0_1px_rgba(255,255,255,0.2)] pointer-events-none box-border min-w-0 min-h-0" style={{ left: `${splitCropRect.left * 100}%`, top: `${splitCropRect.top * 100}%`, width: `${(splitCropRect.right - splitCropRect.left) * 100}%`, height: `${(splitCropRect.bottom - splitCropRect.top) * 100}%`, transform: 'translateZ(0)' }} />
                                <div
                                  className="absolute cursor-move"
                                  style={{
                                    left: `${(splitCropRect.left + 0.02) * 100}%`,
                                    top: `${(splitCropRect.top + 0.02) * 100}%`,
                                    width: `${Math.max(0, (splitCropRect.right - splitCropRect.left - 0.04)) * 100}%`,
                                    height: `${Math.max(0, (splitCropRect.bottom - splitCropRect.top - 0.04)) * 100}%`,
                                  }}
                                  onMouseDown={(e) => {
                                    e.preventDefault();
                                    const el = gridOverlayRef.current;
                                    if (!el) return;
                                    const r = el.getBoundingClientRect();
                                    const startX = (e.clientX - r.left) / r.width;
                                    const startY = (e.clientY - r.top) / r.height;
                                    cropRectMoveStartRef.current = { ...splitCropRect, startX, startY };
                                    setDragging({ type: 'move', index: 0 });
                                  }}
                                />
                                <div className={`absolute top-0 bottom-0 w-2 -ml-1 bg-red-500/90 dark:bg-red-400/90 hover:bg-red-500 dark:hover:bg-red-400 shadow-sm ${handleStyleV}`} style={{ left: `${splitCropRect.left * 100}%` }} onMouseDown={(e) => { e.preventDefault(); setDragging({ type: 'left', index: 0 }); }} />
                                <div className={`absolute top-0 bottom-0 w-2 -ml-1 bg-red-500/90 dark:bg-red-400/90 hover:bg-red-500 dark:hover:bg-red-400 shadow-sm ${handleStyleV}`} style={{ left: `${splitCropRect.right * 100}%` }} onMouseDown={(e) => { e.preventDefault(); setDragging({ type: 'right', index: 0 }); }} />
                                <div className={`absolute left-0 right-0 h-2 -mt-1 bg-red-500/90 dark:bg-red-400/90 hover:bg-red-500 dark:hover:bg-red-400 shadow-sm ${handleStyleH}`} style={{ top: `${splitCropRect.top * 100}%` }} onMouseDown={(e) => { e.preventDefault(); setDragging({ type: 'top', index: 0 }); }} />
                                <div className={`absolute left-0 right-0 h-2 -mt-1 bg-red-500/90 dark:bg-red-400/90 hover:bg-red-500 dark:hover:bg-red-400 shadow-sm ${handleStyleH}`} style={{ top: `${splitCropRect.bottom * 100}%` }} onMouseDown={(e) => { e.preventDefault(); setDragging({ type: 'bottom', index: 0 }); }} />
                                <div className="absolute flex items-center justify-center pointer-events-none" style={{ left: `${splitCropRect.left * 100}%`, top: `${splitCropRect.top * 100}%`, width: `${(splitCropRect.right - splitCropRect.left) * 100}%`, height: `${(splitCropRect.bottom - splitCropRect.top) * 100}%` }}>
                                  <span className="bg-black/70 dark:bg-white/90 text-white dark:text-black text-xs font-bold rounded-full w-6 h-6 flex items-center justify-center shadow-md">1</span>
                                </div>
                              </>
                            ) : (
                              <>
                                {Array.from({ length: splitCols - 1 }).map((_, i) => (
                                  <div key={`v-${i}`} className="absolute top-0 bottom-0 flex items-center flex-shrink-0" style={{ left: `${(splitColDividers[i] ?? (i + 1) / splitCols) * 100}%` }}>
                                    <div className="absolute left-0 w-[3px] sm:w-[2px] min-w-[3px] sm:min-w-[2px] h-full bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.6)] pointer-events-none shrink-0" style={{ marginLeft: '-1.5px', transform: 'translateZ(0)' }} />
                                    <div className={`absolute -ml-2 w-4 h-full ${handleStyleV}`} onMouseDown={(e) => { e.preventDefault(); setDragging({ type: 'v', index: i }); }} />
                                  </div>
                                ))}
                                {Array.from({ length: splitRows - 1 }).map((_, i) => (
                                  <div key={`h-${i}`} className="absolute left-0 right-0 flex justify-center flex-shrink-0" style={{ top: `${(splitRowDividers[i] ?? (i + 1) / splitRows) * 100}%` }}>
                                    <div className="absolute top-0 w-full h-[3px] sm:h-[2px] min-h-[3px] sm:min-h-[2px] bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.6)] pointer-events-none shrink-0" style={{ marginTop: '-1.5px', transform: 'translateZ(0)' }} />
                                    <div className={`absolute -mt-2 w-full h-4 ${handleStyleH}`} onMouseDown={(e) => { e.preventDefault(); setDragging({ type: 'h', index: i }); }} />
                                  </div>
                                ))}
                                {Array.from({ length: splitRows }).map((_, r) =>
                                  Array.from({ length: splitCols }).map((_, c) => {
                                    const left = (colBounds[c] ?? c / splitCols) * 100;
                                    const top = (rowBounds[r] ?? r / splitRows) * 100;
                                    const w = ((colBounds[c + 1] ?? (c + 1) / splitCols) - (colBounds[c] ?? c / splitCols)) * 100;
                                    const h = ((rowBounds[r + 1] ?? (r + 1) / splitRows) - (rowBounds[r] ?? r / splitRows)) * 100;
                                    return (
                                      <div key={`n-${r}-${c}`} className="absolute flex items-center justify-center pointer-events-none" style={{ left: `${left}%`, top: `${top}%`, width: `${w}%`, height: `${h}%` }}>
                                        <span className="bg-black/70 dark:bg-white/90 text-white dark:text-black text-xs font-bold rounded-full w-6 h-6 flex items-center justify-center shadow-md">{r * splitCols + c + 1}</span>
                                      </div>
                                    );
                                  })
                                )}
                              </>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  </div>
                )}

                {/* ── Split Configuration Panel ── */}
                {splitMode && !splitTiles && (
                  <div className="space-y-4 p-4 rounded-lg border border-white/20 dark:border-gray-700/50 bg-white/30 dark:bg-gray-800/30">
                    {/* Presets */}
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-foreground">
                        {t('gridPresets' as any) || 'Grid Presets'}
                      </label>
                      <div className="flex flex-wrap gap-2">
                        {GRID_PRESETS.map((preset) => (
                          <button
                            key={preset.label}
                            onClick={() => { setSplitRows(preset.rows); setSplitCols(preset.cols); }}
                            className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
                              splitRows === preset.rows && splitCols === preset.cols
                                ? 'bg-primary text-primary-foreground border-primary'
                                : 'bg-white/50 dark:bg-gray-700/50 border-white/20 dark:border-gray-600 hover:bg-white/80 dark:hover:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                            }`}
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* 1×1 长宽比选项 */}
                    {splitRows === 1 && splitCols === 1 && (
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-foreground">
                          {t('cropAspectRatio')}
                        </label>
                        <div className="flex flex-wrap gap-2">
                          {CROP_ASPECT_RATIOS.map((opt) => (
                            <button
                              key={opt.label}
                              onClick={() => {
                                setSplitCropAspectRatio(opt.label);
                                applyCropAspectRatio(opt.value);
                              }}
                              className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
                                splitCropAspectRatio === opt.label || (opt.value === null && splitCropAspectRatio === null)
                                  ? 'bg-primary text-primary-foreground border-primary'
                                  : 'bg-white/50 dark:bg-gray-700/50 border-white/20 dark:border-gray-600 hover:bg-white/80 dark:hover:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                              }`}
                            >
                              {opt.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Custom Rows / Cols / Padding */}
                    <div className="flex flex-wrap gap-6">
                      {/* Rows */}
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-muted-foreground w-12">
                          {t('rows' as any) || 'Rows'}
                        </span>
                        <Button
                          variant="outline" size="icon" className="w-7 h-7 border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600"
                          onClick={() => setSplitRows(Math.max(1, splitRows - 1))}
                          disabled={splitRows <= 1}
                        >
                          <Minus className="w-3 h-3 text-gray-900 dark:text-gray-100" />
                        </Button>
                        <span className="text-sm font-medium w-6 text-center text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-700 px-2 py-1 rounded">{splitRows}</span>
                        <Button
                          variant="outline" size="icon" className="w-7 h-7 border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600"
                          onClick={() => setSplitRows(Math.min(10, splitRows + 1))}
                          disabled={splitRows >= 10}
                        >
                          <Plus className="w-3 h-3 text-gray-900 dark:text-gray-100" />
                        </Button>
                      </div>
                      {/* Cols */}
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-muted-foreground w-12">
                          {t('cols' as any) || 'Cols'}
                        </span>
                        <Button
                          variant="outline" size="icon" className="w-7 h-7 border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600"
                          onClick={() => setSplitCols(Math.max(1, splitCols - 1))}
                          disabled={splitCols <= 1}
                        >
                          <Minus className="w-3 h-3 text-gray-900 dark:text-gray-100" />
                        </Button>
                        <span className="text-sm font-medium w-6 text-center text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-700 px-2 py-1 rounded">{splitCols}</span>
                        <Button
                          variant="outline" size="icon" className="w-7 h-7 border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600"
                          onClick={() => setSplitCols(Math.min(10, splitCols + 1))}
                          disabled={splitCols >= 10}
                        >
                          <Plus className="w-3 h-3 text-gray-900 dark:text-gray-100" />
                        </Button>
                      </div>
                    </div>

                    {/* Split button */}
                    <Button
                      className="w-full"
                      onClick={handleSplitCollage}
                      disabled={isSplitting}
                    >
                      {isSplitting ? (
                        <>
                          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                          {t('splitting' as any) || 'Splitting...'}
                        </>
                      ) : (
                        <>
                          <Scissors className="w-4 h-4 mr-2" />
                          {t('splitNow' as any) || `Split into ${splitRows * splitCols} images`}
                        </>
                      )}
                    </Button>
                  </div>
                )}

                {/* ── Split Results Grid ── */}
                {splitTiles && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">
                        {splitTiles.length} {t('imagesSplit' as any) || 'images split successfully'}
                      </span>
                      <Button size="sm" onClick={handleDownloadAllTiles}>
                        <Download className="w-4 h-4 mr-2" />
                        {t('downloadAll' as any) || `Download All (${splitTiles.length})`}
                      </Button>
                    </div>
                    <div
                      className="grid gap-3"
                      style={{ gridTemplateColumns: `repeat(${splitCols}, 1fr)` }}
                    >
                      {splitTiles.map((tile) => (
                        <div
                          key={tile.index}
                          className="group relative rounded-lg overflow-hidden border border-white/20 dark:border-gray-700/50 bg-black/5 dark:bg-black/20 cursor-pointer hover:ring-2 hover:ring-primary/50 transition-all"
                          onClick={() => setPreviewTile(tile)}
                          title={t('viewLargeImage' as any) || 'Click to view large image'}
                        >
                          <img
                            src={tile.dataUrl}
                            alt={`Tile ${tile.index + 1}`}
                            className="w-full h-full object-contain"
                          />
                          {/* Tile number badge */}
                          <span className="absolute top-1 left-1 bg-black/60 text-white text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center">
                            {tile.index + 1}
                          </span>
                          {/* View large overlay on hover */}
                          <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-all duration-200 flex items-center justify-center opacity-0 group-hover:opacity-100">
                            <ZoomIn className="w-5 h-5 text-white drop-shadow-lg" />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* ── Error Message ── */}
                {splitError && (
                  <div className="text-sm text-red-500 dark:text-red-400 bg-red-500/10 rounded-lg p-3">
                    {splitError}
                  </div>
                )}

                {/* ── Action Buttons ── */}
                <div className="flex justify-between items-center gap-2">
                  {splitMode ? (
                    <>
                      <Button variant="outline" onClick={resetSplitState}>
                        <ArrowLeft className="w-4 h-4 mr-2" />
                        {t('back' as any) || 'Back'}
                      </Button>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          onClick={() => {
                            setShareImageUrl(selectedImage.url);
                            setShareImageTitle(selectedImage.title);
                            setShowShareDialog(true);
                          }}
                        >
                          <Share2 className="w-4 h-4 mr-2" />
                          {t('shareToFriends')}
                        </Button>
                      </div>
                    </>
                  ) : (
                    <>
                      <Button variant="outline" onClick={() => setSplitMode(true)}>
                        <Scissors className="w-4 h-4 mr-2" />
                        {t('splitCollage' as any) || 'Split Collage'}
                      </Button>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          onClick={() => {
                            setShareImageUrl(selectedImage.url);
                            setShareImageTitle(selectedImage.title);
                            setShowShareDialog(true);
                          }}
                        >
                          <Share2 className="w-4 h-4 mr-2" />
                          {t('shareToFriends')}
                        </Button>
                        <Button onClick={() => handleDownload(selectedImage)}>
                          <Download className="w-4 h-4 mr-2" />
                          {t('download') || 'Download'}
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* Tile large preview dialog (after crop/split) */}
        <Dialog open={!!previewTile} onOpenChange={(open) => { if (!open) { setPreviewTile(null); setShowShareDialog(false); setShareMode('normal'); } }}>
          <DialogContent className="max-w-[95vw] max-h-[95vh] w-auto p-0 overflow-hidden flex flex-col items-center">
            <DialogHeader className="p-4 pb-0 shrink-0">
              <DialogTitle className="text-base">
                {previewTile ? `#${previewTile.index + 1}` : ''}
              </DialogTitle>
            </DialogHeader>
            {previewTile && (
              <>
                <div className="flex-1 overflow-auto p-4 flex items-center justify-center bg-black/5 dark:bg-black/20 min-h-0">
                  <img
                    src={previewTile.dataUrl}
                    alt={`Tile ${previewTile.index + 1}`}
                    className="max-w-full max-h-[70vh] w-auto h-auto object-contain"
                  />
                </div>
                <div className="p-4 flex justify-end gap-2 border-t">
                  <Button size="sm" variant="outline" onClick={() => setShowShareDialog(true)}>
                    <Share2 className="w-4 h-4 mr-2" />
                    {t('shareToFriends')}
                  </Button>
                  <Button size="sm" onClick={() => { handleDownloadTile(previewTile); }}>
                    <Download className="w-4 h-4 mr-2" />
                    {t('download') || 'Download'}
                  </Button>
                </div>
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* Share dialog: 普通版 / 送礼版 tabs, 复制链接 / 直接分享 */}
        <Dialog open={showShareDialog && (!!previewTile || !!shareImageUrl)} onOpenChange={(open) => { setShowShareDialog(open); if (!open) { setShareMode('normal'); setShareImageUrl(null); setShareImageTitle(''); setShareWatermarkedUrl(null); setShareWatermarkedBlob(null); } }}>
          <DialogContent className="max-w-md p-0 overflow-hidden">
            <DialogHeader className="p-4 pb-2">
              <DialogTitle className="text-base">{t('shareToFriends')}</DialogTitle>
            </DialogHeader>
            {(previewTile || shareImageUrl) && (
              <>
                <Tabs value={shareMode} onValueChange={(v) => setShareMode(v as 'normal' | 'gift')} className="px-4">
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="normal">{t('shareModeNormal')}</TabsTrigger>
                    <TabsTrigger value="gift">{t('shareModeGift')}</TabsTrigger>
                  </TabsList>
                  <TabsContent value="normal" className="mt-3">
                    <div className="rounded-lg border bg-muted/30 p-4 flex flex-col items-center">
                      {shareWatermarkedUrl ? (
                        <>
                          <img src={shareWatermarkedUrl} alt={previewTile ? `#${previewTile.index + 1}` : shareImageTitle} className="max-h-48 w-auto object-contain rounded" />
                          <span className="text-xs text-muted-foreground mt-2">{previewTile ? `#${previewTile.index + 1}` : shareImageTitle}</span>
                        </>
                      ) : (
                        <div className="flex items-center justify-center py-6">
                          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                        </div>
                      )}
                    </div>
                  </TabsContent>
                  <TabsContent value="gift" className="mt-3">
                    <div className="rounded-lg border-2 border-amber-200 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/30 p-4 flex flex-col items-center relative overflow-hidden">
                      <div className="absolute top-2 right-4 text-amber-600 dark:text-amber-400 text-xs font-medium">{t('giftForYou')}</div>
                      {shareWatermarkedUrl ? (
                        <>
                          <img src={shareWatermarkedUrl} alt={previewTile ? `#${previewTile.index + 1}` : shareImageTitle} className="max-h-48 w-auto object-contain rounded shadow-md" />
                          <span className="text-xs text-muted-foreground mt-2">{previewTile ? `#${previewTile.index + 1}` : shareImageTitle}</span>
                        </>
                      ) : (
                        <div className="flex items-center justify-center py-6">
                          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                        </div>
                      )}
                    </div>
                  </TabsContent>
                </Tabs>
                <div className="p-4 pt-2 flex justify-end gap-2 border-t">
                  <Button size="sm" variant="outline" onClick={async () => {
                    try {
                      if (previewTile && selectedImage) {
                        // 裁剪后的 tile：构建包含裁剪参数的分享链接
                        const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');
                        const currentLang = language;
                        const shareTitle = selectedImage.title ? `${selectedImage.title} #${previewTile.index + 1}` : `Tile #${previewTile.index + 1}`;
                        const params = new URLSearchParams({
                          mode: shareMode,
                          image: selectedImage.url,
                          title: shareTitle,
                          rows: String(splitRows),
                          cols: String(splitCols),
                          tile: String(previewTile.index + 1),
                        });
                        if (splitPadding > 0) {
                          params.set('padding', String(splitPadding));
                        }
                        if (splitRows === 1 && splitCols === 1 && splitCropRect) {
                          // 1x1 自定义裁剪
                          params.set('crop', `${splitCropRect.left.toFixed(3)},${splitCropRect.top.toFixed(3)},${splitCropRect.right.toFixed(3)},${splitCropRect.bottom.toFixed(3)}`);
                        } else if (splitRowDividers.length > 0 || splitColDividers.length > 0) {
                          // 自定义分割线
                          if (splitRowDividers.length > 0) {
                            params.set('rd', splitRowDividers.map(d => d.toFixed(3)).join(','));
                          }
                          if (splitColDividers.length > 0) {
                            params.set('cd', splitColDividers.map(d => d.toFixed(3)).join(','));
                          }
                        }
                        const shareUrl = `${window.location.origin}${basePath}/#/${currentLang}/share/image?${params.toString()}`;
                        await navigator.clipboard.writeText(shareUrl);
                        toast.success(t('copyLinkSuccess'));
                      } else if (shareImageUrl) {
                        const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');
                        const currentLang = language;
                        const shareTitle = shareImageTitle || t('generatedImage') || 'Generated Image';
                        const shareUrl = `${window.location.origin}${basePath}/#/${currentLang}/share/image?mode=${shareMode}&image=${encodeURIComponent(shareImageUrl)}&title=${encodeURIComponent(shareTitle)}`;
                        await navigator.clipboard.writeText(shareUrl);
                        toast.success(t('copyLinkSuccess'));
                      }
                    } catch (e) {
                      toast.error(String(e));
                    }
                  }}>
                    {t('copyLink')}
                  </Button>
                  <Button size="sm" onClick={async () => {
                    const shareTitle = previewTile
                      ? (selectedImage?.title ? `${selectedImage.title} #${previewTile.index + 1}` : `Tile #${previewTile.index + 1}`)
                      : (shareImageTitle || t('generatedImage') || 'Generated Image');
                    const shareText = shareMode === 'gift'
                      ? (t('giftForYou') || 'A gift for you')
                      : (t('shareImageDescription') || 'Check out this amazing image');

                    // 优先用带水印的图片文件直接分享
                    if (shareWatermarkedBlob && navigator.share) {
                      const file = new File([shareWatermarkedBlob], `${shareTitle.replace(/\s+/g, '_')}.png`, { type: 'image/png' });
                      if (navigator.canShare?.({ files: [file] })) {
                        try {
                          await navigator.share({ title: shareTitle, text: shareText, files: [file] });
                          setShowShareDialog(false);
                          return;
                        } catch (e) {
                          if ((e as Error).name === 'AbortError') return;
                          // 文件分享失败，降级到 URL 分享
                        }
                      }
                    }

                    // 降级：构建分享链接
                    const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');
                    const currentLang = language;
                    let shareUrl: string;

                    if (previewTile && selectedImage) {
                      const params = new URLSearchParams({
                        mode: shareMode,
                        image: selectedImage.url,
                        title: shareTitle,
                        rows: String(splitRows),
                        cols: String(splitCols),
                        tile: String(previewTile.index + 1),
                      });
                      if (splitPadding > 0) {
                        params.set('padding', String(splitPadding));
                      }
                      if (splitRows === 1 && splitCols === 1 && splitCropRect) {
                        params.set('crop', `${splitCropRect.left.toFixed(3)},${splitCropRect.top.toFixed(3)},${splitCropRect.right.toFixed(3)},${splitCropRect.bottom.toFixed(3)}`);
                      } else if (splitRowDividers.length > 0 || splitColDividers.length > 0) {
                        if (splitRowDividers.length > 0) {
                          params.set('rd', splitRowDividers.map(d => d.toFixed(3)).join(','));
                        }
                        if (splitColDividers.length > 0) {
                          params.set('cd', splitColDividers.map(d => d.toFixed(3)).join(','));
                        }
                      }
                      shareUrl = `${window.location.origin}${basePath}/#/${currentLang}/share/image?${params.toString()}`;
                    } else {
                      shareUrl = `${window.location.origin}${basePath}/#/${currentLang}/share/image?mode=${shareMode}&image=${encodeURIComponent(shareImageUrl!)}&title=${encodeURIComponent(shareTitle)}`;
                    }

                    if (navigator.share) {
                      try {
                        await navigator.share({ title: shareTitle, text: shareText, url: shareUrl });
                        setShowShareDialog(false);
                      } catch (e) {
                        if ((e as Error).name !== 'AbortError') {
                          try {
                            await navigator.clipboard.writeText(shareUrl);
                            toast.success(t('copyLinkSuccess'));
                          } catch (e2) {
                            toast.error(String(e));
                          }
                        }
                      }
                    } else {
                      try {
                        await navigator.clipboard.writeText(shareUrl);
                        toast.success(t('copyLinkSuccess'));
                      } catch (e) {
                        toast.error(String(e));
                      }
                    }
                  }}>
                    <Share2 className="w-4 h-4 mr-2" />
                    {t('shareDirectly')}
                  </Button>
                </div>
              </>
            )}
          </DialogContent>
        </Dialog>

        <style>{`
          @keyframes fade-in {
            from {
              opacity: 0;
              transform: translateY(10px);
            }
            to {
              opacity: 1;
              transform: translateY(0);
            }
          }
          .animate-fade-in {
            animation: fade-in 0.5s ease-out forwards;
          }
        `}</style>
      </ScrollArea>
    );
  }
);

ImageResultsPanel.displayName = 'ImageResultsPanel';

