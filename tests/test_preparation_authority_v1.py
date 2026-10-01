"""No browser execution: durable approval, exact binding and atomic admission."""
import hashlib
import json
import threading

import pytest

from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.task_preparation import CONTRACT_URL, CONTRACT_COMPANY, CONTRACT_ROLE
from executor.autonomy.updater import safe_to_update, runtime_safe_to_update
from executor.preparation.authority import PreparationAuthority, PreparationConflict

SESSION = 's' * 40
BROWSER = {key: hashlib.sha256(key.encode()).hexdigest() for key in
           ('process_sha', 'context_sha', 'document_sha', 'root_sha', 'controls_sha', 'resources_sha')}


@pytest.fixture
def fixture(tmp_path):
    tmp_path = tmp_path.resolve()
    now = [1000.0]
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'fields': {'identity.full_name': {'value': 'PRIVATE_AUTHORITY_CANARY'},
        'identity.id_number': {'value': 'ID_NOT_IN_APPROVAL'}, 'identity.email': {'value': 'synthetic@example.test'}}}))
    profile.chmod(0o600)
    q = TaskQueue(tmp_path / 'state', clock=lambda: now[0])
    task = q.enqueue(TaskSpec(company=CONTRACT_COMPANY, role=CONTRACT_ROLE, target_url=CONTRACT_URL, profile_ref=str(profile)))
    authority = PreparationAuthority(q, session_valid=lambda s: s == SESSION, clock=lambda: now[0])
    return q, task, profile, authority, now


def offer(f):
    q, task, _, authority, _ = f
    return authority.issue(task['task_id'], task['revision'], SESSION, BROWSER, ['0', '8'])


def consume(f, proposal, **changes):
    _, _, _, authority, _ = f
    kwargs = dict(nonce=proposal['nonce'], session=SESSION, scope_sha=proposal['scope_sha'],
                  browser_binding=BROWSER, approve_transmission=True)
    kwargs.update(changes)
    return authority.consume(**kwargs)


def rows(q):
    with q.tx() as db: return [dict(row) for row in db.execute('SELECT * FROM preparation_approvals')]


def test_offer_is_private_non_authorizing_and_consumption_is_durable_value_free(fixture):
    q, task, profile, authority, now = fixture
    proposed = offer(fixture)
    assert 'PRIVATE_AUTHORITY_CANARY' in json.dumps(proposed)
    assert 'ID_NOT_IN_APPROVAL' not in json.dumps(proposed)
    assert rows(q) == [] and q.get(task['task_id']) == task
    permit = consume(fixture, proposed)
    assert len(rows(q)) == 1 and rows(q)[0]['outcome'] == 'ATTEMPTED'
    assert all(marker not in json.dumps(rows(q)) for marker in ('PRIVATE_AUTHORITY_CANARY','ID_NOT_IN_APPROVAL',SESSION,proposed['nonce'],str(profile)))
    assert rows(q)[0]['prior_stage'] == task['stage'] and rows(q)[0]['prior_blocker'] == task['blocker']
    authority.guard(permit, SESSION, BROWSER, plan_sha=permit["plan_sha"])
    reopened = TaskQueue(q.root, clock=lambda: now[0])
    assert rows(reopened) == rows(q)
    assert reopened.claim('worker') is None
    with pytest.raises(PreparationConflict): consume(fixture, proposed)
    with pytest.raises(ValueError): q.resume(task['task_id'])


@pytest.mark.parametrize('changes', [{'session':'x'*40}, {'scope_sha':'a'*64}, {'nonce':'unknown_nonce_with_length_32_12345'},
    {'approve_transmission':False}, {'approve_transmission':'true'}, {'approve_transmission':1},
    {'browser_binding':dict(BROWSER, context_sha='a'*64)}])
def test_wrong_scope_does_not_consume_or_admit(fixture, changes):
    q, task, _, _, _ = fixture
    proposed = offer(fixture)
    with pytest.raises(PreparationConflict): consume(fixture, proposed, **changes)
    assert rows(q) == [] and q.get(task['task_id']) == task


