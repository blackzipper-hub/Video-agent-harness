import React, { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { ReelHeader } from "@/components/ui/reel-header";
import { api, videoAnalysisApi } from "@/services/api";
import { getVideoCheckPayload, setVideoCheckPayload } from "./videoCheckPayload";
import { hardCleanupVideo } from "@/utils/videoCleanup";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  Share2,
  RotateCw,
  Film,
  X,
  Check,
  LayoutGrid,
  Pencil,
  Save,
  Play,
  Pause
} from "lucide-react";

function buildShotsFromPayload(payload: any) {
  const videos = payload?.videosData?.video_generations || [];
  return videos.map((v: any, idx: number) => {
    const targetVersionIndex = v.current_version_index || 0;
    const ver = v.versions?.[targetVersionIndex] || v.versions?.[0];
    const videoUrl = ver?.video_url || ver?.url || "";
    const prompt = ver?.t2v_prompt || ver?.t2i_prompt || ver?.prompt || "";
    const status = ver?.success === false ? "failed" : (videoUrl ? "completed" : (v.status || "pending"));
    
    return {
      id: v.uuid || String(v.shot_number || idx + 1).padStart(2, "0"),
      uuid: v.uuid,
      shotNumber: v.shot_number || idx + 1,
      content: v.content || v.description || `Shot ${v.shot_number || idx + 1}`,
      shotType: v.shot_type || "",
      cameraMovement: v.camera_movement || "",
      timestamp: v.timestamp || "",
      duration: v.duration ? `${v.duration}s` : "",
      status,
      video: videoUrl,
      prompt,
      versionUuid: ver?.uuid,
      currentVersionIndex: targetVersionIndex,
      totalVersions: v.versions?.length || 0,
    };
  });
}

interface ShotsCheckPageProps {
  embedded?: boolean;
  initialIndex?: number;
  initialGridView?: boolean;
}

