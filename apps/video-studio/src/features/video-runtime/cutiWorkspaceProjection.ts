import type { RuntimeArtifact, RuntimeWorkspace } from "./client";

type JsonRecord = Record<string, unknown>;

export interface CutiWorkspaceProjection {
  effectiveVideoSpec: JsonRecord | null;
  storyOutlineData: JsonRecord | null;
  analysisData: JsonRecord | null;
  charactersData: JsonRecord | null;
  scenesData: JsonRecord | null;
  keyframesData: JsonRecord;
  videosData: JsonRecord;
  musicData: JsonRecord | null;
  videoAssemblyData: JsonRecord | null;
}

const record = (value: unknown): JsonRecord | null => (
  value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null
);

const arrayRecords = (value: unknown): JsonRecord[] => (
  Array.isArray(value)
    ? value.filter((item): item is JsonRecord => Boolean(record(item)))
    : []
);

const artifactContent = (artifact?: RuntimeArtifact): unknown => artifact?.metadata?.content;

const artifactPrompt = (artifact: RuntimeArtifact): string => {
  const metadata = artifact.metadata || {};
  const generation = record(metadata.generation_parameters);
  for (const value of [
    metadata.generated_prompt,
    metadata.generation_prompt,
    generation?.prompt,
    generation?.visual_prompt,
    generation?.motion_prompt,
  ]) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
};

const byNewest = (left: RuntimeArtifact, right: RuntimeArtifact) => (
  right.version - left.version
  || String(right.created_at || "").localeCompare(String(left.created_at || ""))
);

const groupedArtifacts = (workspace: RuntimeWorkspace): Map<string, RuntimeArtifact[]> => {
  const groups = new Map<string, RuntimeArtifact[]>();
  workspace.artifacts.forEach((artifact) => {
    const key = artifact.logicalId || artifact.artifact_id;
    const group = groups.get(key) || [];
    group.push(artifact);
    groups.set(key, group);
  });
  groups.forEach((group) => group.sort(byNewest));
  return groups;
};

const preferredArtifact = (artifacts: RuntimeArtifact[]): RuntimeArtifact | undefined => (
  artifacts.find((artifact) => artifact.isSelected) || artifacts[0]
);

const findPreferred = (
  groups: Map<string, RuntimeArtifact[]>,
  predicate: (artifact: RuntimeArtifact) => boolean,
): RuntimeArtifact | undefined => {
  const candidates = [...groups.values()]
    .flatMap((group) => preferredArtifact(group) || [])
    .filter(predicate)
    .sort(byNewest);
  return candidates[0];
};

const shotIdFromLogicalId = (logicalId: string | undefined, suffix: RegExp): string | null => {
  const match = logicalId?.match(suffix);
  return match?.[1] || null;
};

/**
 * Project the durable Runtime graph into the data structures consumed by the
 * original Cuti result components. Draft artifacts are intentionally included:
 * a failed build must not hide script or storyboard work already produced.
 */