@pytest.mark.parametrize('change', ['profile','resume_appeared','cancel','pause','task_spec','revision','expiry'])
def test_drift_before_consume_yields_zero_authority(fixture, change):
    q, task, path, authority, now = fixture
    proposed = offer(fixture)
    if change == 'profile':
        data = json.loads(path.read_text()); data['fields']['identity.full_name']['value']='changed'; path.write_text(json.dumps(data))
    elif change == 'resume_appeared':
        data = json.loads(path.read_text()); data['assets']={'resume':{'kind':'resume_pdf','path':str(path.parent/'resume.pdf')}}
        path.write_text(json.dumps(data))
    elif change == 'cancel': q.cancel(task['task_id'])
    elif change == 'pause': q.pause(task['task_id'])
    elif change == 'expiry': now[0] += 121
    else:
        with q.tx() as db:
            if change == 'revision': db.execute('UPDATE tasks SET revision=revision+1 WHERE task_id=?',(task['task_id'],))
            else:
                spec=dict(task['spec'], company='different'); db.execute('UPDATE tasks SET spec=? WHERE task_id=?',(json.dumps(spec),task['task_id']))
    with pytest.raises((PreparationConflict,RuntimeError)): consume(fixture, proposed)
    assert rows(q) == []


@pytest.mark.parametrize('interrupt', ['pause','cancel','lease_expiry','profile_barrier','session_expiry','profile_changed'])
def test_interrupt_revokes_primitives_but_not_browser_resource_fence(fixture, interrupt):
    from types import SimpleNamespace
    q, task, profile, authority, now = fixture
    permit = consume(fixture, offer(fixture))
    if interrupt == 'pause': q.pause(task['task_id'])
    elif interrupt == 'cancel': q.cancel(task['task_id'])
    elif interrupt == 'lease_expiry': now[0] += 61
    elif interrupt == 'session_expiry': authority.session_valid=lambda _:False
    elif interrupt == 'profile_changed': profile.write_text('{"fields":{}}')
    else: q.begin_profile_write(str(profile),task_id='different-task')
    with pytest.raises((PreparationConflict,RuntimeError)): authority.guard(permit,SESSION,BROWSER,plan_sha=permit["plan_sha"])
    assert q.preparation_in_flight() and q.claim('worker') is None
    assert safe_to_update(SimpleNamespace(worker=SimpleNamespace(active=None),queue=q)) == (False,'preparation_context_unclosed')
    assert runtime_safe_to_update(q.root) == (False,'preparation_context_unclosed')
    authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=False)
    assert q.preparation_in_flight() and q.claim('worker') is None
    before=q.get(task['task_id'])
    authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
    assert not q.preparation_in_flight()
    assert q.get(task['task_id'])['stage']==before['stage']
    assert q.get(task['task_id'])['blocker']==before['blocker']
    with pytest.raises(ValueError): q.resume(task['task_id'])


def test_unknown_consumed_attempt_cannot_be_retried_under_a_new_nonce(fixture):
    q, task, _, authority, _ = fixture
    permit=consume(fixture,offer(fixture)); authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
    latest=q.get(task['task_id'])
    proposed=authority.issue(latest['task_id'],latest['revision'],SESSION,BROWSER,['0'])
    with pytest.raises(PreparationConflict): consume(fixture,proposed)
    assert len(rows(q))==1


def test_restart_never_revives_offer_or_active_permit(fixture):
    q, _, _, authority, now = fixture
    old_offer=offer(fixture)
    replacement=PreparationAuthority(TaskQueue(q.root,clock=lambda:now[0]),session_valid=lambda _:True,clock=lambda:now[0])
    with pytest.raises(PreparationConflict): replacement.consume(old_offer['nonce'],SESSION,old_offer['scope_sha'],BROWSER,approve_transmission=True)
    permit=consume(fixture,old_offer)
    with pytest.raises(PreparationConflict): replacement.guard(permit,SESSION,BROWSER,plan_sha=permit["plan_sha"])
    assert q.preparation_in_flight()


