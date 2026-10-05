"""Explicit pending-first-use recovery and its guarded, owned startup handoff.

No migration, cleanup, automatic retry or alternate authority. Records contain
only finite identity digests; every path is derived from the managed app source.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
import hashlib
import fcntl
import json
import os
from pathlib import Path
import select
import secrets
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request


_FORMAT = 'jae-first-use-startup-v1'
_FIELDS = {'format', 'bundle_tag', 'fence_tag', 'authority_tag'}
_LOCKS = ('native-window.lock', 'worker.lock', 'migration.lock')
# These are completed admission boundaries, never heartbeats or authority.
# Real signed verification measured up to 14.841 seconds per boundary.
# A 30-second idle limit adds per-stage margin; the absolute bound stays 165.
_STARTUP_STAGES = (
    'before_app_lock', 'after_app_lock',
    'before_worker_lock', 'after_worker_lock',
    'before_migration_lock', 'after_migration_lock',
    'before_queue', 'before_prepare', 'after_prepare', 'before_commit',
)
_STARTUP_STAGE_SECONDS = 30
_STARTUP_TOTAL_SECONDS = 165


def _tag(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('first_use_record_invalid')
        value[key] = item
    return value


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _startup_record(value):
    if (type(value) is not dict or set(value) != _FIELDS or value['format'] != _FORMAT
            or not all(_digest(value[key]) for key in _FIELDS - {'format'})):
        raise ValueError('first_use_record_invalid')
    return dict(value)


def _read_pipe(fd, *, seconds=15):
    if type(fd) is not int or not 3 <= fd <= 2147483647:
        raise ValueError('first_use_pipe_invalid')
    try:
        entry = os.fstat(fd)
        if not stat.S_ISFIFO(entry.st_mode) or entry.st_uid != os.geteuid():
            raise ValueError('first_use_pipe_invalid')
        os.set_blocking(fd, False)
        deadline, data = time.monotonic() + seconds, bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(data) > 4096:
                raise ValueError('first_use_pipe_incomplete')
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise ValueError('first_use_pipe_incomplete')
            chunk = os.read(fd, min(1024, 4097 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        return json.loads(data, object_pairs_hook=_unique)
    finally:
        os.close(fd)


def _write_pipe(fd, value, *, framed=False):
    entry = os.fstat(fd)
    if not stat.S_ISFIFO(entry.st_mode) or entry.st_uid != os.geteuid():
        raise ValueError('first_use_pipe_invalid')
    data = json.dumps(value, separators=(',', ':')).encode() + (b'\n' if framed else b'')
    if len(data) > 4096:
        raise ValueError('first_use_record_invalid')
    os.set_blocking(fd, False)
    if os.write(fd, data) != len(data):
        raise OSError('first_use_pipe_incomplete')


def _read_startup_reply(fd, bundle_tag):
    """Finite ordered preparation, followed by the original success ACK + EOF.

    Bytes and partial frames never renew a lease. Ten independently verified
    boundaries permit at most 165 seconds overall, not an unbounded heartbeat.
    No progress message admits a service or changes the final health checks.
    """
    if type(fd) is not int or not 3 <= fd <= 2147483647 or not _digest(bundle_tag):
        raise ValueError('first_use_pipe_invalid')
    try:
        entry = os.fstat(fd)
        if (not stat.S_ISFIFO(entry.st_mode) or entry.st_uid != os.geteuid()
                or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY):
            raise ValueError('first_use_pipe_invalid')
        os.set_blocking(fd, False)
        started = time.monotonic()
        deadline = started + _STARTUP_TOTAL_SECONDS
        lease = started + _STARTUP_STAGE_SECONDS
        data, total, stage, reply = bytearray(), 0, 0, None
        while True:
            remaining = min(deadline, lease) - time.monotonic()
            if remaining <= 0:
                raise ValueError('first_use_pipe_incomplete')
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise ValueError('first_use_pipe_incomplete')
            chunk = os.read(fd, min(1024, 4097 - total))
            if time.monotonic() >= min(deadline, lease):
                raise ValueError('first_use_pipe_incomplete')
            if not chunk:
                if data or reply is None:
                    raise ValueError('first_use_pipe_incomplete')
                return reply
            total += len(chunk)
            if total > 4096:
                raise ValueError('first_use_record_invalid')
            data.extend(chunk)
            while b'\n' in data:
                if time.monotonic() >= min(deadline, lease):
                    raise ValueError('first_use_pipe_incomplete')
                line, _, tail = data.partition(b'\n')
                data = bytearray(tail)
                value = json.loads(line, object_pairs_hook=_unique)
                if reply is not None:
                    raise ValueError('first_use_progress_invalid')
                if stage < len(_STARTUP_STAGES):
                    if value != {'format':'jae-first-use-preparing-v1',
                            'bundle_tag':bundle_tag, 'stage':_STARTUP_STAGES[stage]}:
                        raise ValueError('first_use_progress_invalid')
                    now = time.monotonic()
                    if now >= min(deadline, lease):
                        raise ValueError('first_use_pipe_incomplete')
                    stage += 1
                    lease = now + _STARTUP_STAGE_SECONDS
                else:
                    if (type(value) is not dict or set(value) != {'format','bundle_tag','service'}
                            or value['format'] != 'jae-first-use-started-v1'
                            or value['bundle_tag'] != bundle_tag):
                        raise ValueError('first_use_ack_unverified')
                    reply = value
    finally:
        os.close(fd)


def _entry(path, *, directory=False):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('first_use_authority_changed')
    entry = path.stat(follow_symlinks=False)
    if ((not stat.S_ISDIR(entry.st_mode) if directory else not stat.S_ISREG(entry.st_mode))
            or entry.st_uid != os.geteuid()
            or stat.S_IMODE(entry.st_mode) != (0o700 if directory else 0o600)
            or not directory and entry.st_nlink != 1):
        raise ValueError('first_use_authority_changed')
    return (entry.st_dev, entry.st_ino, stat.S_IMODE(entry.st_mode), entry.st_uid)


def _authority_tag(state):
    # Bind the previously admitted empty root and its actual lock inodes. A new
    # empty directory at the same pathname is not the same first-use authority.
    return _tag([_entry(state, directory=True), *[_entry(state / name) for name in _LOCKS]])


def _fence_tag(apps):
    from .first_install import _fence_path, _read_first_fence
    path = _fence_path(apps)
    before = _entry(path)
    record = _read_first_fence(apps)
    if record is None or record['status'] != 'pending' or _entry(path) != before:
        raise ValueError('first_use_fence_changed')
    return _tag([before, record])


def capture_startup_record(app, state, identity):
    from .first_install import _bundle_tag, _read_first_fence
    record = _read_first_fence(app.parent)
    if record != {'format':'jae-first-install-fence-v1',
                  'bundle_tag':_bundle_tag(identity), 'status':'pending'}:
        raise ValueError('first_use_fence_changed')
    context, prepared = _prepared_admission(app, state, identity)
    from .first_use_prepared import admission_tag
    return {'format':_FORMAT, 'bundle_tag':_bundle_tag(identity),
            'fence_tag':context['fence_tag'], 'authority_tag':admission_tag(context, prepared)}


def _prepared_admission(app, state, identity):
    from .first_install import _bundle_tag
    from .first_use_prepared import inspect
    context = {'bundle_tag':_bundle_tag(identity), 'fence_tag':_fence_tag(app.parent),
               'root_tag':_authority_tag(state)}
    return context, inspect(state, context)


def _admission_tag(app, state, identity):
    from .first_use_prepared import admission_tag
    context, prepared = _prepared_admission(app, state, identity)
    return admission_tag(context, prepared)


def _paths(root):
    from . import consumer
    from .runtime_paths import default_runtime
    source = Path(__file__).absolute().parents[2]
    app = source.parent.parent.parent
    target = Path.home().absolute() / 'Applications' / (consumer.APP_NAME + '.app')
    state = Path(default_runtime(source)).absolute()
    if (sys.platform != 'darwin' or app != target or Path(root).absolute() != state
            or source != app / 'Contents/Resources/release'):
        raise ValueError('first_use_source_unverified')
    return source, app, state


def _known_legacy_absent(source, app, state):
    from .consumer import _legacy_state_migration_needed
    # Read-only finite observations. Default-root locks do not lock arbitrary
    # legacy writers, and no missing in-bundle directory is ever created here.
    return not any(_legacy_state_migration_needed(old, app, state) for old in (
        Path.home().absolute() / 'Job-Application-Executor', source))


class _FirstUseStartup(AbstractContextManager):
    """Internal callback object. Only the strict local child constructs it."""
    def __init__(self, root, port, record, ack_fd):
        self.source, self.app, self.state = _paths(root)
        self.record = _startup_record(record)
        if type(port) is not int or not 0 < port < 65536:
            raise ValueError('first_use_port_invalid')
        if type(ack_fd) is not int or not 3 <= ack_fd <= 2147483647:
            raise ValueError('first_use_pipe_invalid')
        entry = os.fstat(ack_fd)
        if (not stat.S_ISFIFO(entry.st_mode) or entry.st_uid != os.geteuid()
                or fcntl.fcntl(ack_fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_WRONLY):
            raise ValueError('first_use_pipe_invalid')
        self.port, self.ack_fd = port, ack_fd
        self.app_fd = None
        self.components = None
        self.worker_fd = None
        self.committed = False
        self.queue = None
        self.context = None
        self.prepared = None
        self._key = self._token = None
        self.stage = 0

    def verify(self, *, empty, stage):
        from . import consumer
        from .first_install import _bundle_tag
        identity = consumer._bundle_transaction_identity(self.app)
        if (identity is None or _bundle_tag(identity) != self.record['bundle_tag']
                or _fence_tag(self.app.parent) != self.record['fence_tag']
                or _admission_tag(self.app, self.state, identity) != self.record['authority_tag']
                or not _known_legacy_absent(self.source, self.app, self.state)):
            raise ValueError('first_use_admission_changed')
        if self.stage >= len(_STARTUP_STAGES) or stage != _STARTUP_STAGES[self.stage]:
            raise ValueError('first_use_progress_invalid')
        # Cancellation is observed only after a complete read-only admission
        # check. A lost reader cannot interrupt prepared-state/fence writes.
        _write_pipe(self.ack_fd, {'format':'jae-first-use-preparing-v1',
            'bundle_tag':self.record['bundle_tag'], 'stage':stage}, framed=True)
        self.stage += 1
        return identity

    def __enter__(self):
        from .consumer import _acquire_app_transaction_lock
        # Preliminary identity before ProcessLock/private_dir can mkdir/chmod.
        self.verify(empty=True, stage='before_app_lock')
        self.app_fd = _acquire_app_transaction_lock(self.app.parent)
        try:
            self.verify(empty=True, stage='after_app_lock')
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def bound_lock_fd(self, name):
        if name not in {'worker.lock', 'migration.lock'} or self.app_fd is None:
            raise ValueError('first_use_lock_invalid')
        stage = name.removesuffix('.lock')
        self.verify(empty=True, stage='before_' + stage + '_lock')
        path = self.state/name
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            owned = os.fstat(fd)
            if (owned.st_dev, owned.st_ino, stat.S_IMODE(owned.st_mode), owned.st_uid) != _entry(path):
                raise ValueError('first_use_lock_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.verify(empty=True, stage='after_' + stage + '_lock')
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def worker_guard(self):
        fd = self.bound_lock_fd('worker.lock')
        try:
            self.bind_worker(fd)
            yield fd
        finally:
            self.worker_fd = None
            os.close(fd)

    def bind_worker(self, worker_fd):
        expected = _entry(self.state/'worker.lock')
        owned = os.fstat(worker_fd)
        if (owned.st_dev, owned.st_ino, stat.S_IMODE(owned.st_mode), owned.st_uid) != expected:
            raise ValueError('first_use_worker_unverified')
        self.worker_fd = worker_fd

    def before_queue(self, queue):
        if (queue.root != self.state or self.components is not None
                or self.app_fd is None or self.worker_fd is None):
            raise ValueError('first_use_queue_unverified')
        self.bind_worker(self.worker_fd)
        identity = self.verify(empty=True, stage='before_queue')  # Both actual locks held.
        from cryptography.fernet import Fernet
        self.context, self.prepared = _prepared_admission(self.app, self.state, identity)
        self.queue = queue
        if self.prepared is None:
            self._key = Fernet.generate_key()
            self._token = secrets.token_urlsafe(32).encode('ascii')
        else:
            self._key = self.prepared.payloads['task-answers.key']
            self._token = self.prepared.payloads['auth.token']
        if len(self._key) != 44 or len(self._token) != 43:
            raise ValueError('first_use_prepared_credentials_invalid')
        Fernet(self._key)
        if any(c not in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for c in self._token):
            raise ValueError('first_use_prepared_credentials_invalid')
        connection = sqlite3.connect(':memory:', isolation_level=None)
        if not callable(getattr(connection, 'serialize', None)):
            connection.close()
            raise ValueError('first_use_serialization_unavailable')
        queue._initialization_db = connection

    def initial_cipher(self, queue):
        from cryptography.fernet import Fernet
        if queue is not self.queue or queue._initialization_db is None or self._key is None:
            raise ValueError('first_use_queue_unverified')
        return Fernet(self._key)

    def after_queue(self, queue):
        # Still inside TaskQueue's existing migration lock. No nested flock.
        from .cli import browser_mode
        from .worker import Worker
        from .supervisor import Supervisor, create_server
        from ..otp.bridge import OtpBridge
        from .first_install import _complete_first_fence, _read_first_fence, _bundle_tag
        from .first_use_prepared import prepare, admission_tag
        self.verify(empty=False, stage='before_prepare')
        worker = Worker(queue, relay=None if browser_mode() in {'test','isolated','headless'} else OtpBridge())
        supervisor = Supervisor(queue, worker, token=self._token.decode('ascii'))
        payload = queue._initialization_db.serialize()
        payloads = {'tasks.sqlite3':payload, 'task-answers.key':self._key, 'auth.token':self._token}
        if self.prepared is None:
            self.prepared = prepare(self.state, self.context, payloads)
            self.record = {**self.record, 'authority_tag':admission_tag(self.context, self.prepared)}
        elif self.prepared.payloads != payloads:
            # The same unmodified initializer independently proves the recorded
            # database is initial state. Never restore or empty another journal.
            raise ValueError('first_use_prepared_initial_state_changed')
        self.verify(empty=False, stage='after_prepare')
        server = create_server(supervisor, port=self.port)
        self.components = (worker, supervisor, server)
        identity = self.verify(empty=False, stage='before_commit')
        queue._initialization_db.close(); queue._initialization_db = None
        # No server/worker thread has been exposed. On failure, preserve pending
        # + created private state and close only our own bound server object.
        _complete_first_fence(self.app.parent, identity, _expected_fence_tag=self.record['fence_tag'])
        if _read_first_fence(self.app.parent) != {
                'format':'jae-first-install-fence-v1', 'bundle_tag':_bundle_tag(identity), 'status':'complete'}:
            raise ValueError('first_use_completion_unverified')
        self.committed = True
        queue._first_use_startup = None  # Later ordinary stores use the retained disk key.

    def acknowledge(self, service):
        from .cli import _service_identity_valid
        if (not self.committed or self.stage != len(_STARTUP_STAGES)
                or not _service_identity_valid(service)):
            raise ValueError('first_use_service_unverified')
        _write_pipe(self.ack_fd, {'format':'jae-first-use-started-v1',
            'bundle_tag':self.record['bundle_tag'], 'service':service}, framed=True)
        os.close(self.ack_fd); self.ack_fd = None
        if self.app_fd is not None:
            os.close(self.app_fd); self.app_fd = None

    def __exit__(self, *args):
        if self.components is not None:
            self.components[2].server_close()
        if self.queue is not None and self.queue._initialization_db is not None:
            self.queue._initialization_db.close(); self.queue._initialization_db = None
        for name in ('ack_fd', 'app_fd'):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd); setattr(self, name, None)


def serve_first_use(root, port, record_fd, ack_fd):
    from .cli import serve
    # Consume the private finite record before any default-state side effect.
    try:
        record = _startup_record(_read_pipe(record_fd))
        startup = _FirstUseStartup(root, port, record, ack_fd)
    except BaseException:
        os.close(ack_fd)
        raise
    return serve(root, port, _first_use_startup=startup)


def start_first_use(root, port, record):
    """Called with native ownership, before IPC/browser/generic lifecycle."""
    from . import cli, consumer
    from .first_install import _bundle_tag
    from .process_entry import isolated_cli_command
    source, app, state = _paths(root)
    record = _startup_record(record)
    identity = consumer._bundle_transaction_identity(app)
    if identity is None or _bundle_tag(identity) != record['bundle_tag']:
        raise ValueError('first_use_source_unverified')
    descriptors = []
    child = None
    try:
        record_read, record_write = os.pipe(); descriptors.extend((record_read, record_write))
        ack_read, ack_write = os.pipe(); descriptors.extend((ack_read, ack_write))
        _write_pipe(record_write, record)
        os.close(record_write); descriptors.remove(record_write)
        child = subprocess.Popen(isolated_cli_command('--runtime', str(state), '--port', str(port),
            'first-use-serve', '--record-fd', str(record_read), '--ack-fd', str(ack_write),
            python=source.parent/'runtime/bin/python', source=source),
            cwd=source, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, pass_fds=(record_read, ack_write), start_new_session=True,
            env={key:value for key,value in os.environ.items() if key != 'APPLICATION_EXECUTOR_LOCAL_TOKEN'})
        for fd in (record_read, ack_write):
            os.close(fd); descriptors.remove(fd)
        descriptors.remove(ack_read)
        reply = _read_startup_reply(ack_read, record['bundle_tag'])
        if (type(reply) is not dict or set(reply) != {'format','bundle_tag','service'}
                or reply['format'] != 'jae-first-use-started-v1'
                or reply['bundle_tag'] != record['bundle_tag']
                or not cli._service_identity_valid(reply['service'])
                or reply['service']['pid'] != child.pid or reply['service']['port'] != port):
            raise ValueError('first_use_ack_unverified')
        expected = reply['service']
        deadline = time.monotonic() + 5
        while child.poll() is None and time.monotonic() < deadline:
            if cli._service_record(state/'service.json') != expected:
                raise ValueError('first_use_service_changed')
            try:
                observed = _owned_request(state, port, '/v1/service-identity')
                health = _owned_request(state, port, '/health')
                if observed != expected:
                    raise ValueError('first_use_service_changed')
                if health.get('ok') is True and health.get('loaded_source_sha256') == cli._consumer_release_identity()['expected']:
                    return {'service':expected, 'health':health}
            except (OSError, cli.urllib.error.URLError):
                time.sleep(.05)
        raise ValueError('first_use_service_unconfirmed')
    finally:
        for fd in descriptors:
            os.close(fd)
        # Reap only our already-exited child. Closing the private reply reader
        # cancels preparation at the next verified boundary. Never signal a
        # half-written transaction or stop a committed service on ACK loss.
        if child is not None:
            child.poll()



def _state_snapshot(state):
    if not os.path.lexists(state):
        return None
    return (_entry(state, directory=True), tuple(_entry(state/name) for name in _LOCKS))


def _fd_entry(fd, *, directory=False):
    entry = os.fstat(fd)
    if ((not stat.S_ISDIR(entry.st_mode) if directory else not stat.S_ISREG(entry.st_mode))
            or entry.st_uid != os.geteuid()
            or stat.S_IMODE(entry.st_mode) != (0o700 if directory else 0o600)
            or not directory and entry.st_nlink != 1):
        raise ValueError('first_use_authority_changed')
    return (entry.st_dev, entry.st_ino, stat.S_IMODE(entry.st_mode), entry.st_uid)


@contextmanager
def _admitted_state_guard(state, snapshot):
    """Same three flock authorities, anchored to the admitted directory FD.

    This empty-first-use specialization never creates or chmods a root/lock.
    Refusal preserves every existing entry, including a replacement.
    """
    descriptors = []
    try:
        if snapshot is None or _state_snapshot(state) != snapshot:
            raise ValueError('first_use_authority_changed')
        # The strict installer created this exact root and all lock inodes
        # before writing the pending fence. Missing entries are never rebuilt.
        root_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        expected_locks = snapshot[1]
        descriptors.append(root_fd)
        root_entry = _fd_entry(root_fd, directory=True)
        if _entry(state, directory=True) != root_entry or root_entry != snapshot[0]:
            raise ValueError('first_use_authority_changed')
        locks = []
        for name, expected in zip(_LOCKS, expected_locks):
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            descriptors.append(fd)
            entry = _fd_entry(fd)
            if entry != expected:
                raise ValueError('first_use_lock_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.append((name, fd, entry))
        authority = _tag([root_entry, *[entry for _,_,entry in locks]])
        def revalidate():
            if (_fd_entry(root_fd, directory=True) != root_entry
                    or _entry(state, directory=True) != root_entry):
                raise ValueError('first_use_authority_changed')
            for name, fd, expected in locks:
                visible = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if (_fd_entry(fd) != expected
                        or (visible.st_dev, visible.st_ino, stat.S_IMODE(visible.st_mode), visible.st_uid) != expected):
                    raise ValueError('first_use_lock_changed')
            if _authority_tag(state) != authority:
                raise ValueError('first_use_authority_changed')
            return authority
        revalidate()
        yield revalidate
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


@contextmanager
def bound_native_guard(root, record):
    """Strict handoff owns an existing bound native inode without mkdir/chmod."""
    from . import consumer
    from .first_install import _bundle_tag
    source, app, state = _paths(root)
    record = _startup_record(record)
    def validate():
        identity = consumer._bundle_transaction_identity(app)
        if (identity is None or _bundle_tag(identity) != record['bundle_tag']
                or _fence_tag(app.parent) != record['fence_tag']
                or _admission_tag(app, state, identity) != record['authority_tag']):
            raise ValueError('first_use_native_authority_changed')
    validate()
    root_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        root_entry = _fd_entry(root_fd, directory=True)
        fd = os.open('native-window.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        entry = _fd_entry(fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate()
        if _entry(state, directory=True) != root_entry or _entry(state/'native-window.lock') != entry:
            raise ValueError('first_use_native_authority_changed')
        yield fd
    finally:
        if fd is not None:
            os.close(fd)
        os.close(root_fd)

def prepare_recovery(app, state, identity, host, *, confirm=True):
    """Explicit prompt and isolated health, with no completion before handoff."""
    from . import consumer, macos_host
    from .first_install import _bundle_tag, _read_first_fence
    source = app / 'Contents/Resources/release'
    before = _fence_tag(app.parent)
    admitted_state = _state_snapshot(state)
    if admitted_state is None:
        raise ValueError('first_use_authority_missing')
    admitted_data = _admission_tag(app, state, identity)
    if (_read_first_fence(app.parent)['bundle_tag'] != _bundle_tag(identity)
            or consumer._bundle_transaction_identity(app) != identity
            or not _known_legacy_absent(source, app, state)):
        raise ValueError('first_use_admission_changed')
    if confirm:
        choice = macos_host.native_first_use_recovery_choice(
            macos_host.present_native_first_use_recovery(host))
        if choice == {'action':'cancel'}:
            return None
        if choice != {'action':'resume_first_use'}:
            raise ValueError('first_use_intent_unconfirmed')
    if (consumer._bundle_transaction_identity(app) != identity or _fence_tag(app.parent) != before
            or _state_snapshot(state) != admitted_state
            or _admission_tag(app, state, identity) != admitted_data):
        raise ValueError('first_use_admission_changed')
    app_fd = consumer._acquire_app_transaction_lock(app.parent)
    try:
        with _admitted_state_guard(state, admitted_state) as revalidate_authority:
            admitted_tag = revalidate_authority()
            if (consumer._bundle_transaction_identity(app) != identity
                    or _fence_tag(app.parent) != before
                    or _admission_tag(app, state, identity) != admitted_data
                    or not _known_legacy_absent(source, app, state)):
                raise ValueError('first_use_admission_changed')
            if confirm and not consumer._candidate_starts(source.parent/'runtime/bin/python', source):
                raise ValueError('first_use_health_unconfirmed')
            if (consumer._bundle_transaction_identity(app) != identity
                    or _fence_tag(app.parent) != before
                    or _admission_tag(app, state, identity) != admitted_data
                    or not _known_legacy_absent(source, app, state)):
                raise ValueError('first_use_admission_changed')
            if revalidate_authority() != admitted_tag:
                raise ValueError('first_use_authority_changed')
            record = capture_startup_record(app, state, identity)
            if record['authority_tag'] != admitted_data or revalidate_authority() != admitted_tag:
                raise ValueError('first_use_authority_changed')
            return record
    finally:
        os.close(app_fd)


def verify_started_service(root, port, admitted):
    """Read-only exact instance verification; never start, restart or adopt."""
    from . import cli
    expected = admitted['service']
    if (not cli._service_identity_valid(expected) or expected['port'] != port
            or cli._service_record(Path(root)/'service.json') != expected
            or _owned_request(root, port, '/v1/service-identity') != expected):
        raise ValueError('first_use_service_changed')
    health = _owned_request(root, port, '/health')
    if (health.get('ok') is not True
            or health.get('loaded_source_sha256') != cli._consumer_release_identity()['expected']
            or cli._service_record(Path(root)/'service.json') != expected):
        raise ValueError('first_use_service_changed')
    return health


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise ValueError('local_service_redirect_refused')


def _owned_request(root, port, path, data=None):
    """Authenticate with an existing owned token, never create/replace one."""
    from .runtime_paths import _private_regular_fd, _same_entry
    if type(port) is not int or not 0 < port < 65536 or path not in {
            '/health', '/v1/service-identity', '/v1/ui-ticket'}:
        raise ValueError('first_use_request_invalid')
    token_path = Path(root)/'auth.token'
    fd = _private_regular_fd(token_path, os.O_RDONLY, require_private=True)
    with os.fdopen(fd, 'r', encoding='utf-8') as handle:
        entry = os.fstat(handle.fileno())
        raw = handle.read(4097)
        _same_entry(token_path, entry)
    if len(raw) > 4096 or not 32 <= len(raw.strip()) <= 4096:
        raise ValueError('first_use_token_unverified')
    request = urllib.request.Request('http://127.0.0.1:'+str(port)+path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Authorization':'Bearer '+raw.strip(), 'Content-Type':'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=.5) as response:
        body = response.read(65537)
    if len(body)>65536:
        raise ValueError('first_use_response_invalid')
    value = json.loads(body, object_pairs_hook=_unique)
    if type(value) is not dict:
        raise ValueError('first_use_response_invalid')
    return value


def open_started_ui(root, port, admitted, presenter):
    from .consumer_presentation import ConsumerSurface, present_surface
    verify_started_service(root, port, admitted)
    response = _owned_request(root, port, '/v1/ui-ticket', {'expected_service':admitted['service']})
    if (set(response) != {'ticket','service'} or response['service'] != admitted['service']):
        raise ValueError('first_use_ticket_unverified')
    ticket = response['ticket']
    verify_started_service(root, port, admitted)
    opened = present_surface(ConsumerSurface.dashboard(port, ticket), presenter=presenter)
    return {'ok':opened, 'opened':opened}
