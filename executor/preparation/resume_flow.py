"""Owner-thread-only resume stage; not registered with UI/model/worker APIs.

The native document is fenced before arming and after selection returns. During
synchronous XHR callbacks only OS/session/task/material/slot fences are used;
this is not an atomic hostile-DOM attestation. Outbound authority is the exact
reviewed file to the fixed company/form, never the webpage's request payload.
"""
from __future__ import annotations

import threading
import time

from .authority import PreparationConflict
from .qiyunfang import ROOT
from .resume_material import retained_resume
from .resume_journal import ResumeUploadJournal
from .resume_transport import ResumeRequestBroker


class ResumeUploadFlow:
    def __repr__(self):return '<ResumeUploadFlow>'

    def __init__(self,flow):
        flow._owner();flow.review_fence()
        if flow.state!='PREPARED_UNVERIFIED' or flow.permit is None:raise PreparationConflict()
        self.flow=flow;self.page=flow.page;self.owner=flow.owner
        if self.page.context is not self.owner.context or self.owner.context.pages!=[self.page]:raise PreparationConflict()
        self.binding=flow._binding();self._revoked=threading.Event();self._state='REVIEW'
        self._material_context=None;self.material=self.journal=self.broker=None
        self._listeners=[]
        def revoked(*_):self._revoked.set()
        def navigated(frame):
            if frame==self.page.main_frame:self._revoked.set()
        # Permanent revocation listeners remain until this disposable context
        # dies. Never temporarily release a filled document's transport guard.
        for emitter,name,callback in [(self.page,'framenavigated',navigated),(self.page,'close',revoked),
                (self.page,'crash',revoked),(self.owner.context,'close',revoked),
                (self.owner.context,'page',revoked),(self.owner.browser,'disconnected',revoked)]:
            emitter.on(name,callback);self._listeners.append((emitter,name,callback))
        try:
            self._material_context=retained_resume(flow.authority,flow.permit,flow.session)
            self.material=self._material_context.__enter__()
            self.journal=ResumeUploadJournal(flow.authority,flow.permit,flow.session,self.material,
                still_authorized=self._alive,process_guard=self.owner.identity.verify_os)
        except Exception:
            self._revoked.set();self._retire();raise PreparationConflict() from None

    def _alive(self):
        return (not self._revoked.is_set() and self._state in {'REVIEW','UPLOADING'}
                and self.flow._still_authorized() is True and self.flow.state=='PREPARED_UNVERIFIED')

    def private_offer(self):
        self.flow._owner()
        if self._state!='REVIEW' or not self._alive():raise PreparationConflict()
        if self.flow._binding()!=self.binding:raise PreparationConflict()
        return self.journal.private_offer()

    def _retire(self):
        context=self._material_context;self._material_context=None
        if context is not None:
            # Refusal/cleanup must release descriptors even after session loss.
            context.__exit__(PreparationConflict,PreparationConflict(),None)

    def approve(self,nonce,scope_sha,*,approve_upload):
        self.flow._owner()
        if self._state!='REVIEW' or not self._alive():raise PreparationConflict()
        # These observations occur OUTSIDE request callbacks, before selection.
        self.flow.review_fence()
        if self.flow._binding()!=self.binding:raise PreparationConflict()
        try:
            self.journal.approve(nonce,scope_sha,approve_upload=approve_upload)
            self._state='UPLOADING'
            self.broker=ResumeRequestBroker(page=self.page,client=self.owner.client,journal=self.journal,material=self.material)
            self.owner.context.route('**/ajax/advanceUpload.jsp?*',self.broker.handle)
            payload=self.broker.begin_selection()
            if not self._alive():raise PreparationConflict()
            self.page.locator(ROOT+' .form_item[data-formid="11"] input[type="file"]').set_input_files(payload,timeout=60000)
            deadline=time.monotonic()+60
            while self.broker._state=='ARMED' and not self.broker.ready_to_finish():
                if not self._alive() or time.monotonic()>=deadline:raise PreparationConflict()
                self.page.wait_for_timeout(25)
            # Synchronous XHR has returned; native DOM/process RPCs are safe
            # again. Any observed drift makes the receipt UNKNOWN, even if the
            # explicitly approved file already reached its fixed recipient.
            self.flow.review_fence()
            if not self._alive() or self.flow._binding()!=self.binding:raise PreparationConflict()
            result=self.broker.finish()
            if result['status']!='RETURNED_UNVERIFIED' or not self._alive():raise PreparationConflict()
            self._state='RETURNED_UNVERIFIED'
            return result
        except Exception:
            self._revoked.set();self._state='UNKNOWN_OUTCOME'
            if self.broker is not None:self.broker.cancel()
            elif self.journal.intent is not None:
                try:self.journal.finish('UNKNOWN_OUTCOME')
                except Exception:pass
            self.flow.close()
            raise PreparationConflict() from None
        finally:self._retire()

    def cancel(self):
        self.flow._owner();self._revoked.set()
        if self.broker is not None:self.broker.cancel()
        self._retire()
        return self.flow.close()
