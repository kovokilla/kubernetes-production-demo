#!/usr/bin/env python3
"""Download an official Helm binary into .tools, verifying the release checksum."""
import hashlib
import io
import platform
from pathlib import Path
import tarfile
import urllib.request

version = "v3.19.0"
system = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
name = f"helm-{version}-{system}-{arch}.tar.gz"
base = "https://get.helm.sh/"
archive = urllib.request.urlopen(base + name, timeout=60).read()
expected = urllib.request.urlopen(base + name + ".sha256sum", timeout=30).read().decode().split()[0]
if hashlib.sha256(archive).hexdigest() != expected:
    raise SystemExit("Helm checksum verification failed")
destination = Path(__file__).resolve().parents[1] / ".tools" / "helm"
destination.parent.mkdir(exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as release:
    binary = release.extractfile(f"{system}-{arch}/helm").read()
destination.write_bytes(binary)
destination.chmod(0o755)
print(f"Verified Helm {version}: {destination}")
