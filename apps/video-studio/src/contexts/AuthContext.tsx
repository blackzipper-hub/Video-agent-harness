/**
 * 全局认证上下文
 * - 应用启动时只请求一次 /users/me，所有页面共享同一份登录状态，避免「用户信息还没返回就点其他导致误跳登录」的时间差问题
 */
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from '@/services/api';

export interface User {
  id?: number | string;
  user_id?: string;
  email?: string | null;
  phone?: string | null;
  credits?: number;
  auth_type?: string;
  status?: string;
  is_admin?: boolean;
  created_at?: string;
  updated_at?: string;
}

type AuthContextValue = {
  isLoggedIn: boolean;
  user: User | null;
  isLoading: boolean;
  checkAuth: () => Promise<void>;
  logout: () => Promise<void>;
  /** 仅拉取最新积分并更新 user.credits，不触发展示 loading，用于任务结束/切回标签页后刷新 */
  refreshCredits: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const RUNTIME_USER_ID_KEY = 'cuti_runtime_user_id';
const usersMeUrl = `${import.meta.env.VITE_API_BASE_URL || '/api'}/users/me`;
const localSingleUserMode = import.meta.env.VITE_LOCAL_SINGLE_USER_MODE === 'true';
const localSingleUser: User = {
  id: import.meta.env.VITE_LOCAL_SINGLE_USER_ID || '00000000-0000-0000-0000-000000000001',
  user_id: import.meta.env.VITE_LOCAL_SINGLE_USER_ID || '00000000-0000-0000-0000-000000000001',
  email: 'local@cuti.dev',
  credits: Number.MAX_SAFE_INTEGER,
  auth_type: 'local_single_user',
  status: 'active',
};

function persistRuntimeUserId(user: User | null) {
  if (typeof localStorage === 'undefined') return;
  const id = user ? String(user.user_id || user.id || '').trim() : '';
  if (id) localStorage.setItem(RUNTIME_USER_ID_KEY, id);
  else localStorage.removeItem(RUNTIME_USER_ID_KEY);
}

async function fetchUserMe(): Promise<{ code: number; data?: User }> {
  const currentLang = localStorage.getItem('language') || 'en';
  const response = await fetch(usersMeUrl, {
    method: 'GET',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-App-Language': currentLang,
    },
  });
  return response.json();
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isLoggedIn, setIsLoggedIn] = useState(localSingleUserMode);
  const [user, setUser] = useState<User | null>(localSingleUserMode ? localSingleUser : null);
  const [isLoading, setIsLoading] = useState(!localSingleUserMode);

  const checkAuth = useCallback(async () => {
    if (localSingleUserMode) {
      persistRuntimeUserId(localSingleUser);
      setUser(localSingleUser);
      setIsLoggedIn(true);
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const result = await fetchUserMe();
      if (result.code === 0 && result.data) {
        persistRuntimeUserId(result.data);
        setUser(result.data);
        setIsLoggedIn(true);
      } else {
        persistRuntimeUserId(null);
        setUser(null);
        setIsLoggedIn(false);
      }
    } catch (e) {
      console.error('Authentication check failed:', e);
      persistRuntimeUserId(null);
      setUser(null);
      setIsLoggedIn(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuth().catch((e) => {
      console.error('Auth check failed:', e);
      setUser(null);
      setIsLoggedIn(false);
    });
  }, [checkAuth]);

  const logout = useCallback(async () => {
    if (localSingleUserMode) {
      setUser(localSingleUser);
      setIsLoggedIn(true);
      return;
    }
    try {
      await api.auth.logout();
      persistRuntimeUserId(null);
      setUser(null);
      setIsLoggedIn(false);
    } catch (e) {
      console.error('Logout failed:', e);
      throw e;
    }
  }, []);

  const refreshCredits = useCallback(async () => {
    if (!user) return;
    try {
      const res = await api.user.getUserCredits();
      if (res.code === 0 && res.data != null && typeof (res.data as any).balance === 'number') {
        setUser((prev) => (prev ? { ...prev, credits: (res.data as any).balance } : null));
      }
    } catch (e) {
      console.error('Refresh credits failed:', e);
    }
  }, [user]);

  const value: AuthContextValue = {
    isLoggedIn,
    user,
    isLoading,
    checkAuth,
    logout,
    refreshCredits,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
