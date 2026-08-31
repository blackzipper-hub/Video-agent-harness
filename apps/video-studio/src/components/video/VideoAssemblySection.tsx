import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { VideoWithCleanup } from "@/components/ui/VideoWithCleanup";
import { Video, Download, Package, Loader2, Upload } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { CUTI_VIDEO_API_BASE_URL, creationsApi, userApi } from "@/services/api";
import { hasUploadExplorePermission } from "@/utils/explorePublishPermission";
import { resolvePublicMediaUrl } from "@/utils/resolvePublicMediaUrl";
import aiAvatar from "@/assets/ai-avatar-capybara.png";
import { useState, useRef, useCallback, useMemo, useEffect } from "react";
import { FinalVideoTimelinePanel } from "./FinalVideoTimelinePanel";
import { ChainedShotsVideoPreview } from "./ChainedShotsVideoPreview";
import {
  buildShotNaturalDurationSecByKeyframeRows,
  buildShotDurationSecBySceneNarrations,
  getPreviewTimelineEndSec,
  getKeyframePosterPlacementsForPreview,
  getShotClipPlacementsForPreview,
  scalePlacementsToTargetTotal,
} from "@/lib/videoTimelinePlacements";
import type { GetShotClipPlacementsForPreviewOpts, ShotClipPlacement } from "@/lib/videoTimelinePlacements";
import { getPreviewBgmUrlFromMusicData } from "./MusicSection";

const MAX_PUBLISH_VIDEO_BYTES = 2 * 1024 * 1024 * 1024;
const MAX_PUBLISH_COVER_BYTES = 10 * 1024 * 1024;

const FinalVideoLoadingAnimation = ({ message }: { message: string }) => (
  <div className="relative flex min-h-[260px] w-full items-center justify-center overflow-hidden rounded-2xl border border-white/10 bg-[radial-gradient(circle_at_50%_20%,rgba(255,210,140,0.16),transparent_32%),linear-gradient(135deg,rgba(24,24,27,0.9),rgba(5,5,5,0.96))] px-6 py-10 text-center">
    <div className="pointer-events-none absolute left-1/2 top-1/2 h-44 w-44 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/10 blur-3xl animate-pulse" />
    <div className="pointer-events-none absolute inset-x-8 bottom-8 h-px bg-gradient-to-r from-transparent via-white/20 to-transparent" />
    <div className="relative z-10 flex flex-col items-center">
      <div className="relative mb-5">
        <div className="absolute inset-0 scale-125 rounded-full bg-primary/20 blur-xl animate-pulse" />
        <div className="relative flex h-24 w-24 items-center justify-center rounded-full border border-white/15 bg-white/10 shadow-[0_18px_60px_rgba(0,0,0,0.45)] backdrop-blur-md animate-bounce">
          <img src={aiAvatar} alt="Cuti" className="h-20 w-20 rounded-full object-cover" />
        </div>
        <div className="absolute -right-2 top-2 h-4 w-4 rounded-full bg-amber-300/80 shadow-[0_0_18px_rgba(252,211,77,0.9)] animate-ping" />
      </div>
      <p className="text-sm font-medium text-foreground">{message}</p>
      <div className="mt-4 flex items-center gap-2 text-primary">
        <span className="h-2 w-2 rounded-full bg-current animate-bounce [animation-delay:-0.2s]" />
        <span className="h-2 w-2 rounded-full bg-current animate-bounce [animation-delay:-0.1s]" />
        <span className="h-2 w-2 rounded-full bg-current animate-bounce" />
      </div>
    </div>
  </div>
);

const dataUrlToFile = async (dataUrl: string, filename: string): Promise<File> => {
  const response = await fetch(dataUrl);
  const blob = await response.blob();
  return new File([blob], filename, { type: blob.type || "image/jpeg" });
};

const getFilenameFromUrl = (url: string, fallback: string) => {
  try {
    const pathname = new URL(url).pathname;
    const filename = pathname.split("/").filter(Boolean).pop();
    return filename || fallback;
  } catch {
    return fallback;
  }
};

