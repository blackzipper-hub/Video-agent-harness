# Video Studio

English | [中文](README.zh.md)

Video Studio is the video-native frontend for Cuti Video Agent Harness. The imported Cuti chat, artifact browser, player, timeline, SSE handling, and legacy API clients remain available during migration.

`/create/:threadId` is the primary workspace. It keeps DeepSeek chat on the left and projects the Runtime workspace on the right, including characters, shots, artifact versions, build progress, music replacement, per-shot lipsync, timeline order/duration/narration editing, cost confirmation, and MP4 export. The `/video` route remains an advanced diagnostics surface during migration.

## Development

```sh
pnpm --filter @cuti-ai/video-studio run dev
```

The development server uses `VITE_VIDEO_RUNTIME_URL` for `/api/video`; its default is `http://127.0.0.1:8000` for compatibility with the combined Cuti backend. Set it to `http://127.0.0.1:8001` for the standalone runtime.

## Production build

```sh
pnpm --filter @cuti-ai/video-studio run build
```

The root `compose.video.yml` builds and serves the app at `http://127.0.0.1:3000`. The local image proxies `/api/video` to the standalone runtime. Production identity and chat routing remain deployment-owned integrations.
