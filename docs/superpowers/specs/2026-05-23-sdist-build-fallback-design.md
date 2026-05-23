# 设计文档：pip download sdist 回退构建

## 问题

使用 `pip download --only-binary=:all:` 时，如果某个包版本没有对应平台的预编译 wheel（sdist 存在），pip 会直接报错退出。当前需要用户用 `-o` 手动替换版本，体验差。

## 目标

当 `pip download --only-binary=:all:` 失败时，自动回退：移除 `--only-binary` 限制，下载失败包的 sdist 并用 `pip wheel` 构建为 wheel，放入 `wheels/` 目录。

## 范围

- 只改动 `repackage()` 方法中的 pip download 部分
- 不改变正常流程（有 wheel 时行为不变）
- 最多回退重试 3 个包，超过则报错退出
- 对 `--platform` 交叉打包场景有效（sdist 会在当前机器上构建，目标平台可能不兼容，需打印警告）

## 架构

### 流程

```
pip download --only-binary=:all: -r requirements.txt
  |
  +-- 成功 -> 继续
  |
  +-- 失败
       |
       +-- 解析 stderr，提取缺失包名
       |    正则: "Could not find a version that satisfies the requirement (\S+)"
       |    或: "No matching distribution found for (\S+)"
       |
       +-- 无匹配 -> 直接报错（非包缺失错误）
       |
       +-- 匹配到包名 -> print 警告
            |
            +-- 用 pip wheel 下载并构建该包的 wheel
            |    pip wheel <package_spec> -w ./wheels/ --index-url <mirror>
            |
            +-- 成功 -> 从 requirements.txt 移除该包，重试主流程
            |
            +-- 失败 -> print 错误，累计计数
                 |
                 +-- 计数 >= 3 -> 报错退出
                 +-- 计数 < 3 -> 继续下一个包
```

### 关键方法

新增 `build_sdist_fallback(package_spec: str, wheels_dir: Path) -> bool`:

```python
def build_sdist_fallback(self, package_spec: str, wheels_dir: Path) -> bool:
    """Download sdist and build wheel for a package that lacks a pre-built wheel."""
    print(f"  WARNING: No wheel for {package_spec}, building from source...")
    result = subprocess.run(
        ["pip", "wheel", package_spec, "-w", str(wheels_dir),
         "--index-url", self.pip_mirror_url,
         "--trusted-host", "mirrors.aliyun.com"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ERROR: Failed to build {package_spec}:")
        print(result.stderr)
        return False
    return True
```

### repackage 方法改动

将现有的单次 `subprocess.run(pip_cmd, check=True)` 替换为循环重试逻辑：

1. 执行 `pip download --only-binary=:all:`
2. 失败则解析包名，调用 `build_sdist_fallback`
3. 构建成功则从 requirements.txt 中移除该包，回到步骤 1
4. 构建失败则累计计数，>= 3 次退出

### requirements.txt 更新

每次成功构建 sdist 后，需要从 requirements.txt 中移除对应行，避免下次重试时再次触发。

### 交叉打包警告

当使用 `-p` 指定 `--platform` 时，sdist 是在当前机器架构上构建的，可能与目标平台不兼容。需要打印警告：

```
WARNING: Building {package} from source. Target platform is {platform}, but
the built wheel may not be compatible. Use with caution.
```

## 错误处理

- sdist 构建失败（缺少 C 编译器、依赖等）-> 打印 stderr，累计计数
- 非包缺失错误（网络问题、pip 本身故障）-> 直接退出
- 超过 3 个包需要构建 -> 退出，建议用户使用 `-o` 替换版本

## 测试

- 手动测试：找一个有 sdist 但没有 manylinux wheel 的包验证
- 验证 wheels/ 目录下生成了正确的 .whl 文件
- 验证 requirements.txt 中被移除的包不再出现

## 不影响现有功能

- 所有 wheel 都存在的场景：行为完全不变
- `-e` extra 包：同样适用回退逻辑
- `-o` override：仍然可用，作为用户主动替换版本的手段
