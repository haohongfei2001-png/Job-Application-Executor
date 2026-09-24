from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable

from ..models import FieldResolution, WebField


@dataclass(frozen=True)
class ObservedRow:
    section: str
    row_key: str
    field_selectors: tuple[str, ...]
    identity_proven: bool = False


@dataclass(frozen=True, repr=False)
class FormObservation:
    """Actual page state; field values stay in memory and out of repr/receipts."""

    page_url: str
    document_epoch: str
    fields: tuple[WebField, ...]
    sections: tuple[str, ...] = ()
    rows: tuple[ObservedRow, ...] = ()
    dependencies: tuple[tuple[str, str], ...] = ()
    hidden_required_count: int = 0
    unsupported_component_count: int = 0
    ambiguous_selector_count: int = 0
    ambiguous_row_count: int = 0
    validation_error_count: int = 0
    save_status: str = "UNVERIFIED"
    attachment_receipts: tuple[str, ...] = ()
    observed_identity: str = ""
    _structure_digest: str = field(default="", repr=False)

    @classmethod
    def from_fields(cls, page_url: str, document_epoch: str,
                    fields: Iterable[WebField], **kwargs) -> "FormObservation":
        items = tuple(fields)
        structure = [(item.field_id, item.selector, item.input_type,
                      bool(item.required), str(item.metadata.get("section") or ""))
                     for item in items]
        row_structure = [(row.section, row.row_key, row.field_selectors,
                          row.identity_proven) for row in kwargs.get("rows", ())]
        digest = hashlib.sha256(json.dumps(
            [page_url, document_epoch, structure, row_structure], ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest()
        sections = tuple(dict.fromkeys(
            str(item.metadata.get("section") or "") for item in items
            if item.metadata.get("section")))
        return cls(page_url=page_url, document_epoch=document_epoch,
                   fields=items, sections=sections, observed_identity=digest,
                   _structure_digest=digest, **kwargs)

    @property
    def structure_digest(self) -> str:
        return self._structure_digest

    @property
    def unsafe_structure(self) -> bool:
        return bool(self.hidden_required_count or self.unsupported_component_count
                    or self.ambiguous_selector_count or self.ambiguous_row_count)

    def safe_summary(self) -> dict:
        """Copy-safe structural evidence; never serializes values or labels."""
        return {
            "observation_digest": self.structure_digest,
            "field_count": len(self.fields),
            "section_count": len(self.sections),
            "row_count": len(self.rows),
            "hidden_required_count": self.hidden_required_count,
            "unsupported_component_count": self.unsupported_component_count,
            "ambiguous_selector_count": self.ambiguous_selector_count,
            "ambiguous_row_count": self.ambiguous_row_count,
            "validation_error_count": self.validation_error_count,
            "save_status": self.save_status,
            "attachment_receipt_count": len(self.attachment_receipts),
        }


@dataclass(frozen=True, repr=False)
class FillPlan:
    """Actions bound to one observation, not proof of the resulting draft."""

    observation_digest: str
    actions: tuple[FieldResolution, ...]

    @classmethod
    def bind(cls, observation: FormObservation,
             actions: Iterable[FieldResolution]) -> "FillPlan":
        selected = tuple(actions)
        observed = {item.selector for item in observation.fields}
        if any(action.selector not in observed for action in selected):
            raise ValueError("fill plan references unobserved field")
        # Hidden required controls may become visible after a parent choice.
        # They must be re-observed before readiness, but should not prevent a
        # proven unique parent control from being filled.
        if (observation.unsupported_component_count or observation.ambiguous_selector_count
                or observation.ambiguous_row_count):
            raise ValueError("fill plan requires supported unique form structure")
        return cls(observation.structure_digest, selected)
