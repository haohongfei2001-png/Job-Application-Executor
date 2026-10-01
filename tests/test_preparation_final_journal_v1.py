"""No browser or send capability: one durable final slot per preparation."""
import copy
import json
import threading

import pytest

from executor.preparation.authority import PreparationJournal,PreparationConflict
from executor.preparation.final_journal import PreparationFinalJournal
from executor.preparation.human_request import FINAL_URL,CONTRACT_VERSION,CONTRACT_ROLE
from test_preparation_authority_v1 import fixture,offer,consume,SESSION,BROWSER


@pytest.fixture
def prepared(fixture):
    q,task,profile,authority,now=fixture
    permit=consume(fixture,offer(fixture));journal=PreparationJournal(authority,permit,SESSION,BROWSER)
    for key in ['0','8']:
        action=journal.before(key);journal.after(action,'READBACK_VERIFIED')
    journal.complete()
    authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=False,session=SESSION,browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    binding={'browser':dict(BROWSER),'frame_id':'f'*32,'change_epoch':7}
    final=PreparationFinalJournal(authority,permit,SESSION,lambda:copy.deepcopy(binding))
    scope={'request_nonce_sha':'b'*64,'browser':copy.deepcopy(binding),
           'metadata':{'contract':CONTRACT_VERSION,'command':'addWafCk_addSubmit','form_id':6,
                       'role':CONTRACT_ROLE,'category':'final_application','change_epoch':7},
           'purpose':'final_application','recipient':FINAL_URL}
    return fixture,permit,binding,final,scope


def rows(q):
    with q.tx() as db:return [dict(row) for row in db.execute('SELECT * FROM preparation_final_requests')]


def test_final_slot_is_durable_private_and_cannot_be_refunded_with_new_nonce_or_bridge(prepared):
    (q,task,profile,authority,_),permit,binding,final,scope=prepared
    intent=final.consume(scope)
    assert rows(q)[0]['outcome']=='ATTEMPTED'
    assert final.record(intent,'RETURNED_UNVERIFIED')=='RETURNED_UNVERIFIED'
    with pytest.raises(PreparationConflict):PreparationFinalJournal(authority,permit,SESSION,lambda:copy.deepcopy(binding))
    data=json.dumps(rows(q))
    assert 'PRIVATE_AUTHORITY_CANARY' not in data and SESSION not in data and str(profile) not in data
    assert q.get(task['task_id'])['stage']=='BLOCKED' and q.preparation_in_flight()


@pytest.mark.parametrize('change',['cancel','pause','session','profile','root','epoch'])
def test_drift_before_final_slot_has_no_consumed_authority(prepared,change):
    (q,task,profile,authority,_),_,binding,final,scope=prepared
    if change=='cancel':q.cancel(task['task_id'])
    elif change=='pause':q.pause(task['task_id'])
    elif change=='session':authority.session_valid=lambda _:False
    elif change=='profile':profile.write_text('{"fields":{}}')
    elif change=='root':binding['browser']['root_sha']='d'*64
    else:binding['change_epoch']+=1
    with pytest.raises((PreparationConflict,RuntimeError)):final.consume(scope)
    assert rows(q)==[]


def test_unknown_cannot_be_promoted_and_cancel_during_return_does_not_become_success(prepared):
    (q,task,_,_,_),_,_,final,scope=prepared
    intent=final.consume(scope);q.cancel(task['task_id'])
    assert final.record(intent,'RETURNED_UNVERIFIED')=='UNKNOWN_OUTCOME'
    assert final.record(intent,'RETURNED_UNVERIFIED')=='UNKNOWN_OUTCOME'
    assert q.get(task['task_id'])['stage']=='CANCELLED'


def test_two_journals_compete_for_one_final_slot(prepared):
    (q,_,_,authority,_),permit,binding,first,scope=prepared
    second=PreparationFinalJournal(authority,permit,SESSION,lambda:copy.deepcopy(binding))
    barrier=threading.Barrier(2);accepted=[]
    def attempt(journal,nonce):
        barrier.wait()
        try:accepted.append(journal.consume(dict(scope,request_nonce_sha=nonce)))
        except PreparationConflict:pass
    threads=[threading.Thread(target=attempt,args=(first,'c'*64)),threading.Thread(target=attempt,args=(second,'d'*64))]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=5)
    assert len(accepted)==1 and len(rows(q))==1 and all(not thread.is_alive() for thread in threads)


