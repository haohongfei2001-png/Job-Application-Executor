"""One-owner preparation flow; HTTP exposes only its read-only opening stage.

The caller supplies the already-preflighted empty public page in a disposable
session. This joins real process identity, private review, durable approval and
field journals without granting upload, protected-input or final-send methods.
Its field-writing method is not registered with HTTP/model/worker entrypoints.
"""
from __future__ import annotations

import copy
import threading

from .authority import PreparationConflict, PreparationJournal, _material, _sha
from .qiyunfang import ROOT, OBSERVE_ROOT, validate_observation, digest
from .final_journal import _prepared_task
from .runner import PreparationKernel


class PreparationFlow:
    def __repr__(self):return '<PreparationFlow>'

    def __init__(self,authority,owner,page,*,task_id,revision,session,selected_ids,resources_sha,still_authorized=lambda:True):
        self._thread=threading.get_ident()
        self.authority,self.owner,self.page=authority,owner,page
        self.session,self.resources_sha=session,resources_sha
        if not callable(still_authorized):raise PreparationConflict()
        self._still_authorized=still_authorized
        self.permit=self.journal=None
        self.consumption_started=False;self.closure_attempted=False
        self.state='OPENING';self.offer=None
        if owner.identity is None or page.context is not owner.context:
            raise PreparationConflict()
        # Save value-free ownership recovery identity BEFORE issuing any offer.
        process_sha=owner.identity.save(authority.queue.root)
        observed=owner.observe(page,resources_sha=resources_sha)
        if observed.binding['process_sha']!=process_sha:raise PreparationConflict()
        validate_observation(page.locator(ROOT).evaluate(OBSERVE_ROOT))
        owner.transport.seal();owner.transport.require_sealed()
        self.binding=observed.public_binding()
        self.offer=authority.issue(task_id,revision,session,self.binding,selected_ids)
        self.state='OFFERED'

    def _owner(self):
        if threading.get_ident()!=self._thread:raise PreparationConflict()

    def private_offer(self):
        self._owner()
        if self.state!='OFFERED':raise PreparationConflict()
        self.authority._session(self.session)
        return copy.deepcopy(self.offer)

    def _binding(self):
        self._owner()
        if self._still_authorized() is not True:raise PreparationConflict()
        self.owner.transport.require_sealed()
        binding=self.owner.observe(self.page,resources_sha=self.resources_sha).public_binding()
        if self._still_authorized() is not True or binding!=self.binding:raise PreparationConflict()
        return binding

    def approve(self,nonce,scope_sha,session,*,approve_transmission):
        self._owner()
        if (self.state!='OFFERED' or session!=self.session or approve_transmission is not True
                or nonce!=self.offer['nonce'] or scope_sha!=self.offer['scope_sha']):raise PreparationConflict()
        # Refuse changed browser/transport before consuming an otherwise valid
        # offer. The authority then fences original task/content/session itself.
        binding=self._binding()
        try:
            self.consumption_started=True;self.state='PREPARING'
            self.permit=self.authority.consume(nonce,session,scope_sha,binding,
                                                approve_transmission=approve_transmission)
            self.journal=PreparationJournal(self.authority,self.permit,self.session,binding)
            guard=lambda sha:self.authority.guard(self.permit,self.session,self._binding(),plan_sha=sha)
            result=PreparationKernel(self.page,self.owner.transport,guard,self.journal).run(self.permit['plan'])
            self.journal.complete()
            finished=self.authority.finish(self.permit,'PREPARED_UNVERIFIED',context_closed=False,
                session=self.session,browser_binding=self._binding(),plan_sha=self.permit['plan_sha'])
            if finished['status']!='PREPARED_UNVERIFIED':raise PreparationConflict()
            self.state='PREPARED_UNVERIFIED';self.offer=None
            return result
        except BaseException:
            self.state='UNKNOWN_OUTCOME'
            if self.journal is not None:
                try:self.journal.invalidate()
                except Exception:pass
            try:self.close()
            except Exception:pass
            raise PreparationConflict() from None

    def review_fence(self):
        """Read-only heartbeat; no field lease renewal or new write permission."""
        self._binding();self.authority._session(self.session)
        if self.state=='OFFERED':
            with self.authority.lock:
                record=self.authority.pending.get(_sha(self.offer['nonce'])) if self.offer else None
                if record is None or record['expires']<=self.authority.clock():raise PreparationConflict()
                scope=record['scope']
                with _material(self.authority.queue,scope['task_id'],scope['revision']) as (task,report,_,fence):
                    if (task!=record['task'] or report['profile']['version']!=scope['profile_sha']
                            or report['resume']!=scope['resume']):raise PreparationConflict()
                    fence()
            return True
        if (self.state!='PREPARED_UNVERIFIED' or self.permit is None or self.journal is None
                or self.authority.active.get(self.permit['nonce_sha'])!=self.permit
                or self.permit['authority_sha']!=self.authority.instance_sha
                or self.permit['session_sha']!=_sha(self.session)
                or self.permit['plan_sha']!=digest(self.permit['plan'])):raise PreparationConflict()
        with _material(self.authority.queue,self.permit['task_id'],self.permit['task_revision']) as (_,report,_,fence):
            if report['profile']['version']!=self.permit['profile_sha'] or report['resume']!=self.permit['resume']:
                raise PreparationConflict()
            with self.authority.queue.tx() as db:
                _prepared_task(db,self.permit,self.journal.attempt_id)
                # Final transport requires an explicit future ownership change;
                # this preparation-only controller cannot silently coexist.
                if db.execute('SELECT 1 FROM preparation_final_requests WHERE preparation_nonce_sha=?',
                              (self.permit['nonce_sha'],)).fetchone():raise PreparationConflict()
                if db.execute("SELECT 1 FROM preparation_resume_uploads WHERE preparation_nonce_sha=? AND outcome='UNKNOWN_OUTCOME'",
                              (self.permit['nonce_sha'],)).fetchone():raise PreparationConflict()
            fence();self.authority._session(self.session)
            if self._still_authorized() is not True:raise PreparationConflict()
        return True

    def close(self):
        """Destroy the owned browser/API transport before releasing its fence.

        Closing never saves a server draft, retries a primitive or authorizes a
        final request. Uncertain disposal leaves the durable resource fenced.
        """
        self._owner()
        if self.state=='CLOSED':return {'status':'CLOSED','context_closed':True}
        if self.closure_attempted:raise PreparationConflict()
        self.closure_attempted=True
        if self.offer is not None:
            try:self.authority.revoke(self.offer['nonce'],self.session)
            except Exception:pass
            self.offer=None
        self.state='UNKNOWN_OUTCOME'
        try:closed=self.owner.close() is True
        except BaseException:closed=False
        recorded=True
        if self.permit is not None:
            try:self.authority.finish(self.permit,'UNKNOWN_OUTCOME',context_closed=closed)
            except Exception:recorded=False
        # An exception after an admission commit may leave no returned permit.
        # The saved process receipt enables independent read-only reconciliation;
        # never guess that nonce consumption rolled back or retry the admission.
        unresolved=(self.consumption_started and self.permit is None) or not recorded
        self.state='CLOSED' if closed and not unresolved else 'UNKNOWN_OUTCOME'
        return {'status':self.state,'context_closed':closed,'submit_capability':False,
                'reconciliation_required':unresolved or not closed}
