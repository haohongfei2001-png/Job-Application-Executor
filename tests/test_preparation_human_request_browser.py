"""Opaque local transport + cookie continuity; human events are SYNTHETIC.

This file does not prove physical-human input or production UI authorization.
Only a loopback fixture receives synthetic canaries. No real applicant/site data.
"""
import contextlib
import hashlib
import json
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from executor.preparation.network_fence import DenyOnlyProxy
from executor.preparation import human_request as h
from test_qiyunfang_preparation_browser import bounded_browser_oracle

BODY='cmd=addWafCk_addSubmit&formId=6&id=SYNTHETIC_PROTECTED_ID&captcha=SYNTHETIC_CAPTCHA&token=SYNTHETIC_TOKEN'
META={'contract':h.CONTRACT_VERSION,'command':'addWafCk_addSubmit','form_id':6,
      'role':h.CONTRACT_ROLE,'category':'final_application','change_epoch':7}
BINDING={'document':'fixture','root':'fixture-root','change_epoch':7,'frame_id':'synthetic-native-frame'}


@pytest.mark.skipif(sys.platform!='darwin',reason='Native headful Mac dialog gate; Linux display is not provisioned here')
@pytest.mark.parametrize('decision',['cancel','accept'])
def test_headful_native_dialog_pairs_real_events_without_automatic_product_acceptance(monkeypatch,decision):
    """Automation drives a synthetic dialog; this is not physical-human proof."""
    from executor.preparation.session import DisposablePreparationSession
    received=[];consumed=[];opened=[];closed=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            received.append((self.command,self.path,self.rfile.read(int(self.headers.get('Content-Length','0')))))
            self.send_response(204);self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    base=f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(h,'CONTRACT_URL',base+'/');monkeypatch.setattr(h,'FINAL_URL',base+'/final')
    try:
        with DisposablePreparationSession(headless=False,channel=None) as owner:
            page=owner.context.new_page()
            owner.context.route(base+'/',lambda route:route.fulfill(status=200,content_type='text/html',body='<p>Synthetic local confirmation</p>'))
            page.goto(base+'/')
            cdp=owner.context.new_cdp_session(page);cdp.send('Page.enable')
            frame_id=cdp.send('Page.getFrameTree')['frameTree']['frame']['id']
            binding={**BINDING,'frame_id':frame_id}
            def consume(scope):consumed.append(scope);return 'a'*64
            bridge=h.OpaqueHumanRequest(page=page,client=owner.client,binding=binding,
                guard=lambda:dict(binding),consume=consume,record=lambda _,outcome:outcome)
            def opening(event):
                opened.append({key:event.get(key) for key in ('type','frameId','hasBrowserHandler')})
                bridge.dialog_opened(event)
            def closing(event):
                closed.append({key:event.get(key) for key in ('result','frameId')})
                bridge.dialog_closed(event)
            cdp.on('Page.javascriptDialogOpening',opening)
            cdp.on('Page.javascriptDialogClosed',closing)
            page.on('dialog',bridge.dialog_handle)
            owner.context.route(base+'/final',lambda route:bridge.hold(route,META))
            try:
                page.evaluate('''({url,body})=>{fetch(url,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body}).catch(()=>{});}''',
                    {'url':base+'/final','body':BODY})
                for _ in range(100):
                    if bridge._state=='HELD':break
                    page.wait_for_timeout(20)
                assert bridge._state=='HELD' and received==[] and consumed==[]
                bridge.present()
                for _ in range(100):
                    if bridge._dialog is not None and opened:break
                    page.wait_for_timeout(20)
                assert opened==[{'type':'confirm','frameId':frame_id,'hasBrowserHandler':True}]
                assert bridge._state=='DIALOG_OPEN' and received==[] and consumed==[]
                # Only this fixture accepts/dismisses. Product code has no
                # automatic accept call; real user operation remains required.
                if decision=='accept':bridge._dialog.accept()
                else:bridge._dialog.dismiss()
                for _ in range(100):
                    if bridge._state!='DIALOG_OPEN':break
                    page.wait_for_timeout(20)
                result=bridge.drain()
                assert result['status']==('RETURNED_UNVERIFIED' if decision=='accept' else 'CANCELLED')
                assert closed==[{'result':decision=='accept','frameId':frame_id}]
                assert len(consumed)==(decision=='accept')
                assert received==([('POST','/final',BODY.encode())] if decision=='accept' else [])
                bridge.drain();page.wait_for_timeout(50)
                assert len(received)==(decision=='accept')
            finally:
                bridge.cancel();cdp.detach()
    finally:server.shutdown();server.server_close();serving.join()


