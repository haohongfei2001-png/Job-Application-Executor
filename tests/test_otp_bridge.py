from executor.otp.bridge import OtpBridge


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
        },
        messages_finder=lambda **kwargs: {"code": "667788", "sender": "1069"},
    )
    bridge._post = lambda action, **payload: (
        {"request_id": "r2"} if action == "request"
        else {"status": "waiting"} if action == "claim"
        else {"status": "cancelled"}
    )
    assert bridge.wait_for_code("example.com") == {"code": "667788", "source": "mac_messages"}
