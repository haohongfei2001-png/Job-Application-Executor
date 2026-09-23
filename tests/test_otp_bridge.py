from executor.otp.bridge import OtpBridge
from executor.otp.bridge import OtpBridgeError


def test_wait_for_code_prefers_relay():
    bridge = OtpBridge(config={
        "enabled": True, "endpoint": "https://example.invalid", "token": "x",
        "timeout_seconds": 2, "poll_interval_seconds": 0.01, "mac_messages_fallback": False,
    })
    calls = []
    def fake_post(action, **payload):
        calls.append((action, payload))
        if action == "request":
            return {"request_id": "r1"}
        if action == "claim":
            return {"status": "received", "code": "482731"}
        return {"status": "cancelled"}
    bridge._post = fake_post
    assert bridge.wait_for_code("example.com") == {"code": "482731", "source": "iphone_relay"}
    assert calls[0][0] == "request"


def test_wait_for_code_can_use_mac_messages_fallback():
    bridge = OtpBridge(
        config={
            "enabled": True, "endpoint": "https://example.invalid", "token": "x",
            "timeout_seconds": 2, "poll_interval_seconds": 0.01, "mac_messages_fallback": True,
            "site_rules": {"example.com": {"sender_hint": "1069", "body_keyword": "Example"}},
        },
        messages_finder=lambda **kwargs: {"code": "667788", "sender": "1069"},
    )
    bridge._post = lambda action, **payload: (
        {"request_id": "r2"} if action == "request"
        else {"status": "waiting"} if action == "claim"
        else {"status": "cancelled"}
    )
    assert bridge.wait_for_code("example.com") == {"code": "667788", "source": "mac_messages"}


def test_local_messages_works_without_relay_and_binds_attempt():
    seen = []
    bridge = OtpBridge(
        config={"mac_messages_enabled": True, "poll_interval_seconds": 0.01,
                "site_rules": {"example.com": {"sender_hint": "1069", "body_keyword": "Example"}}},
        messages_finder=lambda **kwargs: seen.append(kwargs) or {"code": "667788"},
    )
    assert bridge.enabled and not bridge.relay_enabled
    result = bridge.wait_for_code("example.com", timeout_seconds=1,
                                  attempt_id="a" * 32, requested_at=123.0)
    assert result == {"code": "667788", "source": "mac_messages",
                      "attempt_id": "a" * 32, "origin": "example.com"}
    assert seen[0]["not_before"] == 123.0
    assert seen[0]["sender_hint"] == "1069"
    assert seen[0]["body_keyword"] == "Example"


def test_broken_relay_does_not_block_explicit_local_source():
    bridge = OtpBridge(
        config={"enabled": True, "endpoint": "https://example.invalid", "token": "x",
                "mac_messages_enabled": True, "poll_interval_seconds": 0.01,
                "site_rules": {"example.com": {"sender_hint": "1069", "body_keyword": "Example"}}},
        messages_finder=lambda **kwargs: {"code": "667788"},
    )
    bridge._post = lambda *_args, **_kwargs: (_ for _ in ()).throw(OtpBridgeError("offline"))
    result = bridge.wait_for_code("example.com", timeout_seconds=1,
                                  attempt_id="b" * 32, requested_at=123.0)
    assert result["source"] == "mac_messages"
    assert result["attempt_id"] == "b" * 32


def test_relay_code_without_matching_attempt_is_rejected():
    bridge = OtpBridge(config={
        "enabled": True, "endpoint": "https://example.invalid", "token": "x",
        "mac_messages_fallback": False, "timeout_seconds": 1,
    })
    bridge._post = lambda action, **payload: (
        {"request_id": "r3"} if action == "request"
        else {"status": "received", "code": "667788", "attempt_id": "old"}
        if action == "claim" else {"status": "cancelled"}
    )
    assert bridge.wait_for_code("example.com", attempt_id="c" * 32) is None


def test_relay_code_without_matching_origin_is_rejected():
    bridge = OtpBridge(config={
        "enabled": True, "endpoint": "https://example.invalid", "token": "x",
        "mac_messages_fallback": False, "timeout_seconds": 1,
    })
    bridge._post = lambda action, **payload: (
        {"request_id": "r4"} if action == "request"
        else {"status": "received", "code": "667788", "attempt_id": "c" * 32,
              "origin": "other.example.com"}
        if action == "claim" else {"status": "cancelled"}
    )
    assert bridge.wait_for_code("example.com", attempt_id="c" * 32) is None


def test_relay_configuration_does_not_implicitly_enable_mac_messages():
    reads = []
    bridge = OtpBridge(
        config={"enabled": True, "endpoint": "https://example.invalid", "token": "x",
                "timeout_seconds": 1},
        messages_finder=lambda **kwargs: reads.append(kwargs) or {"code": "667788"},
    )
    assert bridge.relay_enabled and not bridge.local_messages_enabled
    bridge._post = lambda action, **payload: (
        {"request_id": "r5"} if action == "request"
        else {"status": "waiting"} if action == "claim"
        else {"status": "cancelled"}
    )
    assert bridge.wait_for_code("example.com", timeout_seconds=1) is None
    assert reads == []


def test_local_source_never_reads_messages_without_site_sender_rule():
    reads = []
    bridge = OtpBridge(
        config={"mac_messages_enabled": True, "poll_interval_seconds": 0.01,
                "site_rules": {"other.example": {"sender_hint": "none", "body_keyword": "none"}}},
        messages_finder=lambda **kwargs: reads.append(kwargs) or {"code": "667788"},
    )
    assert bridge.wait_for_code("example.com", timeout_seconds=1,
                                attempt_id="d" * 32) is None
    assert reads == []


def test_local_source_requires_sender_and_body_rule_together():
    reads = []
    bridge = OtpBridge(
        config={"mac_messages_enabled": True, "poll_interval_seconds": 0.01,
                "site_rules": {"example.com": {"sender_hint": "1069"}}},
        messages_finder=lambda **kwargs: reads.append(kwargs) or {"code": "667788"},
    )
    assert bridge.wait_for_code("example.com", timeout_seconds=1,
                                attempt_id="e" * 32) is None
    assert reads == []
