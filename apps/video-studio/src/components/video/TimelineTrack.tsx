import { cn } from "@/lib/utils";
import type { ShotClipPlacement } from "@/lib/videoTimelinePlacements";
import { getPreviewTimelineEndSec } from "@/lib/videoTimelinePlacements";

type TimelineTrackProps = {
  placements: ShotClipPlacement[];
  className?: string;
};

/**
 * 仅预演用：不写入 VideoAssembly。
 */
export function TimelineTrack({ placements, className }: TimelineTrackProps) {
  const totalSec = getPreviewTimelineEndSec(placements);
  return (
    <div
      className={cn(
        "w-full h-8 rounded-md bg-muted/60 border border-border/50 relative overflow-hidden",
        className
      )}
      role="img"
      aria-label="Shots duration preview (approximate)"
    >
      {placements.map((p) => {
        if (p.durationSec <= 0) {
          return null;
        }
        const leftPct = (p.startSec / totalSec) * 100;
        const widthPct = (p.durationSec / totalSec) * 100;
        return (
          <div
            key={`s-${p.shotNumber}`}
            title={`Shot ${p.shotNumber}: ${p.durationSec.toFixed(1)}s`}
            className="absolute top-0 bottom-0 bg-primary/45 hover:bg-primary/60 border-r border-border/30 text-[9px] leading-8 text-center text-primary-foreground/90 truncate px-0.5"
            style={{ left: `${leftPct}%`, width: `${Math.max(widthPct, 0.3)}%` }}
          >
            {p.shotNumber}
          </div>
        );
      })}
    </div>
  );
}
