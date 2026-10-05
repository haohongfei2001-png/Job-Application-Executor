"""Bounded same-installation prepared-state recovery, never legacy migration."""
from __future__ import annotations

import os
import hashlib
import json
import stat
import sqlite3
from contextlib import contextmanager

import pytest

from test_macos_host_v1 import _pending_recovery_fixture, _recovery_record, _spare_port


@contextmanager
def startup_for(recovery, app, state):
    record = _recovery_record(recovery, app, state)
    read_fd, write_fd = os.pipe()
    startup = recovery._FirstUseStartup(state, _spare_port(),
                                       record, write_fd)
    try:
        with startup:
            with startup.worker_guard():
                yield startup
    finally:
        startup.__exit__(None, None, None)
        os.close(read_fd)


def checkpoint(tmp_path, monkeypatch):
    from executor.autonomy.queue import TaskQueue
    fixture = _pending_recovery_fixture(tmp_path, monkeypatch)
    module, recovery, consumer, host, app, state = fixture
    with monkeypatch.context() as fault:
        def interrupt(*args, **kwargs):
            raise OSError('synthetic prepared interruption')
        fault.setattr(module, '_complete_first_fence', interrupt)
        with pytest.raises(OSError, match='synthetic prepared interruption'):
            with startup_for(recovery, app, state) as startup:
                TaskQueue(state, _first_use_startup=startup)
    return fixture


def fingerprints(root):
    result={}
    for path in root.rglob('*'):
        entry=path.lstat()
        digest=(hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(entry.st_mode)
                else hashlib.sha256(os.readlink(path).encode()).hexdigest() if path.is_symlink() else None)
        result[path.relative_to(root).as_posix()]=(entry.st_dev,entry.st_ino,entry.st_mode,entry.st_nlink,entry.st_size,digest)
    return result


@pytest.mark.parametrize('fault_phase', ['before_completion', 'short_completion', 'server_bind'])
def test_prepared_first_use_resumes_same_objects_after_completion_interruption(tmp_path, monkeypatch, fault_phase):
    from executor.autonomy.queue import TaskQueue
    module,recovery,consumer,host,app,state=_pending_recovery_fixture(tmp_path,monkeypatch)
    record=_recovery_record(recovery,app,state)
    before_fence=module._fence_path(app.parent).read_bytes()
    read_fd,write_fd=os.pipe()
    startup=recovery._FirstUseStartup(state,_spare_port(),record,write_fd)
    with monkeypatch.context() as fault:
        def interrupt(*args,**kwargs):raise OSError('synthetic completion interruption')
        if fault_phase == 'server_bind':
            from executor.autonomy import supervisor
            fault.setattr(supervisor,'create_server',interrupt)
        elif fault_phase == 'short_completion':
            real_write = os.write; inode = module._fence_path(app.parent).stat().st_ino
            def short_write(fd, data):
                return real_write(fd, data[:-3] if os.fstat(fd).st_ino == inode else data)
            fault.setattr(module.os,'write',short_write)
        else:
            fault.setattr(module,'_complete_first_fence',interrupt)
        try:
            with pytest.raises(OSError):
                with startup:
                    with startup.worker_guard():TaskQueue(state,_first_use_startup=startup)
        finally:
            startup.__exit__(None,None,None);os.close(read_fd)
    assert (state/'tasks.sqlite3').is_file()
    retained=fingerprints(state)
    record=_recovery_record(recovery,app,state)
    assert module._fence_path(app.parent).read_bytes().startswith(before_fence)
    assert module._read_first_fence(app.parent)['status']=='pending'
    read_fd,write_fd=os.pipe()
    retry=recovery._FirstUseStartup(state,_spare_port(),record,write_fd)
    try:
        with retry:
            with retry.worker_guard():
                queue=TaskQueue(state,_first_use_startup=retry)
                assert queue.tasks()==[] and retry.committed is True
                from executor.facts.answers import TaskAnswerStore
                assert TaskAnswerStore(queue).cipher is not None
                retry.acknowledge(retry.components[1].service_identity())
                reply=recovery._read_startup_reply(read_fd,record['bundle_tag']);read_fd=None
                assert reply['bundle_tag']==record['bundle_tag']
    finally:
        retry.__exit__(None,None,None)
        if read_fd is not None:os.close(read_fd)
    assert fingerprints(state)==retained
    assert module._read_first_fence(app.parent)['status']=='complete'
    assert not (state/'service.json').exists() and not (state/'service.log').exists()


