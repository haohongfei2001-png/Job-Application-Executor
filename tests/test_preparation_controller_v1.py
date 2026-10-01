"""Private owner-thread/state tests; fake DOM cannot certify a native backend."""
import json
import threading
import time
from types import SimpleNamespace
import pytest

from executor.preparation.controller import PreparationController
from executor.preparation.authority import PreparationConflict
from test_preparation_authority_v1 import fixture,SESSION


@pytest.fixture
def setup(fixture):
    q,_,_,_,_=fixture;calls=[];entered=threading.Event();release=threading.Event();hold=[False];closed=[True]
    class Owner:
        def __init__(self):self.context=SimpleNamespace(pages=[SimpleNamespace(wait_for_timeout=lambda _:None)])
        def __enter__(self):calls.append(('enter',threading.get_ident()));return self
        def close(self):calls.append(('owner_close',threading.get_ident()));self.context=None;return closed[0]
    class Flow:
        def __init__(self,authority,owner,**kwargs):self.owner=owner;self.alive=kwargs['still_authorized'];calls.append(('flow',threading.get_ident()))
        def private_offer(self):return {'nonce':'private-nonce','scope_sha':'a'*64,'expires_in_seconds':120,'plan':[{'value':'PRIVATE_SYNTHETIC'}]}
        def _binding(self):
            if not self.alive():raise PreparationConflict()
            return {}
        def review_fence(self):return self._binding()
        def approve(self,nonce,scope,session,*,approve_transmission):
            assert nonce=='private-nonce' and scope=='a'*64 and session==SESSION and approve_transmission is True
            entered.set()
            if hold[0]:assert release.wait(2)
            self._binding();calls.append(('primitive',threading.get_ident()))
            return {'status':'PREPARED_UNVERIFIED','field_count':2,'submit_capability':False}
        def close(self):return {'context_closed':self.owner.close(),'reconciliation_required':not closed[0]}
    def factory(**kwargs):return PreparationController(q,lambda value:value==SESSION,owner_factory=Owner,flow_factory=Flow,**kwargs)
    return factory,calls,entered,release,hold,closed


def opened(factory,**kwargs):
    controller=factory(**kwargs);offer=controller.open('task',1,SESSION,['0']);return controller,offer


def test_default_admission_stays_closed_and_status_has_no_private_offer(setup):
    factory,calls,*_=setup;controller,offer=opened(factory)
    try:
        assert offer['live_write_available'] is False and 'PRIVATE_SYNTHETIC' in json.dumps(offer)
        assert 'PRIVATE_SYNTHETIC' not in json.dumps(controller.status()) and 'private-nonce' not in repr(controller)
        with pytest.raises(PreparationConflict):controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        assert not any(name=='primitive' for name,_ in calls)
    finally:assert controller.shutdown()
    assert controller.status()['status']=='CLOSED'


def test_only_owner_thread_performs_browser_and_fill_with_one_exact_approval(setup):
    factory,calls,*_=setup;controller,offer=opened(factory,write_admission=lambda _:True)
    try:
        result=controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        assert result['status']=='PREPARED_UNVERIFIED'
        with pytest.raises(PreparationConflict):controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        assert controller.status()['field_count']==2
    finally:assert controller.shutdown()
    owners={thread for _,thread in calls}
    assert len(owners)==1 and threading.get_ident() not in owners


def test_cancellation_during_approval_is_observed_before_next_primitive(setup):
    factory,calls,entered,release,hold,_=setup;hold[0]=True
    controller,offer=opened(factory,write_admission=lambda _:True);outcomes=[]
    def approve():
        try:controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        except PreparationConflict:outcomes.append('refused')
    caller=threading.Thread(target=approve);caller.start();assert entered.wait(2)
    assert controller.cancel(SESSION)['context_closed'] is False
    release.set();caller.join(2);assert not caller.is_alive() and outcomes==['refused']
    assert controller.shutdown() and not any(name=='primitive' for name,_ in calls)


def test_runtime_admission_is_rechecked_not_borrowed_from_offer(setup):
    factory,calls,*_=setup;valid=[True]
    controller,offer=opened(factory,write_admission=lambda _:valid[0]);valid[0]=False
    try:
        with pytest.raises(PreparationConflict):controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    finally:assert controller.shutdown()
    assert not any(name=='primitive' for name,_ in calls)


