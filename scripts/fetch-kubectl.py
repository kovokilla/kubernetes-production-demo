#!/usr/bin/env python3
"""Download a checksum-verified kubectl matching the pinned cluster into .tools."""
import hashlib
from pathlib import Path
import platform
import urllib.request

version = "v1.34.0"
system = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
url = f"https://dl.k8s.io/release/{version}/bin/{system}/{arch}/kubectl"
binary = urllib.request.urlopen(url, timeout=90).read()
checksum = urllib.request.urlopen(url + ".sha256", timeout=30).read().decode().strip()
if hashlib.sha256(binary).hexdigest() != checksum:
    raise SystemExit("kubectl checksum verification failed")
destination = Path(__file__).resolve().parents[1] / ".tools" / "kubectl"
destination.parent.mkdir(exist_ok=True)
destination.write_bytes(binary)
destination.chmod(0o755)
print(f"Verified kubectl {version}: {destination}")
