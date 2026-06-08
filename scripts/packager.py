#!/usr/bin/env python3
"""
Dify Plugin Offline Packager

Downloads a Dify plugin (from Marketplace, GitHub, or local file),
bundles its Python dependencies as wheels, and packages it as an
offline-ready .difypkg file.

Can run inside the official langgenius/dify-plugin-daemon container
or directly on the host (Linux, macOS, Windows). Requires uv.
"""

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------

MARKETPLACE_API_URL = os.environ.get(
    "MARKETPLACE_API_URL", "https://marketplace.dify.ai"
)
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://github.com")
PIP_INDEX_URL = os.environ.get(
    "PIP_INDEX_URL", "https://pypi.org/simple"
)
DIFY_PLUGIN_DAEMON_VERSION = os.environ.get("DIFY_PLUGIN_DAEMON_VERSION", "0.5.3")

# All paths are relative to the current working directory so the script
# works both inside the container and locally without any path changes.
# Override individual directories via environment variables if needed.
_cwd = Path.cwd()
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(_cwd / "difypkg")))
BIN_DIR    = Path(os.environ.get("BIN_DIR",    str(_cwd / "bin")))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_AGENT = "dify-plugin-offline-packager/1.0"


def download_file(url: str, dest: str) -> None:
    """Download a file from a URL with a proper User-Agent header."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, printing the command and raising on failure."""
    print(f"  ▸ {' '.join(cmd)}", flush=True)
    try:
        return subprocess.run(cmd, check=True, **kwargs)
    except subprocess.CalledProcessError as e:
        if e.stderr:
            print(e.stderr if isinstance(e.stderr, str) else e.stderr.decode(errors="replace"),
                  file=sys.stderr, flush=True)
        sys.exit(f"\n❌ Command failed (exit {e.returncode}): {' '.join(e.cmd)}")


def ensure_dify_plugin_cli() -> Path:
    """
    Ensure the dify-plugin CLI binary exists and is executable.
    Downloads from GitHub releases if not already cached.
    Returns the path to the binary.

    Supported platforms:
      - Linux   : dify-plugin-linux-{arch}
      - macOS   : dify-plugin-darwin-{arch}
      - Windows : dify-plugin-windows-{arch}.exe
    """
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        sys.exit(f"Unsupported architecture: {machine}")

    system = platform.system().lower()  # 'linux', 'darwin', 'windows'
    if system == "linux":
        os_name = "linux"
        suffix = ""
    elif system == "darwin":
        os_name = "darwin"
        suffix = ""
    elif system == "windows":
        os_name = "windows"
        suffix = ".exe"
    else:
        sys.exit(f"Unsupported OS: {platform.system()}")

    binary_name = f"dify-plugin-{os_name}-{arch}{suffix}"
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    cached = BIN_DIR / f"dify-plugin-cli-{DIFY_PLUGIN_DAEMON_VERSION}-{os_name}-{arch}{suffix}"

    if cached.exists():
        print(f"✔  Using cached dify-plugin CLI ({DIFY_PLUGIN_DAEMON_VERSION}) from {cached}")
        return cached

    url = (
        f"https://github.com/langgenius/dify-plugin-daemon"
        f"/releases/download/{DIFY_PLUGIN_DAEMON_VERSION}/{binary_name}"
    )
    print(f"⬇  Downloading dify-plugin CLI ({DIFY_PLUGIN_DAEMON_VERSION}) …")
    print(f"   {url}")
    download_file(url, str(cached))
    if system != "windows":
        cached.chmod(cached.stat().st_mode | stat.S_IEXEC)
    print("   Done.")
    return cached


def download_marketplace(author: str, name: str, version: str, dest: Path) -> Path:
    """Download a plugin from the Dify Marketplace."""
    url = f"{MARKETPLACE_API_URL}/api/v1/plugins/{author}/{name}/{version}/download"
    filename = f"{author}-{name}_{version}.difypkg"
    filepath = dest / filename
    print(f"⬇  Downloading from Marketplace …")
    print(f"   {url}")
    download_file(url, str(filepath))
    return filepath


def download_github(repo: str, tag: str, asset: str, dest: Path) -> Path:
    """Download a plugin from a GitHub release."""
    # Allow full URL or short repo form
    if not repo.startswith("http"):
        repo_url = f"{GITHUB_API_URL}/{repo}"
    else:
        repo_url = repo
    url = f"{repo_url}/releases/download/{tag}/{asset}"
    stem = asset.removesuffix(".difypkg")
    filename = f"{stem}-{tag}.difypkg"
    filepath = dest / filename
    print(f"⬇  Downloading from GitHub …")
    print(f"   {url}")
    download_file(url, str(filepath))
    return filepath