def test_worker_claim_and_approval_are_one_atomic_winner(fixture):
    q, task, _, authority, now = fixture
    proposed=offer(fixture)
    other=TaskQueue(q.root,clock=lambda:now[0])
    barrier=threading.Barrier(2); outcomes=[]
    def admit():
        barrier.wait()
        try: outcomes.append(('preparation',consume(fixture,proposed)))
        except (PreparationConflict,RuntimeError): outcomes.append(('preparation',None))
    def worker():
        barrier.wait(); outcomes.append(('worker',other.claim('worker')))
    threads=[threading.Thread(target=admit),threading.Thread(target=worker)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(value is not None for _,value in outcomes)==1


def test_two_independent_services_cannot_consume_competing_approvals(fixture):
    q, task, _, authority, now = fixture
    other=PreparationAuthority(TaskQueue(q.root,clock=lambda:now[0]),session_valid=lambda _:True,clock=lambda:now[0])
    first=offer(fixture); second=other.issue(task['task_id'],task['revision'],SESSION,BROWSER,['0'])
    consume(fixture,first)
    with pytest.raises((PreparationConflict,RuntimeError)):
        other.consume(second['nonce'],SESSION,second['scope_sha'],BROWSER,approve_transmission=True)
    assert len(rows(q))==1


def test_successful_context_close_never_means_ready_or_requeues(fixture):
    q, task, _, authority, _=fixture
    permit=consume(fixture,offer(fixture))
    from executor.preparation.authority import PreparationJournal
    journal=PreparationJournal(authority,permit,SESSION,BROWSER)
    for key in ('0','8'):
        action=journal.before(key);journal.after(action,'READBACK_VERIFIED')
    journal.complete()
    authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=False,session=SESSION,browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    assert q.preparation_in_flight()
    authority.guard(permit,SESSION,BROWSER,plan_sha=permit["plan_sha"])
    authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=True,session=SESSION,browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    assert not q.preparation_in_flight()
    assert q.get(task['task_id'])['stage']=='BLOCKED'
    assert q.claim('worker') is None
    assert rows(q)[0]['outcome']=='PREPARED_UNVERIFIED'


@pytest.mark.parametrize('change',['plan','profile_sha','scope_sha','session_sha','task_id','task_revision','browser'])
def test_mutated_consumed_permit_never_changes_approved_scope(fixture,change):
    import copy
    _, _, _, authority, _=fixture
    permit=consume(fixture,offer(fixture)); changed=copy.deepcopy(permit)
    if change=='plan':changed['plan'][0]['value']='REPLACED_UNAPPROVED_VALUE'
    elif change=='task_revision':changed[change]+=1
    elif change=='browser':changed[change]['context_sha']='a'*64
    else:changed[change]='a'*64
    with pytest.raises(PreparationConflict):authority.guard(changed,SESSION,BROWSER,plan_sha=changed['plan_sha'])
    with pytest.raises(PreparationConflict):authority.finish(changed,'UNKNOWN_OUTCOME',context_closed=True)
    assert fixture[0].preparation_in_flight()


def test_actual_runner_plan_digest_must_match_the_durable_approval(fixture):
    from executor.preparation.qiyunfang import digest
    _, _, _, authority, _=fixture
    permit=consume(fixture,offer(fixture))
    with pytest.raises(PreparationConflict):authority.guard(permit,SESSION,BROWSER,plan_sha=digest([{'field_id':'0','value':'UNAPPROVED'}]))


@pytest.mark.parametrize('interrupt',['pause','cancel','profile','expired_session','lease'])
def test_completion_downgrades_to_unknown_when_final_fence_loses(fixture,interrupt):
    q,task,profile,authority,now=fixture
    permit=consume(fixture,offer(fixture))
    if interrupt=='pause':q.pause(task['task_id'])
    elif interrupt=='cancel':q.cancel(task['task_id'])
    elif interrupt=='profile':profile.write_text('{"fields":{}}')
    elif interrupt=='expired_session':authority.session_valid=lambda _:False
    else:now[0]+=61
    result=authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=True,
                            session=SESSION,browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    assert result['status']=='UNKNOWN_OUTCOME'
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME'


def test_cancel_between_completion_guard_and_transaction_cannot_record_success(fixture,monkeypatch):
    q,task,_,authority,_=fixture
    permit=consume(fixture,offer(fixture)); original=authority.guard
    def race(*args,**kwargs):
        original(*args,**kwargs);q.cancel(task['task_id'])
    monkeypatch.setattr(authority,'guard',race)
    result=authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=True,
                            session=SESSION,browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    assert result['status']=='UNKNOWN_OUTCOME' and q.get(task['task_id'])['stage']=='CANCELLED'


def test_completed_zero_write_account_refusal_is_admissible_and_kept(fixture):
    q,task,_,authority,_=fixture
    claimed=q.claim('worker'); attempt=q.begin_run_attempt(task['task_id'],claimed['owner'])
    q.finish_run_attempt(attempt,'RETURNED_UNVERIFIED')
    q.checkpoint(task['task_id'],claimed['owner'],'BLOCKED',blocker='account_identity_unverified',release=True)
    current=q.get(task['task_id']); history=q.run_attempts(task['task_id'])
    proposal=authority.issue(current['task_id'],current['revision'],SESSION,BROWSER,['0'])
    consume(fixture,proposal)
    assert q.run_attempts(task['task_id'])==history
    assert rows(q)[0]['prior_blocker']=='account_identity_unverified'


def test_completed_generic_field_actions_do_not_authorize_anonymous_replay(fixture):
    q,task,_,authority,_=fixture
    claimed=q.claim('worker'); attempt=q.begin_run_attempt(task['task_id'],claimed['owner'])
    action=q.begin_field_action(attempt,'a'*64)
    q.finish_field_action(action,'DOM_READBACK_UNVERIFIED')
    q.finish_run_attempt(attempt,'RETURNED_UNVERIFIED')
    q.checkpoint(task['task_id'],claimed['owner'],'BLOCKED',blocker='account_identity_unverified',release=True)
    current=q.get(task['task_id'])
    proposal=authority.issue(current['task_id'],current['revision'],SESSION,BROWSER,['0'])
    with pytest.raises(PreparationConflict):consume(fixture,proposal)
    assert rows(q)==[]


def test_pause_preparation_does_not_refund_previous_generic_attempt(fixture):
    q,task,_,authority,_=fixture
    claimed=q.claim('worker');q.checkpoint(task['task_id'],claimed['owner'],'BLOCKED',blocker='account_identity_unverified',release=True)
    current=q.get(task['task_id']); assert current['attempts']==1
    proposal=authority.issue(current['task_id'],current['revision'],SESSION,BROWSER,['0'])
    consume(fixture,proposal);q.pause(task['task_id'])
    assert q.get(task['task_id'])['attempts']==1


def test_activation_and_rollback_guard_refuses_unclosed_context_even_after_service_and_lease_gone(fixture):
    from executor.autonomy.state_compatibility import task_state_guard
    q,_,_,authority,now=fixture
    permit=consume(fixture,offer(fixture));now[0]+=61
    with pytest.raises(BlockingIOError,match='preparation_context_unclosed'):
        with task_state_guard(q.root):pytest.fail('rollback admitted unclosed browser')
    authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
    with task_state_guard(q.root):pass


def absent(binding):return {'status':'ABSENT','browser':binding}


def test_restart_reconciliation_closes_only_proven_absent_original_context(fixture):
    q,task,_,authority,now=fixture
    permit=consume(fixture,offer(fixture))
    replacement=PreparationAuthority(TaskQueue(q.root,clock=lambda:now[0]),session_valid=lambda _:True,clock=lambda:now[0])
    seen=[]
    def observe(binding):seen.append(binding);return absent(binding)
    result=replacement.reconcile_closed_context(permit['nonce_sha'],SESSION,observe)
    assert seen==[BROWSER]
    assert result=={'status':'UNKNOWN_OUTCOME','context_closed':True,'submit_capability':False}
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME' and rows(q)[0]['context_closed']==1
    assert not q.preparation_in_flight()
    with pytest.raises(ValueError):q.resume(task['task_id'])
    assert q.get(task['task_id'])['stage']=='BLOCKED'
    assert not authority.active[permit['nonce_sha']]['plan']==[]
    with pytest.raises(PreparationConflict):authority.guard(permit,SESSION,BROWSER,plan_sha=permit['plan_sha'])


@pytest.mark.parametrize('evidence',[None,True,{'status':'UNKNOWN'}, {'status':'ABSENT'},
    {'status':'ABSENT','browser':dict(BROWSER,context_sha='b'*64)}])
def test_unavailable_or_wrong_context_is_not_absence_proof(fixture,evidence):
    q,_,_,_,now=fixture
    permit=consume(fixture,offer(fixture))
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    with pytest.raises(PreparationConflict):replacement.reconcile_closed_context(permit['nonce_sha'],SESSION,lambda _:evidence)
    assert q.preparation_in_flight() and rows(q)[0]['context_closed']==0


def test_live_in_memory_authority_cannot_use_restart_reconciliation(fixture):
    _,_,_,authority,_=fixture
    permit=consume(fixture,offer(fixture))
    with pytest.raises(PreparationConflict):authority.reconcile_closed_context(permit['nonce_sha'],SESSION,absent)


def test_marker_before_database_rollback_remains_fenced_and_recoverable(fixture,monkeypatch):
    from contextlib import contextmanager
    from executor.autonomy.queue import PREPARATION_MARKER
    q,task,_,authority,now=fixture
    proposal=offer(fixture);original=q.tx
    @contextmanager
    def fail_commit():
        with original() as db:
            yield db
            if db.execute('SELECT 1 FROM preparation_approvals').fetchone():raise OSError('synthetic commit fault')
    monkeypatch.setattr(q,'tx',fail_commit)
    with pytest.raises(OSError):consume(fixture,proposal)
    monkeypatch.setattr(q,'tx',original)
    assert rows(q)==[] and q.preparation_in_flight() and q.claim('worker') is None
    marker=json.loads((q.root/PREPARATION_MARKER).read_text())
    assert marker['browser']==BROWSER
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    result=replacement.reconcile_closed_context(marker['nonce_sha'],SESSION,absent)
    assert result['status']=='UNKNOWN_OUTCOME' and not q.preparation_in_flight()
    assert rows(q)==[] and q.get(task['task_id'])==task


def test_context_proved_closed_then_sqlite_close_rollback_never_replays(fixture,monkeypatch):
    from contextlib import contextmanager
    from executor.autonomy.queue import PREPARATION_MARKER
    q,task,_,authority,now=fixture
    permit=consume(fixture,offer(fixture));original=q.tx
    @contextmanager
    def fail_commit():
        with original() as db:
            yield db
            if db.execute('SELECT 1 FROM preparation_approvals WHERE context_closed=1').fetchone():raise OSError('synthetic close fault')
    monkeypatch.setattr(q,'tx',fail_commit)
    with pytest.raises(OSError):authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
    monkeypatch.setattr(q,'tx',original)
    assert not (q.root/PREPARATION_MARKER).exists() and rows(q)[0]['context_closed']==0
    assert q.preparation_in_flight() and q.claim('worker') is None
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    replacement.reconcile_closed_context(permit['nonce_sha'],SESSION,absent)
    assert not q.preparation_in_flight()
    with pytest.raises(ValueError):q.resume(task['task_id'])


@pytest.mark.parametrize('mutation',['malformed','wrong_nonce','wrong_context','symlink','hardlink','public','directory'])
def test_unrecognized_recovery_marker_is_never_removed(fixture,mutation):
    import os
    from executor.autonomy.queue import PREPARATION_MARKER
    q,_,_,_,now=fixture
    permit=consume(fixture,offer(fixture));marker=q.root/PREPARATION_MARKER
    if mutation=='malformed':marker.write_text('{')
    elif mutation=='wrong_nonce':
        value=json.loads(marker.read_text());value['nonce_sha']='a'*64;marker.write_text(json.dumps(value))
    elif mutation=='wrong_context':
        value=json.loads(marker.read_text());value['browser']['context_sha']='a'*64;marker.write_text(json.dumps(value))
    elif mutation=='symlink':
        actual=q.root/'other';marker.rename(actual);marker.symlink_to(actual)
    elif mutation=='hardlink':os.link(marker,q.root/'other')
    elif mutation=='public':marker.chmod(0o644)
    else:marker.unlink();marker.mkdir()
    before=marker.lstat()
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    with pytest.raises(Exception):replacement.reconcile_closed_context(permit['nonce_sha'],SESSION,absent)
    assert marker.lstat().st_ino==before.st_ino
    assert rows(q)[0]['context_closed']==0 and q.preparation_in_flight()


def test_recovery_context_and_marker_swap_during_observation_is_refused(fixture):
    from executor.autonomy.queue import PREPARATION_MARKER
    q,_,_,_,now=fixture
    permit=consume(fixture,offer(fixture));marker=q.root/PREPARATION_MARKER
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    def changed(binding):
        value=json.loads(marker.read_text());value['nonce_sha']='b'*64;marker.write_text(json.dumps(value))
        return absent(binding)
    with pytest.raises(PreparationConflict):replacement.reconcile_closed_context(permit['nonce_sha'],SESSION,changed)
    assert json.loads(marker.read_text())['nonce_sha']=='b'*64
    assert rows(q)[0]['context_closed']==0


@pytest.mark.parametrize('phase',['create','retire'])
def test_runtime_root_swap_cannot_write_or_remove_outside_marker(fixture,monkeypatch,phase):
    import os
    from executor.autonomy.queue import PREPARATION_MARKER
    from executor.preparation import authority as module
    q,_,_,authority,_=fixture
    proposal=offer(fixture)
    permit=consume(fixture,proposal) if phase=='retire' else None
    outside=q.root.parent/'outside';outside.mkdir(mode=0o700)
    canary=outside/PREPARATION_MARKER;canary.write_text('OUTSIDE_CANARY');canary.chmod(0o600)
    original_root=q.root.parent/'moved-state';swapped=False
    original_open,original_rename=os.open,os.rename
    def swap():
        nonlocal swapped
        swapped=True;original_rename(q.root,original_root);q.root.symlink_to(outside,target_is_directory=True)
    def opening(path,*args,**kwargs):
        if phase=='create' and path==PREPARATION_MARKER and not swapped:swap()
        return original_open(path,*args,**kwargs)
    def renaming(src,dst,*args,**kwargs):
        if phase=='retire' and src==PREPARATION_MARKER and not swapped:swap()
        return original_rename(src,dst,*args,**kwargs)
    monkeypatch.setattr(module.os,'open',opening);monkeypatch.setattr(module.os,'rename',renaming)
    try:
        with pytest.raises(Exception):
            if phase=='create':consume(fixture,proposal)
            else:authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
        assert swapped and canary.read_text()=='OUTSIDE_CANARY'
        assert set(path.name for path in outside.iterdir())=={PREPARATION_MARKER}
    finally:
        monkeypatch.setattr(module.os,'open',original_open);monkeypatch.setattr(module.os,'rename',original_rename)
        if swapped:q.root.unlink();original_rename(original_root,q.root)
    assert q.preparation_in_flight()


def test_marker_replacement_at_retirement_is_preserved_and_refuses_closure(fixture,monkeypatch):
    import os
    from executor.autonomy.queue import PREPARATION_MARKER
    from executor.preparation import authority as module
    q,_,_,authority,_=fixture
    permit=consume(fixture,offer(fixture));original=os.rename;swapped=False
    def replace(src,dst,*args,**kwargs):
        nonlocal swapped
        if src==PREPARATION_MARKER and not swapped:
            swapped=True
            original(src,'original-marker',src_dir_fd=kwargs['src_dir_fd'],dst_dir_fd=kwargs['src_dir_fd'])
            fd=os.open(PREPARATION_MARKER,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=kwargs['src_dir_fd'])
            try:os.write(fd,b'UNRECOGNIZED_CANARY')
            finally:os.close(fd)
        return original(src,dst,*args,**kwargs)
    monkeypatch.setattr(module.os,'rename',replace)
    with pytest.raises(PreparationConflict):authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=True)
    assert swapped and any(path.read_bytes()==b'UNRECOGNIZED_CANARY' for path in q.root.glob('preparation-closed-*'))
    assert rows(q)[0]['context_closed']==0 and q.preparation_in_flight()


