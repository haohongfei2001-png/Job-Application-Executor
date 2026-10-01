"""Synthetic local controls and fake browser semantics; not native evidence."""
import http.client
import time
from urllib.parse import urlsplit
from types import SimpleNamespace
import pytest
from executor.preparation.native_selfcheck import check_owned_native_browser, NativeSelfCheckFailed
from executor.preparation.session import DisposablePreparationSession
from executor.preparation import native_admission as admission

class FakeIdentity:
    def __init__(self):self.value='a'*64
    def verify(self,_):return self.value

class FakeProxy:
    def __init__(self):self.denied_connections=0;self.valid=True
    def verify_launch(self,_):return self.valid


def owner(fault=None):
    value=DisposablePreparationSession(headless=False,channel='chrome')
    value.authorized=True
    value.identity=FakeIdentity();value.proxy=FakeProxy();value.transport=object();value.context=object()
    class Browser:
        version='152.0.7977.83'
        def __init__(self):self.contexts=[value.context];self.probes=[]
        def new_context(self,**kwargs):
            assert kwargs=={'service_workers':'block','accept_downloads':False,'ignore_https_errors':False}
            probe=Context(self);self.probes.append(probe);self.contexts.append(probe);return probe
    class Context:
        def __init__(self,browser):self.browser=browser;self.closed=False;self.routes=[];self.events={}
        def new_cdp_session(self,page):
            return SimpleNamespace(send=lambda *_:None,on=lambda name,fn:self.events.update({name:fn}),detach=lambda:None)
        def new_page(self):return Page(self)
        def route(self,url,handler):self.routes.append((url,handler))
        def unroute(self,url,handler):self.routes.remove((url,handler))
        def close(self):
            self.closed=True
            if fault!='cleanup':self.browser.contexts.remove(self)
    class Page:
        def __init__(self,context):self.context=context;self.events={}
        def on(self,name,fn):self.events[name]=fn
        def wait_for_timeout(self,ms):
            pending=getattr(self,'pending_native',None)
            if pending:self.pending_native=None;pending()
            time.sleep(ms/1000)
        def set_default_timeout(self,ms):assert ms==3000
        def goto(self,url,timeout):assert timeout==3000 and '/bootstrap/' in url
        def evaluate(self,script,data):
            assert self.context.routes==[]
            assert data['mode'] in {'fetch','fetch_tls','websocket','websocket_tls'}
            def emit_native():
                target=data['target']+('/other' if fault=='wrong_native_url' else '')
                if data['mode'].startswith('fetch'):
                    self.events['response'](SimpleNamespace(url=target,status=403))
                else:
                    url=target.replace('http','ws',1)
                    self.context.events['Network.webSocketCreated']({'requestId':'id','url':url})
                    if fault=='native_tunnel_text':
                        self.context.events['Network.webSocketFrameError']({'requestId':'id','errorMessage':'Establishing a tunnel via proxy server failed.'})
                    else:self.context.events['Network.webSocketHandshakeResponseReceived']({'requestId':'id','response':{'status':403}})
            if fault=='late_native':self.pending_native=emit_native
            elif fault!='no_native_evidence':emit_native()
            if fault=='escape':
                target=urlsplit(data['target']);connection=http.client.HTTPConnection(target.hostname,target.port,timeout=2)
                try:connection.request('POST',target.path,body='SYNTHETIC');response=connection.getresponse();response.read()
                finally:connection.close()
            if fault!='no_proxy_observation':value.proxy.denied_connections+=1
            if fault=='identity':value.identity.value='b'*64
            if fault=='proxy':value.proxy.valid=False
            if fault=='cancel':value.authorized=False
            if fault=='timeout':return {'kind':'timeout'}
            if fault=='wrong_response':return {'kind':'response','status':200}
            return {'kind':'blocked'}
    value.browser=Browser()
    return value


def test_selfcheck_observes_local_positive_and_native_negative_then_closes_only_probe():
    value=owner();original=value.context
    receipt=check_owned_native_browser(value)
    assert receipt['process_sha']=='a'*64 and receipt['native_proxy_blocked'] is True
    assert receipt['applicant_data_used'] is False and receipt['external_recipient_used'] is False
    assert value.browser.contexts==[original] and value.browser.probes[0].closed
    assert value.proxy.denied_connections==4

@pytest.mark.parametrize('fault',['escape','no_proxy_observation','no_native_evidence','wrong_native_url','cancel','timeout','wrong_response','identity','proxy','cleanup'])
def test_uncertain_or_escaping_probe_cannot_admit_and_always_attempts_cleanup(fault):
    value=owner(fault)
    with pytest.raises(NativeSelfCheckFailed):check_owned_native_browser(value,still_authorized=lambda:value.authorized)
    assert value.browser.probes[0].closed
    assert value.context in value.browser.contexts


def test_other_preexisting_context_is_not_adopted_or_closed():
    value=owner();other=object();value.browser.contexts.append(other)
    with pytest.raises(NativeSelfCheckFailed):check_owned_native_browser(value)
    assert value.browser.contexts==[value.context,other] and value.browser.probes==[]


