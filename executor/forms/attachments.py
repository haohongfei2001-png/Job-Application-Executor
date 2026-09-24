"""Check independent, draft-bound upload readback from a certified site driver.

The driver owns the upload and obtains the receipt from its site service. A
browser file-input value, filename, or success toast is never such a receipt.
Only aggregate proof metadata may leave this module for task/audit storage.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

from ..models import FieldResolution, ResolutionStatus


class AttachmentProofMissing(RuntimeError):
    pass


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def verify_attachment_readback(
    target_url: str,
    resolutions: Sequence[FieldResolution],
    canonical_assets: Mapping[str, object],
    receipts: object,
) -> dict:
    """Require one retained server receipt per selected canonical asset slot."""
    selected = [item for item in resolutions
                if item.status == ResolutionStatus.RESOLVED
                and item.canonical_key in {"assets.resume", "assets.photo"}]
    if not selected:
        return {"verified": True, "slot_count": 0}
    if not isinstance(receipts, (list, tuple)) or len(receipts) != len(selected):
        raise AttachmentProofMissing("attachment completion receipts unavailable")
    by_slot: dict[str, Mapping] = {}
    for receipt in receipts:
        if not isinstance(receipt, Mapping) or receipt.get("slot") not in {"resume", "photo"}:
            raise AttachmentProofMissing("attachment receipt slot invalid")
        slot = receipt["slot"]
        if slot in by_slot:
            raise AttachmentProofMissing("duplicate attachment receipt slot")
        by_slot[slot] = receipt
    draft_ids: set[str] = set()
    asset_ids: set[str] = set()
    revisions: list[int] = []
    expected_target = _sha(target_url)
    for item in selected:
        slot = item.canonical_key.removeprefix("assets.")
        asset = canonical_assets.get(slot)
        receipt = by_slot.get(slot)
        if not isinstance(asset, Mapping) or receipt is None:
            raise AttachmentProofMissing("attachment slot has no canonical receipt")
        expected_file_hash = asset.get("sha256")
        revision = receipt.get("draft_revision")
        draft_id = receipt.get("draft_id_digest")
        asset_id = receipt.get("asset_id_digest")
        if (not isinstance(expected_file_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_file_hash)
                or receipt.get("file_sha256") != expected_file_hash
                or receipt.get("target_sha256") != expected_target
                or receipt.get("source") != "server_readback"
                or receipt.get("upload_complete") is not True
                or receipt.get("retained_in_draft") is not True
                or not isinstance(revision, int) or isinstance(revision, bool)
                or revision < 1
                or not isinstance(draft_id, str)
                or not re.fullmatch(r"[0-9a-f]{64}", draft_id)
                or not isinstance(asset_id, str)
                or not re.fullmatch(r"[0-9a-f]{64}", asset_id)):
            raise AttachmentProofMissing("attachment completion or draft binding unverified")
        draft_ids.add(draft_id)
        asset_ids.add(asset_id)
        revisions.append(revision)
    if len(draft_ids) != 1 or len(asset_ids) != len(selected):
        raise AttachmentProofMissing("attachment draft or asset identity ambiguous")
    return {"verified": True, "slot_count": len(selected),
            "minimum_draft_revision": min(revisions), "level": "server_readback",
            "_draft_id_digest": next(iter(draft_ids))}
