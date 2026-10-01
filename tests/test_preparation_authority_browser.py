"""End-to-end synthetic consent → SQLite authority → fresh context → primitives."""
import hashlib
import json

import pytest

from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.task_preparation import CONTRACT_COMPANY, CONTRACT_ROLE, CONTRACT_URL
from executor.preparation.authority import PreparationAuthority, PreparationConflict, PreparationJournal
from executor.preparation.observation import observe_owned_document, observe_context_absence
from executor.preparation.runner import PreparationKernel
from test_qiyunfang_preparation_browser import browser, session, bounded_browser_oracle

SESSION='synthetic_ui_session_'+('a'*32)
PROCESS=hashlib.sha256(b'fixture-owned-launched-browser').hexdigest()
RESOURCES=hashlib.sha256(b'independently-captured-synthetic-popup').hexdigest()


def setup(tmp_path):
    tmp_path=tmp_path.resolve()
    profile=tmp_path/'profile.json'
    profile.write_text(json.dumps({'fields':{'identity.full_name':{'value':'SYNTHETIC_APPLICANT'},
        'identity.email':{'value':'synthetic@example.test'},'identity.id_number':{'value':'NEVER_TRANSMIT_ID'}}}))
    profile.chmod(0o600)
    queue=TaskQueue(tmp_path/'state')
    task=queue.enqueue(TaskSpec(company=CONTRACT_COMPANY,role=CONTRACT_ROLE,target_url=CONTRACT_URL,profile_ref=str(profile)))
    authority=PreparationAuthority(queue,session_valid=lambda value:value==SESSION)
    return queue,task,profile,authority


def binding(page):return observe_owned_document(page,process_sha=PROCESS,resources_sha=RESOURCES)


def test_explicit_exact_offer_drives_durable_kernel_without_ready_or_protected_writes(browser,tmp_path):
    q,task,profile,authority=setup(tmp_path);original=profile.read_bytes()
    with session(browser) as (page,transport):
        document=binding(page)
        assert observe_context_absence(browser,document.binding,process_sha=PROCESS)=={'status':'PRESENT'}
        offer=authority.issue(task['task_id'],task['revision'],SESSION,document.binding,['0','5','8'])
        assert page.locator('[data-formid="0"] input').input_value()==''
        permit=authority.consume(offer['nonce'],SESSION,offer['scope_sha'],binding(page).binding,approve_transmission=True)
        journal=PreparationJournal(authority,permit,SESSION,binding(page).binding)
        guard=lambda plan_sha:authority.guard(permit,SESSION,binding(page).binding,plan_sha=plan_sha)
        result=PreparationKernel(page,transport,guard,journal).run(permit['plan'])
        assert result['status']=='PREPARED_UNVERIFIED' and result['ready_to_submit'] is False
        assert page.locator('[data-formid="0"] input').input_value()=='SYNTHETIC_APPLICANT'
        assert page.locator('[data-formid="12"] input').input_value()==''
        assert page.locator('[data-formid="ValidateCode"] input').input_value()==''
        assert not page.locator('[data-formid="protocol"] input').is_checked()
        assert not page.locator('#M1567R13I19').is_checked()
        assert binding(page).binding==document.binding
        journal.complete()
        result=authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=False,
                               session=SESSION,browser_binding=binding(page).binding,plan_sha=permit['plan_sha'])
        assert result['status']=='PREPARED_UNVERIFIED' and q.preparation_in_flight()
        assert {row['outcome'] for row in q.field_actions(task['task_id'])}=={'DOM_READBACK_UNVERIFIED'}
    assert observe_context_absence(browser,document.binding,process_sha=PROCESS)['status']=='ABSENT'
    authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=True,
                     session=SESSION,browser_binding=document.binding,plan_sha=permit['plan_sha'])
    assert not q.preparation_in_flight() and q.get(task['task_id'])['stage']=='BLOCKED'
    assert profile.read_bytes()==original
    encoded=json.dumps(q.field_actions(task['task_id'])+q.run_attempts(task['task_id']))
    assert 'SYNTHETIC_APPLICANT' not in encoded and 'NEVER_TRANSMIT_ID' not in encoded
    with pytest.raises(ValueError):q.resume(task['task_id'])


def test_popup_replacement_between_private_offer_and_approval_produces_zero_primitives(browser,tmp_path):
    q,task,_,authority=setup(tmp_path)
    with session(browser) as (page,transport):
        original=binding(page)
        offer=authority.issue(task['task_id'],task['revision'],SESSION,original.binding,['0'])
        page.evaluate("const root=document.querySelector('.form_container');root.outerHTML=root.outerHTML")
        changed=binding(page)
        assert changed.binding['root_sha']!=original.binding['root_sha']
        with pytest.raises(PreparationConflict):authority.consume(offer['nonce'],SESSION,offer['scope_sha'],changed.binding,approve_transmission=True)
        assert page.locator('[data-formid="0"] input').input_value()==''
        assert not q.preparation_in_flight() and q.run_attempts(task['task_id'])==[]


def test_explicit_readonly_recovery_requires_real_context_absence_and_never_replays(browser,tmp_path):
    q,task,_,authority=setup(tmp_path)
    with session(browser) as (page,transport):
        document=binding(page);offer=authority.issue(task['task_id'],task['revision'],SESSION,document.binding,['0'])
        permit=authority.consume(offer['nonce'],SESSION,offer['scope_sha'],document.binding,approve_transmission=True)
        journal=PreparationJournal(authority,permit,SESSION,document.binding)
        PreparationKernel(page,transport,lambda sha:authority.guard(permit,SESSION,binding(page).binding,plan_sha=sha),journal).run(permit['plan'])
        journal.invalidate()
        replacement=PreparationAuthority(TaskQueue(q.root),session_valid=lambda value:value==SESSION)
        observe=lambda expected:observe_context_absence(browser,expected,process_sha=PROCESS)
        with pytest.raises(PreparationConflict):replacement.reconcile_closed_context(permit['nonce_sha'],SESSION,observe)
        assert q.preparation_in_flight()
    result=replacement.reconcile_closed_context(permit['nonce_sha'],SESSION,observe)
    assert result['status']=='UNKNOWN_OUTCOME' and not q.preparation_in_flight()
    assert {row['outcome'] for row in q.field_actions(task['task_id'])}=={'UNKNOWN_OUTCOME'}
    with pytest.raises(ValueError):q.resume(task['task_id'])
    assert observe_context_absence(browser,document.binding,process_sha='a'*64)=={'status':'UNKNOWN'}