def test_uncertain_browser_disposal_does_not_report_closed(setup):
    factory,_,_,_,_,closed=setup;closed[0]=False
    controller,_=opened(factory);assert controller.shutdown() is False
    assert controller.status()['status']=='UNKNOWN_OUTCOME'


def test_shutdown_before_open_cannot_enqueue_into_a_dead_owner(setup):
    factory,calls,*_=setup;controller=factory();assert controller.shutdown()
    with pytest.raises(PreparationConflict):controller.open('task',1,SESSION,['0'])
    assert calls==[]


def test_offer_expiry_closes_without_approval_or_primitive(setup):
    factory,calls,*_=setup;now=[10.0]
    controller,_=opened(factory,clock=lambda:now[0]);now[0]=131
    deadline=time.monotonic()+2
    while controller.status()['status']!='CLOSED' and time.monotonic()<deadline:time.sleep(.01)
    assert controller.status()['status']=='CLOSED' and controller.shutdown()
    assert controller._alive() is False
    assert not any(name=='primitive' for name,_ in calls)


@pytest.mark.parametrize('closed',[False,True])
def test_failed_enter_keeps_owned_cleanup_evidence_and_never_retries_disposal(fixture,closed):
    q,*_=fixture;calls=[]
    class Owner:
        entry_cleanup_attempted=False;entry_cleanup_closed=False
        def __enter__(self):
            self.entry_cleanup_attempted=True;self.entry_cleanup_closed=closed
            calls.append('entry_cleanup');raise OSError('synthetic startup failure')
        def close(self):pytest.fail('uncertain entry cleanup retried')
    controller=PreparationController(q,lambda value:value==SESSION,owner_factory=Owner)
    with pytest.raises(PreparationConflict):controller.open('task',1,SESSION,['0'])
    assert controller.shutdown() is closed
    assert controller.status()['status']==('CLOSED' if closed else 'UNKNOWN_OUTCOME')
    assert calls==['entry_cleanup']


def test_unannotated_entry_failure_remains_unknown_without_second_close(fixture):
    q,*_=fixture;calls=[]
    class Owner:
        def __enter__(self):self.close();raise OSError('synthetic startup uncertainty')
        def close(self):calls.append('close');return False
    controller=PreparationController(q,lambda value:value==SESSION,owner_factory=Owner)
    with pytest.raises(PreparationConflict):controller.open('task',1,SESSION,['0'])
    assert controller.shutdown() is False and controller._owner is not None
    assert controller.status()['status']=='UNKNOWN_OUTCOME' and calls==['close']


def resume_controller(setup,**options):
    factory,calls,*_=setup;uploads=[]
    class Resume:
        def __init__(self,flow):
            self.flow=flow;self.retired=False;uploads.append(self)
            calls.append(('resume_create',threading.get_ident()))
        def private_offer(self):
            self.flow._binding();return {'nonce':'upload-private-nonce','scope_sha':'b'*64,
                'expires_in_seconds':120,'resume':{'resume_sha256':'c'*64,'byte_count':1900,'kind':'resume_docx'},
                'submit_capability':False}
        def approve(self,nonce,scope,*,approve_upload):
            assert nonce=='upload-private-nonce' and scope=='b'*64 and approve_upload is True
            self.flow._binding();calls.append(('upload',threading.get_ident()))
            return {'status':'RETURNED_UNVERIFIED','server_attachment_verified':False,'submit_capability':False}
        def _retire(self):self.retired=True;calls.append(('resume_retire',threading.get_ident()))
    controller,offer=opened(factory,write_admission=lambda _:True,resume_factory=Resume,**options)
    controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    return controller,uploads


def test_internal_upload_requires_separate_default_closed_admission(setup):
    controller,uploads=resume_controller(setup)
    with pytest.raises(PreparationConflict):controller.review_resume(SESSION)
    assert controller.shutdown() and uploads==[]
    assert not any(name=='upload' for name,_ in setup[1])