def test_recovered_orphan_nonce_has_durable_tombstone_against_old_pending_offer(fixture,monkeypatch):
    from contextlib import contextmanager
    from executor.autonomy.queue import PREPARATION_MARKER
    q,_,_,authority,now=fixture
    proposal=offer(fixture);original=q.tx
    @contextmanager
    def fail_commit():
        with original() as db:
            yield db
            if db.execute('SELECT 1 FROM preparation_approvals').fetchone():raise OSError('synthetic commit failure')
    monkeypatch.setattr(q,'tx',fail_commit)
    with pytest.raises(OSError):consume(fixture,proposal)
    monkeypatch.setattr(q,'tx',original)
    marker=json.loads((q.root/PREPARATION_MARKER).read_text())
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    replacement.reconcile_closed_context(marker['nonce_sha'],SESSION,absent)
    assert marker['nonce_sha'] in authority.pending
    with pytest.raises(PreparationConflict):consume(fixture,proposal)
    with q.tx() as db:
        tombstone=dict(db.execute('SELECT * FROM preparation_nonce_tombstones').fetchone())
    assert tombstone['nonce_sha']==marker['nonce_sha'] and tombstone['outcome']=='RECOVERED_ORPHAN'
    assert rows(q)==[]


def test_orphan_same_inode_same_length_mutation_restores_refusal_after_retirement(fixture,monkeypatch):
    from contextlib import contextmanager
    from executor.autonomy.queue import PREPARATION_MARKER
    from executor.preparation import authority as module
    q,_,_,authority,now=fixture
    proposal=offer(fixture);original_tx=q.tx
    @contextmanager
    def fail_commit():
        with original_tx() as db:
            yield db
            if db.execute('SELECT 1 FROM preparation_approvals').fetchone():raise OSError('fault')
    monkeypatch.setattr(q,'tx',fail_commit)
    with pytest.raises(OSError):consume(fixture,proposal)
    monkeypatch.setattr(q,'tx',original_tx)
    marker=q.root/PREPARATION_MARKER;data=json.loads(marker.read_text());original_rename=module.os.rename
    def mutate(src,dst,*args,**kwargs):
        if src==PREPARATION_MARKER:
            raw=marker.read_bytes();marker.write_bytes(raw.replace(data['nonce_sha'].encode(),b'b'*64))
        return original_rename(src,dst,*args,**kwargs)
    monkeypatch.setattr(module.os,'rename',mutate)
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    with pytest.raises(PreparationConflict):replacement.reconcile_closed_context(data['nonce_sha'],SESSION,absent)
    assert marker.read_text()=='UNRECOGNIZED_MARKER\n'
    assert q.preparation_in_flight() and q.claim('worker') is None
    assert rows(q)==[]