export const projectRuntimeWorkspaceToCuti = (
  workspace: RuntimeWorkspace,
): CutiWorkspaceProjection => {
  const groups = groupedArtifacts(workspace);
  const specArtifact = findPreferred(groups, (artifact) => artifact.type === "video_spec");
  const effectiveVideoSpec = record(workspace.videoSpec) || record(artifactContent(specArtifact));
  const scriptArtifact = findPreferred(groups, (artifact) => artifact.type === "script");
  const storyboardArtifact = findPreferred(groups, (artifact) => artifact.type === "storyboard");
  const characterDefinitionArtifact = findPreferred(
    groups,
    (artifact) => artifact.type === "characters" || artifact.logicalId === "character:shared:definition",
  );

  const shots = arrayRecords(artifactContent(storyboardArtifact)).length > 0
    ? arrayRecords(artifactContent(storyboardArtifact))
    : arrayRecords(effectiveVideoSpec?.shots);
  const scriptRows = arrayRecords(artifactContent(scriptArtifact));
  const scriptByShot = new Map(scriptRows.map((item) => [String(item.shotId || item.id || ""), item]));
  const characterDefinitions = arrayRecords(artifactContent(characterDefinitionArtifact)).length > 0
    ? arrayRecords(artifactContent(characterDefinitionArtifact))
    : arrayRecords(effectiveVideoSpec?.characters);

  const storyStructure = shots.map((shot, index) => {
    const id = String(shot.id || `shot-${index + 1}`);
    const script = scriptByShot.get(id);
    const beat = String(script?.beat || shot.beat || "");
    const narration = String(script?.narration || shot.narration || "");
    return {
      order: index,
      title: String(shot.title || `Shot ${Number(shot.order || index + 1)}`),
      description: [beat, narration].filter(Boolean).join("\n\n"),
      duration: Number(shot.duration_seconds || 0),
      runtime_shot_id: id,
    };
  });

  const characters = characterDefinitions.map((character, index) => {
    const id = String(character.id || `character-${index + 1}`);
    const characterArtifacts = [...groups.entries()]
      .filter(([logicalId]) => logicalId === `character:${id}:reference`)
      .flatMap(([, artifacts]) => artifacts);
    const versions = [...characterArtifacts]
      .sort((left, right) => left.version - right.version)
      .map((artifact) => ({
        id: artifact.id,
        uuid: artifact.id,
        version_number: artifact.version,
        character_image_url: artifact.uri || "",
        t2i_prompt: artifactPrompt(artifact),
        status: artifact.status,
      }));
    const selectedVersionId = characterArtifacts.find((artifact) => artifact.isSelected)?.id;
    const selectedIndex = Math.max(0, versions.findIndex((version) => version.id === selectedVersionId));
    const selectedVersion = versions[selectedIndex];
    return {
      ...character,
      uuid: id,
      name: String(character.name || id),
      description: String(character.appearance || character.description || ""),
      image_url: selectedVersion?.character_image_url || "",
      versions,
      current_version_index: selectedIndex,
      selected_version_id: selectedVersion?.id,
      runtime_character_id: id,
    };
  });

  const scenes = shots.map((shot, index) => ({
    scene_number: Number(shot.order || index + 1),
    title: String(shot.title || `Shot ${Number(shot.order || index + 1)}`),
    description: String(shot.beat || ""),
    location: String(shot.location || ""),
    narrations: String(shot.narration || "")
      ? [{ narration_text: String(shot.narration), duration: Number(shot.duration_seconds || 0) }]
      : [],
    runtime_shot_id: String(shot.id || `shot-${index + 1}`),
  }));

  const keyframeGroups = [...groups.entries()].flatMap(([logicalId, artifacts]) => {
    const shotId = shotIdFromLogicalId(logicalId, /^shot:(.+):keyframe:(\d+)$/);
    if (!shotId) return [];
    const frameIndex = Number(logicalId.match(/:keyframe:(\d+)$/)?.[1] || 0);
    const shotIndex = Math.max(0, shots.findIndex((shot) => String(shot.id) === shotId));
    const versions = [...artifacts]
      .sort((left, right) => left.version - right.version)
      .map((artifact) => ({
        uuid: artifact.id,
        version_number: artifact.version,
        keyframe_url: artifact.uri || "",
        t2i_prompt: artifactPrompt(artifact),
        frame_index: frameIndex,
        status: artifact.status,
      }));
    const selectedVersionId = artifacts.find((artifact) => artifact.isSelected)?.id;
    const selectedIndex = Math.max(0, versions.findIndex((version) => version.uuid === selectedVersionId));
    return [{
      uuid: `${shotId}:keyframe:${frameIndex}`,
      shot_number: Number(shots[shotIndex]?.order || shotIndex + 1),
      frame_index: frameIndex,
      versions,
      current_version_index: selectedIndex,
    }];
  });

  const videoGroups = [...groups.entries()].flatMap(([logicalId, artifacts]) => {
    const shotId = shotIdFromLogicalId(logicalId, /^shot:(.+):clip$/);
    if (!shotId) return [];
    const shotIndex = Math.max(0, shots.findIndex((shot) => String(shot.id) === shotId));
    const versions = [...artifacts]
      .sort((left, right) => left.version - right.version)
      .map((artifact) => ({
        uuid: artifact.id,
        version_number: artifact.version,
        video_url: artifact.uri || "",
        motion_prompt: artifactPrompt(artifact),
        duration: Number(shots[shotIndex]?.duration_seconds || 0),
        status: artifact.status,
      }));
    const selectedVersionId = artifacts.find((artifact) => artifact.isSelected)?.id;
    const selectedIndex = Math.max(0, versions.findIndex((version) => version.uuid === selectedVersionId));
    return [{
      uuid: `${shotId}:clip`,
      shot_number: Number(shots[shotIndex]?.order || shotIndex + 1),
      versions,
      current_version_index: selectedIndex,
    }];
  });

  const musicGroups = [...groups.entries()]
    .filter(([logicalId]) => logicalId === "audio:bgm")
    .flatMap(([, artifacts]) => artifacts);
  const musicVersions = musicGroups
    .sort((left, right) => left.version - right.version)
    .map((artifact) => ({
      uuid: artifact.id,
      version_number: artifact.version,
      audio_url: artifact.uri || "",
      music_prompt: artifactPrompt(artifact),
      status: artifact.status,
    }));
  const selectedMusicVersionId = musicGroups.find((artifact) => artifact.isSelected)?.id;
  const selectedMusicIndex = Math.max(0, musicVersions.findIndex((version) => version.uuid === selectedMusicVersionId));

  const finalArtifact = findPreferred(
    groups,
    (artifact) => artifact.logicalId === "video:final" || artifact.type === "final_video",
  );

  return {
    effectiveVideoSpec,
    storyOutlineData: effectiveVideoSpec ? {
      title: String(effectiveVideoSpec.title || specArtifact?.title || "Video story"),
      theme: String(effectiveVideoSpec.style_id || ""),
      description: String(effectiveVideoSpec.description || ""),
      style_guide: String(effectiveVideoSpec.style_id || ""),
      structure: storyStructure,
      target_duration_seconds: Number(effectiveVideoSpec.target_duration_seconds || 0),
    } : null,
    analysisData: effectiveVideoSpec ? {
      style_preferences: effectiveVideoSpec.style_id ? [String(effectiveVideoSpec.style_id)] : [],
    } : null,
    charactersData: characters.length > 0 ? { characters } : null,
    scenesData: scenes.length > 0 ? { scenes } : null,
    keyframesData: {
      keyframes: keyframeGroups,
      total: Math.max(keyframeGroups.length, shots.length),
      shot_total: shots.length,
    },
    videosData: {
      video_generations: videoGroups,
      total: Math.max(videoGroups.length, shots.length),
    },
    musicData: musicVersions.length > 0 ? {
      music_generations: [{
        uuid: "audio:bgm",
        versions: musicVersions,
        current_version_index: selectedMusicIndex,
      }],
    } : null,
    videoAssemblyData: finalArtifact?.uri ? {
      uuid: finalArtifact.id,
      final_video_url: finalArtifact.uri,
      success: finalArtifact.status !== "failed",
      total_duration: Number(effectiveVideoSpec?.target_duration_seconds || 0),
    } : null,
  };
};
