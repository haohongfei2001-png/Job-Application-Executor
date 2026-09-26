"""GitHub Actions prepares a digest-pinned Python runtime, never owner state."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

PIN = {
    ("Darwin", "arm64"): (
        "cpython-3.12.14+20260924-aarch64-apple-darwin-install_only.tar.gz",
        "9763f43db2481a6af36af82ec40302aab7a73632f880129d07a6e81aec846277"),
    ("Linux", "x86_64"): (
        "cpython-3.12.14+20260924-x86_64-unknown-linux-gnu-install_only.tar.gz",
        "5eae8cf79dd47fc2496a4fccc892936be831ce7a84d984b2299dfb1cdb592682"),
}


def prepare(destination: Path, requirements: Path) -> Path:
    name, expected = PIN[(platform.system(), platform.machine())]
    destination.mkdir(parents=True, exist_ok=False)
    url = "https://github.com/astral-sh/python-build-standalone/releases/download/20260924/" + name.replace("+", "%2B")
    with tempfile.TemporaryDirectory(prefix="jae-python-download-") as directory:
        archive = Path(directory) / "runtime.tar.gz"
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
            while data := response.read(1024 * 1024):
                digest.update(data)
                output.write(data)
        if digest.hexdigest() != expected:
            raise ValueError("standalone_archive_digest_mismatch")
        with tarfile.open(archive, "r:gz") as handle:
            # Python's data filter refuses absolute/escaping links and members.
            handle.extractall(destination, filter="data")
    root = destination / "python"
    python = root / "bin" / "python3"
    canonical = root / "bin" / "python"
    if not canonical.exists():
        canonical.symlink_to("python3")
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "--isolated", "install",
                    "--no-deps", "--no-compile", "--no-cache-dir",
                    "--disable-pip-version-check", "-r", str(requirements.resolve())],
                   check=True, timeout=180)
    (root / "python-runtime-provenance.json").write_text(json.dumps({
        "format": "jae-python-upstream-v1",
        "repository": "astral-sh/python-build-standalone",
        "release": "20260924", "asset": name, "archive_sha256": expected,
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
    }, sort_keys=True) + "\n", encoding="utf-8")
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--requirements", type=Path, default=Path("requirements.txt"))
    args = parser.parse_args()
    print(prepare(args.destination, args.requirements))