def test_prepared_initialization_uses_only_memory_sqlite_and_matches_normal_schema(tmp_path, monkeypatch):
    from executor.autonomy.queue import TaskQueue
    from executor.autonomy.worker import Worker
    from executor.autonomy.supervisor import Supervisor
    from executor.autonomy.state_compatibility import _snapshot
    real = sqlite3.connect
    opened = []
    with monkeypatch.context() as fault:
        def memory_only(path, *args, **kwargs):
            opened.append(path)
            assert path == ':memory:', 'prepared initialization opened a SQLite pathname'
            return real(path, *args, **kwargs)
        fault.setattr(sqlite3, 'connect', memory_only)
        module, recovery, consumer, host, app, state = checkpoint(tmp_path, monkeypatch)
    assert opened == [':memory:']
    ordinary = TaskQueue(tmp_path/'ordinary')
    Supervisor(ordinary, Worker(ordinary))
    with real(ordinary.path) as db:
        expected = _snapshot(db)
    with real(':memory:') as db:
        db.deserialize((state/'tasks.sqlite3').read_bytes())
        assert _snapshot(db) == expected
    assert _recovery_record(recovery, app, state)['authority_tag']


@pytest.mark.parametrize('fault', ['database_short', 'receipt_short', 'payload_fsync'])
def test_prepared_before_receipt_failure_preserves_and_refuses_partial_objects(tmp_path, monkeypatch, fault):
    from executor.autonomy import first_use_prepared as prepared
    from executor.autonomy.queue import TaskQueue
    module, recovery, consumer, host, app, state = _pending_recovery_fixture(tmp_path, monkeypatch)
    real_write, real_sync = os.write, os.fsync
    seen = []
    def write(fd, data):
        target = ('database_short' if data.startswith(b'SQLite format 3') else
                  'receipt_short' if b'"jae-first-use-prepared-v1"' in data else None)
        if target == fault:
            seen.append(True)
            return real_write(fd, data[:-5])
        return real_write(fd, data)
    def sync(fd):
        if fault == 'payload_fsync' and os.fstat(fd).st_size > 4096:
            seen.append(True)
            raise OSError('synthetic payload fsync failure')
        return real_sync(fd)
    with monkeypatch.context() as failure:
        failure.setattr(prepared.os, 'write', write)
        failure.setattr(prepared.os, 'fsync', sync)
        with pytest.raises(OSError):
            with startup_for(recovery, app, state) as startup:
                TaskQueue(state, _first_use_startup=startup)
    assert seen and (state/'tasks.sqlite3').exists()
    before = fingerprints(state)
    with pytest.raises((OSError, ValueError)):
        _recovery_record(recovery, app, state)
    assert fingerprints(state) == before
    assert module._read_first_fence(app.parent)['status'] == 'pending'
    assert not (state/'service.json').exists()


@pytest.mark.parametrize('fault', ['unknown', 'database_bytes', 'key_bytes', 'token_bytes',
    'database_clone', 'receipt_clone', 'receipt_partial', 'receipt_duplicate',
    'receipt_context', 'receipt_hardlink', 'database_symlink', 'key_mode', 'lock_clone'])
def test_prepared_mutated_or_unknown_state_is_preserved_and_refused(tmp_path, monkeypatch, fault):
    from executor.autonomy.first_use_prepared import RECEIPT
    module, recovery, consumer, host, app, state = checkpoint(tmp_path, monkeypatch)
    path = state/RECEIPT
    if fault == 'unknown':
        (state/'unknown-task-state').write_bytes(b'synthetic retained task state')
    elif fault.endswith('_bytes'):
        name = {'database_bytes':'tasks.sqlite3', 'key_bytes':'task-answers.key', 'token_bytes':'auth.token'}[fault]
        path = state/name; raw = path.read_bytes(); path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    elif fault in {'database_clone', 'receipt_clone', 'lock_clone'}:
        path = state/({'database_clone':'tasks.sqlite3','receipt_clone':RECEIPT,'lock_clone':'worker.lock'}[fault])
        raw = path.read_bytes(); path.rename(tmp_path/'retained-original'); path.write_bytes(raw); path.chmod(0o600)
    elif fault == 'receipt_partial':
        path.write_bytes(path.read_bytes()[:-1])
    elif fault == 'receipt_duplicate':
        path.write_bytes(path.read_bytes()[:-1] + b',"format":"jae-first-use-prepared-v1"}')
    elif fault == 'receipt_context':
        record = json.loads(path.read_bytes()); record['context']['fence_tag'] = 'f'*64
        path.write_text(json.dumps(record))
    elif fault == 'receipt_hardlink':
        os.link(path, tmp_path/'receipt-alias')
    elif fault == 'database_symlink':
        path = state/'tasks.sqlite3'; path.rename(tmp_path/'retained-original'); path.symlink_to(tmp_path/'retained-original')
    elif fault == 'key_mode':
        (state/'task-answers.key').chmod(0o644)
    before = fingerprints(state)
    monkeypatch.setattr(host, 'present_native_first_use_recovery', lambda *a, **k: pytest.fail('invalid state prompted'))
    with pytest.raises((OSError, ValueError)):
        _recovery_record(recovery, app, state)
    assert fingerprints(state) == before
    assert module._read_first_fence(app.parent)['status'] == 'pending'


