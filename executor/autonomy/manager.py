from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..resolver import DeepSeekMapper
from ..settings import load_settings
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
        "checkpoint": str(task.get("checkpoint") or ""),
        "blocker": str(task.get("blocker") or ""),
        "unresolved_keys": unresolved,
        "attempts": int(task.get("attempts") or 0),
    }


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
            "when the user's message itself contains the exact target URL. ANSWER_PENDING "
            "may only use a value explicitly present in the user's current message. "
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
        (?:api[_-]?key|token|password|passwd|pwd|cookie|secret)
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


def _contains_sensitive_credential(message: str) -> bool:
    return bool(_SECRET_RE.search(message) or _STRUCTURED_SECRET_RE.search(message))


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
            r"(?<![0-9A-Za-z_.])"
            r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
            r"(?:[eE][+-]?\d+)?"
            r"(?![0-9A-Za-z_.])",
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

    def _apply(self, decision: ManagerDecision, message: str) -> dict[str, Any]:
        action = decision.action
        if action == ManagerAction.REPORT:
            return {"action": str(action), "status": "observed"}

        if action == ManagerAction.RESUME:
            if not _RESUME_RE.search(message):
                return {"action": str(action), "status": "denied", "reason": "explicit_resume_required"}
            task = self.queue.get(decision.task_id)
            if task["stage"] == "READY_TO_SUBMIT":
                return {"action": str(action), "status": "denied", "reason": "manual_submit_gate"}
            self.queue.resume(decision.task_id)
            return {"action": str(action), "status": "accepted", "task_id": decision.task_id}

        if action == ManagerAction.PAUSE:
            if not _PAUSE_RE.search(message):
                return {"action": str(action), "status": "denied", "reason": "explicit_pause_required"}
            self.queue.pause(decision.task_id)
            return {"action": str(action), "status": "accepted", "task_id": decision.task_id}

        if action == ManagerAction.CANCEL:
            if not _CANCEL_RE.search(message):
                return {"action": str(action), "status": "denied", "reason": "explicit_cancel_required"}
            self.worker.broker.discard(decision.task_id)
            with self.worker.answers_lock:
                self.worker.answers.pop(decision.task_id, None)
            self.queue.cancel(decision.task_id)
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
            spec = TaskSpec(
                company=decision.company,
                role=decision.role,
                target_url=decision.target_url,
                profile_ref=self._profile_ref(),
                live_authorized=True,
            )
            task = self.queue.enqueue(spec)
            return {"action": str(action), "status": "accepted", "task_id": task["task_id"]}

        return {"action": str(action), "status": "denied", "reason": "unsupported_action"}

    def handle(self, message: str) -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("invalid manager message")
        message = message.strip()
        task_rows = self.queue.tasks()
        otp_waiting = any(
            task["stage"] == "NEEDS_USER_ACTION" and task["blocker"] == "otp_waiting"
            for task in task_rows
        )
        # Stop credentials locally before a provider payload can be built.
        # OTP has a dedicated local broker and must never be forwarded to DeepSeek.
        if _contains_sensitive_credential(message) or (
            otp_waiting and _RAW_OTP_RE.fullmatch(message)
        ):
            return {
                "reply": (
                    "检测到验证码或登录凭据。为避免发送给 DeepSeek，此消息已在本地拦截；"
                    "验证码请使用本地 OTP 流程，密码、Cookie、Token 或密钥不要粘贴到聊天中。"
                ),
                "actions": [],
                **self.state(),
            }
        tasks = [safe_task_view(task) for task in task_rows]
        try:
            turn = self.provider.decide(message, tasks)
        except RuntimeError:
            return {
                "reply": "DeepSeek manager is unavailable. Existing queued tasks are unchanged.",
                "actions": [],
                **self.state(),
            }
        actions = []
        for decision in turn.decisions:
            try:
                actions.append(self._apply(decision, message))
            except (KeyError, ValueError, RuntimeError):
                actions.append(
                    {
                        "action": str(decision.action),
                        "status": "denied",
                        "reason": "state_or_policy_conflict",
                    }
                )
        reply = turn.reply
        if actions:
            accepted = [item["action"] for item in actions if item.get("status") in {"accepted", "observed"}]
            denied = [item["action"] for item in actions if item.get("status") == "denied"]
            summary = []
            if accepted:
                summary.append("已执行/确认：" + "、".join(accepted))
            if denied:
                summary.append("被系统门禁拒绝：" + "、".join(denied))
            if summary:
                reply = reply.rstrip() + "\n\n系统执行结果：" + "；".join(summary) + "。"
        return {
            "reply": reply,
            "actions": actions,
            **self.state(),
        }
