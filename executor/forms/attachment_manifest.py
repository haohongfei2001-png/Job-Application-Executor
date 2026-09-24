"""Private, atomic checkpoint of proven attachment slots for later pages.

Only hashes and a server draft revision are stored. Loading a checkpoint never
authorizes replay of a file selection; the site driver must read back the draft.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Mapping


class AttachmentManifestError(RuntimeError):
    pass


_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _scope(target_url: str, execution_id: str) -> str:
    return hashlib.sha256(json.dumps([target_url, execution_id],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def load_attachment_manifest(path: str | Path, *, target_url: str,
                             execution_id: str, assets: Mapping[str, object]) -> dict | None:
    checkpoint = Path(path)
    try:
        descriptor = os.open(checkpoint, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise AttachmentManifestError("attachment checkpoint unavailable") from None
    try:
        if os.fstat(descriptor).st_size > 4096:
            raise AttachmentManifestError("attachment checkpoint invalid")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            value = json.load(stream)
    except (OSError, ValueError, UnicodeError):
        raise AttachmentManifestError("attachment checkpoint invalid") from None
    finally:
        if descriptor != -1:
            os.close(descriptor)
    if (not isinstance(value, dict) or value.get("schema") != 1
            or value.get("scope_digest") != _scope(target_url, execution_id)
            or not isinstance(value.get("draft_id_digest"), str)
            or not _HASH.fullmatch(value["draft_id_digest"])
            or not isinstance(value.get("revision"), int)
            or isinstance(value.get("revision"), bool)
            or value["revision"] < 1
            or not isinstance(value.get("slots"), dict)
            or not value["slots"]
            or set(value["slots"]) - {"resume", "photo"}):
        raise AttachmentManifestError("attachment checkpoint scope or schema invalid")
    for slot, expected_hash in value["slots"].items():
        asset = assets.get(slot)
        if (not isinstance(expected_hash, str) or not _HASH.fullmatch(expected_hash)
                or not isinstance(asset, Mapping)
                or asset.get("sha256") != expected_hash):
            raise AttachmentManifestError("attachment canonical hash changed")
    return value


def save_attachment_manifest(path: str | Path, *, target_url: str,
                             execution_id: str, assets: Mapping[str, object],
                             slots: tuple[str, ...], draft_id_digest: str,
                             revision: int) -> None:
    if (not slots or len(set(slots)) != len(slots)
            or set(slots) - {"resume", "photo"}
            or not _HASH.fullmatch(draft_id_digest)
            or not isinstance(revision, int) or isinstance(revision, bool)
            or revision < 1):
        raise AttachmentManifestError("attachment checkpoint evidence invalid")
    selected = {}
    for slot in slots:
        asset = assets.get(slot)
        expected_hash = asset.get("sha256") if isinstance(asset, Mapping) else None
        if not isinstance(expected_hash, str) or not _HASH.fullmatch(expected_hash):
            raise AttachmentManifestError("attachment canonical hash unavailable")
        selected[slot] = expected_hash
    prior = load_attachment_manifest(path, target_url=target_url,
                                     execution_id=execution_id, assets=assets)
    if prior:
        if prior["draft_id_digest"] != draft_id_digest or revision < prior["revision"]:
            raise AttachmentManifestError("attachment draft identity or revision changed")
        selected = {**prior["slots"], **selected}
    value = {"schema": 1, "scope_digest": _scope(target_url, execution_id),
             "draft_id_digest": draft_id_digest, "revision": revision,
             "slots": selected}
    checkpoint = Path(path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(checkpoint.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".attachment-", dir=checkpoint.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, checkpoint)
        directory = os.open(checkpoint.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
