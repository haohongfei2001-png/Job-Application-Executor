from __future__ import annotations
from enum import StrEnum
from pydantic import BaseModel, Field
from typing import Any

class RunState(StrEnum):
    IDLE="IDLE"; OPENING="OPENING"; AUTHENTICATING="AUTHENTICATING"; LOCATING_APPLICATION="LOCATING_APPLICATION"
    FORM_FILLING="FORM_FILLING"; WAITING_USER_INPUT="WAITING_USER_INPUT"; WAITING_USER_CONFIRMATION="WAITING_USER_CONFIRMATION"
    SUBMITTING="SUBMITTING"; SUBMITTED="SUBMITTED"; ERROR="ERROR"

class RuntimeState(BaseModel):
    state: RunState = RunState.IDLE
    job_url: str | None = None
    active_url: str | None = None
    profile_path: str | None = None
    resume_path: str | None = None
    last_checkpoint_at: str | None = None
    page_index: int = 0
    filled_fields: list[dict[str,Any]] = Field(default_factory=list)
    unresolved_fields: list[dict[str,Any]] = Field(default_factory=list)
    final_submit_detected: bool = False
    last_error: str | None = None
