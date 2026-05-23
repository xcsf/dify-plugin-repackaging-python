# Sdist Build Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 `pip download --only-binary=:all:` 找不到预编译 wheel 时，自动下载源码包并构建 wheel 放入 `wheels/` 目录。

**Architecture:** 新增 `build_sdist_fallback` 方法，将 `repackage()` 中的单次 `pip download` 改为循环重试：失败 -> 解析缺失包名 -> 用 `pip wheel` 构建 -> 从 requirements.txt 移除 -> 重试。

**Tech Stack:** Python 3.12+, subprocess, pathlib, re

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `plugin_repackaging.py` | Modify | Add `build_sdist_fallback` method (new ~30 lines), refactor pip download section in `repackage()` (~40 lines changed) |

All changes are in one file. The `build_sdist_fallback` method is self-contained and follows the existing pattern of methods like `download_file` and `extract_zip`.

---

### Task 1: Add `build_sdist_fallback` method

**Files:**
- Modify: `plugin_repackaging.py` — insert new method after `extract_zip` (around line 69)

- [ ] **Step 1: Add the `build_sdist_fallback` method**

Insert this method into the `DifyPluginRepackager` class, right after the `extract_zip` method (between line 68 and 69):

```python
    def build_sdist_fallback(self, package_spec: str, wheels_dir: Path) -> bool:
        """Download sdist and build wheel for a package that lacks a pre-built wheel."""
        print(f"  WARNING: No wheel for {package_spec}, building from source...")
        if self.pip_platform:
            print(f"  WARNING: Target platform is {self.pip_platform}, but built wheel may not be compatible.")
        result = subprocess.run(
            [
                "pip", "wheel", package_spec,
                "-w", str(wheels_dir),
                "--index-url", self.pip_mirror_url,
                "--trusted-host", "mirrors.aliyun.com",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ERROR: Failed to build {package_spec}:")
            print(result.stderr)
            return False
        return True
```

- [ ] **Step 2: Verify the method was added correctly**

Run: `python -c "from plugin_repackaging import DifyPluginRepackager; r = DifyPluginRepackager(); print(hasattr(r, 'build_sdist_fallback'))"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add plugin_repackaging.py
git commit -m "feat: add build_sdist_fallback method for sdist-to-wheel conversion
When pip download --only-binary=:all: can't find a pre-built wheel for a
package, this method downloads the source distribution and builds the wheel
locally using pip wheel."
```

---

### Task 2: Refactor pip download into retry loop in `repackage()`

**Files:**
- Modify: `plugin_repackaging.py` — replace the pip download section in `repackage()` (lines 134-153)

- [ ] **Step 1: Replace the single pip download call with retry logic**

Replace lines 134-153 in `repackage()` (the section from `print("pip_platform:"...)` through `subprocess.run(pip_cmd, check=True)`) with:

