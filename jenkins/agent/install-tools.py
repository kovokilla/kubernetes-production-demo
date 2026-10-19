import hashlib
import json
from pathlib import Path
import platform
import urllib.request

arch = {"aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]


def install(name, url, checksum):
    binary = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(binary).hexdigest() != checksum:
        raise SystemExit(f"{name}: checksum verification failed")
    target = Path("/usr/local/bin") / name
    target.write_bytes(binary)
    target.chmod(0o755)


url = f"https://dl.k8s.io/release/v1.34.0/bin/linux/{arch}/kubectl"
checksum = urllib.request.urlopen(url + ".sha256", timeout=30).read().decode().strip()
install("kubectl", url, checksum)
release = json.load(urllib.request.urlopen(
    "https://api.github.com/repos/kubernetes-sigs/kind/releases/tags/v0.33.0", timeout=30))
asset = next(item for item in release["assets"] if item["name"] == f"kind-linux-{arch}")
if not asset.get("digest", "").startswith("sha256:"):
    raise SystemExit("kind release has no authoritative SHA256 digest")
install("kind", asset["browser_download_url"], asset["digest"].split(":", 1)[1])
