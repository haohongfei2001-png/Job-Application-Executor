from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .messages import find_recent_sms_code

ROOT = Path.home() / "Job-Application-Executor"
DEFAULT_CONFIG = ROOT / "config" / "otp-bridge.json"
DEFAULT_RULES = ROOT / "config" / "otp-rules.json"


class OtpBridgeError(RuntimeError):
    pass


class OtpBridge:
    def __init__(self, config_path=DEFAULT_CONFIG, *, config=None, messages_finder=find_recent_sms_code):
        self.config_path = Path(config_path).expanduser()
        self.config = dict(config or self._load_json(self.config_path) or {})
        self.messages_finder = messages_finder

    @staticmethod
    def _load_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    @property
    def enabled(self) -> bool:
        return self.relay_enabled or self.local_messages_enabled

    @property
    def relay_enabled(self) -> bool:
        return bool(self.config.get("enabled") and self.config.get("endpoint") and self.config.get("token"))

    @property
    def local_messages_enabled(self) -> bool:
        return bool(self.config.get("mac_messages_enabled") or
                    (self.relay_enabled and self.config.get("mac_messages_fallback", True)))

    def _post(self, action: str, **payload):
        if not self.relay_enabled:
            raise OtpBridgeError("OTP bridge is not configured")
        body = json.dumps({"action": action, **payload}).encode("utf-8")
        request = urllib.request.Request(
            str(self.config["endpoint"]),
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-job-otp-token": str(self.config["token"]),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OtpBridgeError(f"OTP relay HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OtpBridgeError(f"OTP relay unavailable: {type(exc).__name__}") from exc

    def request(self, site: str, ttl_seconds: int | None = None,
                *, attempt_id: str | None = None):
        ttl = int(ttl_seconds or self.config.get("timeout_seconds") or 180)
        payload = {"site": site, "ttl_seconds": ttl}
        if attempt_id:
            payload["attempt_id"] = attempt_id
        return self._post("request", **payload)

    def claim(self, request_id: str):
        return self._post("claim", request_id=request_id)

    def cancel(self, request_id: str):
        try:
            return self._post("cancel", request_id=request_id)
        except OtpBridgeError:
            return None

    def _rule_for(self, site: str) -> dict:
        rules = self._load_json(DEFAULT_RULES) or {}
        host = (urlparse(site).hostname or site).casefold()
        return dict(rules.get(site) or rules.get(host) or {})

    def wait_for_code(self, site: str, timeout_seconds: int | None = None,
                      *, attempt_id: str | None = None,
                      requested_at: float | None = None):
        if not self.enabled:
            return None
        timeout = int(timeout_seconds or self.config.get("timeout_seconds") or 180)
        poll = max(0.25, float(self.config.get("poll_interval_seconds") or 1.0))
        start = time.time()
        request_id = None
        if self.relay_enabled:
            try:
                request = self.request(site, timeout, attempt_id=attempt_id)
                request_id = request.get("request_id")
                if not request_id:
                    raise OtpBridgeError("OTP relay did not return a request id")
            except OtpBridgeError:
                if not self.local_messages_enabled:
                    raise
        rule = self._rule_for(site)

        try:
            while time.time() - start < timeout:
                if request_id:
                    try:
                        response = self.claim(request_id)
                    except OtpBridgeError:
                        if not self.local_messages_enabled:
                            raise
                        request_id = None
                        response = {}
                    if response.get("status") == "received" and response.get("code"):
                        if attempt_id and response.get("attempt_id") != attempt_id:
                            return None
                        result = {"code": str(response["code"]), "source": "iphone_relay"}
                        if attempt_id:
                            result.update({"attempt_id": attempt_id, "origin": site.casefold()})
                        return result
                    if response.get("status") in {"expired", "missing", "consumed"}:
                        return None
                if self.local_messages_enabled:
                    try:
                        hit = self.messages_finder(
                            window_seconds=min(timeout, 300),
                            sender_hint=rule.get("sender_hint"),
                            body_keyword=rule.get("body_keyword"),
                            not_before=(requested_at if requested_at is not None else start) - 3,
                        )
                    except (OSError, PermissionError):
                        hit = None
                    if hit and hit.get("code"):
                        if request_id:
                            self.cancel(request_id)
                        result = {"code": str(hit["code"]), "source": "mac_messages"}
                        if attempt_id:
                            result.update({"attempt_id": attempt_id, "origin": site.casefold()})
                        return result
                time.sleep(poll)
        finally:
            if request_id:
                self.cancel(request_id)
        return None
