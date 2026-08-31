import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Play, Pause } from "lucide-react";
import { VideoWithCleanup } from "@/components/ui/VideoWithCleanup";
import { Button } from "@/components/ui/button";
import type { ShotClipPlacement } from "@/lib/videoTimelinePlacements";
import { getPreviewTimelineEndSec } from "@/lib/videoTimelinePlacements";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/i18n/LanguageContext";

type ChainedShotsVideoPreviewProps = {
  placements: ShotClipPlacement[];
  totalSec: number;
  /** 与下方时间线、父 state 一致的全局秒数，用于自绘控制台（多段 <video> 时原生条只能显示单文件） */
  currentGlobalTime: number;
  className?: string;
  onPlaybackTime: (t: number) => void;
  videoRef: React.MutableRefObject<HTMLVideoElement | null>;
  /** 注册全局 seek，供与成片共用同一时间线 onSeek */
  onRegisterSeek: (seekGlobal: (globalT: number) => void) => void;
  /** 与创建页一致，如 16:9 / 9:16；固定画幅避免多镜切换时容器高度跳动 */
  frameAspectRatio?: string;
  /** API 顶层 music_url 整曲；有 BGM 时由 audio.currentTime 驱动时间轴，播放中不 seek 音乐。 */
  bgmAudioUrl?: string | null;
};

