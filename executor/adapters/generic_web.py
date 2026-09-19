from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .base import SiteAdapter
from ..browser import connect, latest_page
from ..field_classifier import is_final_submit, is_initial_apply, is_next
from ..models import (
    ApplicationPlan,
    FieldResolution,
    ResolutionStatus,
    SubmissionVerification,
    ValidationResult,
    WebField,
)


VISIBLE_FIELD_SELECTOR = 'input:not([type="hidden"]), textarea, select'
BUTTON_SELECTOR = 'button, input[type="button"], input[type="submit"], [role="button"], a'


class GenericWebAdapter(SiteAdapter):
    site_id = "generic_web"

    def __init__(self, target_url: str):
        super().__init__(target_url)
        self.pw = self.browser = self.ctx = self.page = None

    @classmethod
    def can_handle(cls, target_url: str) -> bool:
        return target_url.startswith(("http://", "https://", "file://"))

    def open(self) -> None:
        self.pw, self.browser, self.ctx, self.page = connect(self.target_url)

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
            return loc.first
        if label:
            by_label = self.page.get_by_label(label, exact=False)
            if by_label.count():
                return by_label.first
        return loc.first

    def discover_fields(self) -> list[WebField]:
        """Snapshot the whole form in one browser round-trip.

        The previous implementation crossed the Playwright boundary repeatedly for
        every field (visibility, label, type, section, value and options). Heavy
        React recruitment forms could therefore require hundreds of CDP calls per
        pass. Keep the same fallback selector semantics while collecting metadata
        in-page once.
        """
        script = r"""() => {
          const selector = 'input:not([type="hidden"]), textarea, select';
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
            const inputType = (e.getAttribute('type') || tag).toLowerCase();
            const name = e.getAttribute('name') || '';
            const id = e.id || '';
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
              selector: cssSelector,
              fieldId: name || id || ('field-' + index),
              section: section(e),
            };
          }).filter(x => x.visible);
        }"""
        try:
            snapshot = self.page.evaluate(script) or []
        except Exception:
            snapshot = []

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
                },
            )
            for item in snapshot
        ]

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

    def _fill_select(self, element, value) -> bool:
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
        for i in range(opts.count()):
            opt = opts.nth(i)
            text = (opt.inner_text() or "").strip()
            val = opt.get_attribute("value") or ""
            hay = {text.casefold().removesuffix("市"), val.casefold()}
            for target in normalized:
                if target in hay or (target and any(target in item for item in hay)):
                    element.select_option(value=val)
                    return True
        return False

    def apply_resolutions(self, resolutions: Iterable[FieldResolution]) -> list[dict]:
        actions: list[dict] = []
        for resolution in resolutions:
            if resolution.status != ResolutionStatus.RESOLVED:
                continue
            element = self._locate(resolution.selector, resolution.label)
            if not self._visible(element):
                actions.append({"field_id": resolution.field_id, "ok": False, "reason": "not visible"})
                continue
            try:
                input_type = (element.get_attribute("type") or element.evaluate("e=>e.tagName.toLowerCase()")).lower()
                tag = element.evaluate("e=>e.tagName.toLowerCase()")
                value = resolution.value
                if input_type == "file":
                    element.set_input_files(str(value))
                elif tag == "select":
                    if not self._fill_select(element, value):
                        raise ValueError("no matching select option")
                elif input_type in {"checkbox", "radio"}:
                    desired = bool(value)
                    if element.is_checked() != desired:
                        element.click()
                else:
                    desired = self._control_text(value, input_type, resolution.label)
                    current = str(element.input_value() or "")
                    if current != desired:
                        element.fill(desired)
                actions.append({"field_id": resolution.field_id, "ok": True, "source": resolution.source})
            except Exception as exc:
                actions.append({"field_id": resolution.field_id, "ok": False, "reason": type(exc).__name__})
        return actions

    def validate(self, plan: ApplicationPlan) -> ValidationResult:
        missing: list[str] = []
        errors: list[str] = []
        warnings: list[str] = []
        current_fields = self.discover_fields()
        by_selector = {item.selector: item for item in plan.fields}

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
                expected_name = Path(str(expected.value)).name
                if expected_name and expected_name not in str(actual or ""):
                    errors.append(f"{label}: attachment mismatch")
            elif field.input_type in {"checkbox", "radio"}:
                if bool(actual) != bool(expected.value):
                    errors.append(f"{label}: boolean value mismatch")
            elif field.metadata.get("tag") == "select":
                selected_text = str(field.metadata.get("selected_text") or "")
                candidates = expected.value if isinstance(expected.value, list) else [expected.value]
                normalized = {str(x).strip().casefold().removesuffix("市") for x in candidates}
                actual_norm = selected_text.casefold().removesuffix("市")
                if normalized and actual_norm not in normalized:
                    warnings.append(f"{label}: selected option uses site-specific normalization")
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

    def auth_challenge(self) -> bool:
        try:
            if self.page.locator('input[type="password"]:visible').count() > 0:
                return True
            if self.page.locator('input[autocomplete="one-time-code"]:visible').count() > 0:
                return True
            if self.page.locator('iframe[src*="captcha" i], iframe[title*="captcha" i], [class*="captcha" i]:visible').count() > 0:
                return True
            body = (self.page.locator("body").inner_text(timeout=2500) or "")[-7000:]
            challenge = re.search(
                r"captcha|verify you are human|verification code|two.?factor|multi.?factor|"
                r"验证码|人机验证|安全验证|扫码登录|二次验证",
                body,
                re.I,
            )
            return bool(challenge) and self.page.locator(VISIBLE_FIELD_SELECTOR).count() < 12
        except Exception:
            return False

    def start_application(self) -> bool:
        matches = [(el, text) for el, text in self._buttons() if is_initial_apply(text) and not is_final_submit(text)]
        if len(matches) != 1:
            return False
        matches[0][0].click()
        self.page.wait_for_timeout(1000)
        self.page = latest_page(self.ctx, self.page)
        return True

    def save_draft(self) -> bool:
        matches = [
            (element, text) for element, text in self._buttons()
            if re.search(r"^(?:save draft|save as draft|保存草稿|暂存)$", " ".join(text.split()), re.I)
        ]
        if len(matches) != 1:
            return False
        matches[0][0].click()
        self.page.wait_for_timeout(800)
        self.page = latest_page(self.ctx, self.page)
        return True

    def advance(self) -> bool:
        for element, text in self._buttons():
            if is_next(text) and not is_final_submit(text):
                element.click()
                self.page.wait_for_timeout(1000)
                self.page = latest_page(self.ctx, self.page)
                return True
        return False

    def final_submit_control(self) -> str | None:
        matches = [text for _, text in self._buttons() if is_final_submit(text)]
        return matches[0] if len(matches) == 1 else None

    def submit(self) -> dict:
        matches = [(element, text) for element, text in self._buttons() if is_final_submit(text)]
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one final submit control, found {len(matches)}")
        control, text = matches[0]
        control.click()
        self.page.wait_for_timeout(2200)
        self.page = latest_page(self.ctx, self.page)
        return {"control": text, "url": self.page.url}

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
