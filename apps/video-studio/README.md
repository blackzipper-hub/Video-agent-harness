# Video Studio

English | [中文](README.zh.md)

Video Studio is the video-native frontend for Cuti Video Harness. The imported Cuti chat, artifact browser, player, timeline, SSE handling, and legacy API clients remain available during migration.

`/create/:threadId` is the primary workspace. It keeps DeepSeek chat on the left and projects the Runtime workspace on the right, including characters, shots, artifact versions, build progress, music replacement, per-shot lipsync, timeline order/duration/narration editing, cost confirmation, and MP4 export. The `/video` route remains an advanced diagnostics surface during migration.

## Development

The open-source Studio requires no account or login configuration. Create opens immediately without authentication requests or a login redirect; Video Runtime supplies the local project identity. Backend service tokens and capability grants are still enforced where configured. This single-user UI is not a public multi-user access-control system: protect exposed deployments with an authenticated gateway.

Markdown documents support GFM tables with keyboard-accessible horizontal scrolling and chapter navigation. Runtime execution labels remain in progress views rather than appearing as duplicate script results.

Create renders generated text and scripts as readable Markdown, and complete JSON documents as labeled fields and numbered content cards. Long lists expand on demand; raw JSON remains available in collapsed details. Model-provided HTML is not executed. The shared `ArtifactDocument` renderer preserves unknown fields and supports nested structures without requiring Workflow-specific views.

Frontend type checking includes nullable values and unchecked array indices. Stored pinned-conversation records are validated before display; malformed entries are ignored. Asynchronous UI actions report uncaught failures, and unavailable Canvas contexts or PNG encoding failures reject image export. Request headers accept objects, tuple arrays, and `Headers` instances with per-request overrides.

```sh
pnpm --filter @cuti-ai/video-studio run dev
```

The development server uses `VITE_VIDEO_RUNTIME_URL` for `/api/video`; its default is `http://127.0.0.1:8000` for compatibility with the combined Cuti backend. Set it to `http://127.0.0.1:8001` for the standalone runtime.

## Production build

```sh
pnpm --filter @cuti-ai/video-studio run build
```

`deploy/compose.video.yml` builds and serves the app at `http://127.0.0.1:3000`. The local image proxies `/api/video` to the standalone runtime. Production identity and chat routing remain deployment-owned integrations.
