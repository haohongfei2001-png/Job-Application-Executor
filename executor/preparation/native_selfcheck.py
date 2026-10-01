"""Synthetic-only success-budgeted check of the current owned native browser.

No employer request or applicant data is used. A separate temporary context has
no website cookies or preparation routes. Its canary requests must be rejected
by the native deny proxy itself, while an independent local receiver is proved
reachable by the Python control. The receipt is process-local, not a persistent
credential or a claim of OS-wide network isolation. The12s success budget is
checked between completed operations; the owning controller supplies outer
timeout/revocation. It is not a claim that a hung renderer can be preempted here.
"""
from __future__ import annotations

import http.client
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class NativeSelfCheckFailed(RuntimeError):
    def __init__(self,code='native_preparation_self_check_failed'): super().__init__(code)


def check_owned_native_browser(owner, *, still_authorized=lambda:True):
    start=time.monotonic()
    if still_authorized() is not True:raise NativeSelfCheckFailed()
    browser=owner.browser
    before=list(browser.contexts)
    if before != [owner.context] or owner.proxy.verify_launch(browser) is not True:
        raise NativeSelfCheckFailed()
    identity=owner.identity.verify(browser)
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

    server=ReceiverServer(('127.0.0.1',0),Receiver)
    server.daemon_threads=True
    serving=threading.Thread(target=lambda:server.serve_forever(poll_interval=.05),daemon=True)
    serving.start()
    context=None
    cdp=None
    failed=False
    try:
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
        evidence={};socket_urls={}
        proxy_errors={'net::ERR_PROXY_CONNECTION_FAILED','net::ERR_TUNNEL_CONNECTION_FAILED'}
        def response_seen(response):
            if response.url in evidence and response.status==403:evidence[response.url].append('proxy_http_403')
        def request_failed(request):
            if request.url in evidence and request.failure in proxy_errors:evidence[request.url].append('native_proxy_failure')
        page.on('response',response_seen);page.on('requestfailed',request_failed)
        cdp=context.new_cdp_session(page);cdp.send('Network.enable')
        def socket_created(event):
            if event.get('url') in evidence:socket_urls[event.get('requestId')]=event['url']
        def socket_response(event):
            url=socket_urls.get(event.get('requestId'))
            if url and event.get('response',{}).get('status')==403:evidence[url].append('proxy_websocket_403')
        def socket_error(event):
            url=socket_urls.get(event.get('requestId'));message=event.get('errorMessage','')
            if url and (any(message.endswith(error) for error in proxy_errors) or 'Unexpected response code: 403' in message):
                evidence[url].append('native_proxy_failure')
        cdp.on('Network.webSocketCreated',socket_created)
        cdp.on('Network.webSocketHandshakeResponseReceived',socket_response)
        cdp.on('Network.webSocketFrameError',socket_error)
        # No request-routing handler is present for either probe. The original
        # applicant context and its transport remain untouched throughout.
        for mode in ('fetch','fetch_tls','websocket','websocket_tls'):
            if still_authorized() is not True:raise NativeSelfCheckFailed()
            denied=owner.proxy.denied_connections
            target=base+'/blocked-'+mode+'/'+nonce
            if mode.endswith('_tls'):target=target.replace('http:','https:',1)
            native_url=target.replace('http','ws',1) if mode.startswith('websocket') else target
            evidence[native_url]=[]
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
            if (result not in ({'kind':'blocked'},{'kind':'response','status':403})
                    or owner.proxy.denied_connections<=denied or not evidence[native_url]
                    or unexpected or len(connections)!=1 or still_authorized() is not True
                    or time.monotonic()-start>12):
                raise NativeSelfCheckFailed('native_selfcheck_'+mode+'_unverified')
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
        server.shutdown();server.server_close();serving.join(timeout=2)
        if serving.is_alive():cleanup_ok=False
        try:
            if list(browser.contexts)!=before:cleanup_ok=False
        except Exception:cleanup_ok=False
        if not failed and (not cleanup_ok or unexpected or len(connections)!=1):raise NativeSelfCheckFailed()
    if (still_authorized() is not True or owner.identity.verify(browser)!=identity
            or owner.proxy.verify_launch(browser) is not True):
        raise NativeSelfCheckFailed()
    return {'schema':'native-loopback-self-check-v1','process_sha':identity,
            'browser_version':browser.version,'control_reachable':True,
            'native_proxy_blocked':True,'temporary_context_closed':True,
            'protocols':['http','https','ws','wss'],
            'external_recipient_used':False,'applicant_data_used':False}
