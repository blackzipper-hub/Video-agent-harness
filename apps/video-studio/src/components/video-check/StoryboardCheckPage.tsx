import React, { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { ReelHeader } from "@/components/ui/reel-header";
import { api, videoAnalysisApi } from "@/services/api";
import { getVideoCheckPayload, setVideoCheckPayload } from "./videoCheckPayload";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronDown,
  Download,
  Share2,
  RotateCw,
  Film,
  X,
  Check,
  LayoutGrid,
  Pencil,
  Save,
  Send,
  Link2,
  ZoomIn,
  ZoomOut
} from "lucide-react";

// Version type
interface FrameVersion {
  id: string;
  image: string;
  prompt: string;
  createdAt: string;
  uuid?: string;
  success?: boolean;
}

// Frame type with versions
interface KeyFrame {
  id: string;
  uuid?: string;
  shotNumber?: number;
  content: string;
  cameraShot: string;
  cameraAngle: string;
  timestamp: string;
  status: string;
  image: string;
  prompt: string;
  versions: FrameVersion[];
  currentVersionIndex: number;
}

// Shot connection type
interface ShotConnection {
  id: string;
  frameIndices: number[];
  color: string;
  label: string;
}

function buildKeyFramesFromPayload(payload: any): KeyFrame[] {
  const keyframes = payload?.keyframesData?.keyframes || [];
  return keyframes.map((kf: any, idx: number) => {
    const versions: FrameVersion[] = (kf.versions || []).map((v: any, vIdx: number) => ({
      id: v.uuid || `${kf.uuid}-v${vIdx + 1}`,
      uuid: v.uuid,
      image: v.keyframe_url || "",
      prompt: v.t2i_prompt || v.prompt || "",
      createdAt: v.created_at || v.createdAt || "",
      success: v.success,
    }));

    const currentVersionIndex = kf.current_version_index || 0;
    const currentVersion = versions[currentVersionIndex] || versions[0];
    const status = currentVersion?.success === false ? "failed" : (currentVersion?.image ? "completed" : (kf.status || "pending"));
    
    return {
      id: kf.uuid || String(kf.shot_number || idx + 1).padStart(2, "0"),
      uuid: kf.uuid,
      shotNumber: kf.shot_number || idx + 1,
      content: kf.content || kf.description || `Shot ${kf.shot_number || idx + 1}`,
      cameraShot: kf.camera_shot || "",
      cameraAngle: kf.camera_angle || "",
      timestamp: kf.timestamp || "",
      status,
      image: currentVersion?.image || "",
      prompt: currentVersion?.prompt || "",
      versions,
      currentVersionIndex,
    };
  });
}

interface StoryboardCheckPageProps {
  embedded?: boolean;
  initialIndex?: number;
  initialGridView?: boolean;
}

