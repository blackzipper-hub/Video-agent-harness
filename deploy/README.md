# Local deploy

English | [中文](README.zh.md)

Self-host with the root compose file. That is the supported product path:

```sh
cp .env.example .env
docker compose --env-file .env -f compose.video.yml up --build -d
```

Then start DeepSeek Harness as described in the [root README](../README.md). Identity is `local-user`. Object storage defaults to the local disk (`STORAGE_BACKEND=local`).

| Port | Service |
|------|------|
| 3000 | Video Studio |
| 8001 | Video Runtime |
| 18080 | Media Service |

Helm charts under `deploy/charts` and `services/*/helm` are optional operator templates. They are not required for local use. Overlay values that still mention S3 are for people who bring their own object store; they are not the default.
