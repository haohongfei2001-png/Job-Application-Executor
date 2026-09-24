from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .base import SiteAdapter
from ..browser import BrowserOwnershipError, connect, owned_page_after_action, page_document_epoch, page_target_id
from ..field_classifier import is_final_submit, is_initial_apply, is_next
from ..forms import FormObservation, FormObservationError, ObservedRow
from ..models import (
    ApplicationPlan,
    FieldResolution,
    ResolutionStatus,
    SubmissionVerification,
    ValidationResult,
    WebField,
)


VISIBLE_FIELD_SELECTOR = ('input:not([type="hidden"]), textarea, select, '
                          '[role="combobox"]:not(input):not(textarea):not(select)')
BUTTON_SELECTOR = 'button, input[type="button"], input[type="submit"], [role="button"], a'
OTP_HINT_RE = re.compile(
    r"(?:验证码|校验码|动态码|短信码|verification[\s_-]*code|"
    r"one[\s_-]*time[\s_-]*code|otp)",
    re.I,
)
OTP_STRONG_AUTH_CONFIRM_TEXTS = {
    "登录", "登陆", "sign in", "log in",
}
OTP_CONTEXTUAL_AUTH_CONFIRM_TEXTS = {
    "验证", "确认", "继续", "下一步",
    "verify", "confirm", "continue", "next",
}
OTP_CONFIRM_TEXTS = OTP_STRONG_AUTH_CONFIRM_TEXTS | OTP_CONTEXTUAL_AUTH_CONFIRM_TEXTS
OTP_SEND_CONTROL_RE = re.compile(
    r"发送验证码|获取验证码|重新发送|重发|send\s+(?:the\s+)?code|"
    r"get\s+(?:the\s+)?code|resend",
    re.I,
)
OTP_INITIAL_SEND_CONTROL_RE = re.compile(
    r"^(?:发送验证码|获取验证码|发送短信验证码|获取短信验证码|"
    r"send\s+(?:the\s+)?code|get\s+(?:the\s+)?code|request\s+(?:the\s+)?code)$",
    re.I,
)
OTP_RESEND_CONTROL_RE = re.compile(
    r"重新发送|重发|resend|\d+\s*(?:s|sec|秒).*(?:发送|重发|resend)",
    re.I,
)
PHONE_HINT_RE = re.compile(r"phone|mobile|tel|手机号|手机号码|联系电话|电话", re.I)
AUTH_TERMS_RE = re.compile(
    r"隐私政策|隐私协议|privacy\s+policy|注册协议|用户协议|服务协议|"
    r"terms(?:\s+of\s+(?:service|use))?|terms\s*&\s*conditions",
    re.I,
)
OTP_REGISTER_LOGIN_RE = re.compile(
    r"^(?:注册\s*/\s*登录|登录\s*/\s*注册|注册并登录|register\s*/\s*log\s*in)$",
    re.I,
)
ACCOUNT_OR_DESTRUCTIVE_RE = re.compile(
    r"注册|创建账号|创建账户|注销|删除|取消账号|sign\s*up|register|"
    r"create\s+(?:an?\s+)?account|delete|remove|destroy|deactivate",
    re.I,
)


