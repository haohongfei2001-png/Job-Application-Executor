"""Cookie-only public preflight sessions. This surface cannot approve filling."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from urllib.parse import urlencode

from .preparation_review import review_preparation
from .task_preparation import parse_preparation_query
from ..preparation.qiyunfang import select_plan
from ..preparation.authority import PreparationConflict
from ..preparation.controller import PreparationController
from ..preparation.session import DisposablePreparationSession


def _request(data,opening):
    expected={'request_id','task_id','expected_revision'}|({'selected_ids','profile_version','resume_version'} if opening else set())
    if (not isinstance(data,dict) or set(data)!=expected or not isinstance(data['request_id'],str)
            or not re.fullmatch(r'[a-f0-9]{32}',data['request_id'])
            or type(data['expected_revision']) is not int or data['expected_revision']<0
            or not isinstance(data['task_id'],str)):raise ValueError('invalid preparation session')
    task_id,revision=parse_preparation_query(urlencode({'task_id':data['task_id'],'expected_revision':data['expected_revision']}))
    if opening and (not isinstance(data['selected_ids'],list) or not 0<len(data['selected_ids'])<=13
                    or any(not isinstance(value,str) for value in data['selected_ids'])):
        raise ValueError('invalid preparation selection')
    if opening and (not isinstance(data['profile_version'],str) or not re.fullmatch(r'[a-f0-9]{64}',data['profile_version'])
                    or data['resume_version'] is not None and (not isinstance(data['resume_version'],str)
                       or not re.fullmatch(r'[a-f0-9]{64}',data['resume_version']))):
        raise ValueError('invalid preparation versions')
    return data['request_id'],task_id,revision


def _factory(queue,session_valid):
    # Read-only UI preflight has no human-entered values or visible native form.
    # User-data filling remains closed pending native-device admission/handoff.
    return PreparationController(queue,session_valid,
        owner_factory=lambda:DisposablePreparationSession(headless=True,channel='chrome'))


class PrivatePreparationSessions:
    def __init__(self,queue,session_valid,mutation_fenced,*,factory=_factory,clock=time.monotonic):
        self.queue,self.session_valid,self.mutation_fenced=queue,session_valid,mutation_fenced
        self.factory,self.clock=factory,clock
        self.lock=threading.RLock();self.active=None;self.cancelled={}
        self.retired=False

    @staticmethod
    def _key(session,request_id):return hashlib.sha256(session.encode()).hexdigest(),request_id

    def _session(self,session):
        if not isinstance(session,str) or self.session_valid(session) is not True:raise PreparationConflict()

    def open(self,data,session):
        self._session(session);request_id,task_id,revision=_request(data,True)
        with self.lock:
            if self.retired:raise PreparationConflict()
        # Refuse an unrelated/stale task or unsupported field choice before
        # creating a browser. This projection never reaches the public website.
        review=review_preparation(self.queue,task_id,revision)
        if any(review[key]!=data[key] for key in ('profile_version','resume_version')):raise PreparationConflict()
        select_plan(review['proposals'],data['selected_ids'])
        key=self._key(session,request_id)
        with self.lock:
            self.cancelled={key:expiry for key,expiry in self.cancelled.items() if expiry>self.clock()}
            if (self.retired or self.mutation_fenced() or key in self.cancelled or len(self.cancelled)>=64
                    or self.active and self.active['controller'].status()['status'] not in {'CLOSED'}):
                raise PreparationConflict()
            controller=self.factory(self.queue,lambda value:not self.retired and self.session_valid(value) is True and not self.mutation_fenced())
            self.active={'key':key,'task_id':task_id,'revision':revision,'controller':controller}
        offer=controller.open(task_id,revision,session,data['selected_ids'])
        with self.lock:
            if (self.retired or self.session_valid(session) is not True or self.mutation_fenced()
                    or self.active['key']!=key or key in self.cancelled
                    or any(offer.get(name)!=data[name] for name in ('profile_version','resume_version'))):
                controller.cancel(session);raise PreparationConflict()
        # Do not return approval nonce/scope or any final transport capability.
        return {'mode':'READ_ONLY_PUBLIC_PREFLIGHT','request_id':request_id,'task_id':task_id,
                'task_revision':revision,'status':'EMPTY_FORM_VERIFIED','plan':offer['plan'],
                'source_url':offer['recipient_url'],'company':offer['company'],'role':offer['role'],
                'expires_in_seconds':offer['expires_in_seconds'],
                'profile_version':offer['profile_version'],'resume_version':offer['resume_version'],
                'capabilities':{'live_write':False,'submit':False,'account_verified':False,'server_draft_verified':False}}

    def cancel(self,data,session):
        self._session(session);request_id,task_id,revision=_request(data,False);key=self._key(session,request_id)
        with self.lock:
            if key not in self.cancelled and len(self.cancelled)>=64:raise PreparationConflict()
            if self.active and self.active['key']==key and (self.active['task_id'],self.active['revision'])!=(task_id,revision):
                raise PreparationConflict()
            # Retire before checking active state: a delayed open request must
            # not recreate a session after Close/Back/navigation already won.
            self.cancelled[key]=self.clock()+3600
            if self.active and self.active['key']==key:
                return self.active['controller'].cancel(session)
        return {'status':'CANCELLATION_REQUESTED','context_closed':False,'submit_capability':False}

    def status(self,data,session):
        self._session(session);request_id,task_id,revision=_request(data,False);key=self._key(session,request_id)
        with self.lock:
            if (not self.active or self.active['key']!=key
                    or (self.active['task_id'],self.active['revision'])!=(task_id,revision)):
                return {'status':'UNAVAILABLE','submit_capability':False,'live_write_available':False}
            result=self.active['controller'].status()
            # This API remains read-only even if a test controller has extra
            # internal capabilities. No request field can enable production fill.
            result['live_write_available']=False
            return result

    def revoke_all(self):
        """Service retirement only revokes; it never waits under service locks."""
        with self.lock:
            self.retired=True
            controller=self.active['controller'] if self.active else None
        if controller is not None:controller.shutdown(timeout=0)
