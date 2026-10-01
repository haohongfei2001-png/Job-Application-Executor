"""Unregistered sole-resume authority; no file selector or network sender.

A fresh explicit approval covers the exact original resume/destination metadata.
Three finite source-shaped request slots are consumed before any network call.
No expired field-writing permit is renewed, and no unknown upload is replayable.
"""
from __future__ import annotations

import copy
import secrets

from .authority import PreparationConflict,_sha,HEX
from .final_journal import _prepared_task
from .qiyunfang import digest

UPLOAD_URL='https://www.qiyunfang.com/ajax/advanceUpload.jsp'
STAGES=('selection_lookup','start_lookup','upload')
TTL=120


class ResumeUploadJournal:
    def __repr__(self):return '<ResumeUploadJournal>'

    def __init__(self,authority,permit,session,material,*,still_authorized,process_guard):
        if not callable(still_authorized) or not callable(process_guard):raise PreparationConflict()
        self.authority,self.queue,self.permit,self.session=authority,authority.queue,copy.deepcopy(permit),session
        self.material,self.still_authorized,self.process_guard=material,still_authorized,process_guard
        self.intent=self.stage=None
        self._nonce=secrets.token_urlsafe(32);self._deadline=authority.clock()+TTL
        with self.queue.tx() as db:
            row=db.execute('SELECT attempt_id FROM preparation_approvals WHERE nonce_sha=?',(permit.get('nonce_sha'),)).fetchone()
            if row is None or not row['attempt_id']:raise PreparationConflict()
            self.attempt_id=row['attempt_id']
        self.review=material.private_review()
        self.scope={'preparation_nonce_sha':permit['nonce_sha'],'attempt_id':self.attempt_id,
                    'resume':copy.deepcopy(self.review),'purpose':'resume_upload','recipient':UPLOAD_URL}
        self.scope_sha=digest(self.scope)
        self.guard()

    def _private(self):
        self.authority._session(self.session)
        if (self.still_authorized() is not True or self.authority.clock()>=self._deadline
                or self.authority.active.get(self.permit.get('nonce_sha'))!=self.permit
                or self.permit.get('authority_sha')!=self.authority.instance_sha
                or self.permit.get('session_sha')!=_sha(self.session)
                or self.process_guard()!=self.permit['browser']['process_sha']):
            raise PreparationConflict()
        # No page/DOM/CDP RPC is allowed here: lookup XHR is synchronous inside
        # set_input_files. The supplied process guard is OS-only observation.
        self.material._fence_files()
        if (self.review!=self.material._metadata() or self.scope['resume']!=self.review
                or digest(self.scope)!=self.scope_sha):raise PreparationConflict()

    def _rows(self,db,allowed={'ATTEMPTED'}):
        _prepared_task(db,self.permit,self.attempt_id)
        if db.execute('SELECT 1 FROM preparation_final_requests WHERE preparation_nonce_sha=?',
                      (self.permit['nonce_sha'],)).fetchone():raise PreparationConflict()
        row=db.execute('SELECT * FROM preparation_resume_uploads WHERE preparation_nonce_sha=?',
                       (self.permit['nonce_sha'],)).fetchone()
        if self.intent is None:
            if row is not None:raise PreparationConflict()
        elif (row is None or row['intent_sha']!=self.intent or row['scope_sha']!=self.scope_sha
                or row['task_id']!=self.permit['task_id'] or row['task_revision']!=self.permit['task_revision']
                or row['attempt_id']!=self.attempt_id or row['resume_sha']!=self.review['resume_sha256']
                or row['outcome'] not in allowed):raise PreparationConflict()
        if self.intent is not None and db.execute(
                "SELECT 1 FROM preparation_resume_stages WHERE upload_intent_sha=? AND outcome='UNKNOWN_OUTCOME'",
                (self.intent,)).fetchone():raise PreparationConflict()

    def guard(self,stage=None):
        self._private();self.material.fence()
        with self.queue.tx() as db:
            self._rows(db)
            if stage is not None:
                row=db.execute('SELECT * FROM preparation_resume_stages WHERE stage_sha=?',(stage,)).fetchone()
                if (stage!=self.stage or row is None or row['upload_intent_sha']!=self.intent
                        or row['outcome']!='ATTEMPTED'):raise PreparationConflict()
        self._private()
        return True

    def private_offer(self):
        if self.intent is not None:raise PreparationConflict()
        self.guard()
        return {'nonce':self._nonce,'scope_sha':self.scope_sha,'resume':copy.deepcopy(self.review),
                'recipient':UPLOAD_URL,'expires_in_seconds':max(0,int(self._deadline-self.authority.clock())),
                'warning':'将上传当前任务这份原始简历及文件类型、大小和临时上传标识。文件名将按类型规范为resume.pdf或resume.docx；不转换文件内容。这不授权证件、验证码、协议或最终提交。',
                'submit_capability':False}

    def approve(self,nonce,scope_sha,*,approve_upload):
        if (self.intent is not None or approve_upload is not True or nonce!=self._nonce
                or scope_sha!=self.scope_sha):raise PreparationConflict()
        self.guard();intent=digest({'nonce_sha':_sha(nonce),'scope_sha':self.scope_sha})
        with self.authority.lock,self.queue.tx() as db:
            self._private();self._rows(db)
            now=self.queue.clock()
            db.execute('INSERT INTO preparation_resume_uploads (intent_sha,preparation_nonce_sha,attempt_id,task_id,task_revision,resume_sha,scope_sha,outcome,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (intent,self.permit['nonce_sha'],self.attempt_id,self.permit['task_id'],self.permit['task_revision'],
                        self.review['resume_sha256'],self.scope_sha,'ATTEMPTED',now,now))
            self._private()
        self.intent=intent
        # Cancellation after commit cannot be mistaken for returned authority.
        # The durable slot remains consumed even if this fresh fence rejects it.
        self.guard()
        return intent

    def begin_selection(self):
        """One durable file-input primitive, even across broker reconstruction."""
        if self.intent is None:raise PreparationConflict()
        self.guard()
        with self.queue.tx() as db:
            self._private();self._rows(db)
            changed=db.execute('UPDATE preparation_resume_uploads SET selection_attempted=1,updated=? WHERE intent_sha=? AND selection_attempted=0',
                               (self.queue.clock(),self.intent))
            if changed.rowcount!=1:raise PreparationConflict()
            self._private()
        self.guard()

    def begin_stage(self,name):
        if name not in STAGES or self.intent is None:raise PreparationConflict()
        self.guard();ordinal=STAGES.index(name)
        stage=digest({'intent':self.intent,'ordinal':ordinal})
        with self.queue.tx() as db:
            self._private();self._rows(db)
            if db.execute('SELECT selection_attempted FROM preparation_resume_uploads WHERE intent_sha=?',(self.intent,)).fetchone()[0]!=1:
                raise PreparationConflict()
            rows=db.execute('SELECT ordinal,outcome FROM preparation_resume_stages WHERE upload_intent_sha=? ORDER BY ordinal',
                            (self.intent,)).fetchall()
            if ([row['ordinal'] for row in rows]!=list(range(ordinal))
                    or any(row['outcome']!='RETURNED_UNVERIFIED' for row in rows)):
                raise PreparationConflict()
            now=self.queue.clock()
            db.execute('INSERT INTO preparation_resume_stages VALUES(?,?,?,?,?,?)',
                       (stage,self.intent,ordinal,'ATTEMPTED',now,now))
            self._private()
        self.stage=stage;self.guard(stage)
        return stage

    def record_stage(self,stage,outcome):
        if outcome not in {'RETURNED_UNVERIFIED','UNKNOWN_OUTCOME'}:raise PreparationConflict()
        if outcome=='RETURNED_UNVERIFIED':
            try:self.guard(stage)
            except Exception:outcome='UNKNOWN_OUTCOME'
        with self.queue.tx() as db:
            row=db.execute('SELECT * FROM preparation_resume_stages WHERE stage_sha=?',(stage,)).fetchone()
            if row is None or row['upload_intent_sha']!=self.intent:raise PreparationConflict()
            if row['outcome']=='UNKNOWN_OUTCOME':outcome='UNKNOWN_OUTCOME'
            if outcome=='RETURNED_UNVERIFIED':
                try:self._private();self._rows(db)
                except Exception:outcome='UNKNOWN_OUTCOME'
            db.execute('UPDATE preparation_resume_stages SET outcome=?,updated=? WHERE stage_sha=?',
                       (outcome,self.queue.clock(),stage))
            if outcome=='UNKNOWN_OUTCOME':
                db.execute("UPDATE preparation_resume_uploads SET outcome='UNKNOWN_OUTCOME',updated=? WHERE intent_sha=?",
                           (self.queue.clock(),self.intent))
        return outcome

    def finish(self,outcome):
        if self.intent is None or outcome not in {'RETURNED_UNVERIFIED','UNKNOWN_OUTCOME'}:raise PreparationConflict()
        if outcome=='RETURNED_UNVERIFIED':
            try:self.guard()
            except Exception:outcome='UNKNOWN_OUTCOME'
        with self.queue.tx() as db:
            row=db.execute('SELECT * FROM preparation_resume_uploads WHERE intent_sha=?',(self.intent,)).fetchone()
            if row is None or row['preparation_nonce_sha']!=self.permit['nonce_sha']:raise PreparationConflict()
            stages=db.execute('SELECT ordinal,outcome FROM preparation_resume_stages WHERE upload_intent_sha=? ORDER BY ordinal',
                              (self.intent,)).fetchall()
            if (row['outcome']=='UNKNOWN_OUTCOME' or row['selection_attempted']!=1 or [row['ordinal'] for row in stages]!=[0,1,2]
                    or any(row['outcome']!='RETURNED_UNVERIFIED' for row in stages)):outcome='UNKNOWN_OUTCOME'
            if outcome=='RETURNED_UNVERIFIED':
                try:self._private();self._rows(db)
                except Exception:outcome='UNKNOWN_OUTCOME'
            now=self.queue.clock()
            db.execute('UPDATE preparation_resume_uploads SET outcome=?,updated=? WHERE intent_sha=?',(outcome,now,self.intent))
            if outcome=='UNKNOWN_OUTCOME':
                db.execute("UPDATE preparation_resume_stages SET outcome='UNKNOWN_OUTCOME',updated=? WHERE upload_intent_sha=? AND outcome='ATTEMPTED'",
                           (now,self.intent))
        return outcome
