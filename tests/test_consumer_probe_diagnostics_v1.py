"""Synthetic checks for the one-nodeid observer, not packaged-runtime proof."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from executor.autonomy import consumer, release
import test_consumer_recovery_transactions_v1 as diagnostics


def _event(capsys):
    output = capsys.readouterr()
    assert output.err == ""
    assert "PRIVATE_" not in output.out
    event = json.loads(output.out)
    assert all(type(value) in (str, bool, int, type(None)) for value in event.values())
    assert "returncode_after_cleanup" in event and "returncode" not in event
    return event


def _closed(capture, fds):
    assert not capture._thread.is_alive()
    assert capture._reads == capture._writes == {}
    assert all(not data for data in capture._data.values())
    for fd in fds:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF


def _track_capture(monkeypatch):
    captures = []
    actual = diagnostics._ProbeOutput

    class Tracked(actual):
        def __init__(self):
            super().__init__()
            captures.append((self, [*self._reads.values(), *self._writes.values()]))

    monkeypatch.setattr(diagnostics, "_ProbeOutput", Tracked)
    return captures


@pytest.mark.parametrize("result", [True, False])
def test_diagnostic_preserves_real_process_arguments_methods_and_result(tmp_path, monkeypatch, capsys, result):
    captures = _track_capture(monkeypatch)
    real_subprocess = consumer.subprocess
    calls = []
    child = None
    command = [sys.executable, "-I", "-B", "-c", "print('PRIVATE_STDOUT');raise SystemExit(7)"]
    options = {"cwd": tmp_path, "env": {**os.environ, "PRIVATE_ENV": "PRIVATE_VALUE"},
               "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL}

    def popen(*args, **kwargs):
        nonlocal child
        calls.append((args, kwargs))
        child = subprocess.Popen(*args, **kwargs)
        return child

    monkeypatch.setattr(consumer, "subprocess", SimpleNamespace(Popen=popen))
    original_module = consumer.subprocess

    def probe(python, source):
        assert (python, source) == (Path(sys.executable), tmp_path)
        observed = consumer.subprocess.Popen(command, **options)
        assert observed is child and type(observed) is subprocess.Popen
        assert observed.poll.__func__ is subprocess.Popen.poll
        assert observed.terminate.__func__ is subprocess.Popen.terminate
        assert observed.wait.__func__ is subprocess.Popen.wait
        assert observed.stdout is observed.stderr is None
        assert observed.wait(timeout=5) == 7
        return result

    assert diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1) is result
    assert consumer.subprocess is original_module
    assert len(calls) == 1 and calls[0][0] == (command,)
    assert {key: value for key, value in calls[0][1].items() if key not in ("stdout", "stderr")} == {
        key: value for key, value in options.items() if key not in ("stdout", "stderr")}
    event = _event(capsys)
    assert event["outcome"] == ("healthy" if result else "unhealthy")
    assert event["returncode_after_cleanup"] == 7 and event["child_count"] == 1
    assert event["stdout_class"] == "unclassified" and event["reader_stopped"]
    for capture, fds in captures:
        _closed(capture, fds)
    monkeypatch.setattr(consumer, "subprocess", real_subprocess)


@pytest.mark.parametrize("reader_delay", [0, .001])
def test_diagnostic_continuously_drains_large_dual_output_with_eight_kib_limit(tmp_path, monkeypatch, capsys, reader_delay):
    captures = _track_capture(monkeypatch)
    actual_read = diagnostics._ProbeOutput._read_once
    def delayed_read(*args):
        if reader_delay:
            time.sleep(reader_delay)
        return actual_read(*args)
    monkeypatch.setattr(diagnostics._ProbeOutput, "_read_once", delayed_read)
    size = 2 * 1024 * 1024
    program = "import os\nfor _ in range(512):\n os.write(1,b'x'*4096);os.write(2,b'y'*4096)\n"

    def probe(*_):
        child = consumer.subprocess.Popen([sys.executable, "-I", "-B", "-c", program],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert child.wait(timeout=10) == 0  # More than pipe capacity on both streams.
        return True

    started = time.monotonic()
    assert diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1)
    assert time.monotonic() - started < 12
    event = _event(capsys)
    # Both streams must drain substantial output, but a bounded stop may leave
    # a final pipe buffer unread even after the original child exits normally.
    # In particular, mild reader scheduling delay must not change this oracle.
    assert size // 2 <= event["stdout_bytes_observed"] <= size
    assert size // 2 <= event["stderr_bytes_observed"] <= size
    assert event["stdout_bytes_retained"] + event["stderr_bytes_retained"] == 8192
    assert event["stdout_truncated"] and event["stderr_truncated"]
    assert not event["reader_error"] and event["reader_stopped"]
    for capture, fds in captures:
        _closed(capture, fds)


def test_diagnostic_stops_without_descendant_pipe_eof(tmp_path, monkeypatch, capsys):
    captures = _track_capture(monkeypatch)
    released, done = tmp_path / "release", tmp_path / "done"
    program = (
        "import os,pathlib,sys,time\n"
        "if os.fork()==0:\n"
        " deadline=time.monotonic()+10\n"
        " while not pathlib.Path(sys.argv[1]).exists() and time.monotonic()<deadline:time.sleep(.01)\n"
        " os.close(1);os.close(2);pathlib.Path(sys.argv[2]).touch();os._exit(0)\n"
        "print('{\"ok\": false, \"error\": \"operation_failed\"}');raise SystemExit(1)\n"
    )

    def probe(*_):
        child = consumer.subprocess.Popen([sys.executable, "-I", "-B", "-c", program,
                                          str(released), str(done)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert child.wait(timeout=5) == 1
        return False

    started = time.monotonic()
    try:
        assert diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1) is False
        assert time.monotonic() - started < 2
        assert not done.exists(), "the descendant must still hold the write ends"
        event = _event(capsys)
        assert event["stdout_class"] == "operation_failed"
        assert not event["stdout_eof_observed"] and not event["stderr_eof_observed"]
        assert event["reader_stopped"] and not event["reader_error"]
        for capture, fds in captures:
            _closed(capture, fds)
    finally:
        released.touch()
        deadline = time.monotonic() + 2
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert done.exists()


def test_diagnostic_spawn_exception_preserves_error_and_closes_fds(tmp_path, monkeypatch, capsys):
    captures = _track_capture(monkeypatch)
    error = OSError("PRIVATE_SPAWN_ERROR")
    calls = []

    def popen(*args, **kwargs):
        calls.append(1)
        raise error

    original = SimpleNamespace(Popen=popen)
    monkeypatch.setattr(consumer, "subprocess", original)
    def probe(*_):
        return consumer.subprocess.Popen(["PRIVATE_ARG"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with pytest.raises(OSError) as caught:
        diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1)
    assert caught.value is error and calls == [1] and consumer.subprocess is original
    assert _event(capsys)["outcome"] == "raised"
    for capture, fds in captures:
        _closed(capture, fds)


def test_diagnostic_setup_failure_keeps_original_single_launch(tmp_path, monkeypatch, capsys):
    calls = []
    def broken_capture():
        raise OSError("PRIVATE_CAPTURE_ERROR")
    def popen(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.Popen(*args, **kwargs)
    monkeypatch.setattr(diagnostics, "_ProbeOutput", broken_capture)
    monkeypatch.setattr(consumer, "subprocess", SimpleNamespace(Popen=popen))
    def probe(*_):
        child = consumer.subprocess.Popen([sys.executable, "-I", "-B", "-c", "pass"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return child.wait(timeout=5) == 0
    assert diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1)
    assert calls == [{"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}]
    assert _event(capsys)["capture_failed"] is True


def test_diagnostic_reader_error_keeps_draining_live_writer(tmp_path, monkeypatch, capsys):
    captures = _track_capture(monkeypatch)
    def broken_read(*_):
        raise OSError("PRIVATE_READ_ERROR")
    monkeypatch.setattr(diagnostics._ProbeOutput, "_read_once", broken_read)
    program = "import os\nfor _ in range(512):\n os.write(1,b'x'*4096);os.write(2,b'y'*4096)\n"
    def probe(*_):
        child = consumer.subprocess.Popen([sys.executable, "-I", "-B", "-c", program],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert child.wait(timeout=10) == 0, "diagnostic failure must not close or block the writer"
        return True
    assert diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1)
    event = _event(capsys)
    assert event["reader_error"] and event["reader_stopped"] and not event["observation_valid"]
    for capture, fds in captures:
        _closed(capture, fds)


@pytest.mark.parametrize("failure", ["finish", "_classify", "join"])
@pytest.mark.parametrize("raises", [False, True])
def test_diagnostic_finalization_failure_never_overrides_probe(tmp_path, monkeypatch, capsys, failure, raises):
    captures = _track_capture(monkeypatch)
    error = ValueError("PRIVATE_ORIGINAL_PROBE_ERROR")
    def broken(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_DIAGNOSTIC_ERROR")
    if failure != "join":
        monkeypatch.setattr(diagnostics._ProbeOutput, failure, broken)
    def probe(*_):
        child = consumer.subprocess.Popen([sys.executable, "-I", "-B", "-c", "print('PRIVATE_OUTPUT')"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert child.wait(timeout=5) == 0
        if failure == "join":
            monkeypatch.setattr(captures[0][0]._thread, "join", broken)
        if raises:
            raise error
        return False
    if raises:
        with pytest.raises(ValueError) as caught:
            diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1)
        assert caught.value is error
    else:
        assert diagnostics._observe_candidate_probe(probe, Path(sys.executable), tmp_path, 1) is False
    event = _event(capsys)
    assert event["outcome"] == ("raised" if raises else "unhealthy")
    assert not event["observation_valid"]
    for capture, fds in captures:
        _closed(capture, fds)


@pytest.mark.parametrize("failure", ["second_pipe", "thread_start"])
def test_diagnostic_partial_initialization_retires_every_fd(monkeypatch, failure):
    fds = []
    actual_pipe = os.pipe
    def pipe():
        if failure == "second_pipe" and fds:
            raise OSError("PRIVATE_PIPE_ERROR")
        pair = actual_pipe()
        fds.extend(pair)
        return pair
    def broken_start(*_):
        raise RuntimeError("PRIVATE_THREAD_ERROR")
    monkeypatch.setattr(diagnostics.os, "pipe", pipe)
    if failure == "thread_start":
        monkeypatch.setattr(diagnostics.threading.Thread, "start", broken_start)
    with pytest.raises((OSError, RuntimeError)):
        diagnostics._ProbeOutput()
    for fd in fds:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF


def test_diagnostic_repeated_finish_does_not_close_reused_fds():
    capture = diagnostics._ProbeOutput()
    original_fds = set(capture._reads.values()) | set(capture._writes.values())
    first = capture.finish()
    read_fd, write_fd = os.pipe()
    try:
        assert {read_fd, write_fd} & original_fds, "must actually exercise descriptor reuse"
        assert capture.finish() == first
        os.write(write_fd, b"safe")
        assert os.read(read_fd, 4) == b"safe"
        assert not capture._thread.is_alive()
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_diagnostic_empty_eof_closes_every_descriptor():
    capture = diagnostics._ProbeOutput()
    fds = [*capture._reads.values(), *capture._writes.values()]
    capture.close_writers()
    capture._thread.join(timeout=1)
    result = capture.finish()
    assert result["stdout_eof_observed"] and result["stderr_eof_observed"]
    assert result["stdout_class"] == result["stderr_class"] == "empty"
    assert not result["reader_error"] and result["reader_stopped"]
    _closed(capture, fds)


def test_diagnostic_does_not_turn_capture_sink_error_into_probe_failure(tmp_path, monkeypatch):
    import builtins
    original = consumer.subprocess
    def broken_sink(*_args, **_kwargs):
        raise ValueError("PRIVATE_SINK_ERROR")
    monkeypatch.setattr(builtins, "print", broken_sink)
    assert diagnostics._observe_candidate_probe(lambda *_: True, Path(sys.executable), tmp_path, 1) is True
    assert consumer.subprocess is original


@pytest.mark.parametrize("healthy", [True, False])
def test_diagnostic_observes_unchanged_real_probe_with_synthetic_source(tmp_path, capsys, healthy):
    from test_consumer_entry_v1 import _minimal_source
    repo = tmp_path / "fixture-source"
    repo.mkdir()
    _minimal_source(repo)
    if not healthy:
        cli = repo / "executor" / "autonomy" / "cli.py"
        cli.write_text(cli.read_text().replace("'final_click_actor': 'user'", "'final_click_actor': 'assistant'"))
    sealed = tmp_path / "release"
    release.copy_source_candidate(repo, sealed)
    assert diagnostics._observe_candidate_probe(consumer._candidate_starts, Path(sys.executable), sealed, 1) is healthy
    event = _event(capsys)
    assert event["child_count"] == 1 and event["reader_stopped"] and not event["reader_error"]
    assert event["outcome"] == ("healthy" if healthy else "unhealthy")


@pytest.mark.parametrize("name,observed", [
    ("test_first_recovery_move_rechecks_both_roles_before_restoring_private_authority[retained_resealed-rollback]", True),
    ("test_first_recovery_move_rechecks_both_roles_before_restoring_private_authority[retained_resealed-install]", False),
    ("test_first_recovery_move_rechecks_both_roles_before_restoring_private_authority[retained_replaced-rollback]", False),
])
def test_diagnostic_fixture_is_limited_to_original_failed_node(monkeypatch, capsys, name, observed):
    calls = []
    def original(*args):
        calls.append(args)
        return False
    monkeypatch.setattr(consumer, "_candidate_starts", original)
    diagnostics._diagnose_one_recovery_probe.__wrapped__(SimpleNamespace(node=SimpleNamespace(name=name)), monkeypatch)
    assert (consumer._candidate_starts is not original) is observed
    assert consumer._candidate_starts(Path("synthetic-python"), Path("synthetic-source")) is False
    assert len(calls) == 1
    if observed:
        assert _event(capsys)["child_count"] == 0
    else:
        assert capsys.readouterr() == ("", "")
