#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import platform
import argparse
import shutil
import subprocess
import zipfile
import json
import yaml
from pathlib import Path
from typing import Optional
import requests
from datetime import datetime

class DifyPluginRepackager:
    DEFAULT_GITHUB_API_URL = "https://github.com"
    DEFAULT_MARKETPLACE_API_URL = "https://marketplace.dify.ai"
    DEFAULT_PIP_MIRROR_URL = "https://mirrors.aliyun.com/pypi/simple"
    # DEFAULT_PIP_MIRROR_URL = "https://pypi.org/simple/"

    def __init__(self):
        self.github_api_url = os.getenv("GITHUB_API_URL", self.DEFAULT_GITHUB_API_URL)
        self.marketplace_api_url = os.getenv("MARKETPLACE_API_URL", self.DEFAULT_MARKETPLACE_API_URL)
        self.pip_mirror_url = os.getenv("PIP_MIRROR_URL", self.DEFAULT_PIP_MIRROR_URL)

        self.curr_dir = Path(__file__).parent.absolute()
        self.os_type = platform.system().lower()
        self.arch_name = platform.machine().lower()

        # Determine command name based on OS and architecture
        arch_suffix = "arm64" if self.arch_name in ["arm64", "aarch64"] else "amd64"
        self.cmd_name = f"dify-plugin-{self.os_type}-{arch_suffix}"

        self.pip_platform = ""
        self.package_suffix = "offline"
        self.extra_packages = []
        self.override_packages = {}

    def download_file(self, url: str, output_path: str) -> bool:
        """Download a file from URL to specified path."""
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except Exception as e:
            print(f"Download failed: {e}")
            return False

    def extract_zip(self, zip_path: Path, extract_dir: Path) -> bool:
        """Extract zip file using appropriate method for the current OS."""
        try:
            if self.os_type == "linux" and shutil.which("unzip"):
                # Use unzip command on Linux if available
                subprocess.run(["unzip", "-o", str(zip_path), "-d", str(extract_dir)], check=True)
            else:
                # Use Python's zipfile module as fallback
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
            return True
        except Exception as e:
            print(f"Extraction failed: {e}")
            return False

    def build_sdist_fallback(self, package_spec: str, wheels_dir: Path) -> bool:
        """Download sdist and build wheel for a package that lacks a pre-built wheel.
        Returns False if build fails or the resulting wheel is incompatible with target platform.
        """
        print(f"  WARNING: No wheel for {package_spec}, building from source...")

        # Parse package name and version from spec (e.g. "greenlet==3.3.0" -> "greenlet", "3.3.0")
        match = re.match(r"^([a-zA-Z0-9_-]+)==(.+)$", package_spec)
        pkg_name = match.group(1) if match else package_spec
        pkg_version = match.group(2) if match else None

        # Record existing wheels to detect newly built ones
        existing_wheels = set(wheels_dir.glob("*.whl")) if wheels_dir.exists() else set()

        trusted_host = re.search(r"://([^/]+)", self.pip_mirror_url).group(1) if "://" in self.pip_mirror_url else "mirrors.aliyun.com"
        result = subprocess.run(
            [
                "pip", "wheel", package_spec,
                "-w", str(wheels_dir),
                "--index-url", self.pip_mirror_url,
                "--trusted-host", trusted_host,
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ERROR: Failed to build {package_spec}:")
            print(result.stderr)
            return False

        # Find the newly built wheel and validate platform compatibility
        if pkg_version:
            new_wheels = set(wheels_dir.glob("*.whl")) - existing_wheels
            target_platform = ""
            if self.pip_platform:
                target_platform = self.pip_platform.split("--platform ")[1].split()[0] if "--platform " in self.pip_platform else self.pip_platform

            for whl in new_wheels:
                if not whl.name.lower().startswith(pkg_name.lower().replace("-", "_") + "-"):
                    continue
                if pkg_version and pkg_version not in whl.name:
                    continue

                # Check if wheel is platform-independent
                parts = whl.name.replace("-", ".")
                if "py3-none-any" in whl.name or "py2.py3-none-any" in whl.name:
                    continue  # Universal wheel, always compatible

                if target_platform:
                    is_compatible = (
                        target_platform in whl.name
                        or "manylinux" in whl.name and "manylinux" in target_platform
                        or "linux" in whl.name and "linux" in target_platform
                    )
                    if not is_compatible:
                        whl.unlink()
                        print(f"  ERROR: Built wheel {whl.name} is incompatible with target platform {target_platform}.")
                        print(f"  The package {package_spec} has no {target_platform} distribution on PyPI.")
                        print(f"  Consider using -o to override, e.g. -o \"{pkg_name}==<compatible_version>\"")
                        return False

            if target_platform:
                print(f"  Built wheel validated for target platform: {target_platform}")

        return True

    def repackage(self, package_path: str):
        """Repackage a Dify plugin package."""
        package_path = Path(package_path)
        package_name = package_path.stem
        extract_dir = self.curr_dir / package_name

        # Create extraction directory
        extract_dir.mkdir(exist_ok=True)

        print("Unzipping...")
        if not self.extract_zip(package_path, extract_dir):
            return False

        try:
            print("Repackaging...")

            os.chdir(extract_dir)
            # 修改 manifest.yaml 和 .verification.dify.json
            if Path("manifest.yaml").exists():
                with open("manifest.yaml", "r", encoding='utf-8') as f:
                    manifest_data = yaml.safe_load(f)
                # 修改 author 字段
                manifest_data['author'] = "xcsf"
                created_at = manifest_data['created_at']
                print(created_at)
                manifest_data['created_at'] = self.normalize_datetime_str(created_at)
                
                # 保存修改后的 yaml
                with open("manifest.yaml", "w", encoding='utf-8') as f:
                    yaml.safe_dump(manifest_data, f, allow_unicode=True)
            
            if Path(".verification.dify.json").exists():
                with open(".verification.dify.json", "r", encoding='utf-8') as f:
                    verify_data = json.load(f)
                # 修改 authorized_category 字段
                verify_data['authorized_category'] = "xcsf"
                # 保存修改后的 json
                with open(".verification.dify.json", "w", encoding='utf-8') as f:
                    json.dump(verify_data, f)

            # Override packages in requirements.txt
            if self.override_packages:
                print("override_packages:", self.override_packages)
                requirements_path = Path("requirements.txt")
                if requirements_path.exists():
                    lines = requirements_path.read_text(encoding="utf-8").splitlines()
                    new_lines = []
                    for line in lines:
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            new_lines.append(line)
                            continue
                        match = re.match(r"^([a-zA-Z0-9_-]+)\s*(.*)", stripped.split(";")[0])
                        if match:
                            pkg_name = match.group(1).lower()
                            if pkg_name in self.override_packages:
                                print(f"  Override {stripped} -> {self.override_packages[pkg_name]}")
                                new_lines.append(self.override_packages[pkg_name])
                                continue
                        new_lines.append(line)
                    requirements_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

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
            attempted_pkgs = set()

            while True:
                result = subprocess.run(pip_cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    break

                if sdist_build_count >= MAX_SDIST_BUILDS:
                    print(f"ERROR: Exceeded max sdist builds ({MAX_SDIST_BUILDS}).")
                    print(result.stderr)
                    raise subprocess.CalledProcessError(result.returncode, pip_cmd, result.stdout, result.stderr)

                # Extract the failing package name from stderr
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

                if pkg_spec in attempted_pkgs:
                    print(f"ERROR: Already attempted build for {pkg_spec}, failing.")
                    print(f"Consider using -o to override the package version.")
                    raise subprocess.CalledProcessError(result.returncode, pip_cmd, result.stdout, result.stderr)
                attempted_pkgs.add(pkg_spec)

                if not self.build_sdist_fallback(pkg_spec, wheels_dir):
                    # Build failed for reasons other than "no wheel available"
                    print(f"ERROR: Failed to build {pkg_spec} from source.")
                    print(f"Consider using -o to override the package version.")
                    raise subprocess.CalledProcessError(result.returncode, pip_cmd, result.stdout, result.stderr)

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

            existing_lines = []
            if requirements_path.exists():
                with open(requirements_path, "r", encoding="utf-8") as f:
                    existing_lines = f.read().splitlines()

            filtered_lines = [
                line
                for line in existing_lines
                if not line.strip().startswith("--no-index")
                and not line.strip().startswith("--find-links")
                and not line.strip().startswith("-f")
            ]
            existing_reqs = {line.strip() for line in filtered_lines if line.strip()}
            extra_lines = []
            for pkg in self.extra_packages:
                pkg_line = pkg.strip()
                if not pkg_line or pkg_line in existing_reqs:
                    continue
                extra_lines.append(pkg_line)
                existing_reqs.add(pkg_line)

            new_lines = ["--no-index", "-f ./wheels/"]
            if extra_lines:
                new_lines.extend(extra_lines)
            new_lines.extend(filtered_lines)
            requirements_path.write_text("\n".join(new_lines).rstrip("\n") + "\n", encoding="utf-8")

            self.ensure_uv_offline_settings(Path("pyproject.toml"))

            # Update ignore file
            ignore_path = Path(".difyignore" if Path(".difyignore").exists() else ".gitignore")
            if ignore_path.exists():
                with open(ignore_path, "r") as f:
                    lines = f.readlines()
                
                with open(ignore_path, "w") as f:
                    f.writelines(line for line in lines if not line.strip().startswith("wheels/"))

            # Change back to original directory
            os.chdir(self.curr_dir)

            # Make plugin command executable
            cmd_path = self.curr_dir / self.cmd_name
            if cmd_path.exists():
                if self.os_type != "windows":
                    cmd_path.chmod(0o755)

            # Package the plugin
            output_path = self.curr_dir / f"{package_name}-{self.package_suffix}.difypkg"
            subprocess.run([
                str(cmd_path),
                "plugin", "package",
                str(extract_dir),
                "-o", str(output_path),
                "--max-size", "5120"
            ], check=True)

            print("Repackage success.")
            return True

        except subprocess.CalledProcessError as e:
            print(f"Repackage failed: {e}")
            return False
        finally:
            os.chdir(self.curr_dir)

    def process_market(self, author: str, name: str, version: str):
        """Process marketplace plugin download and repackaging."""
        print("From the Dify Marketplace downloading ...")
        
        package_path = self.curr_dir / f"{author}-{name}_{version}.difypkg"
        download_url = f"{self.marketplace_api_url}/api/v1/plugins/{author}/{name}/{version}/download"
        
        print(f"Downloading {download_url} ...")
        if not self.download_file(download_url, package_path):
            return False
            
        print("Download success.")
        return self.repackage(package_path)

    def process_github(self, repo: str, release: str, asset_name: str):
        """Process GitHub plugin download and repackaging."""
        print("From Github downloading ...")
        
        if not repo.startswith(self.github_api_url):
            repo = f"{self.github_api_url}/{repo}"
            
        plugin_name = Path(asset_name).stem
        package_path = self.curr_dir / f"{plugin_name}-{release}.difypkg"
        download_url = f"{repo}/releases/download/{release}/{asset_name}"
        
        print(f"Downloading {download_url} ...")
        if not self.download_file(download_url, package_path):
            return False
            
        print("Download success.")
        return self.repackage(package_path)

    def process_local(self, package_path: str):
        """Process local plugin repackaging."""
        p = Path(package_path).resolve()
        # On Windows, PowerShell may strip the backslash from `.\filename`,
        # resulting in `.filename` which doesn't exist. Fix by stripping leading dot.
        if not p.exists() and p.name.startswith("."):
            alt = p.parent / p.name[1:]
            if alt.exists():
                p = alt
        return self.repackage(p)

    def normalize_datetime_str(self, dt_input: str) -> str:
        # 如果是 datetime 对象，直接转为 ISO 格式（带 T）
        if isinstance(dt_input, datetime):
            return dt_input.isoformat()
        
        # 如果不是字符串，原样返回（或转为字符串）
        if not isinstance(dt_input, str):
            return str(dt_input)
        
        # 如果已经是合法 ISO 格式（含 T），直接返回
        if 'T' in dt_input:
            return dt_input
        
        # 尝试将空格替换为 T
        if ' ' in dt_input:
            parts = dt_input.split(' ', 1)
            if len(parts) == 2:
                candidate = parts[0] + 'T' + parts[1]
                try:
                    # 验证是否是合法时间（Python 3.13 支持 +08:00）
                    datetime.fromisoformat(candidate)
                    return candidate
                except ValueError:
                    pass
        
        # 无法识别，返回原字符串
        return dt_input

    def ensure_uv_offline_settings(self, pyproject_path: Path):
        if not pyproject_path.exists():
            return

        content = pyproject_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        section_start = None
        for i, line in enumerate(lines):
            if line.strip() == "[tool.uv]":
                section_start = i
                break

        new_settings = ["no-index = true", "find-links = [\"./wheels/\"]"]

        if section_start is None:
            updated = content.rstrip("\n")
            if updated:
                updated += "\n\n"
            updated += "[tool.uv]\n" + "\n".join(new_settings) + "\n"
            pyproject_path.write_text(updated, encoding="utf-8")
            return

        section_end = len(lines)
        for j in range(section_start + 1, len(lines)):
            candidate = lines[j].strip()
            if candidate.startswith("[") and candidate.endswith("]"):
                section_end = j
                break

        section_lines = lines[section_start + 1 : section_end]
        kept_lines = []
        for line in section_lines:
            stripped = line.strip()
            if stripped.startswith("no-index") or stripped.startswith("no_index"):
                continue
            if stripped.startswith("find-links") or stripped.startswith("find_links"):
                continue
            kept_lines.append(line)

        updated_lines = lines[: section_start + 1] + new_settings + kept_lines + lines[section_end:]
        pyproject_path.write_text("\n".join(updated_lines).rstrip("\n") + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Dify Plugin Repackaging Tool")
    parser.add_argument("-p", "--platform", help="Python packages platform for cross repackaging")
    parser.add_argument("-s", "--suffix", help="Output package suffix")
    parser.add_argument(
        "-e",
        "--extra",
        action="append",
        help="Extra packages to include in wheels and requirements.txt, e.g. setuptools==80.9.0 (repeatable, comma-separated supported)",
    )
    parser.add_argument(
        "-o",
        "--override",
        action="append",
        help="Override a package in requirements.txt with specified version, e.g. greenlet==3.2.5 (repeatable)",
    )

    subparsers = parser.add_subparsers(dest="source", required=True)

    market_parser = subparsers.add_parser("market")
    market_parser.add_argument("author")
    market_parser.add_argument("name")
    market_parser.add_argument("version")

    github_parser = subparsers.add_parser("github")
    github_parser.add_argument("repo")
    github_parser.add_argument("release")
    github_parser.add_argument("asset_name")

    local_parser = subparsers.add_parser("local")
    local_parser.add_argument("package_path")

    args = parser.parse_args()

    repackager = DifyPluginRepackager()
    
    if args.platform:
        repackager.pip_platform = f"--platform {args.platform} --only-binary=:all:"
    if args.suffix:
        repackager.package_suffix = args.suffix
    if args.extra:
        extra_packages = []
        for raw in args.extra:
            if raw is None:
                continue
            parts = [p.strip() for p in raw.split(",")]
            extra_packages.extend([p for p in parts if p])
        repackager.extra_packages = extra_packages
    if args.override:
        for raw in args.override:
            if raw is None:
                continue
            parts = [p.strip() for p in raw.split(",")]
            for part in parts:
                if not part:
                    continue
                match = re.match(r"^([a-zA-Z0-9_-]+)\s*(.*)", part)
                if match:
                    pkg_name = match.group(1).lower()
                    pkg_spec = part
                    repackager.override_packages[pkg_name] = pkg_spec

    if args.source == "market":
        return 0 if repackager.process_market(args.author, args.name, args.version) else 1
        
    elif args.source == "github":
        return 0 if repackager.process_github(args.repo, args.release, args.asset_name) else 1
        
    elif args.source == "local":
        return 0 if repackager.process_local(args.package_path) else 1

if __name__ == "__main__":
    sys.exit(main())


# python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 -e "ruff>=0.12.5" -e "pytest>=8.4.1" -e "setuptools==80.9.0" -e "black" local .\langgenius-openai_api_compatible_0.0.45.difypkg
# python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 -o "greenlet==3.2.5" local .\langgenius-email_0.0.14.difypkg