def test_primitive_bridge_is_durable_value_free_and_late_cancel_revokes_all_evidence(fixture):
    from executor.preparation.authority import PreparationJournal
    q,task,_,authority,_=fixture
    permit=consume(fixture,offer(fixture))
    journal=PreparationJournal(authority,permit,SESSION,BROWSER)
    first=journal.before('0');journal.after(first,'READBACK_VERIFIED')
    second=journal.before('8');journal.after(second,'READBACK_VERIFIED')
    assert len(q.field_actions(task['task_id']))==2
    assert {row['outcome'] for row in q.field_actions(task['task_id'])}=={'DOM_READBACK_UNVERIFIED'}
    q.cancel(task['task_id']);journal.invalidate()
    assert {row['outcome'] for row in q.field_actions(task['task_id'])}=={'UNKNOWN_OUTCOME'}
    assert q.run_attempts(task['task_id'])[0]['outcome']=='UNKNOWN_OUTCOME'
    encoded=json.dumps(rows(q)+q.field_actions(task['task_id'])+q.run_attempts(task['task_id']))
    assert 'PRIVATE_AUTHORITY_CANARY' not in encoded and SESSION not in encoded
    with pytest.raises(PreparationConflict):journal.before('0')


def test_primitive_bridge_rejects_unapproved_field_and_never_means_ready(fixture):
    from executor.preparation.authority import PreparationJournal
    q,task,_,authority,_=fixture
    permit=consume(fixture,offer(fixture))
    journal=PreparationJournal(authority,permit,SESSION,BROWSER)
    for field_id in ('12','13','11','ValidateCode','protocol','Submit','5'):
        with pytest.raises(PreparationConflict):journal.before(field_id)
    action=journal.before('0');journal.after(action,'READBACK_VERIFIED')
    action=journal.before('8');journal.after(action,'READBACK_VERIFIED')
    journal.complete()
    assert q.run_attempts(task['task_id'])[0]['outcome']=='RETURNED_UNVERIFIED'
    assert q.get(task['task_id'])['stage']=='BLOCKED'
    assert q.preparation_in_flight()


