"""Offline transport failures with real owned workers, never upstream downloads."""
from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from scripts import prepare_standalone_runtime as runtime


# Substitute only the worker's transport. Execute the production CLI, stream
# writer and parent lifecycle in real processes, including blocked reads.
WORKER = r"""
import base64, http.client, io, json, os, runpy, ssl, sys, time, urllib.error
from pathlib import Path
source, archive, timeout, mode, encoded, audit = sys.argv[1:]
if mode == 'blocked_startup':
    print('startup', flush=True)
    sys.stdin.buffer.read(1)
module = runpy.run_path(source)
payload = base64.b64decode(encoded)
def record(stage, **extra):
    Path(audit).write_text(json.dumps(dict(stage=stage, pid=os.getpid(), **extra)))
    if stage in ('blocked_open', 'blocked_read') or (mode == 'disconnect' and stage == 'request'):
        print(stage, flush=True)
class Response(io.BytesIO):
    def read(self, size=-1):
        if mode == 'partial_disconnect':
            if self.tell():
                raise http.client.RemoteDisconnected('offline partial EOF')
            return super().read(3)
        if mode == 'blocked_read':
            record('blocked_read')
            time.sleep(30)
        return super().read(size)
def request(url, *, timeout):
    record('request', url=url, timeout=timeout)
    if mode == 'disconnect':
        raise http.client.RemoteDisconnected('offline EOF')
    if mode == 'http':
        raise urllib.error.HTTPError(url, 503, 'offline HTTP error', {}, None)
    if mode == 'tls':
        raise ssl.SSLCertVerificationError('offline certificate failure')
    if mode == 'permission':
        raise PermissionError('offline permission failure')
    if mode == 'incomplete':
        raise http.client.IncompleteRead(b'partial')
    if mode == 'wrapped_disconnect':
        raise urllib.error.URLError(http.client.RemoteDisconnected('wrapped'))
    if mode == 'blocked_open':
        record('blocked_open')
        time.sleep(30)
    return Response(payload)
module['urllib'].request.urlopen = request
sys.argv = [source, '--download-once', archive, timeout]
raise SystemExit(module['main']())
"""


@pytest.fixture
def workers(tmp_path, monkeypatch):
    original = subprocess.Popen
    children, paths, commands, audits = [], [], [], []

    def install(modes, payload=b'complete archive', *, ready_stages=(), on_ready=None,
                startup_delay=0):
        pending = iter(modes)
        ready = iter(ready_stages)

        def spawn(command):
            mode = next(pending)  # An unexpected extra attempt fails the test.
            assert command[:3] == [sys.executable, '-I', '-B']
            assert Path(command[3]).resolve() == Path(runtime.__file__).resolve()
            assert command[4] == '--download-once'
            path = Path(command[5])
            assert not path.exists()
            assert all(not previous.parent.exists() for previous in paths)
            paths.append(path)
            commands.append(command)
            audit = tmp_path / f'worker-{len(paths)}.json'
            audits.append(audit)
            script = f'import time; time.sleep({startup_delay!r})\n' + WORKER
            child = original(
                [sys.executable, '-I', '-B', '-c', script, command[3], str(path),
                 command[6], mode, base64.b64encode(payload).decode(), str(audit)],
                stdin=subprocess.PIPE if mode == 'blocked_startup' else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            children.append(child)
            stage = next(ready, None)
            if stage is not None:
                try:
                    # A harness readiness guard, independent of the simulated
                    # download clock: scheduling cannot consume the I/O test.
                    assert select.select([child.stdout], [], [], 15)[0], 'worker not ready'
                    assert os.read(child.stdout.fileno(), 128).decode().strip() == stage
                    if on_ready is not None:
                        on_ready(len(children), child)
                except BaseException:
                    # Until spawn returns, the harness owns this child.
                    with child:
                        child.kill()
                        child.wait()
                    raise
            return child

        monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)

    yield install, children, paths, commands, audits
    for child in children:
        assert child.poll() is not None
        assert child.stdout.closed and child.stderr.closed
        assert child.stdin is None or child.stdin.closed
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    assert all(not path.parent.exists() for path in paths)


@pytest.fixture
def download_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    # Replace only the helper's module reference. Popen's real wait timeout and
    # the harness readiness guard continue using the operating system clock.
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    return clock


@pytest.fixture
def prepared_inputs(tmp_path, monkeypatch):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w:gz') as archive:
        member = tarfile.TarInfo('python/bin/python3')
        content = b'#!/bin/false\n'
        member.size, member.mode = len(content), 0o755
        archive.addfile(member, io.BytesIO(content))
    data = payload.getvalue()
    key = (runtime.platform.system(), runtime.platform.machine())
    name, _ = runtime.PIN[key]
    monkeypatch.setattr(runtime, 'PIN', {key: (name, hashlib.sha256(data).hexdigest())})
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('fixture==1\n')
    calls = []

    def pip(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runtime.subprocess, 'run', pip)
    return data, requirements, calls, name