const ShotsCheckPage = ({ embedded = false, initialIndex: propInitialIndex, initialGridView }: ShotsCheckPageProps) => {
  const navigate = useNavigate();
  const location = useLocation();
  const filmStripRef = useRef<HTMLDivElement>(null);
  const thumbnailNavRef = useRef<HTMLDivElement>(null);
  const thumbnailRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const frameRefs = useRef<(HTMLDivElement | null)[]>([]);
  const videoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const mainVideoRef = useRef<HTMLVideoElement>(null);
  /** 缩略图抽取用 createElement 的 video 实例，unmount 或 effect 重跑时需释放，避免 Detached 泄漏 */
  const thumbnailVideoRefs = useRef<Array<{ video: HTMLVideoElement; onCanPlay: () => void; onSeeked: () => void }>>([]);

  // Get initial index from prop (when embedded) or URL params
  const searchParams = new URLSearchParams(location.search);
  const urlIndex = parseInt(searchParams.get('index') || '0', 10);
  const urlView = searchParams.get('view');
  const urlGalleryView = urlView === 'gallery';
  const urlGridView = urlView === 'grid';
  const initialIndex = propInitialIndex !== undefined ? propInitialIndex : urlIndex;

  const [shots, setShots] = useState(() => buildShotsFromPayload(getVideoCheckPayload()));
  const [currentIndex, setCurrentIndex] = useState(initialIndex);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [editedPromptText, setEditedPromptText] = useState("");
  const [isPlaying, setIsPlaying] = useState(false);
  const [showGridView, setShowGridView] = useState(() => {
    // Priority: prop > URL param > localStorage
    // Note: view=gallery means filmstrip view (showGridView=false)
    // view=grid means grid view (showGridView=true)
    if (initialGridView !== undefined) return initialGridView;
    if (urlGalleryView) return false; // gallery = filmstrip view
    if (urlGridView) return true;
    const saved = localStorage.getItem('shots-grid-view');
    return saved === 'true';
  });
  const [gridCols, setGridCols] = useState(() => {
    const saved = localStorage.getItem('shots-grid-cols');
    const isMobileScreen = window.innerWidth < 768;
    const defaultCols = isMobileScreen ? 2 : 6;
    const parsed = saved ? parseInt(saved, 10) : defaultCols;
    return [1, 2, 3, 6, 9, 12, 15].includes(parsed) ? parsed : defaultCols;
  });
  const [shortcutsCollapsed, setShortcutsCollapsed] = useState(true);
  const [videoThumbnails, setVideoThumbnails] = useState<{ [key: string]: string }>({});
  const [videoRatios, setVideoRatios] = useState<Record<string, number>>({});

  // Gallery centering - measure actual rendered frame width
  const GALLERY_ITEM_GAP_PX = 24; // gap-6
  const [galleryFrameWidth, setGalleryFrameWidth] = useState(0);

  const getVideoAspectRatio = (videoUrl?: string) => {
    if (!videoUrl) return "16/9";
    const ratio = videoRatios[videoUrl];
    return ratio && ratio < 1 ? "9/16" : "16/9";
  };

  useEffect(() => {
    if (showGridView) return;

    let rafId = 0;
    const measure = () => {
      const el = frameRefs.current[0];
      if (el) {
        const w = el.getBoundingClientRect().width;
        if (w > 0) {
          setGalleryFrameWidth(prev => Math.abs(prev - w) > 1 ? w : prev);
        }
      }
    };

    // Initial measure after render
    rafId = requestAnimationFrame(() => {
      measure();
    });

    // Re-measure on resize
    const handleResize = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(measure);
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(rafId);
    };
  }, [showGridView, shots.length]);

  // Save view preferences
  useEffect(() => {
    localStorage.setItem('shots-grid-view', String(showGridView));
  }, [showGridView]);

  useEffect(() => {
    localStorage.setItem('shots-grid-cols', String(gridCols));
  }, [gridCols]);

  // Extract first frame from videos for thumbnails; cleanup created videos on unmount/re-run to avoid Detached leak
  useEffect(() => {
    const created = thumbnailVideoRefs.current;
    shots.forEach((shot) => {
      if (videoThumbnails[shot.id]) return; // Already extracted
      
      const video = document.createElement('video');
      video.src = shot.video;
      video.muted = true;
      video.playsInline = true;
      video.preload = 'auto';
      
      const handleCanPlay = () => {
        video.currentTime = 0.1;
      };
      
      const handleSeeked = () => {
        try {
          const canvas = document.createElement('canvas');
          canvas.width = video.videoWidth || 160;
          canvas.height = video.videoHeight || 90;
          const ctx = canvas.getContext('2d');
          if (ctx && video.videoWidth > 0) {
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.7);
            setVideoThumbnails(prev => ({ ...prev, [shot.id]: dataUrl }));
          }
        } catch (e) {
          console.log('Thumbnail extraction failed for', shot.id);
        }
        video.removeEventListener('canplay', handleCanPlay);
        video.removeEventListener('seeked', handleSeeked);
      };
      
      video.addEventListener('canplay', handleCanPlay);
      video.addEventListener('seeked', handleSeeked);
      video.load();
      created.push({ video, onCanPlay: handleCanPlay, onSeeked: handleSeeked });
    });
    return () => {
      created.forEach(({ video, onCanPlay, onSeeked }) => {
        try {
          video.removeEventListener('canplay', onCanPlay);
          video.removeEventListener('seeked', onSeeked);
          hardCleanupVideo(video);
        } catch (_) {}
      });
      created.length = 0;
    };
  }, [shots, videoThumbnails]);

  // Handle video play/pause for main video
  useEffect(() => {
    if (mainVideoRef.current) {
      if (isPlaying) {
        mainVideoRef.current.play();
      } else {
        mainVideoRef.current.pause();
      }
    }
  }, [isPlaying, currentIndex]);

  // Reset playing state when changing shots
  useEffect(() => {
    setIsPlaying(!showGridView);
  }, [currentIndex, showGridView]);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Handle Ctrl+S for saving edit (works even in textarea)
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        if (editingPrompt) {
          handleSavePrompt();
        }
        return;
      }
      
      // Skip other shortcuts if user is typing in an input/textarea
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }
      
      if (e.key === 'Escape') {
        if (editingPrompt) {
          handleCancelEdit();
        } else {
          navigate('/create');
        }
      } else if (e.key === 'ArrowLeft' && currentIndex > 0) {
        setCurrentIndex(currentIndex - 1);
      } else if (e.key === 'ArrowRight' && currentIndex < shots.length - 1) {
        setCurrentIndex(currentIndex + 1);
      } else if (e.key === 'r' || e.key === 'R') {
        const currentShot = shots[currentIndex];
        if (currentShot) {
          handleRegenerate(currentShot.id);
          toast.success("Regenerating...");
        }
      } else if ((e.key === 'e' || e.key === 'E') && !editingPrompt) {
        handleEditPrompt();
      } else if (e.key === 'v' || e.key === 'V') {
        setShowGridView(prev => !prev);
      } else if (e.key === ' ') {
        e.preventDefault();
        setIsPlaying(prev => !prev);
      } else if (e.key >= '1' && e.key <= '9') {
        const chapterNum = parseInt(e.key, 10);
        const targetIndex = (chapterNum - 1) * 20;
        if (targetIndex < shots.length) {
          setCurrentIndex(targetIndex);
          toast.success(`Jumped to Section ${chapterNum}`);
        }
      } else if (e.key === 'd' || e.key === 'D') {
        handleDownload();
      }
    };
    
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [currentIndex, shots, navigate, editingPrompt, editedPromptText]);

  // Scroll thumbnail into view (horizontal only; avoid vertical scroll that can snap the parent page)
  useEffect(() => {
    const nav = thumbnailNavRef.current;
    const thumb = thumbnailRefs.current[currentIndex];
    if (!nav || !thumb) return;

    const left = thumb.offsetLeft - nav.clientWidth / 2 + thumb.clientWidth / 2;
    nav.scrollTo({ left, behavior: 'smooth' });
  }, [currentIndex]);

  const handleRegenerate = async (shotId: string) => {
    try {
      const payload = getVideoCheckPayload();
      const videosData = payload?.videosData;
      if (!videosData?.video_generations) {
        toast.error("No videos data");
        return;
      }

      const video = videosData.video_generations.find((v: any) => v.uuid === shotId || String(v.shot_number) === shotId);
      if (!video) {
        toast.error("Shot not found");
        return;
      }

      const targetVersionIndex = video.current_version_index || 0;
      const currentVersion = video.versions?.[targetVersionIndex] || video.versions?.[0];
      if (!currentVersion) {
        toast.error("No version data available");
        return;
      }

      toast.info("Regenerating shot...");

      const response = await api.videoEditing.regenerateVideos({
        videos: [{
          uuid: video.uuid,
          versions: [{
            uuid: currentVersion.uuid,
            custom_prompt: currentVersion.motion_prompt || currentVersion.t2i_prompt || currentVersion.t2v_prompt || currentVersion.prompt || "",
            regenerate_strategy: 'prompt_regenerate',
          }]
        }],
        user_option: payload.userOption || {},
      });

      if (response.code !== 0) {
        toast.error(response.message || "Regenerate failed");
        return;
      }

      const threadId = payload.threadId;
      if (threadId) {
        const [updatedVideos, updatedKeyframes] = await Promise.all([
          videoAnalysisApi.getVideoGenerationsDataByThreadId(threadId),
          videoAnalysisApi.getKeyframesDataByThreadId(threadId),
        ]);
        if (updatedVideos?.code === 0) payload.videosData = updatedVideos.data;
        if (updatedKeyframes?.code === 0) payload.keyframesData = updatedKeyframes.data;
      }

      setVideoCheckPayload(payload);
      setShots(buildShotsFromPayload(payload));

      toast.success("Regeneration complete!");
    } catch (e: any) {
      toast.error(`Regenerate failed: ${e?.message || e}`);
    }
  };

  const handleDownload = () => {
    const currentShot = shots[currentIndex];
    if (currentShot) {
      const link = document.createElement('a');
      link.href = currentShot.video;
      link.download = `shot-${String(currentIndex + 1).padStart(3, '0')}.mp4`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      toast.success("Downloading shot...");
    }
  };

  const handleClose = () => {
    navigate('/create');
  };

  const handleEditPrompt = () => {
    setEditedPromptText(currentShot?.prompt || "");
    setEditingPrompt(true);
  };

  const handleSavePrompt = () => {
    if (currentShot) {
      setShots(prev => prev.map((shot, idx) => 
        idx === currentIndex ? { ...shot, prompt: editedPromptText } : shot
      ));
      setEditingPrompt(false);
      toast.success("Prompt updated!");
    }
  };

  const handleCancelEdit = () => {
    setEditingPrompt(false);
    setEditedPromptText("");
  };

  const isFirst = currentIndex === 0;
  const isLast = currentIndex === shots.length - 1;
  const currentShot = shots[currentIndex];

  // Real tasks may not have shots data yet. Avoid render crash (white screen) when empty.
  if (!currentShot) {
    return (
      <div className={`${embedded ? 'relative h-full w-full' : 'fixed inset-0 z-[9999]'} bg-black/95 overflow-hidden`}>
        {/* Film Grain Overlay */}
        <div 
          className="absolute inset-0 opacity-20 pointer-events-none mix-blend-overlay"
          style={{
            backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%' height='100%' filter='url(%23noise)'/%3E%3C/svg%3E")`,
          }}
        />
        <ReelHeader
          title="Shots Reel"
          statusText={`0 SHOTS`}
          className="z-30"
          rightContent={
            <div className="flex items-center gap-4">
              <button
                className="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-red-500/30 hover:border-red-400/50 transition-all duration-300 flex items-center justify-center group"
                onClick={() => navigate('/create')}
              >
                <X className="w-5 h-5 transition-transform duration-300 group-hover:rotate-90 group-hover:scale-110" />
              </button>
            </div>
          }
        />
        <div className="absolute inset-0 top-20 flex items-center justify-center px-8">
          <div className="text-amber-100/80 text-sm bg-black/30 border border-amber-500/20 rounded-xl px-6 py-4">
            No shots data yet. Please generate video clips first.
          </div>
        </div>
      </div>
    );
  }

  // Chapter info
  const currentChapter = Math.floor(currentIndex / 20) + 1;
  const totalChapters = Math.ceil(shots.length / 20);
  const chapterStories: { [key: number]: string } = {
    1: "Opening sequence - establishing the world and introducing key characters through dynamic camera work.",
    2: "Rising tension - action sequences and character interactions that drive the narrative forward.",
    3: "Climactic moments - the peak of dramatic intensity with powerful visual storytelling.",
  };
  
  const sectionSummaries: { [key: number]: string } = {
    1: "Opening Shots",
    2: "Rising Action",
    3: "Climax",
  };

  return (
    <div className={`${embedded ? 'relative h-full w-full' : 'fixed inset-0 z-[9999]'} bg-black/95 overflow-hidden`}>
      {/* Film Grain Overlay */}
      <div 
        className="absolute inset-0 opacity-20 pointer-events-none mix-blend-overlay"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%' height='100%' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        }}
      />
      
      {/* Vintage Header */}
      <ReelHeader
        title="Shots Reel"
        statusText={`${shots.length} SHOTS`}
        className="z-30"
        rightContent={
          <div className="flex items-center gap-4">
            {/* Grid Columns Selector - shown when in grid view */}
            <div 
              className={`flex items-center gap-0.5 bg-amber-950/20 backdrop-blur-sm rounded-full px-2 py-1 border border-amber-800/20 transition-all duration-300 origin-right ${
                showGridView 
                  ? 'opacity-60 hover:opacity-100 scale-x-100 translate-x-0' 
                  : 'opacity-0 scale-x-0 translate-x-8 pointer-events-none'
              }`}
            >
              {[1, 3, 6, 9, 12, 15].map((cols, index) => (
                <button
                  key={cols}
                  className={`w-6 h-6 rounded-full text-xs font-medium transition-all duration-200 ${
                    gridCols === cols 
                      ? 'bg-amber-500/40 text-amber-200 border border-amber-500/30' 
                      : 'text-amber-300/40 hover:bg-amber-500/15 hover:text-amber-200/70'
                  }`}
                  style={{ 
                    transitionDelay: showGridView ? `${index * 50}ms` : '0ms'
                  }}
                  onClick={() => setGridCols(cols)}
                >
                  {cols}
                </button>
              ))}
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-white/20 transition-all duration-300 flex items-center justify-center group overflow-hidden"
                  onClick={() => setShowGridView(!showGridView)}
                >
                  <div className="relative w-5 h-5">
                    <Film className={`w-5 h-5 absolute inset-0 transition-all duration-300 ${showGridView ? 'opacity-100 scale-100 rotate-0' : 'opacity-0 scale-50 rotate-180'}`} />
                    <LayoutGrid className={`w-5 h-5 absolute inset-0 transition-all duration-300 ${!showGridView ? 'opacity-100 scale-100 rotate-0' : 'opacity-0 scale-50 -rotate-180'}`} />
                  </div>
                </button>
              </TooltipTrigger>
              <TooltipContent className="bg-amber-950/90 border-amber-700/50 text-amber-100">
                <p>{showGridView ? 'Film Strip View' : 'Grid View'}</p>
              </TooltipContent>
            </Tooltip>
            <button
              className="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-red-500/30 hover:border-red-400/50 transition-all duration-300 flex items-center justify-center group"
              onClick={handleClose}
            >
              <X className="w-5 h-5 transition-transform duration-300 group-hover:rotate-90 group-hover:scale-110" />
            </button>
          </div>
        }
      />

      {/* Grid View - Responsive padding */}
      {showGridView && (
        <div className="absolute inset-0 top-20 flex flex-col px-4 md:px-8 pb-8">
          {/* Scrollable Grid Container - centered with scrollbar next to grid */}
          <div className="flex-1 flex justify-center overflow-hidden">
            <div 
              data-scrollable 
              className="w-[80%] overflow-y-scroll scrollbar-thin scrollbar-thumb-amber-500/50 scrollbar-track-amber-950/30 hover:scrollbar-thumb-amber-500/70"
            >
              <div 
                className="grid gap-4 transition-all duration-300 pb-4"
                style={{ gridTemplateColumns: `repeat(${gridCols}, minmax(0, 1fr))` }}
              >
                {shots.map((shot, index) => (
                  <div 
                    key={shot.id}
                    className={`relative rounded-lg overflow-hidden cursor-pointer border-2 transition-all group ${
                      currentIndex === index 
                        ? 'border-amber-400 shadow-[0_0_20px_rgba(251,191,36,0.4)]' 
                        : 'border-amber-800/50'
                    }`}
                    style={{ aspectRatio: getVideoAspectRatio(shot.video) }}
                    onClick={() => {
                      setCurrentIndex(index);
                    }}
                  >
                    <video 
                      src={shot.video}
                      className="w-full h-full object-cover"
                      muted
                      loop
                      playsInline
                      preload="metadata"
                      onLoadedMetadata={(event) => {
                        const { videoWidth, videoHeight } = event.currentTarget;
                        if (!videoWidth || !videoHeight) return;
                        const ratio = videoWidth / videoHeight;
                        setVideoRatios((prev) => (prev[shot.video] === ratio ? prev : { ...prev, [shot.video]: ratio }));
                      }}
                      onLoadedData={(e) => { e.currentTarget.currentTime = 0.1; }}
                      onMouseEnter={(e) => e.currentTarget.play()}
                      onMouseLeave={(e) => { e.currentTarget.pause(); e.currentTarget.currentTime = 0.1; }}
                    />
                    <div className="absolute top-2 left-2 px-2 py-0.5 rounded bg-black/60 text-white text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                      Shot {index + 1}
                    </div>
                    {shot.totalVersions > 0 && (
                      <div className="absolute top-2 right-2 px-2 py-0.5 rounded bg-black/60 text-white text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                        v{shot.currentVersionIndex + 1}/{shot.totalVersions}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Lightbox Style Single Video View - Responsive height */}
      {!showGridView && currentShot && (
        <div className="absolute inset-x-0 top-[calc(50%+40px)] -translate-y-1/2 h-[45vh] md:h-[55vh] flex items-center justify-center px-12 md:px-0">
          {/* Video Container */}
          <div className="relative h-full flex flex-col items-center justify-center">
            <div className="relative group" style={{ height: 'calc(100% - 32px)', aspectRatio: getVideoAspectRatio(currentShot.video) }}>
              <video
                ref={mainVideoRef}
                src={currentShot.video}
                className="w-full h-full object-cover rounded-2xl border-4 border-amber-800/50 shadow-2xl"
                muted
                loop
                autoPlay
                playsInline
                preload="auto"
                onLoadedMetadata={(event) => {
                  const { videoWidth, videoHeight } = event.currentTarget;
                  if (!videoWidth || !videoHeight) return;
                  const ratio = videoWidth / videoHeight;
                  setVideoRatios((prev) => (prev[currentShot.video] === ratio ? prev : { ...prev, [currentShot.video]: ratio }));
                }}
                onLoadedData={(e) => { e.currentTarget.currentTime = 0.1; }}
              />
              
              {/* Play/Pause overlay */}
              <button
                className="absolute inset-0 flex items-center justify-center bg-black/20 opacity-0 hover:opacity-100 transition-opacity z-20 rounded-2xl"
                onClick={() => setIsPlaying(!isPlaying)}
              >
                {isPlaying ? (
                  <Pause className="w-16 h-16 text-white/80" />
                ) : (
                  <Play className="w-16 h-16 text-white/80" />
                )}
              </button>
              
              {/* Shot Number Badge */}
              <Badge 
                className="absolute top-4 left-4 bg-amber-500/80 text-white text-sm backdrop-blur-sm border-0 px-3 py-1 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                Shot {currentIndex + 1}
              </Badge>
            </div>
            
            {/* Video Info */}
            <div className="mt-2">
              <span className="text-amber-300/50 text-sm">
                {currentIndex + 1} / {shots.length}
              </span>
            </div>
          </div>

          {/* Previous Button - Responsive positioning */}
          <button
            onClick={() => setCurrentIndex(prev => Math.max(0, prev - 1))}
            disabled={isFirst}
            className="absolute left-2 md:left-6 top-1/2 -translate-y-1/2 w-10 h-10 md:w-14 md:h-14 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-amber-500/30 hover:border-amber-400/50 disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-300 flex items-center justify-center z-10"
          >
            <ChevronLeft className="w-5 h-5 md:w-8 md:h-8" />
          </button>

          {/* Next Button - Responsive positioning */}
          <button
            onClick={() => setCurrentIndex(prev => Math.min(shots.length - 1, prev + 1))}
            disabled={isLast}
            className="absolute right-2 md:right-6 top-1/2 -translate-y-1/2 w-10 h-10 md:w-14 md:h-14 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-amber-500/30 hover:border-amber-400/50 disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-300 flex items-center justify-center z-10"
          >
            <ChevronRight className="w-5 h-5 md:w-8 md:h-8" />
          </button>
        </div>
      )}


      {/* Thumbnail Navigation - at top below header */}
      {currentShot && !showGridView && (
        <div className="absolute top-20 left-0 right-0 z-50">
          <div className="bg-black/40 backdrop-blur-md border-b border-white/10 pb-6 pt-4 px-8">
            <div className="w-[90%] mx-auto">
              <div className="relative">
                {/* Thumbnail Strip Container */}
                <div 
                  ref={thumbnailNavRef}
                  className="overflow-x-auto pb-2 [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-gray-600/30 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb:hover]:bg-gray-500/40"
                >
                  <div className="min-w-max px-4">
                    {/* Use inline-grid to ensure header and thumbnail columns align perfectly */}
                    <div 
                      className="inline-grid gap-x-2 gap-y-2"
                      style={{
                        gridTemplateColumns: shots.map((_, index) => {
                          const isSectionStart = index % 20 === 0 && index !== 0;
                          return isSectionStart ? '10px 56px' : '56px';
                        }).join(' ')
                      }}
                    >
                      {/* Section Header Row */}
                      {Array.from({ length: totalChapters }).map((_, i) => {
                        const sectionNum = i + 1;
                        const startIdx = i * 20;
                        const endIdx = Math.min(startIdx + 20, shots.length);
                        const shotsInSection = endIdx - startIdx;
                        const isCurrentSection = currentChapter === sectionNum;
                        
                        const columnsBeforeThisSection = Array.from({ length: startIdx }).reduce<number>((acc, _, idx) => {
                          const isSectionStart = idx % 20 === 0 && idx !== 0;
                          return acc + (isSectionStart ? 2 : 1);
                        }, 0);
                        
                        const gridColumnStart = i === 0 ? 1 : columnsBeforeThisSection + 2;
                        const gridColumnEnd = gridColumnStart + shotsInSection;
                        
                        return (
                          <button
                            key={sectionNum}
                            onClick={() => setCurrentIndex(startIdx)}
                            className="flex flex-col items-start gap-0.5 transition-all duration-200 cursor-pointer group pt-1"
                            style={{ 
                              gridColumn: `${gridColumnStart} / ${gridColumnEnd}`,
                              gridRow: 1
                            }}
                          >
                            <div className="flex items-center gap-2 px-1">
                              <span className={`text-[10px] font-mono font-bold flex-shrink-0 transition-colors ${isCurrentSection ? 'text-amber-400' : 'text-amber-400/50 group-hover:text-amber-400/70'}`}>
                                Sec.{sectionNum}
                              </span>
                              <span className={`text-[10px] truncate transition-colors ${isCurrentSection ? 'text-amber-100' : 'text-amber-200/40 group-hover:text-amber-200/60'}`}>
                                {sectionSummaries[sectionNum] || "Continues..."}
                              </span>
                            </div>
                            <div className={`w-full h-px transition-all duration-200 ${
                              isCurrentSection 
                                ? 'bg-gradient-to-r from-amber-400/80 via-amber-400 to-amber-400/80 shadow-[0_0_4px_rgba(251,191,36,0.4)]' 
                                : 'bg-gradient-to-r from-transparent via-amber-600/30 to-transparent group-hover:via-amber-500/50'
                            }`} />
                          </button>
                        );
                      })}

                      {/* Thumbnail Row */}
                      {shots.map((shot, index) => {
                        const isActive = index === currentIndex;
                        const isSectionStart = index % 20 === 0 && index !== 0;
                        
                        const columnsBeforeThis = Array.from({ length: index }).reduce<number>((acc, _, idx) => {
                          const isStart = idx % 20 === 0 && idx !== 0;
                          return acc + (isStart ? 2 : 1);
                        }, 0);
                        const gridColumn = isSectionStart ? columnsBeforeThis + 2 : columnsBeforeThis + 1;
                        
                        return (
                          <React.Fragment key={shot.id}>
                            {/* Section Divider Line */}
                            {isSectionStart && (
                              <div 
                                className="flex items-center justify-center"
                                style={{ gridColumn: columnsBeforeThis + 1, gridRow: 2 }}
                              >
                                <div className="w-px h-10 bg-gradient-to-b from-amber-400/40 via-amber-400/20 to-transparent" />
                              </div>
                            )}
                            
                            {/* Thumbnail */}
                            <button
                              ref={el => thumbnailRefs.current[index] = el}
                              onClick={() => setCurrentIndex(index)}
                              className={`relative flex-shrink-0 transition-all duration-300 rounded overflow-hidden justify-self-center w-14 h-9 ${
                                isActive 
                                  ? 'ring-2 ring-amber-400 shadow-[0_0_12px_rgba(251,191,36,0.4)] z-10' 
                                  : 'opacity-60 ring-1 ring-amber-800/50'
                              }`}
                              style={{ gridColumn, gridRow: 2 }}
                            >
                              {videoThumbnails[shot.id] ? (
                                <img 
                                  src={videoThumbnails[shot.id]}
                                  alt={`Shot ${index + 1}`}
                                  className="w-full h-full object-cover"
                                />
                              ) : (
                                <video 
                                  src={shot.video}
                                  className="w-full h-full object-cover"
                                  muted
                                  playsInline
                                  preload="metadata"
                                  onLoadedMetadata={(event) => {
                                    const { videoWidth, videoHeight } = event.currentTarget;
                                    if (!videoWidth || !videoHeight) return;
                                    const ratio = videoWidth / videoHeight;
                                    setVideoRatios((prev) => (prev[shot.video] === ratio ? prev : { ...prev, [shot.video]: ratio }));
                                    event.currentTarget.currentTime = 0.1;
                                  }}
                                />
                              )}
                              {isActive && (
                                <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-amber-400" />
                              )}
                            </button>
                          </React.Fragment>
                        );
                      })}
                    </div>
                  </div>
                </div>
                
                {/* Fade Edges */}
                <div className="absolute left-0 top-0 bottom-2 w-12 bg-gradient-to-r from-black/40 to-transparent pointer-events-none" />
                <div className="absolute right-0 top-0 bottom-2 w-12 bg-gradient-to-l from-black/40 to-transparent pointer-events-none" />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Keyboard Shortcuts Bar - at bottom */}
      {currentShot && !showGridView && (
        <div className="absolute bottom-0 left-0 right-0 z-40">
          <div className="bg-gradient-to-t from-[#1a1510] via-[#1a1510]/95 to-transparent pt-6 pb-4 px-8">
            
            {/* Collapsible Keyboard Shortcuts Bar */}
            <div 
              className="flex items-center pt-3 border-t border-amber-800/30 h-10 cursor-pointer"
              onClick={() => setShortcutsCollapsed(!shortcutsCollapsed)}
            >
              {shortcutsCollapsed ? (
                <div className="text-amber-200/30 hover:text-amber-200/50 transition-colors duration-200 text-xs h-full flex items-center ml-4">
                  Shortcuts
                </div>
              ) : (
                <div className="flex items-center gap-6 h-full ml-4">
                  <div className="flex items-center gap-2">
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">←</kbd>
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">→</kbd>
                    <span className="text-amber-200/30 text-xs">Navigate</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">Space</kbd>
                    <span className="text-amber-200/30 text-xs">Play/Pause</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">R</kbd>
                    <span className="text-amber-200/30 text-xs">Regenerate</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">E</kbd>
                    <span className="text-amber-200/30 text-xs">Edit</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">V</kbd>
                    <span className="text-amber-200/30 text-xs">View</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">Esc</kbd>
                    <span className="text-amber-200/30 text-xs">Exit</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ShotsCheckPage;
