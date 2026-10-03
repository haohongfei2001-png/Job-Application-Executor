"""Finite downloaded-app update/open path for the unsigned Mac installer.

The running candidate, managed target and default state determine every path.
There is no remote selector, migration, forced window closure or task replay.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import stat
import time
import urllib.request


def _fence_snapshot(apps):
    from .first_install import _read_first_fence, _fence_path
    from .first_use_recovery import _entry
    record = _read_first_fence(apps)
    return None if record is None else (_entry(_fence_path(apps)), record)


def _service_snapshot(state):
    from .cli import _service_record
    try:
        return _service_record(state / 'service.json')
    except FileNotFoundError:
        return None


def _app_snapshot(apps):
    from .consumer import APP_NAME
    from .first_use_recovery import _entry
    if any(path.is_symlink() for path in (apps, *apps.parents)):
        raise ValueError('installer_target_changed')
    entry = apps.stat(follow_symlinks=False)
    if (not stat.S_ISDIR(entry.st_mode) or entry.st_uid != os.geteuid()
            or entry.st_mode & 0o022):
        raise ValueError('installer_target_changed')
    return ((entry.st_dev, entry.st_ino, stat.S_IMODE(entry.st_mode), entry.st_uid),
            _entry(apps / ('.' + APP_NAME + '.app.transaction.lock')))


def _app_lock(apps, expected):
    from .consumer import APP_NAME
    from .first_use_recovery import _fd_entry
    if _app_snapshot(apps) != expected:
        raise ValueError('installer_target_changed')
    root = os.open(apps, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        entry = os.fstat(root)
        if (entry.st_dev, entry.st_ino, stat.S_IMODE(entry.st_mode), entry.st_uid) != expected[0]:
            raise ValueError('installer_target_changed')
        fd = os.open('.' + APP_NAME + '.app.transaction.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=root)
        if _fd_entry(fd) != expected[1]:
            raise ValueError('installer_target_changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if _app_snapshot(apps) != expected:
            raise ValueError('installer_target_changed')
        result, fd = fd, None
        return result
    finally:
        os.close(root)
        if fd is not None:
            os.close(fd)


def _capture(target, state):
    from . import consumer
    from .first_install import _bundle_tag
    from .first_use_recovery import _state_snapshot
    from .state_compatibility import _owned_journal_entries
    identity = consumer._bundle_transaction_identity(target)
    authority = _state_snapshot(state)
    if identity is None or authority is None or not _owned_journal_entries(state):
        raise ValueError('installer_target_unverified')
    fence = _fence_snapshot(target.parent)
    if fence is not None and fence[1]['status'] == 'pending':
        if (fence[1]['bundle_tag'] != _bundle_tag(identity)
                or consumer._owned_bundle_text(target / 'Contents/MacOS/AIApplicationManager')
                   == consumer._native_packaged_launcher_v1()):
            raise ValueError('installer_pending_target_unverified')
    return {'target': identity, 'authority': authority, 'apps': _app_snapshot(target.parent),
            'fence': fence, 'service': _service_snapshot(state)}


class _UpdateAuthority(AbstractContextManager):
    """Hold existing exact native/worker/migration inodes without creation."""
    def __init__(self, state, expected, app_fd):
        self.state, self.expected = Path(state), expected
        self.app_fd = app_fd
        self.apps = Path.home().absolute() / 'Applications'
        self.root_fd = None
        self.locks = []

    def _lock(self, index):
        from .first_use_recovery import _LOCKS, _fd_entry
        name = _LOCKS[index]
        fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.root_fd)
        try:
            if _fd_entry(fd) != self.expected['authority'][1][index]:
                raise ValueError('installer_authority_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.locks.append((index, fd))
        except BaseException:
            os.close(fd)
            raise

    def __enter__(self):
        from .first_use_recovery import _state_snapshot, _fd_entry
        try:
            if _state_snapshot(self.state) != self.expected['authority']:
                raise ValueError('installer_authority_changed')
            self.root_fd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if _fd_entry(self.root_fd, directory=True) != self.expected['authority'][0]:
                raise ValueError('installer_authority_changed')
            self._lock(0)  # Never create/chmod or close a busy native window.
            self.verify()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def acquire_task_locks(self):
        from .state_compatibility import _refuse_live_service
        if len(self.locks) != 1:
            raise ValueError('installer_authority_invalid')
        self._lock(1)
        self._lock(2)
        self.verify()
        _refuse_live_service(self.state)
        if os.path.lexists(self.state / 'preparation-context.active'):
            raise BlockingIOError('preparation_context_unclosed')

    def verify(self):
        from .first_use_recovery import _state_snapshot, _fd_entry
        from .state_compatibility import _owned_journal_entries
        if (self.root_fd is None
                or _fd_entry(self.app_fd) != self.expected['apps'][1]
                or _app_snapshot(self.apps) != self.expected['apps']
                or _fence_snapshot(self.apps) != self.expected['fence']
                or _fd_entry(self.root_fd, directory=True) != self.expected['authority'][0]
                or _state_snapshot(self.state) != self.expected['authority']
                or not _owned_journal_entries(self.state)):
            raise ValueError('installer_authority_changed')
        for index, fd in self.locks:
            if _fd_entry(fd) != self.expected['authority'][1][index]:
                raise ValueError('installer_authority_changed')

    def __exit__(self, *_):
        for _, fd in reversed(self.locks):
            os.close(fd)
        self.locks = []
        if self.root_fd is not None:
            os.close(self.root_fd)
            self.root_fd = None


def _stop_request(state, port, expected):
    """Only the already-admitted exact service, with an existing private token."""
    from .runtime_paths import _private_regular_fd, _same_entry
    from .first_use_recovery import _unique, _NoRedirect
    token_path = state / 'auth.token'
    fd = _private_regular_fd(token_path, os.O_RDONLY, require_private=True)
    with os.fdopen(fd, encoding='utf-8') as handle:
        entry = os.fstat(handle.fileno())
        token = handle.read(4097)
        _same_entry(token_path, entry)
    if len(token) > 4096 or not 32 <= len(token.strip()) <= 4096:
        raise ValueError('installer_service_unverified')
    request = urllib.request.Request(f'http://127.0.0.1:{port}/v1/service-stop',
        data=json.dumps(expected).encode(),
        headers={'Authorization': 'Bearer ' + token.strip(), 'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=1) as response:
        body = response.read(65537)
    if len(body) > 65536:
        raise ValueError('installer_stop_unconfirmed')
    return json.loads(body, object_pairs_hook=_unique)


def _retire(state, port, snapshot, verify):
    from .first_use_recovery import _owned_request
    from .release import source_manifest
    expected = snapshot['service']
    verify()
    if _service_snapshot(state) != expected:
        raise ValueError('installer_service_changed')
    if expected is None:
        return
    if (expected['port'] != port
            or _owned_request(state, port, '/v1/service-identity') != expected):
        raise ValueError('installer_service_changed')
    target = Path.home().absolute() / 'Applications' / 'AI 投递经理.app'
    health = _owned_request(state, port, '/health')
    if (health.get('ok') is not True or health.get('loaded_source_sha256') !=
            source_manifest(target / 'Contents/Resources/release')['source_sha256']):
        raise ValueError('installer_service_changed')
    verify()
    if _service_snapshot(state) != expected:
        raise ValueError('installer_service_changed')
    result = _stop_request(state, port, expected)
    if type(result) is not dict or result.get('ok') is not True:
        raise BlockingIOError('installer_service_busy')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        verify()
        current = _service_snapshot(state)
        if current is None:
            return
        if current != expected:
            raise ValueError('installer_service_changed')
        time.sleep(.1)
    raise ValueError('installer_stop_unconfirmed')


def _open_existing(target, expected, port):
    from . import consumer
    from .process_entry import CLI_ENTRY_SCRIPT
    from .runtime_paths import default_runtime
    from .first_install import _bundle_tag
    if consumer._bundle_transaction_identity(target) != expected:
        raise ValueError('installer_target_changed')
    launcher = consumer._owned_bundle_text(target / 'Contents/MacOS/AIApplicationManager')
    if not consumer._is_native_packaged_launcher(launcher):
        raise ValueError('installer_target_changed')
    source = target / 'Contents/Resources/release'
    python = target / 'Contents/Resources/runtime/bin/python'
    command = 'native-launch' if launcher == consumer._native_packaged_launcher_v1() else 'native-entry'
    fence = _fence_snapshot(target.parent)
    if fence is not None and fence[1]['status'] == 'pending' and (
            command != 'native-entry' or fence[1]['bundle_tag'] != _bundle_tag(expected)):
        raise ValueError('installer_pending_target_unverified')
    # Use the exact app-owned interpreter/source. Global CLI options precede
    # the retained command, preserving the admitted port and pending recovery.
    subprocess.Popen([str(python), '-I', '-B', '-c', CLI_ENTRY_SCRIPT, str(source),
        '--runtime', str(default_runtime(source)), '--port', str(port), command], cwd=source,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    return {'ok': True, 'installed': True, 'reopen_requested': True, 'native_ready_verified': False}


def run_downloaded_installer(candidate, target, state, port, candidate_identity):
    from . import consumer, macos_host
    from .runtime_paths import default_runtime
    candidate, target, state = Path(candidate), Path(target), Path(state)
    boundary = {'final_click_actor': 'user', 'submit_capability': False}
    if (target != Path.home().absolute() / 'Applications' / (consumer.APP_NAME + '.app')
            or candidate == target or state != Path(default_runtime(target / 'Contents/Resources/release')).absolute()
            or type(port) is not int or not 0 < port < 65536):
        return {'ok': False, 'reason': 'installer_target_unverified', **boundary}
    host = candidate / 'Contents/Resources/native-host'
    attempted = False
    try:
        snapshot = _capture(target, state)
        same = tuple((p, h) for p, _, h in snapshot['target'][2]) == tuple((p, h) for p, _, h in candidate_identity[2])
        pending = snapshot['fence'] is not None and snapshot['fence'][1]['status'] == 'pending'
        mode = 'recovery' if pending else 'open' if same else 'update'
        choice = macos_host.present_native_installer(host, mode)
        if choice == {'action': 'cancel'}:
            return {'ok': True, 'cancelled': True, 'installed': True, **boundary}
        choice = macos_host.native_installer_choice(choice, mode)
        if choice is None:
            raise ValueError('installer_intent_unconfirmed')

        def unchanged():
            if (consumer._bundle_transaction_identity(candidate) != candidate_identity
                    or consumer._bundle_transaction_identity(target) != snapshot['target']
                    or _fence_snapshot(target.parent) != snapshot['fence']
                    or _app_snapshot(target.parent) != snapshot['apps']):
                raise ValueError('installer_target_changed')
            from .first_use_recovery import _state_snapshot
            if _state_snapshot(state) != snapshot['authority']:
                raise ValueError('installer_authority_changed')

        unchanged()
        if choice['action'] == 'open':
            return {**_open_existing(target, snapshot['target'], port), **boundary}
        if pending or same:
            raise ValueError('installer_intent_unconfirmed')
        release = candidate / 'Contents/Resources/release'
        runtime = candidate / 'Contents/Resources/runtime'
        if not consumer.verify_standalone_runtime(runtime, release):
            raise ValueError('installer_bundle_changed')
        unchanged()
        app_fd = _app_lock(target.parent, snapshot['apps'])
        try:
            unchanged()
            with _UpdateAuthority(state, snapshot, app_fd) as guard:
                _retire(state, port, snapshot, lambda: (unchanged(), guard.verify()))
                guard.acquire_task_locks()
                unchanged()
                attempted = True
                result = consumer._install_macos_app_unlocked(release, destination=target.parent,
                    task_state_root=state, standalone_runtime=runtime, native_presentation=True,
                    native_host=host, _legacy_origin=Path(__file__).absolute().parents[2],
                    _legacy_additional_origins=(Path.home().absolute() / 'Job-Application-Executor',),
                    _approved_bundle=(candidate, candidate_identity),
                    _bundle_payload=(candidate, candidate_identity), _installer_guard=guard,
                    _expected_current_identity=snapshot['target'])
        finally:
            os.close(app_fd)
        if result.get('ok') is not True or result.get('replaced') is not True:
            return {**result, **boundary}
        active = consumer._bundle_transaction_identity(target)
        if (active is None or not consumer._approved_first_install_matches((candidate, candidate_identity), target)
                or _fence_snapshot(target.parent) != snapshot['fence']):
            raise ValueError('installer_activation_unconfirmed')
        return {**result, **_open_existing(target, active, port), 'updated': True, **boundary}
    except BlockingIOError:
        if consumer._bundle_transaction_identity(candidate) == candidate_identity:
            macos_host.present_native_first_install(host, result='app-in-use')
        return {'ok': False, 'reason': 'installer_busy', 'installed': None if attempted else True, **boundary}
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        if consumer._bundle_transaction_identity(candidate) == candidate_identity:
            macos_host.present_native_first_install(host, result='not-confirmed')
        return {'ok': False, 'reason': 'installer_unconfirmed', 'installed': None if attempted else True, **boundary}
