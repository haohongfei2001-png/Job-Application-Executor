"""Value-free sole-final-slot CAS for the unregistered human request bridge.

This journal cannot accept a dialog or send any network request. It can only
consume the original preparation's sole final slot or conservatively downgrade
its outcome. No restart, changed request nonce or new bridge refunds that slot.
"""
from __future__ import annotations

import copy
import hmac
import json
import re

from .authority import PreparationConflict, _material, _sha, _primitive_fields, HEX
from .qiyunfang import CONTRACT_VERSION, CONTRACT_ROLE, digest
from .human_request import FINAL_URL


def _prepared_task(db,permit,attempt_id):
    """Read-only retained preparation proof, without renewing field authority."""
    row=db.execute('SELECT * FROM preparation_approvals WHERE nonce_sha=?',(permit['nonce_sha'],)).fetchone()
    task=db.execute('SELECT * FROM tasks WHERE task_id=?',(permit['task_id'],)).fetchone()
    if (row is None or row['context_closed'] or row['outcome']!='PREPARED_UNVERIFIED'
            or row['owner']!=permit['owner'] or row['scope_sha']!=permit['scope_sha']
            or row['plan_sha']!=permit['plan_sha'] or row['session_sha']!=permit['session_sha']
            or row['attempt_id']!=attempt_id or task is None
            or task['owner']!=permit['owner'] or task['revision']!=permit['task_revision']
            or task['stage']!='BLOCKED' or task['blocker']!='anonymous_preparation_unverified'
            or digest(json.loads(task['spec']))!=permit['spec_sha']
            or db.execute('SELECT 1 FROM profile_write_barriers WHERE profile_ref=?',
                          (json.loads(task['spec'])['profile_ref'],)).fetchone()):
        raise PreparationConflict()
    run=db.execute('SELECT * FROM run_attempts WHERE attempt_id=?',(attempt_id,)).fetchone()
    actions=db.execute('SELECT field_sha256,outcome FROM field_actions WHERE attempt_id=?',(attempt_id,)).fetchall()
    expected={digest({'scope_sha':permit['scope_sha'],'field_id':key,'ordinal':index})
              for index,key in enumerate(_primitive_fields(permit['plan']))}
    if (run is None or run['owner']!=permit['owner'] or run['task_id']!=permit['task_id']
            or run['outcome']!='RETURNED_UNVERIFIED' or len(actions)!=len(expected)
            or {action['field_sha256'] for action in actions}!=expected
            or any(action['outcome']!='DOM_READBACK_UNVERIFIED' for action in actions)):
        raise PreparationConflict()
    return task



