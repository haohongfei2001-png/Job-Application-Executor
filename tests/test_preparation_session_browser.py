"""Synthetic UI only: selected-field preflight, cancellation and stale results."""
import copy
import json
import os
from pathlib import Path
import time
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
import pytest
from playwright.sync_api import expect

from test_preparation_review_browser import open_review,payload,CANARY
from test_dashboard_diagnostics_browser import preparation_ui
from executor.preparation.qiyunfang import CONTRACT_URL


def install(page,observed,*,hold=False,changed=None):
    pending=[];calls=[];state={'status':'OFFERED'}
    def result(data):
        plan=[{'field_id':item['field_id'],'value':item['value']} for item in payload()['proposals'] if item['field_id'] in data['selected_ids']]
        value={'mode':'READ_ONLY_PUBLIC_PREFLIGHT','status':'EMPTY_FORM_VERIFIED','request_id':data['request_id'],
            'task_id':data['task_id'],'task_revision':data['expected_revision'],'source_url':CONTRACT_URL,
            'profile_version':data['profile_version'],'resume_version':data['resume_version'],'plan':plan,
            'expires_in_seconds':120,'capabilities':{'live_write':False,'submit':False,'account_verified':False,'server_draft_verified':False}}
        if changed=='profile':value['profile_version']='b'*64
        if changed=='plan':value['plan'][0]['value']='CHANGED_PRIVATE_VALUE'
        if changed=='capability':value['capabilities']['live_write']=True
        return value
    def route(route):
        action=route.request.url.rsplit('/',1)[-1];data=route.request.post_data_json
        calls.append((action,data));observed['requests'].append(('POST',route.request.url,route.request.post_data))
        if action=='open':
            if hold:pending.append((route,data));return
            response=result(data)
        elif action=='status':response={**state,'live_write_available':False,'submit_capability':False}
        else:response={'status':'CANCELLATION_REQUESTED','context_closed':False,'submit_capability':False}
        route.fulfill(status=200,content_type='application/json',body=json.dumps(response))
    page.route('**/ui/api/preparation-session/*',route)
    open_review(page,observed)
    expect(page.locator('#preparation-site-check')).to_be_disabled()
    page.get_by_role('checkbox',name='选择核对姓名',exact=True).check()
    page.locator('#preparation-site-check').click()
    return pending,calls,state,result


def pump_until(page,predicate):
    deadline=time.monotonic()+3
    while not predicate() and time.monotonic()<deadline:page.wait_for_timeout(25)
    assert predicate()


def test_selected_preflight_uses_only_local_cookie_route_and_clears_on_hide(preparation_ui):
    page,observed=preparation_ui;_,calls,_,_=install(page,observed)
    expect(page.locator('#preparation-site-status')).to_contain_text('已通过只读核对')
    assert calls[0][0]=='open' and calls[0][1]['selected_ids']==['0']
    assert calls[0][1]['profile_version']=='a'*64 and CANARY not in json.dumps(calls[0][1])
    assert 'approve_transmission' not in calls[0][1]
    if os.environ.get('JAE_UI_SCREENSHOT_DIR'):
        directory=Path(os.environ['JAE_UI_SCREENSHOT_DIR']);directory.mkdir(parents=True,exist_ok=True)
        page.locator('#preparation-site-status').scroll_into_view_if_needed()
        page.screenshot(path=str(directory/'preparation-site-check.png'))
    page.locator('#preparation-review-hide').click()
    pump_until(page,lambda:any(action=='cancel' for action,_ in calls))
    assert CANARY not in page.content() and observed['external']==[] and observed['errors']==[]
    assert page.evaluate('localStorage.length+sessionStorage.length')==0


@pytest.mark.parametrize('interrupt',['close','escape','hide','task_change','pagehide'])
def test_late_preflight_reply_never_revives_closed_or_rebound_private_view(preparation_ui,interrupt):
    page,observed=preparation_ui;pending,calls,_,result=install(page,observed,hold=True)
    pump_until(page,lambda:len(pending)==1)
    page.evaluate("document.getElementById('preparation-site-check').dispatchEvent(new MouseEvent('click'))")
    assert len(pending)==1
    if interrupt=='close':page.locator('#preparation-close').click()
    elif interrupt=='escape':page.locator('#preparation-dialog').press('Escape')
    elif interrupt=='hide':page.locator('#preparation-review-hide').click()
    elif interrupt=='task_change':page.evaluate("openTaskPreparation('prep-b',9)")
    else:page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    pump_until(page,lambda:any(action=='cancel' for action,_ in calls))
    route,data=pending[0];route.fulfill(status=200,content_type='application/json',body=json.dumps(result(data)))
    page.wait_for_timeout(100)
    assert CANARY not in page.content()
    assert '已通过只读核对' not in page.locator('#preparation-site-status').inner_text()
    assert observed['external']==[] and observed['errors']==[]


