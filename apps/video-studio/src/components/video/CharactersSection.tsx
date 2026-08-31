import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { User, ZoomIn, ZoomOut, ChevronLeft, ChevronRight } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useState, useRef } from "react";

interface CharactersSectionProps {
  charactersData: any;
  onFullView?: () => void;
}

export const CharactersSection = ({ charactersData, onFullView }: CharactersSectionProps) => {
  const { t } = useLanguage();
  const [zoomLevel, setZoomLevel] = useState(1);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  
  // API返回格式: { characters: [], total: 123 }
  if (!charactersData || !charactersData.characters || charactersData.characters.length === 0) return null;

  const characters = charactersData.characters;

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
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollBy({ left: -200, behavior: 'smooth' });
    }
  };

  const handleScrollRight = () => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollBy({ left: 200, behavior: 'smooth' });
    }
  };

  return (
    <Card className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <User className="w-5 h-5 mr-2 text-accent-pink" />
          {t('characterSection')}
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
      <div ref={scrollContainerRef} className="overflow-x-auto overflow-y-hidden scrollbar-subtle pl-10">
        <div className="flex gap-4 pb-2 pr-4" style={{ minWidth: `${characters.length * (scaledCardWidth + 16)}px` }}>
          {characters.map((character: any, index: number) => (
            <Card 
              key={index} 
              className="p-4 glass-bg flex-shrink-0"
              style={{ width: `${scaledCardWidth}px` }}
            >
              {character.image_url && (
                <div 
                  className="mb-3 flex items-center justify-center overflow-hidden mx-auto rounded-lg bg-black/5" 
                  style={{ height: `${scaledImageHeight}px` }}
                >
                  <img
                    src={character.image_url}
                    alt={character.name}
                    className="max-w-full max-h-full object-contain cursor-pointer hover:opacity-90 transition-opacity"
                    onClick={() => window.open(character.image_url, '_blank')}
                  />
                </div>
              )}
              <h4 className="font-medium text-sm text-center">
                {String(character.name)}
              </h4>
            </Card>
          ))}
        </div>
      </div>
    </Card>
  );
};

