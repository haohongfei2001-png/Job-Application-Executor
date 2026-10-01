"""Private-UI owner-thread controller; never registered with generic APIs.

Production write admission defaults closed. A later validated native-runtime
capability may supply the internal predicate; no JSON/client flag can enable it.
Public status contains enums/counts only. Private offers stay in local memory.
"""
from __future__ import annotations

import copy
import queue
import threading
import time
from concurrent.futures import Future,TimeoutError

from .authority import PreparationAuthority,PreparationConflict
from .preflight import prepare_public_flow
from .session import DisposablePreparationSession
from .resume_flow import ResumeUploadFlow


class PreparationController:
    def __repr__(self):return '<PreparationController>'

    def __init__(self,task_queue,session_valid,*,owner_factory=DisposablePreparationSession,
                 flow_factory=prepare_public_flow,write_admission=lambda owner:False,
                 resume_factory=ResumeUploadFlow,upload_admission=lambda owner:False,clock=time.monotonic):
        self.authority=PreparationAuthority(task_queue,session_valid=session_valid,clock=clock)
        self._session_valid=session_valid;self._owner_factory=owner_factory;self._flow_factory=flow_factory
        self._write_admission=write_admission;self._clock=clock
        self._resume_factory=resume_factory;self._upload_admission=upload_admission
        self._commands=queue.Queue(maxsize=2);self._lock=threading.RLock();self._revoked=threading.Event()
        self._thread=None;self._owner=self._flow=None;self._session=None
        self._entry_failed=False
        self._state='IDLE';self._deadline=0;self._fields=0;self._write_available=False
        self._next_review_fence=0
        self._resume=None;self._resume_status='NOT_REVIEWED';self._review_deadline=0

    def status(self):
        with self._lock:
            return {'status':self._state,'field_count':self._fields,'submit_capability':False,
                    'live_write_available':self._write_available,
                    'resume_status':self._resume_status,
                    'remaining_seconds':max(0,int(self._deadline-self._clock())) if self._deadline else 0}

    def _alive(self):
        try:
            if self._revoked.is_set() or self._deadline and self._clock()>=self._deadline:return False
            valid=self._session_valid(self._session) is True
            return valid and not self._revoked.is_set() and (not self._deadline or self._clock()<self._deadline)
        except Exception:return False

    def _enqueue(self,command,payload):
        future=Future()
        try:self._commands.put_nowait((command,payload,future))
        except queue.Full:raise PreparationConflict() from None
        return future

    def _await(self,future,timeout):
        try:return future.result(timeout=timeout)
        except TimeoutError:
            self._revoked.set()
            raise PreparationConflict() from None

    def open(self,task_id,revision,session,selected_ids):
        if not self._session_valid(session):raise PreparationConflict()
        with self._lock:
            if self._state!='IDLE' or self._revoked.is_set():raise PreparationConflict()
            self._state='OPENING';self._session=session
            future=self._enqueue('open',(task_id,revision,copy.deepcopy(selected_ids)))
            self._thread=threading.Thread(target=self._run,name='private-preparation-owner',daemon=True)
            self._thread.start()
        return self._await(future,90)

    def approve(self,nonce,scope_sha,session,*,approve_transmission):
        with self._lock:
            if (self._state!='OFFERED' or session!=self._session or not self._alive()
                    or approve_transmission is not True or not self._write_available):raise PreparationConflict()
            future=self._enqueue('approve',(nonce,scope_sha))
            self._state='PREPARING'
        return self._await(future,60)

    def cancel(self,session):
        with self._lock:
            if session!=self._session:raise PreparationConflict()
            self._revoked.set()
        return {'status':'CANCELLATION_REQUESTED','context_closed':False,'submit_capability':False}

    def review_resume(self,session):
        """Internal private review only; no HTTP/model route exposes this method."""
        with self._lock:
            if (self._state!='PREPARED_UNVERIFIED' or self._resume is not None
                    or session!=self._session or not self._alive()):raise PreparationConflict()
            future=self._enqueue('review_resume',None);self._state='REVIEWING_RESUME'
        return self._await(future,30)

    def approve_resume(self,nonce,scope_sha,session,*,approve_upload):
        with self._lock:
            if (self._state!='RESUME_OFFERED' or self._resume is None or session!=self._session
                    or not self._alive() or approve_upload is not True):raise PreparationConflict()
            future=self._enqueue('approve_resume',(nonce,scope_sha));self._state='UPLOADING_RESUME'
        return self._await(future,90)

    def shutdown(self,timeout=5):
        self._revoked.set()
        with self._lock:
            if self._thread is None:
                # open starts the owner thread under this same lock and refuses
                # the revocation flag. No browser was ever admitted here.
                self._state='CLOSED';self._deadline=0;self._write_available=False
                return True
            thread=self._thread
        thread.join(timeout=timeout)
        with self._lock:return not thread.is_alive() and self._state=='CLOSED'

    def _close_owned(self):
        # Only this thread calls Playwright. HTTP cancellation sets an Event,
        # which the field guard observes between every durable primitive.
        self._revoked.set()
        with self._lock:self._state='UNKNOWN_OUTCOME';self._write_available=False
        retired=True
        if self._resume is not None:
            try:self._resume._retire()
            except BaseException:retired=False
        try:
            if self._flow is not None:
                result=self._flow.close();closed=result.get('context_closed') is True and not result.get('reconciliation_required',False)
            elif self._owner is not None:
                if self._entry_failed:
                    closed=(getattr(self._owner,'entry_cleanup_attempted',False) is True
                            and getattr(self._owner,'entry_cleanup_closed',False) is True)
                else:closed=self._owner.close() is True
            else:closed=True
        except BaseException:closed=False
        closed=closed and retired
        with self._lock:
            self._state='CLOSED' if closed else 'UNKNOWN_OUTCOME'
            if self._resume_status in {'REVIEWING','OFFERED','UPLOADING'}:
                self._resume_status='CANCELLED' if self._resume_status!='UPLOADING' else 'UNKNOWN_OUTCOME'
            self._deadline=0
        return closed

    def _run(self):
        try:
            while not self._revoked.is_set():
                if self._session is not None and not self._alive():break
                if self._deadline and self._clock()>=self._deadline:break
                try:command,payload,future=self._commands.get(timeout=.1)
                except queue.Empty:
                    if self._owner is not None and self._owner.context is not None:
                        try:
                            pages=self._owner.context.pages
                            if len(pages)!=1:break
                            pages[0].wait_for_timeout(25)
                            if self._flow is not None and self._clock()>=self._next_review_fence:
                                self._flow.review_fence();self._next_review_fence=self._clock()+1
                                if self._state=='RESUME_OFFERED':self._resume.private_offer()
                        except Exception:break
                    continue
                try:
                    if command=='open':
                        if not self._alive():raise PreparationConflict()
                        task_id,revision,selected_ids=payload
                        self._owner=self._owner_factory()
                        if not self._alive():raise PreparationConflict()
                        try:self._owner.__enter__()
                        except BaseException:self._entry_failed=True;raise
                        self._flow=self._flow_factory(self.authority,self._owner,task_id=task_id,revision=revision,
                            session=self._session,selected_ids=selected_ids,still_authorized=self._alive)
                        if not self._alive():raise PreparationConflict()
                        offer=self._flow.private_offer()
                        available=self._write_admission(self._owner) is True
                        if not self._alive():raise PreparationConflict()
                        with self._lock:
                            self._write_available=available;self._state='OFFERED'
                            self._deadline=self._clock()+offer['expires_in_seconds']
                        offer['live_write_available']=available
                        future.set_result(offer)
                    elif command=='approve':
                        if not self._alive() or self._write_admission(self._owner) is not True:raise PreparationConflict()
                        result=self._flow.approve(*payload,self._session,approve_transmission=True)
                        if not self._alive():raise PreparationConflict()
                        with self._lock:
                            self._state='PREPARED_UNVERIFIED';self._fields=result['field_count']
                            self._deadline=self._clock()+900;self._review_deadline=self._deadline;self._write_available=False
                        future.set_result(result)
                    elif command=='review_resume':
                        if (not self._alive() or self._resume is not None
                                or self._upload_admission(self._owner) is not True):raise PreparationConflict()
                        self._resume_status='REVIEWING'
                        self._flow.review_fence()
                        self._resume=self._resume_factory(self._flow)
                        offer=self._resume.private_offer()
                        with self._lock:
                            if (not self._alive() or type(offer.get('expires_in_seconds')) is not int
                                    or not 0<offer['expires_in_seconds']<=120
                                    or self._clock()>=self._review_deadline):raise PreparationConflict()
                            self._state='RESUME_OFFERED';self._resume_status='OFFERED'
                            self._deadline=min(self._review_deadline,self._clock()+offer['expires_in_seconds'])
                        future.set_result(offer)
                    elif command=='approve_resume':
                        if (not self._alive() or self._resume is None
                                or self._upload_admission(self._owner) is not True):raise PreparationConflict()
                        if not self._alive() or self._clock()>=self._review_deadline:raise PreparationConflict()
                        self._resume_status='UPLOADING'
                        result=self._resume.approve(*payload,approve_upload=True)
                        if (not self._alive() or result.get('status')!='RETURNED_UNVERIFIED'
                                or result.get('server_attachment_verified') is not False):raise PreparationConflict()
                        self._flow.review_fence()
                        with self._lock:
                            if not self._alive() or self._clock()>=self._review_deadline:raise PreparationConflict()
                            self._state='PREPARED_UNVERIFIED';self._resume_status='RETURNED_UNVERIFIED'
                            self._deadline=self._review_deadline
                        future.set_result(result)
                    else:raise PreparationConflict()
                except BaseException:
                    if not future.done():future.set_exception(PreparationConflict())
                    self._revoked.set();break
        finally:
            self._close_owned()
            while True:
                try:_,_,future=self._commands.get_nowait()
                except queue.Empty:break
                if not future.done():future.set_exception(PreparationConflict())
