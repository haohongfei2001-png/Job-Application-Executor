"""All values synthetic: owned sandboxed session → review → durable fill → close."""
import json
import sys
import pytest

from executor.preparation.authority import PreparationConflict
from executor.preparation.flow import PreparationFlow
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.qiyunfang import CONTRACT_URL,digest
from test_preparation_authority_browser import setup,SESSION
from test_qiyunfang_preparation_browser import replica,bounded_browser_oracle


def test_complete_owned_flow_has_one_approval_and_no_protected_or_submit_capability(tmp_path):
    q,task,_,authority=setup(tmp_path)
    with DisposablePreparationSession(headless=True,channel='chrome' if sys.platform=='linux' else None) as owner:
        owner.context.route(CONTRACT_URL,lambda route:route.fulfill(status=200,content_type='text/html',body=replica()))
        page=owner.context.new_page();page.goto(CONTRACT_URL)
        flow=PreparationFlow(authority,owner,page,task_id=task['task_id'],revision=task['revision'],
            session=SESSION,selected_ids=['0','5','8'],resources_sha=digest('synthetic fixture resource receipt'))
        offer=flow.private_offer()
        assert page.locator('[data-formid="0"] input').input_value()==''
        assert not q.preparation_in_flight()
        result=flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        assert result['status']=='PREPARED_UNVERIFIED' and not result['submit_capability']
        assert page.locator('[data-formid="0"] input').input_value()=='SYNTHETIC_APPLICANT'
        assert page.locator('[data-formid="12"] input').input_value()==''
        assert page.locator('[data-formid="ValidateCode"] input').input_value()==''
        assert q.preparation_in_flight() and q.claim('ordinary_worker') is None
        with pytest.raises(PreparationConflict):flow.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        assert flow.close()['context_closed'] and flow.state=='CLOSED'
        assert not q.preparation_in_flight() and q.get(task['task_id'])['stage']=='BLOCKED'
        assert {row['outcome'] for row in q.field_actions(task['task_id'])}=={'UNKNOWN_OUTCOME'}
        with pytest.raises(PreparationConflict):flow.private_offer()
    encoded=json.dumps(q.field_actions(task['task_id'])+q.run_attempts(task['task_id']))
    assert 'SYNTHETIC_APPLICANT' not in encoded and 'NEVER_TRANSMIT_ID' not in encoded
    assert list(q.root.glob('preparation-process-*.json'))


def test_real_owner_thread_runs_single_synthetic_plan_then_closes(tmp_path):
    """Controller integration with real sandboxed owner; no live-site writes."""
    from executor.preparation.controller import PreparationController
    q,task,_,_=setup(tmp_path)
    evidence=[]
    def flow_factory(authority,owner,**kwargs):
        owner.context.route(CONTRACT_URL,lambda route:route.fulfill(status=200,content_type='text/html',body=replica()))
        page=owner.context.new_page();page.goto(CONTRACT_URL)
        flow=PreparationFlow(authority,owner,page,resources_sha=digest('synthetic controller fixture'),**kwargs)
        original=flow.approve
        def approved(*args,**options):
            result=original(*args,**options)
            evidence.append({
                'routine_matches':page.locator('[data-formid="0"] input').input_value()=='SYNTHETIC_APPLICANT',
                'identity_blank':page.locator('[data-formid="12"] input').input_value()=='',
                'captcha_blank':page.locator('[data-formid="ValidateCode"] input').input_value()==''})
            return result
        flow.approve=approved
        return flow
    controller=PreparationController(q,lambda session:session==SESSION,flow_factory=flow_factory,
        owner_factory=lambda:DisposablePreparationSession(headless=True,channel='chrome' if sys.platform=='linux' else None),
        write_admission=lambda _:True)  # Synthetic fixture authority, never a product/HTTP flag.
    try:
        offer=controller.open(task['task_id'],task['revision'],SESSION,['0','5','8'])
        assert controller.status()['status']=='OFFERED' and not q.preparation_in_flight()
        result=controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
        assert result['status']=='PREPARED_UNVERIFIED' and controller.status()['status']=='PREPARED_UNVERIFIED'
        assert result['field_count']==3 and controller.status()['field_count']==3
        assert len(q.field_actions(task['task_id']))==3
        assert evidence==[{'routine_matches':True,'identity_blank':True,'captcha_blank':True}]
        assert len(q.run_attempts(task['task_id']))==1 and q.claim('ordinary_worker') is None
        with pytest.raises(PreparationConflict):controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
    finally:assert controller.shutdown(timeout=10)
    assert controller.status()['status']=='CLOSED' and not q.preparation_in_flight()
    assert {row['outcome'] for row in q.field_actions(task['task_id'])}=={'UNKNOWN_OUTCOME'}
    assert 'SYNTHETIC_APPLICANT' not in json.dumps(controller.status())
