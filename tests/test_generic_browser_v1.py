import json

import pytest

from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.models import ApplicationStage, ResolutionStatus


def test_generic_browser_fills_known_fields_and_stops_before_submit(tmp_path, monkeypatch):
    html = tmp_path / "application.html"
    html.write_text('''<!doctype html><meta charset="utf-8"><body>
      <label>姓名 <input id="name" name="full_name" required value="Stale Wrong Name"></label>
      <label>邮箱 <input id="email" name="email" type="email" required></label>
      <label>简历 <input id="resume" type="file" required></label>
      <button id="submit">Submit application</button>
    </body>''', encoding="utf-8")
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n%fixture\n")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "fields": {
            "identity.full_name": {"value": "Example User", "confidence": 1.0},
            "identity.email": {"value": "example@example.test", "confidence": 1.0},
        },
        "assets": {
            "resume": {"path": str(resume), "kind": "resume_pdf"}
        }
    }), encoding="utf-8")
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    runner = ApplicationExecutor(
        html.as_uri(),
        profile,
        {"deepseek": {"enabled": False}},
    )
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    resolved = {
        item.canonical_key
        for item in plan.fields
        if item.status == ResolutionStatus.RESOLVED
    }
    assert "identity.full_name" in resolved
    assert "identity.email" in resolved
    assert "assets.resume" in resolved
    assert not [x for x in plan.unresolved_fields if x.required]


@pytest.mark.parametrize(
    "input_html",
    [
        '<label>Security token <input id="token" autocomplete="one-time-code"></label>',
        '<label>验证码 <input id="sms-token"></label>',
    ],
)
def test_generic_adapter_enters_unambiguous_one_time_code(tmp_path, input_html):
    html = tmp_path / "otp.html"
    html.write_text(f"<!doctype html><body>{input_html}</body>", encoding="utf-8")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.auth_challenge_kind() == "one_time_code"
        assert adapter.otp_field_status() == "unique"
        assert adapter.enter_one_time_code("462810") is True
        assert adapter.page.locator("input").input_value() == "462810"


def test_generic_adapter_rejects_ambiguous_one_time_code_fields(tmp_path):
    html = tmp_path / "ambiguous-otp.html"
    html.write_text(
        '''<!doctype html><body>
        <label>验证码 <input id="sms-code"></label>
        <label>Verification code <input id="email-code"></label>
        </body>''',
        encoding="utf-8",
    )

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.auth_challenge_kind() == "one_time_code"
        assert adapter.otp_field_status() == "ambiguous"
        assert adapter.enter_one_time_code("462810") is False
        assert adapter.page.locator("#sms-code").input_value() == ""
        assert adapter.page.locator("#email-code").input_value() == ""


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('<label>Password <input type="password"></label>', "password"),
        ('<div class="captcha">Verify you are human</div>', "captcha"),
        ('<p>请扫码登录</p>', "other"),
    ],
)
def test_generic_adapter_distinguishes_non_otp_auth_challenges(tmp_path, body, expected):
    html = tmp_path / f"{expected}.html"
    html.write_text(f"<!doctype html><body>{body}</body>", encoding="utf-8")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.auth_challenge_kind() == expected
