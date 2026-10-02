"""Owner-thread human-final coordination; live forwarding is unavailable.

This joins the existing retained request, native event and durable one-slot
components. Production constructs it with no forwarding admission. Only a
synthetic fixture can supply its own internal admission; no HTTP/IPC/environment
value selects it. Parent commands can start review, never confirm or send.
"""
from __future__ import annotations

import threading
from .authority import PreparationConflict
from .request_classifier import RetainedXHRClassifier
from .human_request import OpaqueHumanRequest, FINAL_URL
from .final_journal import PreparationFinalJournal

TERMINAL=frozenset({'RETURNED_UNVERIFIED','UNKNOWN_OUTCOME','CANCELLED'})


class HumanReviewCoordinator:
    def __repr__(self):return '<HumanReviewCoordinator>'

    def __init__(self,owner,native_admission,*,forwarding_admission=lambda:False):
        self._thread=threading.get_ident()
        self.owner=owner;self.native_admission=native_admission
        self._admit=forwarding_admission
        self._state='UNAVAILABLE';self.flow=None
        self.classifier=self.journal=self.bridge=self.cdp=None
        self._route=None;self._request=None;self._frame_id=None
        self._closed=False
        # Default production does not install an interception route or change
        # any live allowlist. It exposes the actual outstanding acceptance gate.
        if self._admit() is True:
            if owner.context.pages or native_admission(owner) is not True:raise PreparationConflict()
            self.classifier=RetainedXHRClassifier(owner.context)
            owner.context.route(FINAL_URL,self._park)
            self._state='AVAILABLE'

    def _owner(self):
        if threading.get_ident()!=self._thread:raise PreparationConflict()

    def status(self):
        return {'status':self._state,'submit_capability':False,
                'automatic_retry':False,'server_application_verified':False}

    def begin(self,flow):
        self._owner()
        if self._closed or self.flow is not None:raise PreparationConflict()
        if self._state=='UNAVAILABLE':
            return {**self.status(),'reason':'PHYSICAL_NATIVE_AND_SITE_ACCEPTANCE_PENDING'}
        if (self._state!='AVAILABLE' or self._admit() is not True
                or flow.owner is not self.owner or flow.state!='PREPARED_UNVERIFIED'
                or flow.permit is None or self.native_admission(self.owner) is not True):
            raise PreparationConflict()
        flow.review_fence()
        self.flow=flow
        self.classifier.arm()
        self.cdp=self.owner.context.new_cdp_session(flow.page)
        self.cdp.send('Page.enable')
        self._frame_id=self.cdp.send('Page.getFrameTree')['frameTree']['frame']['id']
        self.cdp.on('Page.javascriptDialogOpening',self._opening)
        self.cdp.on('Page.javascriptDialogClosed',self._closing)
        flow.page.on('dialog',self._dialog)
        self._state='MANUAL_REVIEW_ACTIVE'
        return self.status()

    def _invalidate(self):
        # Event/route callbacks revoke the bridge synchronously without RPC.
        # A guard may pump these callbacks and must not overwrite the refusal.
        self._state='CANCELLED'
        if self.bridge is not None:self.bridge.invalidate()

    def _park(self,route):
        # No renderer RPC, body/header copying, classification or transmission
        # occurs inside a route callback. The owner tick does the guarded work.
        if self._state!='MANUAL_REVIEW_ACTIVE' or self._route is not None:
            try:route.abort('blockedbyclient')
            except Exception:pass
            self._invalidate();return
        self._route=route;self._request=route.request

    def _opening(self,event):
        if self.bridge is not None:self.bridge.dialog_opened(event)
        else:self._invalidate()

    def _closing(self,event):
        if self.bridge is not None:self.bridge.dialog_closed(event)
        else:self._invalidate()

    def _dialog(self,dialog):
        if self.bridge is not None:self.bridge.dialog_handle(dialog)
        else:
            self._invalidate()
            try:dialog.dismiss()
            except Exception:pass

    def _observe(self):
        if (self._state!='MANUAL_REVIEW_ACTIVE' or self.flow is None or self.flow._still_authorized() is not True
                or self._admit() is not True or self.native_admission(self.owner) is not True):
            raise PreparationConflict()
        binding=self.flow._binding()
        frame=self.cdp.send('Page.getFrameTree')['frameTree']['frame']['id']
        if frame!=self._frame_id:raise PreparationConflict()
        # Last renderer observation: earlier RPCs can pump edits/cancellation.
        epoch=self.classifier.require_exact(self._request)
        if (self._state!='MANUAL_REVIEW_ACTIVE' or self.classifier.invalid
                or self._admit() is not True or self.flow._still_authorized() is not True):
            raise PreparationConflict()
        return {'browser':binding,'frame_id':frame,'change_epoch':epoch}

    def tick(self):
        self._owner()
        if self._state in TERMINAL or self._state=='UNAVAILABLE':return self.status()
        try:
            if (self.flow is None or self.flow._still_authorized() is not True
                    or self._admit() is not True or self.classifier.invalid):raise PreparationConflict()
            if self.bridge is None:
                self.flow.review_fence()
                if self._route is None:return self.status()
                metadata=self.classifier.classify(self._request)
                self.journal=PreparationFinalJournal(self.flow.authority,self.flow.permit,
                                                    self.flow.session,self._observe)
                self.bridge=OpaqueHumanRequest(page=self.flow.page,client=self.owner.client,
                    binding=self.journal.binding,guard=self.journal.guard,
                    consume=self.journal.consume,record=self.journal.record)
                self.bridge.hold(self._route,metadata)
                self.bridge.present()
            # During native modal opening/open, drain only checks its finite
            # state/deadline; it makes no renderer RPC until paired close.
            observed=self.bridge.drain()['status']
            self._state=observed if observed in TERMINAL else 'MANUAL_REVIEW_ACTIVE'
            return self.status()
        except Exception:
            self._state='UNKNOWN_OUTCOME' if self.journal is not None and self.journal.intent else 'CANCELLED'
            self.close()
            return self.status()

    def close(self):
        self._owner()
        if self._closed:return
        self._closed=True
        if self.bridge is not None and self._state not in {'RETURNED_UNVERIFIED','UNKNOWN_OUTCOME'}:
            self.bridge.cancel()
        elif self._route is not None and self.bridge is None:
            try:self._route.abort('blockedbyclient')
            except Exception:pass
        if self.cdp is not None:
            try:self.cdp.detach()
            except Exception:pass
        if self._state not in TERMINAL and self._state!='UNAVAILABLE':self._state='CANCELLED'