def test_disconnect_then_verified_prepare_keeps_source_and_provenance(
    tmp_path, workers, prepared_inputs
):
    install, children, _, _, audits = workers
    data, requirements, pip_calls, name = prepared_inputs
    install(['disconnect', 'success'], data)
    root = runtime.prepare(tmp_path / 'runtime', requirements)
    assert len(children) == 2
    expected_url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260924/' + name.replace('+', '%2B')
    assert [json.loads(path.read_text())['url'] for path in audits] == [expected_url] * 2
    assert (root / 'bin/python').is_symlink()
    assert len(pip_calls) == 1
    assert pip_calls[0] == ([str(root / 'bin/python3'), '-I', '-B', '-m', 'pip',
        '--isolated', 'install', '--no-deps', '--no-compile', '--no-cache-dir',
        '--disable-pip-version-check', '-r', str(requirements.resolve())],
        {'check': True, 'timeout': 180})
    assert json.loads((root / 'python-runtime-provenance.json').read_text()) == {
        'format': 'jae-python-upstream-v1', 'repository': 'astral-sh/python-build-standalone',
        'release': '20260924', 'asset': name, 'archive_sha256': hashlib.sha256(data).hexdigest(),
        'requirements_sha256': hashlib.sha256(requirements.read_bytes()).hexdigest(),
    }


def test_only_three_disconnected_attempts_and_no_retained_archive(tmp_path, workers):
    install, children, _, _, _ = workers
    install(['disconnect'] * 3)
    archive = tmp_path / 'runtime.tar.gz'
    with pytest.raises(http.client.RemoteDisconnected, match='attempts_exhausted'):
        runtime._download_archive(archive)
    assert len(children) == 3
    assert not archive.exists()


def test_partial_disconnected_bytes_are_discarded_in_fresh_attempt(tmp_path, workers):
    install, children, paths, _, _ = workers
    install(['partial_disconnect', 'success'], b'0123456789')
    archive = tmp_path / 'runtime.tar.gz'
    runtime._download_archive(archive)
    assert len(children) == 2
    assert paths[0] != paths[1]
    assert archive.read_bytes() == b'0123456789'


@pytest.mark.parametrize('mode', ['blocked_open', 'blocked_read'])
@pytest.mark.parametrize('startup_delay', [0, .9])
def test_deadline_kills_and_reaps_blocked_worker(
    tmp_path, workers, download_clock, mode, startup_delay
):
    install, children, _, _, audits = workers
    install([mode], ready_stages=[mode], startup_delay=startup_delay,
            on_ready=lambda *_: setattr(download_clock, 'now', 59.9))
    archive = tmp_path / 'runtime.tar.gz'
    with pytest.raises(subprocess.TimeoutExpired):
        runtime._download_archive(archive)
    assert len(children) == 1 and children[0].returncode < 0
    assert json.loads(audits[0].read_text())['stage'] == mode
    assert not archive.exists()


def test_attempts_share_deadline_instead_of_resetting_it(tmp_path, workers, download_clock):
    install, children, _, commands, audits = workers
    def advance(attempt, _child):
        download_clock.now = 20 if attempt == 1 else 59.9
    install(['disconnect', 'blocked_read'], ready_stages=['request', 'blocked_read'],
            on_ready=advance)
    with pytest.raises(subprocess.TimeoutExpired):
        runtime._download_archive(tmp_path / 'runtime.tar.gz')
    assert len(children) == 2
    assert [float(command[-1]) for command in commands] == [60, 40]
    assert json.loads(audits[1].read_text())['stage'] == 'blocked_read'


def test_process_creation_time_is_debited_before_wait(tmp_path, workers, download_clock):
    install, children, _, _, _ = workers
    waits = []

    def creation_finished(_attempt, child):
        wait = child.wait
        def record_wait(*args, **kwargs):
            waits.append(kwargs.get('timeout'))
            return wait(*args, **kwargs)
        child.wait = record_wait
        download_clock.now = 59.75

    install(['blocked_read'], ready_stages=['blocked_read'], on_ready=creation_finished)
    with pytest.raises(subprocess.TimeoutExpired):
        runtime._download_archive(tmp_path / 'runtime.tar.gz')
    assert waits[0] == .25
    assert len(children) == 1 and children[0].returncode < 0


