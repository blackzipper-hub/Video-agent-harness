/**
 * 编辑/时间线 v1：选版 + 预留 trim（出片仍走单条 video-assembly；trim 在阶段 4+ 与后端对齐）。
 */
export type UserOptionShape = Record<string, unknown> | null | undefined;

export type VideoEditClipRefShots = {
  kind: "shot";
  videoGenerationUuid: string;
  versionUuid: string;
  /** 相对源片，秒；未实现时勿传 */
  trimStartSec?: number;
  trimEndSec?: number;
};

export type VideoEditProjectV1 = {
  threadId: string;
  mode: "shots";
  clips: VideoEditClipRefShots[];
};
