"""Synthetic paused-XHR proof; no real applicant or external application request."""
from __future__ import annotations

import contextlib
import json
from urllib.parse import quote

import pytest
from executor.preparation import request_classifier as c
from test_qiyunfang_preparation_browser import browser,bounded_browser_oracle
from test_preparation_change_epoch_browser import HTML
from test_preparation_authority_v1 import fixture as authority_fixture,offer,consume,SESSION,BROWSER


CANARY='SYNTHETIC_PRIVATE_身份_🚀\\\"\nCANARY'


def body(*,role=c.CONTRACT_ROLE,rows=None,patch=None,extra=''):
    if rows is None:
        rows=[{'id':int(f.field_id),'type':int(f.data_type),'must':f.required_marker,
               'val':role if f.field_id=='8' else CANARY} for f in c.FIELDS if f.field_id.isdigit()]
    values={'cmd':'addWafCk_addSubmit','formId':'6',
            'submitContentList':quote(json.dumps(rows,ensure_ascii=False,separators=(',',':')),safe=''),
            'vCodeId':'15676','validateCode':'SYNTHETIC_CAPTCHA',
            'tmpFileList':quote('[]',safe=''),'submitOrigin':quote('{}',safe=''),
            'phoneValidateCodes':quote('[]',safe='')}
    values.update(patch or {})
    return '&'.join(key+'='+value for key,value in values.items())+extra


SEND='''({url,body,type})=>{
  const xhr=new XMLHttpRequest();xhr.open('POST',url,true);
  xhr.setRequestHeader('Content-Type',type||'application/x-www-form-urlencoded; charset=UTF-8');
  xhr.setRequestHeader('X-Application-Token','SYNTHETIC_TOKEN');
  xhr.send(body);globalThis.testXHR=xhr;
}'''


@contextlib.contextmanager
def prepared(browser,*,arm=True):
    context=browser.new_context(service_workers='block');owner=c.RetainedXHRClassifier(context);routes=[]
    def route(r):
        if r.request.url==c.CONTRACT_URL and r.request.method=='GET':
            r.fulfill(status=200,content_type='text/html',headers={'Set-Cookie':'session=SYNTHETIC_SESSION; HttpOnly; SameSite=Lax; Path=/'},body=HTML)
        elif r.request.url==c.FINAL_URL and r.request.method=='POST':routes.append(r)
        else:r.abort()
    context.route('**/*',route);page=context.new_page();page.goto(c.CONTRACT_URL);page.wait_for_timeout(30)
    if arm:owner.arm()
    try:yield context,page,owner,routes
    finally:
        for r in routes:
            try:r.abort()
            except Exception:pass
        context.close()


def send(page,value,**extra):page.evaluate(SEND,{'url':c.FINAL_URL,'body':value,**extra})


def settle(page,routes):
    for _ in range(30):
        page.wait_for_timeout(10)
        if routes:return


def test_exact_native_request_gets_only_fixed_metadata_and_repeated_identity_guard(browser):
    with prepared(browser) as (_,page,owner,routes):
        send(page,body());settle(page,routes)
        assert len(routes)==1
        request=routes[0].request;meta=owner.classify(request)
        assert meta=={'contract':c.CONTRACT_VERSION,'command':'addWafCk_addSubmit','form_id':6,
                      'role':c.CONTRACT_ROLE,'category':'final_application','change_epoch':owner.baseline}
        assert CANARY not in json.dumps(meta,ensure_ascii=False)
        assert owner.require_exact(request)==owner.baseline
        assert owner.require_exact(request)==owner.baseline
        with pytest.raises(c.RequestClassifierConflict):owner.classify(request)