def test_startup_exhaustion_kills_before_audit_or_archive(tmp_path, workers, download_clock):
    install, children, _, _, audits = workers
    install(['blocked_startup'], ready_stages=['startup'],
            on_ready=lambda *_: setattr(download_clock, 'now', 60))
    archive = tmp_path / 'runtime.tar.gz'
    with pytest.raises(subprocess.TimeoutExpired):
        runtime._download_archive(archive)
    assert len(children) == 1 and children[0].returncode < 0
    assert not audits[0].exists() and not archive.exists()


def test_readiness_guard_failure_cleans_its_child(tmp_path, monkeypatch, workers):
    install, children, _, _, _ = workers
    install(['blocked_startup'], ready_stages=['startup'])
    monkeypatch.setattr(select, 'select', lambda *_: ([], [], []))
    with pytest.raises(AssertionError, match='worker not ready'):
        runtime._download_archive(tmp_path / 'runtime.tar.gz')
    assert len(children) == 1 and children[0].returncode < 0
    assert not (tmp_path / 'runtime.tar.gz').exists()


def test_parent_interruption_reaps_only_its_worker(tmp_path, monkeypatch, workers):
    install, children, _, _, _ = workers
    install(['blocked_read'])
    spawn = runtime.subprocess.Popen

    def interrupted_spawn(command):
        child = spawn(command)
        wait = child.wait
        def interrupt_once(*args, **kwargs):
            child.wait = wait
            raise KeyboardInterrupt
        child.wait = interrupt_once
        return child

    monkeypatch.setattr(runtime.subprocess, 'Popen', interrupted_spawn)
    with pytest.raises(KeyboardInterrupt):
        runtime._download_archive(tmp_path / 'runtime.tar.gz')
    assert len(children) == 1 and children[0].returncode < 0
    assert not (tmp_path / 'runtime.tar.gz').exists()


@pytest.mark.parametrize('mode', ['http', 'tls', 'permission', 'incomplete', 'wrapped_disconnect'])
def test_unrelated_errors_never_retry(tmp_path, workers, mode):
    install, children, _, _, _ = workers
    install([mode])
    with pytest.raises(subprocess.CalledProcessError):
        runtime._download_archive(tmp_path / 'runtime.tar.gz')
    assert len(children) == 1
    assert not (tmp_path / 'runtime.tar.gz').exists()


def test_parent_digest_mismatch_prevents_extraction_pip_and_provenance(
    tmp_path, workers, prepared_inputs, monkeypatch
):
    install, children, _, _, _ = workers
    _, requirements, pip_calls, _ = prepared_inputs
    install(['success'], b'wrong bytes')
    def forbidden(*args, **kwargs):
        pytest.fail('must verify the parent archive before extraction')
    monkeypatch.setattr(runtime.tarfile, 'open', forbidden)
    destination = tmp_path / 'runtime'
    with pytest.raises(ValueError, match='standalone_archive_digest_mismatch'):
        runtime.prepare(destination, requirements)
    assert len(children) == 1 and not pip_calls
    assert list(destination.iterdir()) == []


def test_verified_but_malformed_archive_never_retries(tmp_path, workers, prepared_inputs, monkeypatch):
    install, children, _, _, _ = workers
    _, requirements, pip_calls, name = prepared_inputs
    data = b'not a tar archive'
    key = (runtime.platform.system(), runtime.platform.machine())
    monkeypatch.setattr(runtime, 'PIN', {key: (name, hashlib.sha256(data).hexdigest())})
    install(['success'], data)
    with pytest.raises(tarfile.ReadError):
        runtime.prepare(tmp_path / 'runtime', requirements)
    assert len(children) == 1 and not pip_calls
    assert list((tmp_path / 'runtime').iterdir()) == []


def test_data_filter_still_refuses_escaping_archive(tmp_path, workers, prepared_inputs, monkeypatch):
    install, children, _, _, _ = workers
    _, requirements, pip_calls, name = prepared_inputs
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        member = tarfile.TarInfo('../outside')
        archive.addfile(member)
    payload = data.getvalue()
    key = (runtime.platform.system(), runtime.platform.machine())
    monkeypatch.setattr(runtime, 'PIN', {key: (name, hashlib.sha256(payload).hexdigest())})
    install(['success'], payload)
    with pytest.raises(tarfile.OutsideDestinationError):
        runtime.prepare(tmp_path / 'runtime', requirements)
    assert len(children) == 1 and not pip_calls
    assert not (tmp_path / 'outside').exists()


def test_existing_destination_is_untouched_and_never_downloaded(tmp_path, workers, prepared_inputs):
    install, children, _, _, _ = workers
    _, requirements, pip_calls, _ = prepared_inputs
    install([])
    destination = tmp_path / 'runtime'
    destination.mkdir()
    marker = destination / 'owned'
    marker.write_text('unchanged')
    with pytest.raises(FileExistsError):
        runtime.prepare(destination, requirements)
    assert not children and not pip_calls
    assert marker.read_text() == 'unchanged'
