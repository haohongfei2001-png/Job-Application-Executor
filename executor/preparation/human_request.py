"""Unregistered exact-request, human-dialog-only final transport prototype.

No public HTTP/model/queue entrypoint imports this module. Production code never
accepts a dialog. Only the browser's matched native close event may authorize
one already-paused Request; protected bodies stay opaque in local Playwright
transport and are never read, parsed, displayed, logged or persisted here.
"""
from __future__ import annotations

import copy
import re
import secrets
import time
import threading

from .qiyunfang import CONTRACT_ROLE, CONTRACT_URL, CONTRACT_VERSION, digest

FINAL_URL = 'https://www.qiyunfang.com/ajax/siteForm_h.jsp'
NONCE = re.compile(r'[a-f0-9]{64}\Z')
TTL = 60


class HumanRequestConflict(RuntimeError):
    def __init__(self):super().__init__('human_request_conflict')


class OpaqueHumanRequest:
    """One pending concrete Route, not a reusable network permission.

    `guard` independently rechecks exact original task/session/process/document/
    root and browser-side change epoch without projecting protected values.
    `consume` is a durable CAS BEFORE the single outbound operation; `record`
    stores only its enum outcome. All three are internal trusted capabilities.
    Native event callbacks only change state. The owner invokes drain outside
    callbacks, avoiding nested page/dialog RPCs. No method accepts a dialog.
    """
    def __init__(self, *, page, client, binding, guard, consume, record, clock=time.monotonic):
        if (not all(callable(fn) for fn in (guard,consume,record))
                or not isinstance(binding,dict) or not isinstance(binding.get('frame_id'),str)
                or not binding['frame_id']):
            raise HumanRequestConflict()
        self._thread = threading.get_ident()
        self._page, self._client = page, client
        self._binding = copy.deepcopy(binding)
        self._guard, self._consume, self._record, self._clock = guard,consume,record,clock
        self._route = self._request = self._dialog = None
        self._state = 'EMPTY'
        self._nonce = self._message = None
        self._deadline = 0
        self._metadata = None
        self._intent = None

    def __repr__(self):return '<OpaqueHumanRequest>'

    def _owner(self):
        if threading.get_ident()!=self._thread:raise HumanRequestConflict()

    def _check(self, expected):
        self._owner()
        if self._state!=expected or self._clock()>=self._deadline:raise HumanRequestConflict()
        observed=self._guard()
        # Guard may pump browser callbacks. Revoke-before-send must survive any
        # cancellation/replacement delivered while it observes current state.
        if self._state!=expected or self._clock()>=self._deadline or observed!=self._binding:
            raise HumanRequestConflict()

    def hold(self, route, metadata):
        """Only called after the pinned renderer classifier identified final POST.

        Metadata is not hostile-page attestation; runtime must bind its source to
        the owned page/main frame and known driver. No protected values allowed.
        """
        self._owner()
        if self._state!='EMPTY':
            self._abort(route)
            self.invalidate()
            raise HumanRequestConflict()
        request=route.request
        expected={'contract':CONTRACT_VERSION,'command':'addWafCk_addSubmit',
                  'form_id':6,'role':CONTRACT_ROLE,'category':'final_application'}
        if (not isinstance(metadata,dict) or set(metadata)!=(set(expected)|{'change_epoch'})
                or any(metadata.get(key)!=value for key,value in expected.items())
                or type(metadata['change_epoch']) is not int or metadata['change_epoch']<0
                or metadata['change_epoch']!=self._binding.get('change_epoch')
                or request.method!='POST' or request.url!=FINAL_URL
                or not re.fullmatch(r'application/x-www-form-urlencoded(?:;\s*charset=(?:UTF-8|utf-8))?',
                                    request.headers.get('content-type',''))
                or request.is_navigation_request() or request.frame != self._page.main_frame):
            self._abort(route);raise HumanRequestConflict()
        self._route,self._request=route,request
        self._metadata=copy.deepcopy(metadata)
        self._nonce=secrets.token_hex(32)
        self._deadline=self._clock()+TTL
        self._message=('请仅在已亲自核对启云方「'+CONTRACT_ROLE+'」表单后确认最终提交。'
            '本次将发送当前待提交请求中的常规资料、本人填写的证件和验证码、简历引用及协议状态。'
            '这些内容不会发给AI，也不会由应用展示、保存或写入日志；本地浏览器传输层会临时处理请求字节。'
            '取消不会发送；结果不明确时不会自动重试。\n请求编号：'+self._nonce)
        self._state='HELD'
        # Holding cannot transmit. Defer live browser observations to the
        # owner's present/drain calls, never nest page RPCs in this callback.
        return {'status':'HELD','request_id':self._nonce,'company':'武汉启云方科技有限公司',
                'role':CONTRACT_ROLE,'source':CONTRACT_URL,'submit_capability':False}

    def present(self):
        """Owner-thread call, outside a route/event callback. Never waits on confirm."""
        self._owner()
        if self._state!='HELD':raise HumanRequestConflict()
        self._check('HELD');self._state='DIALOG_REQUESTED'
        # Defer the modal until AFTER evaluate returns. No awaited modal result,
        # no automatic accept path and no protected body in the browser script.
        self._page.evaluate('message=>{setTimeout(()=>window.confirm(message),0);return true}',self._message)

    def dialog_opened(self, event):
        # CDP source target is bound by owner. Never read event.userInput.
        self._owner()
        if self._state in {'DONE','UNKNOWN','CANCELLED'}:return
        if (self._state!='DIALOG_REQUESTED' or self._clock()>=self._deadline
                or event.get('type')!='confirm' or event.get('url')!=CONTRACT_URL
                or event.get('frameId')!=self._binding['frame_id']
                or event.get('hasBrowserHandler') is not True
                or event.get('message')!=self._message):
            self._state='INVALID';return
        self._state='DIALOG_OPEN'

    def dialog_handle(self, dialog):
        """Non-accepting listener prevents Playwright's default auto-dismiss."""
        self._owner()
        self._dialog=dialog

    def dialog_closed(self, event):
        self._owner()
        if self._state in {'DONE','UNKNOWN','CANCELLED'}:return
        if (self._state!='DIALOG_OPEN' or self._clock()>=self._deadline
                or event.get('frameId')!=self._binding['frame_id']
                or event.get('result') is not True):
            self._state='INVALID'
        else:self._state='HUMAN_CONFIRMED'
        self._dialog=None

    def invalidate(self):
        self._owner()
        # Input/setter epoch, root/navigation/task/session drift revoke only.
        if self._state not in {'DONE','UNKNOWN','CANCELLED'}:self._state='INVALID'

    @staticmethod
    def _abort(route):
        try:route.abort('blockedbyclient')
        except Exception:pass

    def cancel(self):
        self._owner()
        if self._state in {'FORWARD_ATTEMPTED','DONE','UNKNOWN'}:
            # An in-flight/forwarded effect cannot be undone or made replayable.
            self._state='UNKNOWN';return
        self._state='CANCELLED'
        if self._route is not None:self._abort(self._route)
        self._route=self._request=None
        if self._dialog is not None:
            try:self._dialog.dismiss()
            except Exception:pass
            self._dialog=None

    def drain(self):
        """Owner-thread call. Returns enums only; never exposes response contents."""
        self._owner()
        if self._state in {'DONE','UNKNOWN','CANCELLED'} and self._dialog is not None:
            try:self._dialog.dismiss()
            except Exception:pass
            self._dialog=None
        if self._state=='INVALID' or (self._state not in {'EMPTY','DONE','UNKNOWN','CANCELLED'} and self._clock()>=self._deadline):
            self.cancel();return {'status':'CANCELLED'}
        if self._state!='HUMAN_CONFIRMED':return {'status':self._state}
        try:self._check('HUMAN_CONFIRMED')
        except Exception:self.cancel();return {'status':'CANCELLED'}
        scope={'request_nonce_sha':digest(self._nonce),'browser':self._binding,'metadata':self._metadata,
               'purpose':'final_application','recipient':FINAL_URL}
        try:
            # Must durably retire this exact request before ANY network effect.
            self._intent=self._consume(copy.deepcopy(scope))
            if (not isinstance(self._intent,str) or not NONCE.fullmatch(self._intent)
                    or self._state!='HUMAN_CONFIRMED'):raise HumanRequestConflict()
        except Exception:
            self.cancel();self._state='UNKNOWN'
            if isinstance(self._intent,str) and NONCE.fullmatch(self._intent):
                try:self._record(self._intent,'UNKNOWN_OUTCOME')
                except Exception:pass
            return {'status':'UNKNOWN_OUTCOME'}
        self._state='FORWARD_ATTEMPTED';response=None
        outcome='UNKNOWN_OUTCOME'
        try:
            self._check('FORWARD_ATTEMPTED')
            # The exact Request object is forwarded opaquely by Playwright.
            # No post_data, headers dump, body decoding or Request repr access.
            response=self._client.fetch(self._request,max_redirects=0,max_retries=0,timeout=15000)
            if 300<=response.status<400:raise HumanRequestConflict()
            self._check('FORWARD_ATTEMPTED')
            if self._state!='FORWARD_ATTEMPTED':raise HumanRequestConflict()
            self._route.fulfill(response=response)
            self._check('FORWARD_ATTEMPTED')
            outcome='RETURNED_UNVERIFIED'
        except Exception:
            self._abort(self._route)
        finally:
            if response is not None:
                try:response.dispose()
                except Exception:outcome='UNKNOWN_OUTCOME'
            if self._state!='FORWARD_ATTEMPTED':outcome='UNKNOWN_OUTCOME'
            try:
                recorded=self._record(self._intent,outcome)
                if recorded not in {'RETURNED_UNVERIFIED','UNKNOWN_OUTCOME'} or recorded=='UNKNOWN_OUTCOME':
                    outcome='UNKNOWN_OUTCOME'
            except Exception:outcome='UNKNOWN_OUTCOME'
            # Fulfill/dispose/record can all deliver cancellation callbacks.
            # Never promote their uncertainty to a clean returned receipt.
            if self._state!='FORWARD_ATTEMPTED':outcome='UNKNOWN_OUTCOME'
            if outcome=='UNKNOWN_OUTCOME':
                try:self._record(self._intent,outcome)
                except Exception:pass
            self._state='DONE' if outcome=='RETURNED_UNVERIFIED' else 'UNKNOWN'
            self._route=self._request=self._dialog=None
        return {'status':outcome,'automatic_retry':False,'server_application_verified':False}
