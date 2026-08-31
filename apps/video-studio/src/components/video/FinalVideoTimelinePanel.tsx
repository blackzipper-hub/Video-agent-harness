import { memo, useCallback, useMemo, useRef } from "react";
import { Music } from "lucide-react";
import { cn } from "@/lib/utils";
import { getPreviewTimelineEndSec, getShotClipPlacementsForPreview } from "@/lib/videoTimelinePlacements";
import type { ShotClipPlacement } from "@/lib/videoTimelinePlacements";
import { useLanguage } from "@/i18n/LanguageContext";

type FinalVideoTimelinePanelProps = {
  totalDurationFromAssembly: number;
  currentTime: number;
  onSeek: (timeSec: number) => void;
  videosData?: any;
  /** 与 Shots 区选版一致，用于按镜估算片段边界 */
  selectedVersionsByVideoId?: Map<string, string> | null;
  /** 与上方主预演同源：已由父级合并镜头 MP4 + 关键帧首帧时传入，避免仅用 videosData 重算丢关键帧格 */
  previewPlacements?: ShotClipPlacement[] | null;
  /** 与 VideoAssemblySection 一致：无 assemble 时按目标总时长补未生成镜占位 */
  masterTargetDurationSec?: number;
  className?: string;
  /** 默认不展示「成片时间线」标题与长说明，仅留轨道 */
  showIntro?: boolean;
};

function makeSinglePlacement(totalSec: number): ShotClipPlacement[] {
  const t = totalSec > 0 ? totalSec : 1;
  return [
    { startSec: 0, endSec: t, shotNumber: 0, durationSec: t },
  ];
}

function shotBorderHue(shotNumber: number): string {
  if (shotNumber <= 0) {
    return "hsl(280 55% 42%)";
  }
  return `hsl(${(shotNumber * 47 + 20) % 360} 45% 48%)`;
}

/**
 * 重 DOM（刻度 / 镜头条 / 波形）单独 memo，避免每帧 currentTime 变化都重建上百节点。
 * 仅在 placements / safeEnd / tickValues / 标签 变化时才重渲。
 */
type TimelineHeavyBackgroundProps = {
  placements: ShotClipPlacement[];
  safeEnd: number;
  tickValues: number[];
  onSeek: (timeSec: number) => void;
  shotsLabel: string;
  finalVideoLabel: string;
};