@pytest.mark.parametrize('phase', ['prompt', 'health', 'child'])
def test_prepared_changes_between_admission_and_start_preserve_new_state(tmp_path, monkeypatch, phase):
    from executor.autonomy.queue import TaskQueue
    module, recovery, consumer, host, app, state = checkpoint(tmp_path, monkeypatch)
    record = _recovery_record(recovery, app, state)
    snapshots = []
    def change(*args, **kwargs):
        (state/'foreign-arrival').write_bytes(b'synthetic arrival')
        snapshots.append(fingerprints(state))
        return {'action':'resume_first_use'} if phase == 'prompt' else True
    if phase == 'child':
        change(); read_fd, write_fd = os.pipe()
        startup = recovery._FirstUseStartup(state, _spare_port(), record, write_fd)
        try:
            with pytest.raises(ValueError):
                with startup:
                    with startup.worker_guard():TaskQueue(state, _first_use_startup=startup)
        finally:
            startup.__exit__(None, None, None); os.close(read_fd)
    else:
        monkeypatch.setattr(host if phase == 'prompt' else consumer,
                            'present_native_first_use_recovery' if phase == 'prompt' else '_candidate_starts', change)
        with pytest.raises(ValueError):_recovery_record(recovery, app, state)
    assert fingerprints(state) == snapshots[-1]
    assert module._read_first_fence(app.parent)['status'] == 'pending'


def test_prepared_retagged_noninitial_database_never_replays_or_clears_state(tmp_path, monkeypatch):
    from executor.autonomy.first_use_prepared import RECEIPT
    from executor.autonomy.queue import TaskQueue
    module, recovery, consumer, host, app, state = checkpoint(tmp_path, monkeypatch)
    # Even a same-inode receipt edited to claim foreign contents is insufficient:
    # the existing initializer must independently reproduce exactly initial state.
    path = state/'tasks.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE synthetic_existing_private_data (value TEXT)')
        db.execute("INSERT INTO synthetic_existing_private_data VALUES ('preserve')")
    receipt = state/RECEIPT; record = json.loads(receipt.read_bytes()); raw = path.read_bytes()
    record['files']['tasks.sqlite3']['bytes'] = len(raw)
    record['files']['tasks.sqlite3']['sha256'] = hashlib.sha256(raw).hexdigest()
    receipt.write_text(json.dumps(record, sort_keys=True, separators=(',', ':')))
    before = fingerprints(state)
    with pytest.raises(ValueError, match='initial_state_changed'):
        with startup_for(recovery, app, state) as startup:TaskQueue(state, _first_use_startup=startup)
    assert fingerprints(state) == before
    assert module._read_first_fence(app.parent)['status'] == 'pending'


def test_prepared_cancel_retains_every_object_and_does_not_probe(tmp_path, monkeypatch):
    module, recovery, consumer, host, app, state = checkpoint(tmp_path, monkeypatch)
    before = fingerprints(state)
    monkeypatch.setattr(host, 'present_native_first_use_recovery', lambda *a, **k:{'action':'cancel'})
    monkeypatch.setattr(consumer, '_candidate_starts', lambda *a, **k:pytest.fail('cancel probed'))
    assert _recovery_record(recovery, app, state) is None
    assert fingerprints(state) == before