@pytest.mark.parametrize('server_result',['ok','redirect','disconnect'])
def test_exact_request_opaque_forward_preserves_fresh_session_cookie_and_no_replay(tmp_path,monkeypatch,server_result):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            received.append(('GET',self.path,b'',{}))
            self.send_response(200);self.send_header('Set-Cookie','session=SYNTHETIC_SESSION; HttpOnly; SameSite=Lax; Path=/')
            self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<!doctype html><p>Synthetic form</p>')
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            received.append(('POST',self.path,body,dict(self.headers)))
            if server_result=='disconnect':self.connection.close();return
            self.send_response(307 if server_result=='redirect' else 204)
            if server_result=='redirect':self.send_header('Location','/unexpected-redirect')
            self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(h,'CONTRACT_URL',base+'/');monkeypatch.setattr(h,'FINAL_URL',base+'/final')
    ledger=sqlite3.connect(tmp_path/'opaque-ledger.sqlite3')
    ledger.execute('CREATE TABLE final_intent(slot INTEGER PRIMARY KEY CHECK(slot=1),intent TEXT,outcome TEXT)');ledger.commit()
    events=[]
    def consume(scope):
        intent=hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()
        ledger.execute('INSERT INTO final_intent VALUES(1,?,?)',(intent,'ATTEMPTED'));ledger.commit()
        events.append('durable_consume');return intent
    def record(intent,outcome):
        ledger.execute('UPDATE final_intent SET outcome=? WHERE intent=?',(outcome,intent));ledger.commit();return outcome
    try:
        with DenyOnlyProxy() as proxy,sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,channel='chrome' if sys.platform=='linux' else None,**proxy.browser_options())
            assert proxy.verify_launch(browser)
            client=pw.request.new_context(ignore_https_errors=False)
            context=browser.new_context(service_workers='block',ignore_https_errors=False)
            page=context.new_page()
            # Native-event simulation is isolated to this facade. Production
            # uses real paired CDP events and never calls Dialog.accept().
            facade=SimpleNamespace(main_frame=page.main_frame,evaluate=lambda *_:events.append('synthetic_dialog_present'))
            bridge=h.OpaqueHumanRequest(page=facade,client=client,binding=BINDING,guard=lambda:dict(BINDING),consume=consume,record=record)
            def route(r):
                if r.request.method=='GET' and r.request.url==base+'/':
                    response=client.get(base+'/',max_redirects=0,max_retries=0)
                    try:r.fulfill(response=response)
                    finally:response.dispose()
                elif r.request.method=='POST' and r.request.url==base+'/final':bridge.hold(r,META)
                else:r.abort()
            context.route('**/*',route)
            try:
                page.goto(base+'/')
                page.evaluate('''({target,body})=>{
                  fetch(target,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-Application-Token':'SYNTHETIC_TOKEN'},body}).then(()=>{},()=>{});
                }''',{'target':base+'/final','body':BODY})
                for _ in range(100):
                    if bridge._state=='HELD':break
                    page.wait_for_timeout(20)
                assert bridge._state=='HELD'
                assert [row[0] for row in received]==['GET']
                assert ledger.execute('SELECT * FROM final_intent').fetchall()==[]
                bridge.present()
                bridge.dialog_opened({'type':'confirm','url':base+'/','message':bridge._message,
                                      'frameId':BINDING['frame_id'],'hasBrowserHandler':True})
                bridge.dialog_closed({'result':True,'frameId':BINDING['frame_id']})
                result=bridge.drain()
                expected='RETURNED_UNVERIFIED' if server_result=='ok' else 'UNKNOWN_OUTCOME'
                assert result['status']==expected
                assert ledger.execute('SELECT outcome FROM final_intent').fetchone()[0]==expected
                assert events==['synthetic_dialog_present','durable_consume']
                posts=[row for row in received if row[0]=='POST'];assert len(posts)==1
                assert posts[0][1:3]==('/final',BODY.encode())
                headers={key.lower():value for key,value in posts[0][3].items()}
                assert headers.get('cookie')=='session=SYNTHETIC_SESSION'
                assert headers.get('x-application-token')=='SYNTHETIC_TOKEN'
                assert all(row[1]!='/unexpected-redirect' for row in received)
                bridge.drain();page.wait_for_timeout(50)
                assert len([row for row in received if row[0]=='POST'])==1
                data=(tmp_path/'opaque-ledger.sqlite3').read_bytes()
                assert b'SYNTHETIC_PROTECTED_ID' not in data and b'SYNTHETIC_CAPTCHA' not in data
            finally:
                context.close();client.dispose();browser.close()
    finally:
        ledger.close();server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('input_mode',['disk','memory'])
