const MEDIA_CDN_ORIGIN = String(
  import.meta.env.VITE_MEDIA_CDN_ORIGIN || "https://cdn-dev.newai.land"
).replace(/\/$/, "");

/**
 * 将后端返回的媒体地址规范为浏览器可加载的绝对 URL。
 * 线上 publish 可能返回 `cuti.land/creations/...`（无协议），实际文件在 CDN。
 */
export function resolvePublicMediaUrl(url: string | null | undefined): string {
  const raw = String(url ?? "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw)) return raw;
  if (raw.startsWith("//")) return `https:${raw}`;

  const cutiLandPath = raw.replace(/^cuti\.land\//i, "").replace(/^www\.cuti\.land\//i, "");
  if (cutiLandPath !== raw) {
    return `${MEDIA_CDN_ORIGIN}/${cutiLandPath.replace(/^\//, "")}`;
  }

  if (/^[a-z0-9.-]+\.[a-z]{2,}\//i.test(raw)) {
    return `https://${raw}`;
  }

  if (raw.startsWith("/")) return `${MEDIA_CDN_ORIGIN}${raw}`;
  return `${MEDIA_CDN_ORIGIN}/${raw.replace(/^\//, "")}`;
}