@pytest.mark.parametrize('fault',[
    'wrong_command','wrong_form','wrong_role','wrong_vcode','duplicate_parameter','extra_parameter',
    'missing_role','duplicate_field','duplicate_json_key','unknown_field','wrong_type','object_value',
    'trailing_json','bad_escape','wrong_content_type',
])
def test_malformed_or_ambiguous_purpose_never_creates_a_native_send(browser,fault):
    rows=[{'id':int(f.field_id),'type':int(f.data_type),'must':f.required_marker,
           'val':c.CONTRACT_ROLE if f.field_id=='8' else CANARY} for f in c.FIELDS if f.field_id.isdigit()]
    options={};patch={};extra=''
    if fault=='wrong_command':patch['cmd']='other'
    elif fault=='wrong_form':patch['formId']='7'
    elif fault=='wrong_role':
        next(row for row in rows if row['id']==8)['val']='Other role'
    elif fault=='wrong_vcode':patch['vCodeId']='15686'
    elif fault=='duplicate_parameter':extra='&cmd=addWafCk_addSubmit'
    elif fault=='extra_parameter':extra='&unexpected=SYNTHETIC'
    elif fault=='missing_role':rows=[row for row in rows if row['id']!=8]
    elif fault=='duplicate_field':rows[0]['id']=8
    elif fault=='unknown_field':rows[0]['id']=99
    elif fault=='wrong_type':next(row for row in rows if row['id']==8)['type']=0
    elif fault=='object_value':rows[0]['val']={'value':CANARY}
    elif fault=='wrong_content_type':options['type']='text/plain'
    if fault in {'duplicate_json_key','trailing_json','bad_escape'}:
        raw=json.dumps(rows,ensure_ascii=False,separators=(',',':'))
        if fault=='duplicate_json_key':raw=raw.replace('"id":0','"id":8,"id":0',1)
        if fault=='trailing_json':raw+='[]'
        if fault=='bad_escape':raw=raw.replace('SYNTHETIC_PRIVATE','\\qSYNTHETIC_PRIVATE',1)
        patch['submitContentList']=quote(raw,safe='')
    with prepared(browser) as (_,page,owner,routes):
        send(page,body(rows=rows,patch=patch,extra=extra),**options);page.wait_for_timeout(30)
        assert routes==[]
        with pytest.raises(c.RequestClassifierConflict):owner.classify(None)


def test_opaque_object_body_is_not_coerced_or_sent(browser):
    with prepared(browser) as (_,page,owner,routes):
        page.evaluate('''url=>{
          globalThis.bodyCoercions=0;const xhr=new XMLHttpRequest();xhr.open('POST',url,true);
          xhr.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
          xhr.send({toString(){bodyCoercions++;throw Error('PRIVATE_BODY_CANARY')}});
        }''',c.FINAL_URL)
        assert page.evaluate('bodyCoercions')==0 and routes==[]
        with pytest.raises(c.RequestClassifierConflict):owner.classify(None)


@pytest.mark.parametrize('intrusion',['second_xhr','fetch','beacon','form','change_and_restore','abort','new_frame','other_image'])
def test_post_capture_intrusion_cannot_become_another_authorized_request(browser,intrusion):
    with prepared(browser) as (_,page,owner,routes):
        value=body();send(page,value);settle(page,routes);assert len(routes)==1
        request=routes[0].request
        if intrusion=='second_xhr':send(page,value)
        elif intrusion=='fetch':page.evaluate("url=>fetch(url,{method:'POST',body:'unclassified'}).catch(()=>{})",c.FINAL_URL)
        elif intrusion=='beacon':page.evaluate("url=>navigator.sendBeacon(url,'unclassified')",c.FINAL_URL)
        elif intrusion=='form':page.evaluate("url=>{const f=document.createElement('form');f.action=url;f.method='POST';f.submit()}",c.FINAL_URL)
        elif intrusion=='change_and_restore':page.evaluate("const f=document.querySelector('#field');f.value='changed';f.value='SYNTHETIC'")
        elif intrusion=='abort':page.evaluate('testXHR.abort()')
        elif intrusion=='new_frame':page.evaluate("const f=document.createElement('iframe');document.body.append(f);f.remove()")
        elif intrusion=='other_image':page.evaluate("const i=new Image();i.src='https://synthetic.invalid/other.png'")
        page.wait_for_timeout(30)
        with pytest.raises(c.RequestClassifierConflict):owner.classify(request)
        with pytest.raises(c.RequestClassifierConflict):owner.require_exact(request)
        assert len(routes)==1