const TimelineHeavyBackground = memo(function TimelineHeavyBackground({
  placements,
  safeEnd,
  tickValues,
  onSeek,
  shotsLabel,
  finalVideoLabel,
}: TimelineHeavyBackgroundProps) {
  return (
    <>
      <div
        className="relative h-5 border-b border-border/30 bg-zinc-900/90"
        aria-hidden
      >
        {tickValues.map((sec) => (
          <div
            key={sec}
            className="absolute top-0 bottom-0 w-px bg-white/20"
            style={{ left: `${(sec / safeEnd) * 100}%` }}
          />
        ))}
        {tickValues.map((sec) => (
          <div
            key={`lbl-${sec}`}
            className="absolute top-0.5 text-[10px] text-muted-foreground/90 -translate-x-1/2 tabular-nums"
            style={{ left: `${(sec / safeEnd) * 100}%` }}
          >
            {`${Math.round(sec)}s`}
          </div>
        ))}
      </div>
      <div className="relative h-[52px] bg-zinc-950/40">
        {placements.map((p) => {
          if (p.durationSec <= 0) {
            return null;
          }
          const leftPct = (p.startSec / safeEnd) * 100;
          const widthPct = (p.durationSec / safeEnd) * 100;
          const hue = shotBorderHue(p.shotNumber);
          return (
            <div
              key={p.shotNumber === 0 ? "all" : `s-${p.shotNumber}`}
              className={cn(
                "absolute top-0 bottom-0 z-[1] group overflow-hidden border-l-2",
                p.posterUrl ? "bg-zinc-900" : "bg-primary/20 hover:bg-primary/32"
              )}
              style={{
                left: `${leftPct}%`,
                width: `${Math.max(widthPct, 0.15)}%`,
                borderLeftColor: p.shotNumber ? hue : "hsl(280 45% 45% / 0.5)",
                boxShadow: p.shotNumber
                  ? `inset 0 0 0 1px ${hue}40`
                  : undefined,
              }}
              onMouseDown={(ev) => {
                ev.stopPropagation();
                onSeek(p.startSec);
              }}
              title={
                p.shotNumber ? `${shotsLabel} ${p.shotNumber}` : finalVideoLabel
              }
            >
              {p.posterUrl && (
                <div
                  className="absolute inset-0 opacity-90 group-hover:opacity-100"
                  style={{
                    backgroundImage: `url(${p.posterUrl})`,
                    backgroundSize: "auto 100%",
                    backgroundRepeat: "repeat-x",
                    backgroundPosition: "left center",
                  }}
                />
              )}
              {p.shotNumber > 0 && (
                <div className="absolute top-0.5 left-0.5 z-[2] min-w-[14px] h-3.5 px-0.5 rounded-sm bg-black/50 text-[10px] font-semibold text-white/95 leading-3.5 text-center">
                  {p.shotNumber}
                </div>
              )}
              {!p.posterUrl && p.shotNumber > 0 && (
                <div className="absolute inset-0 flex items-end justify-end pb-0.5 pr-0.5 text-[9px] text-white/70">
                  {p.shotNumber}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="relative h-2.5 flex border-t border-border/25 bg-zinc-900/70">
        <div className="flex h-full w-5 flex-shrink-0 items-center justify-center text-muted-foreground/50">
          <Music className="h-2.5 w-2.5" aria-hidden />
        </div>
        <div className="flex flex-1 h-full items-end gap-px pr-0.5 pb-px pl-0">
          {WAVE_BARS.map((b) => (
            <div
              key={b}
              className="flex-1 min-w-px bg-primary/25"
              style={{ height: `${barHeight(b, safeEnd)}%` }}
              aria-hidden
            />
          ))}
        </div>
      </div>
    </>
  );
});

/**
 * 成片区域下方：类 VidMuse 的刻度、分镜条（关键帧平铺）与示意音轨；与播放器同一播放时间。
 */
export function FinalVideoTimelinePanel({
  totalDurationFromAssembly,
  currentTime,
  onSeek,
  videosData,
  selectedVersionsByVideoId,
  previewPlacements,
  masterTargetDurationSec,
  className,
  showIntro = false,
}: FinalVideoTimelinePanelProps) {
  const { t } = useLanguage();
  const trackRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const placements = useMemo(() => {
    if (previewPlacements != null && previewPlacements.length > 0) {
      return previewPlacements;
    }
    if (videosData && (videosData?.video_generations?.length > 0 || (Number(videosData?.total) > 0))) {
      return getShotClipPlacementsForPreview(
        videosData,
        selectedVersionsByVideoId ?? new Map(),
        masterTargetDurationSec != null && Number.isFinite(masterTargetDurationSec) && masterTargetDurationSec > 0.05
          ? { masterTargetDurationSec }
          : undefined
      );
    }
    return makeSinglePlacement(
      Number.isFinite(totalDurationFromAssembly) && totalDurationFromAssembly > 0
        ? totalDurationFromAssembly
        : 1
    );
  }, [previewPlacements, videosData, selectedVersionsByVideoId, totalDurationFromAssembly, masterTargetDurationSec]);

  const endSec = Math.max(
    getPreviewTimelineEndSec(placements),
    Number.isFinite(totalDurationFromAssembly) && totalDurationFromAssembly > 0
      ? totalDurationFromAssembly
      : 0.05
  );
  // 有成片实测时长时，刻度与分镜条以播放器为准，避免分镜条总宽超过成片
  const safeEnd =
    Number.isFinite(totalDurationFromAssembly) && totalDurationFromAssembly > 0.05
      ? totalDurationFromAssembly
      : endSec > 0
        ? endSec
        : 1;

  const playheadLeftPct = Math.min(100, (currentTime / safeEnd) * 100);

  const tickStep = useMemo(() => {
    if (safeEnd > 150) {
      return 20;
    }
    if (safeEnd > 90) {
      return 10;
    }
    if (safeEnd > 50) {
      return 5;
    }
    return 1;
  }, [safeEnd]);

  const tickValues = useMemo(() => {
    const out: number[] = [];
    for (let v = tickStep; v < safeEnd; v += tickStep) {
      out.push(v);
    }
    return out;
  }, [safeEnd, tickStep]);

  const seekFromClientX = useCallback(
    (clientX: number) => {
      const el = trackRef.current;
      if (!el) {
        return;
      }
      const rect = el.getBoundingClientRect();
      if (rect.width < 1) {
        return;
      }
      const r = (clientX - rect.left) / rect.width;
      onSeek(Math.max(0, Math.min(safeEnd, r * safeEnd)));
    },
    [onSeek, safeEnd]
  );

  const onTrackPointerDown = (e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    seekFromClientX(e.clientX);
    const onMove = (me: MouseEvent) => {
      if (!draggingRef.current) {
        return;
      }
      seekFromClientX(me.clientX);
    };
    const onUp = () => {
      draggingRef.current = false;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div className={cn(showIntro ? "mt-3 space-y-1.5" : "mt-2", className)}>
      {showIntro && (
        <>
          <div className="text-xs font-medium text-foreground/90">
            {t("finalVideoClipTimeline")}
          </div>
          <p className="text-[11px] text-muted-foreground/85 leading-relaxed">
            {t("finalVideoClipTimelineHint")}
          </p>
        </>
      )}
      <div
        ref={trackRef}
        className="relative w-full select-none cursor-pointer rounded-md border border-border/50 bg-zinc-950/80 overflow-hidden shadow-inner"
        onMouseDown={onTrackPointerDown}
        role="slider"
        aria-label="Seek final video"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") {
            e.preventDefault();
            onSeek(Math.max(0, currentTime - 0.5));
          }
          if (e.key === "ArrowRight") {
            e.preventDefault();
            onSeek(Math.min(safeEnd, currentTime + 0.5));
          }
        }}
      >
        <TimelineHeavyBackground
          placements={placements}
          safeEnd={safeEnd}
          tickValues={tickValues}
          onSeek={onSeek}
          shotsLabel={t("shotsSection")}
          finalVideoLabel={t("finalVideoSection")}
        />
        <div
          className="absolute top-0 bottom-0 z-10 pointer-events-none w-0"
          style={{ left: `${playheadLeftPct}%` }}
        >
          <div
            className="absolute -top-px left-1/2 -translate-x-1/2 w-0 h-0 z-20 border-l-[5px] border-r-[5px] border-t-[6px] border-l-transparent border-r-transparent border-t-white/95 drop-shadow-sm"
            aria-hidden
          />
          <div className="absolute top-1.5 left-1/2 w-px h-[calc(100%-4px)] -translate-x-1/2 bg-white/95 shadow-[0_0_2px_rgba(0,0,0,0.8)]" />
        </div>
      </div>
      <div
        className="text-center text-[10px] font-mono text-muted-foreground tabular-nums"
        title={t("masterTimelineTimeHint")}
        role="status"
      >
        <span className="text-foreground/90">{formatClock(currentTime)}</span>
        <span className="mx-1.5 text-muted-foreground/50">/</span>
        <span>{formatClock(safeEnd)}</span>
      </div>
    </div>
  );
}

const WAVE_BARS = Array.from({ length: 72 }, (_, i) => i);

function barHeight(i: number, totalSec: number): number {
  const n = 22 + 58 * (0.5 + 0.5 * Math.sin((i * 0.45 + totalSec * 0.01) % 6.28));
  return Math.min(98, Math.max(18, n));
}

function formatClock(sec: number) {
  if (!Number.isFinite(sec) || sec < 0) {
    return "00:00";
  }
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}
