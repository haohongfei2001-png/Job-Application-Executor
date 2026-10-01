"""Unregistered trusted resume sender, never an opaque multipart forwarder.

Only the reviewed file buffer plus finite source-shaped metadata is serialized
outbound. A paused webpage request is a stage wake-up, not payload authority.
Its body is never read/forwarded. Only header names/content-type are inspected;
authentication/cookie values are never inspected, copied or logged.
The native deny-only proxy is never released. No browser RPC occurs in guards.
"""
from __future__ import annotations

import json
import re
import secrets
import threading

from .authority import PreparationConflict
from .resume_journal import STAGES,UPLOAD_URL

LOOKUP_URL=UPLOAD_URL+'?cmd=_getUploadSize'
SPLIT_BYTES=5*1024*1024
UPLOAD_PATTERN=re.compile(re.escape(UPLOAD_URL)+r'\?cmd=_mobiupload&app=21&type=0&fileUploadLimit=([1-9][0-9]?)&pieceUpload=true&checkId=6&checkItemId=11&bizType=5\Z')


class ResumeRequestBroker:
    def __repr__(self):return '<ResumeRequestBroker>'

    def __init__(self,*,page,client,journal,material):
        if journal.intent is None:raise PreparationConflict()
        journal.guard()
        self._thread=threading.get_ident();self.page,self.client,self.journal,self.material=page,client,journal,material
        self._nonce=secrets.token_hex(16)
        self._next=0;self._active=0;self._state='ARMED'
        self._selected=False
        self._review=material.private_review()

    def _owner(self):
        if threading.get_ident()!=self._thread:raise PreparationConflict()

    def begin_selection(self):
        """Consume the sole native selection before set_input_files can run."""
        self._owner()
        if self._state!='ARMED' or self._selected:raise PreparationConflict()
        self.journal.guard();self.journal.begin_selection();self._selected=True
        return self.material._file_payload()

    def _check(self,stage=None):
        self._owner()
        if self._state!='ARMED' or not self._selected:raise PreparationConflict()
        self.journal.guard(stage)
        if self._state!='ARMED':raise PreparationConflict()

    @staticmethod
    def _abort(route):
        try:route.abort('blockedbyclient')
        except Exception:pass

    def cancel(self):
        self._owner();self._state='UNKNOWN'
        try:self.journal.finish('UNKNOWN_OUTCOME')
        except Exception:pass

    def _classify(self,request):
        if (request.method!='POST' or request.is_navigation_request()
                or request.frame!=self.page.main_frame):raise PreparationConflict()
        headers=request.headers
        # Inspect only presence/type. Never extract or copy authentication state.
        if any(key.lower() in {'authorization','proxy-authorization'} for key in headers):
            raise PreparationConflict()
        content_type=headers.get('content-type','')
        if self._next in {0,1}:
            if (request.url!=LOOKUP_URL or not re.fullmatch(
                    r'application/x-www-form-urlencoded(?:;\s*charset=(?:UTF-8|utf-8))?',content_type)):
                raise PreparationConflict()
            return None
        match=UPLOAD_PATTERN.fullmatch(request.url)
        if (self._next!=2 or match is None or not re.fullmatch(
                r'multipart/form-data;\s*boundary=[A-Za-z0-9_-]{1,70}',content_type)):
            raise PreparationConflict()
        limit=int(match.group(1))
        if self._review['byte_count']>limit*1024*1024:raise PreparationConflict()
        return limit

    @staticmethod
    def _accepted(response,lookup):
        if response.status!=200:raise PreparationConflict()
        # APIRequestContext already buffers local response bytes. This is an
        # application parsing bound, not a claimed streaming download limit.
        data=response.body()
        if len(data)>16384:raise PreparationConflict()
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise PreparationConflict()
                result[key]=value
            return result
        value=json.loads(data,object_pairs_hook=unique)
        if not isinstance(value,dict) or value.get('success') is not True:raise PreparationConflict()
        if lookup and (type(value.get('size')) is not int or value['size']!=0):raise PreparationConflict()

    def handle(self,route):
        """Synchronous route callback: never evaluate/inspect the browser here.

        Source uploadify makes two synchronous lookup XHRs. Record each returned
        response before fulfilling it, so a nested next-stage callback has a
        durable predecessor. Any later callback/fulfill/disposal error downgrades
        the upload; no success response can refund an already-consumed stage.
        """
        self._owner();stage=None;response=None;entered=False
        try:
            self._check();limit=self._classify(route.request)
            ordinal=self._next
            stage=self.journal.begin_stage(STAGES[ordinal])
            self._check(stage)
            self._active+=1;entered=True
            # No incoming request body/header is consulted. The native protocol
            # nonce is generated here and consistently substituted in all stages.
            if ordinal<2:
                response=self.client.post(LOOKUP_URL,form={
                    'fileMd5':self._nonce,'fileSplitSize':str(SPLIT_BYTES),
                    'fileName':self._review['destination_filename'],'totalSize':str(self._review['byte_count'])},
                    headers={'X-Requested-With':'XMLHttpRequest'},max_redirects=0,max_retries=0,timeout=15000)
            else:
                payload=self.material._file_payload();self._check(stage)
                target=(UPLOAD_URL+'?cmd=_mobiupload&app=21&type=0&fileUploadLimit='+str(limit)
                        +'&pieceUpload=true&checkId=6&checkItemId=11&bizType=5')
                response=self.client.post(target,multipart={'filedata':payload,'fileMd5':self._nonce,
                    'totalSize':str(self._review['byte_count']),'complete':'true','initSize':'0'},
                    headers={'X-Requested-With':'XMLHttpRequest'},max_redirects=0,max_retries=0,timeout=15000)
            self._check(stage);self._accepted(response,ordinal<2);self._check(stage)
            if self.journal.record_stage(stage,'RETURNED_UNVERIFIED')!='RETURNED_UNVERIFIED':raise PreparationConflict()
            self._next=ordinal+1
            self._check()
            route.fulfill(response=response)
            self._check()
        except Exception:
            self.cancel();self._abort(route)
            if stage is not None:
                try:self.journal.record_stage(stage,'UNKNOWN_OUTCOME')
                except Exception:pass
        finally:
            if response is not None:
                try:response.dispose()
                except Exception:self.cancel()
            if entered:self._active-=1
            if self._state=='UNKNOWN':
                try:self.journal.finish('UNKNOWN_OUTCOME')
                except Exception:pass
        return {'status':self._state,'submit_capability':False}

    def ready_to_finish(self):
        self._owner()
        return self._state=='ARMED' and self._next==3 and self._active==0

    def finish(self):
        self._owner()
        if not self.ready_to_finish():self.cancel();return {'status':'UNKNOWN_OUTCOME','submit_capability':False}
        try:
            self._check();outcome=self.journal.finish('RETURNED_UNVERIFIED')
        except Exception:
            self.cancel();outcome='UNKNOWN_OUTCOME'
        self._state='RETURNED_UNVERIFIED' if outcome=='RETURNED_UNVERIFIED' and self._state=='ARMED' else 'UNKNOWN'
        if self._state=='UNKNOWN':
            try:self.journal.finish('UNKNOWN_OUTCOME')
            except Exception:pass
        return {'status':'RETURNED_UNVERIFIED' if self._state=='RETURNED_UNVERIFIED' else 'UNKNOWN_OUTCOME',
                'server_attachment_verified':False,'automatic_retry':False,'submit_capability':False}
