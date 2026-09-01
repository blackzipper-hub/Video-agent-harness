/**
 * 物理时间仅预演用（与阶段 3 方案一致；成片以 assemble 输出为准）。
 */
export type ShotClipPlacement = {
  startSec: number;
  endSec: number;
  shotNumber: number;
  durationSec: number;
  /** 当前选版 keyframe，用于时间线缩略/胶片条背景 */
  posterUrl?: string;
  /** 当前选版成片 MP4，用于无 assemble 时串联预览 */
  videoUrl?: string;
};

export type GetShotClipPlacementsForPreviewOpts = {
  /** 用户目标成片时长（秒）：无镜头成片时把未生成段补到该总长，便于时间线黑屏占位 */
  masterTargetDurationSec?: number;
  /** 镜号 → 分镜/剧本自然时长（秒）；仅当版本未带 duration 或尚无片行时填入，便于与故事大纲总长对齐 */
  shotNaturalDurationSecByNumber?: Map<number, number>;
  /** Product Launch / narration_driven：优先用旁白 TTS 时长，而非视频生成档位时长 */
  preferNarrationDrivenDurations?: boolean;
};

type KeyframeRowLite = {
  uuid?: string;
  shot_number?: number;
  current_version_index?: number;
  versions?: Array<{ uuid?: string; duration?: number; keyframe_url?: string }>;
};

/** 优先 total_duration / total_duration_sec，否则用章节 duration 之和（与 DB 故事大纲一致） */
export function getMasterDurationSecFromStoryOutline(storyOutlineData: unknown): number | undefined {
  if (!storyOutlineData || typeof storyOutlineData !== "object") {
    return undefined;
  }
  const o = storyOutlineData as Record<string, unknown>;
  const top = Number(o.total_duration ?? o.total_duration_sec);
  if (Number.isFinite(top) && top > 0.05) {
    return top;
  }
  const chapters = Array.isArray(o.structure)
    ? (o.structure as unknown[])
    : Array.isArray(o.chapters)
      ? (o.chapters as unknown[])
      : [];
  let sum = 0;
  for (const ch of chapters) {
    if (!ch || typeof ch !== "object") {
      continue;
    }
    const d = Number((ch as Record<string, unknown>).duration);
    if (Number.isFinite(d) && d > 0) {
      sum += d;
    }
  }
  return sum > 0.05 ? sum : undefined;
}

/** 从关键帧当前选版读取每镜 duration，供时间线在尚无 MP4 时按镜铺开 */
export function buildShotNaturalDurationSecByKeyframeRows(
  keyframesData: { keyframes?: KeyframeRowLite[] } | null | undefined,
  selectedVersionByKeyframeUuid: Map<string, string>
): Map<number, number> {
  const out = new Map<number, number>();
  const list = keyframesData?.keyframes;
  if (!Array.isArray(list)) {
    return out;
  }
  for (const kf of list) {
    const sn = Number(kf?.shot_number);
    if (!Number.isFinite(sn) || sn < 1) {
      continue;
    }
    const vers = kf.versions || [];
    const kid = kf.uuid != null ? String(kf.uuid) : "";
    const sel = kid ? selectedVersionByKeyframeUuid.get(kid) : undefined;
    const version = sel
      ? vers.find((x) => String(x?.uuid) === String(sel))
      : vers[kf.current_version_index || 0] || vers[0];
    const dRaw = version?.duration != null ? Math.max(0, Number(version.duration) || 0) : 0;
    if (dRaw > 0.05) {
      out.set(sn, dRaw);
    }
  }
  return out;
}

/** 从场景内旁白（含 TTS duration）提取每镜时长，供 narration_driven 时间线 */
export function buildShotDurationSecBySceneNarrations(
  scenesData: { scenes?: Array<{ narrations?: Array<{ shot_number?: number; duration?: number }> }> } | null | undefined,
): Map<number, number> {
  const out = new Map<number, number>();
  const scenes = scenesData?.scenes;
  if (!Array.isArray(scenes)) {
    return out;
  }
  for (const scene of scenes) {
    for (const item of scene.narrations ?? []) {
      const sn = Number(item?.shot_number);
      const d = Number(item?.duration);
      if (Number.isFinite(sn) && sn >= 1 && Number.isFinite(d) && d > 0.05) {
        out.set(sn, d);
      }
    }
  }
  return out;
}

