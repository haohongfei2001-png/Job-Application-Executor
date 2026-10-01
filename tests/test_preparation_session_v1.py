"""Private read-only preflight surface: no approval nonce or filling endpoint."""
import json
import hashlib
import threading
from types import SimpleNamespace
import pytest

from executor.autonomy.preparation_session import PrivatePreparationSessions
from executor.preparation.authority import PreparationConflict
from executor.autonomy.task_preparation import CONTRACT_URL,CONTRACT_COMPANY,CONTRACT_ROLE
from test_task_preparation_v1 import local_task,_snapshot

SESSION='s'*40


@pytest.fixture
def setup(local_task):
    q,task,profile=local_task;calls=[];gate=threading.Event();blocked=[False]
    class Controller:
        def __init__(self):self.state='IDLE'
        def status(self):return {'status':self.state,'live_write_available':True,'resume_status':'INTERNAL_ONLY','submit_capability':False}
        def open(self,tid,revision,session,selected):
            calls.append(('open',tid,selected));self.state='OFFERED'
            if blocked[0]:assert gate.wait(2)
            return {'nonce':'NEVER_PROJECT_NONCE','scope_sha':'NEVER_PROJECT_SCOPE','plan':[{'field_id':'0','value':'PRIVATE_SYNTHETIC'}],
                'recipient_url':CONTRACT_URL,'company':CONTRACT_COMPANY,'role':CONTRACT_ROLE,'expires_in_seconds':120,'live_write_available':True,'profile_version':hashlib.sha256(profile.read_bytes()).hexdigest(),'resume_version':None}
        def cancel(self,session):calls.append(('cancel',));self.state='CLOSED';return {'status':'CANCELLATION_REQUESTED'}
        def shutdown(self,timeout):calls.append(('shutdown',timeout));self.state='CLOSED'
    sessions=PrivatePreparationSessions(q,lambda s:s==SESSION,lambda:False,factory=lambda *_:Controller())
    data={'request_id':'a'*32,'task_id':task['task_id'],'expected_revision':task['revision'],'selected_ids':['0'],'profile_version':hashlib.sha256(profile.read_bytes()).hexdigest(),'resume_version':None}
    return q,sessions,data,calls,gate,blocked


def close_data(data):return {key:value for key,value in data.items() if key in {'request_id','task_id','expected_revision'}}


def test_private_surface_returns_review_without_approval_or_write_capability(setup):
    q,sessions,data,calls,*_=setup;before=_snapshot(q)
    result=sessions.open(data,SESSION)
    assert result['status']=='EMPTY_FORM_VERIFIED' and all(value is False for value in result['capabilities'].values())
    assert 'PRIVATE_SYNTHETIC' in json.dumps(result) and 'NEVER_PROJECT' not in json.dumps(result)
    assert sessions.status(close_data(data),SESSION)['live_write_available'] is False
    assert 'resume_status' not in sessions.status(close_data(data),SESSION)
    sessions.cancel(close_data(data),SESSION)
    assert _snapshot(q)==before and [row[0] for row in calls]==['open','shutdown']


def test_cancel_before_delayed_open_is_a_tombstone_not_a_noop(setup):
    _,sessions,data,calls,*_=setup
    sessions.cancel(close_data(data),SESSION)
    with pytest.raises(PreparationConflict):sessions.open(data,SESSION)
    assert calls==[]


def test_cancel_during_open_cannot_publish_a_late_private_offer(setup):
    _,sessions,data,calls,gate,blocked=setup;blocked[0]=True;outcomes=[]
    def opening():
        try:sessions.open(data,SESSION)
        except PreparationConflict:outcomes.append('refused')
    thread=threading.Thread(target=opening);thread.start()
    import time
    deadline=time.monotonic()+2
    while not calls and time.monotonic()<deadline:time.sleep(.01)
    sessions.cancel(close_data(data),SESSION);gate.set();thread.join(2)
    assert outcomes==['refused'] and not thread.is_alive()


@pytest.mark.parametrize('change',['extra_flag','protected_field','duplicate_field','stale_revision','wrong_session','wrong_target'])
def test_malformed_unapproved_or_rebound_open_never_creates_browser(setup,change):
    _,sessions,data,calls,*_=setup
    if change=='extra_flag':data['approve_transmission']=True
    elif change=='protected_field':data['selected_ids']=['12']
    elif change=='duplicate_field':data['selected_ids']=['0','0']
    elif change=='stale_revision':data['expected_revision']+=1
    elif change=='wrong_target':data['task_id']='missing'
    with pytest.raises((ValueError,RuntimeError,KeyError)):
        sessions.open(data,'x'*40 if change=='wrong_session' else SESSION)
    assert calls==[]


