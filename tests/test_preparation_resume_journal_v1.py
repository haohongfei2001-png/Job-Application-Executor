"""One original PDF/DOCX, one approved upload, three non-replayable stages."""
import copy
import json
import threading
import pytest
from executor.preparation.authority import PreparationJournal,PreparationConflict
from executor.preparation.resume_material import retained_resume
from executor.preparation.resume_journal import ResumeUploadJournal,STAGES
from executor.preparation.final_journal import PreparationFinalJournal
from test_preparation_resume_material_v1 import resume,fixture
from test_preparation_authority_v1 import SESSION,BROWSER


@pytest.fixture
def ready(resume):
    (q,task,profile,authority,now),permit,path,data,kind=resume
    journal=PreparationJournal(authority,permit,SESSION,BROWSER)
    for key in ['0','8']:
        action=journal.before(key);journal.after(action,'READBACK_VERIFIED')
    journal.complete()
    authority.finish(permit,'PREPARED_UNVERIFIED',context_closed=False,session=SESSION,browser_binding=BROWSER,plan_sha=permit['plan_sha'])
    valid=[True]
    context=retained_resume(authority,permit,SESSION);material=context.__enter__()
    upload=ResumeUploadJournal(authority,permit,SESSION,material,still_authorized=lambda:valid[0],
                               process_guard=lambda:BROWSER['process_sha'])
    try:yield resume,material,upload,valid
    finally:
        # Tests may intentionally invalidate retained sources. Close via an
        # exceptional consumer exit, as an owner does after a failed guard.
        context.__exit__(RuntimeError,RuntimeError('synthetic fixture closure'),None)


def rows(q,table='preparation_resume_uploads'):
    with q.tx() as db:return [dict(row) for row in db.execute('SELECT * FROM '+table)]


def approve(upload,select=True):
    offer=upload.private_offer();intent=upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
    if select:upload.begin_selection()
    return intent


def test_one_upload_is_durable_value_free_and_three_stages_are_consumed_before_effect(ready):
    ((q,task,_,_,_),_,path,_,_),_,upload,_=ready
    offer=upload.private_offer();assert rows(q)==[] and not offer['submit_capability']
    approve(upload);assert rows(q)[0]['outcome']=='ATTEMPTED'
    for ordinal,name in enumerate(STAGES):
        stage=upload.begin_stage(name)
        assert rows(q,'preparation_resume_stages')[-1]['outcome']=='ATTEMPTED'
        assert upload.guard(stage)
        assert upload.record_stage(stage,'RETURNED_UNVERIFIED')=='RETURNED_UNVERIFIED'
        with pytest.raises(PreparationConflict):upload.begin_stage(name)
    assert upload.finish('RETURNED_UNVERIFIED')=='RETURNED_UNVERIFIED'
    assert len(rows(q,'preparation_resume_stages'))==3 and q.preparation_in_flight()
    encoded=json.dumps(rows(q)+rows(q,'preparation_resume_stages'))
    assert str(path) not in encoded and SESSION not in encoded and 'SYNTHETIC' not in encoded
    with pytest.raises(PreparationConflict):approve(upload)


@pytest.mark.parametrize('change',['cancel','session','file','process','expiry'])
def test_drift_before_approval_never_consumes_an_upload(ready,change):
    ((q,task,_,authority,now),_,path,_,_),_,upload,valid=ready
    if change=='cancel':q.cancel(task['task_id'])
    elif change=='session':valid[0]=False
    elif change=='file':path.write_bytes(b'changed')
    elif change=='process':upload.process_guard=lambda:'0'*64
    else:now[0]+=121
    with pytest.raises((PreparationConflict,RuntimeError)):approve(upload)
    assert rows(q)==[]


def test_unknown_stage_revokes_send_and_new_nonce_cannot_refund_slot(ready):
    ((q,_,_,authority,_),permit,_,_,_),material,upload,_=ready
    approve(upload);stage=upload.begin_stage('selection_lookup')
    assert upload.record_stage(stage,'UNKNOWN_OUTCOME')=='UNKNOWN_OUTCOME'
    with pytest.raises(PreparationConflict):upload.guard(stage)
    with pytest.raises(PreparationConflict):upload.begin_stage('start_lookup')
    with pytest.raises(PreparationConflict):ResumeUploadJournal(authority,permit,SESSION,material,
        still_authorized=lambda:True,process_guard=lambda:BROWSER['process_sha'])
    assert upload.record_stage(stage,'RETURNED_UNVERIFIED')=='UNKNOWN_OUTCOME'
    assert upload.finish('RETURNED_UNVERIFIED')=='UNKNOWN_OUTCOME'
    assert len(rows(q))==1