class PreparationFinalJournal:
    def __init__(self, authority, permit, session, observe):
        if not callable(observe):raise PreparationConflict()
        self.authority,self.queue=authority,authority.queue
        self.permit,self.session=copy.deepcopy(permit),session
        self.observe=observe
        self.intent=self.scope_sha=self.request_nonce_sha=None
        with self.queue.tx() as db:
            row=db.execute("SELECT attempt_id FROM preparation_approvals WHERE nonce_sha=?",(self.permit.get("nonce_sha"),)).fetchone()
            if row is None or not row["attempt_id"]:raise PreparationConflict()
            self.attempt_id=row["attempt_id"]
        self.binding=self._observe()
        self.guard()

    def _observe(self):
        value=self.observe()
        if (not isinstance(value,dict) or set(value)!={'browser','frame_id','change_epoch'}
                or type(value['change_epoch']) is not int or value['change_epoch']<0
                or not isinstance(value['frame_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',value['frame_id'])
                or self.authority._browser(value['browser'])!=self.permit['browser']):
            raise PreparationConflict()
        return copy.deepcopy(value)

    def _private(self):
        permit=self.permit;authority=self.authority
        authority._session(self.session)
        if (authority.active.get(permit.get('nonce_sha'))!=permit
                or permit['authority_sha']!=authority.instance_sha
                or not hmac.compare_digest(permit['session_sha'],_sha(self.session))):
            raise PreparationConflict()

    def _rows(self,db):
        task=_prepared_task(db,self.permit,self.attempt_id)
        upload=db.execute('SELECT * FROM preparation_resume_uploads WHERE preparation_nonce_sha=?',
                          (self.permit['nonce_sha'],)).fetchone()
        if upload is not None or self.permit['resume'].get('version') is not None:
            if (upload is None or upload['outcome']!='RETURNED_UNVERIFIED' or upload['selection_attempted']!=1
                    or upload['attempt_id']!=self.attempt_id or upload['task_id']!=self.permit['task_id']
                    or upload['task_revision']!=self.permit['task_revision']
                    or upload['resume_sha']!=self.permit['resume']['version']):raise PreparationConflict()
            stages=db.execute('SELECT ordinal,outcome FROM preparation_resume_stages WHERE upload_intent_sha=? ORDER BY ordinal',
                              (upload['intent_sha'],)).fetchall()
            if ([stage['ordinal'] for stage in stages]!=[0,1,2]
                    or any(stage['outcome']!='RETURNED_UNVERIFIED' for stage in stages)):raise PreparationConflict()
        return task

    def _material_matches(self,report):
        if report['profile']['version']!=self.permit['profile_sha'] or report['resume']!=self.permit['resume']:
            raise PreparationConflict()

    def _guard(self, allowed):
        # A new human action does not renew the expired field-writing permit.
        # The separate unclosed context fence still excludes every worker/update.
        # Cancellation clears owner/stage and is never revived here.
        self._private()
        with _material(self.queue,self.permit['task_id'],self.permit['task_revision']) as (_,report,_,fence):
            self._material_matches(report)
            if self._observe()!=self.binding:raise PreparationConflict()
            with self.queue.tx() as db:
                self._rows(db)
                row=db.execute('SELECT * FROM preparation_final_requests WHERE preparation_nonce_sha=?',
                               (self.permit['nonce_sha'],)).fetchone()
                if self.intent is None:
                    if row is not None:raise PreparationConflict()
                elif (row is None or row['intent_sha']!=self.intent or row['scope_sha']!=self.scope_sha
                        or row['request_nonce_sha']!=self.request_nonce_sha or row['attempt_id']!=self.attempt_id
                        or row['task_id']!=self.permit['task_id'] or row['task_revision']!=self.permit['task_revision']
                        or row['outcome'] not in allowed):raise PreparationConflict()
            self._private();fence()
        return copy.deepcopy(self.binding)

    def guard(self):
        return self._guard({'ATTEMPTED'})

    def consume(self,scope):
        """Internal callback after a paired human confirmation; never a sender."""
        expected_meta={'contract':CONTRACT_VERSION,'command':'addWafCk_addSubmit','form_id':6,
                       'role':CONTRACT_ROLE,'category':'final_application','change_epoch':self.binding['change_epoch']}
        if (not isinstance(scope,dict) or set(scope)!={'request_nonce_sha','browser','metadata','purpose','recipient'}
                or not isinstance(scope['request_nonce_sha'],str) or not HEX.fullmatch(scope['request_nonce_sha'])
                or scope['browser']!=self.binding or scope['metadata']!=expected_meta
                or scope['purpose']!='final_application' or scope['recipient']!=FINAL_URL):
            raise PreparationConflict()
        scope=copy.deepcopy(scope);self.guard()
        intent=digest({'preparation_nonce_sha':self.permit['nonce_sha'],'scope':scope})
        with self.authority.lock, _material(self.queue,self.permit['task_id'],self.permit['task_revision']) as (_,report,_,fence):
            self._material_matches(report)
            with self.queue.tx() as db:
                self._private();self._rows(db);fence()
                if db.execute('SELECT 1 FROM preparation_final_requests WHERE preparation_nonce_sha=?',
                              (self.permit['nonce_sha'],)).fetchone():raise PreparationConflict()
                now=self.queue.clock()
                db.execute('INSERT INTO preparation_final_requests VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (intent,self.permit['nonce_sha'],self.attempt_id,self.permit['task_id'],self.permit['task_revision'],scope['request_nonce_sha'],
                            digest(scope),'ATTEMPTED',now,now))
                self._private();fence()
        self.intent,self.scope_sha,self.request_nonce_sha=intent,digest(scope),scope['request_nonce_sha']
        return intent

    def record(self,intent,outcome):
        """Revocation may survive cancellation; successful return cannot resurrect it."""
        if (not isinstance(intent,str) or not HEX.fullmatch(intent)
                or outcome not in {'RETURNED_UNVERIFIED','UNKNOWN_OUTCOME'}):raise PreparationConflict()
        if outcome=='RETURNED_UNVERIFIED':
            try:self._guard({'ATTEMPTED','RETURNED_UNVERIFIED'})
            except Exception:outcome='UNKNOWN_OUTCOME'
        with self.queue.tx() as db:
            row=db.execute('SELECT * FROM preparation_final_requests WHERE intent_sha=?',(intent,)).fetchone()
            if (row is None or row['preparation_nonce_sha']!=self.permit['nonce_sha']
                    or row['task_id']!=self.permit['task_id'] or row['task_revision']!=self.permit['task_revision']
                    or row['attempt_id']!=self.attempt_id):raise PreparationConflict()
            if row['outcome']=='UNKNOWN_OUTCOME':outcome='UNKNOWN_OUTCOME'
            if outcome=='RETURNED_UNVERIFIED':
                try:self._private();self._rows(db)
                except Exception:outcome='UNKNOWN_OUTCOME'
            db.execute('UPDATE preparation_final_requests SET outcome=?,updated=? WHERE intent_sha=?',
                       (outcome,self.queue.clock(),intent))
        return outcome