def test_orphan_rename_effect_then_error_restores_active_refusal(fixture,monkeypatch):
    from contextlib import contextmanager
    from executor.autonomy.queue import PREPARATION_MARKER
    from executor.preparation import authority as module
    q,_,_,_,now=fixture
    proposal=offer(fixture);original_tx=q.tx
    @contextmanager
    def fail_commit():
        with original_tx() as db:
            yield db
            if db.execute('SELECT 1 FROM preparation_approvals').fetchone():raise OSError('fault')
    monkeypatch.setattr(q,'tx',fail_commit)
    with pytest.raises(OSError):consume(fixture,proposal)
    monkeypatch.setattr(q,'tx',original_tx)
    marker=q.root/PREPARATION_MARKER;data=json.loads(marker.read_text());original_rename=module.os.rename
    def effect_then_error(*args,**kwargs):
        original_rename(*args,**kwargs);raise OSError('uncertain rename')
    monkeypatch.setattr(module.os,'rename',effect_then_error)
    replacement=PreparationAuthority(q,session_valid=lambda _:True,clock=lambda:now[0])
    with pytest.raises(OSError):replacement.reconcile_closed_context(data['nonce_sha'],SESSION,absent)
    assert marker.read_text()=='UNRECOGNIZED_MARKER\n'
    assert q.preparation_in_flight() and rows(q)==[]


