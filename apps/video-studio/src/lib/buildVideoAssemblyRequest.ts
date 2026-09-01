export function collectShotVideoAssemblySelections(
  videosData: { video_generations?: any[] } | null | undefined,
  selectedVersions: Map<string, string>
): { videoGenUuid: string; versionUuid: string }[] {
  const out: { videoGenUuid: string; versionUuid: string }[] = [];
  const vgens = videosData?.video_generations;
  if (!Array.isArray(vgens)) {
    return out;
  }
  for (const video of vgens) {
    if (!video?.versions?.length) {
      continue;
    }
    const selectedVersionUuid = selectedVersions.get(video.uuid);
    let targetVersion = selectedVersionUuid
      ? video.versions.find((v: { uuid: string }) => v.uuid === selectedVersionUuid)
      : null;
    if (!targetVersion) {
      const currentIndex = video.current_version_index || 0;
      targetVersion = video.versions[currentIndex];
    }
    if (!targetVersion) {
      targetVersion = video.versions[0];
    }
    if (targetVersion) {
      out.push({ videoGenUuid: video.uuid, versionUuid: targetVersion.uuid });
    }
  }
  return out;
}

/**
 * 与 `POST /agent-router/video-editing/video-assembly` 体一致；shots 模式：先 sync 再合（有 videos 时由后端处理）。
 */
export function buildShotsVideoAssemblyRequestBody(
  threadId: string,
  userOption: unknown,
  selectionList: { videoGenUuid: string; versionUuid: string }[]
): Record<string, unknown> {
  const videos =
    selectionList.length > 0
      ? selectionList.map((s) => ({
          uuid: s.videoGenUuid,
          selected_version: { uuid: s.versionUuid },
        }))
      : undefined;
  const body: Record<string, unknown> = {
    thread_id: threadId,
    segment_versions: [],
  };
  if (videos != null && videos.length > 0) {
    body.videos = videos;
  }
  if (userOption !== undefined && userOption !== null) {
    body.user_option = userOption;
  }
  return body;
}

export function buildSegmentsVideoAssemblyRequestBody(
  threadId: string,
  segmentVersions: { segmentUuid: string; versionUuid: string }[]
): Record<string, unknown> {
  return {
    thread_id: threadId,
    segment_versions: segmentVersions.map((s) => ({
      segment_uuid: s.segmentUuid,
      segment_version_uuid: s.versionUuid,
    })),
  };
}