def resolve_local(path_str: str) -> Path:
    """Resolve a local .difypkg path. Falls back to OUTPUT_DIR if not found as-is."""
    p = Path(path_str)
    if not p.exists():
        # Also look under OUTPUT_DIR (e.g. user passed just the filename)
        alt = OUTPUT_DIR / p.name
        if alt.exists():
            return alt
        sys.exit(f"File not found: {path_str}")
    return p


# ---------------------------------------------------------------------------
# Dependency download & patching helpers
# ---------------------------------------------------------------------------


def _pip_download_cmd(req_file: Path, wheels_dir: Path) -> list[str]:
    """Build the base `uv run python -m pip download` command."""
    cmd = [
        "uv", "run", "python", "-m", "pip", "download",
        "-r", str(req_file),
        "-d", str(wheels_dir),
    ]
    if PIP_INDEX_URL:
        cmd += ["--index-url", PIP_INDEX_URL]
    return cmd


def _download_wheels_pip(req_file: Path, wheels_dir: Path) -> None:
    """Download wheels using pip based on requirements.txt."""
    print("⬇  Downloading Python dependencies …")
    run(_pip_download_cmd(req_file, wheels_dir))


def _download_wheels_uv(extract_dir: Path, wheels_dir: Path) -> None:
    """
    Lock and download wheels using uv based on pyproject.toml.

    Steps:
      1. Inject ``environments`` and strip ``[dependency-groups]`` so that
         ``uv lock`` only resolves production deps for the current OS + Python.
      2. ``uv lock`` to generate / refresh uv.lock (pins exact versions).
      3. ``uv export --frozen --no-hashes --no-dev`` to get the pinned list.
      4. ``uv run python -m pip download`` to fetch all wheels into ``wheels_dir``.
      5. Delete uv.lock so the target machine re-resolves from wheels/ only
         (``--no-index`` + ``--frozen`` is a conflicting combination in uv).
    """
    pyproject_file = extract_dir / "pyproject.toml"

    # ------------------------------------------------------------------
    # 1. Inject environments and strip dev groups before locking.
    # ------------------------------------------------------------------
    _inject_environments(pyproject_file)
    _strip_dependency_groups(pyproject_file)

    # ------------------------------------------------------------------
    # 2. (Re-)generate uv.lock scoped to the target environment.
    # ------------------------------------------------------------------
    print("🔐 Locking dependencies …")
    run(["uv", "lock", "--directory", str(extract_dir)])

    # ------------------------------------------------------------------
    # 3. Export the frozen dependency list (no hashes, no dev).
    # ------------------------------------------------------------------
    print("⬇  Exporting pinned dependencies …")
    export_result = subprocess.run(
        [
            "uv", "export",
            "--frozen", "--no-hashes", "--no-dev",
            "--directory", str(extract_dir),
        ],
        capture_output=True, text=True,
    )
    if export_result.returncode != 0:
        print("   ⚠  uv export failed:")
        print(export_result.stderr, file=sys.stderr)
        # Fallback to requirements.txt if present
        req_file = extract_dir / "requirements.txt"
        if req_file.exists():
            print("   ↪  Falling back to requirements.txt")
            _download_wheels_pip(req_file, wheels_dir)
        return

    # Write the exported list to a temp file and download
    exported_req = extract_dir / "_exported_requirements.txt"
    exported_req.write_text(export_result.stdout)
    try:
        print("⬇  Downloading Python dependencies …")  # step 4
        run(_pip_download_cmd(exported_req, wheels_dir))
    finally:
        exported_req.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 5. Delete uv.lock so the target machine re-resolves from wheels/.
    #    uv --no-index + --frozen is a conflicting combination (uv#15519).
    # ------------------------------------------------------------------
    uv_lock = extract_dir / "uv.lock"
    if uv_lock.exists():
        uv_lock.unlink()
        print("   Ὕ1  Deleted uv.lock (target will re-resolve from wheels/).")


