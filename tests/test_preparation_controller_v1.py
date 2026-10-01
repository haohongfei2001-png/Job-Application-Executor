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
    controller,_=opened(factory);assert controller.shutdown()
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
    assert controller.shutdown()
    assert controller.status()['status']==('CLOSED' if closed else 'UNKNOWN_OUTCOME')
    assert calls==['entry_cleanup']


def test_unannotated_entry_failure_remains_unknown_without_second_close(fixture):
    q,*_=fixture;calls=[]
    class Owner:
        def __enter__(self):self.close();raise OSError('synthetic startup uncertainty')
        def close(self):calls.append('close');return False
    controller=PreparationController(q,lambda value:value==SESSION,owner_factory=Owner)
    with pytest.raises(PreparationConflict):controller.open('task',1,SESSION,['0'])
    assert controller.shutdown() and controller._owner is not None
    assert controller.status()['status']=='UNKNOWN_OUTCOME' and calls==['close']
