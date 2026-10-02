"""Bounded human-review integration: no live network or native acceptance."""
from types import SimpleNamespace
import pytest
from executor.preparation import human_review as m
from executor.preparation.authority import PreparationConflict
from executor.preparation.private_child import _OPERATIONS
from test_preparation_controller_v1 import setup,opened
from test_preparation_authority_v1 import fixture,SESSION


def test_production_default_has_no_interception_or_forwarding_capability():
    class Owner:
        @property
        def context(self):raise AssertionError('Unavailable handoff must not install a route')
    review=m.HumanReviewCoordinator(Owner(),lambda _:(_ for _ in ()).throw(AssertionError()))
    result=review.begin(SimpleNamespace())
    assert result=={'status':'UNAVAILABLE','submit_capability':False,'automatic_retry':False,
                   'server_application_verified':False,'reason':'PHYSICAL_NATIVE_AND_SITE_ACCEPTANCE_PENDING'}
    assert review.classifier is review.bridge is review.journal is None
    review.close()


def test_parent_protocol_cannot_accept_or_forward_a_request():
    assert 'begin_human_review' in _OPERATIONS
    assert not {'confirm','accept','send','submit','forward','solve_captcha'} & _OPERATIONS
    assert not hasattr(m.HumanReviewCoordinator,'confirm')
    assert not hasattr(m.HumanReviewCoordinator,'accept')


def test_modal_tick_never_calls_renderer_review_or_material_guard():
    owner=SimpleNamespace()
    review=m.HumanReviewCoordinator(owner,lambda _:True)
    review._state='MANUAL_REVIEW_ACTIVE';review._admit=lambda:True;review.classifier=SimpleNamespace(invalid=False)
    review.flow=SimpleNamespace(_still_authorized=lambda:True,
        review_fence=lambda:(_ for _ in ()).throw(AssertionError('modal renderer RPC')))
    review.bridge=SimpleNamespace(drain=lambda:{'status':'DIALOG_OPEN'})
    assert review.tick()['status']=='MANUAL_REVIEW_ACTIVE'


def test_parent_revocation_cancels_modal_without_renderer_guard():
    calls=[];review=m.HumanReviewCoordinator(SimpleNamespace(),lambda _:True)
    review._state='MANUAL_REVIEW_ACTIVE';review._admit=lambda:True;review.classifier=SimpleNamespace(invalid=False)
    review.flow=SimpleNamespace(_still_authorized=lambda:False)
    review.bridge=SimpleNamespace(cancel=lambda:calls.append('cancel'))
    assert review.tick()['status']=='CANCELLED' and calls==['cancel']


def test_terminal_return_is_not_rechecked_with_attempted_only_journal_guard():
    review=m.HumanReviewCoordinator(SimpleNamespace(),lambda _:True)
    review._state='RETURNED_UNVERIFIED'
    review.flow=SimpleNamespace(_still_authorized=lambda:(_ for _ in ()).throw(AssertionError()))
    review.bridge=SimpleNamespace(cancel=lambda:(_ for _ in ()).throw(AssertionError('downgrade terminal result')))
    assert review.tick()['status']=='RETURNED_UNVERIFIED'
    review.close();assert review.status()['status']=='RETURNED_UNVERIFIED'


def test_existing_controller_exposes_unavailable_gate_without_new_authority(setup):
    factory,calls,*_=setup
    controller,offer=opened(factory,write_admission=lambda _:True)
    try:
        with pytest.raises(PreparationConflict):controller.begin_human_review(SESSION)
        controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        result=controller.begin_human_review(SESSION)
        assert result['status']=='UNAVAILABLE' and result['submit_capability'] is False
        assert controller.status()['status']=='PREPARED_UNVERIFIED'
        assert controller.status()['final_status']=='UNAVAILABLE'
        assert len([row for row in calls if row[0]=='primitive'])==1
    finally:assert controller.shutdown()


def test_controller_handoff_transfers_guard_and_cannot_resume_routine_actions(setup):
    factory,calls,*_=setup;events=[]
    class Review:
        def __init__(self,owner):events.append('install');self.state='AVAILABLE'
        def begin(self,flow):self.state='MANUAL_REVIEW_ACTIVE';return {'status':self.state,'submit_capability':False}
        def tick(self):events.append('tick');return {'status':self.state}
        def status(self):return {'status':self.state}
        def close(self):events.append('close');self.state='CANCELLED'
    controller,offer=opened(factory,write_admission=lambda _:True,human_review_factory=Review)
    try:
        controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        assert controller.begin_human_review(SESSION)['status']=='MANUAL_REVIEW_ACTIVE'
        assert controller.status()['status']=='HUMAN_REVIEW'
        with pytest.raises(PreparationConflict):controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        with pytest.raises(PreparationConflict):controller.review_resume(SESSION)
        with pytest.raises(PreparationConflict):controller.begin_human_review(SESSION)
    finally:assert controller.shutdown()
    assert events[0]=='install' and events[-1]=='close'


@pytest.mark.parametrize('during',['frame','binding'])
def test_reentrant_observation_invalidation_never_reaches_fetch(during):
    from test_preparation_human_request_v1 import setup as bridge_fixture,META,confirm
    bridge,route,events,_,_=bridge_fixture.__wrapped__()
    bridge.hold(route,META);confirm(bridge)
    review=m.HumanReviewCoordinator(SimpleNamespace(),lambda _:True)
    review._state='MANUAL_REVIEW_ACTIVE';review._admit=lambda:True
    review.bridge=bridge;review._request=route.request;review._frame_id='native-frame'
    review.classifier=SimpleNamespace(invalid=False,require_exact=lambda _:7)
    def binding():
        if during=='binding':review._invalidate()
        return {'synthetic':'binding'}
    def frame(*_):
        if during=='frame':review._invalidate()
        return {'frameTree':{'frame':{'id':'native-frame'}}}
    review.flow=SimpleNamespace(_still_authorized=lambda:True,_binding=binding)
    review.cdp=SimpleNamespace(send=frame)
    bridge._guard=review._observe
    assert bridge.drain()['status']=='CANCELLED'
    assert 'fetch' not in events and 'consume' not in events
    assert review._state=='CANCELLED'
    bridge.drain();assert 'fetch' not in events
