"""Private native surface contracts; all applicant material is synthetic."""
import pytest
from executor.autonomy.preparation_session import _native_factory
from executor.preparation.native_admission import NativePreparationAdmission, NativeAdmissionUnavailable
from executor.preparation.authority import PreparationConflict
from test_preparation_session_v1 import setup, close_data, SESSION
from test_task_preparation_v1 import local_task


def test_default_runtime_is_not_admitted_or_launched():
    assert NativePreparationAdmission.available() is False
    with pytest.raises(NativeAdmissionUnavailable):
        _native_factory(None, lambda _: True)


def test_read_only_owner_cannot_be_upgraded_by_approval_route(setup):
    _, sessions, data, calls, *_ = setup
    sessions.open(data, SESSION)
    consent = {**close_data(data), 'nonce': 'n'*40, 'scope_sha': 'a'*64, 'approve_transmission': True}
    with pytest.raises(PreparationConflict): sessions.approve_fill(consent, SESSION)
    with pytest.raises(PreparationConflict): sessions.review_resume(close_data(data), SESSION)
    assert [row[0] for row in calls] == ['open']


def native(setup):
    _, sessions, data, calls, *_ = setup
    factory = sessions.factory
    def make(*args):
        owner = factory(*args)
        original_open = owner.open
        def opening(*values):
            result = original_open(*values)
            return {**result, 'nonce':'n'*40, 'scope_sha':'a'*64}
        owner.open = opening
        def approve(*args, **kwargs):
            calls.append(('fill', args, kwargs))
            return {'status':'PREPARED_UNVERIFIED','field_count':1,'task_revision':data['expected_revision']+1,'submit_capability':False}
        owner.approve = approve
        return owner
    sessions.native_factory = make
    return sessions, data, calls


def test_native_offer_separates_review_from_explicit_fill(setup):
    sessions, data, calls = native(setup)
    result = sessions.open_native(data, SESSION)
    assert result['mode']=='PRIVATE_NATIVE_FILL_OFFER'
    assert result['nonce']=='n'*40 and result['submit_capability'] is False
    assert [row[0] for row in calls]==['open']
    consent={**close_data(data),'nonce':result['nonce'],'scope_sha':result['scope_sha'],'approve_transmission':True}
    result=sessions.approve_fill(consent,SESSION)
    assert result=={'status':'PREPARED_UNVERIFIED','field_count':1,'task_revision':data['expected_revision']+1,'server_draft_verified':False,'submit_capability':False}
    sessions.cancel(close_data(data),SESSION)
    with pytest.raises(PreparationConflict):sessions.approve_fill(consent,SESSION)


@pytest.mark.parametrize('change',['missing','false','extra','wrong_session','wrong_task','wrong_revision'])
def test_malformed_native_consent_never_reaches_filler(setup,change):
    sessions,data,calls=native(setup)
    offer=sessions.open_native(data,SESSION)
    consent={**close_data(data),'nonce':offer['nonce'],'scope_sha':offer['scope_sha'],'approve_transmission':True}
    if change=='missing':consent.pop('approve_transmission')
    if change=='false':consent['approve_transmission']=False
    if change=='extra':consent['submit']=True
    if change=='wrong_task':consent['task_id']='wrong'
    if change=='wrong_revision':consent['expected_revision']+=1
    with pytest.raises(PreparationConflict):sessions.approve_fill(consent,'x'*40 if change=='wrong_session' else SESSION)
    assert [row[0] for row in calls]==['open']


def test_native_http_routes_require_private_cookie_exact_origin_and_strict_json(local_task):
    import http.client,json,threading
    from types import SimpleNamespace
    from executor.autonomy.supervisor import Supervisor,create_server
    q,_,_=local_task; supervisor=Supervisor(q,token='t'*40); calls=[]
    def action(data,session):calls.append(data);return {'submit_capability':False}
    names=['open_native','approve_fill','review_resume','approve_resume','native_status','cancel']
    supervisor.preparation_sessions=SimpleNamespace(**{name:action for name in names})
    server=create_server(supervisor,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    port=server.server_address[1];cookie=supervisor.consume_ui_ticket(supervisor.issue_ui_ticket())
    def request(action,*,origin=True,authenticated=True,raw='{}'):
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
        headers={'Content-Type':'application/json','Authorization':'Bearer '+supervisor.token}
        if authenticated:headers['Cookie']='application_executor_session='+cookie
        if origin:headers['Origin']=f'http://127.0.0.1:{port}'
        try:
            conn.request('POST','/ui/api/native-preparation/'+action,body=raw,headers=headers)
            response=conn.getresponse();body=json.loads(response.read());return response.status,body,dict(response.getheaders())
        finally:conn.close()
    try:
        for action in ['open','approve-fill','review-resume','approve-resume','status','cancel']:
            assert request(action,authenticated=False)[0]==401
            assert request(action,origin=False)[0]==403
        assert calls==[]
        assert request('approve-fill',raw='{"approve_transmission":true,"approve_transmission":false}')[0]==400
        assert calls==[]
        for action in ['open','approve-fill','review-resume','approve-resume','status','cancel']:
            code,body,headers=request(action)
            assert code==200 and body['submit_capability'] is False
            assert headers['Cache-Control']=='no-store' and headers['Referrer-Policy']=='no-referrer'
        assert len(calls)==6
        assert request('submit')[0]==404
    finally:server.shutdown();server.server_close();thread.join(3)
