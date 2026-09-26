from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..resolver import DeepSeekMapper
from ..discovery.core import DiscoveryRequest, DiscoveryResult
from ..discovery.service import discover
from ..settings import load_settings
from ..profile import DEFAULT_ALIASES
from ..evidence import BOOL_KEYS
from ..target_resolver import is_oppo_campus_landing, resolve_known_landing
from .queue import TaskQueue, TaskSpec


class ManagerAction(StrEnum):
    REPORT = "REPORT"
    RESUME = "RESUME"
    PAUSE = "PAUSE"
    CANCEL = "CANCEL"
    ANSWER_PENDING = "ANSWER_PENDING"
    CREATE_TASK = "CREATE_TASK"


class ManagerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ManagerAction
    task_id: str = Field(default="", max_length=120)
    field_key: str = Field(default="", max_length=180)
    value: str | int | bool | float | None = None
    company: str = Field(default="", max_length=150)
    role: str = Field(default="", max_length=200)
    target_url: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=500)

    @field_validator("task_id", "field_key")
    @classmethod
    def identifiers(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", value):
            raise ValueError("invalid identifier")
        return value


class ManagerTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=3000)
    decisions: list[ManagerDecision] = Field(default_factory=list, max_length=8)


def safe_task_view(task: dict[str, Any]) -> dict[str, Any]:
    """Return only operational metadata safe to expose to the model/UI."""
    spec = task.get("spec") or {}
    host = ""
    try:
        host = urlsplit(str(spec.get("target_url") or "")).hostname or ""
    except Exception:
        pass
    details = task.get("details") or {}
    unresolved = [
        key for key in details.get("unresolved_keys", [])
        if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,150}", key)
    ]
    return {
        "task_id": str(task.get("task_id") or ""),
        "company": str(spec.get("company") or "")[:150],
        "role": str(spec.get("role") or "")[:200],
        "target_host": host,
        "job_id": str(spec.get("job_id") or "")[:150],
        "campaign": str(spec.get("campaign") or "")[:150],
        "stage": str(task.get("stage") or ""),
        "revision": int(task.get("revision") or 0),
        "run_state": str(task.get("run_state") or ""),
        "checkpoint": str(task.get("checkpoint") or ""),
        "blocker": str(task.get("blocker") or ""),
        "unresolved_keys": unresolved,
        "reusable_keys": [key for key in unresolved if key in DEFAULT_ALIASES
                          and not key.startswith("policy.")],
        "boolean_keys": [key for key in unresolved if key in BOOL_KEYS],
        "fact_reuse_status": task.get("fact_reuse_status")
        if task.get("fact_reuse_status") in {"SAVED", "PENDING"} else None,
        "attempts": int(task.get("attempts") or 0),
    }


