from __future__ import annotations

import hashlib
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .adapters.registry import adapter_for_url, site_id_for_url
from .browser import BrowserOwnershipError, browser_mode
from .audit import AuditStore
from .forms import FillPlan, FormObservationError
from .forms.attachments import verify_attachment_readback
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
        audit_store=None,
        guard=None,
        resume_url: str | None = None,
        existing_browser_only: bool = False,
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
        self.audit = audit_store or AuditStore(self.plan.execution_id)
        self.guard = guard or (lambda: None)
        self.resume_url = resume_url
        self.existing_browser_only = existing_browser_only
        self.user_answers = self.audit.load_user_answers()
        self.otp_bridge = otp_bridge or OtpBridge()

    @staticmethod
    def _auth_kind(adapter) -> str | None:
        try:
            method = getattr(adapter, "auth_challenge_kind", None)
            if callable(method):
                return method()
            return "other" if adapter.auth_challenge() else None
        except BrowserOwnershipError:
            raise
        except Exception:
            return "other"

    def _block_auth(self, page_index: int, kind: str, reason: str) -> ApplicationPlan:
        self.plan.stage = ApplicationStage.BLOCKED
        self.plan.metadata["block_reason"] = reason
        self.plan.metadata["auth_kind"] = kind
        self.plan.metadata["page_index"] = page_index
        self.audit.save_plan(self.plan)
        return self.plan

    def _auth_return_target_verified(self, adapter) -> bool:
        page = getattr(adapter, "page", None)
        current = getattr(page, "url", None)
        if not isinstance(current, str):
            return False
        actual = urlparse(current)
        expected = urlparse(self.target_url)
        return ((actual.scheme, actual.netloc, actual.path, actual.query, actual.fragment) ==
                (expected.scheme, expected.netloc, expected.path, expected.query,
                 expected.fragment))

    def _resolve_otp_challenge(self, adapter, page_index: int) -> bool:
        if not self.otp_bridge.enabled:
            self._block_auth(page_index, "one_time_code", "one-time-code authentication requires human handling")
            return False

        try:
            hostname = adapter.current_page_hostname()
        except Exception:
            hostname = ""
        if not hostname:
            self._block_auth(page_index, "one_time_code", "OTP page hostname unavailable")
            return False
        begin_attempt = getattr(self.otp_bridge, "begin_attempt", None)
        attempt = None
        if callable(begin_attempt):
            try:
                attempt = begin_attempt(hostname)
            except (RuntimeError, ValueError):
                self._block_auth(page_index, "one_time_code", "authentication attempt or target unverified")
                return False
        elif browser_mode() not in {"isolated", "test", "headless"}:
            self._block_auth(page_index, "one_time_code", "durable authentication journal unavailable")
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

        phone_field = get_field(self.profile, "identity.phone")
        phone = (
            str(phone_field.value)
            if phone_field is not None and phone_field.value not in (None, "")
            else None
        )
        auth_terms = get_field(self.profile, "policy.auto_accept_privacy_terms")
        allow_standard_auth_terms = bool(
            auth_terms is not None
            and auth_terms.user_confirmed
            and auth_terms.value is True
        )
        try:
            if attempt and attempt["send_outcome"] != "PREPARED":
                # A pre-click unknown-effect marker survives a crash. Never
                # infer that a second click is safe from page appearance.
                prepare = "already_requested"
            else:
                send_callbacks = (
                    {"before_send": self.otp_bridge.before_send,
                     "after_send": self.otp_bridge.after_send,
                     "authorized_resend": bool(attempt.get("resend_parent_id"))}
                    if attempt else {}
                )
                prepare = getattr(
                    adapter,
                    "prepare_one_time_code_auth",
                    lambda *_args, **_kwargs: "not_needed",
                )(
                    phone,
                    allow_standard_auth_terms=allow_standard_auth_terms,
                    **send_callbacks,
                )
                if attempt and prepare == "not_needed":
                    if browser_mode() in {"isolated", "test", "headless"}:
                        self.otp_bridge.observe_existing()
                    else:
                        prepare = "external_request_unverified"
        except BrowserOwnershipError:
            raise
        except Exception:
            prepare = "ambiguous"
        self.audit.record_action({
            "type": "otp_authentication_prepare",
            "ok": prepare in {"not_needed", "requested", "already_requested"},
            "status": prepare,
        })
        if prepare == "phone_required":
            self._block_auth(
                page_index,
                "sms_setup",
                "SMS authentication requires a canonical phone number",
            )
            return False
        if prepare == "consent_required":
            self._block_auth(
                page_index,
                "sms_setup",
                "SMS authentication requires an explicit auth/privacy consent decision",
            )
            return False
        if prepare not in {"not_needed", "requested", "already_requested"}:
            self._block_auth(
                page_index,
                "sms_setup",
                "SMS authentication controls are unavailable or ambiguous",
            )
            return False

        post_prepare_kind = self._auth_kind(adapter)
        if post_prepare_kind not in {None, "one_time_code"}:
            self._block_auth(
                page_index,
                post_prepare_kind,
                f"{post_prepare_kind} authentication requires human handling",
            )
            return False
        if post_prepare_kind is None:
            if attempt:
                if not self._auth_return_target_verified(adapter):
                    self._block_auth(page_index, "return_target_unverified", "return target after authentication unverified")
                    return False
                self.otp_bridge.complete_attempt()
            return True

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
        source = reported_source if reported_source in {"iphone_relay", "mac_messages", "local_broker"} else "unknown"
        code = str(result.get("code") or "")
        entered = False
        try:
            if re.fullmatch(r"\d{4,8}", code):
                entered = bool(adapter.enter_one_time_code(code))
        except BrowserOwnershipError:
            raise
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
            except BrowserOwnershipError:
                raise
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

        if attempt:
            if not self._auth_return_target_verified(adapter):
                self._block_auth(page_index, "return_target_unverified", "return target after authentication unverified")
                return False
            self.otp_bridge.complete_attempt()
        self.audit.record_action({
            "type": "otp_authentication", "source": source, "ok": True,
        })
        return True

    def _attachment_resolution(self, field: WebField) -> FieldResolution | None:
        if field.input_type != "file":
            return None
        label = (field.label or "").casefold()
        matches = [name for name, pattern in (
            ("resume", r"resume|\bcv\b|简历"),
            ("photo", r"photo|portrait|证件照|照片"),
        ) if re.search(pattern, label, re.I)]
        if not matches:
            return None
        asset_name = matches[0] if len(matches) == 1 else None
        def unsupported(reason: str) -> FieldResolution:
            return FieldResolution(
                field_id=field.field_id, selector=field.selector, label=field.label,
                canonical_key=f"assets.{asset_name}" if asset_name else None,
                status=ResolutionStatus.UNRESOLVED, required=field.required,
                reason=reason,
            )
        if asset_name is None or field.metadata.get("multiple"):
            return unsupported("attachment slot is ambiguous or accepts multiple files")
        asset = (self.profile.get("assets") or {}).get(asset_name) or {}
        path = asset.get("path") if isinstance(asset, dict) else None
        if not path or not Path(path).expanduser().is_file():
            return unsupported("canonical attachment is unavailable")
        path_obj = Path(path).expanduser().resolve()
        max_size = str(field.metadata.get("max_size") or "")
        expected_hash = str(asset.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            return unsupported("canonical attachment hash is unavailable")
        digest = hashlib.sha256()
        try:
            if max_size and (not max_size.isdecimal() or path_obj.stat().st_size > int(max_size)):
                return unsupported("attachment size exceeds or cannot be checked against slot limit")
            with path_obj.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return unsupported("canonical attachment became inaccessible")
        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash:
            return unsupported("canonical attachment file changed since evidence capture")
        accept = str(field.metadata.get("accept") or "").lower()
        if accept:
            mime = mimetypes.guess_type(path_obj.name)[0] or "application/octet-stream"
            permitted = [token.strip() for token in accept.split(",") if token.strip()]
            if not any(token == mime or (token.endswith("/*") and mime.startswith(token[:-1]))
                       or (token.startswith(".") and path_obj.suffix.lower() == token)
                       for token in permitted):
                return unsupported("attachment type is not accepted by this slot")
        return FieldResolution(
            field_id=field.field_id,
            selector=field.selector,
            label=field.label,
            canonical_key=f"assets.{asset_name}",
            status=ResolutionStatus.RESOLVED,
            value=str(path_obj),
            source="profile_asset",
            confidence=1.0,
            reason="attachment slot and canonical hash verified locally; upload still unverified",
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
                if item.source == "user_confirmed_scoped_salary":
                    canonical = get_field(self.profile, item.canonical_key)
                    salary_scope = ((canonical.normalization or {}).get("salary") or {}) if canonical else {}
                    if (not canonical or not canonical.user_confirmed
                            or salary_scope.get("target_sha256") != hashlib.sha256(
                                self.target_url.encode()).hexdigest()):
                        errors.append(f"{item.field_id}: salary scope is no longer confirmed")
                    continue
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
        self.guard()
        adapter = adapter_for_url(self.target_url)
        def guarded_browser_mutation():
            self.guard()
            verify = getattr(adapter, "verify_document", None)
            if callable(verify):
                verify()
        adapter.mutation_guard = guarded_browser_mutation
        adapter.existing_browser_only = self.existing_browser_only
        adapter.browser_binding_get = getattr(self, "browser_binding_get", None)
        adapter.browser_binding_set = getattr(self, "browser_binding_set", None)
        adapter.browser_document_set = getattr(self, "browser_document_set", None)
        adapter.browser_session_epoch = getattr(self, "browser_session_epoch", None)
        if self.resume_url:
            from .protected_targets import assert_target_not_protected
            if urlparse(self.resume_url).netloc != urlparse(self.target_url).netloc:
                raise RuntimeError("checkpoint origin differs from exact target")
            assert_target_not_protected(self.resume_url)
            adapter.target_url = self.resume_url
        attachment_binding = None
        attachment_selections: list[FieldResolution] = []
        with adapter:
            for page_index in range(max_pages):
                self.guard()
                page = getattr(adapter, "page", None)
                if page is not None:
                    self.plan.metadata["checkpoint_url"] = page.url
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

                if browser_mode() not in {"isolated", "test", "headless"}:
                    try:
                        account_verified = bool(adapter.account_identity_verified(self.profile))
                    except BrowserOwnershipError:
                        raise
                    except Exception:
                        account_verified = False
                    if not account_verified:
                        return self._block_auth(
                            page_index, "account_identity_unverified",
                            "active account identity is unverified")

                observe_form = getattr(adapter, "observe_form", None)
                try:
                    observation = observe_form() if callable(observe_form) else None
                    fields = list(observation.fields) if observation else adapter.discover_fields()
                except FormObservationError:
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "form observation unavailable"
                    self.plan.metadata["form_observation"] = "ERROR"
                    self.audit.save_plan(self.plan)
                    return self.plan
                if observation:
                    self.plan.metadata["form_observation"] = observation.safe_summary()
                if not fields and adapter.start_application():
                    self.audit.record_action({"type": "start_application", "page_index": page_index})
                    continue
                if observation and (((observation.rows and not getattr(
                        adapter, "repeated_rows_certified", False))
                                     or observation.unsupported_component_count
                                     or observation.ambiguous_selector_count
                                     or observation.ambiguous_row_count) or (
                        not fields and adapter.final_submit_control())):
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "form structure unsupported or incomplete"
                    self.audit.save_plan(self.plan)
                    return self.plan
                resolutions = self._resolve_page(fields)
                if observation:
                    FillPlan.bind(observation, resolutions)
                self.plan.metadata["current_page_selectors"] = [item.selector for item in resolutions]
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
                if observation and not any(not action.get("ok", False) for action in actions):
                    # A parent control can reveal child fields. Rebind each new
                    # field against a fresh DOM observation; never replay the
                    # old selector or silently omit newly mandatory controls.
                    known = set(self.plan.metadata["current_page_selectors"])
                    for _ in range(4):
                        await_render = getattr(adapter, "await_form_render", None)
                        if callable(await_render):
                            await_render()
                        try:
                            updated = observe_form()
                        except FormObservationError:
                            self.plan.stage = ApplicationStage.BLOCKED
                            self.plan.metadata["block_reason"] = "form observation unavailable"
                            self.plan.metadata["form_observation"] = "ERROR"
                            self.audit.save_plan(self.plan)
                            return self.plan
                        self.plan.metadata["form_observation"] = updated.safe_summary()
                        if (updated.unsupported_component_count or updated.ambiguous_selector_count
                                or updated.ambiguous_row_count):
                            break
                        added = [field for field in updated.fields if field.selector not in known]
                        if not added:
                            break
                        new_resolutions = self._resolve_page(added)
                        FillPlan.bind(updated, new_resolutions)
                        self.plan.fields.extend(new_resolutions)
                        self.plan.unresolved_fields.extend(item for item in new_resolutions
                            if item.status in {ResolutionStatus.UNRESOLVED,
                                               ResolutionStatus.USER_CONFIRMATION})
                        resolutions.extend(new_resolutions)
                        known.update(item.selector for item in added)
                        self.plan.metadata["current_page_selectors"].extend(
                            item.selector for item in added)
                        self.audit.save_plan(self.plan)
                        new_actions = adapter.apply_resolutions(new_resolutions)
                        actions.extend(new_actions)
                        for action in new_actions:
                            self.audit.record_action({"type": "fill", **action})
                        if any(not action.get("ok", False) for action in new_actions):
                            break
                self.plan.stage = ApplicationStage.FORM_FILLED
                self.audit.save_plan(self.plan)

                fill_failures = [action for action in actions if not action.get("ok", False)]
                if fill_failures:
                    if getattr(adapter, "save_draft", lambda: False)():
                        self.audit.record_action({"type": "save_draft_requested", "page_index": page_index,
                                                  "outcome": "UNVERIFIED"})
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "resolved field could not be filled"
                    self.plan.metadata["fill_failures"] = fill_failures
                    self.audit.save_plan(self.plan)
                    return self.plan

                selected_assets = [item for item in resolutions
                                   if item.status == ResolutionStatus.RESOLVED
                                   and item.canonical_key in {"assets.resume", "assets.photo"}]
                if selected_assets:
                    readback = getattr(adapter, "verify_attachment_receipts", None)
                    try:
                        receipts = readback(self.plan, selected_assets) if callable(readback) else None
                        proof = verify_attachment_readback(
                            self.target_url, selected_assets,
                            self.profile.get("assets") or {}, receipts)
                    except BrowserOwnershipError:
                        raise
                    except Exception:
                        # An upload may have happened before its readback failed.
                        # Keep this task blocked; never replay the file selection.
                        self.plan.stage = ApplicationStage.BLOCKED
                        self.plan.metadata["block_reason"] = "attachment draft receipt unverified"
                        self.plan.metadata["attachment_persistence"] = "UNVERIFIED"
                        self.audit.save_plan(self.plan)
                        return self.plan
                    new_binding = proof.pop("_draft_id_digest")
                    if attachment_binding and attachment_binding[0] != new_binding:
                        self.plan.stage = ApplicationStage.BLOCKED
                        self.plan.metadata["block_reason"] = "attachment draft receipt unverified"
                        self.plan.metadata["attachment_persistence"] = "UNVERIFIED"
                        self.audit.save_plan(self.plan)
                        return self.plan
                    attachment_binding = (new_binding, proof["minimum_draft_revision"])
                    attachment_selections.extend(selected_assets)
                    self.plan.metadata["attachment_persistence"] = proof
                    self.audit.save_plan(self.plan)

                blocking = self._blocking_unresolved(resolutions)
                if blocking:
                    if getattr(adapter, "save_draft", lambda: False)():
                        self.audit.record_action({"type": "save_draft_requested", "page_index": page_index,
                                                  "outcome": "UNVERIFIED"})
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
                        self.audit.record_action({"type": "save_draft_requested", "page_index": page_index,
                                                  "outcome": "UNVERIFIED"})
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
                        self.audit.record_action({"type": "save_draft_requested", "page_index": page_index,
                                                  "outcome": "UNVERIFIED"})
                    self.plan.stage = ApplicationStage.BLOCKED
                    self.plan.metadata["block_reason"] = "profile consistency audit failed"
                    self.audit.save_plan(self.plan)
                    return self.plan

                self.plan.stage = ApplicationStage.VALIDATED
                final_control = adapter.final_submit_control()
                if final_control:
                    if attachment_binding:
                        # A later page may silently discard a previous upload.
                        # Re-read every selected slot from the server before READY.
                        readback = getattr(adapter, "verify_attachment_receipts", None)
                        try:
                            receipts = (readback(self.plan, attachment_selections)
                                        if callable(readback) else None)
                            retained = verify_attachment_readback(
                                self.target_url, attachment_selections,
                                self.profile.get("assets") or {}, receipts)
                            if retained.pop("_draft_id_digest") != attachment_binding[0]:
                                raise ValueError("attachment draft identity changed")
                        except BrowserOwnershipError:
                            raise
                        except Exception:
                            self.plan.stage = ApplicationStage.BLOCKED
                            self.plan.metadata["block_reason"] = "attachment draft receipt unverified"
                            self.plan.metadata["attachment_persistence"] = "UNVERIFIED"
                            self.audit.save_plan(self.plan)
                            return self.plan
                        attachment_binding = (attachment_binding[0],
                                              retained["minimum_draft_revision"])
                        self.plan.metadata["attachment_persistence"] = retained
                    verify_draft = getattr(adapter, "verify_draft_persistence", None)
                    if attachment_binding and not callable(verify_draft):
                        self.plan.stage = ApplicationStage.BLOCKED
                        self.plan.metadata["block_reason"] = "draft persistence unverified"
                        self.plan.metadata["draft_persistence"] = "UNVERIFIED"
                        self.audit.save_plan(self.plan)
                        return self.plan
                    if callable(verify_draft):
                        draft_evidence = verify_draft(self.plan)
                        if (not isinstance(draft_evidence, dict)
                                or draft_evidence.get("verified") is not True
                                or (attachment_binding and (
                                    draft_evidence.get("level") != "server_readback"
                                    or draft_evidence.get("draft_id_digest") != attachment_binding[0]
                                    or not isinstance(draft_evidence.get("revision"), int)
                                    or isinstance(draft_evidence.get("revision"), bool)
                                    or draft_evidence["revision"] < attachment_binding[1]))):
                            self.plan.stage = ApplicationStage.BLOCKED
                            self.plan.metadata["block_reason"] = "draft persistence unverified"
                            self.plan.metadata["draft_persistence"] = "UNVERIFIED"
                            self.audit.save_plan(self.plan)
                            return self.plan
                        # The driver's receipt may include site payloads. Only
                        # copy the fixed, non-personal proof fields to audit.
                        self.plan.metadata["draft_persistence"] = {
                            "verified": True,
                            "level": "server_readback" if draft_evidence.get("level") == "server_readback"
                                     else "site_draft_readback",
                            "revision": int(draft_evidence["revision"])
                                        if isinstance(draft_evidence.get("revision"), int)
                                        and draft_evidence["revision"] >= 0 else None,
                        }
                    review = build_final_review(self.profile, self.plan, final_control)
                    self.plan.metadata["final_review"] = review
                    if review["project_coverage"]["status"] == "REVIEW_REQUIRED":
                        self.plan.stage = ApplicationStage.BLOCKED
                        self.plan.metadata["block_reason"] = "structured project coverage unproven"
                        self.audit.save_plan(self.plan)
                        return self.plan
                    self.plan.stage = ApplicationStage.READY_TO_SUBMIT
                    self.plan.metadata["final_submit_control"] = final_control
                    self.plan.metadata["manual_final_click_required"] = True
                    self.plan.metadata["submit_authorized_does_not_allow_final_click"] = bool(
                        self.plan.submit_authorized
                    )
                    self.audit.save_plan(self.plan)
                    try:
                        adapter.screenshot(self.audit.screenshot_path("ready-to-submit"))
                    except Exception:
                        pass
                    return self.plan

                next_control = getattr(adapter, "next_control", None)
                if callable(next_control) and next_control():
                    verify_draft = getattr(adapter, "verify_draft_persistence", None)
                    draft_evidence = verify_draft(self.plan) if callable(verify_draft) else None
                    if (not isinstance(draft_evidence, dict)
                            or draft_evidence.get("verified") is not True
                            or (attachment_binding and (
                                draft_evidence.get("level") != "server_readback"
                                or draft_evidence.get("draft_id_digest") != attachment_binding[0]
                                or not isinstance(draft_evidence.get("revision"), int)
                                or isinstance(draft_evidence.get("revision"), bool)
                                or draft_evidence["revision"] < attachment_binding[1]))):
                        self.plan.stage = ApplicationStage.BLOCKED
                        self.plan.metadata["block_reason"] = "draft persistence unverified"
                        self.plan.metadata["draft_persistence"] = "UNVERIFIED"
                        self.plan.metadata["draft_persistence_phase"] = "before_navigation"
                        self.audit.save_plan(self.plan)
                        return self.plan
                    self.plan.metadata.setdefault("page_draft_receipts", []).append({
                        "page_index": page_index,
                        "level": "server_readback" if draft_evidence.get("level") == "server_readback"
                                 else "site_draft_readback",
                        "revision": int(draft_evidence["revision"])
                                    if isinstance(draft_evidence.get("revision"), int)
                                    and draft_evidence["revision"] >= 0 else None,
                    })
                    self.audit.save_plan(self.plan)
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
