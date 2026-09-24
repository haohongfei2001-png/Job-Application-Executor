"""Independent, read-only evidence for the human final-submit boundary.

The adapter supplies a complete server draft inventory. Browser DOM values or
the fill plan are never treated as proof of persistence. Values are compared in
memory and are absent from the copy-safe certificate.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .models import ApplicationPlan, ResolutionStatus
from .profile import get_field


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReviewUnverified(ValueError):
    """No READY certificate can be issued for the observed draft."""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_account_digest(profile: dict[str, Any], *, live: bool) -> str:
    """Bind the observed active account to one canonical identity value.

    An applicant name is sufficient only for isolated synthetic fixtures.
    A real account needs a canonical email or phone; unknown account aliases
    must be reviewed instead of silently using another signed-in session.
    """
    keys = ("identity.email", "identity.phone") if live else (
        "identity.email", "identity.phone", "identity.full_name")
    for key in keys:
        field = get_field(profile, key)
        if field is not None and isinstance(field.value, str) and field.value.strip():
            return _digest(f"{key}:{field.value.strip().casefold()}")
    raise ReviewUnverified("canonical account identity unavailable")


@dataclass(frozen=True)
class ReviewCertificate:
    target_sha256: str
    draft_id_digest: str
    revision: int
    document_epoch: str
    driver_version: str
    profile_version: str
    plan_binding_digest: str
    field_count: int
    attachment_count: int
    row_count: int
    checks: tuple[tuple[str, str], ...]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "target_sha256": self.target_sha256,
            "draft_id_digest": self.draft_id_digest,
            "revision": self.revision,
            "document_epoch": self.document_epoch,
            "driver_version": self.driver_version,
            "field_count": self.field_count,
            "attachment_count": self.attachment_count,
            "row_count": self.row_count,
            "checks": dict(self.checks),
            "final_click_actor": "user",
        }


def certify_review(
    profile: dict[str, Any],
    plan: ApplicationPlan,
    snapshot: dict[str, Any] | None,
    *,
    expected_draft_id_digest: str | None = None,
    expected_account_identity_digest: str | None = None,
    expected_rows: dict[str, dict[str, str]] | None = None,
    expected_attachments: dict[str, str] | None = None,
    minimum_revision: int = 0,
    profile_version: str | None = None,
) -> ReviewCertificate:
    """Compare a certified driver's complete read-only draft with canonical intent.

    `snapshot["fields"]` is a complete ordered inventory of records carrying
    page index, selector and the actual retained value. It comes from a site
    readback, not `plan.fields` or the live DOM. The driver must declare
    complete page/required coverage and an authenticated account binding.
    """
    if not isinstance(snapshot, dict) or snapshot.get("source") != "server_readback":
        raise ReviewUnverified("independent review readback unavailable")
    target = _digest(plan.target_url)
    draft = snapshot.get("draft_id_digest")
    revision = snapshot.get("revision")
    if (snapshot.get("target_sha256") != target
            or not isinstance(draft, str) or not _SHA256.fullmatch(draft)
            or (expected_draft_id_digest and draft != expected_draft_id_digest)
            or type(revision) is not int or revision < max(1, minimum_revision)):
        raise ReviewUnverified("review target or draft identity unverified")
    if (not snapshot.get("account_verified") is True
            or not isinstance(snapshot.get("account_identity_digest"), str)
            or not _SHA256.fullmatch(snapshot["account_identity_digest"])
            or not isinstance(expected_account_identity_digest, str)
            or snapshot["account_identity_digest"] != expected_account_identity_digest):
        raise ReviewUnverified("active account unverified")
    if (snapshot.get("complete_pages") is not True
            or snapshot.get("complete_required") is not True
            or snapshot.get("save_status") != "VERIFIED"
            or type(snapshot.get("validation_error_count")) is not int
            or snapshot["validation_error_count"] != 0
            or type(snapshot.get("hidden_required_count")) is not int
            or snapshot["hidden_required_count"] != 0
            or type(snapshot.get("unverified_default_count")) is not int
            or snapshot["unverified_default_count"] != 0):
        raise ReviewUnverified("mandatory review inventory incomplete")
    epoch, version = snapshot.get("document_epoch"), snapshot.get("driver_version")
    if (not isinstance(epoch, str) or not epoch
            or not isinstance(version, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", version)):
        raise ReviewUnverified("review observer version or session unavailable")
    if plan.unresolved_fields:
        raise ReviewUnverified("unresolved application fields")

    actual = snapshot.get("fields")
    expected = [f for f in plan.fields if f.status in {
        ResolutionStatus.RESOLVED, ResolutionStatus.KEEP_EXISTING}
        and not (f.canonical_key or "").startswith("assets.")]
    if not isinstance(actual, list) or len(actual) != len(expected):
        raise ReviewUnverified("current field inventory differs from plan")
    for index, (field, observed) in enumerate(zip(expected, actual)):
        if (not isinstance(observed, dict)
                or observed.get("index") != index
                or observed.get("selector") != field.selector
                or observed.get("field_id") != field.field_id
                or observed.get("required") is not field.required
                or json.dumps(observed.get("value"), ensure_ascii=False, sort_keys=True, allow_nan=False) != json.dumps(field.model_dump(mode="json")["value"], ensure_ascii=False, sort_keys=True, allow_nan=False)):
            raise ReviewUnverified("current draft field differs from canonical plan")
        if field.status == ResolutionStatus.KEEP_EXISTING and observed.get(
                "default_confirmed") is not True:
            raise ReviewUnverified("unconfirmed site default")

    assets = profile.get("assets") or {}
    actual_assets = snapshot.get("attachments")
    expected_assets = expected_attachments if expected_attachments is not None else {
        field.canonical_key.removeprefix("assets."): assets.get(
            field.canonical_key.removeprefix("assets."), {}).get("sha256")
        for field in plan.fields if field.status == ResolutionStatus.RESOLVED
        and field.canonical_key
        and field.canonical_key.startswith("assets.")
    }
    if (not isinstance(actual_assets, dict) or
            set(actual_assets) != set(expected_assets) or
            any(not isinstance(sha, str) or not _SHA256.fullmatch(sha)
                or actual_assets.get(slot) != sha
                for slot, sha in expected_assets.items())):
        raise ReviewUnverified("attachment readback differs from canonical file")

    rows = snapshot.get("rows")
    expected_rows = expected_rows or {}
    if not isinstance(rows, dict) or set(rows) != set(expected_rows):
        raise ReviewUnverified("structured row inventory unverified")
    for collection, records in expected_rows.items():
        actual_records = rows.get(collection)
        if (not isinstance(actual_records, list)
                or len(actual_records) != len(records)
                or not all(isinstance(item, dict)
                           and isinstance(item.get("record_id"), str)
                           and isinstance(item.get("values_digest"), str)
                           and _SHA256.fullmatch(item["values_digest"])
                           for item in actual_records)
                or {item["record_id"]: item["values_digest"]
                    for item in actual_records} != records):
            raise ReviewUnverified("structured row values differ from canonical records")
    projects = (profile.get("collections") or {}).get("projects") or []
    exclusions = ((profile.get("collections") or {}).get("fact_exclusions") or {}).get(
        plan.execution_id, {})
    required_projects = {
        item.get("id") for item in projects if isinstance(item, dict)
        and not (isinstance(exclusions, dict)
                 and isinstance(exclusions.get(item.get("id")), dict)
                 and exclusions[item["id"]].get("source") == "user_explicit_task"
                 and str(exclusions[item["id"]].get("reason") or "").strip())
    }
    if (None in required_projects or
            not required_projects.issubset({
                item["record_id"] for item in rows.get("projects", [])})):
        raise ReviewUnverified("canonical project rows unverified")

    profile_version = profile_version or profile.get("generated_at")
    if not isinstance(profile_version, str) or not profile_version:
        raise ReviewUnverified("profile version unavailable")
    # Keep this binding in memory only. The copy-safe summary never exposes
    # a digest of low-entropy applicant answers or declarations.
    plan_binding_digest = _digest(json.dumps({
        "target": plan.target,
        "fields": [field.model_dump(mode="json") for field in plan.fields],
        "rows": expected_rows,
        "attachments": expected_assets,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    checks = (
        ("target_account_draft", "PASS"),
        ("complete_fields_defaults", "PASS"),
        ("structured_rows", "PASS"),
        ("attachments", "PASS"),
        ("validation_save", "PASS"),
        ("manual_submit_boundary", "PASS"),
    )
    return ReviewCertificate(
        target, draft, revision, epoch, version, profile_version,
        plan_binding_digest, len(actual), len(actual_assets), sum(len(ids) for ids in rows.values()),
        checks,
    )


def recheck_review(
    certificate: ReviewCertificate,
    profile: dict[str, Any],
    plan: ApplicationPlan,
    snapshot: dict[str, Any] | None,
    *,
    profile_version: str | None = None,
    expected_account_identity_digest: str | None = None,
    expected_rows: dict[str, dict[str, str]] | None = None,
    expected_attachments: dict[str, str] | None = None,
) -> ReviewCertificate:
    """Only a fresh read-only observation can preserve READY after a change."""
    current = certify_review(
        profile, plan, snapshot,
        expected_draft_id_digest=certificate.draft_id_digest,
        expected_account_identity_digest=expected_account_identity_digest,
        expected_rows=expected_rows,
        expected_attachments=expected_attachments,
        minimum_revision=certificate.revision,
        profile_version=profile_version,
    )
    if (current.target_sha256 != certificate.target_sha256
            or current.revision != certificate.revision
            or current.document_epoch != certificate.document_epoch
            or current.driver_version != certificate.driver_version
            or current.profile_version != certificate.profile_version
            or current.plan_binding_digest != certificate.plan_binding_digest
            or current.field_count != certificate.field_count
            or current.attachment_count != certificate.attachment_count
            or current.row_count != certificate.row_count):
        raise ReviewUnverified("review certificate dependencies changed")
    return current