@pytest.mark.parametrize('intrinsic',['parse','ownKeys','indexOf','slice'])
def test_captured_intrinsics_cannot_be_replaced_to_classify_the_wrong_role(browser,intrinsic):
    with prepared(browser) as (_,page,owner,routes):
        page.evaluate('''({url,body,intrinsic})=>{
          const target=intrinsic==='parse'?JSON:intrinsic==='ownKeys'?Reflect:String.prototype;
          const old=target[intrinsic];target[intrinsic]=()=>true;
          try{const x=new XMLHttpRequest();x.open('POST',url,true);x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');x.send(body)}
          finally{target[intrinsic]=old}
        }''',{'url':c.FINAL_URL,'body':body(role='Wrong role'),'intrinsic':intrinsic})
        page.wait_for_timeout(30);assert routes==[]
        with pytest.raises(c.RequestClassifierConflict):owner.classify(None)


def test_prearm_final_send_is_refused_without_an_outbound_request(browser):
    with prepared(browser,arm=False) as (_,page,owner,routes):
        send(page,body());page.wait_for_timeout(30);assert routes==[]
        with pytest.raises(c.RequestClassifierConflict):owner.arm()


def test_page_early_arm_cannot_replace_native_owner_arm(browser):
    with prepared(browser,arm=False) as (_,page,owner,routes):
        assert page.evaluate('key=>globalThis[key].arm()',owner.capture_key) is True
        with pytest.raises(c.RequestClassifierConflict):owner.arm()
        assert routes==[]


