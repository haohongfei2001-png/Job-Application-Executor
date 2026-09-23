from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommandEnvelope(BaseModel):
    """Local deterministic control, independent of model availability."""

    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{8,120}$")
    task_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    action: Literal["PAUSE", "RESUME", "CANCEL"]
    expected_revision: int = Field(ge=0)