/** 将分镜条总时长对齐到成片/播放器实测时长，避免上下时间轴不一致 */
export function scalePlacementsToTargetTotal(
  placements: ShotClipPlacement[],
  targetSec: number,
): ShotClipPlacement[] {
  if (!placements.length || !Number.isFinite(targetSec) || targetSec <= 0.05) {
    return placements;
  }
  const sum = placements.reduce((s, p) => s + Math.max(0, p.durationSec), 0);
  if (sum <= 0.05) {
    return placements;
  }
  const ratio = Math.abs(sum - targetSec) > 0.08 ? targetSec / sum : 1;
  let cumulative = 0;
  return placements.map((p) => {
    const d = Math.max(0, p.durationSec) * ratio;
    const out: ShotClipPlacement = {
      ...p,
      startSec: cumulative,
      endSec: cumulative + d,
      durationSec: d,
    };
    cumulative += d;
    return out;
  });
}

export function getShotClipPlacementsForPreview(
  videosData: {
    total?: number;
    video_generations?: Array<{
      uuid: string;
      shot_number?: number;
      current_version_index?: number;
      versions?: Array<{ uuid: string; duration?: number; keyframe_url?: string; video_url?: string }>;
    }>;
  } | null | undefined,
  selectedVersionByVideoId: Map<string, string>,
  opts?: GetShotClipPlacementsForPreviewOpts
): ShotClipPlacement[] {
  if (!videosData) {
    return [];
  }
  const vgens = videosData.video_generations || [];
  const nFromTotal = Number.isFinite(videosData.total) ? Math.max(0, Number(videosData.total)) : 0;
  const totalSlots = nFromTotal > 0 ? nFromTotal : vgens.length;
  if (totalSlots < 1) {
    return [];
  }
  const byShot = new Map<
    number,
    {
      uuid: string;
      shot_number?: number;
      current_version_index?: number;
      versions?: Array<{ uuid: string; duration?: number }>;
    }
  >();
  for (const v of vgens) {
    const sn = v.shot_number;
    if (Number.isFinite(sn) && !byShot.has(Number(sn))) {
      byShot.set(Number(sn), v);
    }
  }
  type SlotDraft = {
    shotNumber: number;
    naturalDur: number;
    posterUrl?: string;
    videoUrl?: string;
  };
  const drafts: SlotDraft[] = [];
  for (let i = 1; i <= totalSlots; i++) {
    const video = byShot.get(i);
    if (!video) {
      let nd = 0;
      const hint0 = opts?.shotNaturalDurationSecByNumber?.get(i);
      if (hint0 != null && Number.isFinite(hint0) && hint0 > 0.05) {
        nd = hint0;
      }
      drafts.push({ shotNumber: i, naturalDur: nd });
      continue;
    }
    const vers = video.versions || [];
    const sel = selectedVersionByVideoId.get(video.uuid);
    const version = sel
      ? vers.find((x) => x.uuid === sel)
      : vers[video.current_version_index || 0] || vers[0];
    const dRaw = version?.duration != null ? Math.max(0, Number(version.duration) || 0) : 0;
    const kf = (version as { keyframe_url?: string } | undefined)?.keyframe_url;
    const posterUrl = typeof kf === "string" && kf ? kf : undefined;
    const vUrl = (version as { video_url?: string } | undefined)?.video_url;
    const videoUrl = typeof vUrl === "string" && vUrl ? vUrl : undefined;
    let naturalDur = dRaw > 0.05 ? dRaw : 0;
    const hint = opts?.shotNaturalDurationSecByNumber?.get(i);
    if (opts?.preferNarrationDrivenDurations && hint != null && Number.isFinite(hint) && hint > 0.05) {
      naturalDur = hint;
    } else if (naturalDur <= 0.05 && hint != null && Number.isFinite(hint) && hint > 0.05) {
      naturalDur = hint;
    }
    drafts.push({ shotNumber: i, naturalDur, posterUrl, videoUrl });
  }
  return buildPlacementsFromDrafts(drafts, opts);
}

function buildPlacementsFromDrafts(
  drafts: Array<{ shotNumber: number; naturalDur: number; posterUrl?: string; videoUrl?: string }>,
  opts?: GetShotClipPlacementsForPreviewOpts,
): ShotClipPlacement[] {
  const master = opts?.masterTargetDurationSec;
  /** 无目标总时长接口参数时：仅有首帧、尚无成片 mp4 的镜头给短时占位，便于预演区展示关键帧 */
  const keyframeOnlyHoldSec =
    master != null && Number.isFinite(master) && master > 0.05 ? 0 : 2;
  if (keyframeOnlyHoldSec > 0) {
    for (const s of drafts) {
      if (s.naturalDur <= 0.05 && s.posterUrl && !s.videoUrl) {
        s.naturalDur = keyframeOnlyHoldSec;
      }
    }
  }
  const sumNatural = drafts.reduce((s, x) => s + (x.naturalDur > 0.05 ? x.naturalDur : 0), 0);
  const nNeedsPad = drafts.filter((x) => x.naturalDur <= 0.05).length;
  let padEach = 0;
  if (
    master != null &&
    Number.isFinite(master) &&
    master > 0.05 &&
    nNeedsPad > 0 &&
    sumNatural + 1e-3 < master
  ) {
    padEach = (master - sumNatural) / nNeedsPad;
  }
  let cumulative = 0;
  const out: ShotClipPlacement[] = [];
  for (const slot of drafts) {
    const d = slot.naturalDur > 0.05 ? slot.naturalDur : padEach;
    const start = cumulative;
    const end = cumulative + d;
    out.push({
      startSec: start,
      endSec: end,
      shotNumber: slot.shotNumber,
      durationSec: d,
      posterUrl: slot.posterUrl,
      videoUrl: slot.videoUrl,
    });
    cumulative = end;
  }
  return out;
}