def test_other_session_or_wrong_binding_cannot_cancel_active_preview(setup):
    _,sessions,data,calls,*_=setup;sessions.open(data,SESSION)
    with pytest.raises(PreparationConflict):sessions.cancel(close_data(data),'x'*40)
    wrong=close_data(data);wrong['expected_revision']+=1
    with pytest.raises(PreparationConflict):sessions.cancel(wrong,SESSION)
    assert len(calls)==1 and not sessions.cancelled


@pytest.fixture
def api(local_task):
    import http.client
    from executor.autonomy.supervisor import Supervisor,create_server
    q,task,_=local_task;supervisor=Supervisor(q,token='t'*40);calls=[]
    def action(data,session):calls.append((data,session));return {'private':'SYNTHETIC_PRIVATE_HTTP','submit_capability':False}
    supervisor.preparation_sessions=SimpleNamespace(open=action,cancel=action,status=action)
    server=create_server(supervisor,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    port=server.server_address[1];cookie=supervisor.consume_ui_ticket(supervisor.issue_ui_ticket())
    def request(action='open',*,authenticated=True,bearer=False,origin=True,path=None,raw=None):
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
        headers={'Content-Type':'application/json'}
        if authenticated:headers['Cookie']='application_executor_session='+cookie
        if bearer:headers['Authorization']='Bearer '+supervisor.token
        if origin is not None:headers['Origin']=f'http://127.0.0.1:{port}' if origin is True else origin
        try:
            conn.request('POST',path or '/ui/api/preparation-session/'+action,body=raw or '{}',headers=headers)
            response=conn.getresponse();return response.status,json.loads(response.read()),dict(response.getheaders())
        finally:conn.close()
    try:yield request,calls
    finally:server.shutdown();server.server_close();thread.join(3)


def test_private_preflight_api_requires_cookie_exact_origin_and_has_no_generic_or_approve_route(api):
    request,calls=api
    assert request(authenticated=False)[0]==401
    assert request(authenticated=False,bearer=True)[0]==401
    assert request(origin=None)[0]==403 and request(origin='https://other.example')[0]==403
    assert not calls
    for action in ['open','cancel','status']:
        code,data,headers=request(action)
        assert code==200 and headers['Cache-Control']=='no-store' and headers['Referrer-Policy']=='no-referrer'
    assert len(calls)==3
    assert request('approve')[0]==404
    assert request(path='/v1/preparation-session/open',authenticated=False,bearer=True,origin=None)[0]==404
    assert request(raw='{"key":1,"key":2}')[0]==400
    assert request(raw='{"oversized":"'+'x'*5000+'"}')[0]==400
    assert len(calls)==3


def test_profile_drift_before_open_refuses_browser_creation(setup):
    q,sessions,data,calls,*_=setup
    from pathlib import Path
    Path(q.get(data['task_id'])['spec']['profile_ref']).write_text('{"fields":{}}')
    with pytest.raises(PreparationConflict):sessions.open(data,SESSION)
    assert calls==[]


def test_mutation_fence_revokes_existing_controller_liveness(local_task):
    q,task,profile=local_task;fenced=[False];captured=[]
    class Controller:
        def status(self):return {'status':'OFFERED'}
        def open(self,*_):
            return {'plan':[],'recipient_url':CONTRACT_URL,'company':CONTRACT_COMPANY,'role':CONTRACT_ROLE,'expires_in_seconds':120,
                    'profile_version':hashlib.sha256(profile.read_bytes()).hexdigest(),'resume_version':None}
        def shutdown(self,timeout):captured.append(('shutdown',timeout))
    def factory(_,valid):captured.append(valid);return Controller()
    sessions=PrivatePreparationSessions(q,lambda s:s==SESSION,lambda:fenced[0],factory=factory)
    data={'request_id':'a'*32,'task_id':task['task_id'],'expected_revision':task['revision'],'selected_ids':['0'],
          'profile_version':hashlib.sha256(profile.read_bytes()).hexdigest(),'resume_version':None}
    sessions.open(data,SESSION);assert captured[0](SESSION) is True
    fenced[0]=True;assert captured[0](SESSION) is False
    sessions.revoke_all();assert captured[-1]==('shutdown',0)


def test_service_retirement_is_permanent_before_a_new_open(setup):
    _,sessions,data,calls,*_=setup;sessions.revoke_all()
    with pytest.raises(PreparationConflict):sessions.open(data,SESSION)
    assert sessions.retired and calls==[]


def test_service_retirement_during_profile_review_prevents_browser_creation(setup,monkeypatch):
    from executor.autonomy import preparation_session as module
    _,sessions,data,calls,*_=setup;original=module.review_preparation
    def review(*args):
        result=original(*args);sessions.revoke_all();return result
    monkeypatch.setattr(module,'review_preparation',review)
    with pytest.raises(PreparationConflict):sessions.open(data,SESSION)
    assert calls==[]


def test_retirement_during_final_offer_return_never_publishes_stale_verification(setup):
    _,sessions,data,calls,*_=setup;factory=sessions.factory
    def replaced(*args):
        controller=factory(*args);original=controller.open
        def opening(*a):
            result=original(*a);sessions.revoke_all();return result
        controller.open=opening;return controller
    sessions.factory=replaced
    with pytest.raises(PreparationConflict):sessions.open(data,SESSION)
    assert sessions.retired and any(call[0]=='shutdown' for call in calls)


def test_cancel_after_registration_before_controller_open_never_creates_browser(local_task):
    from executor.preparation.controller import PreparationController
    q,task,profile=local_task;registered=threading.Event();release=threading.Event();outcomes=[];owners=[]
    def owner():owners.append('created');raise AssertionError('cancelled browser must not start')
    def factory(queue,valid):
        controller=PreparationController(queue,valid,owner_factory=owner)
        original=controller.open
        def delayed(*args):
            registered.set();assert release.wait(2)
            return original(*args)
        controller.open=delayed
        return controller
    sessions=PrivatePreparationSessions(q,lambda value:value==SESSION,lambda:False,factory=factory)
    data={'request_id':'a'*32,'task_id':task['task_id'],'expected_revision':task['revision'],'selected_ids':['0'],
          'profile_version':hashlib.sha256(profile.read_bytes()).hexdigest(),'resume_version':None}
    def opening():
        try:sessions.open(data,SESSION)
        except PreparationConflict:outcomes.append('refused')
    thread=threading.Thread(target=opening);thread.start();assert registered.wait(2)
    result=sessions.cancel(close_data(data),SESSION)
    assert result['status']=='CANCELLATION_REQUESTED' and not result['context_closed']
    release.set();thread.join(2)
    assert not thread.is_alive() and outcomes==['refused'] and owners==[]
    assert sessions.active['controller']._thread is None
    assert sessions.active['controller'].status()['status']=='CLOSED'

    # Proven never-started cancellation does not strand future read-only checks.
    sessions.factory=lambda *_:SimpleNamespace(
        open=lambda *_:{'plan':[],'recipient_url':CONTRACT_URL,'company':CONTRACT_COMPANY,'role':CONTRACT_ROLE,
                        'expires_in_seconds':120,'profile_version':data['profile_version'],'resume_version':None},
        status=lambda:{'status':'OFFERED'},shutdown=lambda **_:None)
    replacement=dict(data,request_id='b'*32)
    assert sessions.open(replacement,SESSION)['status']=='EMPTY_FORM_VERIFIED'
    assert owners==[]


def test_retirement_wait_requires_proven_cleanup_outside_manager_lock(setup):
    _,sessions,data,*_=setup;sessions.open(data,SESSION);controller=sessions.active['controller']
    with pytest.raises(PreparationConflict):sessions.await_retired()
    states=[None,False,True];seen=[]
    def shutdown(timeout):
        assert not sessions.lock._is_owned()
        seen.append(timeout);return states.pop(0)
    controller.shutdown=shutdown
    sessions.revoke_all()
    assert sessions.await_retired(timeout=.01) is False
    assert sessions.await_retired(timeout=.01) is True
    assert seen==[0,.01,.01]


def test_no_owner_retirement_has_nothing_to_wait_for(setup):
    _,sessions,*_=setup;sessions.revoke_all();assert sessions.await_retired()


def test_read_only_status_projects_no_internal_upload_offer_or_capability(setup):
    _,sessions,data,*_=setup;sessions.open(data,SESSION)
    sessions.active['controller'].status=lambda:{'status':'OFFERED','field_count':3,'remaining_seconds':20,
        'resume_status':'OFFERED','resume':{'byte_count':1900},'nonce':'NEVER_PROJECT',
        'upload_available':True,'live_write_available':True,'submit_capability':True}
    assert sessions.status(close_data(data),SESSION)=={'status':'OFFERED','field_count':3,'remaining_seconds':20,
        'live_write_available':False,'submit_capability':False}
