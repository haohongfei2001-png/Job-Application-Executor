"""Deny-only proxy has no data-reading or forwarding path."""
import socket
from urllib.parse import urlsplit

import pytest

from executor.preparation.network_fence import DenyOnlyProxy


@pytest.mark.parametrize('wire', [
    b'CONNECT www.example.test:443 HTTP/1.1\r\n\r\n',
    b'POST http://example.test/submit HTTP/1.1\r\nContent-Length: 16\r\n\r\nPRIVATE_SYNTHETIC',
    b'GET http://127.0.0.1/submit HTTP/1.1\r\n\r\n', b''])
def test_every_connection_is_denied_without_request_parsing(wire):
    with DenyOnlyProxy() as proxy:
        options=proxy.browser_options();address=urlsplit(options['proxy']['server'])
        with socket.create_connection((address.hostname,address.port),timeout=2) as connection:
            if wire:connection.sendall(wire)
            assert connection.recv(1024).startswith(b'HTTP/1.1 403 Forbidden\r\n')
        assert '--proxy-bypass-list=<-loopback>' in options['args']
        assert '--force-webrtc-ip-handling-policy=disable_non_proxied_udp' in options['args']
        with pytest.raises(RuntimeError):proxy.__enter__()
    with pytest.raises(RuntimeError):proxy.browser_options()
    with pytest.raises(OSError):socket.create_connection((address.hostname,address.port),timeout=.1)


@pytest.mark.parametrize('backend',['cdp','cdp_fresh','launch','persistent','existing',''])
def test_unsafe_backend_refused_before_browser_or_field_capability(backend,monkeypatch):
    from executor.preparation import session
    def forbidden(*_,**__):pytest.fail('browser launched for unsupported backend')
    monkeypatch.setattr(session,'sync_playwright',forbidden)
    with pytest.raises(ValueError):session.DisposablePreparationSession(backend=backend)


def test_public_broker_only_accepts_finite_resources_and_never_browser_request_body():
    from types import SimpleNamespace
    from executor.preparation.session import DisposablePreparationSession
    from executor.preparation.qiyunfang import CONTRACT_URL
    calls=[];session=DisposablePreparationSession()
    session.client=SimpleNamespace(get=lambda *args,**kwargs:calls.append((args,kwargs)))
    for url in ('https://www.qiyunfang.com/ajax/siteForm_h.jsp','http://127.0.0.1/','https://other.example/'):
        with pytest.raises(ValueError):session._public_get(url)
    session._public_get(CONTRACT_URL)
    assert calls==[((CONTRACT_URL,),{'max_redirects':0,'max_retries':0,'timeout':15000})]


@pytest.mark.parametrize('key',['DEBUG','DEBUG_FILE','PWDEBUG','SSLKEYLOGFILE','CHROME_LOG_FILE','NODE_OPTIONS','PLAYWRIGHT_TRACE_DIR'])
def test_recording_environment_is_refused_before_driver_or_browser(monkeypatch,key):
    from executor.preparation import session
    monkeypatch.setenv(key,'SYNTHETIC_DO_NOT_LOG')
    def forbidden(*_,**__):pytest.fail('private browser launched in recording environment')
    monkeypatch.setattr(session,'sync_playwright',forbidden)
    with pytest.raises(RuntimeError,match='preparation_session_unavailable'):
        session.DisposablePreparationSession().__enter__()


def test_environment_privacy_check_never_modifies_proxy_or_trust_settings():
    from executor.preparation.session import require_private_transport_environment
    env={'HTTPS_PROXY':'https://proxy.example.test','SSL_CERT_FILE':'/synthetic/managed-ca.pem'}
    before=dict(env);require_private_transport_environment(env);assert env==before


@pytest.mark.parametrize('extra',[None,'--no-sandbox','--disable-web-security','--ignore-certificate-errors','--enable-logging=stderr','--proxy-server=direct://','--proxy-bypass-list=*'])
def test_native_launch_receipt_rejects_bypass_security_or_recording_flags(extra):
    from types import SimpleNamespace
    with DenyOnlyProxy() as proxy:
        options=proxy.browser_options()
        assert options['chromium_sandbox'] is True
        args=['chrome','--remote-debugging-pipe',*options['args'],'--proxy-server='+options['proxy']['server']]
        if extra:args.append(extra)
        cdp=SimpleNamespace(send=lambda _: {'arguments':args},detach=lambda:None)
        browser=SimpleNamespace(new_browser_cdp_session=lambda:cdp)
        if extra:
            with pytest.raises(RuntimeError):proxy.verify_launch(browser)
        else:assert proxy.verify_launch(browser) is True


