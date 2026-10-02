"""Private native owner process and synchronous parent-authority transport.

No final-request operation exists. Browser, driver, queue authority and native
admission stay together in the child. Private offers cross inherited pipes only;
stdout/stderr are never captured. A dead child is not a proven closed browser.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import threading
import time

from .authority import PreparationConflict
from .session import PRIVATE_TRANSPORT_FORBIDDEN_ENV

_LIMIT = 1024 * 1024
_SAFE_ENV = frozenset({'HOME','PATH','TMPDIR','TMP','TEMP','LANG','LC_ALL','LC_CTYPE',
    'SYSTEMROOT','WINDIR','SSL_CERT_FILE','SSL_CERT_DIR','REQUESTS_CA_BUNDLE',
    'CURL_CA_BUNDLE','NODE_EXTRA_CA_CERTS','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY',
    'NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy'})
_FINAL_STATUS=frozenset({'UNAVAILABLE','AVAILABLE','MANUAL_REVIEW_ACTIVE','RETURNED_UNVERIFIED','UNKNOWN_OUTCOME','CANCELLED'})
_OPERATIONS = frozenset({'open','approve','review_resume','approve_resume','begin_human_review','status','shutdown'})


def private_environment(environ):
    # Preserve configured proxy/trust routing; never modify the parent or copy
    # Python/Node/loader injection, debug/recording settings, or service secrets.
    return {key: value for key, value in environ.items()
            if key in _SAFE_ENV and key not in PRIVATE_TRANSPORT_FORBIDDEN_ENV}


def _read(fd, count, deadline):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise PreparationConflict()
        block = os.read(fd, count-len(result))
        if not block: raise PreparationConflict()
        result.extend(block)
    return bytes(result)


def _receive(fd, timeout):
    deadline = time.monotonic()+timeout
    size = struct.unpack('!I', _read(fd, 4, deadline))[0]
    if not 0 < size <= _LIMIT: raise PreparationConflict()
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise PreparationConflict()
            value[key] = item
        return value
    try:
        value = json.loads(_read(fd, size, deadline), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(PreparationConflict()))
        if not isinstance(value, dict): raise PreparationConflict()
        return value
    except (ValueError, UnicodeError, RecursionError): raise PreparationConflict() from None


def _send(fd, value, timeout=3):
    try: raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError): raise PreparationConflict() from None
    if not 0 < len(raw) <= _LIMIT: raise PreparationConflict()
    raw = struct.pack('!I', len(raw))+raw
    deadline = time.monotonic()+timeout
    while raw:
        remaining = deadline-time.monotonic()
        if remaining <= 0 or not select.select([], [fd], [], remaining)[1]: raise PreparationConflict()
        # Bound each write to PIPE_BUF so a stalled peer cannot block past the
        # deadline after select reports only a little writable capacity.
        try: count = os.write(fd, raw[:4096])
        except BlockingIOError: continue
        if not count: raise PreparationConflict()
        raw = raw[count:]


def _close(fd):
    if fd is not None:
        try: os.close(fd)
        except OSError: pass


def _allocate_pipes():
    pipes=[]
    try:
        for _ in range(5):pipes.append(os.pipe())
        return pipes
    except BaseException:
        for pair in pipes:
            for fd in pair:_close(fd)
        raise PreparationConflict() from None


class PrivatePreparationChild:
    def __repr__(self): return '<PrivatePreparationChild>'

    def __init__(self, task_queue, session_valid):
        self._valid = session_valid
        self._session = None
        self._opened = False
        self._revoked = threading.Event()
        self._closed = False
        self._status_cache={'status':'IDLE','submit_capability':False,'field_count':0,
            'live_write_available':False,'resume_status':'NOT_REVIEWED','final_status':'UNAVAILABLE','remaining_seconds':0}
        self._sequence = 0
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._retirement_started = False
        self._process = None
        self._initialized = False
        self._guard_thread = None
        self._guard_started = False
        self._fds = []
        self._revoke = None
        pipes = _allocate_pipes()
        # command, response, guard-request, guard-response, lifetime
        (cr,cw),(rr,rw),(gr,gw),(ar,aw),(lr,lw) = pipes
        self._command,self._response,self._guard_read,self._guard_write,self._revoke = cw,rr,gr,aw,lw
        self._fds = [cw,rr]
        child_fds = [cr,rw,gw,ar,lr]
        try:
            for fd in (cw,aw):os.set_blocking(fd,False)
            root = Path(__file__).resolve().parents[2]
            bootstrap = ('import sys;sys.path.insert(0,sys.argv[1]);'
                         'from executor.preparation.private_child import worker_main;'
                         'worker_main([int(x) for x in sys.argv[2:]])')
            self._process = subprocess.Popen([sys.executable,'-I','-B','-c',bootstrap,str(root),
                *map(str,child_fds)], stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,env=private_environment(os.environ),close_fds=True,
                pass_fds=tuple(child_fds),start_new_session=True)
            for fd in child_fds: _close(fd)
            child_fds=[]
            self._guard_thread=threading.Thread(target=self._guard_loop,daemon=True,
                                               name='private-preparation-parent-fence')
            self._guard_thread.start()
            self._guard_started=True
            self._call('init',[str(task_queue.root)],{},10)
            with self._state_lock:
                if self._revoked.is_set():raise PreparationConflict()
                self._initialized=True
        except BaseException:
            self._revoke_child(retire=False)
            for fd in child_fds: _close(fd)
            self._dispose_pipes()
            if not self._guard_started:
                _close(self._guard_read);_close(self._guard_write)
            # init never opens a browser. A failed handshake still does not
            # grant CLOSED evidence; reap only this exact launched interpreter.
            if self._process is not None:
                try:self._process.wait(timeout=12)
                except subprocess.TimeoutExpired:pass
            raise PreparationConflict() from None

    def _revoke_child(self, *, retire=True):
        self._revoked.set()
        with self._state_lock:
            fd,self._revoke=self._revoke,None
            start=(retire and self._initialized and not self._retirement_started and self._process is not None)
            if start:self._retirement_started=True
        _close(fd)
        if start:
            threading.Thread(target=self._retire_process,daemon=True,
                             name='private-preparation-retirement').start()

    def _retire_process(self):
        self.shutdown(timeout=20)
        # IPC reclamation is independent of browser closure evidence. Closing
        # the command channel tells the worker to retire, never to retry.
        self._dispose_pipes()
        try:self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:pass

    def _dispose_pipes(self):
        # Only the command owner closes command descriptors. Guard endpoints
        # belong exclusively to the guard thread until its callback returns.
        with self._lock:
            fds,self._fds=self._fds,[]
            self._command=self._response=None
            for fd in fds:_close(fd)

    def _guard_loop(self):
        sequence=0
        try:
            while not self._closed:
                request=_receive(self._guard_read,1800)
                if (set(request)!={'seq','session'} or type(request['seq']) is not int
                        or request['seq']!=sequence+1 or not isinstance(request['session'],str)):
                    raise PreparationConflict()
                sequence=request['seq']
                allowed=(not self._revoked.is_set() and self._session is not None
                         and request['session']==self._session
                         and self._valid(self._session) is True and not self._revoked.is_set())
                _send(self._guard_write,{'seq':sequence,'allowed':allowed})
                if not allowed: self._revoke_child()
        except BaseException: self._revoke_child()
        finally:
            # A slow parent callback may outlive child exit. Its descriptors
            # must not be closed/reused by another thread while it can send.
            _close(self._guard_read);_close(self._guard_write)
            self._guard_read=self._guard_write=None

    def _call(self, operation, args, kwargs, timeout):
        acquired=(self._lock.acquire(blocking=False) if operation=='status'
                  else self._lock.acquire(timeout=timeout))
        if not acquired:
            # Polling is observation, never a reason to cancel an in-flight
            # upload/open/fill. This cache conveys no authority or CLOSED proof.
            if operation=='status':return {**self._status_cache,'status':'UNKNOWN_OUTCOME' if self._status_cache.get('status')=='CLOSED' else self._status_cache['status']}
            self._revoke_child();raise PreparationConflict()
        try:
            if self._closed or (self._revoked.is_set() and operation!='shutdown'):
                raise PreparationConflict()
            states={'open':'OPENING','approve':'PREPARING','review_resume':'REVIEWING_RESUME',
                    'approve_resume':'UPLOADING_RESUME','begin_human_review':'STARTING_HUMAN_REVIEW'}
            if operation in states:self._status_cache['status']=states[operation]
            self._sequence+=1
            _send(self._command,{'seq':self._sequence,'op':operation,'args':args,'kwargs':kwargs})
            response=_receive(self._response,timeout)
            if (set(response)!={'seq','ok','result'} or type(response['seq']) is not int
                    or response['seq']!=self._sequence or response['ok'] is not True):
                raise PreparationConflict()
            if self._revoked.is_set() and operation!='shutdown': raise PreparationConflict()
            result=response['result']
            if operation=='status' and isinstance(result,dict):
                self._status_cache={key:result[key] for key in self._status_cache if key in result}
            elif operation=='open':
                self._status_cache.update(status='OFFERED',live_write_available=result.get('live_write_available') is True,
                                          remaining_seconds=result.get('expires_in_seconds',0))
            elif operation=='approve':
                self._status_cache.update(status='PREPARED_UNVERIFIED',live_write_available=False,
                                          field_count=result.get('field_count',0))
            elif operation=='review_resume':self._status_cache.update(status='RESUME_OFFERED',resume_status='OFFERED')
            elif operation=='approve_resume':self._status_cache.update(status='PREPARED_UNVERIFIED',resume_status='RETURNED_UNVERIFIED')
            elif operation=='begin_human_review':
                self._status_cache['final_status']=result.get('status','UNAVAILABLE')
                self._status_cache['status']='PREPARED_UNVERIFIED'
                if result.get('status')=='MANUAL_REVIEW_ACTIVE':self._status_cache['status']='HUMAN_REVIEW'
            return result
        except BaseException:
            self._revoke_child();raise PreparationConflict() from None
        finally:self._lock.release()

    def open(self, task_id, revision, session, selected_ids):
        if self._valid(session) is not True:raise PreparationConflict()
        with self._state_lock:
            if self._opened or self._revoked.is_set():raise PreparationConflict()
            self._opened=True;self._session=session
        return self._call('open',[task_id,revision,session,selected_ids],{},95)

    def approve(self, nonce, scope_sha, session, *, approve_transmission):
        return self._call('approve',[nonce,scope_sha,session],{'approve_transmission':approve_transmission},65)

    def review_resume(self, session):
        return self._call('review_resume',[session],{},35)

    def approve_resume(self, nonce, scope_sha, session, *, approve_upload):
        return self._call('approve_resume',[nonce,scope_sha,session],{'approve_upload':approve_upload},95)

    def begin_human_review(self, session):
        return self._call('begin_human_review',[session],{},35)

    def cancel(self, session):
        if session!=self._session: raise PreparationConflict()
        self._revoke_child()
        return {'status':'CANCELLATION_REQUESTED','context_closed':False,'submit_capability':False}

    def status(self):
        if self._closed:return {'status':'CLOSED','submit_capability':False,'field_count':0,
            'live_write_available':False,'resume_status':'NOT_REVIEWED','final_status':self._status_cache.get('final_status','UNAVAILABLE'),'remaining_seconds':0}
        if self._revoked.is_set() or self._process.poll() is not None:
            return {'status':'UNKNOWN_OUTCOME','submit_capability':False,'field_count':0,
                    'live_write_available':False,'resume_status':'UNKNOWN_OUTCOME','final_status':self._status_cache.get('final_status','UNAVAILABLE'),'remaining_seconds':0}
        result=self._call('status',[],{},3)
        if result.get('status')=='CLOSED':
            # A closed browser is not yet a retired/reaped child. Do not let the
            # manager replace this proxy until the exact shutdown proof + exit.
            self._revoke_child()
            return {'status':'UNKNOWN_OUTCOME','submit_capability':False,'field_count':0,
                    'live_write_available':False,'resume_status':'UNKNOWN_OUTCOME','final_status':self._status_cache.get('final_status','UNAVAILABLE'),'remaining_seconds':0}
        return result

    def shutdown(self, timeout=5):
        if self._closed:return True
        self._revoke_child(retire=timeout<=0)
        if timeout<=0:return False
        deadline=time.monotonic()+timeout
        if not self._shutdown_lock.acquire(timeout=timeout):return False
        try:
            if self._closed:return True
            remaining=deadline-time.monotonic()
            if remaining<=0:return False
            receipt=self._call('shutdown',[],{'timeout':remaining},remaining)
            if (type(receipt) is not dict or set(receipt)!={'context_closed','final_status'}
                    or receipt['final_status'] not in _FINAL_STATUS):return False
            self._status_cache['final_status']=(receipt['final_status'] if receipt['final_status'] in
                {'UNAVAILABLE','RETURNED_UNVERIFIED','UNKNOWN_OUTCOME','CANCELLED'} else 'UNKNOWN_OUTCOME')
            proven=receipt['context_closed']
            remaining=max(.01,deadline-time.monotonic())
            self._process.wait(timeout=remaining)
            if proven is not True or self._process.returncode!=0:return False
            self._closed=True
            # Guard thread owns and closes its endpoints, even when a parent
            # callback outlives this exact child's acknowledged exit.
            self._dispose_pipes();return True
        except BaseException:return False
        finally:self._shutdown_lock.release()


def approved_qiyunfang_owner(owner):
    """Fixed user-approved domain gate; never controlled by IPC/env/settings."""
    from .session import DisposablePreparationSession
    from .qiyunfang import CONTRACT_URL
    return (type(owner) is DisposablePreparationSession and owner.headless is False
            and owner.channel=='chrome' and not owner._close_attempted
            and owner.context is not None and owner.identity is not None
            and len(owner.context.pages)<=1
            and all(page.url==CONTRACT_URL for page in owner.context.pages))


def _native_controller(root, valid):
    from ..autonomy.queue import TaskQueue
    from .controller import PreparationController
    from .session import DisposablePreparationSession
    from .native_admission import NativePreparationAdmission,NativeAdmissionUnavailable
    if not NativePreparationAdmission.available():raise NativeAdmissionUnavailable()
    queue=TaskQueue(root)
    admission=NativePreparationAdmission(still_authorized=lambda:controller._alive())
    from .human_review import HumanReviewCoordinator
    from .captcha_image import CaptchaImageBroker
    controller=PreparationController(queue,valid,
        owner_factory=lambda:DisposablePreparationSession(headless=False,channel='chrome'),
        write_admission=admission.admit,upload_admission=admission.admit,
        human_review_factory=lambda owner:HumanReviewCoordinator(owner,admission.admit,
            forwarding_admission=lambda:approved_qiyunfang_owner(owner),
            captcha_factory=CaptchaImageBroker,keep_terminal=True))
    return controller


def worker_main(fds, *, controller_factory=_native_controller):
    # The fixed production bootstrap supplies descriptors only; test factories
    # exist only through direct Python calls in synthetic harness processes.
    if len(fds)!=5:return
    commands,responses,guard_out,guard_in,lifetime=fds
    for fd in fds:os.set_inheritable(fd,False)
    for fd in (responses,guard_out):os.set_blocking(fd,False)
    revoked=threading.Event();guard_lock=threading.Lock();sequence=0;controller=None
    def revoke():
        revoked.set()
        if controller is not None:
            try:controller.shutdown(timeout=0)
            except BaseException:pass
    def parent_watch():
        try:
            # No data is valid on the lifetime pipe; EOF is permanent revocation.
            os.read(lifetime,1)
        finally:revoke()
    threading.Thread(target=parent_watch,daemon=True,name='private-preparation-parent-loss').start()
    def valid(session):
        nonlocal sequence
        if revoked.is_set() or session!=bound_session:return False
        try:
            with guard_lock:
                sequence+=1
                _send(guard_out,{'seq':sequence,'session':session})
                result=_receive(guard_in,3)
                if (set(result)!={'seq','allowed'} or type(result['seq']) is not int
                        or result['seq']!=sequence or result['allowed'] is not True):
                    raise PreparationConflict()
                return not revoked.is_set()
        except BaseException:revoke();return False
    expected=0;opened=False;bound_session=None
    try:
        while True:
            message=_receive(commands,1800)
            if (set(message)!={'seq','op','args','kwargs'} or type(message['seq']) is not int
                    or message['seq']!=expected+1 or not isinstance(message['args'],list)
                    or not isinstance(message['kwargs'],dict)):
                raise PreparationConflict()
            expected=message['seq'];operation=message['op']
            try:
                if operation=='init' and controller is None and expected==1 and not revoked.is_set():
                    if len(message['args'])!=1 or message['kwargs']:raise PreparationConflict()
                    controller=controller_factory(message['args'][0],valid)
                    if revoked.is_set():revoke();raise PreparationConflict()
                    result=True
                elif operation in _OPERATIONS and controller is not None:
                    if revoked.is_set() and operation!='shutdown':raise PreparationConflict()
                    if operation=='open':
                        if opened:raise PreparationConflict()
                        if len(message['args'])!=4 or message['kwargs'] or not isinstance(message['args'][2],str):raise PreparationConflict()
                        bound_session=message['args'][2];opened=True
                    result=getattr(controller,operation)(*message['args'],**message['kwargs'])
                    if revoked.is_set() and operation!='shutdown':raise PreparationConflict()
                else:raise PreparationConflict()
                if operation=='shutdown':
                    final_status=controller.status().get('final_status','UNAVAILABLE')
                    if final_status not in _FINAL_STATUS:final_status='UNKNOWN_OUTCOME'
                    result={'context_closed':result is True,'final_status':final_status}
                _send(responses,{'seq':expected,'ok':True,'result':result})
                if operation=='shutdown':return
            except BaseException:
                revoke()
                _send(responses,{'seq':expected,'ok':False,'result':None})
    except BaseException:pass
    finally:
        revoke()
        if controller is not None:
            try:controller.shutdown(timeout=10)
            except BaseException:pass
        for fd in fds:_close(fd)
