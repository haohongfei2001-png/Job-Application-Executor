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
