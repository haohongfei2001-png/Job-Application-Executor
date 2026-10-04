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
                reply=recovery._read_pipe(read_fd);read_fd=None
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
