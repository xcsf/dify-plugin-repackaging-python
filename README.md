## Dify 插件离线重新打包工具

跨平台直接重新打包体验一般，建议使用 Docker 方式执行。

本项目为以下项目的 Fork，用于方便自行维护与定制：
https://github.com/kurokobo/dify-plugin-offline-packager

---

## 使用方式（Docker Compose）

统一入口命令：

```bash
docker compose run --rm packager <参数> "<值>"
```

---

## 从 Dify Marketplace 生成离线包

参数：`--marketplace`

格式：`author/name:version`

```bash
docker compose run --rm packager --marketplace "langgenius/openai:0.3.2"
```

---

## 从 GitHub Releases 生成离线包

参数：`--github`

格式：`owner/repo:tag:asset.difypkg`

```bash
docker compose run --rm packager --github "junjiem/dify-plugin-agent-mcp_sse:0.2.4:agent-mcp_sse.difypkg"
```

---

## 从本地 .difypkg 文件重新打包

参数：`--local`

将 `.difypkg` 文件放入 `./difypkg/` 目录后执行：

```bash
docker compose run --rm packager --local "difypkg/my-plugin.difypkg"
```

---

## Dify 平台配置（安装离线包时可能需要）

安装离线打包的插件时，可能需要调整 Dify 的 `.env` 配置项：

| 配置项                                 | 建议值      | 说明                       |
| -------------------------------------- | ----------- | -------------------------- |
| `FORCE_VERIFYING_SIGNATURE`            | `false`     | 允许安装未签名插件         |
| `ENFORCE_LANGGENIUS_PLUGIN_SIGNATURES` | `false`     | 允许安装未签名的官方插件包 |
| `PLUGIN_MAX_PACKAGE_SIZE`              | `524288000` | 允许插件包最大 500MB       |
| `NGINX_CLIENT_MAX_BODY_SIZE`           | `500M`      | 提高 Nginx 上传限制        |
