#!/usr/bin/env python3
"""Fetch a checksum-verified official kubeconform release into project-local .tools."""
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request

version = "v0.7.0"
system = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
name = f"kubeconform-{system}-{arch}.tar.gz"
base = f"https://github.com/yannh/kubeconform/releases/download/{version}/"
checksums = urllib.request.urlopen(base + "CHECKSUMS", timeout=30).read().decode()
expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == name)
archive = urllib.request.urlopen(base + name, timeout=60).read()
if hashlib.sha256(archive).hexdigest() != expected:
    raise SystemExit("kubeconform checksum verification failed")
destination = Path(__file__).resolve().parents[1] / ".tools" / "kubeconform"
destination.parent.mkdir(exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as release:
    binary = release.extractfile("kubeconform").read()
destination.write_bytes(binary)
destination.chmod(0o755)
print(f"Verified kubeconform {version}: {destination}")
