"""Opaque local transport + cookie continuity; human events are SYNTHETIC.

This file does not prove physical-human input or production UI authorization.
Only a loopback fixture receives synthetic canaries. No real applicant/site data.
"""
import contextlib
import hashlib
import json
import sqlite3
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
            browser=pw.chromium.launch(headless=True,**proxy.browser_options())
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
