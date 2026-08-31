import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Play,
  Pause,
  Scissors,
  Type,
  Sparkles,
  ZoomIn,
  ZoomOut,
  SkipBack,
  SkipForward,
} from "lucide-react";

interface VideoClip {
  id: string;
  title: string;
  thumbnail: string;
  startTime: number;
  duration: number;
  track: number;
}

interface Subtitle {
  id: string;
  text: string;
  startTime: number;
  duration: number;
}

interface Transition {
  id: string;
  type: "fade" | "dissolve" | "wipe" | "slide";
  clipId: string;
  position: "start" | "end";
}

export const VideoEditor = ({ clips }: { clips: any[] }) => {
  const navigate = useNavigate();
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [selectedClip, setSelectedClip] = useState<string | null>(null);
  const [videoClips, setVideoClips] = useState<VideoClip[]>(
    clips.map((clip, index) => ({
      id: clip.id.toString(),
      title: clip.title,
      thumbnail: clip.thumbnail,
      startTime: index * 5,
      duration: parseInt(clip.duration),
      track: 0,
    }))
  );
  const [subtitles, setSubtitles] = useState<Subtitle[]>([]);
  const [transitions, setTransitions] = useState<Transition[]>([]);
  const [showSubtitleInput, setShowSubtitleInput] = useState(false);
  const [newSubtitleText, setNewSubtitleText] = useState("");

  const handleNavigateToPlayer = () => {
    // Save current scroll position
    const scrollElement = document.querySelector('.overflow-y-auto');
    if (scrollElement) {
      sessionStorage.setItem('editorScrollPosition', scrollElement.scrollTop.toString());
    }
    navigate('/video-player');
  };
  
  const timelineRef = useRef<HTMLDivElement>(null);
  const totalDuration = 30; // 30 seconds timeline
  const pixelsPerSecond = 50 * zoom;

  const handleAddSubtitle = () => {
    if (newSubtitleText.trim()) {
      const newSubtitle: Subtitle = {
        id: Date.now().toString(),
        text: newSubtitleText,
        startTime: currentTime,
        duration: 3,
      };
      setSubtitles([...subtitles, newSubtitle]);
      setNewSubtitleText("");
      setShowSubtitleInput(false);
    }
  };

  const handleAddTransition = (clipId: string, type: Transition["type"]) => {
    const newTransition: Transition = {
      id: Date.now().toString(),
      type,
      clipId,
      position: "end",
    };
    setTransitions([...transitions, newTransition]);
  };

  const handleCutClip = (clipId: string) => {
    const clip = videoClips.find(c => c.id === clipId);
    if (!clip) return;

    const cutPosition = currentTime - clip.startTime;
    if (cutPosition <= 0 || cutPosition >= clip.duration) return;

    const firstPart: VideoClip = {
      ...clip,
      id: `${clip.id}-1`,
      duration: cutPosition,
    };

    const secondPart: VideoClip = {
      ...clip,
      id: `${clip.id}-2`,
      startTime: clip.startTime + cutPosition,
      duration: clip.duration - cutPosition,
    };

    setVideoClips(prev => 
      prev.filter(c => c.id !== clipId).concat([firstPart, secondPart])
    );
  };

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <Card className="glass p-6">
      <div className="space-y-4">
        {/* Editing Tools */}
        <div className="flex items-center gap-2 p-4 glass rounded-lg">
          <Button
            size="sm"
            variant={selectedClip ? "default" : "ghost"}
            onClick={() => selectedClip && handleCutClip(selectedClip)}
            disabled={!selectedClip}
          >
            <Scissors className="w-4 h-4 mr-2" />
            Cut
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost" disabled={!selectedClip}>
                <Sparkles className="w-4 h-4 mr-2" />
                Transition
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-card/95 backdrop-blur-md border-white/20">
              <DropdownMenuItem onClick={() => selectedClip && handleAddTransition(selectedClip, "fade")}>
                Fade
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => selectedClip && handleAddTransition(selectedClip, "dissolve")}>
                Dissolve
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => selectedClip && handleAddTransition(selectedClip, "wipe")}>
                Wipe
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => selectedClip && handleAddTransition(selectedClip, "slide")}>
                Slide
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Button
            size="sm"
            variant="ghost"
            onClick={() => setShowSubtitleInput(!showSubtitleInput)}
          >
            <Type className="w-4 h-4 mr-2" />
            Add Subtitle
          </Button>
        </div>

        {/* Subtitle Input */}
        {showSubtitleInput && (
          <div className="flex gap-2 p-4 glass rounded-lg">
            <Input
              placeholder="Enter subtitle text..."
              value={newSubtitleText}
              onChange={(e) => setNewSubtitleText(e.target.value)}
              className="flex-1"
              onKeyDown={(e) => e.key === 'Enter' && handleAddSubtitle()}
            />
            <Button onClick={handleAddSubtitle} size="sm">Add</Button>
          </div>
        )}

        {/* Timeline */}
        <div className="space-y-2">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium">Timeline</h4>
            <div className="text-xs text-muted-foreground">
              Zoom: {Math.round(zoom * 100)}% | Duration: {totalDuration}s
            </div>
          </div>

          <div className="relative bg-card/30 rounded-lg p-4 overflow-x-auto">
            {/* Time Markers */}
            <div className="flex mb-2" style={{ width: `${totalDuration * pixelsPerSecond}px` }}>
              {Array.from({ length: totalDuration + 1 }).map((_, i) => (
                <div
                  key={i}
                  className="flex-shrink-0 text-xs text-muted-foreground"
                  style={{ width: `${pixelsPerSecond}px` }}
                >
                  {i}s
                </div>
              ))}
            </div>

            {/* Video Track */}
            <div className="mb-4">
              <div className="text-xs text-muted-foreground mb-2">Video Track</div>
              <div
                ref={timelineRef}
                className="relative h-16 bg-card/50 rounded"
                style={{ width: `${totalDuration * pixelsPerSecond}px` }}
              >
                {videoClips.map((clip) => (
                  <div
                    key={clip.id}
                    className={`absolute top-1 h-14 rounded cursor-pointer transition-all ${
                      selectedClip === clip.id
                        ? 'ring-2 ring-primary'
                        : 'hover:ring-1 hover:ring-primary/50'
                    }`}
                    style={{
                      left: `${clip.startTime * pixelsPerSecond}px`,
                      width: `${clip.duration * pixelsPerSecond}px`,
                      backgroundImage: `url(${clip.thumbnail})`,
                      backgroundSize: 'cover',
                      backgroundPosition: 'center',
                    }}
                    onClick={() => setSelectedClip(clip.id)}
                  >
                    <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent rounded flex items-end p-2">
                      <span className="text-white text-xs font-medium truncate">
                        {clip.title}
                      </span>
                    </div>
                    
                    {/* Transition Badge */}
                    {transitions
                      .filter(t => t.clipId === clip.id)
                      .map(transition => (
                        <Badge
                          key={transition.id}
                          className="absolute top-1 right-1 bg-accent-purple/80 text-white text-xs"
                        >
                          {transition.type}
                        </Badge>
                      ))}
                  </div>
                ))}
              </div>
            </div>

            {/* Subtitle Track */}
            <div>
              <div className="text-xs text-muted-foreground mb-2">Subtitle Track</div>
              <div
                className="relative h-12 bg-card/50 rounded"
                style={{ width: `${totalDuration * pixelsPerSecond}px` }}
              >
                {subtitles.map((subtitle) => (
                  <div
                    key={subtitle.id}
                    className="absolute top-1 h-10 bg-accent-cyan/50 border border-accent-cyan rounded px-2 flex items-center cursor-pointer hover:bg-accent-cyan/70 transition-colors"
                    style={{
                      left: `${subtitle.startTime * pixelsPerSecond}px`,
                      width: `${subtitle.duration * pixelsPerSecond}px`,
                    }}
                  >
                    <span className="text-white text-xs truncate">{subtitle.text}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Playhead */}
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-primary pointer-events-none"
              style={{ left: `${currentTime * pixelsPerSecond}px` }}
            >
              <div className="absolute -top-2 -left-2 w-4 h-4 bg-primary rounded-full" />
            </div>
          </div>
        </div>

        {/* Preview Area */}
        <div className="aspect-video bg-black rounded-lg flex items-center justify-center relative cursor-pointer" onClick={handleNavigateToPlayer}>
          <div className="text-center text-white">
            <div className="w-24 h-24 bg-white/10 rounded-full flex items-center justify-center mb-2 mx-auto backdrop-blur-sm hover:scale-110 transition-transform">
              <Play className="w-12 h-12 opacity-50" />
            </div>
            <p className="text-sm opacity-50">Video Preview</p>
          </div>
          
          {/* Subtitle Overlay */}
          {subtitles
            .filter(sub => 
              currentTime >= sub.startTime && 
              currentTime <= sub.startTime + sub.duration
            )
            .map(sub => (
              <div
                key={sub.id}
                className="absolute bottom-8 left-1/2 -translate-x-1/2 bg-black/80 px-4 py-2 rounded"
              >
                <p className="text-white text-lg font-medium">{sub.text}</p>
              </div>
            ))}
        </div>

        {/* Playback Controls */}
        <div className="flex items-center justify-between p-4 glass rounded-lg">
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              onClick={() => setIsPlaying(!isPlaying)}
              className="apple-button"
            >
              {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </Button>
          </div>

          <span className="text-sm font-medium">
            {formatTime(currentTime)} / {formatTime(totalDuration)}
          </span>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setZoom(Math.max(0.5, zoom - 0.25))}
            >
              <ZoomOut className="w-4 h-4" />
            </Button>
            <span className="text-xs text-muted-foreground">{Math.round(zoom * 100)}%</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setZoom(Math.min(2, zoom + 0.25))}
            >
              <ZoomIn className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
};
