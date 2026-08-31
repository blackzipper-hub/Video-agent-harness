import React, { useMemo, useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge } from '@/components/ui/badge';
import { ReelHeader } from '@/components/ui/reel-header';
import { MessageSquare, Zap, User, X, ChevronLeft, ChevronRight } from 'lucide-react';
import { getVideoCheckPayload } from './videoCheckPayload';

interface OverviewSectionProps {
  embedded?: boolean;
  autoPlay?: boolean;
  showCelebration?: boolean;
  onCelebrationShown?: () => void;
  initialIndex?: number;
  initialGridView?: boolean;
}

const OverviewSection: React.FC<OverviewSectionProps> = ({ embedded }) => {
  const navigate = useNavigate();
  const [currentElementIndex, setCurrentElementIndex] = useState(0);
  const [imageRatios, setImageRatios] = useState<Record<string, number>>({});

  const payload = useMemo(() => getVideoCheckPayload(), []);
  const storyOutlineData = payload?.storyOutlineData;
  const charactersData = payload?.charactersData;
  const aspectRatio = payload?.userOption?.aspect_ratio || "16:9";
  const isPortrait = aspectRatio === "9:16";
  const defaultAspectRatio = isPortrait ? "9/16" : aspectRatio === "1:1" ? "1/1" : "16/9";
  const storyText = storyOutlineData?.description || storyOutlineData?.story || storyOutlineData?.title || "No story outline";
  const styleDescription = storyOutlineData?.style_guide || storyOutlineData?.style || "No style guide";

  const visualElements = useMemo(() => {
    const chars = charactersData?.characters || [];
    const items = chars.map((c: any, idx: number) => ({
      name: c?.name || `Character ${idx + 1}`,
      image: c?.image_url || null,
      category: 'Character'
    }));
    return items;
  }, [charactersData]);

  const handleClose = () => {
    navigate('/create');
  };

  const currentElement = visualElements[currentElementIndex];
  const currentImageRatio = currentElement?.image ? imageRatios[currentElement.image] : undefined;
  const isPortraitImage = currentImageRatio ? currentImageRatio < 1 : isPortrait;
  const isFirst = currentElementIndex === 0;
  const isLast = currentElementIndex >= visualElements.length - 1;

  const goToPrevious = () => {
    setCurrentElementIndex(prev => Math.max(0, prev - 1));
  };

  const goToNext = () => {
    setCurrentElementIndex(prev => Math.min(Math.max(0, visualElements.length - 1), prev + 1));
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === 'ArrowLeft') goToPrevious();
      if (e.key === 'ArrowRight') goToNext();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <div className={`${embedded ? 'relative h-full w-full' : 'fixed inset-0 z-[9999]'} bg-black/95 overflow-hidden`}>
      <div
        className="absolute inset-0 opacity-20 pointer-events-none mix-blend-overlay"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%' height='100%' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        }}
      />

      <ReelHeader
        title="Project Overview"
        statusText="VIDEO PROJECT"
        className="z-30"
        rightContent={
          <div className="flex items-center gap-4">
            <button
              className="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-red-500/30 hover:border-red-400/50 transition-all duration-300 flex items-center justify-center group"
              onClick={handleClose}
            >
              <X className="w-5 h-5 transition-transform duration-300 group-hover:rotate-90 group-hover:scale-110" />
            </button>
          </div>
        }
      />

      <div className="absolute inset-0 top-20 flex flex-col px-8 pb-8">
        <div className="flex-1 flex justify-center overflow-hidden">
          <div
            data-scrollable
            className="w-[80%] overflow-y-scroll scrollbar-thin scrollbar-thumb-amber-500/50 scrollbar-track-amber-950/30 hover:scrollbar-thumb-amber-500/70"
          >
            <div className="space-y-6 py-6">
              <div className="bg-amber-950/20 backdrop-blur-sm border border-amber-800/30 rounded-2xl p-6">
                <h2 className="text-lg font-semibold mb-4 flex items-center text-amber-100 font-inter">
                  <div className="w-8 h-8 rounded-full bg-accent-cyan/20 flex items-center justify-center mr-3">
                    <MessageSquare className="w-4 h-4 text-accent-cyan" />
                  </div>
                  Story
                </h2>
                <p className="text-amber-200/80 leading-relaxed font-inter text-base pl-11">
                  {storyText}
                </p>
              </div>

              <div className="bg-amber-950/20 backdrop-blur-sm border border-amber-800/30 rounded-2xl p-6">
                <h2 className="text-lg font-semibold mb-4 flex items-center text-amber-100 font-inter">
                  <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center mr-3">
                    <Zap className="w-4 h-4 text-primary" />
                  </div>
                  Style
                </h2>
                  <div className="flex gap-6 pl-11">
                  <div className="flex-1">
                    <p className="text-amber-200/80 leading-relaxed font-inter text-base">
                      {styleDescription}
                    </p>
                  </div>
                  </div>
                </div>
              

              <div className="bg-amber-950/20 backdrop-blur-sm border border-amber-800/30 rounded-2xl p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold flex items-center text-amber-100 font-inter">
                    <div className="w-8 h-8 rounded-full bg-accent-pink/20 flex items-center justify-center mr-3">
                      <User className="w-4 h-4 text-accent-pink" />
                    </div>
                    Visual Elements
                  </h2>
                </div>

                <div className="flex justify-center gap-2 mb-4">
                  {visualElements.map((element, index) => {
                    const elementImageRatio = element.image ? imageRatios[element.image] : undefined;
                    const elementAspectRatio = elementImageRatio ?? defaultAspectRatio;
                    return (
                    <button
                      key={index}
                      onClick={() => setCurrentElementIndex(index)}
                      className={`relative h-14 rounded-lg overflow-hidden border-2 transition-all duration-300 ${
                        currentElementIndex === index
                          ? 'border-amber-400 ring-2 ring-amber-400/50 scale-105'
                          : 'border-amber-800/50 opacity-60 hover:opacity-100 hover:border-amber-600/70'
                      }`}
                      style={{ aspectRatio: elementAspectRatio }}
                    >
                      {element.image ? (
                        <img
                          src={element.image}
                          alt={element.name}
                          className="w-full h-full object-cover"
                          onLoad={(event) => {
                            const { naturalWidth, naturalHeight } = event.currentTarget;
                            if (!naturalWidth || !naturalHeight) return;
                            const ratio = naturalWidth / naturalHeight;
                            setImageRatios((prev) => (prev[element.image] === ratio ? prev : { ...prev, [element.image]: ratio }));
                          }}
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center bg-black/30 text-amber-100 text-xs px-1">
                          {element.name}
                        </div>
                      )}
                      <div className={`absolute bottom-1 right-1 w-2 h-2 rounded-full ${
                        element.category === 'Character' ? 'bg-accent-pink' : 'bg-accent-cyan'
                      }`} />
                    </button>
                  )})}
                </div>

                <div className="relative h-[38vh] flex items-center justify-center">
                  <div className="relative h-full flex flex-col items-center justify-center">
                    <div className="relative" style={{ height: 'calc(100% - 32px)', aspectRatio: isPortraitImage ? '9/16' : aspectRatio === '1:1' ? '1/1' : '16/9' }}>
                      {currentElement?.image ? (
                        <img
                          src={currentElement.image}
                          alt={currentElement.name}
                          className="w-full h-full object-cover rounded-2xl border-4 border-amber-800/50 shadow-2xl"
                          onLoad={(event) => {
                            const { naturalWidth, naturalHeight } = event.currentTarget;
                            if (!naturalWidth || !naturalHeight) return;
                            const ratio = naturalWidth / naturalHeight;
                            setImageRatios((prev) => (prev[currentElement.image] === ratio ? prev : { ...prev, [currentElement.image]: ratio }));
                          }}
                        />
                      ) : (
                        <div className="w-full h-full rounded-2xl border-4 border-amber-800/50 shadow-2xl bg-black/30 flex items-center justify-center text-amber-100">
                          No image
                        </div>
                      )}

                      <Badge
                        className={`absolute top-4 left-4 ${currentElement.category === 'Character' ? 'bg-accent-pink/80' : 'bg-accent-cyan/80'} text-white text-sm backdrop-blur-sm border-0 px-3 py-1`}
                      >
                        {currentElement.category}
                      </Badge>
                    </div>

                    <div className="mt-2">
                      <span className="text-amber-300/50 text-sm">
                        {currentElement.name} · {currentElementIndex + 1} / {visualElements.length}
                      </span>
                    </div>
                  </div>

                  <button
                    onClick={goToPrevious}
                    disabled={isFirst}
                    className="absolute left-4 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-amber-500/30 hover:border-amber-400/50 disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-300 flex items-center justify-center z-10"
                  >
                    <ChevronLeft className="w-5 h-5" />
                  </button>

                  <button
                    onClick={goToNext}
                    disabled={isLast}
                    className="absolute right-4 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-amber-500/30 hover:border-amber-400/50 disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-300 flex items-center justify-center z-10"
                  >
                    <ChevronRight className="w-5 h-5" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default OverviewSection;



