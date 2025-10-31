## Dify 插件重新打包工具 (plugin_repackaging.py)

简体中文说明文档。

### 简介

`plugin_repackaging.py` 是一个用于下载并重新打包 Dify 插件（.difypkg）的脚本。它支持从 Dify 市场、GitHub Release 或本地 `.difypkg` 文件进行处理。脚本会解压插件、修改 `manifest.yaml` 和 `.verification.dify.json` 中的字段、下载并离线打包 Python 依赖（将依赖下载到 `wheels/` 并修改 `requirements.txt`），最后调用本地的 `dify-plugin-<os>-<arch>` 可执行文件进行打包生成带后缀的 `.difypkg`。

主要功能：

- 下载 Python 依赖到 `wheels/`，并把 `requirements.txt` 改为离线安装方式
- 调用本地 `dify-plugin-<os>-<arch>` 工具进行最终打包

### 先决条件

- Python 3.12+
- `pip` 可用
- 安装 Python 库：`requests`、`PyYAML`
- `dify-plugin-<os>-<arch>` 工具位于脚本同目录（脚本会根据宿主机的 OS 与架构自动选择文件名，例如 `dify-plugin-windows-amd64` 或 `dify-plugin-linux-arm64`）。
- 对于 Linux，若系统安装了 `unzip`，脚本会优先使用系统 `unzip`，否则使用 Python 标准库解压。

可选环境变量（覆盖默认值）：

- `GITHUB_API_URL` - GitHub API/主机前缀，默认 `https://github.com`
- `MARKETPLACE_API_URL` - Dify 市场 API 地址，默认 `https://marketplace.dify.ai`
- `PIP_MIRROR_URL` - pip 镜像源，默认 `https://mirrors.aliyun.com/pypi/simple`

### 安装依赖

在 PowerShell 下（Windows）：

```powershell
python -m pip install --upgrade pip
python -m pip install requests pyyaml
```

或者使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install requests pyyaml
```

### 用法

基本命令行：

```powershell
python plugin_repackaging.py [-p 平台] [-s 后缀] <source> <args...>

# source: market | github | local
```

参数说明：

- `-p, --platform`：为 pip 下载指定平台，比如 `manylinux2014_x86_64`。脚本会把其转成 pip 参数：`--platform <platform> --only-binary=:all:`，并在 `pip download` 时使用。可用于交叉打包依赖。
- `-s, --suffix`：输出包后缀，默认 `offline`，最终输出文件名样式为 `<plugin>-<suffix>.difypkg`。

source 依赖的附加参数：

- market: `market [author] [name] [version]`
  - 示例：`python plugin_repackaging.py market xcsf my-plugin 0.1.0`
- github: `github [repo] [release] [asset_name]`
  - `repo` 支持带或不带 `https://github.com` 前缀。`asset_name` 需包含 `.difypkg` 后缀。
  - 示例：`python plugin_repackaging.py github xcsf/my-plugin v1.0.0 my-plugin-1.0.0.difypkg`
- local: `local [difypkg_path]`
  - 示例：`python plugin_repackaging.py local ./langgenius-openai_api_compatible_0.0.23.difypkg`

示例（Windows PowerShell，指定平台并自定义后缀）：

```powershell
python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 local .\your-plugin.difypkg
```

示例（从 Dify 市场下载并打包）：

```powershell
python plugin_repackaging.py market xcsf plugin-name 0.1.0
```

示例（从 GitHub Release 下载并打包）：

```powershell
python plugin_repackaging.py github xcsf/my-repo v1.2.3 plugin-1.2.3.difypkg
```

### 执行流程概览

1. 将目标 `.difypkg` 解压到以包名命名的文件夹
2. 修改 `manifest.yaml` 中的 `author`，并修改 `.verification.dify.json` 中的 `authorized_category`
3. 使用 `pip download` 下载 `requirements.txt` 中列出的依赖到 `./wheels`
4. 将 `requirements.txt` 改为离线安装引用：在最前面写入 `--no-index --find-links=./wheels/`
5. 清理 `.difyignore` 或 `.gitignore` 中有关 `wheels/` 的忽略行
6. 调用本地 `dify-plugin-<os>-<arch>` 工具执行 `plugin package` 并输出带后缀的 `.difypkg`

### 常见问题与排查建议

- pip 下载失败：
  - 检查网络或镜像源是否可用。可用 `PIP_MIRROR_URL` 环境变量替换默认镜像。
  - 若需要更详细日志，手动运行 `pip download -r requirements.txt -d ./wheels --index-url <mirror>` 查看错误。
- 找不到 `dify-plugin-...` 可执行文件：
  - 确认可执行文件位于脚本同目录且命名匹配（脚本会根据 `platform.system()` 与 `platform.machine()` 决定名称）。
  - 在 Windows 上文件通常为 `dify-plugin-windows-amd64`（无扩展名），确认存在并可执行；在 Linux/macOS 上需有可执行权限（`chmod +x`）。
- 解压失败：
  - Linux 环境下优先使用系统 `unzip`（若已安装），否则使用 Python 的 `zipfile` 模块。
- 权限或路径问题：
  - 请在脚本所在目录运行，或者使用绝对路径指定本地 `.difypkg` 文件。
- 在 .env 配置文件将 FORCE_VERIFYING_SIGNATURE 改为 false ，Dify 平台将允许安装所有未在 Dify Marketplace 上架（审核）的插件。
- 在 .env 配置文件将 PLUGIN_MAX_PACKAGE_SIZE 增大为 524288000，Dify 平台将允许安装 500M 大小以内的插件。

- 在 .env 配置文件将 NGINX_CLIENT_MAX_BODY_SIZE 增大为 500M，Nginx 客户端将允许上传 500M 大小以内的内容。

### 可定制点（代码层面）

- `manifest.yaml` / `.verification.dify.json` 的字段修改目前是硬编码为 `xcsf`，如需自定义请修改脚本中对应位置。
- 若需要不同的 pip 镜像或额外 pip 参数，可通过 `PIP_MIRROR_URL` 环境变量或扩展脚本中的 `pip_cmd` 列表。

### 贡献

欢迎提交 issue 或 pull request 来改进脚本：例如让 author 与 authorized_category 支持命令行参数、改进错误处理、或增加对更多平台工具的自动下载。

### 许可证

本仓库未在脚本中指定许可证。使用或分发前请在仓库中添加合适的 LICENSE 文件，或联系原作者确认许可。

---

生成者: README 自动生成器（基于 `plugin_repackaging.py` 分析）
