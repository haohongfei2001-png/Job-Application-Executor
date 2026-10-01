"""Synthetic-only success-budgeted check of the current owned native browser.

No employer request or applicant data is used. A separate temporary context has
no website cookies or preparation routes. Its canary requests must be rejected
by the native deny proxy itself, while an independent local receiver is proved
reachable by the Python control. The receipt is process-local, not a persistent
credential or a claim of OS-wide network isolation. The12s network-probe budget starts after synthetic-context setup and is checked
between completed operations; the owning controller supplies the whole-open
90s timeout/revocation. Neither budget claims to preempt a hung renderer here.
"""
from __future__ import annotations

import http.client
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# Chromium's FailureMessageFromNetError special-cases this exact native text
# for ERR_TUNNEL_CONNECTION_FAILED. It is not a generic connection refusal.
# https://github.com/chromium/chromium/blob/d7d74add46d2971946a3ade2a5b990e3e30dfd4a/net/websockets/websocket_stream.cc#L266-L276
_CHROMIUM_PROXY_TUNNEL_MESSAGE = 'Establishing a tunnel via proxy server failed.'
_PROXY_ERRORS = frozenset({'net::ERR_PROXY_CONNECTION_FAILED','net::ERR_TUNNEL_CONNECTION_FAILED'})


def native_proxy_error_code(message):
    if message == _CHROMIUM_PROXY_TUNNEL_MESSAGE:return 'net::ERR_TUNNEL_CONNECTION_FAILED'
    for code in _PROXY_ERRORS:
        if message in {code,'Error in connection establishment: '+code}:return code
    return None


class NativeSelfCheckFailed(RuntimeError):
    def __init__(self,code='native_preparation_self_check_failed',diagnostic=None):
        super().__init__(code)
        self.diagnostic=diagnostic or {}