def _inject_environments(pyproject_file: Path) -> None:
    """
    Inject only the ``environments`` key into the ``[tool.uv]`` section of
    pyproject.toml.

    This is called **before** ``uv lock`` so that the lock file is scoped
    to the current OS (linux / darwin / win32) + Python version only,
    avoiding unnecessary wheels for other platforms or future Python versions.
    """
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"  # e.g. "3.12"
    _sys = platform.system().lower()
    if _sys == "darwin":
        sys_platform = "darwin"
    elif _sys == "windows":
        sys_platform = "win32"
    else:
        sys_platform = "linux"
    content = pyproject_file.read_text()
    env_line = f'environments = ["sys_platform == \'{sys_platform}\' and python_version == \'{py_ver}\'"]'
    uv_block = f'\n[tool.uv]\n{env_line}\n'

    if re.search(r'^\[tool\.uv\]', content, re.MULTILINE):
        def _inject(m: re.Match) -> str:
            return m.group(0) + '\n' + env_line
        content = re.sub(
            r'^\[tool\.uv\]',
            _inject,
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        content = content.rstrip() + "\n" + uv_block

    pyproject_file.write_text(content)
    print(f'   ✏  Injected environments = [{sys_platform} + python {py_ver}] into pyproject.toml.')


def _strip_dependency_groups(pyproject_file: Path) -> None:
    """
    Remove the ``[dependency-groups]`` section from pyproject.toml.

    This is called **before** ``uv lock`` so that dev/test packages are never
    included in the lockfile.  Even with ``uv sync --no-dev``, uv still
    *resolves* dev-group packages when ``no-index = true``, causing
    "not found in provided package locations" errors for packages that are
    intentionally absent from wheels/ (see uv#15519).
    """
    content = pyproject_file.read_text()
    new_content = re.sub(
        r'^\[dependency-groups\].*?(?=^\[|\Z)',
        '',
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    if new_content != content:
        pyproject_file.write_text(new_content)
        print("   ✏  Removed [dependency-groups] from pyproject.toml.")


def _patch_pyproject_toml_offline(pyproject_file: Path) -> None:
    """
    Add offline settings to the ``[tool.uv]`` section of pyproject.toml.

    Merges into the existing ``[tool.uv]`` section (which already has
    ``environments`` from ``_inject_environments``)::

        no-index = true
        find-links = ["./wheels/"]

    uv.lock is deleted before packaging so that the target machine
    re-resolves dependencies from wheels/ only (uv#15519: --no-index +
    --frozen is a conflicting combination).
    """
    content = pyproject_file.read_text()

    offline_settings = (
        'no-index = true\n'
        'find-links = ["./wheels/"]'
    )
    uv_block = f'\n[tool.uv]\n{offline_settings}\n'

    if re.search(r'^\[tool\.uv\]', content, re.MULTILINE):
        def _inject(m: re.Match) -> str:
            return m.group(0) + '\n' + offline_settings
        content = re.sub(
            r'^\[tool\.uv\]',
            _inject,
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        content = content.rstrip() + "\n" + uv_block

    pyproject_file.write_text(content)
    print("   ✏  Patched pyproject.toml with [tool.uv] offline settings (no-index, find-links).")


def _patch_requirements_txt_offline(req_file: Path) -> None:
    """Prepend --no-index --find-links=./wheels/ to requirements.txt."""
    original = req_file.read_text()
    patched = f"--no-index --find-links=./wheels/\n{original}"
    req_file.write_text(patched)
    print("   ✏  Patched requirements.txt for offline use.")


def _remove_from_ignore_files(extract_dir: Path, entries: set[str]) -> None:
    """Remove specific entries from .difyignore and .gitignore if they exist."""
    for ignore_name in (".difyignore", ".gitignore"):
        ignore_file = extract_dir / ignore_name
        if not ignore_file.exists():
            continue
        lines = ignore_file.read_text().splitlines()
        filtered = [l for l in lines if l.strip() not in entries]
        if len(filtered) != len(lines):
            ignore_file.write_text("\n".join(filtered) + "\n")
            removed = sorted({l.strip() for l in lines} & entries)
            print(f"   ✏  Removed {removed} from {ignore_name}.")


# ---------------------------------------------------------------------------
# Core packaging logic
# ---------------------------------------------------------------------------


def package_offline(pkg_path: Path, cli: Path, work: Path) -> Path:
    """
    Package a .difypkg with bundled wheels for offline use.

    1. Unzip the package
    2. Lock dependencies and download all wheels (via uv lock + uv run python -m pip download)
    3. Patch pyproject.toml or requirements.txt for offline use
    4. Re-pack using the dify-plugin CLI
    """
    pkg_name = pkg_path.stem
    extract_dir = work / pkg_name

    # -- Unzip --
    print(f"\n📦 Unzipping {pkg_path.name} …")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(pkg_path, "r") as zf:
        zf.extractall(extract_dir)

    # -- Bundle dependencies --
    pyproject_file = extract_dir / "pyproject.toml"
    req_file = extract_dir / "requirements.txt"
    has_pyproject = pyproject_file.exists()
    has_requirements = req_file.exists()

    if not has_pyproject and not has_requirements:
        print("   ⚠  No pyproject.toml or requirements.txt found – skipping dependency download.")
    else:
        wheels_dir = extract_dir / "wheels"
        wheels_dir.mkdir(exist_ok=True)

        if has_pyproject:
            print("   ℹ  pyproject.toml detected – using uv lock + uv pip download.")
            _download_wheels_uv(extract_dir, wheels_dir)
            _patch_pyproject_toml_offline(pyproject_file)
        else:
            print("   ℹ  requirements.txt detected (no pyproject.toml) – using uv pip download.")
            _download_wheels_pip(req_file, wheels_dir)
            _patch_requirements_txt_offline(req_file)

        # Ensure wheels/ is not excluded by ignore files
        _remove_from_ignore_files(extract_dir, {"wheels/", "wheels"})

    # -- Re-pack --
    output_name = f"{pkg_name}-offline.difypkg"
    output_path = OUTPUT_DIR / output_name

    print(f"\n📦 Packaging → {output_name} …")
    run([
        str(cli), "plugin", "package",
        str(extract_dir),
        "-o", str(output_path),
        "--max-size", "5120",
    ])

    print(f"\n✅ Success! → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Marketplace shorthand parser  (e.g. "langgenius/openai:0.0.17")
# ---------------------------------------------------------------------------


def parse_marketplace_shorthand(value: str) -> tuple[str, str, str]:
    """
    Parse 'author/name:version' → (author, name, version)
    """
    if ":" not in value or "/" not in value:
        sys.exit(
            f"Invalid marketplace shorthand: {value}\n"
            f"Expected format: author/name:version  (e.g. langgenius/openai:0.0.17)"
        )
    repo_part, version = value.rsplit(":", 1)
    author, name = repo_part.split("/", 1)
    return author, name, version


def parse_github_shorthand(value: str) -> tuple[str, str, str]:
    """
    Parse 'owner/repo:tag:asset' or 'owner/repo:tag' → (repo, tag, asset)
    If asset is omitted, we guess '<repo-name>.difypkg'.
    """
    parts = value.split(":")
    if len(parts) == 3:
        repo, tag, asset = parts
        if not asset.endswith(".difypkg"):
            asset += ".difypkg"
        return repo, tag, asset
    elif len(parts) == 2:
        repo, tag = parts
        repo_name = repo.split("/")[-1]
        asset = f"{repo_name}.difypkg"
        return repo, tag, asset
    else:
        sys.exit(
            f"Invalid github shorthand: {value}\n"
            f"Expected format: owner/repo:tag[:asset.difypkg]"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Dify Plugin Offline Packager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --marketplace "langgenius/openai:0.0.17"
  %(prog)s --github "junjiem/dify-plugin-tools-dbquery:v0.0.2:db_query.difypkg"
  %(prog)s --local "./my-plugin.difypkg"
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--marketplace", "-m",
        metavar="AUTHOR/NAME:VERSION",
        help="Download from the Dify Marketplace (e.g. langgenius/openai:0.0.17)",
    )
    group.add_argument(
        "--github", "-g",
        metavar="OWNER/REPO:TAG[:ASSET]",
        help="Download from GitHub releases",
    )
    group.add_argument(
        "--local", "-l",
        metavar="PATH",
        help="Use a local .difypkg file",
    )

    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cli = ensure_dify_plugin_cli()

    with tempfile.TemporaryDirectory(prefix="packager-work-") as _tmp:
        work = Path(_tmp)

        # -- Acquire the .difypkg --
        if args.marketplace:
            author, name, version = parse_marketplace_shorthand(args.marketplace)
            pkg_path = download_marketplace(author, name, version, work)

        elif args.github:
            repo, tag, asset = parse_github_shorthand(args.github)
            pkg_path = download_github(repo, tag, asset, work)

        elif args.local:
            pkg_path = resolve_local(args.local)

        # -- Copy original to output dir --
        original_dest = OUTPUT_DIR / pkg_path.name
        if pkg_path != original_dest and not original_dest.exists():
            shutil.copy2(pkg_path, original_dest)
            print(f"📄 Original saved → {original_dest}")

        # -- Package for offline use --
        package_offline(pkg_path, cli, work)


if __name__ == "__main__":
    main()
