"""Fake emitter lifecycle tests; actual synchronous browser proof is separate."""
import copy
from types import SimpleNamespace
import pytest
from executor.preparation.authority import PreparationConflict
from executor.preparation.resume_flow import ResumeUploadFlow
from test_preparation_resume_journal_v1 import ready,resume,fixture,rows
from test_preparation_resume_transport_v1 import Route
from test_preparation_authority_v1 import SESSION,BROWSER


class Emitter:
    def __init__(self):self.handlers={}
    def on(self,name,callback):self.handlers.setdefault(name,[]).append(callback)
    def emit(self,name,*args):
        for callback in self.handlers.get(name,[]):callback(*args)


@pytest.fixture
def owned(ready):
    ((q,task,_,authority,_),permit,_,_,_),_,_,_=ready
    effects=[];context=Emitter();browser=Emitter();page=Emitter();page.context=context;page.main_frame='owned-frame'
    context.pages=[page];context.routes=[];context.route=lambda pattern,handler:context.routes.append((pattern,handler))
    def selected(payload,**options):
        effects.append(('selection',payload))
        handler=context.routes[-1][1]
        for route in [Route(),Route(),Route(True)]:handler(route)
    page.locator=lambda _:SimpleNamespace(set_input_files=selected)
    page.wait_for_timeout=lambda _:None
    def post(*args,**kwargs):
        effects.append(('post',args,kwargs))
        return SimpleNamespace(status=200,body=lambda:b'{"success":true,"size":0}',dispose=lambda:None)
    owner=SimpleNamespace(context=context,browser=browser,identity=SimpleNamespace(verify_os=lambda:BROWSER['process_sha']),client=SimpleNamespace(post=post))
    binding=copy.deepcopy(BROWSER)
    flow=SimpleNamespace(state='PREPARED_UNVERIFIED',permit=permit,authority=authority,session=SESSION,page=page,owner=owner,
        _owner=lambda:None,review_fence=lambda:True,_binding=lambda:copy.deepcopy(binding),_still_authorized=lambda:True)
    def close():
        if flow.state!='CLOSED':
            authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True);flow.state='CLOSED';context.emit('close')
        return {'context_closed':True}
    flow.close=close
    upload=ResumeUploadFlow(flow)
    try:yield q,flow,upload,effects,binding
    finally:upload._retire();flow.close()


def test_mock_owned_stage_requires_exact_offer_and_retires_private_bytes(owned):
    q,flow,upload,effects,_=owned;offer=upload.private_offer()
    result=upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
    assert result['status']=='RETURNED_UNVERIFIED' and upload.material._data is None
    assert [event[0] for event in effects]==['selection','post','post','post']
    assert rows(q)[0]['outcome']=='RETURNED_UNVERIFIED'
    with pytest.raises(PreparationConflict):upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)


@pytest.mark.parametrize('event',['navigation','page_close','page_crash','context_close','popup','browser_disconnect','root'])
def test_owner_lifecycle_or_binding_drift_before_approval_has_zero_selection_or_send(owned,event):
    q,flow,upload,effects,binding=owned;offer=upload.private_offer()
    if event=='navigation':flow.page.emit('framenavigated',flow.page.main_frame)
    elif event=='page_close':flow.page.emit('close')
    elif event=='page_crash':flow.page.emit('crash')
    elif event=='context_close':flow.owner.context.emit('close')
    elif event=='popup':flow.owner.context.emit('page',object())
    elif event=='browser_disconnect':flow.owner.browser.emit('disconnected')
    else:binding['root_sha']='0'*64
    with pytest.raises(PreparationConflict):upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
    assert effects==[] and rows(q)==[]


@pytest.mark.parametrize('event',['navigation','close','cancel'])
def test_revocation_after_stage_commit_before_send_consumes_but_never_sends(owned,event):
    q,flow,upload,effects,_=owned;offer=upload.private_offer();original=upload.journal.begin_stage
    def before_send(name):
        stage=original(name)
        if event=='navigation':flow.page.emit('framenavigated',flow.page.main_frame)
        elif event=='close':flow.owner.context.emit('close')
        else:upload._revoked.set()
        return stage
    upload.journal.begin_stage=before_send
    with pytest.raises(PreparationConflict):upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
    assert [event[0] for event in effects]==['selection']
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME' and flow.state=='CLOSED'


def test_revocation_in_terminal_receipt_callback_cannot_publish_clean_result(owned):
    q,flow,upload,effects,_=owned;offer=upload.private_offer();original=upload.journal.finish
    def finish(outcome):
        result=original(outcome);flow.page.emit('close');return result
    upload.journal.finish=finish
    with pytest.raises(PreparationConflict):upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
    assert len([event for event in effects if event[0]=='post'])==3
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME' and upload.material._data is None


@pytest.mark.parametrize('point',['route_registration','selection'])
def test_registration_or_native_selection_exception_remains_consumed_unknown(owned,point):
    q,flow,upload,effects,_=owned;offer=upload.private_offer()
    def fail(*args,**kwargs):raise OSError('synthetic native result uncertainty')
    if point=='route_registration':flow.owner.context.route=fail
    else:flow.page.locator=lambda _:SimpleNamespace(set_input_files=fail)
    with pytest.raises(PreparationConflict):upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
    row=rows(q)[0]
    assert row['outcome']=='UNKNOWN_OUTCOME'
    assert row['selection_attempted']==(point=='selection')
    assert effects==[] and flow.state=='CLOSED' and upload.material._data is None
