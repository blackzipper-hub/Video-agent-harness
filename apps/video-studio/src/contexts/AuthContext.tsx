import React, { createContext, useContext } from "react";

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
  user: User;
  isLoading: boolean;
  checkAuth: () => Promise<void>;
  logout: () => Promise<void>;
  refreshCredits: () => Promise<void>;
};

const localUserId = import.meta.env.VITE_LOCAL_SINGLE_USER_ID || "00000000-0000-0000-0000-000000000001";
const localUser: User = {
  id: localUserId,
  user_id: localUserId,
  email: "local@cuti.dev",
  credits: Number.MAX_SAFE_INTEGER,
  auth_type: "local_single_user",
  status: "active",
};

const noop = async () => undefined;
const value: AuthContextValue = {
  isLoggedIn: true,
  user: localUser,
  isLoading: false,
  checkAuth: noop,
  logout: noop,
  refreshCredits: noop,
};

const AuthContext = createContext<AuthContextValue>(value);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