@pytest.mark.parametrize('encoding',['multipart_file','multipart_slice','multipart_full_slice','binary_chunk'])
def test_native_file_request_is_refused_and_raw_transport_is_characterized(tmp_path,monkeypatch,encoding,input_mode):
    """Refusal test plus diagnostic, never certification of a resume uploader.

    The independent receiver proves the native positive control contains the
    exact synthetic binary. The separate raw fetch diagnostic reports whether
    CDP retained those bytes; either observation leaves production unsupported.
    """
    from email import policy
    from email.parser import BytesParser
    binary=b'%PDF-SYNTHETIC-ONLY\x00\xff\r\n'+bytes(range(256))*16
    received=[];held=[]
    html='''<!doctype html><input type="file" id="file"><button id="send">Synthetic upload</button>
    <script>send.onclick=()=>{
      const file=document.getElementById('file').files[0];
      let body=file.slice(0,file.size,'application/octet-stream');
      if(location.hash.startsWith('#multipart')){
        body=new FormData();body.append('fixture','synthetic');
        const value=location.hash==='#multipart_slice'?file.slice(0,Math.floor(file.size/2),file.type):
          location.hash==='#multipart_full_slice'?file.slice(0,file.size,file.type):file;
        body.append('file',value,file.name);
      }
      fetch('/upload',{method:'POST',body}).then(()=>{document.body.dataset.done='yes'},()=>{document.body.dataset.done='refused'});
    };</script>'''
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(html.encode())
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            content_type=self.headers.get('Content-Type','')
            if content_type.startswith('multipart/form-data;'):
                message=BytesParser(policy=policy.default).parsebytes(
                    ('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
                files=[(part.get_payload(decode=True),part.get_content_type()) for part in message.iter_parts()
                       if part.get_filename()=='synthetic_resume.pdf']
                value,mime=files[0] if len(files)==1 else (None,None)
            else:value,mime=body,content_type
            received.append((self.path,value,mime))
            self.send_response(204);self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    file=tmp_path/'synthetic_resume.pdf';file.write_bytes(binary)
    expected=binary[:len(binary)//2] if encoding=='multipart_slice' else binary
    expected_mime='application/pdf' if encoding.startswith('multipart') else 'application/octet-stream'
    def choose_and_send(page):
        # Characterize separately: disk-backed native selection and an exact
        # in-memory FilePayload represent different browser upload primitives.
        page.locator('#file').set_input_files(str(file) if input_mode=='disk' else
            {'name':file.name,'mimeType':'application/pdf','buffer':binary})
        page.locator('#send').click()
    try:
        with sync_playwright() as pw:
            mode={'headless':True,'channel':'chrome' if sys.platform=='linux' else None}
            # Same installed/bundled target and enabled Chromium sandbox, with
            # only the network fence absent for the native positive control.
            native=pw.chromium.launch(chromium_sandbox=True,**mode)
            try:
                page=native.new_page();page.goto(base+'/#'+encoding);choose_and_send(page)
                page.wait_for_function("document.body.dataset.done==='yes'")
                assert received==[('/upload',expected,expected_mime)],'native binary fixture did not reach independent recipient intact'
            finally:native.close()
            received.clear()
            with DenyOnlyProxy() as proxy:
                browser=pw.chromium.launch(**mode,**proxy.browser_options());assert proxy.verify_launch(browser)
                context=browser.new_context(service_workers='block');page=context.new_page()
                client=pw.request.new_context(ignore_https_errors=False)
                def route(r):
                    if r.request.method=='GET' and r.request.url==base+'/':
                        response=client.get(base+'/',max_redirects=0,max_retries=0)
                        try:r.fulfill(response=response)
                        finally:response.dispose()
                    elif r.request.method=='POST' and r.request.url==base+'/upload':held.append(r)
                    else:r.abort()
                context.route('**/*',route)
                try:
                    page.goto(base+'/#'+encoding);choose_and_send(page)
                    for _ in range(100):
                        if held:break
                        page.wait_for_timeout(20)
                    assert len(held)==1 and received==[],'upload was not paused before recipient effect'
                    content_type=held[0].request.headers.get('content-type','')
                    if encoding.startswith('multipart'):
                        assert content_type.startswith('multipart/form-data; boundary=')
                    else:assert content_type=='application/octet-stream'
                    monkeypatch.setattr(h,'FINAL_URL',base+'/upload')
                    bridge=h.OpaqueHumanRequest(page=page,client=client,binding=BINDING,guard=lambda:dict(BINDING),
                        consume=lambda _:pytest.fail('file request acquired final-send authority'),record=lambda *_:pytest.fail('file request acquired final journal'))
                    with pytest.raises(h.HumanRequestConflict):bridge.hold(held[0],META)
                    assert received==[] and bridge._state=='EMPTY'
                    page.wait_for_function("document.body.dataset.done==='refused'")
                    # Start a DIFFERENT native request for byte characterization.
                    # An aborted request would confound byte fidelity with the
                    # lifetime of the first refusal-only route.
                    choose_and_send(page)
                    for _ in range(100):
                        if len(held)==2:break
                        page.wait_for_timeout(20)
                    assert len(held)==2 and received==[]
                    assert held[1].request is not held[0].request
                    # Research-only exact Request transport to the SYNTHETIC
                    # loopback sink. No production helper/permission is added.
                    response=client.fetch(held[1].request,max_redirects=0,max_retries=0,timeout=5000)
                    try:
                        assert response.status==204
                        held[1].fulfill(response=response)
                    finally:response.dispose()
                    assert len(received)==1 and received[0][0]=='/upload'
                    print('UPLOAD_CHARACTERIZATION '+json.dumps({'encoding':encoding,'input_mode':input_mode,
                        'native_control_exact':True,'raw_request_binary_exact':received[0][1]==expected,
                        'raw_request_mime_exact':received[0][2]==expected_mime,
                        'production_upload_admitted':False}),flush=True)
                finally:context.close();client.dispose();browser.close()
    finally:server.shutdown();server.server_close();thread.join()