def test_admission_is_per_owned_process_not_a_reusable_browser_version(monkeypatch):
    monkeypatch.setattr(admission.sys,'platform','darwin')
    monkeypatch.setattr(admission.platform,'machine',lambda:'arm64')
    value=owner();other=owner();gate=admission.NativePreparationAdmission();calls=[]
    def check(current,**_):
        calls.append(current)
        return {'process_sha':'a'*64,'browser_version':current.browser.version}
    monkeypatch.setattr(admission,'check_owned_native_browser',check)
    assert gate.admit(value) is True and gate.admit(value) is True
    assert calls==[value] and gate.admit(other) is False
    value.identity.value='b'*64
    assert gate.admit(value) is False and calls==[value]


def test_failed_selfcheck_never_creates_admission(monkeypatch):
    monkeypatch.setattr(admission.sys,'platform','darwin')
    monkeypatch.setattr(admission.platform,'machine',lambda:'arm64')
    def unavailable(_,**kwargs):raise NativeSelfCheckFailed()
    monkeypatch.setattr(admission,'check_owned_native_browser',unavailable)
    gate=admission.NativePreparationAdmission()
    assert gate.admit(owner()) is False and gate._owner is None and gate._receipt is None


def test_native_event_delivered_after_evaluate_is_pumped_before_admission():
    value=owner('late_native')
    result=check_owned_native_browser(value)
    assert result['native_proxy_blocked'] is True and value.proxy.denied_connections==4


def test_slow_native_setup_does_not_spend_the_network_probe_budget():
    value=owner();now=[0.0];original=value.browser.new_context
    def setup(**kwargs):
        now[0]+=20
        return original(**kwargs)
    value.browser.new_context=setup
    result=check_owned_native_browser(value,clock=lambda:now[0])
    assert result['native_proxy_blocked'] is True


def test_network_probe_budget_still_refuses_even_with_valid_native_proof():
    value=owner();now=[0.0];original=value.browser.new_context
    def setup(**kwargs):
        context=original(**kwargs);new_page=context.new_page
        def page_factory():
            page=new_page();evaluate=page.evaluate
            def slow(*args):now[0]+=13;return evaluate(*args)
            page.evaluate=slow;return page
        context.new_page=page_factory;return context
    value.browser.new_context=setup
    with pytest.raises(NativeSelfCheckFailed) as caught:
        check_owned_native_browser(value,clock=lambda:now[0])
    assert caught.value.diagnostic['success_budget_exceeded'] is True
    assert caught.value.diagnostic['scoped_proof'] is True


def test_whole_selfcheck_cannot_succeed_after_ninety_seconds_of_setup():
    value=owner();now=[0.0];original=value.browser.new_context
    def setup(**kwargs):
        now[0]+=91
        return original(**kwargs)
    value.browser.new_context=setup
    with pytest.raises(NativeSelfCheckFailed):check_owned_native_browser(value,clock=lambda:now[0])
    assert value.browser.probes[0].closed and value.proxy.denied_connections==0


@pytest.mark.parametrize('phase',['constructor','start'])
def test_receiver_thread_start_failure_closes_listener_without_admission(monkeypatch,phase):
    from executor.preparation import native_selfcheck as module
    servers=[];base=module.ThreadingHTTPServer
    class TrackingServer(base):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);servers.append(self)
    def unavailable():raise RuntimeError('synthetic thread allocation failed')
    monkeypatch.setattr(module,'ThreadingHTTPServer',TrackingServer)
    def thread_factory(**_):
        if phase=='constructor':unavailable()
        return SimpleNamespace(start=unavailable)
    monkeypatch.setattr(module.threading,'Thread',thread_factory)
    value=owner()
    with pytest.raises(RuntimeError,match='synthetic thread allocation'):check_owned_native_browser(value)
    assert len(servers)==1 and servers[0].socket.fileno()==-1 and value.browser.probes==[]


def test_exact_chromium_proxy_tunnel_text_is_a_scoped_native_error():
    value=owner('native_tunnel_text')
    receipt=check_owned_native_browser(value)
    assert receipt['protocols']==['http','https','ws','wss']
    assert receipt['native_proxy_blocked'] is True and value.proxy.denied_connections==4
    for mode in ('websocket','websocket_tls'):
        assert receipt['proof_by_protocol'][mode]['proof']==['native_proxy_failure']
        assert 'net::ERR_TUNNEL_CONNECTION_FAILED' in receipt['proof_by_protocol'][mode]['native_observations']


@pytest.mark.parametrize('message',[
    'net::ERR_CONNECTION_RESET', 'Error in connection establishment: net::ERR_CONNECTION_CLOSED',
    'WebSocket opening handshake was canceled', 'WebSocket opening handshake timed out',
    'Establishing a tunnel via proxy server failed',
    'Untrusted prefix Establishing a tunnel via proxy server failed.',
    'Establishing a tunnel via proxy server failed. Untrusted suffix',
])
def test_generic_and_near_match_websocket_errors_do_not_establish_proxy_proof(message):
    from executor.preparation.native_selfcheck import native_proxy_error_code
    assert native_proxy_error_code(message) is None