def check_owned_native_browser(owner, *, still_authorized=lambda:True, clock=time.monotonic):
    start=clock()
    if still_authorized() is not True:raise NativeSelfCheckFailed()
    browser=owner.browser
    before=list(browser.contexts)
    if before != [owner.context] or owner.proxy.verify_launch(browser) is not True:
        raise NativeSelfCheckFailed()
    identity=owner.identity.verify(browser)
    if still_authorized() is not True or clock()-start>90:raise NativeSelfCheckFailed()
    nonce=secrets.token_hex(16)
    positive='/positive/'+nonce
    unexpected=[]
    connections=[]

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_GET(self):
            if self.path != positive: unexpected.append(True)
            body=b'SYNTHETIC_LOCAL_CONTROL'
            self.send_response(200);self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def do_POST(self):
            # Record presence only. Never inspect or retain a request body.
            unexpected.append(True)
            self.send_response(204);self.send_header('Content-Length','0');self.end_headers()

    class ReceiverServer(ThreadingHTTPServer):
        def get_request(self):
            result=super().get_request();connections.append(True);return result
        def handle_error(self,*_):pass

    server=ReceiverServer(('127.0.0.1',0),Receiver)
    server.daemon_threads=True
    serving=None
    started=False
    context=None
    cdp=None
    failed=False
    try:
        serving=threading.Thread(target=lambda:server.serve_forever(poll_interval=.05),daemon=True)
        serving.start();started=True
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        try:
            connection.request('GET',positive)
            response=connection.getresponse()
            if response.status!=200 or response.read()!=b'SYNTHETIC_LOCAL_CONTROL':
                raise NativeSelfCheckFailed()
        finally:connection.close()
        if len(connections)!=1 or still_authorized() is not True:raise NativeSelfCheckFailed()
        context=browser.new_context(service_workers='block',accept_downloads=False,ignore_https_errors=False)
        page=context.new_page();page.set_default_timeout(3000)
        base=f'http://127.0.0.1:{server.server_port}'
        bootstrap=base+'/bootstrap/'+nonce
        def bootstrap_only(route):
            if route.request.method!='GET':route.abort('blockedbyclient');return
            route.fulfill(status=200,content_type='text/html',body='<!doctype html><title>Local browser check</title>')
        context.route(bootstrap,bootstrap_only)
        page.goto(bootstrap,timeout=3000)
        context.unroute(bootstrap,bootstrap_only)
        evidence={};socket_urls={};native_observations={};proofs={}
        proxy_errors=_PROXY_ERRORS
        def response_seen(response):
            if response.url in evidence:
                native_observations[response.url].append('http_status_'+str(response.status))
                if response.status==403:evidence[response.url].append('proxy_http_403')
        def request_failed(request):
            if request.url in evidence:
                code=request.failure
                native_observations[request.url].append(code if isinstance(code,str) and re.fullmatch(r'net::ERR_[A-Z_]+',code) else 'unclassified_failure')
                if code in proxy_errors:evidence[request.url].append('native_proxy_failure')
        page.on('response',response_seen);page.on('requestfailed',request_failed)
        cdp=context.new_cdp_session(page);cdp.send('Network.enable')
        def socket_created(event):
            if event.get('url') in evidence:
                socket_urls[event.get('requestId')]=event['url']
                native_observations[event['url']].append('websocket_created')
        def socket_response(event):
            url=socket_urls.get(event.get('requestId'))
            if url:
                status=event.get('response',{}).get('status')
                native_observations[url].append('websocket_status_'+str(status) if type(status) is int else 'unclassified_status')
                if status==403:evidence[url].append('proxy_websocket_403')
        def socket_error(event):
            url=socket_urls.get(event.get('requestId'));message=event.get('errorMessage','')
            proxy_code=native_proxy_error_code(message)
            if url:
                native_observations[url].extend([proxy_code] if proxy_code else re.findall(r'net::ERR_[A-Z_]+',message) or ['unclassified_websocket_error'])
            if url and (proxy_code is not None or 'Unexpected response code: 403' in message):
                evidence[url].append('native_proxy_failure')
        cdp.on('Network.webSocketCreated',socket_created)
        cdp.on('Network.webSocketHandshakeResponseReceived',socket_response)
        cdp.on('Network.webSocketFrameError',socket_error)
        if still_authorized() is not True or clock()-start>90:raise NativeSelfCheckFailed()
        probe_start=clock()
        # No request-routing handler is present for any probe. The original
        # applicant context and its transport remain untouched throughout.
        for mode in ('fetch','fetch_tls','websocket','websocket_tls'):
            if still_authorized() is not True:raise NativeSelfCheckFailed()
            denied=owner.proxy.denied_connections
            target=base+'/blocked-'+mode+'/'+nonce
            if mode.endswith('_tls'):target=target.replace('http:','https:',1)
            native_url=target.replace('http','ws',1) if mode.startswith('websocket') else target
            evidence[native_url]=[];native_observations[native_url]=[]
            result=page.evaluate('''async ({mode,target})=>{
              if(mode.startsWith('fetch')){
                const control=new AbortController();let expired=false;
                const timer=setTimeout(()=>{expired=true;control.abort()},1800);
                try{const response=await fetch(target,{method:'POST',body:'SYNTHETIC_NATIVE_CANARY',
                  cache:'no-store',signal:control.signal});return {kind:'response',status:response.status};}
                catch(_){return {kind:expired?'timeout':'blocked'};}
                finally{clearTimeout(timer)}
              }
              return await new Promise(resolve=>{
                let complete=false;const socket=new WebSocket(target.replace(/^http/,'ws'));
                const finish=kind=>{if(complete)return;complete=true;clearTimeout(timer);socket.close();resolve({kind})};
                const timer=setTimeout(()=>finish('timeout'),1800);
                socket.onopen=()=>finish('escaped');socket.onerror=()=>finish('blocked');
              });
            }''',{'mode':mode,'target':target})
            # CDP network events and the evaluate reply use separate delivery
            # paths. Pump until the scoped proof arrives, never infer it from
            # a fulfilled JS promise or an unrelated global denial.
            observe_until=min(probe_start+12,clock()+.5)
            while not evidence[native_url] and clock()<observe_until:
                if still_authorized() is not True or len(connections)!=1:break
                page.wait_for_timeout(10)
            if (result not in ({'kind':'blocked'},{'kind':'response','status':403})
                    or owner.proxy.denied_connections<=denied or not evidence[native_url]
                    or unexpected or len(connections)!=1 or still_authorized() is not True
                    or clock()-probe_start>12 or clock()-start>90):
                raise NativeSelfCheckFailed('native_selfcheck_'+mode+'_unverified',
                    {'protocol':mode,'outcome':result.get('kind') if result.get('kind') in {'blocked','response','timeout','escaped'} else 'invalid',
                     'response_status':result.get('status') if type(result.get('status')) is int else None,
                     'native_observations':native_observations[native_url][:8],
                     'scoped_proof':bool(evidence[native_url]),'proxy_denial_seen':owner.proxy.denied_connections>denied,
                     'receiver_connections':len(connections),'revoked':still_authorized() is not True,
                     'success_budget_exceeded':clock()-probe_start>12,'overall_budget_exceeded':clock()-start>90,
                     'setup_ms':int((probe_start-start)*1000),'probe_elapsed_ms':int((clock()-probe_start)*1000)})
            proofs[mode]={'proof':list(evidence[native_url]),'native_observations':native_observations[native_url][:8]}
        if owner.proxy.verify_launch(browser) is not True or owner.identity.verify(browser)!=identity:
            raise NativeSelfCheckFailed()
    except BaseException:
        failed=True
        raise
    finally:
        cleanup_ok=True
        if cdp is not None:
            try:cdp.detach()
            except Exception:cleanup_ok=False
        if context is not None:
            try:context.close()
            except Exception:cleanup_ok=False
        if started:server.shutdown()
        server.server_close()
        if started:
            serving.join(timeout=2)
            if serving.is_alive():cleanup_ok=False
        try:
            if list(browser.contexts)!=before:cleanup_ok=False
        except Exception:cleanup_ok=False
        if not failed and (not cleanup_ok or unexpected or len(connections)!=1):raise NativeSelfCheckFailed()
    if (still_authorized() is not True or owner.identity.verify(browser)!=identity
            or owner.proxy.verify_launch(browser) is not True):
        raise NativeSelfCheckFailed()
    if clock()-start>90 or still_authorized() is not True:raise NativeSelfCheckFailed()
    return {'schema':'native-loopback-self-check-v1','process_sha':identity,
            'browser_version':browser.version,'control_reachable':True,
            'native_proxy_blocked':True,'temporary_context_closed':True,
            'protocols':['http','https','ws','wss'],'proof_by_protocol':proofs,
            'external_recipient_used':False,'applicant_data_used':False}