def test_internal_resume_review_and_single_approval_stay_on_owner_thread(setup):
    controller,uploads=resume_controller(setup,upload_admission=lambda _:True)
    manual_deadline=controller._review_deadline
    try:
        offer=controller.review_resume(SESSION)
        assert offer['resume']['kind']=='resume_docx' and controller.status()['resume_status']=='OFFERED'
        assert controller._deadline<=manual_deadline
        public=json.dumps(controller.status())
        assert all(secret not in public for secret in ['upload-private-nonce','c'*64,'1900','resume_docx'])
        with pytest.raises(PreparationConflict):controller.approve_resume(offer['nonce'],offer['scope_sha'],'wrong-session',approve_upload=True)
        with pytest.raises(PreparationConflict):controller.approve_resume(offer['nonce'],offer['scope_sha'],SESSION,approve_upload=False)
        result=controller.approve_resume(offer['nonce'],offer['scope_sha'],SESSION,approve_upload=True)
        assert result['status']=='RETURNED_UNVERIFIED'
        assert controller.status()['status']=='PREPARED_UNVERIFIED' and controller.status()['resume_status']=='RETURNED_UNVERIFIED'
        assert controller._deadline==manual_deadline
        with pytest.raises(PreparationConflict):controller.approve_resume(offer['nonce'],offer['scope_sha'],SESSION,approve_upload=True)
        with pytest.raises(PreparationConflict):controller.review_resume(SESSION)
    finally:assert controller.shutdown()
    assert len(uploads)==1 and uploads[0].retired
    assert len({thread for _,thread in setup[1]})==1 and threading.get_ident() not in {thread for _,thread in setup[1]}


def test_upload_admission_is_rechecked_after_private_review(setup):
    valid=[True];controller,uploads=resume_controller(setup,upload_admission=lambda _:valid[0])
    offer=controller.review_resume(SESSION);valid[0]=False
    with pytest.raises(PreparationConflict):controller.approve_resume(offer['nonce'],offer['scope_sha'],SESSION,approve_upload=True)
    assert controller.shutdown() and uploads[0].retired
    assert not any(name=='upload' for name,_ in setup[1])


def test_upload_offer_expiry_retires_bytes_without_renewing_field_or_manual_deadline(setup):
    now=[10.];controller,uploads=resume_controller(setup,upload_admission=lambda _:True,clock=lambda:now[0])
    controller.review_resume(SESSION);assert controller._deadline==130. and controller._review_deadline==910.
    now[0]=131.
    deadline=time.monotonic()+2
    while controller.status()['status']!='CLOSED' and time.monotonic()<deadline:time.sleep(.01)
    assert controller.shutdown() and uploads[0].retired
    assert not any(name=='upload' for name,_ in setup[1])


def test_cancel_during_upload_never_publishes_a_clean_result(setup):
    controller,uploads=resume_controller(setup,upload_admission=lambda _:True)
    offer=controller.review_resume(SESSION);resume=uploads[0];original=resume.approve
    def cancelled(*args,**kwargs):
        result=original(*args,**kwargs);controller.cancel(SESSION);return result
    resume.approve=cancelled
    with pytest.raises(PreparationConflict):controller.approve_resume(offer['nonce'],offer['scope_sha'],SESSION,approve_upload=True)
    assert controller.shutdown() and resume.retired
    assert controller.status()['resume_status']=='UNKNOWN_OUTCOME'


def test_uncertain_close_retires_upload_material_but_shutdown_is_false(setup):
    controller,uploads=resume_controller(setup,upload_admission=lambda _:True)
    controller.review_resume(SESSION);setup[-1][0]=False
    assert controller.shutdown() is False and uploads[0].retired
    assert controller.status()['status']=='UNKNOWN_OUTCOME'


def test_manual_deadline_wins_during_upload_admission_before_any_upload(setup):
    now=[10.];admissions=[0]
    def admitted(_):
        admissions[0]+=1
        if admissions[0]==2:now[0]=911.
        return True
    controller,uploads=resume_controller(setup,upload_admission=admitted,clock=lambda:now[0])
    now[0]=890.;offer=controller.review_resume(SESSION)
    assert controller._deadline==910.
    with pytest.raises(PreparationConflict):controller.approve_resume(offer['nonce'],offer['scope_sha'],SESSION,approve_upload=True)
    assert controller.shutdown() and uploads[0].retired
    assert not any(name=='upload' for name,_ in setup[1])


def test_every_upload_guard_observes_controller_manual_deadline(setup):
    now=[10.];controller,uploads=resume_controller(setup,upload_admission=lambda _:True,clock=lambda:now[0])
    now[0]=890.;offer=controller.review_resume(SESSION);resume=uploads[0];original=resume.approve
    def expiring(*args,**kwargs):
        now[0]=911.;return original(*args,**kwargs)
    resume.approve=expiring
    with pytest.raises(PreparationConflict):controller.approve_resume(offer['nonce'],offer['scope_sha'],SESSION,approve_upload=True)
    assert controller.shutdown() and resume.retired
    assert not any(name=='upload' for name,_ in setup[1])