def test_native_command_line_read_requires_explicit_automation_disclosure():
    from types import SimpleNamespace
    with DenyOnlyProxy() as proxy:
        options=proxy.browser_options();assert '--enable-automation' in options['args']
        args=['chrome','--remote-debugging-pipe',*options['args'],'--proxy-server='+options['proxy']['server']]
        args.remove('--enable-automation')
        browser=SimpleNamespace(new_browser_cdp_session=lambda:SimpleNamespace(send=lambda _:{'arguments':args},detach=lambda:None))
        with pytest.raises(RuntimeError,match='recipe_changed'):proxy.verify_launch(browser)


@pytest.mark.parametrize('fault',[None,'browser','client','driver'])
def test_owned_cleanup_attempts_each_phase_once_and_requires_exact_absence(fault):
    from types import SimpleNamespace
    from executor.preparation.session import DisposablePreparationSession
    owner=DisposablePreparationSession();events=[]
    def phase(name):
        events.append(name)
        if name==fault:raise OSError('synthetic acknowledgement loss')
    owner._driver_start_attempted=True
    owner.browser=SimpleNamespace(close=lambda:phase('browser'))
    owner.client=SimpleNamespace(dispose=lambda:phase('client'))
    owner.pw=SimpleNamespace(stop=lambda:phase('driver'))
    owner.identity=SimpleNamespace(process_sha='a'*64,
        absence=lambda:(events.append('absence') or {'status':'ABSENT','process_sha':'a'*64}))
    owner.proxy=SimpleNamespace(__exit__=lambda *_:phase('proxy'))
    assert owner.close() is True
    assert events==['browser','client','driver','absence','proxy']
    assert owner.close() is True and events==['browser','client','driver','absence','proxy']
    with pytest.raises(RuntimeError,match='already_started'):owner.__enter__()


@pytest.mark.parametrize('evidence',[{'status':'UNKNOWN'},{'status':'PRESENT','process_sha':'a'*64},
                                    {'status':'ABSENT','process_sha':'b'*64},{'status':'ABSENT'}])
def test_uncertain_cleanup_keeps_proxy_and_never_retries_native_close(evidence):
    from types import SimpleNamespace
    from executor.preparation.session import DisposablePreparationSession
    owner=DisposablePreparationSession();events=[]
    owner._driver_start_attempted=True
    owner.browser=SimpleNamespace(close=lambda:events.append('browser'))
    owner.client=SimpleNamespace(dispose=lambda:events.append('client'))
    owner.pw=SimpleNamespace(stop=lambda:events.append('driver'))
    owner.identity=SimpleNamespace(process_sha='a'*64,absence=lambda:evidence)
    proxy=owner.proxy=SimpleNamespace(__exit__=lambda *_:pytest.fail('proxy removed without absence'))
    assert owner.close() is False and owner.proxy is proxy
    assert owner.close() is False and events==['browser','client','driver']


def test_missing_startup_identity_never_certifies_a_started_runtime_closed():
    from types import SimpleNamespace
    from executor.preparation.session import DisposablePreparationSession
    owner=DisposablePreparationSession();owner._driver_start_attempted=True
    owner.proxy=SimpleNamespace(__exit__=lambda *_:pytest.fail('unidentified runtime fence removed'))
    assert owner.close() is False


def test_cleanup_before_driver_start_has_no_process_to_reconcile():
    from types import SimpleNamespace
    from executor.preparation.session import DisposablePreparationSession
    owner=DisposablePreparationSession();events=[]
    owner.proxy=SimpleNamespace(__exit__=lambda *_:events.append('proxy'))
    assert owner.close() is True and events==['proxy']


@pytest.mark.parametrize('failure',['absence','digest','proxy'])
def test_cleanup_observation_or_proxy_ack_loss_stays_unknown_without_retry(failure):
    from types import SimpleNamespace
    from executor.preparation.session import DisposablePreparationSession
    owner=DisposablePreparationSession();owner._driver_start_attempted=True;events=[]
    owner.browser=SimpleNamespace(close=lambda:events.append('browser'))
    owner.client=SimpleNamespace(dispose=lambda:events.append('client'))
    owner.pw=SimpleNamespace(stop=lambda:events.append('driver'))
    class Identity:
        def absence(self):
            events.append('absence')
            if failure=='absence':raise OSError('synthetic observation uncertainty')
            return {'status':'ABSENT','process_sha':'a'*64}
        @property
        def process_sha(self):
            if failure=='digest':raise ValueError('synthetic identity uncertainty')
            return 'a'*64
    owner.identity=Identity()
    def proxy_close(*_):events.append('proxy');raise OSError('synthetic proxy acknowledgement loss')
    proxy=owner.proxy=SimpleNamespace(__exit__=proxy_close)
    assert owner.close() is False
    before=list(events)
    assert owner.close() is False and events==before and owner.proxy is proxy
    assert events[:3]==['browser','client','driver'] and ('proxy' in events)==(failure=='proxy')
    with pytest.raises(RuntimeError):owner.__enter__()