def test_native_request_bytes_remain_exact_and_are_never_read_by_the_classifier(browser,monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            received.append((self.rfile.read(int(self.headers['Content-Length'])),self.headers.get('Cookie'),self.headers.get('X-Application-Token')))
            self.send_response(204);self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(c,'CONTRACT_URL',base+'/');monkeypatch.setattr(c,'FINAL_URL',base+'/final')
    try:
        with prepared(browser) as (context,page,owner,routes):
            value=body();send(page,value);settle(page,routes);assert len(routes)==1 and received==[]
            request=routes[0].request;owner.classify(request);owner.require_exact(request)
            # Test-only local sink dispatch. The classifier has no fetch/send or
            # permission method, and this does not simulate physical-human proof.
            response=context.request.fetch(request,max_redirects=0,max_retries=0)
            try:assert response.status==204
            finally:response.dispose()
            assert received==[(value.encode(),'session=SYNTHETIC_SESSION','SYNTHETIC_TOKEN')]
            with pytest.raises(c.RequestClassifierConflict):owner.classify(request)
            assert len(received)==1
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('opening',["x.open('POST',url,false)","x.open('POST',url,true,'SYNTHETIC_USER','SYNTHETIC_PASSWORD')"])
def test_sync_or_credentialed_open_cannot_deadlock_or_create_a_pending_send(browser,opening):
    with prepared(browser) as (_,page,owner,routes):
        page.evaluate('''({url,body,opening})=>{
          const x=new XMLHttpRequest();try{eval(opening);x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');x.send(body)}catch(_){}
        }''',{'url':c.FINAL_URL,'body':body(),'opening':opening})
        page.wait_for_timeout(30);assert routes==[]
        with pytest.raises(c.RequestClassifierConflict):owner.classify(None)


def test_native_open_arguments_ignore_replaced_array_iteration(browser):
    with prepared(browser) as (_,page,owner,routes):
        page.evaluate('''({url,body})=>{
          const original=Array.prototype[Symbol.iterator];
          Array.prototype[Symbol.iterator]=function*(){yield false;yield 'SYNTHETIC_USER';yield 'SYNTHETIC_PASSWORD'};
          try{const x=new XMLHttpRequest();x.open('POST',url,true,undefined,undefined);x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');x.send(body)}
          finally{Array.prototype[Symbol.iterator]=original}
        }''',{'url':c.FINAL_URL,'body':body()})
        settle(page,routes);assert len(routes)==1
        owner.classify(routes[0].request);owner.require_exact(routes[0].request)


@pytest.mark.parametrize('constructor',['Worker','Worker.prototype.constructor','SharedWorker','SharedWorker.prototype.constructor'])
def test_worker_constructor_and_native_alias_cannot_create_an_unclassified_xhr(browser,constructor):
    with prepared(browser) as (_,page,owner,routes):
        send(page,body());settle(page,routes);assert len(routes)==1
        request=routes[0].request
        page.evaluate('''constructor=>{
          const url=URL.createObjectURL(new Blob(['self.onmessage=()=>{}'],{type:'text/javascript'}));
          try{const C=eval(constructor);new C(url)}catch(_){}
          URL.revokeObjectURL(url);
        }''',constructor)
        page.wait_for_timeout(30)
        with pytest.raises(c.RequestClassifierConflict):owner.classify(request)
        assert len(routes)==1


@pytest.mark.parametrize('outcome',['return','cancel','edit_after_confirm','redirect','disconnect'])
def test_classifier_composes_with_real_durable_slot_and_opaque_bridge_without_replay(browser,authority_fixture,monkeypatch,outcome):
    """Actual XHR/SQLite/sink; native human events and process binding are fixtures."""
    import copy
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from types import SimpleNamespace
    from executor.preparation import human_request as h,final_journal as f
    from executor.preparation.authority import PreparationJournal,PreparationConflict
    from test_preparation_final_journal_v1 import rows
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            if outcome=='disconnect':self.connection.close();return
            self.send_response(307 if outcome=='redirect' else 204)
            if outcome=='redirect':self.send_header('Location','/unexpected')
            self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    base=f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(c,'CONTRACT_URL',base+'/');monkeypatch.setattr(c,'FINAL_URL',base+'/final')
    monkeypatch.setattr(h,'CONTRACT_URL',base+'/');monkeypatch.setattr(h,'FINAL_URL',base+'/final')
    monkeypatch.setattr(f,'FINAL_URL',base+'/final')
    q,task,_,authority,_=authority_fixture
    permit=consume(authority_fixture,offer(authority_fixture))
    preparation=PreparationJournal(authority,permit,SESSION,BROWSER)
    for key in ['0','8']:
        action=preparation.before(key);preparation.after(action,'READBACK_VERIFIED')
    preparation.complete()
    authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=False,session=SESSION,
                     browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    try:
        with prepared(browser) as (context,page,classifier,routes):
            value=body();send(page,value);settle(page,routes);assert len(routes)==1 and received==[]
            route=routes[0];request=route.request;metadata=classifier.classify(request)
            binding={'browser':dict(BROWSER),'frame_id':'SYNTHETIC_NATIVE_FRAME','change_epoch':metadata['change_epoch']}
            def observe():
                classifier.require_exact(request)
                return copy.deepcopy(binding)
            journal=f.PreparationFinalJournal(authority,permit,SESSION,observe)
            # No product Dialog.accept call. This finite facade supplies explicit
            # synthetic native events solely to exercise component composition.
            facade=SimpleNamespace(main_frame=page.main_frame,evaluate=lambda *_:None)
            bridge=h.OpaqueHumanRequest(page=facade,client=context.request,binding=binding,
                guard=journal.guard,consume=journal.consume,record=journal.record)
            bridge.hold(route,metadata)
            assert bridge.drain()=={'status':'HELD'} and rows(q)==[] and received==[]
            bridge.present()
            bridge.dialog_opened({'type':'confirm','url':h.CONTRACT_URL,'message':bridge._message,
                                  'frameId':binding['frame_id'],'hasBrowserHandler':True})
            assert rows(q)==[] and received==[]
            bridge.dialog_closed({'result':outcome!='cancel','frameId':binding['frame_id']})
            if outcome=='edit_after_confirm':page.evaluate("document.querySelector('#field').value='changed'")
            result=bridge.drain();bridge.drain()
            expected='RETURNED_UNVERIFIED' if outcome=='return' else 'UNKNOWN_OUTCOME' if outcome in {'redirect','disconnect'} else 'CANCELLED'
            assert result['status']==expected
            if outcome in {'cancel','edit_after_confirm'}:
                assert rows(q)==[] and received==[]
            else:
                assert received==[value.encode()]
                assert len(rows(q))==1 and rows(q)[0]['outcome']==expected
                with pytest.raises((PreparationConflict,c.RequestClassifierConflict)):f.PreparationFinalJournal(authority,permit,SESSION,observe)
            assert CANARY not in json.dumps(rows(q),ensure_ascii=False)
            assert 'PRIVATE_AUTHORITY_CANARY' not in json.dumps(rows(q))
            assert q.get(task['task_id'])['stage']=='BLOCKED'
    finally:
        server.shutdown();server.server_close();serving.join()
        authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
