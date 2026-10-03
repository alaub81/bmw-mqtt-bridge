#!/usr/bin/env python3
"""Install pinned native linters into .lint-tools without changing system packages."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".lint-tools"
BIN = TOOLS / "bin"


def has_version(binary, specification):
    try:
        result = subprocess.run(
            [str(binary), specification.get("versionFlag", "--version")],
            text=True, capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(re.search(r"(?<![\d.])" + re.escape(specification["version"]) + r"(?![\d.])",
                          result.stdout + result.stderr))


def download(url, checksum, destination):
    print(f"Downloading {destination.name}", flush=True)
    urllib.request.urlretrieve(url, destination)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != checksum:
        destination.unlink()
        raise ValueError(f"Checksum mismatch: {url}")


def install_binary(name, specification, target_platform):
    destination = BIN / name
    if destination.exists() and has_version(destination, specification):
        return
    system_binary = shutil.which(name)
    if system_binary and has_version(system_binary, specification):
        # Homebrew supplies Hadolint on macOS, for which no release binary exists.
        if destination.is_symlink():
            destination.unlink()
        shutil.copyfile(system_binary, destination)
    else:
        asset = specification["assets"].get(target_platform)
        if asset is None:
            raise ValueError(f"Install {name} {specification['version']} on PATH first; no asset for {target_platform}")
        filename, checksum = asset
        archive_path = TOOLS / filename
        download(specification["releaseBase"] + filename, checksum, archive_path)
        if filename.endswith(".tar.gz"):
            with tarfile.open(archive_path) as archive:
                candidates = [m for m in archive.getmembers() if m.isfile() and Path(m.name).name == name]
                if len(candidates) != 1:
                    raise ValueError(f"Expected one {name} executable in {filename}")
                with archive.extractfile(candidates[0]) as binary:
                    destination.write_bytes(binary.read())
        else:
            shutil.copyfile(archive_path, destination)
    destination.chmod(0o755)
    if not has_version(destination, specification):
        raise ValueError(f"Unexpected {name} version")


def install_cppcheck(specification):
    destination = BIN / "cppcheck"
    if destination.exists() and has_version(destination, specification):
        return
    archive_path = TOOLS / "cppcheck-source.tar.gz"
    download(specification["url"], specification["sha256"], archive_path)
    with tarfile.open(archive_path) as archive:
        archive.extractall(TOOLS, filter="data")
    source = TOOLS / f"cppcheck-{specification['version']}"
    subprocess.run(
        ["make", "-C", str(source), f"-j{min(os.cpu_count() or 2, 4)}",
         "MATCHCOMPILER=yes", f"FILESDIR={source}"], check=True,
    )
    if destination.is_symlink():
        destination.unlink()
    shutil.copyfile(source / "cppcheck", destination)
    destination.chmod(0o755)
    if not has_version(destination, specification):
        raise ValueError("Unexpected Cppcheck version")


def main():
    if sys.version_info < (3, 12):
        raise ValueError("Python 3.12 or newer is required to install lint tools")
    BIN.mkdir(parents=True, exist_ok=True)
    machine = {"aarch64": "arm64", "AMD64": "x86_64"}.get(platform.machine(), platform.machine())
    target_platform = f"{sys.platform}-{machine}"
    specifications = json.loads((ROOT / "lint-tools.json").read_text())
    for name in ("shellcheck", "hadolint", "actionlint"):
        install_binary(name, specifications[name], target_platform)
        print(f"Ready: {name} {specifications[name]['version']}", flush=True)
    install_cppcheck(specifications["cppcheck"])
    print(f"Ready: cppcheck {specifications['cppcheck']['version']}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Lint tool installation failed: {error}", file=sys.stderr)
        sys.exit(1)
