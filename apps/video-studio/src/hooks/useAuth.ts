/**
 * Authentication Hook
 * 从全局 AuthContext 读取登录状态，保证全应用只请求一次 /users/me，避免「未返回就点击导致误跳登录」的时间差。
 */
export type { User } from '@/contexts/AuthContext';
export { useAuth } from '@/contexts/AuthContext';
