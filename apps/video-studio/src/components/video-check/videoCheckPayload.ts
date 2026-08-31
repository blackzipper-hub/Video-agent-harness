export const VIDEO_CHECK_STORAGE_KEY = "cuti_video_check_payload";
export const VIDEO_CHECK_ROUTE_MODE_KEY = "cuti_video_check_route_mode";

export type VideoCheckSectionParam = "overview" | "storyboard" | "shots" | "final";
export type VideoCheckRouteMode = "normal" | "canvas";

export function getVideoCheckPayload(): any | null {
  const raw = sessionStorage.getItem(VIDEO_CHECK_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setVideoCheckPayload(payload: any): void {
  sessionStorage.setItem(VIDEO_CHECK_STORAGE_KEY, JSON.stringify(payload));
}

export function getVideoCheckRouteMode(): VideoCheckRouteMode {
  const raw = sessionStorage.getItem(VIDEO_CHECK_ROUTE_MODE_KEY);
  return raw === "canvas" ? "canvas" : "normal";
}

export function setVideoCheckRouteMode(mode: VideoCheckRouteMode): void {
  sessionStorage.setItem(VIDEO_CHECK_ROUTE_MODE_KEY, mode);
}


