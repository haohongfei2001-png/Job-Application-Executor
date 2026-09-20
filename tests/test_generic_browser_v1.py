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


def _otp_confirmation_page(tmp_path, body, name="otp-confirm.html"):
    html = tmp_path / name
    html.write_text(f"<!doctype html><body>{body}</body>", encoding="utf-8")
    return html


def test_generic_adapter_clicks_unique_verify_in_same_otp_form(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="auth">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="verify" type="button"
          onclick="document.querySelector('#auth').remove()">Verify</button>
      </form>
    ''')

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is True
        assert adapter.page.locator("#auth").count() == 0
        assert adapter.auth_challenge_kind() is None


def test_generic_adapter_ignores_candidate_outside_otp_form(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="auth"><label>验证码 <input id="otp" value="462810"></label></form>
      <button id="outside" type="button" onclick="this.dataset.clicked='yes'">Verify</button>
    ''')

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is False
        assert adapter.page.locator("#outside").get_attribute("data-clicked") is None


def test_generic_adapter_rejects_two_otp_auth_confirmation_candidates(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="auth">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="verify" type="button" onclick="this.dataset.clicked='yes'">Verify</button>
        <button id="login" type="button" onclick="this.dataset.clicked='yes'">Log in</button>
      </form>
    ''')

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is False
        assert adapter.page.locator("[data-clicked]").count() == 0


@pytest.mark.parametrize("text", ["发送验证码", "获取验证码", "重新发送", "重发", "Send code", "Get code", "Resend"])
def test_generic_adapter_ignores_send_or_resend_code_control(tmp_path, text):
    html = _otp_confirmation_page(tmp_path, f'''
      <form id="auth">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="resend" type="button" onclick="this.dataset.clicked='yes'">{text}</button>
      </form>
    ''')

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is False
        assert adapter.page.locator("#resend").get_attribute("data-clicked") is None


@pytest.mark.parametrize("text", ["Submit application", "提交申请", "确认投递", "立即投递", "正式投递"])
def test_generic_adapter_never_clicks_final_submit_like_control_in_otp_form(tmp_path, text):
    html = _otp_confirmation_page(tmp_path, f'''
      <form id="auth">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="final" type="button" onclick="this.dataset.clicked='yes'">{text}</button>
      </form>
    ''', name="otp-final.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is False
        assert adapter.page.locator("#final").get_attribute("data-clicked") is None


def test_generic_adapter_rejects_bare_confirm_in_application_context(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="application">
        <label>姓名 <input id="name" value="Example User"></label>
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="final" type="button" onclick="this.dataset.clicked='yes'">确认</button>
      </form>
    ''', name="otp-bare-confirm.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is False
        assert adapter.page.locator("#final").get_attribute("data-clicked") is None


def test_generic_adapter_rejects_bare_confirm_without_explicit_auth_context(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="plain">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="confirm" type="button" onclick="this.dataset.clicked='yes'">确认</button>
      </form>
    ''', name="otp-plain-confirm.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is False
        assert adapter.page.locator("#confirm").get_attribute("data-clicked") is None


def test_generic_adapter_clicks_strong_login_without_extra_auth_context(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="plain">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="login" type="button"
          onclick="document.querySelector('#plain').remove()">Log in</button>
      </form>
    ''', name="otp-strong-login.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is True
        assert adapter.page.locator("#plain").count() == 0


def test_generic_adapter_allows_contextual_confirm_in_explicit_login_form(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="login">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="confirm" type="button"
          onclick="document.querySelector('#login').remove()">Confirm</button>
      </form>
    ''', name="otp-login-confirm.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is True
        assert adapter.page.locator("#login").count() == 0


def test_generic_adapter_rejects_otp_only_confirm_in_application_form(tmp_path):
    html = _otp_confirmation_page(tmp_path, '''
      <form id="application">
        <label>验证码 <input id="otp" value="462810"></label>
        <button id="confirm" type="button" onclick="this.dataset.clicked='yes'">确认</button>
      </form>
    ''', name="otp-application-confirm.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.confirm_one_time_code_auth() is False
        assert adapter.page.locator("#confirm").get_attribute("data-clicked") is None
