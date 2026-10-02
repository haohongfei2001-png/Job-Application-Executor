"""Real private-process protocol tests; synthetic controller is test-only.

Production launcher has no test selector. The fixture replaces only its fixed
bootstrap in this test process, retaining actual pipes/env/stdio/process death.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from executor.preparation import private_child as m
from executor.preparation.authority import PreparationConflict

SESSION='SYNTHETIC_SESSION_'+'s'*32
CANARY='SYNTHETIC_PRIVATE_CANARY_DO_NOT_LOG'

HARNESS=r'''
import os,time,json
from pathlib import Path
from executor.preparation.authority import PreparationConflict
class Controller:
 def __init__(self,root,valid):
  self.root=Path(root);self.valid=valid;self.state='IDLE';self.closed=False
  print('SYNTHETIC_PRIVATE_CANARY_DO_NOT_LOG',flush=True)
  os.write(2,b'SYNTHETIC_PRIVATE_CANARY_DO_NOT_LOG')
  (self.root/'environment.json').write_text(json.dumps({k:os.environ.get(k) for k in ('DEBUG','NODE_OPTIONS','PYTHONPATH','SSLKEYLOGFILE','HTTPS_PROXY','SSL_CERT_FILE','JAE_PRIVATE_SECRET')}))
  (self.root/'pid').write_text(str(os.getpid()))
  if (self.root/'fail_init').exists():raise RuntimeError('SYNTHETIC_PRIVATE_CANARY_DO_NOT_LOG')
 def open(self,task,revision,session,selected):
  if not self.valid(session):raise PreparationConflict()
  self.session=session;self.state='OFFERED'
  return {'nonce':'SYNTHETIC_NONCE','scope_sha':'a'*64,'plan':[{'value':'SYNTHETIC_PRIVATE_CANARY_DO_NOT_LOG'}],'expires_in_seconds':120,'live_write_available':True}
 def approve(self,nonce,scope,session,approve_transmission):
  if self.state!='OFFERED' or not approve_transmission or not self.valid(session):raise PreparationConflict()
  self.state='PREPARING'
  if (self.root/'hold').exists():
   (self.root/'entered').touch()
   while (self.root/'hold').exists() and self.valid(session):time.sleep(.01)
  if not self.valid(session):raise PreparationConflict()
  (self.root/'effect').write_text('ONE_SYNTHETIC_EFFECT');self.state='PREPARED_UNVERIFIED'
  return {'status':self.state,'submit_capability':False,'field_count':1}
 def status(self):
  if (self.root/'natural_close').exists():self.shutdown()
  return {'status':self.state,'submit_capability':False,'final_status':'RETURNED_UNVERIFIED' if (self.root/'final_returned').exists() else 'UNAVAILABLE'}
 def shutdown(self,timeout=5):
  if (self.root/'unknown').exists():self.state='UNKNOWN_OUTCOME';return False
  self.closed=True;self.state='CLOSED';(self.root/'closed').touch();return True
'''

@pytest.fixture
def child(tmp_path,monkeypatch):
    harness=tmp_path/'synthetic_private_owner.py';harness.write_text(HARNESS)
    real=m.subprocess.Popen;launches=[];instances=[]
    def launch(args,**kwargs):
        launches.append((args,kwargs))
        args=list(args)
        args[4]=('import sys;sys.path.insert(0,sys.argv[1]);sys.path.insert(0,'+repr(str(tmp_path))+');'
                 'from synthetic_private_owner import Controller;'
                 'from executor.preparation.private_child import worker_main;'
                 'worker_main([int(x) for x in sys.argv[2:]],controller_factory=Controller)')
        return real(args,**kwargs)
    monkeypatch.setattr(m.subprocess,'Popen',launch)
    def make(valid=lambda value:value==SESSION):
        owner=m.PrivatePreparationChild(SimpleNamespace(root=tmp_path),valid);instances.append(owner);return owner
    yield make,tmp_path,launches
    for owner in instances:
        owner.shutdown(timeout=2)
        # Test fixture teardown only. Unknown production owners are not killed
        # or treated as closed by the product.
        if owner._process.poll() is None:owner._process.kill();owner._process.wait()
        owner._dispose_pipes()


def test_private_environment_preserves_routing_and_drops_injection(child,monkeypatch,capfd):
    make,root,launches=child
    values={'DEBUG':CANARY,'NODE_OPTIONS':'--inspect','PYTHONPATH':CANARY,'SSLKEYLOGFILE':CANARY,
            'HTTPS_PROXY':'https://synthetic-proxy.invalid','SSL_CERT_FILE':'/synthetic/trust.pem','JAE_PRIVATE_SECRET':CANARY}
    for key,value in values.items():monkeypatch.setenv(key,value)
    owner=make();offer=owner.open('task',1,SESSION,['0'])
    assert CANARY in json.dumps(offer) and CANARY not in repr(owner)
    env=json.loads((root/'environment.json').read_text())
    assert env=={key:(value if key in {'HTTPS_PROXY','SSL_CERT_FILE'} else None) for key,value in values.items()}
    assert all(os.environ[key]==value for key,value in values.items())
    args,options=launches[0]
    assert args[1:4]==['-I','-B','-c']
    assert options['stdin']==options['stdout']==options['stderr']==subprocess.DEVNULL
    assert options['close_fds'] is True and 'shell' not in options
    assert owner.approve('SYNTHETIC_NONCE','a'*64,SESSION,approve_transmission=True)['field_count']==1
    assert CANARY not in json.dumps(owner.status())
    assert owner.shutdown(timeout=3)
    assert CANARY not in ''.join(capfd.readouterr())
    assert not (root/CANARY).exists()


def test_every_child_guard_observes_current_parent_revocation(child):
    make,root,_=child;allowed=[True];checks=[]
    owner=make(lambda value:checks.append(value) is None and allowed[0])
    owner.open('task',1,SESSION,['0']);before=len(checks);allowed[0]=False
    with pytest.raises(PreparationConflict):owner.approve('n','s',SESSION,approve_transmission=True)
    assert len(checks)>before and not (root/'effect').exists()
    assert owner._revoked.is_set()


def test_wrong_session_is_never_forwarded_as_valid_authority(child):
    make,root,_=child;owner=make(lambda _:True);owner.open('task',1,SESSION,['0'])
    with pytest.raises(PreparationConflict):owner.approve('n','s','OTHER_SESSION',approve_transmission=True)
    assert not (root/'effect').exists()


def test_cancel_is_nonblocking_during_command_and_prevents_effect(child):
    make,root,_=child;owner=make();owner.open('task',1,SESSION,['0']);(root/'hold').touch();out=[]
    def approve():
        try:owner.approve('n','s',SESSION,approve_transmission=True)
        except PreparationConflict:out.append('refused')
    thread=threading.Thread(target=approve);thread.start()
    deadline=time.monotonic()+3
    while not (root/'entered').exists() and time.monotonic()<deadline:time.sleep(.01)
    assert (root/'entered').exists()
    poll=time.monotonic();assert owner.status()['status']=='PREPARING'
    assert time.monotonic()-poll<.25 and not owner._revoked.is_set()
    started=time.monotonic()
    assert owner.cancel(SESSION)['context_closed'] is False
    assert time.monotonic()-started<.25
    thread.join(4);assert not thread.is_alive() and out==['refused']
    assert not (root/'effect').exists();assert owner.shutdown(timeout=3)


def test_cancel_before_open_and_no_rebinding(child):
    make,root,_=child;owner=make()
    assert owner.shutdown(timeout=0) is False
    with pytest.raises(PreparationConflict):owner.open('task',1,SESSION,['0'])
    assert owner.shutdown(timeout=3)
    owner=make();owner.open('task',1,SESSION,['0'])
    with pytest.raises(PreparationConflict):owner.open('other',2,SESSION,['0'])
    assert owner.shutdown(timeout=3)


def test_exit_without_closed_proof_is_unknown_and_never_relaunched(child):
    make,root,launches=child;owner=make();owner.open('task',1,SESSION,['0'])
    owner._process.kill();owner._process.wait()
    assert owner.status()['status']=='UNKNOWN_OUTCOME'
    assert not owner.shutdown(timeout=.3)
    assert len(launches)==1


def test_explicit_unknown_closure_is_not_child_exit_success(child):
    make,root,_=child;(root/'unknown').touch();owner=make();owner.open('task',1,SESSION,['0'])
    assert owner.shutdown(timeout=2) is False
    assert owner.status()['status']=='UNKNOWN_OUTCOME'
    assert not owner._closed


@pytest.mark.parametrize('raw',[struct.pack('!I',m._LIMIT+1),struct.pack('!I',2)+b'[]',
    struct.pack('!I',13)+b'{"x":1,"x":2}',struct.pack('!I',3)+b'{xx'])
def test_malformed_frames_fail_without_raw_exception(raw):
    reader,writer=os.pipe()
    try:
        os.write(writer,raw);os.close(writer);writer=None
        with pytest.raises(PreparationConflict) as error:m._receive(reader,.1)
        assert str(error.value)=='preparation_authority_conflict'
    finally:m._close(reader);m._close(writer)


def test_partial_frame_times_out_without_waiting_forever():
    reader,writer=os.pipe()
    try:
        os.write(writer,struct.pack('!I',100)+b'x')
        start=time.monotonic()
        with pytest.raises(PreparationConflict):m._receive(reader,.05)
        assert time.monotonic()-start<.5
    finally:os.close(reader);os.close(writer)


def test_revoke_pipe_eof_closes_owned_controller_without_command(child):
    make,root,_=child;owner=make();owner.open('task',1,SESSION,['0'])
    # Simulates the independently inherited lifetime descriptor disappearing.
    m._close(owner._revoke);owner._revoke=None
    deadline=time.monotonic()+3
    while not (root/'closed').exists() and time.monotonic()<deadline:time.sleep(.01)
    assert (root/'closed').exists() and not (root/'effect').exists()
    assert owner.shutdown(timeout=3)


def test_guard_response_timeout_is_permanent_and_cannot_replay(child):
    make,root,_=child;entered=threading.Event();release=threading.Event();slow=[False]
    def valid(_):
        if slow[0]:entered.set();release.wait(5)
        return True
    owner=make(valid);owner.open('task',1,SESSION,['0']);slow[0]=True;results=[]
    def approve():
        try:owner.approve('n','s',SESSION,approve_transmission=True)
        except PreparationConflict:results.append('refused')
    thread=threading.Thread(target=approve);thread.start();assert entered.wait(1)
    thread.join(4);release.set();thread.join(2)
    assert results==['refused'] and not (root/'effect').exists()
    with pytest.raises(PreparationConflict):owner.approve('n','s',SESSION,approve_transmission=True)


def test_nonconsuming_pipe_peer_has_bounded_send():
    reader,writer=os.pipe();os.set_blocking(writer,False)
    try:
        start=time.monotonic()
        with pytest.raises(PreparationConflict):m._send(writer,{'value':'x'*100000},timeout=.05)
        assert time.monotonic()-start<.5
    finally:os.close(reader);os.close(writer)


def test_natural_closed_status_never_admits_replacement_before_child_exit(child):
    make,root,_=child;owner=make();owner.open('task',1,SESSION,['0'])
    (root/'natural_close').touch()
    observed=owner.status()
    assert observed['status']=='UNKNOWN_OUTCOME'
    deadline=time.monotonic()+3
    while not owner._closed and time.monotonic()<deadline:time.sleep(.01)
    assert owner._closed and owner._process.poll()==0
    assert owner.status()['status']=='CLOSED'


def test_actual_parent_death_revokes_child_and_child_exits(tmp_path):
    harness=tmp_path/'synthetic_private_owner.py';harness.write_text(HARNESS)
    package=str(Path(m.__file__).resolve().parents[2])
    # This bootstrap belongs only to this synthetic test, not any product
    # argument, environment variable or registered HTTP route.
    program='''import sys,time,subprocess
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from executor.preparation import private_child as m
root=Path(sys.argv[2]);real=subprocess.Popen
bootstrap="import sys;sys.path.insert(0,sys.argv[1]);sys.path.insert(0,"+repr(str(root))+");from synthetic_private_owner import Controller;from executor.preparation.private_child import worker_main;worker_main([int(x) for x in sys.argv[2:]],controller_factory=Controller)"
def launch(args,**kwargs):
 args=list(args);args[4]=bootstrap;return real(args,**kwargs)
m.subprocess.Popen=launch
owner=m.PrivatePreparationChild(SimpleNamespace(root=root),lambda _:True)
owner.open('task',1,'SYNTHETIC_SESSION_'+'s'*32,['0'])
(root/'ready').touch()
time.sleep(60)
'''
    parent=subprocess.Popen([sys.executable,'-I','-B','-c',program,package,str(tmp_path)],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/'ready').exists() and parent.poll() is None and time.monotonic()<deadline:time.sleep(.01)
        assert (tmp_path/'ready').exists()
        pid=int((tmp_path/'pid').read_text());parent.kill();parent.wait(timeout=3)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=subprocess.run(['ps','-o','stat=','-p',str(pid)],text=True,capture_output=True).stdout.strip()
            if not state or state.startswith('Z'):break
            time.sleep(.03)
        assert not state or state.startswith('Z')
        assert (tmp_path/'closed').exists() and not (tmp_path/'effect').exists()
    finally:
        if parent.poll() is None:parent.kill();parent.wait()


def test_failed_constructor_does_not_leave_threads_using_reused_descriptors(child,capfd):
    make,root,_=child;(root/'fail_init').touch()
    with pytest.raises(PreparationConflict) as error:make()
    assert CANARY not in str(error.value)
    (root/'fail_init').unlink()
    reader,writer=os.pipe()
    try:
        owner=make();owner.open('task',1,SESSION,['0']);assert owner.shutdown(timeout=3)
        os.write(writer,b'SYNTHETIC_FD_SENTINEL')
        assert os.read(reader,100)==b'SYNTHETIC_FD_SENTINEL'
        assert CANARY not in ''.join(capfd.readouterr())
    finally:os.close(reader);os.close(writer)


def test_guard_endpoints_are_not_reused_while_callback_is_still_running(child):
    make,root,_=child;slow=[False];entered=threading.Event();release=threading.Event()
    def valid(_):
        if slow[0]:entered.set();release.wait(10)
        return True
    owner=make(valid);owner.open('task',1,SESSION,['0']);old=(owner._guard_read,owner._guard_write)
    slow[0]=True;out=[]
    def approve():
        try:owner.approve('n','s',SESSION,approve_transmission=True)
        except PreparationConflict:out.append('refused')
    task=threading.Thread(target=approve);task.start();assert entered.wait(1)
    task.join(4);assert out==['refused']
    # Exact child cleanup can finish while the parent callback remains blocked.
    deadline=time.monotonic()+3
    while owner._process.poll() is None and time.monotonic()<deadline:time.sleep(.01)
    assert owner._process.poll() is not None
    reader,writer=os.pipe()
    try:
        assert all(fd not in old for fd in (reader,writer))
        for fd in old:os.fstat(fd)
        release.set();owner._guard_thread.join(2);assert not owner._guard_thread.is_alive()
        os.write(writer,b'SYNTHETIC_STILL_OWNED')
        assert os.read(reader,100)==b'SYNTHETIC_STILL_OWNED'
    finally:release.set();os.close(reader);os.close(writer)


def test_partial_pipe_allocation_releases_only_its_own_descriptors(monkeypatch):
    real=os.pipe;created=[]
    def pipe():
        if len(created)==2:raise OSError('SYNTHETIC_ALLOCATION_FAILURE')
        pair=real();created.append(pair);return pair
    monkeypatch.setattr(m.os,'pipe',pipe)
    with pytest.raises(PreparationConflict):m._allocate_pipes()
    for pair in created:
        for fd in pair:
            with pytest.raises(OSError):os.fstat(fd)


def test_shutdown_acknowledges_terminal_status_without_prior_poll(child):
    make,root,_=child;owner=make();owner.open('task',1,SESSION,['0'])
    owner._status_cache['final_status']='MANUAL_REVIEW_ACTIVE'
    (root/'final_returned').touch()
    assert owner.shutdown(timeout=3)
    assert owner.status()['status']=='CLOSED'
    assert owner.status()['final_status']=='RETURNED_UNVERIFIED'
