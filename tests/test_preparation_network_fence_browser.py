"""Synthetic independent recipient checks with browser routing deliberately absent."""
import contextlib
import urllib.request
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from executor.preparation.network_fence import DenyOnlyProxy
from test_qiyunfang_preparation_browser import server, bounded_browser_oracle


@pytest.fixture(params=['bundled_headless','installed_chrome_headful'])
def launch_mode(request):
    if request.param=='installed_chrome_headful':
        if sys.platform!='darwin' or not Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').is_file():
            pytest.skip('installed headful Mac Chrome unavailable; not verified')
        return {'headless':False,'channel':'chrome'}
    return {'headless':True}


@pytest.mark.parametrize('proxy_state', ['listening','closed'])
@pytest.mark.parametrize('channel', ['fetch','beacon','image','websocket','popup','worker_fetch','worker_websocket'])
def test_native_browser_cannot_reach_loopback_recipient_even_without_route_guard(server,channel,proxy_state,launch_mode):
    url,received=server
    with urllib.request.urlopen(url+"/health",timeout=2) as response:assert response.status==200
    assert received;received.clear()
    with DenyOnlyProxy() as proxy, sync_playwright() as pw:
        browser=pw.chromium.launch(**launch_mode,**proxy.browser_options())
        try:
            context=browser.new_context(service_workers='block');page=context.new_page()
            context.route(url+'/',lambda route:route.fulfill(status=200,content_type='text/html',body='<input id="ordinary">'))
            page.goto(url+'/');context.unroute(url+'/')
            # No request routing/mocks remain. The browser proxy itself must
            # block these requests, including Chromium's implicit loopback bypass.
            failures=[];context.on('requestfailed',lambda _request:failures.append(True))
            baseline=proxy.denied_connections
            if proxy_state=='closed':proxy.__exit__(None,None,None)
            page.locator('#ordinary').fill('SYNTHETIC_PROXY_CANARY')
            page.evaluate('''({base,channel})=>{
              const target=base+'/submit-native?canary='+ordinary.value;
              if(channel==='fetch')fetch(target,{method:'POST',body:ordinary.value}).catch(()=>{});
              if(channel==='beacon')navigator.sendBeacon(target,ordinary.value);
              if(channel==='image')new Image().src=target;
              if(channel==='websocket')new WebSocket(target.replace('http:','ws:'));
              if(channel==='popup')window.open(target);
              if(channel==='worker_fetch'||channel==='worker_websocket'){
                const body=channel==='worker_fetch'?'fetch('+JSON.stringify(target)+').catch(()=>{})':'new WebSocket('+JSON.stringify(target.replace('http:','ws:'))+')';
                new Worker(URL.createObjectURL(new Blob([body],{type:'application/javascript'})));
              }
            }''',{'base':url,'channel':channel})
            page.wait_for_timeout(1500)
            if proxy_state=='listening':assert proxy.denied_connections>baseline, "browser never attempted proxy connection"
            assert received==[], 'browser bypassed deny-only proxy'
        finally:browser.close()


@pytest.mark.parametrize('host', ['127.0.0.1','localhost','[::1]'])
@pytest.mark.parametrize('scheme', ['http','https','ws','wss'])
def test_native_destination_variants_never_even_connect_to_recipient(host,scheme,launch_mode):
    import socket
    import socketserver
    import threading
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):self.server.arrivals.append(True)
    class IPv6(socketserver.ThreadingTCPServer):address_family=socket.AF_INET6
    factory=IPv6 if host=='[::1]' else socketserver.ThreadingTCPServer
    try:recipient=factory(('::1' if host=='[::1]' else '127.0.0.1',0),Handler)
    except OSError:
        if host=='[::1]':pytest.skip('runner has no IPv6 loopback; not verified')
        raise
    recipient.arrivals=[];thread=threading.Thread(target=recipient.serve_forever,daemon=True);thread.start()
    try:
        # Independent endpoint liveness, without relaxing TLS validation.
        with socket.create_connection((recipient.server_address[0],recipient.server_address[1]),timeout=2):pass
        import time
        deadline=time.monotonic()+2
        while not recipient.arrivals and time.monotonic()<deadline:time.sleep(.01)
        assert recipient.arrivals;recipient.arrivals.clear()
        with DenyOnlyProxy() as proxy,sync_playwright() as pw:
            browser=pw.chromium.launch(**launch_mode,**proxy.browser_options())
            try:
                page=browser.new_page();base=proxy.denied_connections
                destination=f'{scheme}://{host}:{recipient.server_address[1]}/submit-synthetic'
                if scheme in ['http','https']:
                    with contextlib.suppress(Exception):page.goto(destination,timeout=3000)
                else:
                    page.evaluate('url=>{window.probeSocket=new WebSocket(url)}',destination)
                    page.wait_for_timeout(1000)
                assert not recipient.arrivals, 'browser reached recipient outside proxy'
                assert proxy.denied_connections>base, 'request never reached deny proxy'
            finally:browser.close()
    finally:recipient.shutdown();recipient.server_close();thread.join()


