import { creationsApi, exploreApi } from "@/services/api";
import { resolvePublicMediaUrl } from "@/utils/resolvePublicMediaUrl";

export type PublishedCreationItem = {
  uuid: string;
  title: string;
  description: string;
  videoUrl: string;
  coverUrl: string;
  publishedAt: string;
};

const extractRawPublishedItems = (data: unknown): unknown[] => {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  if (typeof data !== "object") return [];
  const record = data as Record<string, unknown>;
  if (Array.isArray(record.items)) return record.items;
  if (Array.isArray(record.creations)) return record.creations;
  if (Array.isArray(record.list)) return record.list;
  if (record.data) return extractRawPublishedItems(record.data);
  return [];
};

export const mapRawPublishedItem = (item: Record<string, unknown>): PublishedCreationItem | null => {
  const uuid = String(item.uuid ?? item.id ?? "").trim();
  if (!uuid) return null;

  const videoRaw =
    item.video_url ??
    item.videoUrl ??
    item.video ??
    item.playback_url ??
    item.media_url ??
    "";
  const coverRaw =
    item.cover_url ??
    item.coverUrl ??
    item.cover ??
    item.preview_url ??
    item.thumbnail_url ??
    "";

  const videoUrl = resolvePublicMediaUrl(String(videoRaw || ""));
  const coverUrl = resolvePublicMediaUrl(String(coverRaw || ""));

  if (!videoUrl && !coverUrl) return null;

  return {
    uuid,
    title: String(item.title ?? "").trim() || "Untitled",
    description: String(item.description ?? "").trim(),
    videoUrl,
    coverUrl,
    publishedAt: String(
      item.published_at ?? item.publishedAt ?? item.created_at ?? item.createdAt ?? ""
    ).trim(),
  };
};

const mapRawPublishedList = (rawItems: unknown[]): PublishedCreationItem[] => {
  const seen = new Set<string>();
  const result: PublishedCreationItem[] = [];
  for (const raw of rawItems) {
    if (!raw || typeof raw !== "object") continue;
    const mapped = mapRawPublishedItem(raw as Record<string, unknown>);
    if (!mapped || seen.has(mapped.uuid)) continue;
    seen.add(mapped.uuid);
    result.push(mapped);
  }
  return result;
};

export type PublishedCreationsOwner = {
  user_id?: string | null;
  id?: string | number | null;
  email?: string | null;
};

const getCreatorMatchIds = (owner: PublishedCreationsOwner | null | undefined): Set<string> => {
  const ids = new Set<string>();
  const userId = String(owner?.user_id ?? "").trim();
  const numericId = owner?.id != null ? String(owner.id).trim() : "";
  const email = String(owner?.email ?? "").trim();
  if (userId) ids.add(userId);
  if (numericId) ids.add(numericId);
  if (email) ids.add(email);
  return ids;
};

const fetchPublishedFromExploreFeed = async (
  owner: PublishedCreationsOwner | null | undefined
): Promise<PublishedCreationItem[]> => {
  const matchIds = getCreatorMatchIds(owner);
  if (matchIds.size === 0) return [];

  const collected: PublishedCreationItem[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  let page = 0;

  while (page < 15) {
    const response = await exploreApi.getFeed(cursor);
    const feed = response.data;
    if (!feed?.items?.length) break;

    for (const item of feed.items) {
      const ownerId = String(item.creator?.user_id ?? "").trim();
      if (!matchIds.has(ownerId)) continue;
      const mapped = mapRawPublishedItem(item as unknown as Record<string, unknown>);
      if (!mapped || seen.has(mapped.uuid)) continue;
      seen.add(mapped.uuid);
      collected.push(mapped);
    }

    if (!feed.has_more || !feed.next_cursor) break;
    cursor = feed.next_cursor;
    page += 1;
  }

  return collected;
};

/**
 * 加载当前用户已发布作品：优先 /users/me/creations，若为空则从 explore/feed 按 creator 过滤兜底。
 */
export const loadMyPublishedCreations = async (
  owner: PublishedCreationsOwner | null | undefined
): Promise<PublishedCreationItem[]> => {
  let fromApi: PublishedCreationItem[] = [];
  try {
    const publishedRes = await creationsApi.getMyPublished();
    if (publishedRes.code === 0 && publishedRes.data) {
      fromApi = mapRawPublishedList(extractRawPublishedItems(publishedRes.data));
    }
  } catch {
    fromApi = [];
  }

  if (fromApi.length > 0) return fromApi;

  try {
    return await fetchPublishedFromExploreFeed(owner);
  } catch {
    return [];
  }
};