class GenericWebAdapter(SiteAdapter):
    site_id = "generic_web"

    def __init__(self, target_url: str):
        super().__init__(target_url)
        self.pw = self.browser = self.ctx = self.page = None

    @classmethod
    def can_handle(cls, target_url: str) -> bool:
        return target_url.startswith(("http://", "https://", "file://"))

    def open(self) -> None:
        binding_get = getattr(self, "browser_binding_get", None)
        binding = binding_get() if callable(binding_get) else None
        bind_page = getattr(self, "browser_binding_set", None)
        record_document = getattr(self, "browser_document_set", None)
        epoch = getattr(self, "browser_session_epoch", None)
        if getattr(self, "existing_browser_only", False):
            self.pw, self.browser, self.ctx, self.page = connect(
                self.target_url, existing_only=True, task_binding=binding,
                bind_page=bind_page, session_epoch=epoch,
            )
        else:
            self.pw, self.browser, self.ctx, self.page = connect(self.target_url)
        if callable(bind_page):
            try:
                self.bound_target_id = page_target_id(self.ctx, self.page)
                self.document_epoch = page_document_epoch(self.page)
                if callable(record_document):
                    record_document(self.bound_target_id, self.document_epoch)
            except BaseException:
                self.close()
                raise
        else:
            self.bound_target_id = None
            self.document_epoch = None

    def verify_document(self) -> None:
        if self.bound_target_id is None:
            return
        if self.page.is_closed() or page_target_id(self.ctx, self.page) != self.bound_target_id:
            raise BrowserOwnershipError("task tab changed before write")
        # This is a read-only check immediately before the browser primitive.
        if page_document_epoch(self.page) != self.document_epoch:
            raise BrowserOwnershipError("task document changed before write")
        if owned_page_after_action(self.ctx, self.page, self.target_url) is not self.page:
            raise BrowserOwnershipError("task popup changed before write")

    def _adopt_owned_page(self) -> None:
        selected = owned_page_after_action(self.ctx, self.page, self.target_url)
        record_document = getattr(self, "browser_document_set", None)
        if selected is not self.page:
            bind_page = getattr(self, "browser_binding_set", None)
            if callable(bind_page):
                target_id = page_target_id(self.ctx, selected)
                bind_page(target_id, previous_target_id=self.bound_target_id)
                self.bound_target_id = target_id
        if self.bound_target_id is not None:
            observed_epoch = page_document_epoch(selected)
            if observed_epoch != self.document_epoch and callable(record_document):
                record_document(self.bound_target_id, observed_epoch)
            self.document_epoch = observed_epoch
        self.page = selected

    def close(self) -> None:
        if self.pw:
            try:
                self.pw.stop()
            except Exception:
                pass

    @staticmethod
    def _visible(locator) -> bool:
        try:
            return locator.is_visible() and locator.is_enabled()
        except Exception:
            return False

    def _locate(self, selector: str, label: str = ""):
        if selector.startswith("__field_index__:"):
            index = int(selector.rsplit(":", 1)[1])
            return self.page.locator(VISIBLE_FIELD_SELECTOR).nth(index)
        loc = self.page.locator(selector)
        if loc.count():
            return loc
        if label:
            by_label = self.page.get_by_label(label, exact=False)
            if by_label.count():
                return by_label
        return loc

    def discover_fields(self) -> list[WebField]:
        """Snapshot the whole form in one browser round-trip.

        The previous implementation crossed the Playwright boundary repeatedly for
        every field (visibility, label, type, section, value and options). Heavy
        React recruitment forms could therefore require hundreds of CDP calls per
        pass. Keep the same fallback selector semantics while collecting metadata
        in-page once.
        """
        script = r"""() => {
          const selector = 'input:not([type="hidden"]), textarea, select, [role="combobox"]:not(input):not(textarea):not(select)';
          const clean = s => (s || '').replace(/\s+/g, ' ').trim();
          const known = /^(个人信息|教育经历|实习经历|工作经历|项目经历|项目\/活动经历|科研经历|语言能力|证书|家庭情况|家庭成员|获奖情况|在校实践|论文\/?专著|附加信息|简历附件|personal information|education|internship|work experience|projects?|research|family|awards?)$/i;
          const visible = e => {
            const style = getComputedStyle(e);
            const rect = e.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
              && !e.disabled
              && e.getAttribute('aria-disabled') !== 'true';
          };
          const hint = e => {
            const labels = [...(e.labels || [])].map(x => x.innerText);
            const near = e.closest('label,fieldset,[class*=field],[class*=form]');
            return clean([
              labels.join(' '),
              e.getAttribute('aria-label'),
              e.getAttribute('placeholder'),
              e.getAttribute('name'),
              e.id,
              near?.innerText?.slice(0, 280),
            ].filter(Boolean).join(' | '));
          };
          const section = e => {
            let node = e;
            for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
              let sib = node.previousElementSibling, hops = 0;
              while (sib && hops < 8) {
                const own = clean(sib.innerText);
                if (own && own.length <= 80 && known.test(own)) return own;
                const candidates = sib.querySelectorAll?.('h1,h2,h3,h4,h5,h6,[class*=title],[class*=header]') || [];
                for (let i = candidates.length - 1; i >= 0; i--) {
                  const text = clean(candidates[i].innerText);
                  if (text && text.length <= 80 && known.test(text)) return text;
                }
                sib = sib.previousElementSibling;
                hops++;
              }
            }
            return '';
          };

          return [...document.querySelectorAll(selector)].slice(0, 350).map((e, index) => {
            const tag = e.tagName.toLowerCase();
            const inputType = e.getAttribute('role') === 'combobox' ? 'combobox'
              : (e.getAttribute('type') || tag).toLowerCase();
            const name = e.getAttribute('name') || '';
            const id = e.id || '';
            const row = e.closest('[data-record-id], [data-row-id]');
            const rowAttribute = row?.hasAttribute('data-record-id') ? 'data-record-id' : 'data-row-id';
            const rowToken = row?.getAttribute(rowAttribute) || '';
            const rowUnique = !!rowToken && [...document.querySelectorAll('[' + rowAttribute + ']')]
              .filter(node => node.getAttribute(rowAttribute) === rowToken).length === 1;
            let cssSelector = '__field_index__:' + index;
            if (id) cssSelector = '#' + CSS.escape(id);
            else if (name) cssSelector = tag + '[name=' + JSON.stringify(name) + ']';

            const options = tag === 'select'
              ? [...e.options].slice(0, 200).map(x => clean(x.innerText)).filter(Boolean)
              : [];
            const selectedText = tag === 'select' && e.selectedIndex >= 0
              ? clean(e.options[e.selectedIndex]?.innerText)
              : '';
            let current = null;
            if (inputType === 'checkbox' || inputType === 'radio') current = !!e.checked;
            else if (inputType === 'combobox') current = e.value ?? e.getAttribute('aria-valuetext') ?? clean(e.innerText);
            else if (!['submit', 'button'].includes(inputType)) current = e.value ?? '';

            return {
              visible: visible(e),
              index,
              tag,
              inputType,
              label: hint(e),
              required: !!e.required || e.getAttribute('aria-required') === 'true',
              current,
              options,
              selectedText,
              accept: e.getAttribute('accept') || '',
              multiple: !!e.multiple,
              maxSize: e.getAttribute('data-max-size') || e.getAttribute('data-max-file-size') || '',
              dependsOn: e.getAttribute('data-depends-on') || '',
              rowToken,
              rowAttribute: rowToken ? rowAttribute : '',
              rowUnique,
              selector: cssSelector,
              fieldId: name || id || ('field-' + index),
              section: section(e),
            };
          }).filter(x => x.visible);
        }"""
        try:
            snapshot = self.page.evaluate(script) or []
        except Exception:
            raise FormObservationError("field observation unavailable") from None

        return [
            WebField(
                field_id=item["fieldId"],
                selector=item["selector"],
                label=item.get("label") or "",
                input_type=item.get("inputType") or item.get("tag") or "text",
                required=bool(item.get("required")),
                options=list(item.get("options") or []),
                current_value=item.get("current"),
                page_url=self.page.url,
                metadata={
                    "index": item.get("index"),
                    "tag": item.get("tag"),
                    "section": item.get("section") or "",
                    "selected_text": item.get("selectedText") or "",
                    "accept": item.get("accept") or "",
                    "multiple": bool(item.get("multiple")),
                    "max_size": item.get("maxSize") or "",
                    "depends_on": item.get("dependsOn") or "",
                    "row_key": hashlib.sha256(
                        (str(item.get("rowAttribute") or "") + ":" +
                         str(item.get("rowToken") or "")).encode()).hexdigest()[:20]
                        if item.get("rowToken") else "",
                    "row_identity_proven": bool(item.get("rowUnique")),
                },
            )
            for item in snapshot
        ]

    def observe_form(self) -> FormObservation:
        """Read form structure separately from any intended fill actions."""
        fields = self.discover_fields()
        try:
            signals = self.page.evaluate(r"""() => {
              const visible = e => {
                const style = getComputedStyle(e), rect = e.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
                  && !e.disabled && e.getAttribute('aria-disabled') !== 'true';
              };
              const required = [...document.querySelectorAll(
                'input:not([type="hidden"]), textarea, select')].filter(e =>
                  e.required || e.getAttribute('aria-required') === 'true');
              const custom = [...document.querySelectorAll(
                '[role="combobox"], [role="listbox"], [contenteditable="true"], iframe')]
                .filter(visible);
              const controlled = new Set([...document.querySelectorAll('[role="combobox"][aria-controls]')]
                .map(e => e.getAttribute('aria-controls')).filter(Boolean));
              const unsupported = custom.filter(e => {
                if (e.getAttribute('role') === 'combobox') {
                  const id = e.getAttribute('aria-controls');
                  const list = id && document.getElementById(id);
                  return !list || list.getAttribute('role') !== 'listbox';
                }
                if (e.getAttribute('role') === 'listbox' && controlled.has(e.id)) return false;
                return true;
              });
              // Standard DOM queries do not traverse shadow roots. Treat
              // form controls inside an open root and opaque custom elements
              // as unsupported until a site driver proves their semantics.
              const shadowOrOpaque = [...document.querySelectorAll('*')].filter(e => {
                if (e.shadowRoot) return !!e.shadowRoot.querySelector(
                  'input, textarea, select, [role="combobox"], [contenteditable="true"]');
                return e.tagName.includes('-') && visible(e);
              });
              const errors = [...document.querySelectorAll(
                '[aria-invalid="true"], [role="alert"], .error-message, [data-error]')]
                .filter(visible);
              return {
                hiddenRequired: required.filter(e => !visible(e)).length,
                unsupported: unsupported.length + shadowOrOpaque.length,
                validationErrors: errors.length,
              };
            }""") or {}
            epoch = page_document_epoch(self.page)
        except Exception:
            raise FormObservationError("form structure observation unavailable") from None
        selector_counts = Counter(item.selector for item in fields)
        ambiguous = sum(count - 1 for count in selector_counts.values() if count > 1)
        grouped: dict[str, list[WebField]] = {}
        for item in fields:
            key = str(item.metadata.get("row_key") or "")
            if key:
                grouped.setdefault(key, []).append(item)
        rows = tuple(ObservedRow(
            section=str(items[0].metadata.get("section") or ""),
            row_key=key,
            field_selectors=tuple(item.selector for item in items),
            identity_proven=all(bool(item.metadata.get("row_identity_proven"))
                                for item in items),
        ) for key, items in grouped.items())
        dependencies = tuple((str(item.metadata["depends_on"]), item.selector)
                             for item in fields if item.metadata.get("depends_on"))
        return FormObservation.from_fields(
            self.page.url, epoch, fields,
            rows=rows, dependencies=dependencies,
            hidden_required_count=int(signals.get("hiddenRequired") or 0),
            unsupported_component_count=int(signals.get("unsupported") or 0),
            ambiguous_selector_count=ambiguous,
            ambiguous_row_count=sum(not row.identity_proven for row in rows),
            validation_error_count=int(signals.get("validationErrors") or 0),
        )

    def await_form_render(self) -> None:
        """Wait for controlled component redraw without claiming save success."""
        self.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

    @staticmethod
    def _control_text(value, input_type: str = "text", label: str = "") -> str:
        if isinstance(value, list):
            if not value:
                return ""
            if re.search(r"首选|第一|preferred.?city|first.?choice", label, re.I):
                value = value[0]
            else:
                return "、".join(str(x) for x in value)
        if value is True:
            return "是" if re.search(r"[\u4e00-\u9fff]", label) else "yes"
        if value is False:
            return "否" if re.search(r"[\u4e00-\u9fff]", label) else "no"
        text = str(value)
        if input_type == "month" and re.match(r"^\d{4}-\d{2}", text):
            return text[:7]
        if input_type == "date" and re.match(r"^\d{4}-\d{2}-\d{2}", text):
            return text[:10]
        return text

    @staticmethod
    def _date_precision_supported(value, input_type: str) -> bool:
        if input_type not in {"date", "month"}:
            return True
        text = str(value)
        if input_type == "date":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                return False
            try:
                date.fromisoformat(text)
            except ValueError:
                return False
            return True
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return GenericWebAdapter._date_precision_supported(text, "date")
        if not re.fullmatch(r"\d{4}-\d{2}", text):
            return False
        return 1 <= int(text[5:7]) <= 12

    def _fill_select(self, element, value) -> bool:
        if element.get_attribute("multiple") is not None:
            return False
        candidates = value if isinstance(value, list) else [value]
        expanded = []
        for candidate in candidates:
            if candidate is True:
                expanded.extend(["是", "yes", "true"])
            elif candidate is False:
                expanded.extend(["否", "no", "false"])
            else:
                expanded.append(candidate)
        opts = element.locator("option")
        normalized = [str(x).strip().casefold().removesuffix("市") for x in expanded]
        if not normalized or any(not target for target in normalized):
            return False
        matches = []
        for i in range(opts.count()):
            opt = opts.nth(i)
            text = (opt.inner_text() or "").strip()
            val = opt.get_attribute("value") or ""
            hay = {text.casefold().removesuffix("市"), val.casefold()}
            if any(target in hay for target in normalized):
                matches.append(i)
        if len(matches) != 1:
            return False
        getattr(self, "mutation_guard", lambda: None)()
        element.select_option(index=matches[0])
        if not element.evaluate("(e, index) => e.selectedIndex === index", matches[0]):
            raise BrowserOwnershipError("select choice outcome unknown")
        return True

    def _fill_combobox(self, element, selector: str, value) -> bool:
        if not isinstance(value, (str, int, float)):
            return False
        target = str(value).strip()
        if not target:
            return False
        controlled_id = element.get_attribute("aria-controls")
        if not controlled_id:
            return False
        listbox = self.page.locator(f'[id={json.dumps(controlled_id)}][role="listbox"]')
        if listbox.count() != 1:
            return False
        getattr(self, "mutation_guard", lambda: None)()
        element.click()
        choices = listbox.locator('[role="option"]')
        exact = [choices.nth(index) for index in range(choices.count())
                 if choices.nth(index).is_visible()
                 and (choices.nth(index).get_attribute("aria-label") or
                      choices.nth(index).inner_text()).strip() == target]
        if len(exact) != 1:
            return False
        getattr(self, "mutation_guard", lambda: None)()
        exact[0].click()
        self.await_form_render()
        selected = self._locate(selector)
        if selected.count() != 1:
            raise BrowserOwnershipError("combobox selection outcome unknown")
        actual = selected.evaluate("e => e.value ?? e.getAttribute('aria-valuetext') ?? (e.innerText || '').trim()")
        if str(actual).strip() != target:
            raise BrowserOwnershipError("combobox selection outcome unknown")
        return True

    def apply_resolutions(self, resolutions: Iterable[FieldResolution]) -> list[dict]:
        resolutions = list(resolutions)
        # Explicit site dependency metadata permits a bounded parent-first
        # order. The child locator is resolved after the parent's redraw.
        # Missing or cyclic dependencies are unsupported, never guessed.
        observed_by_selector = {item.selector: item for item in self.discover_fields()}
        by_selector = {item.selector: item for item in resolutions}
        ordered: list[FieldResolution] = []
        pending = list(resolutions)
        completed: set[str] = set()
        while pending:
            ready = []
            for item in pending:
                observed = observed_by_selector.get(item.selector)
                dependency = str(observed.metadata.get("depends_on") or "") if observed else ""
                if not dependency or dependency in completed or dependency not in by_selector:
                    ready.append(item)
            if not ready:
                return [{"field_id": item.field_id, "ok": False,
                         "reason": "cyclic_field_dependency"} for item in pending]
            for item in ready:
                observed = observed_by_selector.get(item.selector)
                dependency = str(observed.metadata.get("depends_on") or "") if observed else ""
                if dependency and dependency not in observed_by_selector:
                    return [{"field_id": item.field_id, "ok": False,
                             "reason": "missing_field_dependency"}]
                if (dependency in by_selector and item.status == ResolutionStatus.RESOLVED
                        and by_selector[dependency].status != ResolutionStatus.RESOLVED):
                    return [{"field_id": item.field_id, "ok": False,
                             "reason": "unresolved_field_dependency"}]
                ordered.append(item)
                completed.add(item.selector)
                pending.remove(item)
        actions: list[dict] = []
        for resolution in ordered:
            getattr(self, "mutation_guard", lambda: None)()
            if resolution.status != ResolutionStatus.RESOLVED:
                continue
            observed = observed_by_selector.get(resolution.selector)
            dependency = str(observed.metadata.get("depends_on") or "") if observed else ""
            if (dependency in by_selector and any(
                    action["field_id"] == by_selector[dependency].field_id
                    and not action["ok"] for action in actions)):
                actions.append({"field_id": resolution.field_id, "ok": False,
                                "reason": "parent_field_write_failed"})
                continue
            if dependency:
                self.await_form_render()
            element = self._locate(resolution.selector, resolution.label)
            if element.count() != 1:
                actions.append({"field_id": resolution.field_id, "ok": False,
                                "reason": "ambiguous_or_missing_locator"})
                continue
            if not self._visible(element):
                actions.append({"field_id": resolution.field_id, "ok": False, "reason": "not visible"})
                continue
            try:
                input_type = (element.get_attribute("type") or element.evaluate("e=>e.tagName.toLowerCase()")).lower()
                tag = element.evaluate("e=>e.tagName.toLowerCase()")
                value = resolution.value
                if input_type == "file":
                    # A browser file input only proves that a local file was
                    # selected. Generic sites provide no trustworthy upload
                    # completion or server draft receipt. A site driver must
                    # own that effect and its readback before it can be used.
                    actions.append({"field_id": resolution.field_id, "ok": False,
                                    "reason": "upload_receipt_unsupported"})
                    continue
                elif input_type == "combobox" or element.get_attribute("role") == "combobox":
                    if not self._fill_combobox(element, resolution.selector, value):
                        actions.append({"field_id": resolution.field_id, "ok": False,
                                        "reason": "combobox_exact_choice_unavailable"})
                        continue
                elif tag == "select":
                    getattr(self, "mutation_guard", lambda: None)()
                    if not self._fill_select(element, value):
                        actions.append({"field_id": resolution.field_id, "ok": False, "reason": "no_matching_select_option"})
                        continue
                elif input_type in {"checkbox", "radio"}:
                    desired = bool(value)
                    if element.is_checked() != desired:
                        getattr(self, "mutation_guard", lambda: None)()
                        element.click()
                else:
                    if not self._date_precision_supported(value, input_type):
                        actions.append({"field_id": resolution.field_id, "ok": False,
                                        "reason": "date_precision_unsupported"})
                        continue
                    desired = self._control_text(value, input_type, resolution.label)
                    current = str(element.input_value() or "")
                    if current != desired:
                        getattr(self, "mutation_guard", lambda: None)()
                        element.fill(desired)
                actions.append({"field_id": resolution.field_id, "ok": True, "source": resolution.source})
            except Exception:
                # The primitive may have reached the page before Playwright
                # reported failure. Do not turn that uncertainty into a retry.
                raise BrowserOwnershipError("field write outcome unknown") from None
        return actions

    def validate(self, plan: ApplicationPlan) -> ValidationResult:
        missing: list[str] = []
        errors: list[str] = []
        warnings: list[str] = []
        # Let controlled components commit and redraw before reading page
        # state. This is synchronization with the page render, not proof that
        # a network autosave completed.
        try:
            self.await_form_render()
            observation = self.observe_form()
        except Exception:
            return ValidationResult(ok=False, errors=["form observation unavailable after fill"])
        current_fields = list(observation.fields)
        if observation.unsafe_structure:
            errors.append("form structure changed or contains unsupported required controls")
        if observation.validation_error_count:
            errors.append("site validation errors remain visible")
        page_selectors = plan.metadata.get("current_page_selectors")
        current_scope = set(page_selectors) if isinstance(page_selectors, list) else None
        by_selector = {item.selector: item for item in plan.fields
                       if current_scope is None or item.selector in current_scope}
        observed_selectors = {item.selector for item in current_fields}
        if current_scope is not None:
            for selector in observed_selectors - current_scope:
                errors.append("new field appeared after fill; plan requires re-observation")
        for selector, expected in by_selector.items():
            if expected.status == ResolutionStatus.RESOLVED and selector not in observed_selectors:
                errors.append(f"{expected.label or expected.field_id}: field was not observed after fill")

        for field in current_fields:
            label = field.label or field.field_id
            actual = field.current_value
            empty = not bool(actual) if field.input_type in {"checkbox", "radio"} else not str(actual or "").strip()
            if field.required and empty:
                missing.append(label)
                continue

            expected = by_selector.get(field.selector)
            if not expected or expected.status != ResolutionStatus.RESOLVED:
                continue
            if field.input_type == "file":
                errors.append(f"{label}: upload completion and draft receipt unverified")
            elif field.input_type in {"checkbox", "radio"}:
                if bool(actual) != bool(expected.value):
                    errors.append(f"{label}: boolean value mismatch")
            elif field.metadata.get("tag") == "select":
                selected_text = str(field.metadata.get("selected_text") or "")
                candidates = expected.value if isinstance(expected.value, list) else [expected.value]
                normalized = {str(x).strip().casefold().removesuffix("市") for x in candidates}
                actual_norm = selected_text.casefold().removesuffix("市")
                if normalized and actual_norm not in normalized:
                    errors.append(f"{label}: selected option does not match canonical value")
            else:
                desired = self._control_text(expected.value, field.input_type, field.label)
                if str(actual or "") != desired:
                    errors.append(f"{label}: value does not match canonical profile")

        return ValidationResult(
            ok=not missing and not errors,
            missing_required=missing,
            errors=errors,
            warnings=warnings,
        )

    def _buttons(self):
        script = r"""() => {
          const selector = 'button, input[type="button"], input[type="submit"], [role="button"], a';
          return [...document.querySelectorAll(selector)].slice(0, 220).map((e, index) => {
            const style = getComputedStyle(e);
            const rect = e.getBoundingClientRect();
            const visible = style.display !== 'none'
              && style.visibility !== 'hidden'
              && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
              && !e.disabled
              && e.getAttribute('aria-disabled') !== 'true';
            const text = ((e.innerText || e.value || e.getAttribute('aria-label') || '') + '').trim();
            return {index, visible, text};
          }).filter(x => x.visible && x.text);
        }"""
        try:
            snapshot = self.page.evaluate(script) or []
        except Exception:
            snapshot = []
        loc = self.page.locator(BUTTON_SELECTOR)
        return [(loc.nth(item["index"]), item["text"]) for item in snapshot]

    def _otp_candidates(self):
        script = r"""() => {
          const visible = e => {
            const style = getComputedStyle(e);
            const rect = e.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
              && !e.disabled
              && e.getAttribute('aria-disabled') !== 'true';
          };
          return [...document.querySelectorAll('input:not([type="hidden"])')]
            .map((e, index) => ({
              index,
              autocomplete: (e.getAttribute('autocomplete') || '').toLowerCase(),
              hint: [
                ...(e.labels ? [...e.labels].map(label => label.innerText || '') : []),
                e.getAttribute('name') || '',
                e.id || '',
                e.getAttribute('placeholder') || '',
                e.getAttribute('aria-label') || '',
              ].join(' '),
              visible: visible(e),
            }))
            .filter(item => item.visible);
        }"""
        try:
            snapshot = self.page.evaluate(script) or []
        except Exception:
            return []
        visible_inputs = self.page.locator('input:not([type="hidden"])')
        matches = []
        for item in snapshot:
            if (
                item.get("autocomplete") == "one-time-code"
                or OTP_HINT_RE.search(item.get("hint") or "")
            ):
                matches.append(visible_inputs.nth(int(item["index"])))
        return matches

    def prepare_one_time_code_auth(
        self,
        phone: str | None,
        *,
        allow_standard_auth_terms: bool = False,
        before_send=None,
        after_send=None,
        authorized_resend: bool = False,
    ) -> str:
        """Prepare one conservative SMS-login request before waiting for the OTP.

        The phone value is supplied by the local canonical profile. It is never
        returned from this method or written to audit. Resend controls are never
        clicked automatically.
        """
        script = r"""() => {
          const visible = e => {
            const style = getComputedStyle(e), rect = e.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
              && !e.disabled && e.getAttribute('aria-disabled') !== 'true';
          };
          const hint = e => [
            ...(e.labels ? [...e.labels].map(x => x.innerText || '') : []),
            e.getAttribute('name') || '', e.id || '',
            e.getAttribute('placeholder') || '', e.getAttribute('aria-label') || '',
            e.getAttribute('autocomplete') || '', e.getAttribute('type') || '',
          ].join(' ');
          const otpHint = /验证码|校验码|动态码|短信码|verification[\s_-]*code|one[\s_-]*time[\s_-]*code|otp/i;
          const phoneHint = /phone|mobile|tel|手机号|手机号码|联系电话|电话/i;
          const authTerms = /隐私政策|隐私协议|privacy\s+policy|注册协议|用户协议|服务协议|terms(?:\s+of\s+(?:service|use))?|terms\s*&\s*conditions/i;
          const initialSend = /^(?:发送验证码|获取验证码|发送短信验证码|获取短信验证码|send\s+(?:the\s+)?code|get\s+(?:the\s+)?code|request\s+(?:the\s+)?code)$/i;
          const resend = /重新发送|重发|resend|\d+\s*(?:s|sec|秒).*(?:发送|重发|resend)/i;
          const inputs = [...document.querySelectorAll('input:not([type="hidden"])')];
          const otps = inputs.filter(e => visible(e) && (
            (e.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code'
            || otpHint.test(hint(e))
          ));
          if (otps.length !== 1) return {status: 'ambiguous'};

          const otp = otps[0];
          let context = otp.closest('form');
          if (!context) {
            context = otp.closest(
              '[role="dialog"], [aria-modal="true"], [class*="auth" i], '
              + '[class*="login" i], [class*="register" i], [class*="verify" i], '
              + '[class*="verification" i], [class*="otp" i], [class*="modal" i], '
              + '[class*="dialog" i]'
            );
          }
          if (!context) return {status: 'not_needed'};

          const contextText = (context.innerText || '').replace(/\s+/g, ' ').trim();
          const contextAttrs = [
            context.id || '', context.getAttribute('class') || '',
            context.getAttribute('name') || '', context.getAttribute('action') || '',
            context.getAttribute('aria-label') || '',
          ].join(' ');
          const explicitAuth = /login|log[-_ ]?in|sign[-_ ]?in|signin|auth|account|register|注册|登录|登陆|账号|账户/i
            .test(contextText + ' ' + contextAttrs);

          const phones = inputs.filter(e => {
            if (!visible(e) || !context.contains(e) || e === otp) return false;
            const type = (e.getAttribute('type') || 'text').toLowerCase();
            if (['checkbox','radio','button','submit','reset','password','file'].includes(type)) return false;
            return type === 'tel'
              || (e.getAttribute('autocomplete') || '').toLowerCase() === 'tel'
              || phoneHint.test(hint(e));
          });
          if (!phones.length) return {status: 'not_needed'};
          if (phones.length !== 1 || !explicitAuth) return {status: 'ambiguous'};

          const allControls = [...document.querySelectorAll(
            'button, input[type="button"], input[type="submit"], [role="button"], a'
          )];
          const controls = allControls.filter(e => visible(e) && context.contains(e));
          const text = e => ((e.innerText || e.value || e.getAttribute('aria-label') || '') + '')
            .replace(/\s+/g, ' ').trim();
          const initial = controls.filter(e => initialSend.test(text(e)));
          const resendControls = controls.filter(e => resend.test(text(e)));
          if (initial.length > 1 || resendControls.length > 1) return {status: 'ambiguous'};

          const checkboxes = inputs.filter(e =>
            visible(e) && context.contains(e)
            && (e.getAttribute('type') || '').toLowerCase() === 'checkbox'
          );
          const terms = checkboxes.filter(e => {
            const label = e.closest('label');
            const parent = e.parentElement;
            const t = [
              hint(e),
              label ? label.innerText || '' : '',
              parent ? parent.innerText || '' : '',
            ].join(' ');
            return authTerms.test(t);
          });

          return {
            status: initial.length === 1 ? 'prepare' : (resendControls.length ? 'already_requested' : 'not_needed'),
            phoneIndex: inputs.indexOf(phones[0]),
            sendIndex: initial.length === 1 ? allControls.indexOf(initial[0]) : -1,
            resendIndex: initial.length === 0 && resendControls.length === 1
              ? allControls.indexOf(resendControls[0]) : -1,
            consentIndices: terms.map(e => inputs.indexOf(e)),
            uncheckedConsentIndices: terms.filter(e => !e.checked).map(e => inputs.indexOf(e)),
            hasChina86: /(?:中国\s*)?\+\s*86/.test(contextText),
          };
        }"""
        try:
            result = self.page.evaluate(script) or {}
        except Exception:
            return "ambiguous"

        status = str(result.get("status") or "ambiguous")
        if authorized_resend:
            status = ("resend_prepare" if status == "already_requested"
                      and int(result.get("resendIndex", -1)) >= 0 else "ambiguous")
        if status in {"not_needed", "already_requested", "ambiguous"}:
            if status == "already_requested":
                self._otp_request_prepared = True
            return status
        if status not in {"prepare", "resend_prepare"}:
            return "ambiguous"

        digits = re.sub(r"\D", "", str(phone or ""))
        if not (6 <= len(digits) <= 15):
            return "phone_required"
        if result.get("hasChina86") and len(digits) == 13 and digits.startswith("86"):
            digits = digits[2:]

        inputs = self.page.locator('input:not([type="hidden"])')
        phone_index = int(result.get("phoneIndex", -1))
        send_index = int(result.get("resendIndex" if authorized_resend else "sendIndex", -1))
        if phone_index < 0 or send_index < 0:
            return "ambiguous"

        try:
            phone_input = inputs.nth(phone_index)
            existing_digits = re.sub(r"\D", "", phone_input.input_value() or "")
            if existing_digits and existing_digits != digits:
                return "ambiguous"

            unchecked_terms = [int(i) for i in result.get("uncheckedConsentIndices") or []]
            terms_present = bool(result.get("consentIndices"))
            terms_already_accepted = terms_present and not unchecked_terms
            if unchecked_terms and not allow_standard_auth_terms:
                return "consent_required"

            getattr(self, "mutation_guard", lambda: None)()
            if not existing_digits:
                phone_input.fill(digits)

            if unchecked_terms:
                for index in unchecked_terms:
                    checkbox = inputs.nth(index)
                    if not checkbox.is_checked():
                        getattr(self, "mutation_guard", lambda: None)()
                        checkbox.check()

            self._otp_auth_terms_allowed = bool(
                allow_standard_auth_terms or terms_already_accepted
            )
            # Filling/checking can re-render React forms. Re-prove the exact
            # send-code control after those mutations instead of trusting the
            # pre-mutation global nth() index.
            if authorized_resend:
                current = self.page.evaluate(script) or {}
                if (current.get("status") != "already_requested"
                        or int(current.get("resendIndex", -1)) < 0):
                    return "ambiguous"
                send_index = int(current["resendIndex"])
            else:
                send_index = self._current_sms_initial_send_index()
            if send_index < 0:
                return "ambiguous"
            send = self.page.locator(BUTTON_SELECTOR).nth(send_index)
            send_text = " ".join(
                (
                    send.get_attribute("value")
                    or send.get_attribute("aria-label")
                    or send.inner_text()
                    or ""
                ).split()
            )
            if (
                ((not OTP_RESEND_CONTROL_RE.search(send_text)) if authorized_resend
                 else (not OTP_INITIAL_SEND_CONTROL_RE.fullmatch(send_text)
                       or OTP_RESEND_CONTROL_RE.search(send_text)))
                or is_final_submit(send_text)
                or is_initial_apply(send_text)
                or ACCOUNT_OR_DESTRUCTIVE_RE.search(send_text)
            ):
                return "ambiguous"
            getattr(self, "mutation_guard", lambda: None)()
            if before_send is not None:
                try:
                    before_send()
                except (RuntimeError, ValueError):
                    return "send_outcome_unknown"
            send.click()
            if after_send is not None:
                after_send()
            self._otp_request_prepared = True
            self.page.wait_for_timeout(800)
            self._adopt_owned_page()
            return "requested"
        except Exception:
            raise BrowserOwnershipError("SMS preparation outcome unknown") from None

    def _current_sms_initial_send_index(self) -> int:
        """Return the one current send-code control in a proven SMS auth context."""
        try:
            value = self.page.evaluate(r"""() => {
              const visible = e => {
                const style = getComputedStyle(e), rect = e.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
                  && !e.disabled && e.getAttribute('aria-disabled') !== 'true';
              };
              const hint = e => [
                ...(e.labels ? [...e.labels].map(x => x.innerText || '') : []),
                e.getAttribute('name') || '', e.id || '',
                e.getAttribute('placeholder') || '', e.getAttribute('aria-label') || '',
                e.getAttribute('autocomplete') || '', e.getAttribute('type') || '',
              ].join(' ');
              const otpHint = /验证码|校验码|动态码|短信码|verification[\s_-]*code|one[\s_-]*time[\s_-]*code|otp/i;
              const phoneHint = /phone|mobile|tel|手机号|手机号码|联系电话|电话/i;
              const initialSend = /^(?:发送验证码|获取验证码|发送短信验证码|获取短信验证码|send\s+(?:the\s+)?code|get\s+(?:the\s+)?code|request\s+(?:the\s+)?code)$/i;
              const inputs = [...document.querySelectorAll('input:not([type="hidden"])')];
              const otps = inputs.filter(e => visible(e) && (
                (e.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code'
                || otpHint.test(hint(e))
              ));
              if (otps.length !== 1) return -1;
              const otp = otps[0];
              let context = otp.closest('form');
              if (!context) {
                context = otp.closest(
                  '[role="dialog"], [aria-modal="true"], [class*="auth" i], '
                  + '[class*="login" i], [class*="register" i], [class*="verify" i], '
                  + '[class*="verification" i], [class*="otp" i], [class*="modal" i], '
                  + '[class*="dialog" i]'
                );
              }
              if (!context) return -1;
              const contextText = (context.innerText || '').replace(/\s+/g, ' ').trim();
              const contextAttrs = [
                context.id || '', context.getAttribute('class') || '',
                context.getAttribute('name') || '', context.getAttribute('action') || '',
                context.getAttribute('aria-label') || '',
              ].join(' ');
              if (!/login|log[-_ ]?in|sign[-_ ]?in|signin|auth|account|register|注册|登录|登陆|账号|账户/i
                    .test(contextText + ' ' + contextAttrs)) return -1;
              const phones = inputs.filter(e => {
                if (!visible(e) || !context.contains(e) || e === otp) return false;
                const type = (e.getAttribute('type') || 'text').toLowerCase();
                if (['checkbox','radio','button','submit','reset','password','file'].includes(type)) return false;
                return type === 'tel'
                  || (e.getAttribute('autocomplete') || '').toLowerCase() === 'tel'
                  || phoneHint.test(hint(e));
              });
              if (phones.length !== 1) return -1;
              const all = [...document.querySelectorAll(
                'button, input[type="button"], input[type="submit"], [role="button"], a'
              )];
              const text = e => ((e.innerText || e.value || e.getAttribute('aria-label') || '') + '')
                .replace(/\s+/g, ' ').trim();
              const sends = all.filter(e => visible(e) && context.contains(e) && initialSend.test(text(e)));
              return sends.length === 1 ? all.indexOf(sends[0]) : -1;
            }""")
            return int(value)
        except Exception:
            return -1

    def _has_preparable_sms_auth(self) -> bool:
        """Read-only proof that the visible OTP belongs to a unique SMS-login setup."""
        return self._current_sms_initial_send_index() >= 0

    def _sms_context_excludes_password(self) -> bool:
        """A separate password mode cannot block a proven SMS form."""
        try:
            return bool(self.page.evaluate(r"""() => {
              const visible = e => {
                const s = getComputedStyle(e), r = e.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden'
                  && (r.width > 0 || r.height > 0 || e.getClientRects().length > 0)
                  && !e.disabled;
              };
              const otps = [...document.querySelectorAll('input')].filter(e =>
                visible(e) && ((e.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code'
                || /验证码|校验码|动态码|短信码|otp|one[ -]?time[ -]?code/i.test([
                  e.id || '', e.name || '', e.placeholder || '', e.getAttribute('aria-label') || '',
                  ...(e.labels ? [...e.labels].map(x => x.innerText || '') : [])
                ].join(' '))));
              if (otps.length !== 1) return false;
              const context = otps[0].closest('form') || otps[0].closest(
                '[role="dialog"], [aria-modal="true"], [class*="auth" i], [class*="login" i]');
              return !!context && ![...context.querySelectorAll('input[type="password"]')].some(visible);
            }"""))
        except Exception:
            return False

    def _visible_alternative_auth_challenge(self) -> bool:
        """Detect QR/face/security-key auth only inside a visible auth container."""
        try:
            return bool(self.page.evaluate(r"""() => {
              const visible = e => {
                const style = getComputedStyle(e), rect = e.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0);
              };
              const selectors = [
                '[role="dialog"]',
                '[aria-modal="true"]',
                '[class*="login" i]',
                '[class*="signin" i]',
                '[class*="auth" i]',
                '[class*="verify" i]',
                '[id*="login" i]',
                '[id*="signin" i]',
                '[id*="auth" i]',
                '[id*="verify" i]'
              ];
              const containers = [...new Set(
                selectors.flatMap(selector => [...document.querySelectorAll(selector)])
              )].filter(visible);
              const alternative = /扫码登录|二维码|qr[ -]?code|face (?:id|verification)|人脸|安全密钥/i;
              const auth = /登录|登陆|sign\s*in|log\s*in|账号|账户|account|authentication|身份验证|安全登录/i;
              return containers.some(container => {
                const text = (container.innerText || '').replace(/\s+/g, ' ').trim();
                const attrs = [
                  container.id || '',
                  container.getAttribute('class') || '',
                  container.getAttribute('aria-label') || ''
                ].join(' ');
                return alternative.test(text) && (
                  auth.test(text) || /login|signin|auth|verify/i.test(attrs)
                );
              });
            }"""))
        except Exception:
            return False

    def auth_challenge_kind(self) -> str | None:
        try:
            kinds: set[str] = set()
            otp_candidates = self._otp_candidates()
            if otp_candidates:
                kinds.add("one_time_code")
            password_count = self.page.evaluate(r"""() => {
              const otpHint = /验证码|校验码|动态码|短信码|verification[\s_-]*code|one[\s_-]*time[\s_-]*code|otp/i;
              return [...document.querySelectorAll('input[type="password"]')].filter(e => {
                const style = getComputedStyle(e), rect = e.getBoundingClientRect();
                const visible = style.display !== 'none' && style.visibility !== 'hidden'
                  && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
                  && !e.disabled && e.getAttribute('aria-disabled') !== 'true';
                const hint = [
                  ...(e.labels ? [...e.labels].map(label => label.innerText || '') : []),
                  e.getAttribute('name') || '', e.id || '',
                  e.getAttribute('placeholder') || '', e.getAttribute('aria-label') || '',
                ].join(' ');
                return visible
                  && e.getAttribute('autocomplete') !== 'one-time-code'
                  && !otpHint.test(hint);
              }).length;
            }""")
            if password_count > 0:
                kinds.add("password")
            if self.page.locator(
                'iframe[src*="captcha" i], iframe[title*="captcha" i], [class*="captcha" i]:visible'
            ).count() > 0:
                kinds.add("captcha")
            body = (self.page.locator("body").inner_text(timeout=2500) or "")[-7000:]
            if re.search(r"captcha|verify you are human|人机验证|滑块|滑动.*验证|拖动.*验证|图形验证码|图片验证|slide.*verify|drag.*puzzle", body, re.I):
                kinds.add("captcha")
            if self._visible_alternative_auth_challenge():
                kinds.add("other")
            if ("password" in kinds and "one_time_code" in kinds
                    and self._sms_context_excludes_password()
                    and (bool(getattr(self, "_otp_request_prepared", False))
                         or self._has_preparable_sms_auth())):
                kinds.discard("password")
            if not kinds and re.search(
                r"verification code|one.?time code|\botp\b|two.?factor|multi.?factor|"
                r"验证码|校验码|动态码|短信码|安全验证|二次验证",
                body,
                re.I,
            ) and self.page.locator(VISIBLE_FIELD_SELECTOR).count() < 12:
                kinds.add("other")
            if "one_time_code" in kinds and not ({"captcha", "password"} & kinds):
                if kinds == {"one_time_code"}:
                    return "one_time_code"
                # QR/face/security alternatives may coexist in the same login dialog.
                # Override them only when a read-only proof finds one phone field and
                # one initial send-code control in that same explicit auth context.
                if kinds <= {"one_time_code", "other"} and (
                    bool(getattr(self, "_otp_request_prepared", False))
                    or self._has_preparable_sms_auth()
                ):
                    return "one_time_code"
            return next(iter(kinds)) if len(kinds) == 1 else "other" if kinds else None
        except Exception:
            return "other"

    def auth_challenge(self) -> bool:
        return self.auth_challenge_kind() is not None

    def current_page_hostname(self) -> str:
        try:
            return urlparse(self.page.url).hostname or ""
        except Exception:
            return super().current_page_hostname()

    def enter_one_time_code(self, code: str) -> bool:
        getattr(self, "mutation_guard", lambda: None)()
        if not re.fullmatch(r"\d{4,8}", code or ""):
            return False
        candidates = self._otp_candidates()
        if len(candidates) != 1:
            return False
        try:
            candidates[0].fill(code)
            self.page.wait_for_timeout(1500)
            self._adopt_owned_page()
            return True
        except Exception:
            raise BrowserOwnershipError("OTP entry outcome unknown") from None

    def confirm_one_time_code_auth(self) -> bool:
        """Confirm OTP auth only inside the unique, conservative OTP context.

        The in-page pass proves that a unique visible OTP field already contains a
        valid code, chooses its closest form (or a nearest explicit auth/dialog
        container), and rejects application-like contexts. Python then applies the
        shared final-submit/apply detectors before allowing one exact whitelist
        match to be clicked.
        """
        script = r"""() => {
          const visible = e => {
            const style = getComputedStyle(e), rect = e.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && (rect.width > 0 || rect.height > 0 || e.getClientRects().length > 0)
              && !e.disabled && e.getAttribute('aria-disabled') !== 'true';
          };
          const hint = e => [
            ...(e.labels ? [...e.labels].map(x => x.innerText || '') : []),
            e.getAttribute('name') || '', e.id || '',
            e.getAttribute('placeholder') || '', e.getAttribute('aria-label') || '',
          ].join(' ');
          const otpHint = /验证码|校验码|动态码|短信码|verification[\s_-]*code|one[\s_-]*time[\s_-]*code|otp/i;
          const inputs = [...document.querySelectorAll('input:not([type="hidden"])')];
          const otps = inputs.filter(e => visible(e) && (
            (e.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code'
            || otpHint.test(hint(e))
          ));
          if (otps.length !== 1 || !/^\d{4,8}$/.test(otps[0].value || '')) {
            return {safe: false, controls: []};
          }

          const otp = otps[0];
          let context = otp.closest('form');
          if (!context) {
            context = otp.closest(
              '[role="dialog"], [aria-modal="true"], [class*="auth" i], '
              + '[class*="login" i], [class*="verify" i], [class*="verification" i], '
              + '[class*="otp" i], [class*="modal" i], [class*="dialog" i]'
            );
          }
          if (!context) return {safe: false, controls: []};

          const contextText = (context.innerText || '').replace(/\s+/g, ' ').trim();
          const contextAttrs = [
            context.id || '',
            context.getAttribute('class') || '',
            context.getAttribute('name') || '',
            context.getAttribute('action') || '',
            context.getAttribute('aria-label') || '',
          ].join(' ');
          const otherInputs = [...context.querySelectorAll('input:not([type="hidden"])')]
            .filter(e => visible(e) && e !== otp
              && !['button', 'submit', 'reset', 'checkbox', 'radio'].includes((e.type || '').toLowerCase()));
          const identifier = /phone|mobile|tel|e-?mail|account|username|手机|电话|邮箱|账号|用户名/i;
          if (otherInputs.length > 1 || otherInputs.some(e => !identifier.test(hint(e)))) {
            return {safe: false, controls: []};
          }
          const authAttrs = /login|log[-_ ]?in|sign[-_ ]?in|signin|auth|session|account|登录|登陆/i;
          const authText = /登录|登陆|sign\s*in|log\s*in|账号|账户|account|authentication|身份验证|安全登录/i;
          const explicitAuthContext = (
            authAttrs.test(contextAttrs)
            || authText.test(contextText)
            || otherInputs.some(e => identifier.test(hint(e)))
          );
          const applicationContext = /submit application|apply now|application|job[-_ ]?apply|提交申请|确认投递|立即投递|正式投递|申请职位|投递|报名|应聘|简历|教育经历|工作经历|项目经历/i;
          if (applicationContext.test(contextText + ' ' + contextAttrs) && !explicitAuthContext) {
            return {safe: false, controls: []};
          }

          const selector = 'button, input[type="button"], input[type="submit"], [role="button"], a';
          const all = [...document.querySelectorAll(selector)];
          const controls = [...context.querySelectorAll(selector)]
            .filter(visible)
            .map(e => ({
              index: all.indexOf(e),
              text: ((e.innerText || e.value || e.getAttribute('aria-label') || '') + '')
                .replace(/\s+/g, ' ').trim(),
            }))
            .filter(x => x.index >= 0 && x.text);
          return {safe: true, authContext: explicitAuthContext, controls};
        }"""
        try:
            result = self.page.evaluate(script) or {}
            if not result.get("safe"):
                return False
            eligible = []
            auth_context = bool(result.get("authContext"))
            for item in result.get("controls") or []:
                text = str(item.get("text") or "")
                normalized = " ".join(text.split()).casefold()
                register_login = bool(OTP_REGISTER_LOGIN_RE.fullmatch(normalized))
                if normalized not in OTP_CONFIRM_TEXTS and not register_login:
                    continue
                if normalized in OTP_CONTEXTUAL_AUTH_CONFIRM_TEXTS and not auth_context:
                    continue
                if (
                    is_final_submit(text)
                    or is_initial_apply(text)
                    or OTP_SEND_CONTROL_RE.search(text)
                ):
                    continue
                if ACCOUNT_OR_DESTRUCTIVE_RE.search(text) and not (
                    register_login
                    and auth_context
                    and bool(getattr(self, "_otp_auth_terms_allowed", False))
                ):
                    continue
                eligible.append(item)
            if len(eligible) != 1:
                return False
            index = int(eligible[0]["index"])
            getattr(self, "mutation_guard", lambda: None)()
            self.page.locator(BUTTON_SELECTOR).nth(index).click()
            self.page.wait_for_timeout(1800)
            self._adopt_owned_page()
            return True
        except Exception:
            raise BrowserOwnershipError("OTP confirmation outcome unknown") from None

    def otp_field_status(self) -> str:
        count = len(self._otp_candidates())
        if count == 1:
            return "unique"
        return "ambiguous" if count > 1 else "unavailable"

    def start_application(self) -> bool:
        getattr(self, "mutation_guard", lambda: None)()
        matches = [(el, text) for el, text in self._buttons() if is_initial_apply(text) and not is_final_submit(text)]
        if len(matches) != 1:
            return False
        getattr(self, "mutation_guard", lambda: None)()
        matches[0][0].click()
        self.page.wait_for_timeout(1000)
        self._adopt_owned_page()
        return True

    def save_draft(self) -> bool:
        getattr(self, "mutation_guard", lambda: None)()
        matches = [
            (element, text) for element, text in self._buttons()
            if re.search(r"^(?:save draft|save as draft|保存草稿|暂存)$", " ".join(text.split()), re.I)
        ]
        if len(matches) != 1:
            return False
        getattr(self, "mutation_guard", lambda: None)()
        matches[0][0].click()
        self.page.wait_for_timeout(800)
        self._adopt_owned_page()
        return True

    def verify_draft_persistence(self, plan: ApplicationPlan) -> dict | None:
        """Site drivers must prove a retained draft by independent readback.

        DOM values, a clicked save button, and a fixed wait are insufficient.
        The generic adapter has no authenticated site contract for this proof.
        """
        return None

    def advance(self) -> bool:
        """Generic labels cannot prove that Continue is not the final submit."""
        return False

    def next_control(self) -> bool:
        """Read-only navigation preflight; a draft must be proven before leaving it."""
        return any(is_next(text) and not is_final_submit(text)
                   for _, text in self._buttons())

    def final_submit_control(self) -> str | None:
        matches = [text for _, text in self._buttons() if is_final_submit(text)]
        return matches[0] if len(matches) == 1 else None

    def submit(self) -> dict:
        raise RuntimeError("automated final submission is disabled; user click required")

    def verify_submission(self) -> SubmissionVerification:
        try:
            body = (self.page.locator("body").inner_text(timeout=5000) or "")[-12000:]
        except Exception:
            body = ""
        match = re.search(
            r"application (?:has been )?submitted|thank you for applying|"
            r"申请已提交|投递成功|申请成功|提交成功",
            body,
            re.I,
        )
        app_id = None
        id_match = re.search(r"(?:application|申请)\s*(?:id|编号)?[:：#\s]+([A-Za-z0-9_-]{6,})", body, re.I)
        if id_match:
            app_id = id_match.group(1)
        return SubmissionVerification(
            verified=bool(match),
            level="page_signal" if match else "none",
            application_id=app_id,
            status="submitted" if match else None,
            evidence={"matched_success_text": match.group(0) if match else None, "url": self.page.url},
        )

    def screenshot(self, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        self.page.screenshot(path=str(target), full_page=True)
        target.chmod(0o600)