def _sink_liveness(service, *, udp):
    import socket,time
    address=('127.0.0.1',service.server_address[1])
    if udp:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:client.sendto(b'synthetic',address)
    else:
        with socket.create_connection(address,timeout=2):pass
    deadline=time.monotonic()+2
    while not service.arrivals and time.monotonic()<deadline:time.sleep(.01)
    assert service.arrivals, 'independent sink is not reachable'
    service.arrivals.clear()


def _secure_fixture(browser):
    context=browser.new_context()
    context.route('https://127.0.0.1/',lambda route:route.fulfill(body='<!doctype html>'))
    page=context.new_page();page.goto('https://127.0.0.1/')
    return page


def _ice_attempt(page,endpoint):
    return page.evaluate('''async url=>{
      const pc=new RTCPeerConnection({iceServers:[{urls:url,username:'synthetic',credential:'synthetic'}]});
      pc.createDataChannel('synthetic');await pc.setLocalDescription(await pc.createOffer());
      await new Promise(resolve=>setTimeout(resolve,2500));
      const state=pc.iceGatheringState;pc.close();return state;
    }''',endpoint)


@pytest.mark.parametrize('ice_transport',['stun_udp','turn_tcp'])
def test_webrtc_candidate_probe_does_not_bypass_disposable_proxy(ice_transport,launch_mode):
    import socketserver,threading
    class Sink(socketserver.BaseRequestHandler):
        def handle(self):self.server.arrivals.append(True)
    udp=ice_transport=='stun_udp'
    cls=socketserver.ThreadingUDPServer if udp else socketserver.ThreadingTCPServer
    service=cls(('127.0.0.1',0),Sink);service.arrivals=[]
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    try:
        _sink_liveness(service,udp=udp)
        endpoint=('stun:' if udp else 'turn:')+f'127.0.0.1:{service.server_address[1]}'
        if not udp:endpoint+='?transport=tcp'
        with sync_playwright() as pw:
            # Prove THIS browser/fixture reaches THIS sink without the fence.
            control=pw.chromium.launch(**launch_mode)
            try:
                assert _ice_attempt(_secure_fixture(control),endpoint) in ['gathering','complete']
                assert service.arrivals, 'unfenced browser never reached ICE sink; oracle invalid'
            finally:control.close()
            service.arrivals.clear()
            with DenyOnlyProxy() as proxy:
                browser=pw.chromium.launch(**launch_mode,**proxy.browser_options())
                try:
                    assert _ice_attempt(_secure_fixture(browser),endpoint) in ['gathering','complete']
                    assert service.arrivals==[], 'WebRTC bypassed disposable proxy'
                finally:browser.close()
    finally:service.shutdown();service.server_close();thread.join()


def _webtransport_attempt(page,endpoint):
    return page.evaluate('''async url=>{
      if(typeof WebTransport!=='function')return 'unsupported';
      const transport=new WebTransport(url);transport.closed.catch(()=>{});
      const result=await Promise.race([transport.ready.then(()=> 'connected',()=> 'refused'),new Promise(r=>setTimeout(()=>r('pending'),2500))]);
      transport.close();return result;
    }''',endpoint)


def test_native_webtransport_never_reaches_udp_sink(launch_mode):
    import socketserver,threading
    class Sink(socketserver.BaseRequestHandler):
        def handle(self):self.server.arrivals.append(True)
    service=socketserver.ThreadingUDPServer(('127.0.0.1',0),Sink);service.arrivals=[]
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    try:
        _sink_liveness(service,udp=True)
        endpoint=f'https://127.0.0.1:{service.server_address[1]}/synthetic'
        with sync_playwright() as pw:
            control=pw.chromium.launch(**launch_mode)
            try:
                state=_webtransport_attempt(_secure_fixture(control),endpoint)
                assert state!='unsupported', 'native WebTransport unavailable; not verified'
                assert service.arrivals, 'unfenced browser never reached UDP sink; oracle invalid'
            finally:control.close()
            service.arrivals.clear()
            with DenyOnlyProxy() as proxy:
                browser=pw.chromium.launch(**launch_mode,**proxy.browser_options())
                try:
                    assert _webtransport_attempt(_secure_fixture(browser),endpoint)=='refused'
                    assert service.arrivals==[], 'WebTransport bypassed fixed proxy'
                finally:browser.close()
    finally:service.shutdown();service.server_close();thread.join()
