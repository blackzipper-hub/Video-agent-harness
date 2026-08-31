import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { User, ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Loader2, ChevronDown } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useState, useRef } from "react";
import { useLazyLoading } from "@/hooks/useLazyLoading";

interface LazyCharactersSectionProps {
  charactersData: any;
}

export const LazyCharactersSection = ({ charactersData }: LazyCharactersSectionProps) => {
  const { t } = useLanguage();
  const [zoomLevel, setZoomLevel] = useState(1);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  
  // 确保 characters 数组存在，避免 Hook 调用顺序问题
  const characters = charactersData?.characters || [];

  // 使用懒加载 Hook
  const {
    visibleItems: visibleCharacters,
    isLoading,
    hasMore,
    loadMore,
    scrollRef,
    currentPage
  } = useLazyLoading(characters, {
    itemsPerPage: 8, // 每次加载8个角色
    threshold: 300,
    preloadPages: 1,
    initialPages: 1
  });

  // 在所有 Hook 调用之后进行条件判断
  if (!charactersData || !charactersData.characters || charactersData.characters.length === 0) return null;

  // 计算缩放后的尺寸
  const baseCardWidth = 200;
  const baseImageHeight = 150;
  const scaledCardWidth = baseCardWidth * zoomLevel;
  const scaledImageHeight = baseImageHeight * zoomLevel;

  const handleZoomIn = () => {
    setZoomLevel(prev => Math.min(prev * 1.2, 4)); // 最大400%
  };

  const handleZoomOut = () => {
    setZoomLevel(prev => Math.max(prev / 1.2, 0.1)); // 最小10%
  };

  const handleScrollLeft = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollBy({ left: -200, behavior: 'smooth' });
    }
  };

  const handleScrollRight = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollBy({ left: 200, behavior: 'smooth' });
    }
  };

  return (
    <Card className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <User className="w-5 h-5 mr-2 text-accent-pink" />
          {t('characterSection')}
          {/* 懒加载扩展：显示加载进度 */}
          <span className="ml-2 text-sm text-muted-foreground">
            ({visibleCharacters.length} / {characters.length})
          </span>
        </h3>
        <div className="flex items-center gap-2">
          {/* 滚动控制 */}
          <div className="flex items-center gap-1 bg-white/10 rounded-lg p-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={handleScrollLeft}
              className="h-8 w-8 p-0"
              title="Scroll Left"
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleScrollRight}
              className="h-8 w-8 p-0"
              title="Scroll Right"
            >
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
          
          {/* 缩放控制 */}
          <div className="flex items-center gap-1 bg-white/10 rounded-lg p-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={handleZoomOut}
              className="h-8 w-8 p-0"
              title="Zoom Out"
            >
              <ZoomOut className="w-4 h-4" />
            </Button>
            <span className="text-xs px-2 min-w-[3rem] text-center">
              {Math.round(zoomLevel * 100)}%
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleZoomIn}
              className="h-8 w-8 p-0"
              title="Zoom In"
            >
              <ZoomIn className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>
      
      {/* 横向滚动容器 - 滚动容器自身 pl-10，与 Storyboards 一致，避免第一个图被裁切 */}
      <div ref={scrollRef} className="overflow-x-auto overflow-y-hidden scrollbar-subtle pl-10">
        <div className="flex gap-4 pb-2 pr-4" style={{ minWidth: `${visibleCharacters.length * (scaledCardWidth + 16)}px` }}>
          {visibleCharacters.map((character: any, index: number) => (
            <Card 
              key={index} 
              className="p-4 glass-bg flex-shrink-0"
              style={{ width: `${scaledCardWidth}px` }}
            >
              {character.image_url && (
                <div 
                  className="mb-3 flex items-center justify-center mx-auto rounded-lg bg-black/5 border border-white/20 p-2"
                  style={{ maxWidth: `${scaledCardWidth - 32}px` }}
                >
                  <img
                    src={character.image_url}
                    alt={character.name}
                    className="max-w-full h-auto object-contain cursor-pointer hover:opacity-90 transition-opacity rounded"
                    onClick={() => window.open(character.image_url, '_blank')}
                    loading="lazy" // 原生懒加载
                    style={{ maxHeight: `${scaledImageHeight}px` }}
                  />
                </div>
              )}
              <h4 className="font-medium text-sm text-center">{character.name}</h4>
            </Card>
          ))}
          
          {/* 懒加载扩展：加载更多指示器 */}
          {isLoading && (
            <div 
              className="flex-shrink-0 flex items-center justify-center bg-card/30 rounded-lg border-2 border-dashed border-gray-300"
              style={{ width: scaledCardWidth, height: scaledImageHeight + 80 }}
            >
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="w-6 h-6 animate-spin" />
                <span className="text-sm">{t('loading') || 'Loading...'}</span>
              </div>
            </div>
          )}
          
          {/* 懒加载扩展：加载更多按钮 */}
          {hasMore && !isLoading && (
            <div 
              className="flex-shrink-0 flex items-center justify-center bg-card/30 rounded-lg border-2 border-dashed border-gray-300 hover:border-primary/50 transition-colors cursor-pointer"
              style={{ width: scaledCardWidth, height: scaledImageHeight + 80 }}
              onClick={loadMore}
            >
              <div className="flex flex-col items-center gap-2 text-muted-foreground hover:text-primary transition-colors">
                <ChevronDown className="w-6 h-6" />
                <span className="text-sm">{t('loadMore') || 'Load More'}</span>
                <span className="text-xs">{t('remainingCount', { count: characters.length - visibleCharacters.length })}</span>
              </div>
            </div>
          )}
        </div>
      </div>
      
      {/* 懒加载扩展：页面指示器 */}
      {characters.length > 8 && (
        <div className="flex items-center justify-center mt-4 gap-2 text-sm text-muted-foreground">
          <span>{t('pageIndicator', { page: currentPage })}</span>
          <span>•</span>
          <span>{t('charactersVisibleCount', { visible: visibleCharacters.length, total: characters.length })}</span>
          {hasMore && (
            <>
              <span>•</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={loadMore}
                disabled={isLoading}
                className="h-6 px-2 text-xs"
              >
                {isLoading ? (t('loading') || 'Loading...') : (t('loadMore') || 'Load More')}
              </Button>
            </>
          )}
        </div>
      )}
    </Card>
  );
};
