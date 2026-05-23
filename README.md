## Dify 插件重新打包工具

### 功能

下载 Dify 插件（.difypkg）并重新打包，包括：
- 修改插件的 `author` 和 `authorized_category` 为 `xcsf`
- 离线下载 Python 依赖（`wheels/`），修改 `requirements.txt` 为离线安装
- 更新 `pyproject.toml` 的 `[tool.uv]` 离线配置
- 规范化 `created_at` 日期格式（ISO 8601）
- 调用 `dify-plugin-<os>-<arch>` 重新打包输出

### 安装

```powershell
pip install requests pyyaml
```

### 前置条件

- `dify-plugin-<os>-<arch>` 可执行文件需放在脚本同目录（如 `dify-plugin-windows-amd64`）

### 基本用法

```powershell
python plugin_repackaging.py [-p 平台] [-s 后缀] [-e 额外包] [-o 替换包] <来源> <参数>
```

| 参数 | 说明 | 示例 |
|------|------|------|
| `-p, --platform` | pip 交叉打包目标平台 | `manylinux2014_x86_64` |
| `-s, --suffix` | 输出文件名后缀，默认 `offline` | `linux-amd64` |
| `-e, --extra` | 额外下载并打包的包，可重复或逗号分隔 | `-e "setuptools==80.9.0"` |
| `-o, --override` | 替换 requirements.txt 中不存在的包版本 | `-o "greenlet==3.2.5"` |

### 来源类型

| 来源 | 格式 | 示例 |
|------|------|------|
| `market` | `market [author] [name] [version]` | `python plugin_repackaging.py market langgenius email 0.0.14` |
| `github` | `github [repo] [release] [asset_name]` | `python plugin_repackaging.py github user/repo v1.0.0 plugin.difypkg` |
| `local` | `local [difypkg路径]` | `python plugin_repackaging.py local .\langgenius-email_0.0.14.difypkg` |

### 常用示例

**本地重新打包**（Windows -> Linux）：
```powershell
python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 local .\langgenius-email_0.0.14.difypkg
```

**替换不存在版本的包**（如 `greenlet==3.3.0` 不存在，改用 `3.2.5`）：
```powershell
python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 -o "greenlet==3.2.5" local .\langgenius-email_0.0.14.difypkg
```

**额外打包 dev 工具依赖**：
```powershell
python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 -e "ruff>=0.12.5" -e "pytest>=8.4.1" -e "setuptools==80.9.0" -e "black" local .\langgenius-openai_api_compatible_0.0.45.difypkg
```

### 注意事项

- **PowerShell 路径**：使用 `.\\文件名` 或绝对路径，避免 `.\` 被 PowerShell 吞掉反斜杠
- **sdist 自动回退**：当 pip 找不到指定平台的预编译 wheel 时，会自动下载源码包并构建 wheel。最多尝试 3 个包，超过请检查包版本或使用 `-o` 替换
- **pip 下载失败**：设置 `PIP_MIRROR_URL` 环境变量切换镜像源，默认使用阿里云镜像
- **插件安装限制**：在 Dify `.env` 中设置 `FORCE_VERIFYING_SIGNATURE=false` 允许安装未审核插件，`PLUGIN_MAX_PACKAGE_SIZE=524288000` 允许最大 500M
