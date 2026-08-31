/** 与后端 KeyframeListResponse 对齐：首尾帧模式每镜两条根记录（frame_index 0 / -1），勿按 shot_number 去重丢尾帧。 */

export function getKeyframeFrameIndex(keyframe: any): number {
  const versions = keyframe?.versions;
  if (Array.isArray(versions) && versions.length > 0) {
    const fi = versions[0]?.frame_index;
    if (fi != null && Number.isFinite(Number(fi))) {
      return Number(fi);
    }
  }
  const rootFi = keyframe?.frame_index;
  if (rootFi != null && Number.isFinite(Number(rootFi))) {
    return Number(rootFi);
  }
  return 0;
}

function frameIndexDisplayRank(frameIndex: number): number {
  if (frameIndex === 0) return 0;
  if (frameIndex === -1) return 2;
  return 1;
}

export function sortKeyframesForDisplay(keyframes: any[]): any[] {
  return [...keyframes].sort((a, b) => {
    const shotA = Number(a?.shot_number ?? 0);
    const shotB = Number(b?.shot_number ?? 0);
    if (shotA !== shotB) return shotA - shotB;
    const fa = getKeyframeFrameIndex(a);
    const fb = getKeyframeFrameIndex(b);
    const ra = frameIndexDisplayRank(fa);
    const rb = frameIndexDisplayRank(fb);
    if (ra !== rb) return ra - rb;
    return fa - fb;
  });
}

/** 与后端 _infer_continuity_from_keyframe_response_counts 一致：同一镜多条根记录或已有尾帧才算首尾帧模式。 */
export function inferContinuityFromKeyframeRecords(keyframes: any[]): boolean {
  if (!keyframes?.length) return false;
  if (keyframes.some((kf) => getKeyframeFrameIndex(kf) === -1)) return true;
  const perShot = new Map<number, number>();
  for (const kf of keyframes) {
    const sn = Number(kf?.shot_number ?? 0);
    if (sn < 1) continue;
    perShot.set(sn, (perShot.get(sn) ?? 0) + 1);
  }
  return Array.from(perShot.values()).some((c) => c >= 2);
}

export type KeyframeDisplayMetrics = {
  displayKeyframes: any[];
  shotSlotTotal: number;
  /** 分镜 Tab 计数分母：仅在实际首尾帧数据存在时用 API total(2×镜数)，否则用镜数 */
  expectedRecordTotal: number;
  usesContinuityLayout: boolean;
};

export function resolveKeyframeDisplayMetrics(
  keyframesData: any,
  totalCount?: number,
): KeyframeDisplayMetrics {
  const keyframes = keyframesData?.keyframes || [];
  const shotSlotTotal =
    Number.isFinite(keyframesData?.shot_total) && keyframesData.shot_total > 0
      ? keyframesData.shot_total
      : Number.isFinite(totalCount) && (totalCount as number) > 0
        ? (totalCount as number)
        : 0;
  const apiExpectedTotal =
    Number.isFinite(keyframesData?.total) && keyframesData.total > 0
      ? keyframesData.total
      : shotSlotTotal > 0
        ? shotSlotTotal
        : keyframes.length;
  const sorted = sortKeyframesForDisplay(keyframes);
  const usesContinuityLayout =
    shotSlotTotal > 0 && inferContinuityFromKeyframeRecords(sorted);
  const expectedRecordTotal = usesContinuityLayout
    ? apiExpectedTotal > shotSlotTotal
      ? apiExpectedTotal
      : shotSlotTotal * 2
    : shotSlotTotal > 0
      ? shotSlotTotal
      : keyframes.length;

  return {
    displayKeyframes: buildDisplayKeyframesFromSorted(sorted, shotSlotTotal, usesContinuityLayout),
    shotSlotTotal,
    expectedRecordTotal,
    usesContinuityLayout,
  };
}

function buildDisplayKeyframesFromSorted(
  sorted: any[],
  shotSlotTotal: number,
  continuityMode: boolean,
): any[] {
  if (continuityMode) {
    const bySlot = new Map<string, any>();
    for (const kf of sorted) {
      const sn = Number(kf?.shot_number ?? 0);
      if (sn < 1) continue;
      const fi = getKeyframeFrameIndex(kf);
      bySlot.set(`${sn}:${fi}`, kf);
    }
    const slots: any[] = [];
    for (let sn = 1; sn <= shotSlotTotal; sn++) {
      for (const fi of [0, -1] as const) {
        slots.push(
          bySlot.get(`${sn}:${fi}`) ?? {
            shot_number: sn,
            frame_index: fi,
            versions: [],
          },
        );
      }
    }
    return slots;
  }

  if (shotSlotTotal > 0) {
    const byShot = new Map<number, any>();
    for (const kf of sorted) {
      const sn = Number(kf?.shot_number ?? 0);
      if (sn > 0 && !byShot.has(sn)) {
        byShot.set(sn, kf);
      }
    }
    return Array.from({ length: shotSlotTotal }, (_, index) => {
      const sn = index + 1;
      return (
        byShot.get(sn) ?? {
          shot_number: sn,
          frame_index: 0,
          versions: [],
        }
      );
    });
  }

  return sorted;
}

/** @deprecated 请用 resolveKeyframeDisplayMetrics */
export function buildDisplayKeyframesFromApi(
  keyframesData: any,
  totalCount?: number,
): any[] {
  return resolveKeyframeDisplayMetrics(keyframesData, totalCount).displayKeyframes;
}

export function keyframeDisplayReactKey(keyframe: any, fallbackIndex: number): string {
  if (keyframe?.uuid) return String(keyframe.uuid);
  const sn = keyframe?.shot_number ?? fallbackIndex + 1;
  return `shot-${sn}-frame-${getKeyframeFrameIndex(keyframe)}`;
}
