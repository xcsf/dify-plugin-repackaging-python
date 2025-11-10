#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
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

            print("pip_platform:", self.pip_platform)
            # Download dependencies
            pip_cmd = [
                "pip", "download",
                *(self.pip_platform.split() if self.pip_platform else []),
                "-r", "requirements.txt",
                "-d", "./wheels",
                "--index-url", self.pip_mirror_url,
                "--trusted-host", "mirrors.aliyun.com"
            ]
            subprocess.run(pip_cmd, check=True)
            # Modify requirements.txt
            with open("requirements.txt", "r") as f:
                content = f.read()
            
            with open("requirements.txt", "w") as f:
                f.write("--no-index --find-links=./wheels/\n" + content)

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
        return self.repackage(Path(package_path).absolute())

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


def main():
    parser = argparse.ArgumentParser(description="Dify Plugin Repackaging Tool")
    parser.add_argument("-p", "--platform", help="Python packages platform for cross repackaging")
    parser.add_argument("-s", "--suffix", help="Output package suffix")
    parser.add_argument("source", choices=["market", "github", "local"], help="Source of the plugin")
    parser.add_argument("args", nargs="*", help="Additional arguments based on source type")

    args = parser.parse_args()
    
    repackager = DifyPluginRepackager()
    
    if args.platform:
        repackager.pip_platform = f"--platform {args.platform} --only-binary=:all:"
    if args.suffix:
        repackager.package_suffix = args.suffix

    if args.source == "market":
        if len(args.args) != 3:
            print("Usage: market [plugin author] [plugin name] [plugin version]")
            return 1
        return 0 if repackager.process_market(*args.args) else 1
        
    elif args.source == "github":
        if len(args.args) != 3:
            print("Usage: github [Github repo] [Release title] [Assets name (include .difypkg suffix)]")
            return 1
        return 0 if repackager.process_github(*args.args) else 1
        
    elif args.source == "local":
        if len(args.args) != 1:
            print("Usage: local [difypkg path]")
            return 1
        return 0 if repackager.process_local(args.args[0]) else 1

if __name__ == "__main__":
    sys.exit(main())

# python plugin_repackaging.py -p manylinux2014_x86_64 -s linux-amd64 local ./your-plugin.difypkg