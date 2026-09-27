"""Dependency-free owned runtime paths and the existing local authentication token.

Recovery must not import the task schema, worker, browser or model provider.
These helpers retain the existing path, mode, token and fail-closed contracts.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from .release import is_packaged_source

# A packaged release is immutable. Keep task state outside the app bundle so
# replacing or rolling back source/runtime never replaces the applicant journal.
def default_runtime(source_root: Path, *, home: Path | None = None) -> Path:
    source_root = Path(source_root)
    if is_packaged_source(source_root):
        return (home or Path.home()) / "Library" / "Application Support" / "AI投递经理" / "autonomy"
    return source_root / "runtime" / "autonomy"


_SOURCE_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = default_runtime(_SOURCE_ROOT)


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path



def local_token(root):
    token = os.getenv("APPLICATION_EXECUTOR_LOCAL_TOKEN")
    if token:
        if len(token) < 32:
            raise ValueError("local auth token must have at least 32 characters")
        return token
    path = private_dir(root) / "auth.token"
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("private token permissions required")
    token = path.read_text().strip()
    if len(token) < 32:
        raise ValueError("invalid private auth token")
    return token