@pytest.mark.parametrize('changed',['profile','plan','capability'])
def test_changed_plan_or_version_never_leaves_old_values_or_success_visible(preparation_ui,changed):
    page,observed=preparation_ui;_,calls,_,_=install(page,observed,changed=changed)
    expect(page.locator('#preparation-review-status')).to_contain_text('已隐藏个人值')
    assert CANARY not in page.content() and 'CHANGED_PRIVATE_VALUE' not in page.content()
    pump_until(page,lambda:any(action=='cancel' for action,_ in calls))
    assert observed['errors']==[]


def test_backend_closure_is_polled_and_hides_a_previously_verified_view(preparation_ui):
    page,observed=preparation_ui;_,_,state,_=install(page,observed)
    expect(page.locator('#preparation-site-status')).to_contain_text('已通过只读核对')
    state['status']='UNKNOWN_OUTCOME'
    expect(page.locator('#preparation-review-status')).to_contain_text('状态无法确认',timeout=4000)
    assert CANARY not in page.content() and observed['errors']==[]


def test_real_back_forward_clears_private_values_and_retires_pending_check(preparation_ui):
    """Use an independent HTTP recipient for unload/keepalive delivery.

    Page-bound DevTools routing can lose an unload request with its old frame;
    server receipt, not a surviving route callback, is the cancellation oracle.
    """
    from executor.autonomy.dashboard import DASHBOARD_HTML
    page,observed=preparation_ui
    page.context.unroute('**/*')
    opened=threading.Event();cancelled=threading.Event();release=threading.Event()
    requests=[];source_visits=[];lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,value,*,html=False):
            body=(value if html else json.dumps(value)).encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Set-Cookie','synthetic_session=local_fixture; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            path=urlsplit(self.path).path
            if path in {'/','/synthetic-prior'}:
                with lock:source_visits.append(path)
                self.reply(DASHBOARD_HTML if path=='/' else '<p>Known local prior page</p>',html=True)
            elif path=='/ui/api/task-preparation':self.reply(observed['payload'])
            elif path=='/ui/api/preparation-review':self.reply(payload())
            else:self.reply({'tasks':observed['tasks'],'ready_for_live_e2e':False})
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            action=self.path.rsplit('/',1)[-1]
            with lock:requests.append((action,data,self.headers.get('Origin'),self.headers.get('Cookie')))
            if action=='open':
                opened.set();release.wait(10)
                self.reply({'mode':'READ_ONLY_PUBLIC_PREFLIGHT','status':'EMPTY_FORM_VERIFIED',
                    'request_id':data['request_id'],'task_id':data['task_id'],'task_revision':data['expected_revision'],
                    'source_url':CONTRACT_URL,'profile_version':data['profile_version'],'resume_version':None,
                    'plan':[{'field_id':'0','value':CANARY}], 'expires_in_seconds':120,
                    'capabilities':{'live_write':False,'submit':False,'account_verified':False,'server_draft_verified':False}})
            elif action=='cancel':
                cancelled.set();self.reply({'status':'CANCELLATION_REQUESTED','context_closed':False,'submit_capability':False})
            else:self.reply({'status':'OFFERED','live_write_available':False,'submit_capability':False})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_address[1]);prior=origin+'/synthetic-prior'
    try:
        page.goto(prior);page.goto(origin+'/')
        expect(page.locator('[data-task-preparation]')).to_have_count(2)
        page.locator('[data-task-preparation][data-task="prep-a"]').click()
        page.locator('#preparation-review-open').click()
        expect(page.locator('#preparation-review-fields')).to_contain_text(CANARY)
        page.get_by_role('checkbox',name='选择核对姓名',exact=True).check()
        page.locator('#preparation-site-check').click()
        assert opened.wait(3)
        page.go_back();expect(page).to_have_url(prior)
        assert cancelled.wait(3),'independent loopback recipient did not receive unload cancellation'
        assert CANARY not in page.content()
        release.set()
        page.go_forward();expect(page).to_have_url(origin+'/')
        expect(page.locator('[data-task-preparation]')).to_have_count(2)
        assert CANARY not in page.content()
        expect(page.locator('#preparation-review')).to_be_hidden()
        assert page.locator('#preparation-site-status').inner_text()==''
        with lock:
            assert [row[0] for row in requests]==['open','cancel']
            assert requests[0][1]['request_id']==requests[1][1]['request_id']
            assert all(row[2]==origin and row[3]=='synthetic_session=local_fixture' for row in requests)
            assert CANARY not in json.dumps(requests)
            assert '/' in source_visits and '/synthetic-prior' in source_visits
        assert observed['errors']==[]
    finally:
        release.set();server.shutdown();server.server_close();thread.join(3)
