from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ApplicationPlan, FieldResolution, utc_now
from .profile import masked_preview


ROOT = Path.home() / "Job-Application-Executor" / "applications"
SECRET_KEY_FRAGMENTS = (
    "password", "cookie", "authorization", "secret", "access_token",
    "refresh_token", "api_key", "otp", "id_number", "id_card", "credentials_no",
)


def redact_secrets(value: Any, key: str = "") -> Any:
    low = key.casefold()
    if any(fragment in low for fragment in SECRET_KEY_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact_secrets(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    return value


def _safe_resolution(item: FieldResolution) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    if data.get("value") not in (None, "", []):
        key = data.get("canonical_key") or data.get("field_id") or ""
        data["value"] = masked_preview(key, data["value"])
    return data


def safe_plan(plan: ApplicationPlan) -> dict[str, Any]:
    data = plan.model_dump(mode="json")
    data["fields"] = [_safe_resolution(x) for x in plan.fields]
    data["unresolved_fields"] = [_safe_resolution(x) for x in plan.unresolved_fields]
    data["attachments"] = {k: Path(v).name for k, v in plan.attachments.items()}
    return redact_secrets(data)


class AuditStore:
    def __init__(self, execution_id: str):
        ROOT.mkdir(parents=True, exist_ok=True)
        try:
            ROOT.chmod(0o700)
        except OSError:
            pass
        self.root = ROOT / execution_id
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self.actions_path = self.root / "actions.jsonl"
        self.user_answers_path = self.root / "user-answers.json"

    def save_plan(self, plan: ApplicationPlan) -> Path:
        path = self.root / "plan.json"
        path.write_text(json.dumps(safe_plan(plan), ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)
        return path

    def load_plan(self) -> ApplicationPlan:
        path = self.root / "plan.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        return ApplicationPlan.model_validate_json(path.read_text(encoding="utf-8"))

    def load_user_answers(self) -> list[dict[str, Any]]:
        if not self.user_answers_path.is_file():
            return []
        data = json.loads(self.user_answers_path.read_text(encoding="utf-8"))
        items = data.get("answers") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def add_user_answer(self, answer: dict[str, Any]) -> Path:
        items = self.load_user_answers()
        selector = answer.get("selector")
        field_id = answer.get("field_id")
        canonical_key = answer.get("canonical_key")
        items = [
            item for item in items
            if not (
                (selector and item.get("selector") == selector)
                or (not selector and field_id and item.get("field_id") == field_id)
                or (not selector and not field_id and canonical_key and item.get("canonical_key") == canonical_key)
            )
        ]
        items.append(answer)
        self.user_answers_path.write_text(
            json.dumps({"answers": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.user_answers_path.chmod(0o600)
        return self.user_answers_path

    def record_action(self, action: dict[str, Any]) -> None:
        item = redact_secrets({"at": utc_now(), **action})
        with self.actions_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.actions_path.chmod(0o600)

    def save_receipt(self, payload: dict[str, Any]) -> Path:
        path = self.root / "submit-receipt.json"
        safe = redact_secrets(payload)
        path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)
        return path

    def screenshot_path(self, name: str) -> str:
        return str(self.root / "evidence" / f"{name}.png")