def test_fresh_human_slot_never_renews_expired_field_writing_authority(prepared):
    (q,_,_,authority,now),permit,_,final,scope=prepared
    now[0]+=121
    with pytest.raises(PreparationConflict):authority.guard(permit,SESSION,BROWSER,plan_sha=permit['plan_sha'])
    final.consume(scope)
    with pytest.raises(PreparationConflict):authority.guard(permit,SESSION,BROWSER,plan_sha=permit['plan_sha'])
    assert q.claim('worker') is None


@pytest.mark.parametrize('change',['attempt','run','actions','task_revision','task_spec','owner'])
def test_retained_proof_or_exact_task_identity_drift_blocks_final_slot(prepared,change):
    (q,task,_,_,_),_,_,final,scope=prepared
    with q.tx() as db:
        if change=='attempt':db.execute("UPDATE preparation_approvals SET attempt_id='different'")
        elif change=='run':db.execute("UPDATE run_attempts SET outcome='UNKNOWN_OUTCOME'")
        elif change=='actions':db.execute("UPDATE field_actions SET outcome='UNKNOWN_OUTCOME'")
        elif change=='task_revision':db.execute('UPDATE tasks SET revision=revision+1')
        elif change=='task_spec':
            spec=dict(task['spec'],role='different');db.execute('UPDATE tasks SET spec=?',(json.dumps(spec),))
        else:db.execute("UPDATE tasks SET owner='different'")
    with pytest.raises((PreparationConflict,RuntimeError)):final.consume(scope)
    assert rows(q)==[]


def test_commit_uncertainty_never_admits_a_second_nonce(prepared,monkeypatch):
    from contextlib import contextmanager
    (q,_,_,_,_),_,_,final,scope=prepared;original=q.tx
    @contextmanager
    def uncertain():
        with original() as db:
            yield db
            attempted=db.execute('SELECT 1 FROM preparation_final_requests').fetchone()
        if attempted:raise OSError('synthetic post-commit uncertainty')
    monkeypatch.setattr(q,'tx',uncertain)
    with pytest.raises(OSError):final.consume(scope)
    monkeypatch.setattr(q,'tx',original)
    assert rows(q)[0]['outcome']=='ATTEMPTED'
    with pytest.raises(PreparationConflict):final.consume(dict(scope,request_nonce_sha='e'*64))


@pytest.mark.parametrize('change',['UNKNOWN_OUTCOME','RETURNED_UNVERIFIED','scope','request_nonce','attempt'])
def test_durable_slot_downgrade_or_rebinding_revokes_pre_send_guard(prepared,change):
    (q,_,_,_,_),_,_,final,scope=prepared;final.consume(scope)
    with q.tx() as db:
        if change in {'UNKNOWN_OUTCOME','RETURNED_UNVERIFIED'}:db.execute('UPDATE preparation_final_requests SET outcome=?',(change,))
        elif change=='scope':db.execute("UPDATE preparation_final_requests SET scope_sha=?",('e'*64,))
        elif change=='request_nonce':db.execute("UPDATE preparation_final_requests SET request_nonce_sha=?",('f'*64,))
        else:db.execute("UPDATE preparation_final_requests SET attempt_id='other'")
    with pytest.raises(PreparationConflict):final.guard()


def test_unknown_final_and_closed_context_cannot_mint_new_preparation(prepared):
    (q,task,_,authority,_),permit,_,final,scope=prepared
    intent=final.consume(scope);final.record(intent,'UNKNOWN_OUTCOME')
    authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
    current=q.get(task['task_id']);offer=authority.issue(current['task_id'],current['revision'],SESSION,BROWSER,['0'])
    with pytest.raises(PreparationConflict):authority.consume(offer['nonce'],SESSION,offer['scope_sha'],BROWSER,approve_transmission=True)