function formatClockShort(sec: number) {
  if (!Number.isFinite(sec) || sec < 0) {
    return "0:00";
  }
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m.toString()}:${s.toString().padStart(2, "0")}`;
}

function isVideoSegment(p: ShotClipPlacement | undefined) {
  return !!(p && p.videoUrl && p.durationSec > 0.05);
}

function hasTimelineSegment(p: ShotClipPlacement | undefined) {
  return !!(p && p.durationSec > 0.05);
}

function frameAspectClass(aspect?: string): string {
  const s = typeof aspect === "string" ? aspect.trim().toLowerCase() : "";
  if (s === "9:16" || s === "9/16") {
    return "aspect-[9/16]";
  }
  if (s === "1:1") {
    return "aspect-square";
  }
  return "aspect-video";
}

/**
 * 无组片 final URL 时：按镜顺序连接播放各镜 video_url；全局时间轴与 FinalVideoTimelinePanel 一致.
 */
export function ChainedShotsVideoPreview({
  placements,
  totalSec,
  currentGlobalTime,
  className,
  onPlaybackTime,
  videoRef,
  onRegisterSeek,
  frameAspectRatio,
  bgmAudioUrl,
}: ChainedShotsVideoPreviewProps) {
  const { t } = useLanguage();
  const [activeIndex, setActiveIndex] = useState(0);
  /** 时间轴是否在播（BGM / 占位镜）；勿与单镜 <video> 的 paused 绑定，避免切镜时误 pause BGM */
  const [timelinePlaying, setTimelinePlaying] = useState(false);
  const bgmRef = useRef<HTMLAudioElement | null>(null);
  const activeIndexRef = useRef(0);
  const lastPlacementsKey = useRef<string>("");
  const pendingLocalSeek = useRef<number | null>(null);
  /** 上一镜自然结束切到下一镜时置位，onCanPlay 里 play()，避免新 <video> 默认暂停 */
  const autoPlayAfterAdvanceRef = useRef(false);
  /** 切镜 / seek 换 src 时旧 video 会触发 onPause，忽略以免 BGM 跟着停 */
  const suppressVideoPauseForBgmRef = useRef(false);
  const bgmMasterRafRef = useRef<number | null>(null);
  const lastVideoSyncIndexRef = useRef(-1);
  /** onLoadedMetadata 后 ref 才与 activeIndexRef 一致，避免切镜 rAF 误操作旧 <video> */
  const mountedVideoIndexRef = useRef(-1);
  /** 有整曲 BGM 时由 <audio> 驱动全局时间，避免切镜时 video timeupdate 中断导致 BGM seek */
  const bgmDrivesClock = !!(bgmAudioUrl && bgmAudioUrl.trim());
  /** rAF 节流 onTimeUpdate 上抛，避免 60Hz setState 把整棵时间线重渲 */
  const rafPendingRef = useRef<number | null>(null);
  const pendingGlobalTRef = useRef<number | null>(null);
  const lastReportedAtRef = useRef(0);
  const globalTimeRef = useRef(currentGlobalTime);
  useLayoutEffect(() => {
    activeIndexRef.current = activeIndex;
  }, [activeIndex]);
  useLayoutEffect(() => {
    if (bgmDrivesClock && timelinePlaying) {
      return;
    }
    globalTimeRef.current = currentGlobalTime;
  }, [currentGlobalTime, bgmDrivesClock, timelinePlaying]);
  useEffect(() => {
    return () => {
      if (rafPendingRef.current != null) {
        cancelAnimationFrame(rafPendingRef.current);
        rafPendingRef.current = null;
      }
      if (bgmMasterRafRef.current != null) {
        cancelAnimationFrame(bgmMasterRafRef.current);
        bgmMasterRafRef.current = null;
      }
    };
  }, []);

  const endSec = useMemo(() => {
    const t = getPreviewTimelineEndSec(placements);
    return totalSec > 0 ? Math.max(t, totalSec) : t;
  }, [placements, totalSec]);

  const findPlacementIndexForTime = useCallback(
    (globalT: number) => {
      for (let i = 0; i < placements.length; i++) {
        const p = placements[i]!;
        if (globalT < p.endSec - 1e-4) {
          return i;
        }
      }
      return Math.max(0, placements.length - 1);
    },
    [placements]
  );

  const seekBgmToGlobalTime = useCallback(() => {
    const a = bgmRef.current;
    if (!a || !bgmDrivesClock) {
      return;
    }
    const d = a.duration;
    const gt = globalTimeRef.current;
    if (!Number.isFinite(d) || d <= 0 || !Number.isFinite(gt) || gt < 0) {
      return;
    }
    a.currentTime = Math.min(Math.max(0, gt), Math.max(0, d - 0.05));
  }, [bgmDrivesClock]);

  const ensureBgmPlaying = useCallback(() => {
    const a = bgmRef.current;
    if (!a || !bgmAudioUrl) {
      return;
    }
    if (a.paused) {
      const p = a.play();
      if (p && typeof p.catch === "function") {
        p.catch(() => {});
      }
    }
  }, [bgmAudioUrl]);

  const switchActivePlacementForTime = useCallback(
    (globalT: number, targetIndex: number) => {
      if (targetIndex < 0 || targetIndex >= placements.length) {
        return;
      }
      if (targetIndex === activeIndexRef.current) {
        return;
      }
      mountedVideoIndexRef.current = -1;
      activeIndexRef.current = targetIndex;
      const p = placements[targetIndex]!;
      suppressVideoPauseForBgmRef.current = true;
      if (isVideoSegment(p)) {
        pendingLocalSeek.current = Math.max(
          0,
          Math.min(globalT - p.startSec, p.durationSec - 1e-3)
        );
        autoPlayAfterAdvanceRef.current = true;
      } else {
        pendingLocalSeek.current = null;
        autoPlayAfterAdvanceRef.current = false;
      }
      setActiveIndex(targetIndex);
    },
    [placements]
  );

  const syncVideoToGlobalTime = useCallback(
    (globalT: number) => {
      const idx = activeIndexRef.current;
      if (mountedVideoIndexRef.current !== idx) {
        return;
      }
      const p = placements[idx];
      const el = videoRef.current;
      if (!el || !p || !isVideoSegment(p)) {
        return;
      }
      const fileDur = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : p.durationSec;
      const want = Math.max(
        0,
        Math.min(globalT - p.startSec, p.durationSec - 1e-3, fileDur - 1e-3)
      );
      const indexJustChanged = idx !== lastVideoSyncIndexRef.current;
      if (indexJustChanged) {
        lastVideoSyncIndexRef.current = idx;
        el.currentTime = want;
      } else if (Math.abs(el.currentTime - want) > 0.35) {
        el.currentTime = want;
      }
      if (el.ended && globalT < p.endSec - 1e-3) {
        el.currentTime = Math.max(0, Math.min(want, fileDur - 0.04));
        return;
      }
      if (timelinePlaying && el.paused && globalT < p.endSec - 1e-3) {
        el.play().catch(() => {});
      }
    },
    [placements, videoRef, timelinePlaying]
  );

  useEffect(() => {
    const a = bgmRef.current;
    if (!a || !bgmAudioUrl) {
      return;
    }
    if (!timelinePlaying) {
      a.pause();
      return;
    }
    ensureBgmPlaying();
  }, [timelinePlaying, bgmAudioUrl, ensureBgmPlaying]);

  useEffect(() => {
    if (!bgmDrivesClock || !timelinePlaying) {
      if (bgmMasterRafRef.current != null) {
        cancelAnimationFrame(bgmMasterRafRef.current);
        bgmMasterRafRef.current = null;
      }
      return;
    }
    const tick = () => {
      const a = bgmRef.current;
      if (!a || !timelinePlaying) {
        bgmMasterRafRef.current = null;
        return;
      }
      if (a.paused) {
        ensureBgmPlaying();
      }
      let globalT = a.currentTime;
      const audioDur = a.duration;
      if (Number.isFinite(audioDur) && audioDur > 0) {
        globalT = Math.min(globalT, Math.max(0, audioDur - 0.001));
      }
      const cap = endSec > 0 ? endSec : globalT;
      globalT = Math.min(globalT, cap);
      globalTimeRef.current = globalT;

      const now = typeof performance !== "undefined" ? performance.now() : Date.now();
      if (now - lastReportedAtRef.current >= 80) {
        lastReportedAtRef.current = now;
        onPlaybackTime(globalT);
      }

      const idx = findPlacementIndexForTime(globalT);
      switchActivePlacementForTime(globalT, idx);
      syncVideoToGlobalTime(globalT);

      if (globalT >= cap - 0.02) {
        setTimelinePlaying(false);
        a.pause();
        onPlaybackTime(cap);
        bgmMasterRafRef.current = null;
        return;
      }
      bgmMasterRafRef.current = requestAnimationFrame(tick);
    };
    bgmMasterRafRef.current = requestAnimationFrame(tick);
    return () => {
      if (bgmMasterRafRef.current != null) {
        cancelAnimationFrame(bgmMasterRafRef.current);
        bgmMasterRafRef.current = null;
      }
    };
  }, [
    bgmDrivesClock,
    timelinePlaying,
    endSec,
    ensureBgmPlaying,
    findPlacementIndexForTime,
    switchActivePlacementForTime,
    syncVideoToGlobalTime,
    onPlaybackTime,
  ]);

  useEffect(() => {
    return () => {
      const a = bgmRef.current;
      if (a) {
        a.pause();
      }
    };
  }, []);

  const firstSegmentIndex = useMemo(() => {
    return placements.findIndex((p) => hasTimelineSegment(p));
  }, [placements]);

  const placementsKey = useMemo(
    () =>
      placements
        .map(
          (p) =>
            `${p.shotNumber}-${p.startSec.toFixed(2)}-${p.durationSec.toFixed(2)}-${(p.videoUrl || "").slice(-20)}-${(p.posterUrl || "").slice(-12)}`
        )
        .join("|"),
    [placements]
  );
  useEffect(() => {
    if (placementsKey === lastPlacementsKey.current) {
      return;
    }
    lastPlacementsKey.current = placementsKey;
    if (firstSegmentIndex < 0) {
      return;
    }
    const cur = activeIndexRef.current;
    const curPlacement = placements[cur];
    if (cur < 0 || cur >= placements.length || !hasTimelineSegment(curPlacement)) {
      setActiveIndex(firstSegmentIndex);
    }
  }, [placementsKey, firstSegmentIndex, placements]);

  const reportGlobalImmediate = useCallback(
    (globalT: number) => {
      lastReportedAtRef.current =
        typeof performance !== "undefined" ? performance.now() : Date.now();
      onPlaybackTime(globalT);
    },
    [onPlaybackTime]
  );

  const flushPendingGlobal = useCallback(() => {
    rafPendingRef.current = null;
    const t = pendingGlobalTRef.current;
    pendingGlobalTRef.current = null;
    if (t != null) {
      reportGlobalImmediate(t);
    }
  }, [reportGlobalImmediate]);

  /** 节流上抛：~80ms 内最多一次（rAF 在前台保证不超过 60Hz，时间窗口再加节流） */
  const reportGlobalThrottled = useCallback(
    (el: HTMLVideoElement | null) => {
      if (!el) {
        return;
      }
      const p = placements[activeIndexRef.current];
      if (!p || !isVideoSegment(p)) {
        return;
      }
      const globalT = p.startSec + el.currentTime;
      globalTimeRef.current = globalT;
      const now = typeof performance !== "undefined" ? performance.now() : Date.now();
      if (now - lastReportedAtRef.current >= 80) {
        if (rafPendingRef.current != null) {
          cancelAnimationFrame(rafPendingRef.current);
          rafPendingRef.current = null;
        }
        pendingGlobalTRef.current = null;
        reportGlobalImmediate(globalT);
        return;
      }
      pendingGlobalTRef.current = globalT;
      if (rafPendingRef.current == null) {
        rafPendingRef.current = requestAnimationFrame(flushPendingGlobal);
      }
    },
    [placements, reportGlobalImmediate, flushPendingGlobal]
  );

  const seekGlobal = useCallback(
    (globalT: number) => {
      const t = Math.max(0, Math.min(globalT, endSec > 0 ? endSec : 0));
      globalTimeRef.current = t;
      lastVideoSyncIndexRef.current = -1;
      mountedVideoIndexRef.current = -1;
      for (let i = 0; i < placements.length; i++) {
        const p = placements[i]!;
        if (t < p.endSec - 1e-4) {
          if (isVideoSegment(p)) {
            const localT = Math.max(0, Math.min(t - p.startSec, p.durationSec - 1e-3));
            if (i === activeIndexRef.current) {
              const el = videoRef.current;
              if (el) {
                el.currentTime = localT;
              }
              onPlaybackTime(t);
            } else {
              suppressVideoPauseForBgmRef.current = true;
              pendingLocalSeek.current = localT;
              mountedVideoIndexRef.current = -1;
              autoPlayAfterAdvanceRef.current = true;
              activeIndexRef.current = i;
              setActiveIndex(i);
              onPlaybackTime(t);
            }
          } else if (hasTimelineSegment(p)) {
            if (i !== activeIndexRef.current) {
              pendingLocalSeek.current = null;
              setActiveIndex(i);
            }
            onPlaybackTime(Math.max(p.startSec, Math.min(t, p.endSec - 1e-4)));
          } else {
            onPlaybackTime(Math.min(t, p.endSec));
          }
          if (bgmDrivesClock) {
            seekBgmToGlobalTime();
          }
          return;
        }
      }
      onPlaybackTime(endSec);
      if (bgmDrivesClock) {
        seekBgmToGlobalTime();
      }
    },
    [placements, endSec, onPlaybackTime, videoRef, bgmDrivesClock, seekBgmToGlobalTime]
  );

  useLayoutEffect(() => {
    onRegisterSeek(seekGlobal);
    return () => {
      onRegisterSeek(() => {});
    };
  }, [onRegisterSeek, seekGlobal]);

  const onTimeUpdate = (e: React.SyntheticEvent<HTMLVideoElement>) => {
    if (bgmDrivesClock) {
      return;
    }
    reportGlobalThrottled(e.currentTarget);
  };

  /** 预加载下一镜的 URL：从当前位置向后找第一个有 videoUrl 的 placement，浏览器后台 fetch 元数据 + 数据，
   *  避免切到下一镜时白屏 / 暂停感。仅在 timelinePlaying 时启用，避免页面静止也消耗带宽。 */
  const nextPreloadVideoUrl = useMemo(() => {
    if (!timelinePlaying) return null;
    for (let j = activeIndex + 1; j < placements.length; j++) {
      const p = placements[j];
      if (isVideoSegment(p)) {
        return p!.videoUrl || null;
      }
    }
    return null;
  }, [timelinePlaying, activeIndex, placements]);

  const onEnded = useCallback(() => {
    if (bgmDrivesClock) {
      suppressVideoPauseForBgmRef.current = true;
      return;
    }
    for (let j = activeIndex + 1; j < placements.length; j++) {
      const p = placements[j]!;
      if (!hasTimelineSegment(p)) {
        continue;
      }
      suppressVideoPauseForBgmRef.current = true;
      autoPlayAfterAdvanceRef.current = isVideoSegment(p);
      setActiveIndex(j);
      globalTimeRef.current = p.startSec;
      onPlaybackTime(p.startSec);
      if (!autoPlayAfterAdvanceRef.current) {
        setTimelinePlaying(true);
      }
      return;
    }
    setTimelinePlaying(false);
    bgmRef.current?.pause();
    onPlaybackTime(endSec);
    globalTimeRef.current = endSec;
  }, [placements, activeIndex, onPlaybackTime, endSec, bgmDrivesClock]);

  useEffect(() => {
    if (!timelinePlaying || bgmDrivesClock) {
      return;
    }
    const p = placements[activeIndexRef.current];
    if (isVideoSegment(p)) {
      return;
    }
    if (!hasTimelineSegment(p)) {
      return;
    }
    const id = window.setInterval(() => {
      const ac = activeIndexRef.current;
      const cur = placements[ac];
      if (!hasTimelineSegment(cur) || isVideoSegment(cur)) {
        return;
      }
      const t0 = globalTimeRef.current;
      const nextT = t0 + 0.08;
      if (nextT >= cur.endSec - 1e-4) {
        let found = -1;
        for (let j = ac + 1; j < placements.length; j++) {
          if (hasTimelineSegment(placements[j])) {
            found = j;
            break;
          }
        }
        if (found >= 0) {
          const np = placements[found]!;
          suppressVideoPauseForBgmRef.current = true;
          autoPlayAfterAdvanceRef.current = isVideoSegment(np);
          setActiveIndex(found);
          onPlaybackTime(np.startSec);
          globalTimeRef.current = np.startSec;
          if (!autoPlayAfterAdvanceRef.current) {
            setTimelinePlaying(true);
          }
        } else {
          setTimelinePlaying(false);
          bgmRef.current?.pause();
          onPlaybackTime(endSec);
          globalTimeRef.current = endSec;
        }
      } else {
        onPlaybackTime(nextT);
        globalTimeRef.current = nextT;
      }
    }, 80);
    return () => window.clearInterval(id);
  }, [timelinePlaying, placements, onPlaybackTime, endSec]);

  const safeEnd = endSec > 0 ? endSec : 0.1;
  const rangeVal = Math.min(currentGlobalTime, safeEnd - 1e-6);

  const isAtTimelineEnd = useCallback(() => {
    const cap = endSec > 0 ? endSec : 0;
    const gt = globalTimeRef.current;
    return cap > 0.05 && gt >= cap - 0.08;
  }, [endSec]);

  const togglePlay = useCallback(() => {
    const p = placements[activeIndexRef.current];
    if (isVideoSegment(p)) {
      const el = videoRef.current;
      if (!el) {
        return;
      }
      if (el.paused) {
        if (isAtTimelineEnd()) {
          seekGlobal(0);
        }
        setTimelinePlaying(true);
        if (bgmDrivesClock) {
          ensureBgmPlaying();
        } else {
          el.play().catch(() => {});
        }
      } else {
        setTimelinePlaying(false);
        el.pause();
        bgmRef.current?.pause();
      }
      return;
    }
    if (hasTimelineSegment(p)) {
      if (timelinePlaying) {
        setTimelinePlaying(false);
        bgmRef.current?.pause();
      } else {
        if (isAtTimelineEnd()) {
          seekGlobal(0);
        }
        setTimelinePlaying(true);
      }
    }
  }, [
    videoRef,
    placements,
    bgmDrivesClock,
    ensureBgmPlaying,
    timelinePlaying,
    isAtTimelineEnd,
    seekGlobal,
  ]);

  if (firstSegmentIndex < 0) {
    return null;
  }

  const active = placements[activeIndex];
  const videoActive = isVideoSegment(active);
  const posterActive =
    !!active && !videoActive && !!active.posterUrl && active.durationSec > 0.05;
  const blackActive =
    !!active && !videoActive && !active.posterUrl && active.durationSec > 0.05;

  if (!active || active.durationSec <= 0.05) {
    return null;
  }

  const frameClass = frameAspectClass(frameAspectRatio);

  return (
    <div className={cn("w-full max-w-4xl mx-auto rounded-lg overflow-hidden border border-border/20", className)}>
      {bgmAudioUrl ? (
        <audio
          key={bgmAudioUrl}
          ref={bgmRef}
          src={bgmAudioUrl}
          playsInline
          preload="auto"
          className="sr-only h-0 w-0 overflow-hidden opacity-0"
          aria-hidden
        />
      ) : null}
      {nextPreloadVideoUrl ? (
        // 隐藏预加载下一镜，浏览器后台 fetch 媒体数据，切换 activeIndex 时大概率已有缓存可直接 canplay；
        // muted + 极小尺寸 + preload="auto"，不占视觉、不发声、不参与主播放轨。
        <video
          key={`preload-${nextPreloadVideoUrl}`}
          src={nextPreloadVideoUrl}
          muted
          playsInline
          preload="auto"
          className="sr-only h-0 w-0 overflow-hidden opacity-0"
          aria-hidden
        />
      ) : null}
      <div className="relative w-full flex justify-center group">
        <div className={cn("relative w-full max-w-4xl bg-black", frameClass)}>
          <div className="absolute inset-0 flex items-center justify-center">
          {videoActive && (
            <VideoWithCleanup
              key={`${activeIndex}-${active.videoUrl}`}
              ref={videoRef}
              className="h-full w-full object-contain bg-black"
              controls={false}
              src={active.videoUrl!}
              poster={timelinePlaying ? undefined : active.posterUrl}
              preload="auto"
              onTimeUpdate={onTimeUpdate}
              onPlay={() => {
                suppressVideoPauseForBgmRef.current = false;
                setTimelinePlaying(true);
              }}
              onPause={() => {
                if (suppressVideoPauseForBgmRef.current) {
                  return;
                }
                if (bgmDrivesClock && timelinePlaying && e.currentTarget.ended) {
                  return;
                }
                setTimelinePlaying(false);
              }}
              onLoadedMetadata={(e) => {
                mountedVideoIndexRef.current = activeIndexRef.current;
                const p = placements[activeIndex];
                if (p && isVideoSegment(p)) {
                  if (pendingLocalSeek.current != null) {
                    e.currentTarget.currentTime = pendingLocalSeek.current;
                    pendingLocalSeek.current = null;
                  }
                  if (!bgmDrivesClock) {
                    onPlaybackTime(p.startSec + e.currentTarget.currentTime);
                  }
                }
                if (
                  !bgmDrivesClock &&
                  !suppressVideoPauseForBgmRef.current &&
                  !autoPlayAfterAdvanceRef.current
                ) {
                  setTimelinePlaying(!e.currentTarget.paused);
                }
              }}
              onCanPlay={(e) => {
                suppressVideoPauseForBgmRef.current = false;
                if (autoPlayAfterAdvanceRef.current || timelinePlaying) {
                  autoPlayAfterAdvanceRef.current = false;
                  setTimelinePlaying(true);
                  e.currentTarget.play().catch(() => {
                    // 部分浏览器会拦自动带声播放
                  });
                }
              }}
              onEnded={onEnded}
              playsInline
            />
          )}
          {(posterActive || blackActive) && (
            <div className="flex h-full w-full items-center justify-center bg-black">
              {posterActive ? (
                <img
                  src={active.posterUrl}
                  alt=""
                  className="h-full w-full object-contain"
                />
              ) : null}
            </div>
          )}
          </div>
          <div
            className={cn(
              "absolute inset-0 z-[3] flex items-center justify-center transition-opacity duration-200",
              timelinePlaying
                ? "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100"
                : "pointer-events-auto opacity-100"
            )}
          >
            <Button
              type="button"
              size="icon"
              className="h-16 w-16 rounded-full border border-white/30 bg-black/50 text-white shadow-lg backdrop-blur-sm hover:bg-black/65"
              onClick={togglePlay}
              aria-label={timelinePlaying ? t("chainedPlayerPause") : t("chainedPlayerPlay")}
            >
              {timelinePlaying ? <Pause className="h-7 w-7" /> : <Play className="h-7 w-7 pl-0.5" />}
            </Button>
          </div>
          <div
            className="pointer-events-none absolute bottom-14 left-0 right-0 z-[1] flex justify-center px-3"
            title={t("chainedPlayerGlobalTimeHint")}
          >
            <span className="text-xs font-mono tabular-nums text-white/95 [text-shadow:0_1px_2px_rgba(0,0,0,0.9)]">
              {formatClockShort(currentGlobalTime)} / {formatClockShort(safeEnd)}
            </span>
          </div>
          <div className="pointer-events-auto absolute bottom-0 left-0 right-0 z-[2] bg-gradient-to-t from-black/80 via-black/20 to-transparent px-3 pb-2 pt-10">
            <input
              type="range"
              className="h-1.5 w-full cursor-pointer accent-primary"
              min={0}
              max={safeEnd}
              step={0.01}
              value={rangeVal}
              aria-label="Seek across shots"
              onInput={(e) => {
                seekGlobal(parseFloat((e.target as HTMLInputElement).value));
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
