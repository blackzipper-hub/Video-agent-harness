FROM node:24-bookworm-slim AS build

RUN corepack enable
WORKDIR /workspace
COPY . .
RUN pnpm install --frozen-lockfile && pnpm --filter @cuti-ai/video-studio run build

FROM nginx:1.27-alpine
COPY apps/video-studio/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/video-studio/dist /usr/share/nginx/html
EXPOSE 80
