/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LOCAL_SINGLE_USER_MODE?: string
  readonly VITE_LOCAL_SINGLE_USER_ID?: string
  readonly VITE_API_BASE_URL?: string
  readonly VITE_BACKEND_URL?: string
  readonly VITE_CUTI_BACKEND_URL?: string
  readonly VITE_VIDEOCHAT_URL?: string
  readonly VITE_VIDEO_RUNTIME_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