def test_cancellation_during_native_selfcheck_cannot_publish_a_late_offer(setup):
    factory,calls,*_=setup;entered=threading.Event();release=threading.Event();outcomes=[]
    def slow_admission(_):
        entered.set();assert release.wait(2);return True
    controller=factory(write_admission=slow_admission)
    def opening():
        try:controller.open('task',1,SESSION,['0'])
        except PreparationConflict:outcomes.append('refused')
    caller=threading.Thread(target=opening);caller.start();assert entered.wait(2)
    controller.cancel(SESSION);release.set();caller.join(2)
    assert not caller.is_alive() and outcomes==['refused']
    assert controller.shutdown() and controller.status()['status']=='CLOSED'
    assert not any(name=='primitive' for name,_ in calls)


def test_slow_runtime_admission_finishes_before_private_offer_is_issued(setup):
    factory,calls,*_=setup;now=[100.0];issued=[]
    def admission(_):
        assert not any(name=='flow' for name,_ in calls)
        now[0]+=45
        calls.append(('admission',threading.get_ident()))
        return True
    controller=factory(write_admission=admission,clock=lambda:now[0])
    original=controller._flow_factory
    def flow_factory(*args,**kwargs):
        issued.append(now[0]);return original(*args,**kwargs)
    controller._flow_factory=flow_factory
    try:
        offer=controller.open('task',1,SESSION,['0'])
        assert issued==[145.0] and offer['expires_in_seconds']==120
        assert controller.status()['remaining_seconds']==120
        assert [name for name,_ in calls][:3]==['enter','admission','flow']
    finally:assert controller.shutdown()


def test_authority_and_ui_share_full_lifetime_after_native_setup(fixture):
    import hashlib
    from test_preparation_authority_v1 import BROWSER
    q,task,_,_,now=fixture
    class Owner:
        def __init__(self):self.context=SimpleNamespace(pages=[])
        def __enter__(self):return self
        def close(self):self.context=None;return True
    class Flow:
        def __init__(self,authority,owner,**kwargs):
            self.authority,self.owner=authority,owner
            self.offer=authority.issue(kwargs['task_id'],kwargs['revision'],kwargs['session'],BROWSER,kwargs['selected_ids'])
        def private_offer(self):return dict(self.offer)
        def close(self):
            self.authority.revoke(self.offer['nonce'],SESSION)
            return {'context_closed':self.owner.close(),'reconciliation_required':False}
    def admission(_):now[0]+=45;return True
    controller=PreparationController(q,lambda s:s==SESSION,owner_factory=Owner,flow_factory=Flow,
        write_admission=admission,clock=lambda:now[0])
    try:
        offered=controller.open(task['task_id'],task['revision'],SESSION,['0'])
        record=controller.authority.pending[hashlib.sha256(offered['nonce'].encode()).hexdigest()]
        assert record['expires']==controller._deadline==now[0]+120
        assert offered['expires_in_seconds']==controller.status()['remaining_seconds']==120
        now[0]+=119
        assert controller.status()['remaining_seconds']==1 and record['expires']-now[0]==1
        now[0]+=2
        with pytest.raises(PreparationConflict):
            controller.authority.consume(offered['nonce'],SESSION,offered['scope_sha'],BROWSER,approve_transmission=True)
        assert q.run_attempts(task['task_id'])==[]
    finally:assert controller.shutdown()


def test_cancellation_during_private_offer_cannot_publish_after_native_admission(setup):
    factory,calls,*_=setup;entered=threading.Event();release=threading.Event();outcomes=[]
    controller=factory(write_admission=lambda _:True)
    original=controller._flow_factory
    def flow_factory(*args,**kwargs):
        flow=original(*args,**kwargs);private_offer=flow.private_offer
        def held_offer():
            entered.set();assert release.wait(2);return private_offer()
        flow.private_offer=held_offer;return flow
    controller._flow_factory=flow_factory
    def opening():
        try:controller.open('task',1,SESSION,['0'])
        except PreparationConflict:outcomes.append('refused')
    caller=threading.Thread(target=opening);caller.start();assert entered.wait(2)
    controller.cancel(SESSION);release.set();caller.join(2)
    assert not caller.is_alive() and outcomes==['refused']
    assert controller.shutdown() and controller.status()['status']=='CLOSED'
    assert not any(name=='primitive' for name,_ in calls)
