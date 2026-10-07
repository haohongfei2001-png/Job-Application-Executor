"""GitHub Actions prepares a digest-pinned Python runtime, never owner state."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import platform
import subprocess
import sys
import tarfile
import tempfile
import time
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
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_ATTEMPTS = 3
DISCONNECTED_EXIT = 75


def _download_once(archive: Path, timeout: float) -> None:
    name, _ = PIN[(platform.system(), platform.machine())]
    url = "https://github.com/astral-sh/python-build-standalone/releases/download/20260924/" + name.replace("+", "%2B")
    with urllib.request.urlopen(url, timeout=timeout) as response, archive.open("xb") as output:
        while data := response.read(1024 * 1024):
            output.write(data)


def _download_archive(archive: Path) -> None:
    # One allowance for all attempts, including connection and blocked reads.
    # The child only downloads; the parent verifies bytes before extraction.
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    for attempt in range(DOWNLOAD_ATTEMPTS):
        with tempfile.TemporaryDirectory(prefix="attempt-", dir=archive.parent) as directory:
            partial = Path(directory) / "runtime.tar.gz"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("standalone_download_deadline")
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                       "--download-once", str(partial), str(remaining)]
            with subprocess.Popen(command) as child:
                try:
                    # Debit process creation from the same deadline, too.
                    result = child.wait(timeout=max(0, deadline - time.monotonic()))
                except BaseException:
                    # This worker creates no descendants. Reap only our child.
                    child.kill()
                    child.wait()
                    raise
            if time.monotonic() >= deadline:
                raise TimeoutError("standalone_download_deadline")
            if result == DISCONNECTED_EXIT:
                if attempt + 1 == DOWNLOAD_ATTEMPTS:
                    raise http.client.RemoteDisconnected("standalone_download_attempts_exhausted")
                continue
            if result:
                raise subprocess.CalledProcessError(result, command)
            partial.replace(archive)
            return


def prepare(destination: Path, requirements: Path) -> Path:
    name, expected = PIN[(platform.system(), platform.machine())]
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="jae-python-download-") as directory:
        archive = Path(directory) / "runtime.tar.gz"
        _download_archive(archive)
        with archive.open("rb") as downloaded:
            digest = hashlib.file_digest(downloaded, "sha256")
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


def main() -> int:
    if sys.argv[1:2] == ["--download-once"]:
        parser = argparse.ArgumentParser()
        parser.add_argument("archive", type=Path)
        parser.add_argument("timeout", type=float)
        args = parser.parse_args(sys.argv[2:])
        try:
            _download_once(args.archive, args.timeout)
        except http.client.RemoteDisconnected:
            return DISCONNECTED_EXIT
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--requirements", type=Path, default=Path("requirements.txt"))
    args = parser.parse_args()
    print(prepare(args.destination, args.requirements))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
