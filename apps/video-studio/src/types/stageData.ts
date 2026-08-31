/** By-thread 阶段数据（与 video_analysis POST /keyframes-by-thread、/video-generations-by-thread 对齐的常用字段） */

export type KeyframesByThreadPayload = {
  keyframes: unknown[];
  /** 预期镜数/记录数，列表可为空时仍有意义 */
  shot_total?: number;
  total?: number;
};

export type VideoGenerationsByThreadPayload = {
  video_generations: unknown[];
  total?: number;
};