def test_one_approval_can_never_create_second_attempt_after_clean_journal_return(fixture):
    from executor.preparation.authority import PreparationJournal
    q,task,_,authority,_=fixture
    permit=consume(fixture,offer(fixture));first=PreparationJournal(authority,permit,SESSION,BROWSER)
    action=first.before('0');first.after(action,'READBACK_VERIFIED')
    action=first.before('8');first.after(action,'READBACK_VERIFIED');first.complete()
    with pytest.raises(PreparationConflict):PreparationJournal(authority,permit,SESSION,BROWSER)
    assert len(q.run_attempts(task['task_id']))==1
    assert rows(q)[0]['attempt_id']==first.attempt_id


def test_competing_journal_constructors_share_one_durable_attempt(fixture):
    from executor.preparation.authority import PreparationJournal
    q,task,_,authority,_=fixture
    permit=consume(fixture,offer(fixture));barrier=threading.Barrier(2);accepted=[]
    def start():
        barrier.wait()
        try:accepted.append(PreparationJournal(authority,permit,SESSION,BROWSER))
        except PreparationConflict:pass
    threads=[threading.Thread(target=start),threading.Thread(target=start)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert len(accepted)==1 and all(not thread.is_alive() for thread in threads)
    assert len(q.run_attempts(task['task_id']))==1


def test_partial_or_absent_journal_can_never_claim_prepared(fixture):
    from executor.preparation.authority import PreparationJournal
    q,_,_,authority,_=fixture
    permit=consume(fixture,offer(fixture));journal=PreparationJournal(authority,permit,SESSION,BROWSER)
    action=journal.before('0');journal.after(action,'READBACK_VERIFIED')
    with pytest.raises(PreparationConflict):journal.complete()
    with pytest.raises(PreparationConflict):journal.before('0')
    result=authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=True,
                            session=SESSION,browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    assert result['status']=='UNKNOWN_OUTCOME'
    assert {row['outcome'] for row in q.field_actions(permit['task_id'])}=={'UNKNOWN_OUTCOME'}


def test_activation_first_blocks_cross_instance_admission_without_consumption(fixture):
    from executor.autonomy.state_compatibility import task_state_guard
    q,task,_,_,now=fixture
    other=TaskQueue(q.root,clock=lambda:now[0])
    authority=PreparationAuthority(other,session_valid=lambda _:True,clock=lambda:now[0])
    proposed=authority.issue(task['task_id'],task['revision'],SESSION,BROWSER,['0'])
    with task_state_guard(q.root):
        with pytest.raises(PreparationConflict):
            authority.consume(proposed['nonce'],SESSION,proposed['scope_sha'],BROWSER,approve_transmission=True)
        assert rows(q)==[] and not q.preparation_in_flight()
    permit=authority.consume(proposed['nonce'],SESSION,proposed['scope_sha'],BROWSER,approve_transmission=True)
    assert rows(q)[0]['nonce_sha']==permit['nonce_sha']


def test_admission_lock_and_then_marker_both_exclude_activation(fixture,monkeypatch):
    from executor.autonomy.state_compatibility import task_state_guard
    from executor.preparation import authority as module
    q,_,_,_,_=fixture
    proposed=offer(fixture);original=module._create_marker;observed=[]
    def create(*args,**kwargs):
        # Admission holds migration.lock even BEFORE marker exists.
        assert not (q.root / "preparation-context.active").exists()
        with pytest.raises(BlockingIOError):
            with task_state_guard(q.root):pytest.fail('activation entered during admission')
        observed.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(module,'_create_marker',create)
    consume(fixture,proposed)
    assert observed==[True]
    with pytest.raises(BlockingIOError):
        with task_state_guard(q.root):pytest.fail('activation ignored durable context fence')


def test_retired_session_may_only_revoke_its_own_exact_offer(fixture):
    _,task,_,authority,_=fixture
    offer=authority.issue(task['task_id'],task['revision'],SESSION,BROWSER,['0'])
    authority.session_valid=lambda _:False
    authority.revoke(offer['nonce'],'x'*40)
    assert len(authority.pending)==1
    authority.revoke('other-nonce',SESSION)
    assert len(authority.pending)==1
    authority.revoke(offer['nonce'],SESSION)
    assert authority.pending=={}