def _provider_context(message: str, task_rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Send only local intent flags and bounded queue state to the provider.

    Arbitrary chat text, URLs, applicant facts, and externally sourced task
    descriptions have no safe general-purpose redactor. Never put them in a
    provider request, even if a known-pattern scanner found no secret.
    """
    context = {
        "intent": {
            "status": True,
            "pause": _explicit_control(message, _PAUSE_RE),
            "resume": _explicit_control(message, _RESUME_RE),
            "cancel": _explicit_control(message, _CANCEL_RE),
            "apply": bool(_APPLY_RE.search(message)),
            "target_url_supplied": bool(re.search(r"https?://\S+", message, re.I)),
        },
        "referenced_task_ids": [
            str(task["task_id"])
            for task in task_rows
            if _selected_task_is_explicit(message, task, task_rows)
        ],
        "private_answer_pending": any(
            task.get("stage") == "NEEDS_USER_INPUT" for task in task_rows
        ),
    }
    tasks = [
        {key: view[key] for key in ("task_id", "stage", "revision", "run_state")}
        for view in (safe_task_view(task) for task in task_rows)
    ]
    return json.dumps(context, ensure_ascii=False, sort_keys=True), tasks


class DeepSeekManagerProvider:
    """Semantic manager. It can propose only typed high-level decisions."""

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or load_settings()
        # Reuse the existing DeepSeek configuration and Keychain lookup without
        # duplicating or persisting credentials.
        self.client = DeepSeekMapper(self.settings)

    @property
    def available(self) -> bool:
        return self.client.available

    def decide(self, message: str, tasks: list[dict[str, Any]]) -> ManagerTurn:
        if not self.available:
            raise RuntimeError("DeepSeek manager unavailable")
        system = (
            "You are the local Job Application Manager. Decide what high-level action "
            "the deterministic executor should take. You never receive arbitrary shell, "
            "JavaScript, CDP, filesystem, or final-submit capabilities. Never invent "
            "applicant facts. Never expose or request passwords, cookies, tokens or OTPs. "
            "Unknown objective facts must remain for the user. CAPTCHA, slider, QR, face "
            "or security-device challenges remain human actions. Final application submit "
            "is always performed by the user. Return strict JSON: "
            '{"reply":"...", "decisions":[{"action":"REPORT|RESUME|PAUSE|CANCEL|ANSWER_PENDING|CREATE_TASK",'
            '"task_id":"","field_key":"","value":null,"company":"","role":"","target_url":"","reason":""}]}. '
            "Use REPORT when no state change is justified. CREATE_TASK is allowed only "
            "when the user's message itself contains the exact target URL. The provider "
            "does not receive raw URLs or applicant answers, so do not propose CREATE_TASK "
            "or ANSWER_PENDING from this minimized context. Those values use local inputs. "
            "Do not claim that a state-changing action has already succeeded; describe intent only. "
            "The deterministic controller will append the actual execution result."
        )
        payload = {
            "model": self.client.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"message": message, "tasks": tasks},
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "reasoning_effort": self.client.reasoning_effort,
            "max_tokens": max(700, self.client.max_tokens),
            "stream": False,
        }
        req = urllib.request.Request(
            self.client.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.client.api_key,
            },
            method="POST",
        )
        handlers = []
        if self.client.proxy:
            handlers.append(
                urllib.request.ProxyHandler(
                    {"http": self.client.proxy, "https": self.client.proxy}
                )
            )
        opener = urllib.request.build_opener(*handlers)
        try:
            try:
                response = opener.open(req, timeout=self.client.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in {400, 422}:
                    raise
                fallback = dict(payload)
                fallback.pop("thinking", None)
                fallback.pop("reasoning_effort", None)
                req = urllib.request.Request(
                    self.client.base_url + "/chat/completions",
                    data=json.dumps(fallback, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + self.client.api_key,
                    },
                    method="POST",
                )
                response = opener.open(req, timeout=self.client.timeout)
            with response as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = str(result["choices"][0]["message"].get("content") or "").strip()
            try:
                parsed = json.loads(content)
            except Exception:
                match = re.search(r"\{.*\}", content, re.S)
                if not match:
                    raise ValueError("manager response is not JSON")
                parsed = json.loads(match.group(0))
            return ManagerTurn.model_validate(parsed)
        except Exception as exc:
            raise RuntimeError("DeepSeek manager request failed") from exc


_RESUME_RE = re.compile(r"(?:继续|恢复|接着|resume|continue|retry)", re.I)
_PAUSE_RE = re.compile(r"(?:暂停|先别|等一下|pause|hold)", re.I)
_CANCEL_RE = re.compile(r"(?:取消|停止|不投|放弃|cancel|stop|drop)", re.I)
_APPLY_RE = re.compile(r"(?:投递|申请|开始投|apply|application)", re.I)
_NEGATED_CONTROL = re.compile(r"(?:不(?:要|想|用|必|需)?|别|请勿|无须|无需|don't|do not|not|never)\s*$", re.I)


def _explicit_control(message: str, pattern: re.Pattern[str]) -> bool:
    return any(not _NEGATED_CONTROL.search(message[max(0, match.start() - 12):match.start()])
               for match in pattern.finditer(message))


_DISCOVERY_COMPANIES = ("Schneider Electric", "施耐德电气", "Schneider", "施耐德", "OPPO")


def _local_discovery_intent(message: str) -> tuple[str, str, str, str] | None:
    """Parse only explicit supported company/role phrases; never guess a URL."""
    if re.search(r"https?://\S+", message, re.I) or not _explicit_control(message, _APPLY_RE):
        return None
    company_pattern = "|".join(re.escape(item) for item in _DISCOVERY_COMPANIES)
    match = re.fullmatch(
        rf"\s*(?:请|帮我|我要|我想|请帮我)?\s*(?:投递|申请|开始投)\s*"
        rf"(?P<company>{company_pattern})\s*(?:的)?\s*(?P<role>.+?)\s*[。！!]?\s*",
        message, re.I,
    )
    if not match:
        return None
    role = match.group("role").strip(" ：:，,。！! ")
    location = ""
    location_match = re.search(r"[（(](北京|上海|深圳|广州|杭州|成都|南京|武汉|西安|重庆)[）)]$", role)
    if location_match:
        location = location_match.group(1)
        role = role[:location_match.start()].strip()
    campaign = ""
    campaign_match = re.search(r"[（(](20\d\d届校园招聘)[）)]$", role)
    if campaign_match:
        campaign = campaign_match.group(1)
        role = role[:campaign_match.start()].strip()
    role = re.sub(r"(?:岗位|职位)$", "", role).strip()
    if len(role) < 2 or len(role) > 200:
        return None
    return match.group("company"), role, location, campaign


def _selected_task_is_explicit(message: str, selected: dict[str, Any], tasks: list[dict[str, Any]]) -> bool:
    active = [task for task in tasks if task.get("stage") not in {"CANCELLED", "SUBMITTED", "VERIFIED"}]
    if len(active) <= 1:
        return True
    text = message.casefold()
    identifiers = [str(selected.get("task_id") or "")[:8]]
    spec = selected.get("spec") or {}
    for key in ("company", "role"):
        value = str(spec.get(key) or "").casefold().strip()
        if len(value) >= 3:
            identifiers.append(value)
        identifiers.extend(token for token in re.findall(r"[a-z0-9\u4e00-\u9fff]+", value)
                           if len(token) >= 3)
    for identifier in identifiers:
        if not identifier or identifier not in text:
            continue
        competing = 0
        for task in active:
            other = task.get("spec") or {}
            if (identifier in str(task.get("task_id") or "")
                    or identifier in str(other.get("company") or "").casefold()
                    or identifier in str(other.get("role") or "").casefold()):
                competing += 1
        if competing == 1:
            return True
    return False
_URL_LEFT_BOUNDARIES = set(" \t\r\n([{<\"'：，。；！？、")
_URL_RIGHT_BOUNDARIES = set(" \t\r\n>\"'")


def _url_right_boundary(message: str, end: int, target_url: str) -> bool:
    if end == len(message):
        return True
    char = message[end]
    if char in _URL_RIGHT_BOUNDARIES:
        return True
    # Common ASCII sentence punctuation is accepted only at a sentence edge.
    # Structural URL continuations stay strict: a '?' can start a query and an
    # unmatched ')' can close a URL path component such as engineer_(ml).
    next_is_edge = end + 1 == len(message) or message[end + 1].isspace()
    if not next_is_edge:
        return False
    if char in ".,;!，。；：！？、":
        return True
    if char == "?":
        return "?" in target_url
    if char == ")":
        return target_url.count("(") <= target_url.count(")")
    return False


def _target_url_is_explicit(message: str, target_url: str) -> bool:
    """Require one complete user-supplied target URL, never a model substring."""
    if not target_url or not target_url.lower().startswith(("https://", "http://")):
        return False
    start = 0
    while True:
        index = message.find(target_url, start)
        if index < 0:
            return False
        left_ok = index == 0 or message[index - 1] in _URL_LEFT_BOUNDARIES
        end = index + len(target_url)
        if left_ok and _url_right_boundary(message, end, target_url):
            return True
        start = index + 1
_SECRET_RE = re.compile(
    r"""(?ix)
    (?:
        (?:\b(?:otp|verification\s+code|one[-\s]?time\s+(?:password|code))\b|验证码|动态码|短信码)
        \s*(?:(?:is|equals)\s+|是|为|[:：=])?\s*\d{4,8}(?!\d)
    )
    |
    (?:
        (?:\b(?:password|passwd|pwd|cookie|token|api[_ -]?key|secret)\b|密码|口令|令牌|密钥)
        (?:
            \s+(?:(?:is|equals)\s+)?
            |
            \s*(?:是|为|[:：=])\s*
        )
        \S+
    )
    |
    (?:\bbearer\s+[A-Za-z0-9._~+/=-]+)
    """
)


_STRUCTURED_SECRET_RE = re.compile(
    r"""(?ix)
    ["']?
    (?:
        [A-Za-z0-9_-]*
        (?:api[_-]?key|token|password|passwd|pwd|cookie|secret|otp|verification[_-]?code)
        [A-Za-z0-9_-]*
        |
        验证码|动态码|短信码|密码|口令|令牌|密钥
    )
    ["']?
    \s*[:=]\s*
    (?:
        "(?:\\.|[^"\\])*"
        |
        '(?:\\.|[^'\\])*'
        |
        [^\s,;}]+
    )
    """
)
_RAW_OTP_RE = re.compile(r"\d{4,8}")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9][A-Z0-9 -]* )?PRIVATE KEY(?: BLOCK)?-----",
    re.I,
)
_PRIVATE_FACT_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"(?<!\d)1[3-9]\d{9}(?!\d)|"
    r"(?<!\d)\d{15,18}[0-9Xx]?(?!\d)|"
    r"CANARY_PRIVATE", re.I,
)


def _contains_sensitive_credential(message: str) -> bool:
    return bool(
        _SECRET_RE.search(message)
        or _STRUCTURED_SECRET_RE.search(message)
        or _PRIVATE_KEY_RE.search(message)
    )


def _value_is_explicit(message: str, value: Any) -> bool:
    text = message.casefold()
    if isinstance(value, bool):
        positive = ("是", "接受", "同意", "yes", "true")
        negative = ("不是", "否", "不接受", "不同意", "no", "false")
        if value:
            # Conservative: a negated phrase must never satisfy a positive value
            # merely because "接受"/"同意"/"是" is a substring of it.
            if any(option.casefold() in text for option in negative):
                return False
            return any(option.casefold() in text for option in positive)
        return any(option.casefold() in text for option in negative)
    rendered = str(value).casefold()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric_tokens = re.findall(
            r"(?<![0-9A-Za-z_.])(?<!\d[,，])"
            r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
            r"(?:[eE][+-]?\d+)?"
            r"(?![0-9A-Za-z_.])(?![,，]\d)",
            text,
        )
        return rendered in numeric_tokens
    return rendered in text


class ManagerController:
    """Policy-gated natural-language control plane for the existing executor."""

    def __init__(
        self,
        queue: TaskQueue,
        worker,
        *,
        provider: DeepSeekManagerProvider | None = None,
        settings: dict[str, Any] | None = None,
    ):
        self.queue = queue
        self.worker = worker
        self.settings = settings or load_settings()
        self._provider = provider

    @property
    def provider(self) -> DeepSeekManagerProvider:
        # Lazy construction avoids Keychain/API access in ordinary daemon/tests
        # until the user actually opens a manager conversation.
        if self._provider is None:
            self._provider = DeepSeekManagerProvider(self.settings)
        return self._provider

    def loaded_provider_state(self) -> dict[str, Any]:
        """Observe loaded state without triggering lazy credential initialization."""
        if self._provider is None:
            return {"state": "not_loaded", "available": False}
        try:
            available = self._provider.available is True
        except Exception:
            return {"state": "unavailable", "available": False}
        return {"state": "available" if available else "unavailable",
                "available": available}

    def state(self) -> dict[str, Any]:
        tasks = [safe_task_view(task) for task in self.queue.tasks()]
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task["stage"]] = counts.get(task["stage"], 0) + 1
        return {
            "tasks": tasks,
            "counts": counts,
            "manager_available": bool(
                self._provider.available if self._provider is not None else True
            ),
            "final_click_actor": "user",
        }

    def _profile_ref(self) -> str:
        value = self.settings.get("profile_path")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("profile_path is not configured")
        return value

    def prepare_local_form(self, company: str, role: str, target_url: str = "", *,
                           location: str = "", campaign: str = "",
                           employment_type: str = "",
                           selected_candidate_id: str = "") -> tuple[DiscoveryRequest, DiscoveryResult]:
        """Read-only discovery runs outside the supervisor mutation lock."""
        if not all(isinstance(value, str) for value in (
            company, role, target_url, location, campaign, employment_type, selected_candidate_id
        )):
            raise ValueError("invalid task fields")
        request = DiscoveryRequest(
            company=company.strip(), role=role.strip(), source_url=target_url.strip(),
            location=location.strip(), campaign=campaign.strip(),
            employment_type=employment_type.strip(),
        )
        return request, discover(request, selected_candidate_id=selected_candidate_id)

    def commit_local_form(self, request: DiscoveryRequest, result: DiscoveryResult, *,
                          selected_candidate_id: str = "") -> dict[str, Any]:
        """Queue only the already observed candidate under the mutation lock."""
        if result.status == "VERIFIED" and result.target is not None:
            target = result.target
            spec = TaskSpec(
                company=target.employer, role=target.title,
                target_url=target.detail_url, job_id=target.job_id,
                tenant=target.tenant, campaign=target.campaign,
                location=target.location, employment_type=target.employment_type,
                target_evidence_digest=target.evidence_digest, target_verified=True,
                target_source_chain=list(target.source_chain),
                profile_ref=self._profile_ref(), live_authorized=False,
            )
            task = self.queue.enqueue(spec)
            return {**task, "discovery": result.public_dict()}
        if (result.status != "UNSUPPORTED" or result.reason != "no_official_site_contract"
                or not request.source_url or selected_candidate_id):
            return {"discovery": result.public_dict()}
        # Legacy exact URLs remain queueable but are explicitly unverified and
        # cannot authorize a live write. Unsupported discovery is surfaced.
        spec = TaskSpec(
            company=request.company,
            role=request.role,
            target_url=request.source_url,
            profile_ref=self._profile_ref(),
            # JCR-03 owns verified target identity. A user-supplied URL alone
            # is not yet sufficient evidence for automatic external writes.
            live_authorized=False,
        )
        return {**self.queue.enqueue(spec), "discovery": result.public_dict()}

    def create_from_local_form(self, company: str, role: str, target_url: str = "", *,
                               location: str = "", campaign: str = "",
                               employment_type: str = "",
                               selected_candidate_id: str = "") -> dict[str, Any]:
        """Structured direct entry, also used by non-server clients."""
        request, result = self.prepare_local_form(
            company, role, target_url, location=location, campaign=campaign,
            employment_type=employment_type, selected_candidate_id=selected_candidate_id)
        return self.commit_local_form(request, result,
                                      selected_candidate_id=selected_candidate_id)

    def prepare_chat_discovery(self, message: str) -> tuple[DiscoveryRequest, DiscoveryResult] | None:
        if not isinstance(message, str) or len(message) > 4000:
            return None
        if _contains_sensitive_credential(message) or _PRIVATE_FACT_RE.search(message):
            return None
        intent = _local_discovery_intent(message.strip())
        if intent is None:
            return None
        company, role, location, campaign = intent
        return self.prepare_local_form(company, role, location=location, campaign=campaign)

    def finish_chat_discovery(self, prepared: tuple[DiscoveryRequest, DiscoveryResult]) -> dict[str, Any]:
        request, discovery = prepared
        discovered = self.commit_local_form(request, discovery)
        result = discovered["discovery"]
        status = result["status"]
        if "task_id" in discovered:
            reply = "已找到并核验唯一岗位，任务已加入；开始准备前仍会检查运行授权。"
            action = {"action": "CREATE_TASK", "status": "accepted",
                      "task_id": discovered["task_id"], "resolved_target": True}
        elif status == "AMBIGUOUS":
            labels = "；".join(
                f"{item['title']}（{item['location'] or '地点未注明'}、"
                f"{item['campaign'] or '批次未注明'}、职位 {item['job_id']}）"
                for item in result["candidates"][:8]
            )
            reply = "找到多个符合条件的岗位，请在左侧填写公司和岗位后选择具体候选：" + labels
            action = {"action": "DISCOVER", "status": "candidate_selection_required"}
        else:
            reply = {
                "INCOMPLETE": "公开职位列表尚未完整核验，任务未创建；请稍后重试或使用左侧表单提供官方入口。",
                "UNAVAILABLE": "没有找到符合全部条件的在招岗位，任务未创建。",
                "UNSUPPORTED": "目前不支持从这家公司自动发现岗位，任务未创建；可在左侧提供官方入口。",
            }.get(status, "岗位尚未核验，任务未创建。")
            action = {"action": "DISCOVER", "status": "denied", "reason": status.lower()}
        return {"reply": reply, "actions": [action],
                "discovery": result, **self.state()}

    def _apply(self, decision: ManagerDecision, message: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        action = decision.action
        if action == ManagerAction.REPORT:
            return {"action": str(action), "status": "observed"}

        if action == ManagerAction.RESUME:
            if not _explicit_control(message, _RESUME_RE):
                return {"action": str(action), "status": "denied", "reason": "explicit_resume_required"}
            task = self.queue.get(decision.task_id)
            if task["stage"] == "READY_TO_SUBMIT":
                return {"action": str(action), "status": "denied", "reason": "manual_submit_gate"}
            spec = task.get("spec") or {}
            if is_oppo_campus_landing(str(spec.get("target_url") or "")):
                try:
                    candidate = resolve_known_landing(
                        str(spec.get("company") or ""),
                        str(spec.get("role") or ""),
                        str(spec.get("target_url") or ""),
                    )
                except Exception:
                    candidate = None
                if candidate is None:
                    return {
                        "action": str(action),
                        "status": "denied",
                        "reason": "exact_job_resolution_required",
                    }
                self.queue.retarget_same_origin_landing(
                    decision.task_id,
                    candidate.job_url,
                    job_id=candidate.job_id,
                    tenant=candidate.tenant, campaign=candidate.campaign,
                    location=candidate.location, employment_type=candidate.employment_type,
                    evidence_digest=candidate.evidence_digest,
                    source_chain=candidate.source_chain,
                )
                return {
                    "action": str(action),
                    "status": "accepted",
                    "task_id": decision.task_id,
                    "resolved_target": True,
                }
            self.queue.control("RESUME", decision.task_id,
                command_id="manager-" + uuid.uuid4().hex,
                expected_revision=expected_revision)
            return {"action": str(action), "status": "accepted", "task_id": decision.task_id}

        if action == ManagerAction.PAUSE:
            if not _explicit_control(message, _PAUSE_RE):
                return {"action": str(action), "status": "denied", "reason": "explicit_pause_required"}
            self.queue.control("PAUSE", decision.task_id,
                command_id="manager-" + uuid.uuid4().hex,
                expected_revision=expected_revision)
            return {"action": str(action), "status": "accepted", "task_id": decision.task_id}

        if action == ManagerAction.CANCEL:
            if not _explicit_control(message, _CANCEL_RE):
                return {"action": str(action), "status": "denied", "reason": "explicit_cancel_required"}
            self.queue.control("CANCEL", decision.task_id,
                command_id="manager-" + uuid.uuid4().hex,
                expected_revision=expected_revision)
            self.worker.broker.discard(decision.task_id)
            with self.worker.answers_lock:
                self.worker.answers.pop(decision.task_id, None)
            return {"action": str(action), "status": "accepted", "task_id": decision.task_id}

        if action == ManagerAction.ANSWER_PENDING:
            task = self.queue.get(decision.task_id)
            pending = set((task.get("details") or {}).get("unresolved_keys") or [])
            if task["stage"] != "NEEDS_USER_INPUT" or decision.field_key not in pending:
                return {"action": str(action), "status": "denied", "reason": "field_not_pending"}
            if decision.value is None or not _value_is_explicit(message, decision.value):
                return {"action": str(action), "status": "denied", "reason": "value_not_explicit"}
            self.worker.user_input(
                decision.task_id,
                {decision.field_key: decision.value},
                expected_revision=expected_revision,
            )
            return {
                "action": str(action),
                "status": "accepted",
                "task_id": decision.task_id,
                "field_key": decision.field_key,
            }

        if action == ManagerAction.CREATE_TASK:
            if not decision.target_url or not _target_url_is_explicit(message, decision.target_url):
                return {"action": str(action), "status": "denied", "reason": "exact_url_required"}
            # Remove the exact, validated target before checking intent so
            # "apply" inside a URL can never authorize task creation.
            intent_text = message.replace(decision.target_url, " ")
            if not _APPLY_RE.search(intent_text):
                return {"action": str(action), "status": "denied", "reason": "explicit_apply_required"}
            if not decision.company or not decision.role:
                return {"action": str(action), "status": "denied", "reason": "target_identity_required"}

            target_url = decision.target_url
            job_id = ""
            resolved_target = False
            if is_oppo_campus_landing(target_url):
                try:
                    candidate = resolve_known_landing(
                        decision.company,
                        decision.role,
                        target_url,
                    )
                except Exception:
                    candidate = None
                if candidate is None:
                    return {
                        "action": str(action),
                        "status": "denied",
                        "reason": "exact_job_resolution_required",
                    }
                target_url = candidate.job_url
                job_id = candidate.job_id
                resolved_target = True

            spec = TaskSpec(
                company=decision.company,
                role=decision.role,
                target_url=target_url,
                job_id=job_id,
                tenant=candidate.tenant if resolved_target else "",
                campaign=candidate.campaign if resolved_target else "",
                location=candidate.location if resolved_target else "",
                employment_type=candidate.employment_type if resolved_target else "",
                target_evidence_digest=candidate.evidence_digest if resolved_target else "",
                target_source_chain=list(candidate.source_chain) if resolved_target else [],
                target_verified=bool(candidate.evidence_digest and candidate.source_chain) if resolved_target else False,
                profile_ref=self._profile_ref(),
                live_authorized=False,
            )
            task = self.queue.enqueue(spec)
            result = {"action": str(action), "status": "accepted", "task_id": task["task_id"]}
            if resolved_target:
                result["resolved_target"] = True
            return result

        return {"action": str(action), "status": "denied", "reason": "unsupported_action"}

    def handle(self, message: str) -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("invalid manager message")
        message = message.strip()
        task_rows = self.queue.tasks()
        otp_waiting = any(
            (
                task["stage"] == "NEEDS_USER_ACTION"
                and task["blocker"] in {"otp_waiting", "otp_ambiguous"}
            )
            or (
                task["stage"] == "BLOCKED"
                and task["blocker"] in {
                    "user_paused_from_otp_waiting",
                    "user_paused_from_otp_ambiguous",
                    # Compatibility with earlier marker revisions where all
                    # NEEDS_USER_ACTION pauses used this generic origin.
                    "user_paused_from_action",
                }
            )
            for task in task_rows
        )
        # Stop credentials locally before a provider payload can be built.
        # OTP has a dedicated local broker and must never be forwarded to DeepSeek.
        if _contains_sensitive_credential(message) or _PRIVATE_FACT_RE.search(message) or (
            otp_waiting and _RAW_OTP_RE.fullmatch(message)
        ):
            return {
                "reply": (
                    "检测到验证码或登录凭据。为避免发送给 DeepSeek，此消息已在本地拦截；"
                    "验证码请使用本地 OTP 流程；请不要在聊天中粘贴私人资料。"
                ),
                "actions": [],
                **self.state(),
            }
        discovery_intent = _local_discovery_intent(message)
        if discovery_intent:
            try:
                prepared = self.prepare_chat_discovery(message)
            except (ValueError, RuntimeError):
                return {"reply": "公开岗位暂时无法核验，任务未创建。", "actions": [], **self.state()}
            if prepared is not None:
                return self.finish_chat_discovery(prepared)
        # A plain, explicit resume command for one human-action wait is
        # deterministic control, not a semantic decision. Handle it locally so
        # the model cannot downgrade "继续这个岗位" into a read-only REPORT.
        # The executor still re-checks the current page and will immediately
        # block again for a real CAPTCHA/password/ambiguous challenge.
        explicit_resume = _explicit_control(message, _RESUME_RE)
        conflicting_control = bool(
            _explicit_control(message, _PAUSE_RE)
            or _explicit_control(message, _CANCEL_RE)
            or _APPLY_RE.search(message)
        )
        if explicit_resume and not conflicting_control:
            waiting = [
                task for task in task_rows
                if task.get("stage") == "NEEDS_USER_ACTION"
            ]
            if len(waiting) == 1 and _selected_task_is_explicit(message, waiting[0], task_rows):
                task = waiting[0]
                tid = task["task_id"]
                spec = task.get("spec") or {}
                landing_retargeted = False
                if is_oppo_campus_landing(str(spec.get("target_url") or "")):
                    try:
                        candidate = resolve_known_landing(
                            str(spec.get("company") or ""),
                            str(spec.get("role") or ""),
                            str(spec.get("target_url") or ""),
                        )
                    except Exception:
                        candidate = None
                    if candidate is None:
                        return {
                            "reply": (
                                "当前 OPPO 任务指向校招首页，但没有唯一解析出对应的具体岗位。"
                                "任务未继续，避免在错误页面上操作。"
                            ),
                            "actions": [{
                                "action": str(ManagerAction.RESUME),
                                "status": "denied",
                                "reason": "exact_job_resolution_required",
                            }],
                            **self.state(),
                        }
                    try:
                        self.queue.retarget_same_origin_landing(
                            tid,
                            candidate.job_url,
                            job_id=candidate.job_id,
                            tenant=candidate.tenant, campaign=candidate.campaign,
                            location=candidate.location, employment_type=candidate.employment_type,
                            evidence_digest=candidate.evidence_digest,
                            source_chain=candidate.source_chain,
                        )
                        landing_retargeted = True
                    except (KeyError, ValueError, RuntimeError):
                        return {
                            "reply": "具体岗位已解析，但当前任务无法安全切换目标；队列状态保持不变。",
                            "actions": [{
                                "action": str(ManagerAction.RESUME),
                                "status": "denied",
                                "reason": "state_or_policy_conflict",
                            }],
                            **self.state(),
                        }
                else:
                    try:
                        self.queue.resume(tid)
                    except (KeyError, ValueError, RuntimeError):
                        return {
                            "reply": "当前任务无法从该状态恢复；队列状态保持不变。",
                            "actions": [{
                                "action": str(ManagerAction.RESUME),
                                "status": "denied",
                                "reason": "state_or_policy_conflict",
                            }],
                            **self.state(),
                        }
                action_result = {
                    "action": str(ManagerAction.RESUME),
                    "status": "accepted",
                    "task_id": tid,
                }
                if landing_retargeted:
                    action_result["resolved_target"] = True
                return {
                    "reply": (
                        "已解析到具体 OPPO 岗位并切换任务目标，正在进入岗位详情继续处理。"
                        if landing_retargeted
                        else
                        "已收到明确继续指令，正在从安全检查点重新检查当前页面。"
                        "如果仍是需要人工处理的安全验证，系统会再次停住。"
                    ),
                    "actions": [action_result],
                    **self.state(),
                }
        # No arbitrary chat or task description crosses the model boundary.
        # The model receives local intent flags only. Structured task creation
        # and applicant answers remain local until later product rounds.
        provider_message, tasks = _provider_context(message, task_rows)
        try:
            turn = self.provider.decide(provider_message, tasks)
        except RuntimeError:
            return {
                "reply": "DeepSeek manager is unavailable. Existing queued tasks are unchanged.",
                "actions": [],
                **self.state(),
            }
        actions = []
        observed_revisions = {task["task_id"]: task["revision"] for task in task_rows}
        for decision in turn.decisions:
            try:
                if decision.action not in {ManagerAction.REPORT, ManagerAction.CREATE_TASK}:
                    observed = observed_revisions.get(decision.task_id)
                    if observed is None or self.queue.get(decision.task_id)["revision"] != observed:
                        actions.append({"action": str(decision.action), "status": "denied",
                                        "reason": "stale_task_revision"})
                        continue
                    selected = next(task for task in task_rows if task["task_id"] == decision.task_id)
                    if not _selected_task_is_explicit(message, selected, task_rows):
                        actions.append({"action": str(decision.action), "status": "denied",
                                        "reason": "ambiguous_task_reference"})
                        continue
                if decision.action == ManagerAction.CREATE_TASK:
                    current = {task["task_id"]: task["revision"] for task in self.queue.tasks()}
                    if current != observed_revisions:
                        actions.append({"action": str(decision.action), "status": "denied",
                                        "reason": "stale_task_context"})
                        continue
                actions.append(self._apply(decision, message,
                    expected_revision=observed_revisions.get(decision.task_id)))
            except (KeyError, ValueError, RuntimeError):
                actions.append(
                    {
                        "action": str(decision.action),
                        "status": "denied",
                        "reason": "state_or_policy_conflict",
                    }
                )
        reply = turn.reply
        if any(item["action"] != "REPORT" for item in actions):
            # A model's conversational claim is not a command receipt. Build
            # the user-visible result only from deterministic controller output.
            reply = "系统执行结果：" + "；".join(
                f"{item['action']} {item['status']}"
                + (f"（{item['reason']}）" if item.get("reason") else "")
                for item in actions
            ) + "。"
        if _APPLY_RE.search(message) and not any(
            item["action"] == "CREATE_TASK" and item["status"] == "accepted"
            for item in actions
        ):
            reply = "系统未创建任务。请在左侧填写公司、岗位和完整岗位链接；目标核验前不会自动写入招聘网站。"
        return {
            "reply": reply,
            "actions": actions,
            **self.state(),
        }
