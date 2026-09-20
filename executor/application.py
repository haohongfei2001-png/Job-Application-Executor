from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .adapters.registry import adapter_for_url, site_id_for_url
from .audit import AuditStore
from .models import ApplicationPlan, ApplicationStage, FieldResolution, ResolutionStatus, WebField
from .otp.bridge import OtpBridge, OtpBridgeError
from .profile import get_field, is_sensitive_key, load_profile
from .resolver import FieldResolver
from .review import build_final_review


def execution_id_for(target_url: str) -> str:
    host = (urlparse(target_url).hostname or "local").replace(".", "-")
    digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()[:8]
    return f"{host}-{int(time.time())}-{digest}"


class ApplicationExecutor:
    def __init__(
        self,
        target_url: str,
        profile_path: str | Path,
        settings: dict | None = None,
        *,
        submit_authorized: bool = False,
        execution_id: str | None = None,
        otp_bridge: OtpBridge | None = None,
    ):
        self.target_url = target_url
        self.profile_path = Path(profile_path).expanduser().resolve()
        self.profile = load_profile(self.profile_path)
        self.settings = settings or {}
        self.resolver = FieldResolver(self.profile, self.settings)
        assets = self.profile.get("assets") or {}
        self.plan = ApplicationPlan(
            execution_id=execution_id or execution_id_for(target_url),
            target_url=target_url,
            site_id=site_id_for_url(target_url),
            submit_authorized=submit_authorized,
            attachments={
                name: str(asset.get("path"))
                for name, asset in assets.items()
                if isinstance(asset, dict) and asset.get("path")
            },
        )
        self.audit = AuditStore(self.plan.execution_id)
        self.user_answers = self.audit.load_user_answers()
        self.otp_bridge = otp_bridge or OtpBridge()

    @staticmethod
    def _auth_kind(adapter) -> str | None:
        try:
            method = getattr(adapter, "auth_challenge_kind", None)
            if callable(method):
                return method()
            return "other" if adapter.auth_challenge() else None
        except Exception:
            return "other"

    def _block_auth(self, page_index: int, kind: str, reason: str) -> ApplicationPlan:
        self.plan.stage = ApplicationStage.BLOCKED
        self.plan.metadata["block_reason"] = reason
        self.plan.metadata["auth_kind"] = kind
        self.plan.metadata["page_index"] = page_index
        self.audit.save_plan(self.plan)
        return self.plan

    def _resolve_otp_challenge(self, adapter, page_index: int) -> bool:
        if not self.otp_bridge.enabled:
            self._block_auth(page_index, "one_time_code", "one-time-code authentication requires human handling")
            return False

        try:
            field_status = getattr(adapter, "otp_field_status", lambda: "unique")()
        except Exception:
            field_status = "unavailable"
        if field_status != "unique":
            self.audit.record_action({
                "type": "otp_authentication", "source": "iphone_relay", "ok": False,
                "reason": "OTP field unavailable or ambiguous",
            })
            self._block_auth(page_index, "one_time_code", "OTP field unavailable or ambiguous")
            return False

        try:
            hostname = adapter.current_page_hostname()
        except Exception:
            hostname = ""
        if not hostname:
            self._block_auth(page_index, "one_time_code", "OTP page hostname unavailable")
            return False

        try:
            result = self.otp_bridge.wait_for_code(hostname)
        except OtpBridgeError:
            self.audit.record_action({
                "type": "otp_authentication", "source": "iphone_relay", "ok": False,
                "reason": "relay_error",
            })
            self._block_auth(page_index, "one_time_code", "OTP relay error")
            return False
        except Exception:
            self.audit.record_action({
                "type": "otp_authentication", "source": "iphone_relay", "ok": False,
                "reason": "relay_error",
            })
            self._block_auth(page_index, "one_time_code", "OTP relay error")
            return False

        if not result:
            self.audit.record_action({
                "type": "otp_authentication", "source": "iphone_relay", "ok": False,
                "reason": "timeout",
            })
            self._block_auth(page_index, "one_time_code", "OTP relay timed out")
            return False

        if not isinstance(result, dict):
            self.audit.record_action({
                "type": "otp_authentication", "source": "iphone_relay", "ok": False,
                "reason": "relay_error",
            })
            self._block_auth(page_index, "one_time_code", "OTP relay error")
            return False

        reported_source = result.get("source")
        source = reported_source if reported_source in {"iphone_relay", "mac_messages"} else "unknown"
        code = str(result.get("code") or "")
        entered = False
        try:
            if re.fullmatch(r"\d{4,8}", code):
                entered = bool(adapter.enter_one_time_code(code))
        except Exception:
            entered = False
        finally:
            code = ""
        if not entered:
            self.audit.record_action({
                "type": "otp_authentication", "source": source, "ok": False,
                "reason": "OTP field unavailable or ambiguous",
            })
            self._block_auth(page_index, "one_time_code", "OTP field unavailable or ambiguous")
            return False

        remaining_kind = self._auth_kind(adapter)
        confirmation_clicked = False
        if remaining_kind == "one_time_code":
            try:
                confirmed = bool(adapter.confirm_one_time_code_auth())
            except Exception:
                confirmed = False
            self.audit.record_action({
                "type": "otp_authentication_confirmation",
                "source": source,
                "ok": confirmed,
                "reason": None if confirmed else "confirmation_control_unavailable_or_ambiguous",
            })
            if not confirmed:
                self._block_auth(
                    page_index,
                    "one_time_code",
                    "OTP authentication confirmation control unavailable or ambiguous",
                )
                return False
            confirmation_clicked = True
            remaining_kind = self._auth_kind(adapter)

        if remaining_kind is not None:
            self.audit.record_action({
                "type": "otp_authentication", "source": source, "ok": False,
                "reason": "challenge_remains",
            })
            self._block_auth(
                page_index,
                remaining_kind,
                (
                    "authentication challenge remains after OTP confirmation"
                    if confirmation_clicked
                    else "authentication challenge remains after OTP entry"
                ),
            )
            return False

        self.audit.record_action({
            "type": "otp_authentication", "source": source, "ok": True,
        })
        return True

    def _attachment_resolution(self, field: WebField) -> FieldResolution | None:
        if field.input_type != "file":
            return None
        label = (field.label or "").casefold()
        asset_name = None
        if re.search(r"resume|cv|简历", label, re.I):
            asset_name = "resume"
        elif re.search(r"photo|portrait|证件照|照片", label, re.I):
            asset_name = "photo"
        if not asset_name:
            return None
        asset = (self.profile.get("assets") or {}).get(asset_name) or {}
        path = asset.get("path") if isinstance(asset, dict) else None
        if not path or not Path(path).expanduser().is_file():
            return None
        return FieldResolution(
            field_id=field.field_id,
            selector=field.selector,
            label=field.label,
            canonical_key=f"assets.{asset_name}",
            status=ResolutionStatus.RESOLVED,
            value=str(Path(path).expanduser().resolve()),
            source="profile_asset",
            confidence=1.0,
            reason="attachment type matched from field label",
            required=field.required,
        )

    def _user_answer_resolution(
        self,
        field: WebField,
        base: FieldResolution,
    ) -> FieldResolution | None:
        matches = [
            answer for answer in self.user_answers
            if answer.get("selector") == field.selector
        ]
        if not matches:
            matches = [
                answer for answer in self.user_answers
                if not answer.get("selector") and answer.get("field_id") == field.field_id
            ]
        if not matches and base.canonical_key:
            matches = [
                answer for answer in self.user_answers
                if answer.get("canonical_key") == base.canonical_key
            ]
        if not matches:
            return None
        answer = matches[-1]
        canonical_key = answer.get("canonical_key") or base.canonical_key
        return FieldResolution(
            field_id=field.field_id,
            selector=field.selector,
            label=field.label,
            canonical_key=canonical_key,
            status=ResolutionStatus.RESOLVED,
            value=answer.get("value"),
            source="user_execution_answer",
            confidence=1.0,
            reason="explicit user answer for this execution",
            required=field.required,
            sensitive=base.sensitive or is_sensitive_key(canonical_key),
        )

    def _resolve_page(self, fields: list[WebField]) -> list[FieldResolution]:
        resolved: list[FieldResolution] = []
        for field in fields:
            base = self._attachment_resolution(field) or self.resolver.resolve(field)
            resolved.append(self._user_answer_resolution(field, base) or base)
        return resolved

    @staticmethod
    def _blocking_unresolved(items: list[FieldResolution]) -> list[FieldResolution]:
        return [
            item for item in items
            if (
                item.status == ResolutionStatus.USER_CONFIRMATION
                or (item.required and item.status == ResolutionStatus.UNRESOLVED)
            )
        ]

    def _profile_consistency(self) -> dict:
        errors: list[str] = []
        warnings: list[str] = []
        for item in self.plan.fields:
            if item.status == ResolutionStatus.RESOLVED and item.source == "user_execution_answer":
                if item.canonical_key:
                    canonical = get_field(self.profile, item.canonical_key)
                    if canonical and canonical.value != item.value:
                        warnings.append(
                            f"{item.field_id}: explicit execution answer overrides canonical profile"
                        )
                continue
            if item.status == ResolutionStatus.RESOLVED and item.canonical_key:
                if item.canonical_key.startswith("assets."):
                    if not Path(str(item.value)).expanduser().is_file():
                        errors.append(f"{item.field_id}: resolved attachment is missing")
                    continue
                canonical = get_field(self.profile, item.canonical_key)
                if canonical is None or canonical.value in (None, ""):
                    errors.append(f"{item.field_id}: canonical source value is missing")
                elif item.value != canonical.value:
                    errors.append(f"{item.field_id}: resolved value differs from canonical profile")
            elif item.status == ResolutionStatus.KEEP_EXISTING:
                warnings.append(f"{item.field_id}: preserved site-existing value")
            elif item.status == ResolutionStatus.UNRESOLVED and not item.required:
                warnings.append(f"{item.field_id}: optional field remains unresolved")
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def run(self, max_pages: int = 15) -> ApplicationPlan:
        adapter = adapter_for_url(self.target_url)
        with adapter:
            for page_index in range(max_pages):
                auth_kind = self._auth_kind(adapter)
                if auth_kind == "one_time_code":
                    if not self._resolve_otp_challenge(adapter, page_index):
                        return self.plan
                elif auth_kind is not None:
                    return self._block_auth(
                        page_index,
                        auth_kind,
                        f"{auth_kind} authentication requires human handling",
                    )

                fields = adapter.discover_fields()
                if not fields and adapter.start_application():
                    self.audit.record_action({"type": "start_application", "page_index": page_index})
                    continue
                resolutions = self._resolve_page(fields)
                self.plan.fields.extend(resolutions)
                self.plan.unresolved_fields.extend([
                    item for item in resolutions
                    if item.status in {
                        ResolutionStatus.UNRESOLVED,
                        ResolutionStatus.USER_CONFIRMATION,
                    }
                ])
                self.plan.stage = ApplicationStage.PROFILE_RESOLVED
                self.plan.metadata["page_index"] = page_index
                self.audit.save_plan(self.plan)

                actions = adapter.apply_resolutions(resolutions)
                for action in actions:
                    self.audit.record_action({"type": "fill", **action})
                self.plan.stage = ApplicationStage.FORM_FILLED
                self.audit.save_plan(self.plan)

                fill_failures = [action for action in actions if not action.get("ok", False)]
                if fill_failures:
                    if getattr(adapter, "save_draft", lambda: False)():
                        self.audit.record_action({"type": "save_draft", "page_index": page_index})
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "resolved field could not be filled"
                    self.plan.metadata["fill_failures"] = fill_failures
                    self.audit.save_plan(self.plan)
                    return self.plan

                blocking = self._blocking_unresolved(resolutions)
                if blocking:
                    if getattr(adapter, "save_draft", lambda: False)():
                        self.audit.record_action({"type": "save_draft", "page_index": page_index})
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "unresolved fields require user input"
                    self.audit.save_plan(self.plan)
                    try:
                        adapter.screenshot(self.audit.screenshot_path(f"page-{page_index}-blocked"))
                    except Exception:
                        pass
                    return self.plan

                validation = adapter.validate(self.plan)
                self.plan.metadata["validation"] = validation.model_dump(mode="json")
                if not validation.ok:
                    if getattr(adapter, "save_draft", lambda: False)():
                        self.audit.record_action({"type": "save_draft", "page_index": page_index})
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "validation failed"
                    self.audit.save_plan(self.plan)
                    try:
                        adapter.screenshot(self.audit.screenshot_path(f"page-{page_index}-validation"))
                    except Exception:
                        pass
                    return self.plan

                consistency = self._profile_consistency()
                self.plan.metadata["profile_consistency"] = consistency
                if not consistency["ok"]:
                    if getattr(adapter, "save_draft", lambda: False)():
                        self.audit.record_action({"type": "save_draft", "page_index": page_index})
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "profile consistency audit failed"
                    self.audit.save_plan(self.plan)
                    return self.plan

                self.plan.stage = ApplicationStage.VALIDATED
                final_control = adapter.final_submit_control()
                if final_control:
                    self.plan.stage = ApplicationStage.READY_TO_SUBMIT
                    self.plan.metadata["final_submit_control"] = final_control
                    self.plan.metadata["manual_final_click_required"] = True
                    self.plan.metadata["submit_authorized_does_not_allow_final_click"] = bool(
                        self.plan.submit_authorized
                    )
                    self.plan.metadata["final_review"] = build_final_review(
                        self.profile,
                        self.plan,
                        final_control,
                    )
                    self.audit.save_plan(self.plan)
                    try:
                        adapter.screenshot(self.audit.screenshot_path("ready-to-submit"))
                    except Exception:
                        pass
                    return self.plan

                if adapter.advance():
                    self.audit.record_action({"type": "advance", "page_index": page_index})
                    continue

                self.plan.stage = ApplicationStage.BLOCKED
                self.plan.metadata["block_reason"] = "no next or final-submit control found"
                self.audit.save_plan(self.plan)
                return self.plan

            self.plan.stage = ApplicationStage.BLOCKED
            self.plan.metadata["block_reason"] = "max page limit reached"
            self.audit.save_plan(self.plan)
            return self.plan