export function getPreviewTimelineEndSec(placement: ShotClipPlacement[]): number {
  if (!placement.length) {
    return 1;
  }
  const last = placement[placement.length - 1]!.endSec;
  return last > 0.05 ? last : 1;
}

/**
 * 尚无 video_generations 时：按镜号用关键帧当前选版的 keyframe_url 作为「片段首帧」串联预演，
 * 时长规则与 getShotClipPlacementsForPreview 一致（无 master 时每格约 2s 有图占位）。
 */
export function getKeyframePosterPlacementsForPreview(
  keyframesData: {
    keyframes?: KeyframeRowLite[];
    total?: number;
    shot_total?: number;
  } | null | undefined,
  selectedVersionByKeyframeUuid: Map<string, string>,
  opts?: GetShotClipPlacementsForPreviewOpts
): ShotClipPlacement[] {
  if (!keyframesData) {
    return [];
  }
  const keyframes = keyframesData.keyframes || [];
  const nFromTotal = Number.isFinite(keyframesData.total) ? Math.max(0, Number(keyframesData.total)) : 0;
  const nFromShotTotal =
    Number.isFinite((keyframesData as { shot_total?: number }).shot_total) &&
    Number((keyframesData as { shot_total?: number }).shot_total) > 0
      ? Math.max(0, Number((keyframesData as { shot_total?: number }).shot_total))
      : 0;
  let maxFromRows = 0;
  for (const k of keyframes) {
    const sn = Number(k?.shot_number);
    if (Number.isFinite(sn) && sn > maxFromRows) {
      maxFromRows = sn;
    }
  }
  const totalSlots = Math.max(nFromTotal, nFromShotTotal, maxFromRows, keyframes.length);
  if (totalSlots < 1) {
    return [];
  }
  const byShot = new Map<number, KeyframeRowLite>();
  for (const k of keyframes) {
    const sn = k?.shot_number;
    if (Number.isFinite(sn) && !byShot.has(Number(sn))) {
      byShot.set(Number(sn), k);
    }
  }
  type SlotDraft = {
    shotNumber: number;
    naturalDur: number;
    posterUrl?: string;
    videoUrl?: string;
  };
  const drafts: SlotDraft[] = [];
  for (let i = 1; i <= totalSlots; i++) {
    const kf = byShot.get(i);
    if (!kf) {
      let nd = 0;
      const hint0 = opts?.shotNaturalDurationSecByNumber?.get(i);
      if (hint0 != null && Number.isFinite(hint0) && hint0 > 0.05) {
        nd = hint0;
      }
      drafts.push({ shotNumber: i, naturalDur: nd, posterUrl: undefined, videoUrl: undefined });
      continue;
    }
    const vers = kf.versions || [];
    const kid = kf.uuid != null ? String(kf.uuid) : "";
    const sel = kid ? selectedVersionByKeyframeUuid.get(kid) : undefined;
    const version = sel
      ? vers.find((x) => String(x?.uuid) === String(sel))
      : vers[kf.current_version_index || 0] || vers[0];
    const dRaw = version?.duration != null ? Math.max(0, Number(version.duration) || 0) : 0;
    const kUrl = version?.keyframe_url;
    const posterUrl = typeof kUrl === "string" && kUrl ? kUrl : undefined;
    let naturalDur = dRaw > 0.05 ? dRaw : 0;
    const hint = opts?.shotNaturalDurationSecByNumber?.get(i);
    if (opts?.preferNarrationDrivenDurations && hint != null && Number.isFinite(hint) && hint > 0.05) {
      naturalDur = hint;
    } else if (naturalDur <= 0.05 && hint != null && Number.isFinite(hint) && hint > 0.05) {
      naturalDur = hint;
    }
    drafts.push({ shotNumber: i, naturalDur, posterUrl, videoUrl: undefined });
  }
  return buildPlacementsFromDrafts(drafts, opts);
}