```python
            print("pip_platform:", self.pip_platform)
            # Download dependencies with sdist fallback for missing wheels
            requirements_path = Path("requirements.txt")
            pip_cmd = [
                "pip", "download",

                *(self.pip_platform.split() if self.pip_platform else []),
                "-d", "./wheels",
                "--index-url", self.pip_mirror_url,
                "--trusted-host", "mirrors.aliyun.com"
            ]
            if requirements_path.exists():
                pip_cmd.extend(["-r", str(requirements_path)])
            elif not self.extra_packages:
                print("requirements.txt not found.")
                return False

            if self.extra_packages:
                pip_cmd.extend(self.extra_packages)

            wheels_dir = Path("./wheels")
            wheels_dir.mkdir(exist_ok=True)
            MAX_SDIST_BUILDS = 3
            sdist_build_count = 0

            while True:
                result = subprocess.run(pip_cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    break

                if sdist_build_count >= MAX_SDIST_BUILDS:
                    print(f"ERROR: Exceeded max sdist builds ({MAX_SDIST_BUILDS}).")
                    print(result.stderr)
                    raise subprocess.CalledProcessError(result.returncode, pip_cmd, result.stdout, result.stderr)

                # Extract the failing package name from stderr
                import re
                match = re.search(
                    r"(?:Could not find a version that satisfies the requirement|No matching distribution found for)\s+(\S+)",
                    result.stderr,
                )
                if not match:
                    # Non-package-missing error, fail immediately
                    print(result.stderr)
                    raise subprocess.CalledProcessError(result.returncode, pip_cmd, result.stdout, result.stderr)

                failing_pkg = match.group(1)
                # Strip environment markers (e.g. "; platform_python_implementation == 'CPython'")
                pkg_spec = failing_pkg.split(";")[0].strip()

                if not self.build_sdist_fallback(pkg_spec, wheels_dir):
                    # Build failed for reasons other than "no wheel available"
                    sdist_build_count += 1
                    if sdist_build_count >= MAX_SDIST_BUILDS:
                        print(f"ERROR: Exceeded max sdist builds ({MAX_SDIST_BUILDS}).")
                        print(f"Consider using -o to override the package version.")
                        raise subprocess.CalledProcessError(result.returncode, pip_cmd, result.stdout, result.stderr)
                    continue

                # Build succeeded: remove the package from requirements.txt
                lines = requirements_path.read_text(encoding="utf-8").splitlines()
                filtered = [
                    line for line in lines
                    if not re.match(rf"^{re.escape(pkg_spec)}\b", line.strip().split(";")[0], re.IGNORECASE)
                ]
                requirements_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
                sdist_build_count += 1

                # Rebuild pip_cmd with updated requirements
                pip_cmd = [
                    "pip", "download",
                    *(self.pip_platform.split() if self.pip_platform else []),
                    "-d", "./wheels",
                    "--index-url", self.pip_mirror_url,
                    "--trusted-host", "mirrors.aliyun.com",
                    "-r", str(requirements_path),
                ]
                if self.extra_packages:
                    pip_cmd.extend(self.extra_packages)
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import plugin_repackaging; print('OK')"`
Expected: `OK` with no errors

- [ ] **Step 3: Commit**

```bash
git add plugin_repackaging.py
git commit -m "feat: add retry loop with sdist fallback to pip download
When pip download --only-binary=:all: fails due to missing wheel,
automatically build the wheel from source using pip wheel, remove the
package from requirements.txt, and retry. Max 3 sdist builds allowed."
```

---

### Task 3: Manual end-to-end test

**Files:**
- Test: `plugin_repackaging.py` — run the full pipeline

- [ ] **Step 1: Clean up any previous test artifacts**

```bash
rm -rf langgenius-email_0.0.14/
```

- [ ] **Step 2: Run the original email command (should work, wheels all exist)**

```bash
python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 local .\langgenius-email_0.0.14.difypkg
```

Expected: Success message, `langgenius-email_0.0.14-linux-amd64.difypkg` created. This verifies the normal path (all wheels exist) still works.

- [ ] **Step 3: Verify no `-o` needed for the email package**

The email package no longer needs `-o "greenlet==3.2.5"` because we replaced the broken version in the plugin's requirements.txt previously. However, the `-o` flag should still work if needed for other packages.

- [ ] **Step 4: Final commit if any issues found and fixed**

If any bugs are discovered during testing, fix them and commit.

---

### Task 4: Update README with new behavior

**Files:**
- Modify: `README.md` — add note about sdist fallback

- [ ] **Step 1: Add a note to the 注意事项 section**

Add this bullet to the 注意事项 section:

```markdown
- **sdist 自动回退**：当 pip 找不到指定平台的预编译 wheel 时，会自动下载源码包并构建。最多尝试 3 个包，超过请检查包版本或使用 `-o` 替换。
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add note about sdist auto-build fallback behavior"
```

---

## Spec Self-Review

**1. Spec coverage check:**
- [x] `build_sdist_fallback` method — Task 1
- [x] Retry loop in `repackage()` — Task 2
- [x] Extract failing package from stderr — Task 2 (regex in code)
- [x] Remove built package from requirements.txt — Task 2
- [x] Max 3 sdist builds — Task 2 (`MAX_SDIST_BUILDS = 3`)
- [x] Cross-platform warning — Task 1 (`if self.pip_platform` warning)
- [x] Non-package-missing errors fail immediately — Task 2 (`if not match: raise`)
- [x] No impact on existing functionality — verified in Task 3

**2. Placeholder scan:** No TBD/TODO/incomplete sections found.

**3. Type consistency:** All references to `build_sdist_fallback`, `pip_cmd`, `wheels_dir`, `requirements_path` are consistent across tasks.

**4. Ambiguity check:** All requirements are explicit with concrete code.