const urlToFile = async (url: string, fallbackName: string): Promise<File> => {
  const response = await fetch(url, { credentials: "omit" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${fallbackName}`);
  }
  const blob = await response.blob();
  return new File([blob], getFilenameFromUrl(url, fallbackName), { type: blob.type || "application/octet-stream" });
};

interface VideoAssemblySectionProps {
  /** 尚无组装记录时传 null，仍可展示与镜头一致的预估成片时间线 */
  videoAssemblyData: any | null;
  onReassemble?: () => Promise<void>;
  /** 与 Shots 区数据一致，用于在最终成片下展示可跳转的镜头时间线 */
  videosData?: any;
  /** 尚无镜头行时，用关键帧首帧串联预演；有镜头行时可为缺 poster 的格补图 */
  keyframesData?: any;
  /** 与分镜区 keyframe.uuid → version.uuid 选版一致 */
  selectedKeyframeVersions?: Map<string, string> | null;
  /** 与 LazyVideoResultsPanel / Shots 的选版 Map 一致 */
  selectedVersionsForShots?: Map<string, string> | null;
  /** 用户选择的目标成片时长（秒），用于预演时间线总长与未生成镜黑屏占位 */
  masterTargetDurationSec?: number;
  /** 串联预演画幅，与创建页 aspect_ratio 一致（如 16:9），减轻多镜切换时布局跳动 */
  previewAspectRatio?: string;
  /** 与 Music 区一致；有 URL 时在串联预演中与全局时间轴同步播放 BGM */
  musicData?: any;
  /** 与 scenes 区一致；旁白 TTS duration 用于 narration_driven 时间线分镜条 */
  scenesData?: any;
  /** 兜底 thread_id：当 videoAssemblyData?.thread_id 为空时用于发布 / 重组接口 */
  threadId?: string;
  /** Product Launch 等 talking head：优先播放无字幕成片 */
  contentCategory?: string;
}

export const VideoAssemblySection = ({
  videoAssemblyData,
  videosData,
  keyframesData,
  selectedKeyframeVersions,
  selectedVersionsForShots,
  masterTargetDurationSec,
  previewAspectRatio,
  musicData,
  scenesData,
  threadId,
  contentCategory,
}: VideoAssemblySectionProps) => {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [isExporting, setIsExporting] = useState(false);
  const [isCheckingPublishPermission, setIsCheckingPublishPermission] = useState(false);
  const [canPublishCreations, setCanPublishCreations] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishTitle, setPublishTitle] = useState("");
  const [publishDescription, setPublishDescription] = useState("");
  const [publishTags, setPublishTags] = useState("");
  const [publishCoverUrl, setPublishCoverUrl] = useState("");
  const [publishCoverTime, setPublishCoverTime] = useState(0);
  const [publishVideoDuration, setPublishVideoDuration] = useState(0);
  const [isPublishing, setIsPublishing] = useState(false);
  const [timelineTime, setTimelineTime] = useState(0);
  /** 与浏览器解码后的 <video> 总时长一致（成片刻度更准，避免与接口 total 轻微偏差） */
  const [measuredFileDuration, setMeasuredFileDuration] = useState<number | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const coverVideoRef = useRef<HTMLVideoElement | null>(null);
  const chainSeekRef = useRef<((globalT: number) => void) | null>(null);
  const hasAssemblyRecord = videoAssemblyData != null;
  const videoUrlWithSubtitle = hasAssemblyRecord ? videoAssemblyData.final_video_url : undefined;
  const videoUrlNoSubtitle = hasAssemblyRecord ? videoAssemblyData.final_video_url_no_subtitle : undefined;
  const hasNoSubtitleVersion = !!videoUrlNoSubtitle;
  const isProductLaunch = contentCategory === "Product Launch";
  const preferSubtitleFirst =
    !isProductLaunch &&
    videoAssemblyData?.assembly_mode === "narration_driven" &&
    (!videoUrlNoSubtitle && !!videoUrlWithSubtitle);
  const finalVideoUrl = isProductLaunch
    ? (videoUrlNoSubtitle || videoUrlWithSubtitle)
    : preferSubtitleFirst
      ? (videoUrlWithSubtitle || videoUrlNoSubtitle)
      : (videoUrlNoSubtitle || videoUrlWithSubtitle);
  const showSingleFinalPlayer = !!finalVideoUrl;

  const rawDuration = hasAssemblyRecord ? videoAssemblyData.total_duration : undefined;
  const assemblyDuration = typeof rawDuration === "number" ? rawDuration : (hasAssemblyRecord && rawDuration != null ? (parseFloat(String(rawDuration)) || 0) : 0);
  const success = hasAssemblyRecord && videoAssemblyData.success !== false;

  const selectedMap = useMemo(
    () => selectedVersionsForShots ?? new Map<string, string>(),
    [selectedVersionsForShots]
  );
  const selectedKeyframeMap = useMemo(
    () => selectedKeyframeVersions ?? new Map<string, string>(),
    [selectedKeyframeVersions]
  );
  const previewBgmUrl = useMemo(() => getPreviewBgmUrlFromMusicData(musicData), [musicData]);
  const isNarrationDriven = videoAssemblyData?.assembly_mode === "narration_driven";
  const shotDurationHints = useMemo(
    () => buildShotNaturalDurationSecByKeyframeRows(keyframesData, selectedKeyframeMap),
    [keyframesData, selectedKeyframeMap]
  );
  const narrationDurationHints = useMemo(
    () => buildShotDurationSecBySceneNarrations(scenesData),
    [scenesData]
  );
  const placementOpts = useMemo((): GetShotClipPlacementsForPreviewOpts | undefined => {
    const o: GetShotClipPlacementsForPreviewOpts = {};
    if (
      masterTargetDurationSec != null &&
      Number.isFinite(masterTargetDurationSec) &&
      masterTargetDurationSec > 0.05
    ) {
      o.masterTargetDurationSec = masterTargetDurationSec;
    }
    const merged = new Map<number, number>(shotDurationHints);
    narrationDurationHints.forEach((v, k) => merged.set(k, v));
    if (merged.size > 0) {
      o.shotNaturalDurationSecByNumber = merged;
    }
    if (isNarrationDriven && narrationDurationHints.size > 0) {
      o.preferNarrationDrivenDurations = true;
    }
    return Object.keys(o).length > 0 ? o : undefined;
  }, [masterTargetDurationSec, shotDurationHints, narrationDurationHints, isNarrationDriven]);
  const rawPlacements = useMemo((): ShotClipPlacement[] => {
    const fromVideo = videosData
      ? getShotClipPlacementsForPreview(videosData, selectedMap, placementOpts)
      : [];
    if (fromVideo.length > 0) {
      const kfList = keyframesData?.keyframes;
      if (!Array.isArray(kfList) || kfList.length === 0) {
        return fromVideo;
      }
      const posterByShot = new Map<number, string>();
      for (const k of kfList) {
        const sn = Number((k as { shot_number?: number }).shot_number);
        if (!Number.isFinite(sn) || sn < 1 || posterByShot.has(sn)) {
          continue;
        }
        const vers = (k as { versions?: Array<{ uuid?: string; keyframe_url?: string }> }).versions || [];
        const kid = (k as { uuid?: string }).uuid != null ? String((k as { uuid?: string }).uuid) : "";
        const sel = kid ? selectedKeyframeMap.get(kid) : undefined;
        const version = sel
          ? vers.find((x) => String(x?.uuid) === String(sel))
          : vers[(k as { current_version_index?: number }).current_version_index || 0] || vers[0];
        const url = version?.keyframe_url;
        if (typeof url === "string" && url) {
          posterByShot.set(sn, url);
        }
      }
      let changed = false;
      const merged = fromVideo.map((p) => {
        if (p.shotNumber <= 0 || p.posterUrl) {
          return p;
        }
        const fill = posterByShot.get(p.shotNumber);
        if (!fill) {
          return p;
        }
        changed = true;
        return { ...p, posterUrl: fill };
      });
      return changed ? merged : fromVideo;
    }
    return keyframesData
      ? getKeyframePosterPlacementsForPreview(keyframesData, selectedKeyframeMap, placementOpts)
      : [];
  }, [videosData, selectedMap, placementOpts, keyframesData, selectedKeyframeMap]);
  const previewEndSec = getPreviewTimelineEndSec(rawPlacements);
  const lineTotalSec = useMemo(() => {
    if (showSingleFinalPlayer) {
      if (measuredFileDuration != null && measuredFileDuration > 0 && Number.isFinite(measuredFileDuration)) {
        return measuredFileDuration;
      }
      if (assemblyDuration > 0) {
        return assemblyDuration;
      }
      return previewEndSec;
    }
    const masterFloor =
      masterTargetDurationSec != null &&
      Number.isFinite(masterTargetDurationSec) &&
      masterTargetDurationSec > 0.05
        ? masterTargetDurationSec
        : 0;
    const base = assemblyDuration > 0 ? Math.max(assemblyDuration, previewEndSec) : previewEndSec;
    return masterFloor > 0 ? Math.max(base, masterFloor) : base;
  }, [showSingleFinalPlayer, measuredFileDuration, assemblyDuration, previewEndSec, masterTargetDurationSec]);

  const placements = useMemo(() => {
    if (!rawPlacements.length) {
      return rawPlacements;
    }
    if (showSingleFinalPlayer) {
      const target =
        measuredFileDuration != null && measuredFileDuration > 0.05
          ? measuredFileDuration
          : assemblyDuration > 0.05
            ? assemblyDuration
            : previewEndSec;
      if (target > 0.05) {
        return scalePlacementsToTargetTotal(rawPlacements, target);
      }
    }
    return rawPlacements;
  }, [rawPlacements, showSingleFinalPlayer, measuredFileDuration, assemblyDuration, previewEndSec]);

  const chainable = !showSingleFinalPlayer && placements.some((p) => p.durationSec > 0.05);

  useEffect(() => {
    setMeasuredFileDuration(null);
  }, [finalVideoUrl]);

  const checkPublishPermission = useCallback(async () => {
    setIsCheckingPublishPermission(true);
    try {
      const response = await userApi.getCurrentUser();
      const allowed = response.code === 0 && hasUploadExplorePermission(response.data);
      setCanPublishCreations(allowed);
      return allowed;
    } catch {
      setCanPublishCreations(false);
      return false;
    } finally {
      setIsCheckingPublishPermission(false);
    }
  }, []);

  useEffect(() => {
    if (!finalVideoUrl) return;
    checkPublishPermission();
  }, [checkPublishPermission, finalVideoUrl]);

  const registerChainSeek = useCallback((fn: (n: number) => void) => {
    chainSeekRef.current = fn;
  }, []);

  const handleSeek = useCallback(
    (timeSec: number) => {
      if (showSingleFinalPlayer) {
        const el = videoRef.current;
        if (el) {
          el.currentTime = timeSec;
        }
        setTimelineTime(timeSec);
        return;
      }
      if (chainSeekRef.current) {
        chainSeekRef.current(timeSec);
        return;
      }
      setTimelineTime(timeSec);
    },
    [showSingleFinalPlayer]
  );

  // 导出所有素材
  const handleExportAllAssets = async () => {
    if (!hasAssemblyRecord) {
      return;
    }
    try {
      setIsExporting(true);
      const currentLang = localStorage.getItem("language") || "en";
      const response = await fetch(
        `${CUTI_VIDEO_API_BASE_URL}/video-analysis/video-assembly/${videoAssemblyData.uuid}/download?resource_type=complete`,
        {
          method: "GET",
          credentials: "include",
          headers: {
            "X-App-Language": currentLang,
          },
        }
      );
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`导出失败: ${response.status} ${errorText.slice(0, 80)}`);
      }
      const blob = await response.blob();
      if (blob.size === 0) {
        throw new Error("下载的文件为空");
      }
      const contentDisposition = response.headers.get("Content-Disposition");
      let filename = `video-assets-${Date.now()}.zip`;
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (filenameMatch && filenameMatch[1]) {
          filename = filenameMatch[1].replace(/['"]/g, "");
        }
      }
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      toast({
        title: t("exportSuccess") || "导出成功",
        description: t("exportSuccessDesc") || "所有素材已打包下载",
      });
    } catch (error) {
      toast({
        title: t("exportFailed") || "导出失败",
        description: error instanceof Error ? error.message : t("exportFailedDesc") || "请重试",
        variant: "destructive",
      });
    } finally {
      setIsExporting(false);
    }
  };

  const handleDownloadVideo = (withSubtitle: boolean) => {
    if (!hasAssemblyRecord) {
      return;
    }
    const url = withSubtitle ? videoUrlWithSubtitle : videoUrlNoSubtitle;
    if (url) {
      window.open(url, "_blank");
    }
  };

  const handleOpenPublish = async () => {
    if (!finalVideoUrl) return;
    const allowed = await checkPublishPermission();
    if (!allowed) {
      toast({
        title: t("publishNoPermission") || "No publish permission",
        variant: "destructive",
      });
      return;
    }
    setPublishTitle(videoAssemblyData?.title || t("videoResult") || "Video result");
    setPublishDescription("");
    setPublishTags("");
    setPublishCoverUrl("");
    setPublishCoverTime(0);
    setPublishVideoDuration(0);
    setPublishOpen(true);
  };

  const handleCaptureCover = () => {
    const video = coverVideoRef.current;
    if (!video) return;
    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || 1280;
      canvas.height = video.videoHeight || 720;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      setPublishCoverUrl(canvas.toDataURL("image/jpeg", 0.92));
      setPublishCoverTime(video.currentTime || 0);
    } catch (error) {
      toast({
        title: t("publishCover") || "Cover",
        description: String(error || "Failed to capture frame"),
        variant: "destructive",
      });
    }
  };

  const getPublishCoverFile = async () => {
    if (!publishCoverUrl) {
      throw new Error(t("publishCover") || "Cover");
    }
    const file = publishCoverUrl.startsWith("data:")
      ? await dataUrlToFile(publishCoverUrl, "cover.jpg")
      : await urlToFile(publishCoverUrl, "cover.jpg");
    if (file.size > MAX_PUBLISH_COVER_BYTES) {
      throw new Error(t("publishCoverTooLarge") || "Cover must be 10 MB or smaller");
    }
    return file;
  };

  const handlePublish = async () => {
    if (!finalVideoUrl) return;
    if (!publishTitle.trim()) {
      toast({ title: t("publishTitle") || "Title", variant: "destructive" });
      return;
    }
    if (!publishCoverUrl) {
      toast({ title: t("publishCover") || "Cover", variant: "destructive" });
      return;
    }
    setIsPublishing(true);
    try {
      const allowed = await checkPublishPermission();
      if (!allowed) {
        throw new Error(t("publishNoPermission") || "No publish permission");
      }

      toast({ title: t("publishUploadingVideo") || "Uploading video..." });
      const videoFile = await urlToFile(finalVideoUrl, "creation.mp4");
      if (videoFile.size > MAX_PUBLISH_VIDEO_BYTES) {
        throw new Error(t("publishVideoTooLarge") || "Video must be 500 MB or smaller");
      }
      const videoResponse = await creationsApi.uploadVideo(videoFile);
      const videoUrl = videoResponse.data?.video_url;
      if (!videoUrl) {
        throw new Error(videoResponse.message || t("publishFailed") || "Failed to publish creation");
      }

      toast({ title: t("publishUploadingCover") || "Uploading cover..." });
      const coverFile = await getPublishCoverFile();
      const coverResponse = await creationsApi.uploadCover(coverFile);
      const coverUrl = coverResponse.data?.cover_url;
      if (!coverUrl) {
        throw new Error(coverResponse.message || t("publishFailed") || "Failed to publish creation");
      }

      const tags = publishTags
        .split(/[,，]/)
        .map((tag) => tag.trim())
        .filter(Boolean);
      const publishResponse = await creationsApi.publish({
        title: publishTitle.trim(),
        description: publishDescription.trim(),
        video_url: resolvePublicMediaUrl(videoUrl),
        cover_url: resolvePublicMediaUrl(coverUrl),
        tags,
        source: "studio",
        thread_id: videoAssemblyData?.thread_id || threadId || "",
      });
      if (publishResponse.code !== 0) {
        throw new Error(publishResponse.message || t("publishFailed") || "Failed to publish creation");
      }
      setPublishOpen(false);
      toast({ title: t("publishSuccess") || "Creation published" });
    } catch (error) {
      toast({
        title: error instanceof Error ? error.message : t("publishFailed") || "Failed to publish creation",
        variant: "destructive",
      });
    } finally {
      setIsPublishing(false);
    }
  };

  const handleExportVersions = async (version: "with_subtitle" | "no_subtitle" | "both") => {
    if (!hasAssemblyRecord) {
      return;
    }
    try {
      setIsExporting(true);
      const currentLang = localStorage.getItem("language") || "en";
      const response = await fetch(
        `${CUTI_VIDEO_API_BASE_URL}/video-analysis/video-assembly/${videoAssemblyData.uuid}/export?version=${version}`,
        {
          method: "GET",
          credentials: "include",
          headers: {
            "X-App-Language": currentLang,
          },
        }
      );
      if (!response.ok) {
        throw new Error(`导出失败: ${response.status}`);
      }
      const blob = await response.blob();
      if (blob.size === 0) {
        throw new Error("下载的文件为空");
      }
      const contentDisposition = response.headers.get("Content-Disposition");
      let filename = `video-export-${version}-${Date.now()}.zip`;
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (filenameMatch && filenameMatch[1]) {
          filename = filenameMatch[1].replace(/['"]/g, "");
        }
      }
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      toast({ title: t("exportSuccess") || "导出成功", description: filename });
    } catch (error) {
      toast({
        title: t("exportFailed") || "导出失败",
        description: error instanceof Error ? error.message : "请重试",
        variant: "destructive",
      });
    } finally {
      setIsExporting(false);
    }
  };

  const showAssembling = hasAssemblyRecord && !showSingleFinalPlayer && !chainable;
  const showPending = !hasAssemblyRecord && !chainable;

  const mainPlayer = (() => {
    if (showSingleFinalPlayer) {
      return (
        <div className="flex max-h-[calc(100dvh-18rem)] w-full min-w-0 items-center justify-center overflow-hidden rounded-lg">
          <VideoWithCleanup
            ref={videoRef}
            key={finalVideoUrl}
            className="block h-auto max-h-[calc(100dvh-18rem)] max-w-full object-contain bg-transparent"
            controls
            src={finalVideoUrl}
            onTimeUpdate={(e) => setTimelineTime(e.currentTarget.currentTime)}
            onLoadedMetadata={(e) => {
              const d = e.currentTarget.duration;
              if (Number.isFinite(d) && d > 0) {
                setMeasuredFileDuration(d);
              }
            }}
            onDurationChange={(e) => {
              const d = e.currentTarget.duration;
              if (Number.isFinite(d) && d > 0) {
                setMeasuredFileDuration(d);
              }
            }}
          >
            {t("videoNotSupported")}
          </VideoWithCleanup>
        </div>
      );
    }
    if (showAssembling || showPending) {
      return <FinalVideoLoadingAnimation message={String(t("videoBeingAssembled"))} />;
    }
    if (chainable) {
      return (
        <ChainedShotsVideoPreview
          placements={placements}
          totalSec={lineTotalSec}
          currentGlobalTime={timelineTime}
          videoRef={videoRef}
          onRegisterSeek={registerChainSeek}
          onPlaybackTime={setTimelineTime}
          frameAspectRatio={previewAspectRatio}
          bgmAudioUrl={previewBgmUrl}
        />
      );
    }
    return (
      <div className="w-full min-h-[min(200px,40vh)] max-h-[min(80vh,800px)] flex items-center justify-center rounded-lg border border-border/20 bg-background/30"
      >
        <div className="text-center text-muted-foreground">
          <Video className="w-16 h-16 mx-auto mb-2 opacity-50" />
          <p className="text-sm">{String(t("videoBeingAssembled"))}</p>
        </div>
      </div>
    );
  })();

  return (
    <Card className="p-6 bg-transparent border-none shadow-none">
      <div className="mb-4">
        <h3 className="text-lg font-semibold flex items-center">
          <Video className="w-5 h-5 mr-2 text-primary" />
          {t("finalVideoSection")}
          {(showAssembling || showPending) && (
            <Loader2 className="w-4 h-4 ml-2 animate-spin text-muted-foreground" aria-hidden />
          )}
        </h3>
      </div>

      <div className="space-y-4">
        {mainPlayer}
        <FinalVideoTimelinePanel
          totalDurationFromAssembly={lineTotalSec}
          currentTime={timelineTime}
          onSeek={handleSeek}
          videosData={videosData}
          selectedVersionsByVideoId={selectedVersionsForShots}
          masterTargetDurationSec={placementOpts?.masterTargetDurationSec}
          previewPlacements={placements.length > 0 ? placements : null}
        />

        {hasAssemblyRecord && showSingleFinalPlayer && !success && (
          <div className="text-sm text-red-500">
            {String(t("assemblyFailed"))}
          </div>
        )}

        {hasAssemblyRecord && showSingleFinalPlayer && (
          <div className="flex gap-2">
            {canPublishCreations ? (
              <Button variant="outline" className="flex-1" onClick={handleOpenPublish} disabled={isPublishing || isCheckingPublishPermission}>
                {isPublishing || isCheckingPublishPermission ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Upload className="w-4 h-4 mr-2" />
                )}
                {t("publishCreations") || "Publish Creations"}
              </Button>
            ) : null}
            <Button variant="outline" className="flex-1" onClick={handleExportAllAssets} disabled={isExporting}>
              <Package className="w-4 h-4 mr-2" />
              {isExporting ? t("exporting") || "导出中..." : t("exportAllAssets") || "导出所有素材"}
            </Button>
            <Button
              variant="apple"
              className="flex-1"
              disabled={isExporting}
              onClick={() => handleDownloadVideo(hasNoSubtitleVersion ? false : true)}
            >
              <Download className="w-4 h-4 mr-2" />
              {isExporting ? t("processing") || "处理中..." : t("downloadVideo") || "下载视频"}
            </Button>
          </div>
        )}
      </div>

      <Dialog open={publishOpen} onOpenChange={setPublishOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t("publishCreations") || "Publish Creations"}</DialogTitle>
            <DialogDescription>{t("publishCreationsDescription") || "Add a title, description, and cover."}</DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 md:grid-cols-[1fr_1fr]">
            <div className="space-y-3">
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">{t("publishTitle") || "Title"}</label>
                <Input value={publishTitle} onChange={(e) => setPublishTitle(e.target.value)} />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">{t("publishDescription") || "Description"}</label>
                <Textarea value={publishDescription} onChange={(e) => setPublishDescription(e.target.value)} />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">{t("publishTags") || "Tags"}</label>
                <Input
                  value={publishTags}
                  onChange={(e) => setPublishTags(e.target.value)}
                  placeholder={t("publishTagsPlaceholder") || "Travel, vlog"}
                />
              </div>
            </div>

            <div className="space-y-3">
              <div>
                <p className="mb-2 text-sm font-medium text-foreground">{t("publishCover") || "Cover"}</p>
                <p className="mb-3 text-xs text-muted-foreground">{t("publishCoverHint") || "Pick a frame from the video and use it as the cover."}</p>
                <video
                  ref={coverVideoRef}
                  src={finalVideoUrl}
                  className="aspect-video w-full rounded-lg bg-black"
                  controls
                  crossOrigin="anonymous"
                  onLoadedMetadata={(e) => {
                    const duration = e.currentTarget.duration;
                    setPublishVideoDuration(Number.isFinite(duration) ? duration : 0);
                    if (publishCoverTime > 0) {
                      e.currentTarget.currentTime = Math.min(duration, publishCoverTime);
                    }
                  }}
                />
                <div className="mt-3 space-y-2">
                  <input
                    type="range"
                    min={0}
                    max={publishVideoDuration || 0}
                    step={0.1}
                    value={Math.min(publishCoverTime, publishVideoDuration || 0)}
                    onChange={(e) => {
                      const nextTime = Number(e.target.value);
                      setPublishCoverTime(nextTime);
                      if (coverVideoRef.current) {
                        coverVideoRef.current.currentTime = nextTime;
                      }
                    }}
                    className="w-full"
                  />
                  <Button type="button" variant="outline" onClick={handleCaptureCover}>
                    {t("publishUseCurrentFrame") || "Use current frame"}
                  </Button>
                </div>
              </div>
              {publishCoverUrl ? (
                <img src={publishCoverUrl} alt="Publish cover preview" className="aspect-video max-h-32 w-full rounded-lg object-cover" />
              ) : null}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setPublishOpen(false)}>
              {t("cancel") || "Cancel"}
            </Button>
            <Button onClick={handlePublish} disabled={isPublishing}>
              {isPublishing ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {t("publishCreations") || "Publish Creations"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
};