def test_final_slot_requires_complete_original_resume_stages(ready):
    ((_,_,_,authority,_),permit,_,_,_),_,upload,_=ready
    observe=lambda:{'browser':dict(BROWSER),'frame_id':'f'*32,'change_epoch':7}
    with pytest.raises(PreparationConflict):PreparationFinalJournal(authority,permit,SESSION,observe)
    approve(upload)
    with pytest.raises(PreparationConflict):PreparationFinalJournal(authority,permit,SESSION,observe)
    for name in STAGES:
        stage=upload.begin_stage(name);upload.record_stage(stage,'RETURNED_UNVERIFIED')
    upload.finish('RETURNED_UNVERIFIED')
    assert PreparationFinalJournal(authority,permit,SESSION,observe).guard()


def test_two_journals_race_for_one_upload_slot(ready):
    ((q,_,_,authority,_),permit,_,_,_),material,first,_=ready
    second=ResumeUploadJournal(authority,permit,SESSION,material,still_authorized=lambda:True,
                               process_guard=lambda:BROWSER['process_sha'])
    barrier=threading.Barrier(2);accepted=[]
    def run(value):
        offer=value.private_offer();barrier.wait()
        try:accepted.append(value.approve(offer['nonce'],offer['scope_sha'],approve_upload=True))
        except PreparationConflict:pass
    threads=[threading.Thread(target=run,args=(value,)) for value in [first,second]]
    for thread in threads:thread.start()
    for thread in threads:thread.join(3)
    assert all(not thread.is_alive() for thread in threads) and len(accepted)==1 and len(rows(q))==1


def test_uncertain_close_downgrades_attempted_upload_and_stage_without_refunding(ready):
    ((q,_,_,authority,_),permit,_,_,_),_,upload,_=ready
    approve(upload);stage=upload.begin_stage('selection_lookup')
    authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=False)
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME'
    assert rows(q,'preparation_resume_stages')[0]['outcome']=='UNKNOWN_OUTCOME'
    assert q.preparation_in_flight()
    with pytest.raises(PreparationConflict):upload.guard(stage)


def test_selection_intent_is_durable_and_cannot_be_reconstructed_or_repeated(ready):
    ((q,_,_,_,_),_,_,_,_),_,upload,_=ready
    approve(upload,select=False)
    with pytest.raises(PreparationConflict):upload.begin_stage('selection_lookup')
    assert rows(q,'preparation_resume_stages')==[]
    upload.begin_selection()
    assert rows(q)[0]['selection_attempted']==1
    with pytest.raises(PreparationConflict):upload.begin_selection()
    stage=upload.begin_stage('selection_lookup');assert upload.guard(stage)


@pytest.mark.parametrize('point',['approve','selection','stage'])
def test_commit_acknowledgement_loss_never_refunds_upload_selection_or_stage(ready,point,monkeypatch):
    from contextlib import contextmanager
    ((q,_,_,authority,_),permit,_,_,_),_,upload,_=ready
    if point in {'selection','stage'}:approve(upload,select=(point=='stage'))
    original=q.tx;triggered=[False]
    @contextmanager
    def lost_commit():
        with original() as db:
            baseline=db.total_changes;yield db;changed=db.total_changes>baseline
        if changed and not triggered[0]:
            triggered[0]=True;raise OSError('synthetic acknowledgement loss')
    monkeypatch.setattr(q,'tx',lost_commit)
    with pytest.raises(OSError):
        if point=='approve':approve(upload)
        elif point=='selection':upload.begin_selection()
        else:upload.begin_stage('selection_lookup')
    monkeypatch.setattr(q,'tx',original)
    assert len(rows(q))==1
    with pytest.raises(PreparationConflict):
        if point=='approve':approve(upload)
        elif point=='selection':upload.begin_selection()
        else:upload.begin_stage('selection_lookup')
    assert q.preparation_in_flight()
    authority.finish(permit,'UNKNOWN_OUTCOME',context_closed=False)
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME'
    if point=='stage':assert rows(q,'preparation_resume_stages')[0]['outcome']=='UNKNOWN_OUTCOME'
