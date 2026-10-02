"""Owned native Mac composition; a fixture, not a person, drives the dialog.

Only an independent loopback server receives synthetic bytes. No source API,
process identity, native admission, dialog event, kernel or durable journal is
replaced. The site's URL is relocated to the loopback fixture explicitly.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import pytest

from executor.autonomy import task_preparation as task_module
from executor.autonomy.queue import TaskQueue,TaskSpec
from executor.preparation import request_classifier as c,human_request as h,final_journal as f
from executor.preparation import observation as observation_module,runner as runner_module
from executor.preparation.authority import PreparationAuthority,PreparationJournal,PreparationConflict
from executor.preparation.native_admission import NativePreparationAdmission
from executor.preparation.session import DisposablePreparationSession
from test_qiyunfang_preparation_browser import replica,bounded_browser_oracle
from test_preparation_request_classifier_browser import body,SEND
from test_preparation_final_journal_v1 import rows


pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Actual installed headed Chrome/native-handler proof requires the hosted Mac target')


@pytest.mark.parametrize('decision',['accept','cancel'])
def test_owned_native_classifier_kernel_dialog_and_final_slot(tmp_path,monkeypatch,decision):
    html=replica();received=[];opened=[];closed=[];permit=None;identity=None;authority=None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            data=html.encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=UTF-8')
            self.send_header('Set-Cookie','session=SYNTHETIC_NATIVE_SESSION; HttpOnly; SameSite=Lax; Path=/')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            received.append((self.rfile.read(int(self.headers['Content-Length'])),self.headers.get('Cookie'),self.headers.get('X-Application-Token')))
            self.send_response(204);self.send_header('Content-Length','0');self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    for module in (c,h,task_module,observation_module,runner_module):monkeypatch.setattr(module,'CONTRACT_URL',base+'/')
    for module in (c,h,f):monkeypatch.setattr(module,'FINAL_URL',base+'/final')
    root=tmp_path.resolve();profile=root/'profile.json'
    profile.write_text(json.dumps({'fields':{'identity.full_name':{'value':'SYNTHETIC_NATIVE_APPLICANT'}}}))
    profile.chmod(0o600)
    queue=TaskQueue(root/'state')
    task=queue.enqueue(TaskSpec(company=task_module.CONTRACT_COMPANY,role=c.CONTRACT_ROLE,
                               target_url=base+'/',profile_ref=str(profile)))
    session='NATIVE_SYNTHETIC_SESSION_'+'s'*32
    authority=PreparationAuthority(queue,session_valid=lambda value:value==session)
    try:
        assert NativePreparationAdmission.available()
        with DisposablePreparationSession(headless=False,channel='chrome') as owner:
            admission=NativePreparationAdmission()
            assert admission.admit(owner) is True
            assert admission._receipt['native_proxy_blocked'] is True
            assert admission._receipt['protocols']==['http','https','ws','wss']
            assert owner.context.pages==[]
            classifier=c.RetainedXHRClassifier(owner.context)
            routes=[]
            def route(r):
                if r.request.method=='GET' and r.request.url==base+'/':
                    response=owner.client.get(base+'/',max_redirects=0,max_retries=0)
                    try:r.fulfill(response=response)
                    finally:response.dispose()
                elif r.request.method=='POST' and r.request.url==base+'/final':routes.append(r)
                else:r.abort()
            owner.context.route('**/*',route)
            page=owner.context.new_page();page.goto(base+'/');page.wait_for_timeout(30)
            resources_sha=hashlib.sha256(html.encode()).hexdigest()
            binding=owner.observe(page,resources_sha=resources_sha).public_binding()
            identity=owner.identity
            owner.transport.seal();owner.transport.require_sealed()
            # Admission completes before the full-duration routine offer exists.
            offer=authority.issue(task['task_id'],task['revision'],session,binding,['0','8'])
            assert offer['expires_in_seconds']==120 and offer['submit_capability'] is False
            permit=authority.consume(offer['nonce'],session,offer['scope_sha'],binding,approve_transmission=True)
            journal=PreparationJournal(authority,permit,session,binding)
            def routine_guard(plan_sha):
                assert admission.admit(owner)
                current=owner.observe(page,resources_sha=resources_sha).public_binding()
                authority.guard(permit,session,current,plan_sha=plan_sha)
            result=runner_module.PreparationKernel(page,owner.transport,routine_guard,journal).run(permit['plan'])
            assert result['status']=='PREPARED_UNVERIFIED' and result['submit_capability'] is False
            journal.complete()
            authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=False,session=session,
                             browser_binding=binding,plan_sha=permit['plan_sha'])
            classifier.arm()
            final_fields=[{'id':int(field.field_id),'type':int(field.data_type),'must':field.required_marker,
                           'val':'SYNTHETIC_NATIVE_APPLICANT' if field.field_id=='0' else c.CONTRACT_ROLE if field.field_id=='8' else 'SYNTHETIC_PRIVATE_VALUE'}
                          for field in c.FIELDS if field.field_id.isdigit()]
            value=body(rows=final_fields);page.evaluate(SEND,{'url':base+'/final','body':value})
            for _ in range(100):
                if routes:break
                page.wait_for_timeout(10)
            assert len(routes)==1 and received==[]
            request=routes[0].request;metadata=classifier.classify(request)
            cdp=owner.context.new_cdp_session(page);cdp.send('Page.enable')
            frame_id=cdp.send('Page.getFrameTree')['frameTree']['frame']['id']
            def final_observe():
                epoch=classifier.require_exact(request)
                assert admission.admit(owner)
                current=owner.observe(page,resources_sha=resources_sha).public_binding()
                observed_frame=cdp.send('Page.getFrameTree')['frameTree']['frame']['id']
                return {'browser':current,'frame_id':observed_frame,'change_epoch':epoch}
            final=f.PreparationFinalJournal(authority,permit,session,final_observe)
            assert final.binding=={'browser':binding,'frame_id':frame_id,'change_epoch':metadata['change_epoch']}
            bridge=h.OpaqueHumanRequest(page=page,client=owner.client,binding=final.binding,
                guard=final.guard,consume=final.consume,record=final.record)
            def opening(event):
                opened.append({key:event.get(key) for key in ('type','frameId','hasBrowserHandler')})
                bridge.dialog_opened(event)
            def closing(event):
                closed.append({key:event.get(key) for key in ('result','frameId')})
                bridge.dialog_closed(event)
            cdp.on('Page.javascriptDialogOpening',opening);cdp.on('Page.javascriptDialogClosed',closing)
            page.on('dialog',bridge.dialog_handle)
            try:
                bridge.hold(routes[0],metadata);assert bridge.drain()=={'status':'HELD'}
                assert received==[] and rows(queue)==[]
                bridge.present()
                for _ in range(100):
                    if bridge._dialog is not None and opened:break
                    page.wait_for_timeout(20)
                assert opened==[{'type':'confirm','frameId':frame_id,'hasBrowserHandler':True}]
                assert bridge._state=='DIALOG_OPEN' and received==[] and rows(queue)==[]
                # Only this isolated fixture drives the real native dialog.
                # Product code has no accept method; physical intent is unproved.
                if decision=='accept':bridge._dialog.accept()
                else:bridge._dialog.dismiss()
                for _ in range(100):
                    if closed:break
                    page.wait_for_timeout(20)
                assert closed==[{'result':decision=='accept','frameId':frame_id}]
                outcome=bridge.drain();bridge.drain()
                assert outcome['status']==('RETURNED_UNVERIFIED' if decision=='accept' else 'CANCELLED')
                assert received==([(value.encode(),'session=SYNTHETIC_NATIVE_SESSION','SYNTHETIC_TOKEN')] if decision=='accept' else [])
                assert len(rows(queue))==(1 if decision=='accept' else 0)
                if decision=='accept':
                    assert rows(queue)[0]['outcome']=='RETURNED_UNVERIFIED'
                    with pytest.raises(PreparationConflict):f.PreparationFinalJournal(authority,permit,session,final_observe)
                assert 'SYNTHETIC_NATIVE_APPLICANT' not in json.dumps(rows(queue))
                print('NATIVE_CLASSIFIED_HANDOFF '+json.dumps({'decision':decision,'proxy_selfcheck':True,
                    'native_dialog_events':True,'owned_process_binding':True,'final_post_count':len(received),
                    'durable_final_slots':len(rows(queue)),'physical_human_proven':False}),flush=True)
            finally:
                bridge.cancel();cdp.detach()
        assert identity.absence()=={'status':'ABSENT','process_sha':identity.process_sha}
    finally:
        server.shutdown();server.server_close();thread.join()
        if permit is not None and identity is not None and identity.absence().get('status')=='ABSENT':
            authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
