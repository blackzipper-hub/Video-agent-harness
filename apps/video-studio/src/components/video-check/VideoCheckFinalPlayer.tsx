import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ReelHeader } from "@/components/ui/reel-header";
import {
  Play,
  Pause,
  Volume2,
  VolumeX,
  Maximize,
  Download,
  X,
  Film,
  RefreshCw,
  ZoomIn,
  ZoomOut,
  Share2,
  Sparkles,
  PartyPopper
} from "lucide-react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { getVideoCheckPayload } from "./videoCheckPayload";
import { hardCleanupVideo } from "@/utils/videoCleanup";

interface VideoCheckFinalPlayerProps {
  embedded?: boolean;
  autoPlay?: boolean;
  showCelebration?: boolean;
  onCelebrationShown?: () => void;
}

const VideoCheckFinalPlayer = ({ embedded = false, autoPlay = false, showCelebration: externalShowCelebration, onCelebrationShown }: VideoCheckFinalPlayerProps) => {
  const navigate = useNavigate();
  const location = useLocation();
  const payload = useMemo(() => getVideoCheckPayload(), []);
  const finalVideoUrl = payload?.videoAssemblyData?.final_video_url;
  const clips = useMemo(() => {
    const list: Array<{ start: number; end: number; label: string; video: string }> = [];
    const videos = payload?.videosData?.video_generations || [];
    const usable: Array<{ label: string; video: string }> = [];
    videos.forEach((v: any, idx: number) => {
      const versions = v?.versions || [];
      const currentIdx = v?.current_version_index || 0;
      const ver = versions[currentIdx] || versions[0];
      const url = ver?.video_url || ver?.url;
      if (url) {
        usable.push({ label: `Shot ${v?.shot_number || idx + 1}`, video: url });
      }
    });
    const n = usable.length;
    if (n === 0) return list;
    const step = 100 / n;
    usable.forEach((c, i) => {
      list.push({ start: i * step, end: (i + 1) * step, label: c.label, video: c.video });
    });
    return list;
  }, [payload]);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(autoPlay);
  const [progress, setProgress] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [duration, setDuration] = useState(10);
  const [currentTime, setCurrentTime] = useState(0);
  const [isVideoHovered, setIsVideoHovered] = useState(false);
  const [posterFrame, setPosterFrame] = useState<string | null>(null);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [linkCopied, setLinkCopied] = useState(false);
  const [internalShowCelebration, setInternalShowCelebration] = useState(false);
  const [shortcutsCollapsed, setShortcutsCollapsed] = useState(true);

  const showCelebration = externalShowCelebration !== undefined ? externalShowCelebration : internalShowCelebration;
  const timelineScrollRef = useRef<HTMLDivElement>(null);
  const progressBarRef = useRef<HTMLDivElement>(null);
  const playheadTrackRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef(0);
  const lastUiTimeRef = useRef(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const previewVideoRefs = useRef<Array<HTMLVideoElement | null>>([]);
  const videoProgressRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const calculateVideoProgress = useCallback((clientX: number) => {
    if (!videoProgressRef.current) return 0;
    const rect = videoProgressRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const percentage = Math.max(0, Math.min(100, (x / rect.width) * 100));
    return percentage;
  }, []);

  const handleVideoProgressClick = (e: React.MouseEvent<HTMLDivElement>) => {
    e.stopPropagation();
    const newProgress = calculateVideoProgress(e.clientX);
    setProgress(newProgress);
    if (videoRef.current) {
      videoRef.current.currentTime = (newProgress / 100) * duration;
    }
  };

  const calculateProgress = useCallback((clientX: number) => {
    if (!progressBarRef.current) return 0;
    const rect = progressBarRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const percentage = Math.max(0, Math.min(100, (x / rect.width) * 100));
    return percentage;
  }, []);

  const handleProgressClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const newProgress = calculateProgress(e.clientX);
    setProgress(newProgress);
    if (videoRef.current) {
      videoRef.current.currentTime = (newProgress / 100) * duration;
    }
  };

  const handleDragStart = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);

    const newProgress = calculateProgress(e.clientX);
    progressRef.current = newProgress;
    setProgress(newProgress);

    if (playheadTrackRef.current) {
      playheadTrackRef.current.style.transform = `translate3d(${newProgress}%, 0, 0)`;
    }

    if (videoRef.current) {
      const newTime = (newProgress / 100) * duration;
      videoRef.current.currentTime = newTime;
      lastUiTimeRef.current = newTime;
      setCurrentTime(newTime);
    }
  };

  const handleDragMove = useCallback((e: MouseEvent) => {
    if (!isDragging) return;

    const newProgress = calculateProgress(e.clientX);
    progressRef.current = newProgress;

    if (playheadTrackRef.current) {
      playheadTrackRef.current.style.transform = `translate3d(${newProgress}%, 0, 0)`;
    }

    if (videoRef.current) {
      const newTime = (newProgress / 100) * duration;
      videoRef.current.currentTime = newTime;

      if (Math.abs(newTime - lastUiTimeRef.current) >= 0.1) {
        lastUiTimeRef.current = newTime;
        setCurrentTime(newTime);
      }
    }
  }, [isDragging, calculateProgress, duration]);

  const handleDragEnd = useCallback(() => {
    setIsDragging(false);
    setProgress(progressRef.current);
  }, []);

  useEffect(() => {
    if (isDragging) {
      window.addEventListener('mousemove', handleDragMove);
      window.addEventListener('mouseup', handleDragEnd);
    }
    return () => {
      window.removeEventListener('mousemove', handleDragMove);
      window.removeEventListener('mouseup', handleDragEnd);
    };
  }, [isDragging, handleDragMove, handleDragEnd]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }

      switch (e.code) {
        case 'Space':
          e.preventDefault();
          togglePlay();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          if (videoRef.current) {
            const newTime = Math.max(0, videoRef.current.currentTime - 3);
            videoRef.current.currentTime = newTime;
            setCurrentTime(newTime);
            setProgress((newTime / duration) * 100);
          }
          break;
        case 'ArrowRight':
          e.preventDefault();
          if (videoRef.current) {
            const newTime = Math.min(duration, videoRef.current.currentTime + 3);
            videoRef.current.currentTime = newTime;
            setCurrentTime(newTime);
            setProgress((newTime / duration) * 100);
          }
          break;
        case 'KeyE':
          e.preventDefault();
          toast.info("重新生成当前片段");
          break;
        case 'KeyD':
          e.preventDefault();
          handleDownload();
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [duration, isPlaying]);

  useEffect(() => {
    if (autoPlay && videoRef.current && !isPlaying) {
      videoRef.current.muted = true;
      setIsMuted(true);
      videoRef.current.play().then(() => {
        setIsPlaying(true);
      }).catch(() => {
        // Autoplay blocked, ignore
      });

      if (externalShowCelebration === undefined) {
        setInternalShowCelebration(true);
        setTimeout(() => setInternalShowCelebration(false), 3000);
      }
    } else if (!autoPlay && videoRef.current && isPlaying) {
      captureFrame();
      videoRef.current.pause();
      setIsPlaying(false);
    }
  }, [autoPlay, externalShowCelebration]);

  // When leaving the Final section (autoPlay=false), stop/unload all preview videos to avoid UI freeze; main video only pause (full cleanup on unmount).
  useEffect(() => {
    if (autoPlay) return;
    try {
      previewVideoRefs.current.forEach((v) => hardCleanupVideo(v));
    } catch {}
    if (videoRef.current) {
      try { videoRef.current.pause(); } catch (_) {}
    }
  }, [autoPlay]);

  // On unmount: release main video and all preview clip videos to avoid Detached leak
  useEffect(() => {
    return () => {
      try {
        previewVideoRefs.current.forEach((v) => hardCleanupVideo(v));
        hardCleanupVideo(videoRef.current);
      } catch (_) {}
    };
  }, []);

  useEffect(() => {
    if (showCelebration && onCelebrationShown) {
      const timer = setTimeout(() => {
        onCelebrationShown();
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [showCelebration, onCelebrationShown]);

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video || isDragging) return;

    const current = video.currentTime;
    const total = video.duration || duration || 1;

    if (Math.abs(current - lastUiTimeRef.current) >= 0.1 || current === 0) {
      lastUiTimeRef.current = current;
      setCurrentTime(current);
    }

    if (!isPlaying) {
      setProgress((current / total) * 100);
    }
  };

  useEffect(() => {
    if (!isPlaying || isDragging) return;

    let rafId = 0;

    const tick = () => {
      const video = videoRef.current;
      const playhead = playheadTrackRef.current;

      if (video && playhead) {
        const total = video.duration || duration || 1;
        const p = Math.max(0, Math.min(100, (video.currentTime / total) * 100));
        playhead.style.transform = `translate3d(${p}%, 0, 0)`;
      }

      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [isPlaying, isDragging, duration]);

  const handleLoadedMetadata = () => {
    if (videoRef.current) {
      setDuration(videoRef.current.duration);
    }
  };

  const handleVideoEnded = () => {
    setIsPlaying(false);
    setProgress(0);
    setCurrentTime(0);
    if (videoRef.current) {
      videoRef.current.currentTime = 0;
    }
  };

  const captureFrame = useCallback(() => {
    if (videoRef.current && canvasRef.current) {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        try {
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
          setPosterFrame(dataUrl);
        } catch {
          // Cross-origin videos can taint the canvas and throw on toDataURL.
          // Skip poster capture to avoid crashing the whole page.
          setPosterFrame(null);
        }
      }
    }
  }, []);

  const togglePlay = () => {
    if (videoRef.current) {
      if (isPlaying) {
        captureFrame();
        videoRef.current.pause();

        const current = videoRef.current.currentTime;
        const total = videoRef.current.duration || duration || 1;
        const p = Math.max(0, Math.min(100, (current / total) * 100));

        progressRef.current = p;
        lastUiTimeRef.current = current;
        setCurrentTime(current);
        setProgress(p);
      } else {
        setPosterFrame(null);
        videoRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  const toggleMute = () => {
    if (videoRef.current) {
      videoRef.current.muted = !isMuted;
      setIsMuted(!isMuted);
    }
  };

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${String(secs).padStart(2, '0')}`;
  };

  const handleClose = () => {
    const from = (location.state as any)?.from;
    if (from === 'instant-generation') {
      navigate('/instant-generation', { state: { prompt: (location.state as any)?.prompt, skipGeneration: true } });
    } else {
      navigate(-1);
    }
  };

  const handleDownload = async () => {
    if (!finalVideoUrl) {
      toast.error("No video available to download");
      return;
    }
    if (isDownloading) return;
    setIsDownloading(true);
    setDownloadProgress(0);
    try {
      const opened = window.open(finalVideoUrl, "_blank");
      if (!opened) {
        throw new Error("Popup blocked");
      }
      setDownloadProgress(100);
      toast.success("Video download started");
    } catch (error) {
      console.error("Download failed:", error);
      toast.error("Download failed, please try again");
    } finally {
      setTimeout(() => {
        setIsDownloading(false);
        setDownloadProgress(0);
      }, 300);
    }
  };

  return (
    <div className={`${embedded ? 'relative h-full w-full' : 'fixed inset-0 z-[9999]'} bg-black/80 backdrop-blur-sm overflow-hidden`}>
      {showCelebration && (
        <div className="absolute inset-0 pointer-events-none z-[100] overflow-hidden">
          {Array.from({ length: 50 }).map((_, i) => (
            <div
              key={`confetti-${i}`}
              className="absolute"
              style={{
                left: `${Math.random() * 100}%`,
                top: '-20px',
                animation: `confettiFall ${2 + Math.random() * 2}s ease-out forwards`,
                animationDelay: `${Math.random() * 0.5}s`,
              }}
            >
              <div
                className="w-3 h-3 rounded-sm"
                style={{
                  backgroundColor: ['#fbbf24', '#f59e0b', '#ef4444', '#ec4899', '#8b5cf6', '#06b6d4'][Math.floor(Math.random() * 6)],
                  transform: `rotate(${Math.random() * 360}deg)`,
                  animation: `confettiSpin ${0.5 + Math.random() * 0.5}s linear infinite`,
                }}
              />
            </div>
          ))}

          {Array.from({ length: 20 }).map((_, i) => (
            <div
              key={`star-${i}`}
              className="absolute"
              style={{
                left: `${10 + Math.random() * 80}%`,
                top: `${10 + Math.random() * 30}%`,
                animation: `sparkleIn ${0.5 + Math.random() * 0.5}s ease-out forwards`,
                animationDelay: `${0.2 + Math.random() * 0.8}s`,
                opacity: 0,
              }}
            >
              <Sparkles
                className="text-amber-400"
                style={{
                  width: `${16 + Math.random() * 24}px`,
                  height: `${16 + Math.random() * 24}px`,
                  filter: 'drop-shadow(0 0 8px rgba(251, 191, 36, 0.8))',
                }}
              />
            </div>
          ))}

          <div
            className="absolute top-20 left-1/2 -translate-x-1/2 flex items-center gap-4"
            style={{
              animation: 'celebrationTextIn 0.8s cubic-bezier(0.34, 1.56, 0.64, 1) forwards',
              opacity: 0,
            }}
          >
            <PartyPopper className="w-10 h-10 text-amber-400" style={{ transform: 'scaleX(-1)' }} />
            <span className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-amber-300 via-yellow-400 to-amber-300 drop-shadow-[0_0_20px_rgba(251,191,36,0.5)]">
              Your Video is Ready!
            </span>
            <PartyPopper className="w-10 h-10 text-amber-400" />
          </div>
        </div>
      )}

      <div
        className="absolute inset-0 opacity-20 pointer-events-none mix-blend-overlay"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%' height='100%' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        }}
      />

      <ReelHeader
        title="Cinema Reel"
        statusText="PLAYBACK"
        statusIcon={Film}
        className="z-50"
        rightContent={
          <button
            className="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-red-500/30 hover:border-red-400/50 transition-all duration-300 flex items-center justify-center group"
            onClick={handleClose}
          >
            <X className="w-5 h-5 transition-transform duration-300 group-hover:rotate-90 group-hover:scale-110" />
          </button>
        }
      />

      <div className="absolute inset-0 top-32 bottom-32 flex flex-col items-center justify-center px-8">
        <div className="bg-[#1a1510] border border-amber-900/30 rounded-lg shadow-[0_8px_32px_rgba(0,0,0,0.5),0_0_60px_rgba(180,83,9,0.15)]">
          <div className="relative">
            <div className="flex justify-center gap-[40px] overflow-hidden py-1.5">
              {Array.from({ length: 20 }).map((_, i) => (
                <div key={`top-${i}`} className="flex-shrink-0 w-5 h-5 rounded-[3px] bg-[#0a0806] border border-amber-900/25" />
              ))}
            </div>

            <div className="relative p-2">
              <div className="absolute inset-0 pointer-events-none z-20" />

              <div
                className="relative w-[1800px] aspect-video bg-[#1a1510] overflow-hidden group"
                onMouseEnter={() => setIsVideoHovered(true)}
                onMouseLeave={() => setIsVideoHovered(false)}
              >
                <div
                  className="absolute inset-0 bg-amber-900/5 mix-blend-soft-light pointer-events-none z-10 transition-opacity duration-300"
                  style={{ opacity: 0.12 }}
                />

                <canvas ref={canvasRef} className="hidden" />

                <video
                  ref={videoRef}
                  src={finalVideoUrl || ""}
                  className="w-full h-full object-cover cursor-pointer focus:outline-none focus:ring-0 active:outline-none"
                  onTimeUpdate={handleTimeUpdate}
                  onLoadedMetadata={handleLoadedMetadata}
                  onEnded={handleVideoEnded}
                  onClick={togglePlay}
                  muted={isMuted}
                  playsInline
                  preload="metadata"
                />

                {!isPlaying && posterFrame && (
                  <img
                    src={posterFrame}
                    alt="Paused frame"
                    className="absolute inset-0 w-full h-full object-cover pointer-events-none z-[5]"
                  />
                )}

                <div
                  className={`absolute bottom-0 left-0 right-0 z-30 transition-all duration-300 ${
                    isVideoHovered || !isPlaying ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'
                  }`}
                >
                  <div className="bg-gradient-to-t from-black/80 via-black/40 to-transparent pt-12 pb-4 px-4">
                    <div className="flex items-center justify-between mb-2 text-xs font-mono text-amber-100/80">
                      <span>{formatTime(currentTime)}</span>
                      <span>{formatTime(duration)}</span>
                    </div>

                    <div
                      ref={videoProgressRef}
                      className="relative h-2 bg-white/20 rounded-full cursor-pointer group/progress overflow-hidden"
                      onClick={handleVideoProgressClick}
                    >
                      <div className="absolute h-full bg-white/20 rounded-full" style={{ width: '100%' }} />
                      <div
                        className="absolute h-full bg-gradient-to-r from-amber-500 to-amber-400 rounded-full"
                        style={{ width: `${progress}%` }}
                      />
                      <div
                        className="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-amber-400 rounded-full shadow-[0_0_10px_rgba(251,191,36,0.6)] opacity-0 group-hover/progress:opacity-100 transition-opacity pointer-events-none"
                        style={{ left: `${progress}%`, marginLeft: '-8px' }}
                      />
                    </div>

                    <div className="flex items-center justify-between mt-3">
                      <div className="flex items-center gap-3">
                        <button
                          onClick={(e) => { e.stopPropagation(); togglePlay(); }}
                          className="w-8 h-8 rounded-full bg-amber-500/80 hover:bg-amber-500 flex items-center justify-center transition-colors"
                        >
                          {isPlaying ? (
                            <Pause className="w-4 h-4 text-amber-950" />
                          ) : (
                            <Play className="w-4 h-4 text-amber-950 ml-0.5" />
                          )}
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); toggleMute(); }}
                          className="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center transition-colors"
                        >
                          {isMuted ? (
                            <VolumeX className="w-4 h-4 text-amber-100" />
                          ) : (
                            <Volume2 className="w-4 h-4 text-amber-100" />
                          )}
                        </button>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (videoRef.current) {
                            if (document.fullscreenElement) {
                              document.exitFullscreen();
                            } else {
                              videoRef.current.requestFullscreen();
                            }
                          }
                        }}
                        className="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center transition-colors"
                      >
                        <Maximize className="w-4 h-4 text-amber-100" />
                      </button>
                    </div>
                  </div>
                </div>

                {!isPlaying && !isVideoHovered && (
                  <div
                    className="absolute inset-0 flex items-center justify-center cursor-pointer"
                    onClick={togglePlay}
                  >
                    <button
                      className="w-24 h-24 bg-amber-900/50 backdrop-blur-sm rounded-full flex items-center justify-center border-2 border-amber-400/50 hover:bg-amber-900/70 hover:border-amber-400 hover:scale-110 transition-all duration-300"
                    >
                      <Play className="w-12 h-12 text-amber-100 ml-1" />
                    </button>
                  </div>
                )}

                <div
                  className="absolute inset-0 bg-gradient-to-br from-orange-500/10 via-transparent to-transparent pointer-events-none"
                  style={{ opacity: 0.3 }}
                />

                <div
                  className="absolute inset-0 pointer-events-none"
                  style={{ boxShadow: 'inset 0 0 80px rgba(0,0,0,0.4)' }}
                />

                <div className="absolute inset-0 pointer-events-none animate-pulse" style={{
                  background: 'radial-gradient(circle at center, rgba(251,191,36,0.08) 0%, transparent 70%)',
                }} />
              </div>
            </div>
          </div>

          <div className="w-[1800px] p-4 relative z-10">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setZoomLevel(prev => Math.max(1, prev - 0.5))}
                      disabled={zoomLevel <= 1}
                      className="w-7 h-7 rounded-md bg-white/5 border border-white/10 flex items-center justify-center hover:bg-white/10 hover:border-amber-400/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                    >
                      <ZoomOut className="w-4 h-4 text-amber-200/80" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="bg-amber-950/90 border-amber-700/50 text-amber-100">
                    缩小
                  </TooltipContent>
                </Tooltip>

                <div className="px-2 py-1 min-w-[40px] text-center">
                  <span className="text-amber-200/50 font-mono text-xs">{zoomLevel.toFixed(1)}x</span>
                </div>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setZoomLevel(prev => Math.min(4, prev + 0.5))}
                      disabled={zoomLevel >= 4}
                      className="w-7 h-7 rounded-md bg-white/5 border border-white/10 flex items-center justify-center hover:bg-white/10 hover:border-amber-400/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                    >
                      <ZoomIn className="w-4 h-4 text-amber-200/80" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="bg-amber-950/90 border-amber-700/50 text-amber-100">
                    放大
                  </TooltipContent>
                </Tooltip>
              </div>

              <div className="px-2 py-1">
                <span className="text-amber-200/40 font-mono text-xs">{formatTime(currentTime)} / {formatTime(duration)}</span>
              </div>
            </div>

            <div
              ref={timelineScrollRef}
              className="overflow-x-auto scrollbar-thin scrollbar-thumb-amber-500/50 scrollbar-track-transparent"
            >
              <div style={{ width: `${100 * zoomLevel}%`, minWidth: '100%' }}>
                <div className="flex items-end justify-between mb-2 px-1 transition-all duration-200">
                  {Array.from({ length: Math.max(12, Math.floor(12 * zoomLevel)) }).map((_, i) => {
                    const totalMarkers = Math.max(12, Math.floor(12 * zoomLevel));
                    const seconds = Math.floor((i / (totalMarkers - 1)) * duration);
                    return (
                      <div key={i} className="flex flex-col items-center">
                        <span className="text-[10px] text-amber-400/60 font-mono">
                          {Math.floor(seconds / 60)}:{String(Math.floor(seconds % 60)).padStart(2, '0')}
                        </span>
                        <div className="w-px h-2 bg-amber-600/40 mt-0.5" />
                      </div>
                    );
                  })}
                </div>

                <div
                  ref={progressBarRef}
                  className={`relative h-36 bg-[#0a0806] rounded-lg cursor-pointer select-none transition-all duration-200 ${isDragging ? 'cursor-grabbing' : 'cursor-pointer'}`}
                  onClick={handleProgressClick}
                  onMouseDown={handleDragStart}
                >
                  <div className="absolute inset-0 flex gap-1 p-1 pointer-events-none">
                    {clips.map((clip, i) => (
                      <div
                        key={i}
                        className="relative h-full rounded-md border border-white/10 group/clip hover:border-amber-400/50 transition-colors pointer-events-auto cursor-pointer"
                        style={{ width: `${clip.end - clip.start}%` }}
                        onClick={(e) => {
                          e.stopPropagation();
                          const newProgress = clip.start;
                          setProgress(newProgress);
                          if (videoRef.current) {
                            videoRef.current.currentTime = (newProgress / 100) * duration;
                          }
                        }}
                      >
                        <video
                          ref={(el) => { previewVideoRefs.current[i] = el; }}
                          src={autoPlay ? clip.video : undefined}
                          className="absolute inset-0 w-full h-full object-cover"
                          autoPlay={autoPlay}
                          muted
                          loop
                          playsInline
                          preload="metadata"
                        />
                        <div className="absolute inset-0 flex items-center justify-center z-10">
                          <span className="text-xs font-medium text-amber-100 bg-black/50 px-2 py-0.5 rounded opacity-0 group-hover/clip:opacity-100 transition-opacity">
                            {clip.label}
                          </span>
                        </div>
                        <div className="absolute top-0 left-0 right-0 h-2 flex z-10">
                          {Array.from({ length: 8 }).map((_, k) => (
                            <div key={k} className="flex-1 border-r border-black/30 bg-[#1a1510]/50" />
                          ))}
                        </div>
                        <div className="absolute bottom-0 left-0 right-0 h-2 flex z-10">
                          {Array.from({ length: 8 }).map((_, k) => (
                            <div key={k} className="flex-1 border-r border-black/30 bg-[#1a1510]/50" />
                          ))}
                        </div>
                        <div className="absolute -bottom-10 left-1/2 -translate-x-1/2 opacity-0 group-hover/clip:opacity-100 transition-all duration-200 z-30">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toast.info(`重新生成 "${clip.label}" 片段`);
                                }}
                                className="w-8 h-8 rounded-full bg-black/40 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-black/60 hover:border-amber-400/50 hover:scale-110 transition-all duration-200 shadow-lg"
                              >
                                <RefreshCw className="w-4 h-4 text-amber-100" />
                              </button>
                            </TooltipTrigger>
                            <TooltipContent side="bottom" className="bg-amber-950/90 border-amber-700/50 text-amber-100">
                              Regenerate
                            </TooltipContent>
                          </Tooltip>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div
                    ref={playheadTrackRef}
                    className="absolute top-0 bottom-0 left-0 w-full z-20 pointer-events-none will-change-transform"
                    style={
                      isPlaying && !isDragging
                        ? { transition: 'none' }
                        : {
                          transform: `translate3d(${progress}%, 0, 0)`,
                          transition: isDragging ? 'none' : 'transform 0.08s linear',
                        }
                    }
                  >
                    <div className="absolute -top-1 left-0 -translate-x-1/2 w-0 h-0 border-l-[8px] border-r-[8px] border-t-[10px] border-l-transparent border-r-transparent border-t-red-500 drop-shadow-[0_0_4px_rgba(239,68,68,0.8)]" />
                    <div className="absolute top-2 bottom-0 left-0 -translate-x-1/2 w-0.5 bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]" />
                    <div className="absolute -bottom-1 left-0 -translate-x-1/2 w-0 h-0 border-l-[8px] border-r-[8px] border-b-[10px] border-l-transparent border-r-transparent border-b-red-500 drop-shadow-[0_0_4px_rgba(239,68,68,0.8)]" />
                  </div>
                </div>
              </div>
            </div>

            <div className="flex justify-center gap-[40px] overflow-hidden py-1.5">
              {Array.from({ length: 20 }).map((_, i) => (
                <div key={i} className="flex-shrink-0 w-5 h-5 rounded-[3px] bg-[#0a0806] border border-amber-900/25" />
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="absolute bottom-0 left-0 right-0 z-40">
        <div className="bg-gradient-to-t from-[#1a1510] via-[#1a1510]/95 to-transparent pt-12 pb-4 px-8">
          <div className="flex justify-center gap-4 mb-8">
            <button
              onClick={handleDownload}
              disabled={isDownloading}
              className={`relative h-16 px-10 rounded-full backdrop-blur-md border flex items-center justify-center gap-3 transition-all duration-200 shadow-lg overflow-hidden ${
                isDownloading
                  ? 'bg-amber-500/20 border-amber-400/50 cursor-not-allowed'
                  : 'bg-black/40 border-white/20 hover:bg-black/60 hover:border-amber-400/50 hover:scale-105'
              }`}
            >
              {isDownloading && (
                <div
                  className="absolute inset-0 bg-gradient-to-r from-amber-500/40 to-amber-400/40 transition-all duration-200"
                  style={{ width: `${Math.min(downloadProgress, 100)}%` }}
                />
              )}

              <div className="relative z-10 flex items-center gap-3">
                {isDownloading ? (
                  <>
                    <div className="w-7 h-7 border-3 border-amber-100/30 border-t-amber-100 rounded-full animate-spin" />
                    <span className="text-amber-100 font-medium text-lg">
                      {Math.min(Math.round(downloadProgress), 100)}%
                    </span>
                  </>
                ) : (
                  <>
                    <Download className="w-7 h-7 text-amber-100" />
                    <span className="text-amber-100 font-medium text-lg">Download</span>
                  </>
                )}
              </div>
            </button>

            <button
              onClick={() => {
                const shareUrl = window.location.href;
                navigator.clipboard.writeText(shareUrl).then(() => {
                  setLinkCopied(true);
                  setTimeout(() => {
                    setLinkCopied(false);
                  }, 2000);
                }).catch(() => {
                  toast.error("Failed to copy link");
                });
              }}
              className={`h-16 px-10 rounded-full backdrop-blur-md border flex items-center justify-center gap-3 transition-all duration-200 shadow-lg ${
                linkCopied
                  ? 'bg-green-500/20 border-green-400/50'
                  : 'bg-black/40 border-white/20 hover:bg-black/60 hover:border-amber-400/50 hover:scale-105'
              }`}
            >
              <Share2 className={`w-7 h-7 transition-colors ${linkCopied ? 'text-green-300' : 'text-amber-100'}`} />
              <span className={`font-medium text-lg transition-colors ${linkCopied ? 'text-green-300' : 'text-amber-100'}`}>
                {linkCopied ? 'Link Copied!' : 'Share My Video'}
              </span>
            </button>
          </div>

          <div
            className="flex items-center mt-4 pt-3 border-t border-amber-800/30 h-10 cursor-pointer"
            onClick={() => setShortcutsCollapsed(!shortcutsCollapsed)}
          >
            {shortcutsCollapsed ? (
              <div className="text-amber-200/30 hover:text-amber-200/50 transition-colors duration-200 text-xs h-full flex items-center ml-4">
                Shortcuts
              </div>
            ) : (
              <div className="flex items-center justify-center gap-6 h-full">
                <div className="flex items-center gap-2">
                  <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">Space</kbd>
                  <span className="text-amber-200/30 text-xs">Play/Pause</span>
                </div>
                <div className="flex items-center gap-2">
                  <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">E</kbd>
                  <span className="text-amber-200/30 text-xs">Regenerate</span>
                </div>
                <div className="flex items-center gap-2">
                  <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">←</kbd>
                  <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">→</kbd>
                  <span className="text-amber-200/30 text-xs">Seek ±3s</span>
                </div>
                <div className="flex items-center gap-2">
                  <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">↑</kbd>
                  <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">↓</kbd>
                  <span className="text-amber-200/30 text-xs">Page</span>
                </div>
                <div className="flex items-center gap-2">
                  <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">D</kbd>
                  <span className="text-amber-200/30 text-xs">Download</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default VideoCheckFinalPlayer;