def test_prepared_unavailable_serialize_refuses_before_payload_creation(tmp_path, monkeypatch):
    from executor.autonomy.queue import TaskQueue
    module, recovery, consumer, host, app, state = _pending_recovery_fixture(tmp_path, monkeypatch)
    real = sqlite3.connect
    class NoSerialize(sqlite3.Connection):
        serialize = None
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k:real(*a, **k, factory=NoSerialize))
    before = fingerprints(state)
    with pytest.raises(ValueError, match='serialization_unavailable'):
        with startup_for(recovery, app, state) as startup:TaskQueue(state, _first_use_startup=startup)
    assert fingerprints(state) == before


@pytest.mark.parametrize('fault', ['target_arrival', 'root_replaced'])
def test_prepared_creation_keeps_actual_fd_ownership_and_never_overwrites(tmp_path, monkeypatch, fault):
    from executor.autonomy import first_use_prepared as prepared
    from executor.autonomy.queue import TaskQueue
    module, recovery, consumer, host, app, state = _pending_recovery_fixture(tmp_path, monkeypatch)
    real_open, real_write = os.open, os.write
    changed = []
    def opening(path, flags, *args, **kwargs):
        if path == 'tasks.sqlite3' and flags & os.O_CREAT and fault == 'target_arrival' and not changed:
            (state/'tasks.sqlite3').write_bytes(b'synthetic arriving journal'); changed.append(fingerprints(state))
        return real_open(path, flags, *args, **kwargs)
    def writing(fd, data):
        if data.startswith(b'SQLite format 3') and fault == 'root_replaced' and not changed:
            state.rename(tmp_path/'retained-created-root'); state.mkdir(mode=0o700)
            (state/'foreign').write_bytes(b'preserve replacement'); changed.append(fingerprints(state))
        return real_write(fd, data)
    with monkeypatch.context() as race:
        race.setattr(prepared.os, 'open', opening); race.setattr(prepared.os, 'write', writing)
        with pytest.raises((OSError, ValueError)):
            with startup_for(recovery, app, state) as startup:TaskQueue(state, _first_use_startup=startup)
    assert changed and fingerprints(state) == changed[-1]
    assert not (state/prepared.RECEIPT).exists()


def _startup_frames(recovery, tag='a'*64):
    progress = [{'format':'jae-first-use-preparing-v1', 'bundle_tag':tag, 'stage':stage}
                for stage in recovery._STARTUP_STAGES]
    ack = {'format':'jae-first-use-started-v1', 'bundle_tag':tag,
           'service':{'pid':123, 'port':9344, 'instance':'b'*32}}
    return [json.dumps(value, separators=(',', ':')).encode()+b'\n'
            for value in [*progress, ack]]


def _read_startup_bytes(recovery, payload):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload); os.close(write_fd)
    try:
        return recovery._read_startup_reply(read_fd, 'a'*64)
    finally:
        with pytest.raises(OSError):os.fstat(read_fd)


def test_startup_progress_coalesced_frames_require_all_boundaries_and_terminal_eof():
    from executor.autonomy import first_use_recovery as recovery
    frames = _startup_frames(recovery)
    assert len(b''.join(frames)) < 4096
    assert _read_startup_bytes(recovery, b''.join(frames)) == json.loads(frames[-1])
    assert recovery._STARTUP_STAGE_SECONDS == 30
    assert recovery._STARTUP_TOTAL_SECONDS == 165


@pytest.mark.parametrize('fault', [
    'empty', 'early_ack', 'repeat', 'skip', 'unknown', 'wrong_tag', 'extra_field',
    'duplicate_key', 'oversized', 'missing_ack', 'partial_ack', 'trailing',
    'extra_ack', 'extra_progress', 'empty_line', 'non_object',
])
def test_startup_progress_rejects_incomplete_unordered_or_unbounded_stream(fault):
    from executor.autonomy import first_use_recovery as recovery
    frames = _startup_frames(recovery)
    if fault == 'empty':frames=[]
    elif fault == 'early_ack':frames=[frames[-1]]
    elif fault == 'repeat':frames.insert(1, frames[0])
    elif fault == 'skip':frames.pop(1)
    elif fault == 'unknown':frames[0]=frames[0].replace(b'before_app_lock',b'heartbeat')
    elif fault == 'wrong_tag':frames[0]=frames[0].replace(b'a'*64,b'c'*64)
    elif fault == 'extra_field':frames[0]=frames[0].replace(b'}',b',"extra":true}')
    elif fault == 'duplicate_key':frames[0]=frames[0].replace(b'{',b'{"stage":"before_app_lock",',1)
    elif fault == 'oversized':frames=[b'x'*4097]
    elif fault == 'missing_ack':frames.pop()
    elif fault == 'partial_ack':frames[-1]=frames[-1][:-1]
    elif fault == 'trailing':frames.append(b'x')
    elif fault == 'extra_ack':frames.append(frames[-1])
    elif fault == 'extra_progress':frames.append(frames[0])
    elif fault == 'empty_line':frames.insert(1,b'\n')
    elif fault == 'non_object':frames[0]=b'[]\n'
    with pytest.raises((ValueError,UnicodeError)):
        _read_startup_bytes(recovery,b''.join(frames))


