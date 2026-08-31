/** 从 /users/me 响应中取出用户 profile 对象 */
export function extractUserProfile(raw: unknown): Record<string, unknown> | null {
  if (!raw || typeof raw !== "object") return null;
  const root = raw as Record<string, unknown>;
  const data = root.data;
  if (data && typeof data === "object") {
    const nested = data as Record<string, unknown>;
    if (nested.user && typeof nested.user === "object") {
      return nested.user as Record<string, unknown>;
    }
    return nested;
  }
  if (root.user && typeof root.user === "object") {
    return root.user as Record<string, unknown>;
  }
  return root;
}

const isTruthyAdminFlag = (value: unknown): boolean =>
  value === true || value === 1 || value === "1" || value === "true";

/**
 * Explore 发布权限：uploadExplorePermision（或常见拼写变体）为 1，或 is_admin 为 true。
 */
export function hasUploadExplorePermission(raw: unknown): boolean {
  const user = extractUserProfile(raw);
  if (!user) return false;
  if (isTruthyAdminFlag(user.is_admin)) return true;
  const perm =
    user.uploadExplorePermision ??
    user.uploadExplorePermission ??
    user.upload_explore_permission;
  return perm === 1 || perm === "1";
}
