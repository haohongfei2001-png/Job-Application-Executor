"""Flow state/authority tests with explicitly fake DOM, not browser proof."""
import copy
from types import SimpleNamespace
import pytest

from executor.preparation.flow import PreparationFlow
from executor.preparation.authority import PreparationConflict
from test_preparation_authority_v1 import fixture,SESSION,BROWSER


@pytest.fixture
def flow_setup(fixture,monkeypatch):
    from executor.preparation import flow as module
    q,task,_,authority,_=fixture;events=[];context=object();closed=[True]
    transport=SimpleNamespace(phase='READ_ONLY',blocked=False)
    def seal():transport.phase='SEALED';events.append('seal')
    def sealed():
        if transport.phase!='SEALED' or transport.blocked:raise RuntimeError('transport changed')
    transport.seal,transport.require_sealed=seal,sealed
    page=SimpleNamespace(context=context,locator=lambda _:SimpleNamespace(evaluate=lambda _:'explicit fake DOM'))
    binding=copy.deepcopy(BROWSER)
    def save(_):events.append('save_process_receipt');return BROWSER['process_sha']
    def observe(*_,**__):
        value=copy.deepcopy(binding)
        return SimpleNamespace(binding=value,public_binding=lambda:copy.deepcopy(value))
    owner=SimpleNamespace(context=context,identity=SimpleNamespace(save=save),observe=observe,
                          transport=transport,close=lambda:events.append('close') or closed[0])
    monkeypatch.setattr(module,'validate_observation',lambda _:True)
    class Kernel:
        def __init__(self,page,transport,guard,journal):self.guard,self.journal=guard,journal
        def run(self,plan):
            from executor.preparation.qiyunfang import digest
            for item in plan:
                self.guard(digest(plan));action=self.journal.before(item['field_id']);events.append('primitive')
                self.journal.after(action,'READBACK_VERIFIED')
            return {'status':'PREPARED_UNVERIFIED','submit_capability':False}
    monkeypatch.setattr(module,'PreparationKernel',Kernel)
    flow=PreparationFlow(authority,owner,page,task_id=task['task_id'],revision=task['revision'],
                         session=SESSION,selected_ids=['0','8'],resources_sha=BROWSER['resources_sha'])
    return fixture,flow,events,binding,closed


def test_flow_requires_private_exact_offer_before_one_durable_run(flow_setup):
    (q,task,_,_,_),flow,events,_,_=flow_setup
    offer=flow.private_offer();offer['plan'][0]['value']='mutated client copy'
    assert events==['save_process_receipt','seal'] and not q.preparation_in_flight()
    with pytest.raises(PreparationConflict):flow.approve('wrong',offer['scope_sha'],SESSION,approve_transmission=True)
    result=flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    assert result['status']=='PREPARED_UNVERIFIED' and events.count('primitive')==2
    assert q.preparation_in_flight() and len(q.run_attempts(task['task_id']))==1
    with pytest.raises(PreparationConflict):flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    assert flow.close()['context_closed'] and not q.preparation_in_flight()
    assert flow.close()['status']=='CLOSED' and events.count('close')==1


@pytest.mark.parametrize('fault',['root','transport','cancel','profile'])
def test_changed_target_or_authority_never_executes_a_primitive(flow_setup,fault):
    (q,task,profile,_,_),flow,events,binding,_=flow_setup;offer=flow.private_offer()
    if fault=='root':binding['root_sha']='a'*64
    elif fault=='transport':flow.owner.transport.blocked=True
    elif fault=='cancel':q.cancel(task['task_id'])
    else:profile.write_text('{"fields":{}}')
    with pytest.raises((PreparationConflict,RuntimeError)):
        flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    assert 'primitive' not in events


def test_uncertain_admission_closes_transport_but_never_retries_or_refunds(flow_setup,monkeypatch):
    (q,_,_,authority,_),flow,events,_,_=flow_setup;offer=flow.private_offer();original=authority.consume
    def commit_then_error(*args,**kwargs):original(*args,**kwargs);raise OSError('synthetic result loss')
    monkeypatch.setattr(authority,'consume',commit_then_error)
    with pytest.raises(PreparationConflict):flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    assert flow.state=='UNKNOWN_OUTCOME' and events.count('close')==1 and 'primitive' not in events
    assert q.preparation_in_flight()
    with pytest.raises(PreparationConflict):flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    with pytest.raises(PreparationConflict):flow.close()


def test_failed_context_disposal_retains_durable_fence(flow_setup):
    (q,_,_,_,_),flow,events,_,closed=flow_setup;offer=flow.private_offer()
    flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    closed[0]=False
    result=flow.close()
    assert result['status']=='UNKNOWN_OUTCOME' and result['reconciliation_required']
    assert q.preparation_in_flight() and q.claim('worker') is None


def test_disposal_exception_revokes_prepared_state_and_readback_evidence(flow_setup):
    (q,task,_,_,_),flow,events,_,_=flow_setup;offer=flow.private_offer()
    flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    def fail():raise OSError('synthetic disposal uncertainty')
    flow.owner.close=fail
    result=flow.close()
    assert result['status']=='UNKNOWN_OUTCOME' and result['context_closed'] is False
    assert flow.state=='UNKNOWN_OUTCOME' and q.preparation_in_flight()
    assert {row['outcome'] for row in q.field_actions(task['task_id'])}=={'UNKNOWN_OUTCOME'}
    with pytest.raises(PreparationConflict):flow.close()
