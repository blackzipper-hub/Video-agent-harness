# 本地部署

[English](README.md) | 中文

自托管请用仓库根目录的 compose，这是支持的产品路径：

```sh
cp .env.example .env
docker compose --env-file .env -f compose.video.yml up --build -d
```

然后按[根目录 README](../README.md) 启动 DeepSeek Harness。身份是 `local-user`。对象存储默认本地磁盘（`STORAGE_BACKEND=local`）。

| 端口 | 服务 |
|------|------|
| 3000 | Video Studio |
| 8001 | Video Runtime |
| 18080 | Media Service |

`deploy/charts` 和 `services/*/helm` 里的 Helm chart 是可选的运维模板，本地不必用。overlay 里若仍写 S3，只给自带对象存储的人用，不是默认。
