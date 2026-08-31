import { useMemo } from "react";
import { getShotClipPlacementsForPreview } from "@/lib/videoTimelinePlacements";

/**
 * A 方法编辑态 v1：从 shots 与选版推预览时间线（不替代 assemble 成片）。
 */
export function useVideoEditProject(
  _mode: "shots",
  options: {
    videosData: any;
    selectedVersionByVideoId: Map<string, string>;
  }
) {
  const clipPlacements = useMemo(
    () => getShotClipPlacementsForPreview(options.videosData, options.selectedVersionByVideoId),
    [options.videosData, options.selectedVersionByVideoId]
  );
  return { clipPlacements };
}
