"""Private local value review for an existing task, without live authority.

Returned values are for the authenticated UI only. Never pass this projection to
manager/model context, diagnostics, task details, journals, or browser storage.
"""
from __future__ import annotations

import hashlib

from .profile_setup import PROFILE_LIMIT, validate_profile_text
from .task_preparation import _LocalFile, _Unavailable, prepare_task
from ..preparation.qiyunfang import CONTRACT_VERSION, OBSERVED_AT, map_routine_fields


def review_preparation(queue, task_id, revision):
    report = prepare_task(queue, task_id, revision)
    if not report["contract"]["matched"] or report["profile"]["status"] != "available":
        raise ValueError("preparation review unavailable")
    before = queue.get(task_id)
    if before["revision"] != revision:
        raise RuntimeError("preparation changed")
    with _LocalFile(before["spec"].get("profile_ref"), PROFILE_LIMIT, private=True) as source:
        try:
            raw = source.read()
            if hashlib.sha256(raw).hexdigest() != report["profile"]["version"]:
                raise RuntimeError("preparation changed")
            profile = validate_profile_text(raw.decode("utf-8"))
            proposals = map_routine_fields(profile)
        except (_Unavailable, ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError("preparation review unavailable") from None
        source.fence()
        if queue.get(task_id) != before or prepare_task(queue, task_id, revision) != report:
            raise RuntimeError("preparation changed")
        source.fence()
        return {"mode": "PRIVATE_MAPPING_REVIEW", "state": "AWAITING_LIVE_PREFLIGHT",
                "task_id": task_id, "task_revision": revision,
                "profile_version": report["profile"]["version"],
                "resume_version": report["resume"]["version"],
                "contract_version": CONTRACT_VERSION, "observed_at": OBSERVED_AT,
                "proposals": proposals,
                "capabilities": {"live_write": False, "submit": False,
                                 "account_verified": False, "server_draft_verified": False}}
