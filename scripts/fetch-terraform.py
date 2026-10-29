#!/usr/bin/env python3
"""Install a pinned, SHA256-verified Terraform CLI into ignored .tools."""
import hashlib
import io
import os
from pathlib import Path
import platform
import urllib.request
import zipfile

VERSION = "1.13.5"
ROOT = Path(__file__).resolve().parents[1]


def main():
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    if not system or not machine:
        raise SystemExit("Terraform helper supports macOS/Linux ARM64/AMD64")
    name = f"terraform_{VERSION}_{system}_{machine}.zip"
    base = f"https://releases.hashicorp.com/terraform/{VERSION}/"
    with urllib.request.urlopen(base + f"terraform_{VERSION}_SHA256SUMS", timeout=60) as response:
        sums = response.read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == name)
    with urllib.request.urlopen(base + name, timeout=120) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != expected:
        raise SystemExit("Terraform checksum mismatch")
    destination = ROOT / ".tools/terraform"
    destination.parent.mkdir(exist_ok=True)
    if destination.is_symlink():
        raise SystemExit("Refusing to overwrite a tool symlink")
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        data = source.read("terraform")
    temporary = destination.with_suffix(".download")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o755)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
    temporary.chmod(0o755)
    temporary.replace(destination)
    print(f"Terraform {VERSION} installed into ignored .tools")


if __name__ == "__main__":
    main()
