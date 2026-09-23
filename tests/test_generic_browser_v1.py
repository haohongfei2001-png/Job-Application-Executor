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
        ('<div role="dialog"><h2>登录</h2><p>请扫码登录</p></div>', "other"),
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


def _sms_login_page(tmp_path, body, name="sms-login.html"):
    html = tmp_path / name
    html.write_text(f"<!doctype html><meta charset=\"utf-8\"><body>{body}</body>", encoding="utf-8")
    return html


def test_sms_login_is_preferred_over_qr_alternative_and_requests_code_once(tmp_path):
    html = _sms_login_page(tmp_path, '''
      <div role="dialog" id="login-dialog">
        <h2>注册登录</h2>
        <label>手机号 <input id="phone" type="tel" placeholder="请输入手机号"></label>
        <label>验证码 <input id="otp" autocomplete="one-time-code"></label>
        <button id="send" type="button"
          onclick="this.dataset.clicked='yes'; this.textContent='重新发送'">发送验证码</button>
        <div>扫码登录</div>
      </div>
    ''')

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.auth_challenge_kind() == "one_time_code"
        assert adapter.prepare_one_time_code_auth("13800138000") == "requested"
        assert adapter.page.locator("#phone").input_value() == "13800138000"
        assert adapter.page.locator("#send").get_attribute("data-clicked") == "yes"
        assert adapter.page.locator("#send").inner_text() == "重新发送"
        assert adapter.auth_challenge_kind() == "one_time_code"


def test_sms_login_requires_policy_before_checking_auth_terms(tmp_path):
    html = _sms_login_page(tmp_path, '''
      <div role="dialog" id="login-dialog">
        <h2>注册登录</h2>
        <label>手机号 <input id="phone" type="tel"></label>
        <label>验证码 <input id="otp"></label>
        <label><input id="terms" type="checkbox">同意《注册协议》和《隐私政策》</label>
        <button id="send" type="button" onclick="this.dataset.clicked='yes'">发送验证码</button>
      </div>
    ''', name="sms-terms.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.prepare_one_time_code_auth(
            "13800138000",
            allow_standard_auth_terms=False,
        ) == "consent_required"
        assert adapter.page.locator("#terms").is_checked() is False
        assert adapter.page.locator("#phone").input_value() == ""
        assert adapter.page.locator("#send").get_attribute("data-clicked") is None


def test_sms_login_checks_only_auth_terms_when_policy_is_confirmed(tmp_path):
    html = _sms_login_page(tmp_path, '''
      <div role="dialog" id="login-dialog">
        <h2>注册登录</h2>
        <label>手机号 <input id="phone" type="tel"></label>
        <label>验证码 <input id="otp"></label>
        <label><input id="terms" type="checkbox">同意《注册协议》和《隐私政策》</label>
        <label><input id="marketing" type="checkbox">订阅产品营销消息</label>
        <button id="send" type="button" onclick="this.dataset.clicked='yes'">发送验证码</button>
      </div>
    ''', name="sms-policy.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.prepare_one_time_code_auth(
            "13800138000",
            allow_standard_auth_terms=True,
        ) == "requested"
        assert adapter.page.locator("#terms").is_checked() is True
        assert adapter.page.locator("#marketing").is_checked() is False
        assert adapter.page.locator("#send").get_attribute("data-clicked") == "yes"


def test_sms_login_never_auto_resends_code(tmp_path):
    html = _sms_login_page(tmp_path, '''
      <div role="dialog" id="login-dialog">
        <h2>登录</h2>
        <label>手机号 <input id="phone" type="tel" value="13800138000"></label>
        <label>验证码 <input id="otp"></label>
        <button id="resend" type="button" onclick="this.dataset.clicked='yes'">重新发送</button>
      </div>
    ''', name="sms-resend.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.prepare_one_time_code_auth("13800138000") == "already_requested"
        assert adapter.page.locator("#resend").get_attribute("data-clicked") is None


def test_sms_login_rejects_ambiguous_phone_controls(tmp_path):
    html = _sms_login_page(tmp_path, '''
      <div role="dialog" id="login-dialog">
        <h2>登录</h2>
        <label>手机号 <input id="phone1" type="tel"></label>
        <label>备用手机号 <input id="phone2" name="mobile"></label>
        <label>验证码 <input id="otp"></label>
        <button id="send" type="button" onclick="this.dataset.clicked='yes'">发送验证码</button>
      </div>
    ''', name="sms-ambiguous-phone.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.prepare_one_time_code_auth("13800138000") == "ambiguous"
        assert adapter.page.locator("#send").get_attribute("data-clicked") is None


def test_sms_login_can_confirm_register_login_only_after_authorized_terms(tmp_path):
    html = _sms_login_page(tmp_path, '''
      <div role="dialog" id="login-dialog">
        <h2>注册登录</h2>
        <label>手机号 <input id="phone" type="tel"></label>
        <label>验证码 <input id="otp"></label>
        <label><input id="terms" type="checkbox">同意《注册协议》和《隐私政策》</label>
        <button id="send" type="button">发送验证码</button>
        <button id="login" type="button"
          onclick="document.querySelector('#login-dialog').remove()">注册/登录</button>
      </div>
    ''', name="sms-register-login.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.prepare_one_time_code_auth(
            "13800138000",
            allow_standard_auth_terms=True,
        ) == "requested"
        assert adapter.enter_one_time_code("462810") is True
        assert adapter.confirm_one_time_code_auth() is True
        assert adapter.page.locator("#login-dialog").count() == 0


def test_sms_login_reproves_send_control_after_consent_rerender(tmp_path):
    html = _sms_login_page(tmp_path, '''
      <div role="dialog" id="login-dialog">
        <h2>注册登录</h2>
        <label>手机号 <input id="phone" type="tel"></label>
        <label>验证码 <input id="otp"></label>
        <label><input id="terms" type="checkbox"
          onchange="document.querySelector('#send').id='send2'">
          同意《注册协议》和《隐私政策》
        </label>
        <button id="send" type="button" onclick="this.dataset.clicked='yes'">发送验证码</button>
      </div>
    ''', name="sms-rerender.html")

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.prepare_one_time_code_auth(
            "13800138000",
            allow_standard_auth_terms=True,
        ) == "requested"
        assert adapter.page.locator("#send").count() == 0
        assert adapter.page.locator("#send2").get_attribute("data-clicked") == "yes"


def test_page_level_qr_copy_is_not_an_auth_challenge(tmp_path):
    html = tmp_path / "qr-marketing-copy.html"
    html.write_text(
        '''<!doctype html><meta charset="utf-8"><body>
        <header>OPPO 校园招聘</header>
        <main><h1>开启新征程</h1><p>扫码关注 OPPO 招聘二维码，获取更多校园资讯。</p></main>
        </body>''',
        encoding="utf-8",
    )

    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.auth_challenge_kind() is None