def test_startup_progress_fragmented_real_child_cannot_be_mistaken_for_ack(tmp_path):
    import subprocess, sys
    from executor.autonomy import first_use_recovery as recovery
    frames=_startup_frames(recovery)
    read_fd,write_fd=os.pipe()
    program=('import os,sys,time;fd=int(sys.argv[1]);data=bytes.fromhex(sys.argv[2]);'
             '\nfor value in data:\n os.write(fd,bytes([value]));time.sleep(.0001)\n'
             'os.close(fd)')
    child=subprocess.Popen([sys.executable,'-I','-B','-c',program,str(write_fd),
                            b''.join(frames).hex()],pass_fds=(write_fd,))
    os.close(write_fd)
    try:
        assert recovery._read_startup_reply(read_fd,'a'*64)==json.loads(frames[-1])
        assert child.wait(timeout=5)==0
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)


@pytest.mark.parametrize('mode', ['near_boundary', 'slow_stage', 'drip', 'total_bound', 'ack_no_eof'])
def test_startup_progress_deadlines_reset_only_on_real_bounded_stages(monkeypatch,mode):
    from executor.autonomy import first_use_recovery as recovery
    frames=_startup_frames(recovery); read_fd,write_fd=os.pipe()
    clock=[0.0]; sent=[]; closed=[False]
    monkeypatch.setattr(recovery.time,'monotonic',lambda:clock[0])
    waits=[]
    def select_ready(readers,writers,errors,remaining):
        assert readers==[read_fd] and not writers and not errors and remaining>0
        waits.append(remaining)
        index=len(sent)
        if mode=='slow_stage':clock[0]+=30.001;payload=frames[0]
        elif mode=='drip':clock[0]+=16;payload=b'{' if index==0 else b' '
        elif mode=='ack_no_eof':
            clock[0]+=.01 if index<10 else 29.8 if index==10 else .4
            payload=frames[index] if index<len(frames) else b' '
        else:
            clock[0]+=29.9 if index==0 or mode=='total_bound' else 13.4
            payload=frames[index]
        os.write(write_fd,payload);sent.append(payload)
        if mode=='near_boundary' and len(sent)==len(frames):
            os.close(write_fd);closed[0]=True
        return [read_fd],[],[]
    monkeypatch.setattr(recovery.select,'select',select_ready)
    # The final EOF is immediate, not another prepared boundary.
    original=select_ready
    def ready(*args):
        return ([read_fd],[],[]) if closed[0] else original(*args)
    monkeypatch.setattr(recovery.select,'select',ready)
    try:
        if mode=='near_boundary':
            assert recovery._read_startup_reply(read_fd,'a'*64)==json.loads(frames[-1])
            assert clock[0]==pytest.approx(163.9)
        else:
            with pytest.raises(ValueError,match='first_use_pipe_incomplete'):
                recovery._read_startup_reply(read_fd,'a'*64)
            assert len(sent)<=12
            if mode=='total_bound':
                assert len(sent)==6 and waits[-1]==pytest.approx(15.5)
                assert recovery._STARTUP_TOTAL_SECONDS==165
    finally:
        if not closed[0]:os.close(write_fd)


