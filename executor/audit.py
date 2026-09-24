from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ApplicationPlan, FieldResolution, utc_now
from .profile import masked_preview
from .facts.answers import ExecutionAnswerStore


ROOT = Path.home() / "Job-Application-Executor" / "applications"
SECRET_KEY_FRAGMENTS = (
    "password", "cookie", "authorization", "secret", "access_token",
    "refresh_token", "api_key", "otp", "id_number", "id_card", "credentials_no",
)


def redact_secrets(value: Any, key: str = "") -> Any:
    low = key.casefold()
    if low == "code" or any(fragment in low for fragment in SECRET_KEY_FRAGMENTS):
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
    # Names of local files and project records can contain applicant facts.
    # Recovery reconstructs the profile; the persisted audit is copy-safe.
    data["attachments"] = {k: "[LOCAL_FILE]" for k in plan.attachments}
    review = data["metadata"].get("final_review")
    if isinstance(review, dict):
        coverage = review.get("project_coverage")
        coverage = coverage if isinstance(coverage, dict) else {}
        data["metadata"]["final_review"] = {
            "human_review_required": review.get("human_review_required") is True,
            "final_click_actor": "user",
            "unresolved_field_count": len(plan.unresolved_fields),
            "attachment_count": len(plan.attachments),
            "project_coverage": {
                "status": coverage.get("status"),
                "canonical_count": coverage.get("canonical_count"),
                "structured_count": coverage.get("structured_count"),
                "uncovered_count": len(coverage.get("uncovered_projects") or []),
                "explicit_exclusion_count": len(
                    coverage.get("explicit_exclusions") or []),
            },
        }
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
        self.answer_store = ExecutionAnswerStore(self.root)

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
        if self.user_answers_path.is_file():
            data = json.loads(self.user_answers_path.read_text(encoding="utf-8"))
            items = data.get("answers") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise ValueError("legacy execution answers are malformed")
            for item in items:
                self.answer_store.save(item)
            # Only remove the raw predecessor after every value is readable
            # from the encrypted journal. A crash before unlink retries safely.
            loaded = self.answer_store.load()
            expected = {
                self.answer_store._match_key(item): item for item in items
            }
            observed = {
                self.answer_store._match_key(item): item for item in loaded
            }
            if any(observed.get(key) != value for key, value in expected.items()):
                raise RuntimeError("execution answer migration incomplete")
            self.user_answers_path.unlink()
        return self.answer_store.load()

    def add_user_answer(self, answer: dict[str, Any]) -> Path:
        self.load_user_answers()
        return self.answer_store.save(answer)

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
        # Real pages may contain applicant facts, OTPs, cookies or QR codes.
        # No default screenshot path is safe for this legacy audit store.
        raise RuntimeError("automatic screenshots are disabled")
