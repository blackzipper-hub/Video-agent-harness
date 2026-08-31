import { useEffect, useMemo, useRef, useState } from "react";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Film, Video } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { LazyStoryboardsSection } from "./LazyStoryboardsSection";
import { LazyShotsSection } from "./LazyShotsSection";
import { resolveKeyframeDisplayMetrics } from "./keyframeDisplayUtils";

interface LazyStoryboardShotsSectionProps {
  keyframesData: any;
  videosData: any;
  videoProgress?: {
    completed: number;
    total: number;
    progress_percent: number;
  };
  keyframesTotalCount?: number;
  shotsTotalCount?: number;
  selectedKeyframeVersions?: Map<string, string>;
  onKeyframeVersionSelection?: (keyframeUuid: string, versionUuid: string) => void;
  selectedVersions?: Map<string, string>;
  onVersionSelection?: (videoUuid: string, versionUuid: string) => void;
  onRegenerateKeyframe?: (shotNumber: number, prompt: string, frameIndex?: number) => Promise<boolean>;
  onKeyframeImageEditWithInstruction?: (
    shotNumber: number,
    instruction: string,
    versionIndex: number,
    frameIndex?: number,
  ) => Promise<boolean>;
  onRefreshKeyframes?: () => Promise<void>;
  onMentionClick?: (type: "image" | "prompt", keyframe: any) => void;
  zoomLevel?: number;
  userOption?: any;
  threadId?: string;
  onKeyframeRefinePromptOnly?: (
    keyframeUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  companionDiceKeys?: ReadonlySet<string>;
  onRegenerateVideo?: (shotNumber: number, prompt: string) => Promise<boolean>;
  onVideoRefinePromptOnly?: (
    videoUuid: string,
    versionUuid: string,
    instruction: string,
  ) => Promise<string>;
  conversationId?: string;
  onSegmentsSynced?: () => void;
  onVideoAssembled?: (assemblyUuid: string) => void | Promise<void>;
  companionVideoDiceKeys?: ReadonlySet<string>;
  companionMergeAssemblyBusy?: boolean;
  onStoryboardsFullView?: () => void;
  onShotsFullView?: () => void;
}

export const LazyStoryboardShotsSection = ({
  keyframesData,
  videosData,
  videoProgress,
  keyframesTotalCount,
  shotsTotalCount,
  selectedKeyframeVersions,
  onKeyframeVersionSelection,
  selectedVersions,
  onVersionSelection,
  onRegenerateKeyframe,
  onKeyframeImageEditWithInstruction,
  onRefreshKeyframes,
  onMentionClick,
  zoomLevel = 1,
  userOption,
  threadId,
  onKeyframeRefinePromptOnly,
  companionDiceKeys,
  onRegenerateVideo,
  onVideoRefinePromptOnly,
  conversationId,
  onSegmentsSynced,
  onVideoAssembled,
  companionVideoDiceKeys,
  companionMergeAssemblyBusy,
  onStoryboardsFullView,
  onShotsFullView,
}: LazyStoryboardShotsSectionProps) => {
  const { t } = useLanguage();
  const [activeTab, setActiveTab] = useState<"storyboards" | "shots">("storyboards");
  const scrollTopRef = useRef<{ storyboards: number; shots: number }>({ storyboards: 0, shots: 0 });
  const [storyboardsSyncedTop, setStoryboardsSyncedTop] = useState(0);
  const [shotsSyncedTop, setShotsSyncedTop] = useState(0);

  const hasStoryboards =
    (keyframesData?.keyframes?.length || 0) > 0 ||
    (Number.isFinite(keyframesData?.total) && keyframesData.total > 0) ||
    (Number.isFinite((keyframesData as any)?.shot_total) && (keyframesData as any).shot_total > 0) ||
    (Number.isFinite(keyframesTotalCount) && (keyframesTotalCount as number) > 0);
  const hasShots =
    (videosData?.video_generations?.length || 0) > 0 ||
    (Number.isFinite(videosData?.total) && videosData.total > 0) ||
    (Number.isFinite(shotsTotalCount) && (shotsTotalCount as number) > 0);

  useEffect(() => {
    if (activeTab === "storyboards" && !hasStoryboards && hasShots) {
      setActiveTab("shots");
    }
  }, [activeTab, hasStoryboards, hasShots]);

  const storyboardCountLabel = useMemo(() => {
    const generatedCount = (keyframesData?.keyframes || []).reduce((count: number, keyframe: any) => {
      const currentVersion = keyframe?.versions?.[keyframe?.current_version_index || 0];
      return currentVersion?.keyframe_url ? count + 1 : count;
    }, 0);
    const { expectedRecordTotal } = resolveKeyframeDisplayMetrics(
      keyframesData,
      Number.isFinite(keyframesTotalCount) ? (keyframesTotalCount as number) : undefined,
    );
    const totalCount = expectedRecordTotal > 0 ? expectedRecordTotal : generatedCount;
    return `(${generatedCount}/${totalCount})`;
  }, [keyframesData, keyframesTotalCount]);

  const shotsCountLabel = useMemo(() => {
    if (videoProgress && videoProgress.total > 0) {
      return `(${videoProgress.completed}/${videoProgress.total})`;
    }
    const generatedCount = (videosData?.video_generations || []).filter((video: any) =>
      (video?.versions || []).some((v: any) => !!v?.video_url)
    ).length;
    const totalCount =
      Number.isFinite(videosData?.total) && videosData.total > 0
        ? videosData.total
        : Number.isFinite(shotsTotalCount) && (shotsTotalCount as number) > 0
          ? (shotsTotalCount as number)
          : generatedCount;
    return `(${generatedCount}/${totalCount})`;
  }, [videoProgress, videosData, shotsTotalCount]);

  const handleTabChange = (value: string) => {
    const next = value as "storyboards" | "shots";
    if (next === activeTab) return;
    if (next === "shots") {
      const alignedTop = scrollTopRef.current.storyboards;
      scrollTopRef.current.shots = alignedTop;
      setShotsSyncedTop(alignedTop);
    } else {
      const alignedTop = scrollTopRef.current.shots;
      scrollTopRef.current.storyboards = alignedTop;
      setStoryboardsSyncedTop(alignedTop);
    }
    setActiveTab(next);
  };

  return (
    <Card className="glass p-4 h-auto">
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <div className="mb-4 flex items-center justify-between">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="storyboards" className="gap-2 px-2 text-xs sm:text-sm">
              <Film className="w-4 h-4" />
              {t("storyboardsSection")} {storyboardCountLabel}
            </TabsTrigger>
            <TabsTrigger value="shots" className="gap-2 px-2 text-xs sm:text-sm">
              <Video className="w-4 h-4" />
              {t("shotsSection")} {shotsCountLabel}
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="storyboards" className="mt-0">
          <LazyStoryboardsSection
            keyframesData={keyframesData}
            onRegenerate={onRegenerateKeyframe}
            onKeyframeImageEditWithInstruction={onKeyframeImageEditWithInstruction}
            onRefreshKeyframes={onRefreshKeyframes}
            onMentionClick={onMentionClick}
            onFullView={onStoryboardsFullView}
            zoomLevel={zoomLevel}
            userOption={userOption}
            threadId={threadId}
            totalCount={keyframesTotalCount}
            selectedKeyframeVersions={selectedKeyframeVersions}
            onKeyframeVersionSelection={onKeyframeVersionSelection}
            onKeyframeRefinePromptOnly={onKeyframeRefinePromptOnly}
            companionDiceKeys={companionDiceKeys}
            syncedScrollTop={storyboardsSyncedTop}
            onSyncedScrollTopChange={(scrollTop) => {
              scrollTopRef.current.storyboards = scrollTop;
            }}
            embedded
          />
        </TabsContent>

        <TabsContent value="shots" className="mt-0">
          <LazyShotsSection
            videosData={videosData}
            onRegenerate={onRegenerateVideo}
            onVideoRefinePromptOnly={onVideoRefinePromptOnly}
            selectedVersions={selectedVersions}
            onVersionSelection={onVersionSelection}
            conversationId={conversationId}
            threadId={threadId}
            userOption={userOption}
            videoProgress={videoProgress}
            onSegmentsSynced={onSegmentsSynced}
            onVideoAssembled={onVideoAssembled}
            onFullView={onShotsFullView}
            zoomLevel={zoomLevel}
            totalCount={shotsTotalCount}
            companionDiceKeys={companionVideoDiceKeys}
            companionMergeAssemblyBusy={companionMergeAssemblyBusy}
            syncedScrollTop={shotsSyncedTop}
            onSyncedScrollTopChange={(scrollTop) => {
              scrollTopRef.current.shots = scrollTop;
            }}
            embedded
          />
        </TabsContent>
      </Tabs>
    </Card>
  );
};