@pytest.mark.parametrize('boundary', ['before_app_lock','before_prepare','after_prepare','before_commit'])
def test_startup_reader_disconnect_cancels_only_at_verified_boundary_and_preserves_state(tmp_path,monkeypatch,boundary):
    from executor.autonomy.queue import TaskQueue
    module,recovery,consumer,host,app,state=_pending_recovery_fixture(tmp_path,monkeypatch)
    record=_recovery_record(recovery,app,state)
    read_fd,write_fd=os.pipe(); closed=False; retained=None; emitted=[]
    original=recovery._write_pipe
    def observe(fd,value,**kwargs):
        nonlocal closed,retained
        if value.get('stage')==boundary:
            retained=fingerprints(state)
            os.close(read_fd);closed=True
        original(fd,value,**kwargs)
        emitted.append(value['stage'])
    monkeypatch.setattr(recovery,'_write_pipe',observe)
    startup=recovery._FirstUseStartup(state,_spare_port(),record,write_fd)
    try:
        with pytest.raises(BrokenPipeError):
            with startup:
                with startup.worker_guard():TaskQueue(state,_first_use_startup=startup)
    finally:
        startup.__exit__(None,None,None)
        if not closed:os.close(read_fd)
    index=recovery._STARTUP_STAGES.index(boundary)
    assert emitted==list(recovery._STARTUP_STAGES[:index])
    assert module._read_first_fence(app.parent)['status']=='pending'
    assert fingerprints(state)==retained
    assert not (state/'service.json').exists()
    if boundary in {'before_app_lock','before_prepare'}:
        assert set(p.name for p in state.iterdir())==set(recovery._LOCKS)
    else:
        assert (state/'.first-use-prepared.json').is_file()
        assert _recovery_record(recovery,app,state)['bundle_tag']==record['bundle_tag']


def test_startup_progress_contains_only_successful_complete_verifications(tmp_path,monkeypatch):
    from executor.autonomy.queue import TaskQueue
    module,recovery,consumer,host,app,state=_pending_recovery_fixture(tmp_path,monkeypatch)
    record=_recovery_record(recovery,app,state); read_fd,write_fd=os.pipe()
    original=consumer._bundle_transaction_identity; calls=[]
    def identity(path):
        calls.append(path)
        return original(path) if len(calls)<4 else None
    monkeypatch.setattr(consumer,'_bundle_transaction_identity',identity)
    startup=recovery._FirstUseStartup(state,_spare_port(),record,write_fd)
    try:
        with pytest.raises(ValueError,match='first_use_admission_changed'):
            with startup:
                with startup.worker_guard():TaskQueue(state,_first_use_startup=startup)
    finally:startup.__exit__(None,None,None)
    raw=os.read(read_fd,4096);os.close(read_fd)
    assert [json.loads(line)['stage'] for line in raw.splitlines()]==list(recovery._STARTUP_STAGES[:3])
    assert len(calls)==4 and module._read_first_fence(app.parent)['status']=='pending'


def test_startup_lost_ack_after_commit_retains_exact_data_and_normal_reopen(tmp_path,monkeypatch):
    from executor.autonomy import cli
    from executor.autonomy.queue import TaskQueue
    module,recovery,consumer,host,app,state=_pending_recovery_fixture(tmp_path,monkeypatch)
    record=_recovery_record(recovery,app,state); read_fd,write_fd=os.pipe()
    startup=recovery._FirstUseStartup(state,_spare_port(),record,write_fd)
    with startup:
        with startup.worker_guard():
            TaskQueue(state,_first_use_startup=startup)
            retained=fingerprints(state);fence=module._fence_path(app.parent).read_bytes()
            assert startup.committed and startup.stage==len(recovery._STARTUP_STAGES)
            os.close(read_fd)
            with pytest.raises(BrokenPipeError):
                startup.acknowledge(startup.components[1].service_identity())
    assert fingerprints(state)==retained
    assert module._read_first_fence(app.parent)['status']=='complete'
    assert module._fence_path(app.parent).read_bytes()==fence
    with pytest.raises(ValueError):_recovery_record(recovery,app,state)
    launches=[]
    def ordinary(root,port,**options):
        assert '_first_use_record' not in options
        launches.append(root)
        return {'ok':True,'opened':True}
    monkeypatch.setattr(cli,'launch_native_consumer',ordinary)
    assert module.launch_native_entry(state,9344)['opened'] is True
    assert launches==[state] and fingerprints(state)==retained
    assert module._fence_path(app.parent).read_bytes()==fence


