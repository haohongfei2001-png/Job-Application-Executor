from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceRef(BaseModel):
    kind: str
    path: str | None = None
    locator: str | None = None
    verified_at: str | None = None
    note: str | None = None


class ProfileField(BaseModel):
    value: Any = None
    sources: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = 1.0
    last_verified: str | None = None
    aliases: list[str] = Field(default_factory=list)
    normalization: dict[str, Any] = Field(default_factory=dict)
    user_confirmed: bool = False
    sensitive: bool = False


class AssetRef(BaseModel):
    path: str
    kind: str
    sha256: str | None = None
    source: EvidenceRef | None = None


class ApplicantProfile(BaseModel):
    schema_version: str = "1.0"
    generated_at: str = Field(default_factory=utc_now)
    fields: dict[str, ProfileField] = Field(default_factory=dict)
    collections: dict[str, Any] = Field(default_factory=dict)
    assets: dict[str, AssetRef] = Field(default_factory=dict)
    source_documents: list[EvidenceRef] = Field(default_factory=list)


class WebField(BaseModel):
    field_id: str
    selector: str
    label: str = ""
    input_type: str = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    current_value: Any = None
    page_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    KEEP_EXISTING = "KEEP_EXISTING"
    UNRESOLVED = "UNRESOLVED"
    USER_CONFIRMATION = "USER_CONFIRMATION"


class FieldResolution(BaseModel):
    field_id: str
    selector: str
    label: str
    scope_sha256: str | None = None
    canonical_key: str | None = None
    record_id: str | None = None
    status: ResolutionStatus
    value: Any = None
    source: str | None = None
    confidence: float = 0.0
    reason: str = ""
    required: bool = False
    sensitive: bool = False


class ApplicationStage(StrEnum):
    DISCOVERED = "DISCOVERED"
    PROFILE_RESOLVED = "PROFILE_RESOLVED"
    FORM_FILLED = "FORM_FILLED"
    VALIDATED = "VALIDATED"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED = "SUBMITTED"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"
    NEEDS_USER_ACTION = "NEEDS_USER_ACTION"
    CANCELLED = "CANCELLED"


class ApplicationPlan(BaseModel):
    execution_id: str
    target_url: str
    site_id: str
    created_at: str = Field(default_factory=utc_now)
    stage: ApplicationStage = ApplicationStage.DISCOVERED
    submit_authorized: bool = False
    fields: list[FieldResolution] = Field(default_factory=list)
    unresolved_fields: list[FieldResolution] = Field(default_factory=list)
    attachments: dict[str, str] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    ok: bool
    missing_required: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SubmissionVerification(BaseModel):
    verified: bool
    level: str = "none"
    application_id: str | None = None
    status: str | None = None
    timestamp: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