const StoryboardCheckPage = ({ embedded = false, initialIndex: propInitialIndex, initialGridView }: StoryboardCheckPageProps) => {
  const navigate = useNavigate();
  const location = useLocation();
  const filmStripRef = useRef<HTMLDivElement>(null);
  const thumbnailNavRef = useRef<HTMLDivElement>(null);
  const thumbnailRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const frameRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Get initial index from prop (when embedded) or URL params
  const searchParams = new URLSearchParams(location.search);
  const urlIndex = parseInt(searchParams.get('index') || '0', 10);
  const urlView = searchParams.get('view');
  const urlGalleryView = urlView === 'gallery';
  const urlGridView = urlView === 'grid';
  const initialIndex = propInitialIndex !== undefined ? propInitialIndex : urlIndex;

  const [keyFrames, setKeyFrames] = useState(() => buildKeyFramesFromPayload(getVideoCheckPayload()));
  const [currentIndex, setCurrentIndex] = useState(initialIndex);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [editedPromptText, setEditedPromptText] = useState("");
  const [showGridView, setShowGridView] = useState(() => {
    // Priority: prop > URL param > localStorage
    // Note: view=gallery means filmstrip/lightbox view (showGridView=false)
    // view=grid means grid view (showGridView=true)
    if (initialGridView !== undefined) return initialGridView;
    if (urlGalleryView) return false; // gallery = filmstrip view
    if (urlGridView) return true;
    const saved = localStorage.getItem('lightbox-grid-view');
    return saved === 'true';
  });
  const [gridCols, setGridCols] = useState(() => {
    const saved = localStorage.getItem('lightbox-grid-cols');
    const isMobileScreen = window.innerWidth < 768;
    const defaultCols = isMobileScreen ? 2 : 6;
    const parsed = saved ? parseInt(saved, 10) : defaultCols;
    return [1, 2, 3, 6, 9, 12, 15].includes(parsed) ? parsed : defaultCols;
  });
  const [regeneratePrompt, setRegeneratePrompt] = useState("");
  const [instructionText, setInstructionText] = useState("");
  const [showEditPrompt, setShowEditPrompt] = useState(false);
  const [shortcutsCollapsed, setShortcutsCollapsed] = useState(true);
  const [regenerateCount, setRegenerateCount] = useState(1);
  const [showRegenerateNotice, setShowRegenerateNotice] = useState(false);
  const [showVersionPanel, setShowVersionPanel] = useState(true);
  const [fullscreenRefImage, setFullscreenRefImage] = useState<string | null>(null);
  const [customRefImage, setCustomRefImage] = useState<string | null>(null);
  const [imageRatios, setImageRatios] = useState<Record<string, number>>({});
  const refImageInputRef = React.useRef<HTMLInputElement>(null);
  
  // Multi-selection state for grid view
  const [selectedFrames, setSelectedFrames] = useState<Set<number>>(new Set());
  const [selectionStart, setSelectionStart] = useState<number | null>(null);
  const [isShiftPressed, setIsShiftPressed] = useState(false);
  const [showShotPanel, setShowShotPanel] = useState(true);

  const getImageAspectRatio = (imageUrl?: string) => {
    if (!imageUrl) return "16/9";
    const ratio = imageRatios[imageUrl];
    return ratio && ratio < 1 ? "9/16" : "16/9";
  };
  const [showShotConnections, setShowShotConnections] = useState(false);
  const [hoveredVersionFrame, setHoveredVersionFrame] = useState<number | null>(null);

  // Update frame version
  const updateFrameVersion = (frameIndex: number, versionIndex: number) => {
    setKeyFrames(prev => prev.map((frame, idx) => 
      idx === frameIndex 
        ? { ...frame, currentVersionIndex: versionIndex }
        : frame
    ));
  };

  // Shot connections - AI auto-connected frames (mock data)
  const shotColors = ['#22d3ee', '#a855f7', '#f472b6', '#4ade80', '#fb923c', '#60a5fa'];
  const [shotConnections, setShotConnections] = useState<ShotConnection[]>([
    { id: 'shot-1', frameIndices: [0, 1, 2], color: '#22d3ee', label: 'Shot A' },
    { id: 'shot-2', frameIndices: [5, 6], color: '#a855f7', label: 'Shot B' },
    { id: 'shot-3', frameIndices: [10, 11, 12, 13], color: '#f472b6', label: 'Shot C' },
    { id: 'shot-4', frameIndices: [18, 19, 20], color: '#4ade80', label: 'Shot D' },
  ]);

  // Helper to get shot info for a frame
  const getFrameShotInfo = (frameIndex: number) => {
    for (const shot of shotConnections) {
      const idx = shot.frameIndices.indexOf(frameIndex);
      if (idx !== -1) {
        return {
          shot,
          isFirst: idx === 0,
          isLast: idx === shot.frameIndices.length - 1,
          position: idx + 1,
          total: shot.frameIndices.length
        };
      }
    }
    return null;
  };

  // Track shift key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Shift') setIsShiftPressed(true);
      if (e.key === 'Escape') {
        setSelectedFrames(new Set());
        setSelectionStart(null);
      }
    };
    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.key === 'Shift') setIsShiftPressed(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, []);

  const handleFrameClick = (index: number, e: React.MouseEvent) => {
    if (e.shiftKey && selectionStart !== null) {
      // Shift+click: select range
      const start = Math.min(selectionStart, index);
      const end = Math.max(selectionStart, index);
      const newSelection = new Set<number>();
      for (let i = start; i <= end; i++) {
        newSelection.add(i);
      }
      setSelectedFrames(newSelection);
    } else if (e.ctrlKey || e.metaKey) {
      // Ctrl/Cmd+click: toggle single frame
      setSelectedFrames(prev => {
        const newSet = new Set(prev);
        if (newSet.has(index)) {
          newSet.delete(index);
        } else {
          newSet.add(index);
        }
        return newSet;
      });
      setSelectionStart(index);
    } else {
      // Regular click: toggle selection if already selected, otherwise select single
      if (selectedFrames.has(index) && selectedFrames.size === 1) {
        // Click on already selected single item: deselect
        setSelectedFrames(new Set());
        setSelectionStart(null);
      } else {
        // Select single and set as current
        setSelectedFrames(new Set([index]));
        setSelectionStart(index);
        setCurrentIndex(index);
      }
    }
  };

  const handleConnectToShot = () => {
    if (selectedFrames.size < 2) {
      toast.error("Please select at least 2 consecutive frames");
      return;
    }
    
    const sortedFrames = Array.from(selectedFrames).sort((a, b) => a - b);
    
    // Check if frames are consecutive
    let isConsecutive = true;
    for (let i = 1; i < sortedFrames.length; i++) {
      if (sortedFrames[i] - sortedFrames[i - 1] !== 1) {
        isConsecutive = false;
        break;
      }
    }
    
    if (!isConsecutive) {
      toast.error("Please select consecutive frames only");
      return;
    }
    
    // Check if any frame is already in a shot
    const alreadyConnected = sortedFrames.some(idx => getFrameShotInfo(idx) !== null);
    if (alreadyConnected) {
      toast.error("Some frames are already connected to a shot");
      return;
    }
    
    // Create new shot connection
    const newShotId = `shot-${Date.now()}`;
    const colorIndex = shotConnections.length % shotColors.length;
    const shotLabel = `Shot ${String.fromCharCode(65 + shotConnections.length)}`;
    
    setShotConnections(prev => [...prev, {
      id: newShotId,
      frameIndices: sortedFrames,
      color: shotColors[colorIndex],
      label: shotLabel
    }]);
    
    const frameNumbers = sortedFrames.map(i => i + 1).join(', ');
    toast.success(`Connected frames ${frameNumbers} to ${shotLabel}`, {
      description: `${sortedFrames.length} frames will be rendered as a single shot`
    });
    
    // Clear selection after connecting
    setSelectedFrames(new Set());
    setSelectionStart(null);
  };

  const handleRefImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (event) => {
        setCustomRefImage(event.target?.result as string);
      };
      reader.readAsDataURL(file);
    }
    // Reset input so same file can be re-selected
    if (refImageInputRef.current) {
      refImageInputRef.current.value = '';
    }
  };

  const clearCustomRefImage = () => {
    setCustomRefImage(null);
  };

  const getCurrentRefImage = () => {
    return customRefImage || currentFrame?.versions?.[currentFrame.currentVersionIndex]?.image || currentFrame?.image || null;
  };

  // Gallery centering - measure actual rendered frame width
  const GALLERY_ITEM_GAP_PX = 24; // gap-6
  const [galleryFrameWidth, setGalleryFrameWidth] = useState(0);

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
  }, [showGridView, keyFrames.length]);

  // Save view preferences
  useEffect(() => {
    localStorage.setItem('lightbox-grid-view', String(showGridView));
  }, [showGridView]);

  useEffect(() => {
    localStorage.setItem('lightbox-grid-cols', String(gridCols));
  }, [gridCols]);

  // Update prompt when switching frames while Edit Prompt is open
  useEffect(() => {
    if (showEditPrompt && currentFrame) {
      setRegeneratePrompt(currentFrame.prompt || "");
    }
  }, [currentIndex, showEditPrompt]);

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
      } else if (e.key === 'ArrowRight' && currentIndex < keyFrames.length - 1) {
        setCurrentIndex(currentIndex + 1);
      } else if (e.key === 'r' || e.key === 'R') {
        const currentFrame = keyFrames[currentIndex];
        if (currentFrame) {
          handleRegenerate(currentFrame.id);
          toast.success("Regenerating...");
        }
      } else if ((e.key === 'e' || e.key === 'E') && !editingPrompt) {
        handleEditPrompt();
      } else if (e.key === 'v' || e.key === 'V') {
        setShowGridView(prev => !prev);
      } else if (e.key >= '1' && e.key <= '9') {
        const chapterNum = parseInt(e.key, 10);
        const targetIndex = (chapterNum - 1) * 20;
        if (targetIndex < keyFrames.length) {
          setCurrentIndex(targetIndex);
          toast.success(`Jumped to Section ${chapterNum}`);
        }
      } else if (e.key === 'd' || e.key === 'D') {
        handleDownload();
      }
    };
    
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [currentIndex, keyFrames, navigate, editingPrompt, editedPromptText]);

  // Scroll thumbnail into view (horizontal only; avoid vertical scroll that can snap the parent page)
  useEffect(() => {
    const nav = thumbnailNavRef.current;
    const thumb = thumbnailRefs.current[currentIndex];
    if (!nav || !thumb) return;

    const left = thumb.offsetLeft - nav.clientWidth / 2 + thumb.clientWidth / 2;
    nav.scrollTo({ left, behavior: 'smooth' });
  }, [currentIndex]);

  const handleRegenerate = async (
    frameId: string,
    customPrompt?: string,
    options?: { instructionOnly?: boolean },
  ) => {
    try {
      const payload = getVideoCheckPayload();
      if (!payload?.keyframesData?.keyframes) {
        toast.error("No keyframes data");
        return;
      }

      const keyframe = payload.keyframesData.keyframes.find((k: any) => k.uuid === frameId || String(k.shot_number) === frameId);
      if (!keyframe) {
        toast.error("Keyframe not found");
        return;
      }

      const targetVersionIndex = keyframe.current_version_index || 0;
      const currentVersion = keyframe.versions?.[targetVersionIndex] || keyframe.versions?.[0];
      if (!currentVersion) {
        toast.error("No version data available");
        return;
      }

    setShowRegenerateNotice(true);
    setTimeout(() => setShowRegenerateNotice(false), 1000);
    setRegeneratePrompt("");

      const instructionOnly = Boolean(options?.instructionOnly && (customPrompt || "").trim());
      const response = await api.videoEditing.regenerateKeyframes({
        keyframes: [{
          uuid: keyframe.uuid,
          versions: instructionOnly
            ? [{
                uuid: currentVersion.uuid,
                instruction: (customPrompt || "").trim(),
                regenerate_strategy: 'instruction_regenerate',
              }]
            : [{
                uuid: currentVersion.uuid,
                custom_prompt: customPrompt || regeneratePrompt || currentVersion.t2i_prompt,
                regenerate_strategy: 'prompt_regenerate',
              }],
        }],
        user_option: payload.userOption || {},
      });

      if (response.code !== 0) {
        toast.error(response.message || "Regenerate failed");
        return;
      }

      const threadId = payload.threadId;
      if (threadId) {
        const [updatedKeyframes, updatedVideos] = await Promise.all([
          videoAnalysisApi.getKeyframesDataByThreadId(threadId),
          videoAnalysisApi.getVideoGenerationsDataByThreadId(threadId),
        ]);
        if (updatedKeyframes?.code === 0) payload.keyframesData = updatedKeyframes.data;
        if (updatedVideos?.code === 0) payload.videosData = updatedVideos.data;
      }

      setVideoCheckPayload(payload);
      setKeyFrames(buildKeyFramesFromPayload(payload));

      toast.success("Regeneration complete!");
    } catch (e: any) {
      toast.error(`Regenerate failed: ${e?.message || e}`);
    }
  };

  const handleRegenerateSubmit = () => {
    if (currentFrame) {
      handleRegenerate(currentFrame.id, regeneratePrompt);
    }
  };

  const handleDownload = () => {
    const currentFrame = keyFrames[currentIndex];
    if (currentFrame) {
      const link = document.createElement('a');
      link.href = currentFrame.image;
      link.download = `storyboard-${String(currentIndex + 1).padStart(3, '0')}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      toast.success("Downloading frame...");
    }
  };

  const handleClose = () => {
    navigate('/create');
  };

  const handleEditPrompt = () => {
    setEditedPromptText(currentFrame?.prompt || "");
    setEditingPrompt(true);
  };

  const handleSavePrompt = () => {
    if (currentFrame) {
      setKeyFrames(prev => prev.map((frame, idx) => 
        idx === currentIndex ? { ...frame, prompt: editedPromptText } : frame
      ));
      setEditingPrompt(false);
      toast.success("Prompt updated!");
    }
  };

  const handleCancelEdit = () => {
    setEditingPrompt(false);
    setEditedPromptText("");
  };

  const handleEditPromptScrollWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    // Force wheel scrolling to stay inside the expanded prompt panel.
    // This prevents the outer /video-check snap container from consuming the scroll.
    e.stopPropagation();
    e.preventDefault();
    const el = e.currentTarget;
    el.scrollTop += e.deltaY;
  };

  const isFirst = currentIndex === 0;
  const isLast = currentIndex === keyFrames.length - 1;
  const currentFrame = keyFrames[currentIndex];

  // Real tasks may not have storyboard data yet. Avoid render crash (white screen) when empty.
  if (!currentFrame) {
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
          title="Storyboard Reel"
          statusText={`0 KEYFRAMES`}
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
            No storyboard data yet. Please generate keyframes first.
          </div>
        </div>
      </div>
    );
  }

  // Chapter info
  const currentChapter = Math.floor(currentIndex / 20) + 1;
  const totalChapters = Math.ceil(keyFrames.length / 20);
  const chapterStories: { [key: number]: string } = {
    1: "Our hero embarks on an extraordinary journey, leaving behind the familiar world to chase an impossible dream across uncharted territories.",
    2: "Challenges arise as the protagonist faces their first major obstacle, testing their resolve and forcing them to discover hidden strengths.",
    3: "A pivotal alliance forms in the darkest hour, bringing unexpected hope and new possibilities that change everything.",
    4: "The climactic confrontation begins as all forces converge, pushing our hero to their absolute limits in a battle for everything they hold dear.",
    5: "Resolution and transformation—the journey ends but a new chapter of life begins, forever changed by the trials overcome.",
  };

  const sectionSummaries: { [key: number]: string } = {
    1: "The Journey Begins",
    2: "First Challenge",
    3: "Unexpected Alliance",
    4: "Final Confrontation",
    5: "Resolution",
  };

  return (
    <div className={`${embedded ? 'relative h-full w-full' : 'fixed inset-0 z-[9999]'} bg-black/95 overflow-hidden`}>
      {/* Fullscreen Reference Image Modal */}
      {fullscreenRefImage && (
        <div 
          className="fixed inset-0 z-[10001] flex items-center justify-center bg-black/90 backdrop-blur-md cursor-pointer"
          onClick={() => setFullscreenRefImage(null)}
        >
          <button
            className="absolute top-6 right-6 w-12 h-12 rounded-full bg-white/10 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-red-500/30 hover:border-red-400/50 transition-all duration-200 z-10"
            onClick={() => setFullscreenRefImage(null)}
          >
            <X className="w-6 h-6 text-white" />
          </button>
          <img 
            src={fullscreenRefImage} 
            alt="Reference Image Fullscreen" 
            className="max-w-[90vw] max-h-[90vh] object-contain rounded-2xl border-4 border-amber-500/30 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
      
      {/* Regenerate Notice - Centered Modal */}
      {showRegenerateNotice && (
        <div className="fixed inset-0 z-[10000] flex items-center justify-center pointer-events-none">
          <div className="bg-gradient-to-br from-amber-900/90 to-amber-950/95 backdrop-blur-xl border border-amber-400/50 rounded-2xl px-8 py-6 shadow-[0_0_60px_rgba(251,191,36,0.3)] animate-scale-in">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-amber-400/20 flex items-center justify-center">
                <RotateCw className="w-6 h-6 text-amber-400 animate-spin" />
              </div>
              <div>
                <p className="text-amber-100 font-semibold text-lg">Regenerating</p>
                <p className="text-amber-300/80 text-sm">{regenerateCount} variation{regenerateCount > 1 ? 's' : ''} will be generated</p>
              </div>
            </div>
          </div>
        </div>
      )}
      {/* Film Grain Overlay */}
      <div 
        className="absolute inset-0 opacity-20 pointer-events-none mix-blend-overlay"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%' height='100%' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        }}
      />
      
      {/* Vintage Header */}
      <ReelHeader
        title="Storyboard Reel"
        statusText={`${keyFrames.length} KEYFRAMES${selectedFrames.size === 1 ? ` (#${Array.from(selectedFrames)[0] + 1})` : ''}`}
        className="z-30"
        rightContent={
          <div className="flex items-center gap-4">
            {/* Zoom Controls - shown when in grid view */}
            <div 
              className={`flex items-center gap-1 bg-amber-950/20 backdrop-blur-sm rounded-full px-1 py-1 border border-amber-800/20 transition-all duration-300 origin-right ${
                showGridView 
                  ? 'opacity-60 hover:opacity-100 scale-x-100 translate-x-0' 
                  : 'opacity-0 scale-x-0 translate-x-8 pointer-events-none'
              }`}
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className={`w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 ${
                      gridCols < 10 
                        ? 'text-amber-300 hover:bg-amber-500/20 hover:text-amber-200' 
                        : 'text-amber-300/30 cursor-not-allowed'
                    }`}
                    onClick={() => {
                      if (gridCols < 10) {
                        setGridCols(gridCols + 1);
                      }
                    }}
                    disabled={gridCols >= 10}
                  >
                    <ZoomOut className="w-4 h-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="bg-amber-950/90 border-amber-700/50 text-amber-100">
                  <p>Zoom Out</p>
                </TooltipContent>
              </Tooltip>
              
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className={`w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 ${
                      gridCols > 1 
                        ? 'text-amber-300 hover:bg-amber-500/20 hover:text-amber-200' 
                        : 'text-amber-300/30 cursor-not-allowed'
                    }`}
                    onClick={() => {
                      if (gridCols > 1) {
                        setGridCols(gridCols - 1);
                      }
                    }}
                    disabled={gridCols <= 1}
                  >
                    <ZoomIn className="w-4 h-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="bg-amber-950/90 border-amber-700/50 text-amber-100">
                  <p>Zoom In</p>
                </TooltipContent>
              </Tooltip>
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

      {/* Grid View */}
      {showGridView && (
        <div 
          className="absolute inset-0 top-20 flex px-8 pb-8"
          onClick={(e) => {
            // Deselect when clicking empty area (not on a frame)
            if (e.target === e.currentTarget) {
              setSelectedFrames(new Set());
              setSelectionStart(null);
            }
          }}
        >
          {/* Scrollable Grid Container */}
          <div 
            className="flex-1 flex justify-center overflow-hidden"
            onClick={(e) => {
              // Also handle clicks on the container but not on frames
              if (e.target === e.currentTarget) {
                setSelectedFrames(new Set());
                setSelectionStart(null);
              }
            }}
          >
            <div 
              data-scrollable 
              className="w-[80%] overflow-y-scroll scrollbar-thin scrollbar-thumb-amber-500/50 scrollbar-track-amber-950/30 hover:scrollbar-thumb-amber-500/70"
              onClick={(e) => {
                // Deselect when clicking on scrollable area
                if (e.target === e.currentTarget) {
                  setSelectedFrames(new Set());
                  setSelectionStart(null);
                }
              }}
            >
              <div 
                className="grid gap-4 transition-all duration-300 pb-24"
                style={{ gridTemplateColumns: `repeat(${gridCols}, minmax(0, 1fr))` }}
                onClick={(e) => {
                  // Deselect when clicking on grid background
                  if (e.target === e.currentTarget) {
                    setSelectedFrames(new Set());
                    setSelectionStart(null);
                  }
                }}
              >
                {(() => {
                  // Group frames: create items that can be either single frames or shot groups
                  const items: { type: 'single' | 'shot'; frameIndices: number[]; shot?: ShotConnection }[] = [];
                  const processedIndices = new Set<number>();
                  
                  keyFrames.forEach((_, index) => {
                    if (processedIndices.has(index)) return;
                    
                    // Only group into shots when showShotConnections is true
                    if (showShotConnections) {
                      const shotInfo = getFrameShotInfo(index);
                      if (shotInfo && shotInfo.isFirst) {
                        // This is the first frame of a shot group
                        items.push({
                          type: 'shot',
                          frameIndices: shotInfo.shot.frameIndices,
                          shot: shotInfo.shot
                        });
                        shotInfo.shot.frameIndices.forEach(i => processedIndices.add(i));
                        return;
                      } else if (shotInfo) {
                        // Frame is part of a shot but not the first - skip, already processed
                        return;
                      }
                    }
                    
                    // Single frame (or showShotConnections is false)
                    items.push({
                      type: 'single',
                      frameIndices: [index]
                    });
                    processedIndices.add(index);
                  });
                  
                  return items.map((item, itemIdx) => {
                    if (item.type === 'shot' && item.shot) {
                      // Render shot group with rounded background
                      const shotFrameCount = item.frameIndices.length;
                      const colSpan = Math.min(shotFrameCount, gridCols);
                      
                      return (
                        <div
                          key={`shot-${item.shot.id}`}
                          className="relative rounded-2xl p-2 transition-all"
                          style={{ 
                            gridColumn: `span ${colSpan}`,
                            backgroundColor: `${item.shot.color}20`,
                            border: `2px solid ${item.shot.color}50`,
                            boxShadow: `0 0 20px ${item.shot.color}20`
                          }}
                        >
                          {/* Shot Label */}
                          <div 
                            className="absolute -top-3 left-4 px-3 py-1 rounded-full text-xs font-bold text-white flex items-center gap-1.5 z-10"
                            style={{ backgroundColor: item.shot.color }}
                          >
                            <Link2 className="w-3 h-3" />
                            {item.shot.label}
                            <span className="opacity-70 ml-1">({shotFrameCount} frames)</span>
                          </div>
                          
                          {/* Frames inside shot */}
                          <div 
                            className="grid gap-2 mt-2"
                            style={{ gridTemplateColumns: `repeat(${Math.min(shotFrameCount, colSpan)}, minmax(0, 1fr))` }}
                          >
                            {item.frameIndices.map((frameIndex, posInShot) => {
                              const frame = keyFrames[frameIndex];
                              const currentVersion = frame.versions[frame.currentVersionIndex];
                              const displayImage = currentVersion?.image || frame.image;
                              const isSelected = selectedFrames.has(frameIndex);
                              
                              return (
                                <div 
                                  key={frame.id}
                                  className={`relative flex flex-col rounded-lg overflow-hidden cursor-pointer border-2 transition-all group ${
                                    isSelected
                                      ? 'border-cyan-400 shadow-[0_0_20px_rgba(34,211,238,0.5)] ring-2 ring-cyan-400/30'
                                      : currentIndex === frameIndex 
                                        ? 'border-amber-400 shadow-[0_0_20px_rgba(251,191,36,0.4)]' 
                                        : 'border-transparent hover:border-white/30'
                                  }`}
                                  onClick={(e) => handleFrameClick(frameIndex, e)}
                                  onMouseEnter={() => frame.versions.length > 1 && setHoveredVersionFrame(frameIndex)}
                                  onMouseLeave={() => setHoveredVersionFrame(null)}
                                >
                                  {/* Image Container */}
                                  <div className="relative" style={{ aspectRatio: getImageAspectRatio(displayImage) }}>
                                    <img 
                                      src={displayImage} 
                                      alt={frame.content}
                                      className="w-full h-full object-cover"
                                      onLoad={(event) => {
                                        const { naturalWidth, naturalHeight } = event.currentTarget;
                                        if (!naturalWidth || !naturalHeight) return;
                                        const ratio = naturalWidth / naturalHeight;
                                        setImageRatios((prev) => (prev[displayImage] === ratio ? prev : { ...prev, [displayImage]: ratio }));
                                      }}
                                    />
                                    <div className="absolute top-2 left-2 px-2 py-0.5 rounded bg-black/60 text-white text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                                      Frame {frameIndex + 1}
                                    </div>
                                    {frame.versions.length > 0 && (
                                      <div className="absolute top-2 right-2 px-2 py-0.5 rounded bg-black/60 text-white text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                                        v{frame.currentVersionIndex + 1}/{frame.versions.length}
                                      </div>
                                    )}
                                    
                                    {/* Selection Checkbox */}
                                    <div 
                                      className={`absolute top-2 left-2 w-6 h-6 rounded-md border-2 flex items-center justify-center transition-all ${
                                        isSelected
                                          ? 'bg-cyan-500 border-cyan-400'
                                          : 'bg-black/40 border-white/30 opacity-0 group-hover:opacity-100'
                                      }`}
                                    >
                                      {isSelected && <Check className="w-4 h-4 text-white" />}
                                    </div>
                                    
                                    {/* Selection Order Badge */}
                                    {isSelected && selectedFrames.size > 1 && (
                                      <div className="absolute bottom-2 left-2 px-2 py-1 bg-cyan-500/90 backdrop-blur-sm rounded text-xs text-white font-bold">
                                        {Array.from(selectedFrames).sort((a, b) => a - b).indexOf(frameIndex) + 1}
                                      </div>
                                    )}
                                    
                                    
                                    
                                    {/* Version History Slider - appears on hover when multiple versions exist */}
                                    {frame.versions.length > 1 && hoveredVersionFrame === frameIndex && (
                                      <div 
                                        className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/70 to-transparent pt-8 pb-3 px-3 animate-fade-in"
                                        onClick={(e) => e.stopPropagation()}
                                      >
                                        <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-hide">
                                          {frame.versions.map((version, vIdx) => (
                                            <button
                                              key={version.id}
                                              onClick={(e) => {
                                                e.stopPropagation();
                                                updateFrameVersion(frameIndex, vIdx);
                                              }}
                                              className={`relative flex-shrink-0 w-10 h-10 rounded-md overflow-hidden border-2 transition-all hover:scale-105 ${
                                                vIdx === frame.currentVersionIndex
                                                  ? 'border-amber-400 ring-2 ring-amber-400/50'
                                                  : 'border-white/30 hover:border-white/60'
                                              }`}
                                            >
                                              <img 
                                                src={version.image} 
                                                alt={`Version ${vIdx + 1}`}
                                                className="w-full h-full object-cover"
                                              />
                                              {vIdx === frame.currentVersionIndex && (
                                                <div className="absolute inset-0 bg-amber-400/20 flex items-center justify-center">
                                                  <Check className="w-3 h-3 text-amber-400" />
                                                </div>
                                              )}
                                              <span className="absolute bottom-0 right-0 text-[7px] font-mono text-white bg-black/60 px-0.5 rounded-tl">
                                                v{vIdx + 1}
                                              </span>
                                            </button>
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      );
                    } else {
                      // Render single frame
                      const frameIndex = item.frameIndices[0];
                      const frame = keyFrames[frameIndex];
                      const currentVersion = frame.versions[frame.currentVersionIndex];
                      const displayImage = currentVersion?.image || frame.image;
                      const isSelected = selectedFrames.has(frameIndex);
                      
                      return (
                        <div 
                          key={frame.id}
                          className={`relative rounded-lg overflow-hidden cursor-pointer border-2 transition-all group ${
                            isSelected
                              ? 'border-cyan-400 shadow-[0_0_20px_rgba(34,211,238,0.5)] ring-2 ring-cyan-400/30'
                              : currentIndex === frameIndex 
                                ? 'border-amber-400 shadow-[0_0_20px_rgba(251,191,36,0.4)]' 
                                : 'border-amber-800/50 hover:border-amber-600/70'
                          }`}
                          onClick={(e) => handleFrameClick(frameIndex, e)}
                          onMouseEnter={() => frame.versions.length > 1 && setHoveredVersionFrame(frameIndex)}
                          onMouseLeave={() => setHoveredVersionFrame(null)}
                        >
                          {/* Image Container */}
                          <div className="relative" style={{ aspectRatio: getImageAspectRatio(displayImage) }}>
                            <img 
                              src={displayImage} 
                              alt={frame.content}
                              className="w-full h-full object-cover"
                              onLoad={(event) => {
                                const { naturalWidth, naturalHeight } = event.currentTarget;
                                if (!naturalWidth || !naturalHeight) return;
                                const ratio = naturalWidth / naturalHeight;
                                setImageRatios((prev) => (prev[displayImage] === ratio ? prev : { ...prev, [displayImage]: ratio }));
                              }}
                            />
                            <div className="absolute top-2 left-2 px-2 py-0.5 rounded bg-black/60 text-white text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                              Frame {frameIndex + 1}
                            </div>
                            {frame.versions.length > 0 && (
                              <div className="absolute top-2 right-2 px-2 py-0.5 rounded bg-black/60 text-white text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                                v{frame.currentVersionIndex + 1}/{frame.versions.length}
                              </div>
                            )}
                            
                            {/* Selection Checkbox */}
                            <div 
                              className={`absolute top-2 left-2 w-6 h-6 rounded-md border-2 flex items-center justify-center transition-all ${
                                isSelected
                                  ? 'bg-cyan-500 border-cyan-400'
                                  : 'bg-black/40 border-white/30 opacity-0 group-hover:opacity-100'
                              }`}
                            >
                              {isSelected && <Check className="w-4 h-4 text-white" />}
                            </div>
                            
                            {/* Selection Order Badge */}
                            {isSelected && selectedFrames.size > 1 && (
                              <div className="absolute bottom-2 left-2 px-2 py-1 bg-cyan-500/90 backdrop-blur-sm rounded text-xs text-white font-bold">
                                {Array.from(selectedFrames).sort((a, b) => a - b).indexOf(frameIndex) + 1}
                              </div>
                            )}
                            
                            
                            
                            {/* Version History Slider - appears on hover when multiple versions exist */}
                            {frame.versions.length > 1 && hoveredVersionFrame === frameIndex && (
                              <div 
                                className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/70 to-transparent pt-8 pb-3 px-3 animate-fade-in"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-hide">
                                  {frame.versions.map((version, vIdx) => (
                                    <button
                                      key={version.id}
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        updateFrameVersion(frameIndex, vIdx);
                                      }}
                                      className={`relative flex-shrink-0 w-12 h-12 rounded-md overflow-hidden border-2 transition-all hover:scale-105 ${
                                        vIdx === frame.currentVersionIndex
                                          ? 'border-amber-400 ring-2 ring-amber-400/50'
                                          : 'border-white/30 hover:border-white/60'
                                      }`}
                                    >
                                      <img 
                                        src={version.image} 
                                        alt={`Version ${vIdx + 1}`}
                                        className="w-full h-full object-cover"
                                      />
                                      {vIdx === frame.currentVersionIndex && (
                                        <div className="absolute inset-0 bg-amber-400/20 flex items-center justify-center">
                                          <Check className="w-4 h-4 text-amber-400" />
                                        </div>
                                      )}
                                      <span className="absolute bottom-0.5 right-0.5 text-[8px] font-mono text-white bg-black/60 px-1 rounded">
                                        v{vIdx + 1}
                                      </span>
                                    </button>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    }
                  });
                })()}
              </div>
            </div>
          </div>

          {/* Selection Action Bar - shows when multiple frames selected */}
          {selectedFrames.size > 1 && (
            <div className="absolute top-24 left-1/2 -translate-x-1/2 z-50 animate-in fade-in slide-in-from-top-2 duration-200">
              <div className="flex items-center gap-3 px-4 py-3 bg-cyan-950/90 backdrop-blur-xl border border-cyan-400/50 rounded-2xl shadow-[0_0_30px_rgba(34,211,238,0.3)]">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-full bg-cyan-500/20 flex items-center justify-center">
                    <span className="text-cyan-300 font-bold text-sm">{selectedFrames.size}</span>
                  </div>
                  <span className="text-cyan-100 text-sm">frames selected</span>
                </div>
                
                <div className="h-6 w-px bg-cyan-400/30" />
                
                <button
                  onClick={handleConnectToShot}
                  className="flex items-center gap-2 px-4 py-2 rounded-xl bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-400/50 hover:border-cyan-400 text-cyan-200 hover:text-white transition-all duration-200"
                >
                  <Link2 className="w-4 h-4" />
                  <span className="text-sm font-medium">Connect Frames to Shot</span>
                </button>
                
                <button
                  onClick={() => { setSelectedFrames(new Set()); setSelectionStart(null); }}
                  className="p-2 rounded-lg hover:bg-cyan-500/20 text-cyan-300/60 hover:text-cyan-200 transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}


          {/* Grid View Regenerate Input Section with Frosted Glass Background */}
          {currentFrame && (
            <div className={`absolute ${showGridView ? 'bottom-0' : '-bottom-12 pointer-events-none'} left-0 right-0 z-40`}>
              {/* Frosted glass background */}
              <div className="absolute inset-0 bg-black/60 backdrop-blur-xl" />
              
              <div className="relative px-8 py-6 flex items-end gap-4">
                {/* Show Shots Toggle Button - Separate on left */}
                <button
                  onClick={() => setShowShotConnections(prev => !prev)}
                  className={`flex-shrink-0 h-11 px-4 rounded-full backdrop-blur-xl border flex items-center gap-2 transition-all duration-200 ${
                    showShotConnections 
                      ? 'bg-cyan-500/30 border-cyan-400/50 text-cyan-200' 
                      : 'bg-white/10 border-white/30 text-amber-100 hover:bg-white/20 hover:border-cyan-400/50'
                  }`}
                >
                  <Link2 className="w-4 h-4" />
                  <span className="font-medium text-sm">{showShotConnections ? 'Hide Shots' : 'Show Shots'}</span>
                </button>
                
                {/* Edit Module - Centered */}
                <div className="flex-1 max-w-4xl mx-auto">
                  <div className="flex flex-col bg-black/40 backdrop-blur-md border border-white/20 shadow-lg rounded-2xl overflow-hidden">
                    {/* All Actions Row */}
                    <div className="flex items-center gap-3 px-5 py-4">
                      {/* Quick Regenerate Button with Count */}
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => currentFrame && handleRegenerate(currentFrame.id)}
                          className="flex-shrink-0 h-11 px-5 rounded-full bg-white/10 backdrop-blur-xl border border-white/30 flex items-center gap-2 hover:bg-white/20 hover:border-amber-400/50 hover:scale-105 transition-all duration-200 shadow-[0_4px_16px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.1)]"
                        >
                          <RotateCw className="w-4 h-4 text-amber-100" />
                          <span className="text-amber-100 font-medium text-sm">Regenerate</span>
                        </button>
                        <div className="flex items-center h-11 rounded-full bg-white/10 backdrop-blur-xl border border-white/30 overflow-hidden">
                          <button
                            onClick={() => setRegenerateCount(prev => Math.max(1, prev - 1))}
                            className="h-full px-2 flex items-center justify-center hover:bg-white/10 transition-colors disabled:opacity-30"
                            disabled={regenerateCount <= 1}
                          >
                            <ChevronDown className="w-4 h-4 text-amber-100" />
                          </button>
                          <span className="w-6 text-center text-amber-100 text-sm font-medium">{regenerateCount}</span>
                          <button
                            onClick={() => setRegenerateCount(prev => Math.min(10, prev + 1))}
                            className="h-full px-2 flex items-center justify-center hover:bg-white/10 transition-colors disabled:opacity-30"
                            disabled={regenerateCount >= 10}
                          >
                            <ChevronUp className="w-4 h-4 text-amber-100" />
                          </button>
                        </div>
                      </div>
                      
                      <div className="h-8 w-px bg-white/20" />
                      
                      {/* Instruction Input */}
                      <div className="flex-1 flex items-center gap-2">
                        <Input
                          value={instructionText}
                          onChange={(e) => setInstructionText(e.target.value)}
                          placeholder="Add instruction, e.g., Make it more colorful..."
                          className="flex-1 h-11 bg-white/5 border border-white/10 text-amber-100 placeholder:text-amber-100/40 focus:border-amber-400/50 focus:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 rounded-xl text-sm"
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                              e.preventDefault();
                              if (instructionText.trim()) {
                                handleRegenerate(currentFrame?.id || "", instructionText, { instructionOnly: true });
                                setInstructionText("");
                              }
                            }
                          }}
                        />
                        <button
                          onClick={() => {
                            if (instructionText.trim()) {
                              handleRegenerate(currentFrame?.id || "", instructionText, { instructionOnly: true });
                              setInstructionText("");
                            }
                          }}
                          disabled={!instructionText.trim()}
                          className="flex-shrink-0 h-11 w-11 rounded-xl bg-white/10 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-white/20 hover:border-amber-400/50 hover:scale-105 transition-all duration-200 disabled:opacity-30 disabled:hover:scale-100"
                        >
                          <Send className="w-4 h-4 text-amber-100" />
                        </button>
                      </div>
                      
                      <div className="h-8 w-px bg-white/20" />
                      
                      {/* Edit Prompt Toggle Button */}
                      <button
                        onClick={() => {
                          setShowEditPrompt(!showEditPrompt);
                          if (!showEditPrompt && !regeneratePrompt) {
                            setRegeneratePrompt(currentFrame?.prompt || "");
                          }
                        }}
                        className={`flex-shrink-0 h-11 px-5 rounded-full backdrop-blur-xl border flex items-center gap-2 transition-all duration-200 shadow-[0_4px_16px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.1)] ${
                          showEditPrompt 
                            ? 'bg-amber-500/20 border-amber-400/50 text-amber-200' 
                            : 'bg-white/10 border-white/30 text-amber-100 hover:bg-white/20 hover:border-amber-400/50 hover:scale-105'
                        }`}
                      >
                        <Pencil className="w-4 h-4" />
                        <span className="text-sm font-medium">Edit Prompt</span>
                      </button>
                    </div>
                    
                    {/* Edit Prompt Expanded Area */}
                    <div 
                      className="grid transition-all duration-300 ease-out"
                      style={{ 
                        gridTemplateRows: showEditPrompt ? '1fr' : '0fr',
                        opacity: showEditPrompt ? 1 : 0,
                      }}
                      onTouchMove={(e) => e.stopPropagation()}
                    >
                      <div className="overflow-hidden min-h-0">
                        <div
                          className="px-5 pt-4 pb-4 border-t border-white/10 max-h-[42vh] overflow-y-auto overscroll-contain touch-pan-y"
                          onWheelCapture={handleEditPromptScrollWheel}
                          onTouchMoveCapture={(e) => e.stopPropagation()}
                        >
                          <div className="flex items-center justify-between mb-3">
                            <span className="text-amber-200/60 text-xs font-medium uppercase tracking-wider">Edit Full Prompt</span>
                            <button
                              onClick={() => setRegeneratePrompt(currentFrame?.prompt || "")}
                              className="text-amber-400/60 hover:text-amber-400 text-xs transition-colors"
                            >
                              Reset to Original
                            </button>
                          </div>
                          
                          {/* Reference Image and Prompt Input Row */}
                          <div className="flex gap-4">
                            {/* Reference Image */}
                            <div className="flex-shrink-0 w-32">
                              <input
                                type="file"
                                ref={refImageInputRef}
                                accept="image/*"
                                onChange={handleRefImageUpload}
                                className="hidden"
                              />
                              <div 
                                className="rounded-lg overflow-hidden border border-amber-500/30 bg-black/30 cursor-pointer hover:border-amber-400/60 hover:shadow-[0_0_20px_rgba(251,191,36,0.2)] transition-all duration-200 group relative"
                                style={{ aspectRatio: getImageAspectRatio(getCurrentRefImage() || undefined) }}
                                onClick={() => setFullscreenRefImage(getCurrentRefImage())}
                              >
                                <img 
                                  src={getCurrentRefImage() || ''} 
                                  alt="Reference" 
                                  className="w-full h-full object-cover"
                                  onLoad={(event) => {
                                    const { naturalWidth, naturalHeight } = event.currentTarget;
                                    if (!naturalWidth || !naturalHeight) return;
                                    const ratio = naturalWidth / naturalHeight;
                                    const imageUrl = getCurrentRefImage();
                                    if (!imageUrl) return;
                                    setImageRatios((prev) => (prev[imageUrl] === ratio ? prev : { ...prev, [imageUrl]: ratio }));
                                  }}
                                />
                                {customRefImage && (
                                  <div className="absolute top-1 left-1 px-1.5 py-0.5 bg-amber-500/80 rounded text-[8px] text-black font-semibold">
                                    CUSTOM
                                  </div>
                                )}
                                <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/30">
                                  <div className="w-8 h-8 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center">
                                    <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
                                    </svg>
                                  </div>
                                </div>
                              </div>
                              <div className="flex items-center justify-center gap-1 mt-1">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    refImageInputRef.current?.click();
                                  }}
                                  className="text-[10px] text-amber-400/70 hover:text-amber-400 transition-colors"
                                >
                                  Upload
                                </button>
                                {customRefImage && (
                                  <>
                                    <span className="text-amber-300/30">|</span>
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        clearCustomRefImage();
                                      }}
                                      className="text-[10px] text-red-400/70 hover:text-red-400 transition-colors"
                                    >
                                      Clear
                                    </button>
                                  </>
                                )}
                              </div>
                            </div>
                            
                            {/* Prompt Textarea */}
                            <div className="flex-1 relative">
                              <textarea
                                value={regeneratePrompt}
                                onChange={(e) => setRegeneratePrompt(e.target.value)}
                                className="w-full h-full bg-white/5 rounded-xl p-3 pb-12 text-amber-100 placeholder:text-amber-100/40 border border-white/10 focus:border-amber-400/50 focus:outline-none resize-none text-sm leading-relaxed min-h-[80px]"
                                placeholder="Edit the prompt for regeneration..."
                              />
                              <div className="absolute bottom-3 right-3 flex items-center gap-2">
                                <button
                                  onClick={() => {
                                    setShowEditPrompt(false);
                                    setRegeneratePrompt("");
                                  }}
                                  className="h-9 w-9 rounded-full bg-white/10 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-red-500/20 hover:border-red-400/50 transition-all duration-200"
                                >
                                  <X className="w-4 h-4 text-amber-100" />
                                </button>
                                <button
                                  onClick={() => {
                                    handleRegenerateSubmit();
                                    setShowEditPrompt(false);
                                  }}
                                  disabled={!regeneratePrompt.trim()}
                                  className="h-9 w-9 rounded-full bg-white/10 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-white/20 hover:border-amber-400/50 hover:scale-105 transition-all duration-200 disabled:opacity-30 disabled:hover:scale-100"
                                >
                                  <Send className="w-4 h-4 text-amber-100" />
                                </button>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                  </div>
                </div>
              </div>
            </div>
            </div>
          )}
        </div>
      )}

      {/* Lightbox Style Single Image View - Responsive height */}
      {!showGridView && currentFrame && (
        <div className="absolute inset-x-0 top-[calc(50%+40px)] -translate-y-1/2 h-[45vh] md:h-[55vh] flex items-center justify-center px-12 md:px-0">
          {/* Image Container */}
          <div className="relative h-full flex flex-col items-center justify-center">
            <div className="relative group" style={{ height: 'calc(100% - 32px)', aspectRatio: getImageAspectRatio(currentFrame.versions[currentFrame.currentVersionIndex]?.image || currentFrame.image) }}>
              <img
                src={currentFrame.versions[currentFrame.currentVersionIndex]?.image || currentFrame.image}
                alt={currentFrame.content}
                className="w-full h-full object-cover rounded-2xl border-4 border-amber-800/50 shadow-2xl"
                onLoad={(event) => {
                  const { naturalWidth, naturalHeight } = event.currentTarget;
                  if (!naturalWidth || !naturalHeight) return;
                  const ratio = naturalWidth / naturalHeight;
                  const imageUrl = currentFrame.versions[currentFrame.currentVersionIndex]?.image || currentFrame.image;
                  setImageRatios((prev) => (prev[imageUrl] === ratio ? prev : { ...prev, [imageUrl]: ratio }));
                }}
              />
              
              {/* Frame Number Badge */}
              <Badge 
                className="absolute top-4 left-4 bg-amber-500/80 text-white text-sm backdrop-blur-sm border-0 px-3 py-1 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                Frame {currentIndex + 1}
              </Badge>
              
              {/* Version Badge */}
              <Badge 
                className="absolute top-4 right-4 bg-primary/80 text-primary-foreground text-sm backdrop-blur-sm border-0 px-3 py-1 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                v{currentFrame.currentVersionIndex + 1} / {currentFrame.versions.length}
              </Badge>
            </div>
            
            {/* Image Info */}
            <div className="mt-2">
              <span className="text-amber-300/50 text-sm">
                {currentIndex + 1} / {keyFrames.length}
              </span>
            </div>
            
            {/* Version Thumbnails Panel */}
            {currentFrame.versions.length > 1 && showVersionPanel && (
              <div className="absolute -right-4 top-1/2 -translate-y-1/2 translate-x-full flex flex-col items-center gap-2 bg-black/60 backdrop-blur-md rounded-xl p-3 border border-amber-800/30">
                <span className="text-amber-300/70 text-xs font-medium mb-1">Versions</span>
                {currentFrame.versions.map((version, vIdx) => (
                  <button
                    key={version.id}
                    onClick={() => {
                      setKeyFrames(prev => prev.map((f, idx) => 
                        idx === currentIndex 
                          ? { ...f, currentVersionIndex: vIdx, image: version.image }
                          : f
                      ));
                    }}
                    className={`relative w-16 h-10 rounded-lg overflow-hidden border-2 transition-all hover:scale-105 ${
                      currentFrame.currentVersionIndex === vIdx
                        ? 'border-amber-400 shadow-[0_0_10px_rgba(251,191,36,0.5)]'
                        : 'border-amber-800/50 hover:border-amber-600/70'
                    }`}
                  >
                    <img 
                      src={version.image} 
                      alt={`Version ${vIdx + 1}`}
                      className="w-full h-full object-cover"
                    />
                    <Badge 
                      className={`absolute bottom-0.5 left-0.5 text-[10px] px-1 py-0 ${
                        currentFrame.currentVersionIndex === vIdx
                          ? 'bg-amber-500 text-white'
                          : 'bg-black/60 text-amber-300'
                      }`}
                    >
                      v{vIdx + 1}
                    </Badge>
                  </button>
                ))}
              </div>
            )}
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
            onClick={() => setCurrentIndex(prev => Math.min(keyFrames.length - 1, prev + 1))}
            disabled={isLast}
            className="absolute right-2 md:right-6 top-1/2 -translate-y-1/2 w-10 h-10 md:w-14 md:h-14 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-amber-200 hover:text-white hover:bg-amber-500/30 hover:border-amber-400/50 disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-300 flex items-center justify-center z-10"
          >
            <ChevronRight className="w-5 h-5 md:w-8 md:h-8" />
          </button>
        </div>
      )}


      {/* Regenerate Input Section - positioned right below gallery, responsive */}
      {/* When Edit Prompt is open we anchor to the bottom so the expanded area stays visible */}
      {currentFrame && !showGridView && (
        <div 
          className={`absolute left-0 right-0 px-4 md:px-0 ${showEditPrompt ? 'bottom-0 pb-4 z-50' : 'z-40'}`}
          style={showEditPrompt ? undefined : { 
            top: 'calc(50% + 27.5vh + 16px)',
          }}
        >
          <div 
            className="mx-auto transition-all duration-500 ease-out px-8"
            style={{ width: galleryFrameWidth > 0 ? `${galleryFrameWidth}px` : '80%' }}
          >
            <div className="flex flex-col bg-black/40 backdrop-blur-md border border-white/20 shadow-lg rounded-2xl overflow-hidden">
              {/* All Actions Row */}
              <div className="flex items-center gap-3 px-5 py-4">
                {/* Quick Regenerate Button with Count */}
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => currentFrame && handleRegenerate(currentFrame.id)}
                    className="flex-shrink-0 h-11 px-5 rounded-full bg-white/10 backdrop-blur-xl border border-white/30 flex items-center gap-2 hover:bg-white/20 hover:border-amber-400/50 hover:scale-105 transition-all duration-200 shadow-[0_4px_16px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.1)]"
                  >
                    <RotateCw className="w-4 h-4 text-amber-100" />
                    <span className="text-amber-100 font-medium text-sm">Regenerate</span>
                  </button>
                  <div className="flex items-center h-11 rounded-full bg-white/10 backdrop-blur-xl border border-white/30 overflow-hidden">
                    <button
                      onClick={() => setRegenerateCount(prev => Math.max(1, prev - 1))}
                      className="h-full px-2 flex items-center justify-center hover:bg-white/10 transition-colors disabled:opacity-30"
                      disabled={regenerateCount <= 1}
                    >
                      <ChevronDown className="w-4 h-4 text-amber-100" />
                    </button>
                    <span className="w-6 text-center text-amber-100 text-sm font-medium">{regenerateCount}</span>
                    <button
                      onClick={() => setRegenerateCount(prev => Math.min(10, prev + 1))}
                      className="h-full px-2 flex items-center justify-center hover:bg-white/10 transition-colors disabled:opacity-30"
                      disabled={regenerateCount >= 10}
                    >
                      <ChevronUp className="w-4 h-4 text-amber-100" />
                    </button>
                  </div>
                </div>
                
                <div className="h-8 w-px bg-white/20" />
                
                {/* Instruction Input */}
                <div className="flex-1 flex items-center gap-2">
                  <Input
                    value={instructionText}
                    onChange={(e) => setInstructionText(e.target.value)}
                    placeholder="Add instruction, e.g., Make it more colorful..."
                    className="flex-1 h-11 bg-white/5 border border-white/10 text-amber-100 placeholder:text-amber-100/40 focus:border-amber-400/50 focus:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 rounded-xl text-sm"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        if (instructionText.trim()) {
                          handleRegenerate(currentFrame?.id || "", instructionText, { instructionOnly: true });
                          setInstructionText("");
                        }
                      }
                    }}
                  />
                  <button
                    onClick={() => {
                      if (instructionText.trim()) {
                        handleRegenerate(currentFrame?.id || "", instructionText, { instructionOnly: true });
                        setInstructionText("");
                      }
                    }}
                    disabled={!instructionText.trim()}
                    className="flex-shrink-0 h-11 w-11 rounded-xl bg-white/10 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-white/20 hover:border-amber-400/50 hover:scale-105 transition-all duration-200 disabled:opacity-30 disabled:hover:scale-100"
                  >
                    <Send className="w-4 h-4 text-amber-100" />
                  </button>
                </div>
                
                <div className="h-8 w-px bg-white/20" />
                
                {/* Edit Prompt Toggle Button */}
                <button
                  onClick={() => {
                    setShowEditPrompt(!showEditPrompt);
                    if (!showEditPrompt && !regeneratePrompt) {
                      setRegeneratePrompt(currentFrame?.prompt || "");
                    }
                  }}
                  className={`flex-shrink-0 h-11 px-5 rounded-full backdrop-blur-xl border flex items-center gap-2 transition-all duration-200 shadow-[0_4px_16px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.1)] ${
                    showEditPrompt 
                      ? 'bg-amber-500/20 border-amber-400/50 text-amber-200' 
                      : 'bg-white/10 border-white/30 text-amber-100 hover:bg-white/20 hover:border-amber-400/50 hover:scale-105'
                  }`}
                >
                  <Pencil className="w-4 h-4" />
                  <span className="text-sm font-medium">Edit Prompt</span>
                </button>
              </div>
              
              {/* Edit Prompt Expanded Area - Now below the actions row with smooth animation */}
              <div 
                className="grid transition-all duration-300 ease-out"
                style={{ 
                  gridTemplateRows: showEditPrompt ? '1fr' : '0fr',
                  opacity: showEditPrompt ? 1 : 0,
                }}
                onTouchMove={(e) => e.stopPropagation()}
              >
                <div className="overflow-hidden min-h-0">
                  <div
                    className="px-5 pt-4 pb-4 border-t border-white/10 max-h-[42vh] overflow-y-auto overscroll-contain touch-pan-y"
                    onWheelCapture={handleEditPromptScrollWheel}
                    onTouchMoveCapture={(e) => e.stopPropagation()}
                  >
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-amber-200/60 text-xs font-medium uppercase tracking-wider">Edit Full Prompt</span>
                      <button
                        onClick={() => setRegeneratePrompt(currentFrame?.prompt || "")}
                        className="text-amber-400/60 hover:text-amber-400 text-xs transition-colors"
                      >
                        Reset to Original
                      </button>
                    </div>
                    
                    {/* Reference Image and Prompt Input Row */}
                    <div className="flex gap-4">
                      {/* Reference Image */}
                      <div className="flex-shrink-0 w-32">
                        <div 
                          className="rounded-lg overflow-hidden border border-amber-500/30 bg-black/30 cursor-pointer hover:border-amber-400/60 hover:shadow-[0_0_20px_rgba(251,191,36,0.2)] transition-all duration-200 group relative"
                          style={{ aspectRatio: getImageAspectRatio(getCurrentRefImage() || undefined) }}
                          onClick={() => setFullscreenRefImage(getCurrentRefImage())}
                        >
                          <img 
                            src={getCurrentRefImage() || ''} 
                            alt="Reference" 
                            className="w-full h-full object-cover"
                            onLoad={(event) => {
                              const { naturalWidth, naturalHeight } = event.currentTarget;
                              if (!naturalWidth || !naturalHeight) return;
                              const ratio = naturalWidth / naturalHeight;
                              const imageUrl = getCurrentRefImage();
                              if (!imageUrl) return;
                              setImageRatios((prev) => (prev[imageUrl] === ratio ? prev : { ...prev, [imageUrl]: ratio }));
                            }}
                          />
                          {customRefImage && (
                            <div className="absolute top-1 left-1 px-1.5 py-0.5 bg-amber-500/80 rounded text-[8px] text-black font-semibold">
                              CUSTOM
                            </div>
                          )}
                          <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/30">
                            <div className="w-8 h-8 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center">
                              <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
                              </svg>
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center justify-center gap-1 mt-1">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              refImageInputRef.current?.click();
                            }}
                            className="text-[10px] text-amber-400/70 hover:text-amber-400 transition-colors"
                          >
                            Upload
                          </button>
                          {customRefImage && (
                            <>
                              <span className="text-amber-300/30">|</span>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  clearCustomRefImage();
                                }}
                                className="text-[10px] text-red-400/70 hover:text-red-400 transition-colors"
                              >
                                Clear
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                      
                      {/* Prompt Textarea */}
                      <div className="flex-1 relative">
                        <textarea
                          value={regeneratePrompt}
                          onChange={(e) => setRegeneratePrompt(e.target.value)}
                          className="w-full h-full bg-white/5 rounded-xl p-3 pb-12 text-amber-100 placeholder:text-amber-100/40 border border-white/10 focus:border-amber-400/50 focus:outline-none resize-none text-sm leading-relaxed min-h-[80px]"
                          placeholder="Edit the prompt for regeneration..."
                        />
                        {/* Buttons inside textarea container */}
                        <div className="absolute bottom-3 right-3 flex items-center gap-2">
                          <button
                            onClick={() => {
                              setShowEditPrompt(false);
                              setRegeneratePrompt("");
                            }}
                            className="h-9 w-9 rounded-full bg-white/10 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-red-500/20 hover:border-red-400/50 transition-all duration-200"
                          >
                            <X className="w-4 h-4 text-amber-100" />
                          </button>
                          <button
                            onClick={() => {
                              handleRegenerateSubmit();
                              setShowEditPrompt(false);
                            }}
                            disabled={!regeneratePrompt.trim()}
                            className="h-9 w-9 rounded-full bg-white/10 backdrop-blur-md border border-white/20 flex items-center justify-center hover:bg-white/20 hover:border-amber-400/50 hover:scale-105 transition-all duration-200 disabled:opacity-30 disabled:hover:scale-100"
                          >
                            <Send className="w-4 h-4 text-amber-100" />
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Thumbnail Navigation - at top below header */}
      {currentFrame && !showGridView && (
        <div className="absolute top-24 left-0 right-0 z-40">
          <div className="bg-black/40 backdrop-blur-md border-b border-white/10 pb-6 pt-4 px-8">
            <div className="w-[90%] mx-auto mb-6">
              <div className="relative">
                {/* Combined Section Headers + Thumbnails */}
                <div 
                  ref={thumbnailNavRef}
                  className="overflow-x-auto pb-2 [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-gray-600/30 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb:hover]:bg-gray-500/40"
                >
                  <div className="min-w-max px-4">
                    {/* Use inline-grid to ensure header and thumbnail columns align perfectly */}
                    <div 
                      className="inline-grid gap-x-2 gap-y-2"
                      style={{
                        gridTemplateColumns: keyFrames.map((_, index) => {
                          const isChapterStart = index % 20 === 0 && index !== 0;
                          // Add extra column for divider before chapter starts
                          return isChapterStart ? '10px 56px' : '56px';
                        }).join(' ')
                      }}
                    >
                      {/* Section Header Row - spans across corresponding thumbnail columns */}
                      {Array.from({ length: totalChapters }).map((_, i) => {
                        const sectionNum = i + 1;
                        const startIdx = i * 20;
                        const endIdx = Math.min(startIdx + 20, keyFrames.length);
                        const framesInSection = endIdx - startIdx;
                        const isCurrentSection = currentChapter === sectionNum;
                        
                        // Calculate grid column span
                        // Each frame takes 1 column, each divider takes 1 column
                        // Section 1: starts at col 1, spans framesInSection
                        // Section 2+: starts after divider column
                        const columnsBeforeThisSection = Array.from({ length: startIdx }).reduce<number>((acc, _, idx) => {
                          const isChapterStart = idx % 20 === 0 && idx !== 0;
                          return acc + (isChapterStart ? 2 : 1);
                        }, 0);
                        
                        // For sections after the first, we need to skip the divider column
                        const gridColumnStart = i === 0 ? 1 : columnsBeforeThisSection + 2;
                        const gridColumnEnd = gridColumnStart + framesInSection;
                        
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
                            {/* Text Row */}
                            <div className="flex items-center gap-2 px-1">
                              <span className={`text-[10px] font-mono font-bold flex-shrink-0 transition-colors ${isCurrentSection ? 'text-amber-400' : 'text-amber-400/50 group-hover:text-amber-400/70'}`}>
                                Sec.{sectionNum}
                              </span>
                              <span className={`text-[10px] truncate transition-colors ${isCurrentSection ? 'text-amber-100' : 'text-amber-200/40 group-hover:text-amber-200/60'}`}>
                                {sectionSummaries[sectionNum] || "Continues..."}
                              </span>
                            </div>
                            {/* Horizontal Line */}
                            <div className={`w-full h-px transition-all duration-200 ${
                              isCurrentSection 
                                ? 'bg-gradient-to-r from-amber-400/80 via-amber-400 to-amber-400/80 shadow-[0_0_4px_rgba(251,191,36,0.4)]' 
                                : 'bg-gradient-to-r from-transparent via-amber-600/30 to-transparent group-hover:via-amber-500/50'
                            }`} />
                          </button>
                        );
                      })}

                      {/* Thumbnail Row - each thumbnail in its own column */}
                      {keyFrames.map((frame, index) => {
                        const isActive = index === currentIndex;
                        const isChapterStart = index % 20 === 0 && index !== 0;
                        
                        // Calculate the grid column for this thumbnail
                        const columnsBeforeThis = Array.from({ length: index }).reduce<number>((acc, _, idx) => {
                          const isStart = idx % 20 === 0 && idx !== 0;
                          return acc + (isStart ? 2 : 1);
                        }, 0);
                        const gridColumn = isChapterStart ? columnsBeforeThis + 2 : columnsBeforeThis + 1;
                        
                        return (
                          <React.Fragment key={frame.id}>
                            {/* Section Divider Line */}
                            {isChapterStart && (
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
                              <img 
                                src={frame.image} 
                                alt={`Frame ${index + 1}`}
                                className="w-full h-full object-cover"
                              />
                              {/* Active Indicator */}
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
                <div className="absolute left-0 top-0 bottom-2 w-12 bg-gradient-to-r from-[#1a1510] to-transparent pointer-events-none" />
                <div className="absolute right-0 top-0 bottom-2 w-12 bg-gradient-to-l from-[#1a1510] to-transparent pointer-events-none" />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Collapsible Keyboard Shortcuts Bar - at bottom (hidden when Edit Prompt is open to avoid overlap) */}
      {currentFrame && !showGridView && !showEditPrompt && (
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
                    <kbd className="px-2 py-1 bg-amber-900/20 border border-amber-800/30 rounded text-amber-400/40 text-xs font-mono">1-9</kbd>
                    <span className="text-amber-200/30 text-xs">Section</span>
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

export default StoryboardCheckPage;