@pytest.mark.parametrize('fault',['wrong_registry','wrong_health','wrong_ack_pid','bad_progress'])
def test_startup_refusal_only_polls_its_real_child_and_never_signals_or_adopts_service(tmp_path,monkeypatch,fault):
    import subprocess, sys
    from executor.autonomy import cli,process_entry
    module,recovery,consumer,host,app,state=_pending_recovery_fixture(tmp_path,monkeypatch)
    record=_recovery_record(recovery,app,state)
    frames=_startup_frames(recovery,record['bundle_tag'])
    port=_spare_port()
    program=('import json,os,sys,time;'
        'args=sys.argv;fd=int(args[args.index("--ack-fd")+1]);'
        'frames=json.loads(args[1]);reply=json.loads(frames[-1]);'
        'reply["service"]["pid"]=os.getpid()'+(' + 1' if fault=='wrong_ack_pid' else '')+';'
        'reply["service"]["port"]='+str(port)+';'
        'frames[-1]=json.dumps(reply)+chr(10);'
        'os.write(fd,"".join(frames).encode());os.close(fd);time.sleep(10)')
    if fault=='bad_progress':frames[0]=frames[0].replace(b'before_app_lock',b'wrong_stage')
    monkeypatch.setattr(process_entry,'isolated_cli_command',lambda *args,**kw:
        [sys.executable,'-I','-B','-c',program,json.dumps([frame.decode() for frame in frames]),*args])
    real_popen=subprocess.Popen;children=[];polls=[]
    def spawn(*args,**kwargs):
        child=real_popen(*args,**kwargs);children.append(child)
        original_poll=child.poll
        def poll():polls.append(child.pid);return original_poll()
        child.poll=poll
        return child
    monkeypatch.setattr(recovery.subprocess,'Popen',spawn)
    registry_calls=[];request_calls=[]
    def registry(path):
        registry_calls.append(path)
        return {'pid':children[0].pid if fault!='wrong_registry' else 1,
                'port':port,'instance':'b'*32}
    monkeypatch.setattr(cli,'_service_record',registry)
    monkeypatch.setattr(cli,'lifecycle',lambda *a,**kw:pytest.fail('startup refusal used lifecycle'))
    monkeypatch.setattr(cli,'_stop_owned_service',lambda *a,**kw:pytest.fail('startup refusal stopped a service'))
    def request(*args,**kwargs):
        request_calls.append(args)
        raise ValueError('synthetic unverified health')
    monkeypatch.setattr(recovery,'_owned_request',request)
    try:
        with pytest.raises(ValueError):recovery.start_first_use(state,port,record)
        assert len(children)==1 and polls and set(polls)=={children[0].pid}
        assert bool(registry_calls)==(fault in {'wrong_registry','wrong_health'})
        assert bool(request_calls)==(fault=='wrong_health')
        assert children[0].poll() is None  # A committed/ambiguous child is not killed.
        assert set(p.name for p in state.iterdir())==set(recovery._LOCKS)
    finally:
        # Only this test's known sleep-only fixture child is terminated here.
        for child in children:child.kill();child.wait(timeout=5)


def test_startup_progress_parsing_cannot_renew_an_expired_lease(monkeypatch):
    from executor.autonomy import first_use_recovery as recovery
    frames=_startup_frames(recovery);clock=[0.0]
    original=json.loads
    def slow_parse(*args,**kwargs):
        value=original(*args,**kwargs)
        clock[0]=30.001
        return value
    monkeypatch.setattr(recovery.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(recovery.json,'loads',slow_parse)
    with pytest.raises(ValueError,match='first_use_pipe_incomplete'):
        _read_startup_bytes(recovery,b''.join(frames))


def test_startup_no_progress_wait_is_thirty_seconds_but_input_record_stays_fifteen(monkeypatch):
    import inspect
    from executor.autonomy import first_use_recovery as recovery
    read_fd,write_fd=os.pipe();waits=[]
    monkeypatch.setattr(recovery.time,'monotonic',lambda:0.0)
    def stalled(readers,writers,errors,seconds):
        waits.append(seconds)
        return [],[],[]
    monkeypatch.setattr(recovery.select,'select',stalled)
    try:
        with pytest.raises(ValueError,match='first_use_pipe_incomplete'):
            recovery._read_startup_reply(read_fd,'a'*64)
    finally:os.close(write_fd)
    assert waits==[30] and recovery._STARTUP_TOTAL_SECONDS==165
    assert inspect.signature(recovery._read_pipe).parameters['seconds'].default==15
