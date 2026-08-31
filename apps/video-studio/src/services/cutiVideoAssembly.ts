import { CUTI_VIDEO_API_BASE_URL } from "@/services/api";

/**
 * 调用 `POST /agent-router/video-editing/video-assembly`（与 LazyShotsSection 一致需 Bearer）。
 */
export async function postVideoAssembly(body: Record<string, unknown>): Promise<any> {
  const currentLang = localStorage.getItem("language") || "en";
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-App-Language": currentLang,
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const response = await fetch(`${CUTI_VIDEO_API_BASE_URL}/agent-router/video-editing/video-assembly`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  return response.json();
}
