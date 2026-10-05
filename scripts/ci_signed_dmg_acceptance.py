"""Independent hosted-Mac signed-DMG engineering acceptance owner.

This is a disposable, synthetic publisher experiment, never a release builder.
Only the isolated source snapshot gets the fixed ad-hoc identity adapter, before
any manifest, build or seal. Production keeps rejecting the same ad-hoc bytes.
All three complete versions share one unsigned runtime/native build. No test
app, key, profile, runtime or DMG is an uploadable output of this owner.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

WALL_SECONDS = 30 * 60
FAILURE_OBSERVATION_SECONDS = 180
FORMAT = 'jae-signed-dmg-acceptance-v1'
BUNDLE_ID = 'com.local.job-application-executor.ai-application-manager'
APP_NAME = 'AI 投递经理.app'
POLICY = {'format': 'jae-required-publisher-policy-v1', 'team_id': 'SYNTHETIC1',
          'bundle_id': BUNDLE_ID, 'certificate_kind': 'Developer ID Application'}
REQUIRED = frozenset({
    'three_complete_signed_versions', 'readonly_dmg_current_identity',
    'production_rejects_adhoc', 'missing_policy', 'wrong_policy',
    'missing_current', 'tampered_payload', 'extra_file', 'extra_xattr',
    'real_cocoa_cancel', 'fixture_explicit_first_install', 'native_reopen_health',
    'checkout_independent', 'unsigned_candidate_preserves_signed_service',
    'real_service_retired_update', 'paused_tasks_preserved',
    'encrypted_typed_answers_preserved', 'answer_key_profile_preserved',
    'retained_old_signed_version', 'damaged_retained_refused', 'occupied_slot_refused',
    'explicit_signed_rollback_health', 'final_path_failure_after_staging_health',
    'real_failed_activation_restoration', 'failed_signed_version_retained',
})


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode()


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise AssertionError('test adapter source contract changed: ' + path.name)
    path.write_text(text.replace(old, new))


def adapt_test_source(source, root):
    """Write a fixed, directory-confined adapter into an UNMANIFESTED copy."""
    if (source / 'release-source-manifest.json').exists():
        raise ValueError('test_adapter_requires_unmanifested_source')
    policy_file = source / 'executor/autonomy/publisher_policy.py'
    replace_once(policy_file, 'BUILT_IN_PUBLISHER_POLICY = None',
                 'BUILT_IN_PUBLISHER_POLICY = ' + repr(POLICY))
    original = policy_file.read_text()
    begin = original.index("    requirement = ('anchor apple generic and '")
    end = original.index('    try:\n', begin)
    replacement = (
        '    # TEST COPY ONLY: fixed identity; Apple still verifies every architecture.\n'
        '    from pathlib import Path\n'
        f'    if policy != {POLICY!r} or not Path(app).resolve(strict=True).is_relative_to(Path({str(root)!r})):\n'
        "        raise ValueError('signed_payload_publisher_refused')\n"
        '    requirement = \'identifier = "\' + BUNDLE_ID + \'"\'\n')
    policy_file.write_text(original[:begin] + replacement + original[end:])
    # Build-time static verification uses exactly the same bounded synthetic
    # identity. Neither this script nor these adapted scripts ship normally.
    helper = source / 'scripts/prepare_macos_signing.py'
    original = helper.read_text()
    begin = original.index('def _requirement(policy, identifier):')
    end = original.index('\n\ndef _no_xattrs', begin)
    replacement = (
        'def _requirement(policy, identifier):\n'
        f'    if policy != {POLICY!r}:\n'
        "        raise ValueError('test_publisher_policy_refused')\n"
        f'    if identifier != {BUNDLE_ID!r} and not re.fullmatch({re.escape(BUNDLE_ID) + r"\.component\.[0-9a-f]{64}"!r}, identifier):\n'
        "        raise ValueError('test_publisher_identifier_refused')\n"
        '    return \'identifier = "\' + identifier + \'"\'\n')
    helper.write_text(original[:begin] + replacement + original[end:])


def copy_unmanifested_source(repo, target):
    target.mkdir()
    shutil.copytree(repo / 'executor', target / 'executor',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(repo / 'requirements.txt', target / 'requirements.txt')
    shutil.copytree(repo / 'scripts', target / 'scripts',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def inject_final_path_failure(source, target, trace):
    """Fault is part of version three BEFORE its manifest and signing."""
    signature = 'def serve(root, port, *, _first_use_startup=None):\n'
    body = (
        f'    _oracle_final = Path(__file__).absolute().parents[2] == Path({str(target / "Contents/Resources/release")!r})\n'
        f'    with open({str(trace)!r}, "a", encoding="utf-8") as _oracle_trace:\n'
        '        _oracle_trace.write(("final" if _oracle_final else "staging") + "\\n")\n'
        '    if _oracle_final:\n'
        '        raise RuntimeError("synthetic_signed_final_path_serve_failure")\n')
    replace_once(source / 'executor/autonomy/cli.py', signature, signature + body)


def public_wheels(runtime):
    """Read public app-owned wheel metadata; use existing normalization helpers."""
    from importlib.metadata import distributions
    from packaging.utils import canonicalize_name
    from packaging.version import Version
    sites = sorted(runtime.glob('lib/python*/site-packages'))
    assert sites and all(site.is_dir() and not site.is_symlink() for site in sites)
    wheels = []
    for item in distributions(path=[str(site) for site in sites]):
        assert Path(item.locate_file('')).resolve().is_relative_to(runtime)
        assert item.read_text('WHEEL') is not None
        name = canonicalize_name(item.metadata['Name'] or '')
        assert name
        wheels.append([name, str(Version(item.version))])
    wheels.sort()
    assert wheels and len(wheels) == len({name for name, version in wheels})
    return wheels


def coverage_record(unsigned_paths, current_paths, unsigned_wheels, observed):
    """Prove exact full-runtime preservation; only the two producer files grow."""
    added = ['release-runtime-manifest.json', 'signing-input-bridge.json']
    assert len(unsigned_paths) == len(set(unsigned_paths))
    assert len(current_paths) == len(set(current_paths))
    assert not set(added) & set(unsigned_paths)
    assert set(current_paths) == set(unsigned_paths) | set(added)
    assert observed['runtime_files'] == len(current_paths)
    assert observed['wheels'] == unsigned_wheels
    assert observed['wheel_count'] == len(unsigned_wheels)
    assert len([name for name in unsigned_paths if name.endswith('.dist-info/WHEEL')]) == len(unsigned_wheels)
    return {'unsigned_runtime_files':len(unsigned_paths),
            'current_runtime_files':len(current_paths), 'added_runtime_files':added,
            'wheel_count':len(unsigned_wheels), 'wheels':unsigned_wheels}


# These fixtures run INSIDE the app's own isolated interpreter, importing only
# that same signed source. They alter explicit user choices / smoke UI lifetime,
# never signature, manifest, health, service retirement or transaction outcomes.
ENTRY_DIAGNOSTICS = r'''
import functools, itertools, json, os, sys, time
_trace_started = time.monotonic()
_trace_calls = itertools.count(1)
def _trace_event(stage, call, event, started, error_type=None):
    print('JAE_ORACLE_STAGE ' + json.dumps({
        'stage':stage, 'call':call, 'event':event, 'pid':os.getpid(),
        **({'error_type':error_type} if error_type is not None else {}),
        'elapsed_seconds':round(time.monotonic()-_trace_started, 3),
        'duration_seconds':round(time.monotonic()-started, 3),
    }, sort_keys=True), file=sys.stderr, flush=True)
def _trace_function(module, name):
    original = getattr(module, name)
    stage = module.__name__.rsplit('.', 1)[-1] + '.' + name
    @functools.wraps(original)
    def observed(*args, **kwargs):
        call, started = next(_trace_calls), time.monotonic()
        _trace_event(stage, call, 'begin', started)
        try:
            result = original(*args, **kwargs)
        except BaseException as error:
            _trace_event(stage, call, 'raise', started, type(error).__name__)
            raise
        _trace_event(stage, call, 'return', started)
        return result
    setattr(module, name, observed)
def _trace_native_timeout(child, error):
    def tail(value, limit):
        if isinstance(value, bytes): value = value.decode('utf-8', errors='replace')
        return (value or '')[-limit:]
    print('JAE_ORACLE_NATIVE_TIMEOUT ' + json.dumps({
        'returncode':child.poll(), 'timeout_seconds':error.timeout,
        'stdout_tail':tail(error.stdout, 4096), 'stderr_tail':tail(error.stderr, 16384),
    }, sort_keys=True), file=sys.stderr, flush=True)
'''

SERVICE_DIAGNOSTICS = ENTRY_DIAGNOSTICS + r'''
from pathlib import Path
_service_source = Path(sys.argv[1])
sys.path.insert(0, str(_service_source))
from executor.autonomy import consumer, first_use_recovery
assert all(Path(module.__file__).is_relative_to(_service_source)
           for module in (consumer, first_use_recovery))
for module, names in (
    (consumer, ('_bundle_transaction_identity',)),
    (first_use_recovery, ('serve_first_use', '_read_pipe', '_write_pipe')),
    (first_use_recovery._FirstUseStartup, ('__enter__', 'verify', 'bound_lock_fd',
        'before_queue', 'after_queue', 'acknowledge', '__exit__')),
):
    for name in names: _trace_function(module, name)
# The original service CLI program follows with the same source and arguments.
'''

REOPEN_DIAGNOSTICS = ENTRY_DIAGNOSTICS + '\n_service_diagnostics = ' + repr(SERVICE_DIAGNOSTICS) + r'''
import subprocess
from pathlib import Path
_reopen_source = Path(sys.argv[1])
sys.path.insert(0, str(_reopen_source))
from executor.autonomy import consumer, first_install, first_use_recovery
for module, names in (
    (consumer, ('_bundle_transaction_identity',)),
    (first_install, ('launch_native_entry',)),
    (first_use_recovery, ('prepare_recovery', 'start_first_use', '_read_pipe')),
):
    assert Path(module.__file__).is_relative_to(_reopen_source)
    for name in names: _trace_function(module, name)
_service_children = []
_service_popen = subprocess.Popen
def _observe_service(args, *a, **kw):
    if 'first-use-serve' in args:
        assert args[:5] == [str(_reopen_source.parent/'runtime/bin/python'), '-I', '-B', '-c', args[4]]
        assert args[5] == str(_reopen_source)
        observed_args = list(args)
        observed_args[4] = _service_diagnostics + '\n' + args[4]
        # A daemon can outlive the finite native CLI. Observe stderr in a
        # private fixture file, never a PIPE that could keep communicate open.
        fd = os.open(_oracle_service_log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab', buffering=0) as stream:
            child = _service_popen(observed_args, *a, **{**kw, 'stderr':stream})
        _service_children.append(child)
        return child
    return _service_popen(args, *a, **kw)
subprocess.Popen = _observe_service
_start_first_use = first_use_recovery.start_first_use
@functools.wraps(_start_first_use)
def _observe_start(*a, **kw):
    try:
        return _start_first_use(*a, **kw)
    finally:
        for child in _service_children:
            print('JAE_ORACLE_SERVICE_STATUS ' + json.dumps({
                'pid':child.pid, 'returncode':child.poll(),
            }, sort_keys=True), file=sys.stderr, flush=True)
first_use_recovery.start_first_use = _observe_start
# The original -c program follows unchanged and owns argv parsing/execution.
'''

ENTRY_FIXTURE = ENTRY_DIAGNOSTICS + '\n_reopen_diagnostics = ' + repr(REOPEN_DIAGNOSTICS) + r'''
import os, subprocess
from pathlib import Path
source = Path(sys.argv[1]); sys.path.insert(0, str(source))
from executor.autonomy import first_install, macos_host, consumer, bundle_copy
from executor.autonomy.runtime_paths import default_runtime
for module, names in (
    (first_install, ('launch_native_entry',)),
    (consumer, ('_bundle_transaction_identity', 'install_macos_bundle',
                '_install_macos_app_unlocked', '_candidate_starts',
                '_prepare_task_state_release', '_activate_first_install_exclusive')),
    (bundle_copy, ('copy_bundle_payload',)),
):
    for name in names: _trace_function(module, name)
mode, port = sys.argv[2], int(sys.argv[3])
actual = macos_host.present_native_first_install
if mode == 'cancel':
    macos_host.present_native_first_install = lambda host, **kw: actual(host, smoke=True)
else:
    macos_host.present_native_first_install = lambda host, **kw: {'action':'install','continuity':'first_use'}
    macos_host.present_native_installer = lambda host, kind, **kw: {'action':'update'}
children = []
real_popen = subprocess.Popen
def smoke_reopen(args, *a, **kw):
    # This is still the actual signed -I child. Only the existing finite Cocoa
    # smoke flag is added; do not emulate Popen or discard any child execution.
    if 'native-entry' in args or 'native-launch' in args:
        assert args[:5] == [str(Path(args[5]).parent/'runtime/bin/python'), '-I', '-B', '-c', args[4]]
        args = [*args, '--native-smoke']
        # Prepend transparent observations, then execute the exact original
        # entry program against the same sealed app-owned source and argv.
        assert Path.home().name == 'home'
        args[4] = ('_oracle_service_log_path = ' + repr(str(Path.home().parent/'first-use-service-diagnostic.log'))
                   + '\n' + _reopen_diagnostics + '\n' + args[4])
        kw.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child = real_popen(args, *a, **kw); children.append(child); return child
    return real_popen(args, *a, **kw)
subprocess.Popen = smoke_reopen
result = first_install.launch_native_entry(default_runtime(source), port)
for child in children:
    call, started = next(_trace_calls), time.monotonic()
    _trace_event('fixture.native_reopen_wait', call, 'begin', started)
    try:
        # The real finite preparation protocol can span multiple verified
        # stages; observe it within the enclosing complete-transaction budget.
        out, err = child.communicate(timeout=600)
    except subprocess.TimeoutExpired as error:
        _trace_native_timeout(child, error)
        raise
    _trace_event('fixture.native_reopen_wait', call, 'return', started)
    # Keep native-child observations even when its original CLI reports a
    # refusal. Diagnostics do not supply or rewrite its native/health result.
    if err: print(err[-16384:], file=sys.stderr, flush=True)
    assert child.returncode == 0, (out[-2000:], err[-2000:])
    report = json.loads(out)
    assert report.get('ok') is True and report.get('native_window') is True and report.get('native_page') is True, report
result['actual_native_reopens'] = len(children)
print(json.dumps(result, sort_keys=True))
'''

READERS = r'''
import json, sys
from pathlib import Path
source=Path(sys.argv[1]);sys.path.insert(0,str(source))
from executor.autonomy import consumer, release, macos_host, standalone_runtime
from executor.autonomy.signed_payload import read_current_payload
app=Path(sys.argv[2]);resources=app/'Contents/Resources'
saved=read_current_payload(app)
assert consumer._trusted_bundle(app)
assert release.read_runtime_candidate(resources/'runtime',resources/'release') == saved['runtime']
assert macos_host.verify_native_host(resources/'native-host')
assert standalone_runtime.verify_standalone_runtime(resources/'runtime',resources/'release')
assert release.installed_dependencies_match(source)
assert all(Path(mod.__file__).is_relative_to(source) for mod in (consumer,release,macos_host,standalone_runtime))
# Authentication and all existing dependency checks above finish before
# reading public installed-wheel names. Never consult global/user site metadata.
from importlib.metadata import distributions
from packaging.utils import canonicalize_name
from packaging.version import Version
runtime=resources/'runtime';sites=sorted(runtime.glob('lib/python*/site-packages'))
assert sites and all(site.is_dir() and not site.is_symlink() for site in sites)
wheels=[]
for item in distributions(path=[str(site) for site in sites]):
    assert Path(item.locate_file('')).resolve().is_relative_to(runtime)
    assert item.read_text('WHEEL') is not None
    name=canonicalize_name(item.metadata['Name'] or '');assert name
    wheels.append([name,str(Version(item.version))])
wheels.sort();assert wheels and len(wheels)==len({name for name,version in wheels})
print(json.dumps({'ok':True,'runtime_files':len(saved['runtime']['files']),
                  'wheel_count':len(wheels),'wheels':wheels}))
'''

PRODUCTION_REFUSAL = r'''
import json, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import publisher_policy, consumer
app=Path(sys.argv[2]);policy=json.loads(sys.argv[3])
for supplied, reason in ((None,'signed_payload_publisher_policy_required'),
                         (policy,'signed_payload_publisher_refused')):
    try: publisher_policy.verify_publisher(app,supplied)
    except ValueError as error: assert str(error)==reason, str(error)
    else: raise AssertionError('unpatched production admitted ad-hoc payload')
assert not consumer._trusted_bundle(app)
assert not consumer._trusted_bundle(app,required_publisher_policy=policy)
print(json.dumps({'ok':True}))
'''

SEED_STATE = r'''
import json, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);state=Path(sys.argv[2])
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.worker import Worker
from executor.facts.answers import TaskAnswerStore
queue=TaskQueue(state);Worker(queue,settings={});store=TaskAnswerStore(queue)
profile=state.parent/'synthetic-profile.json'
profile.write_text(json.dumps({'fields':{'identity.full_name':{'value':'SYNTHETIC PERSON'}},'research':['complete synthetic profile']*100}));profile.chmod(0o600)
values={'availability.year':2028,'availability.ready':True,'availability.rate':1.25,'identity.full_name':'SYNTHETIC PERSON'}
tasks=[]
for number in range(3):
    task=queue.enqueue(TaskSpec(company='Synthetic signed delivery '+str(number),role='Engineer',target_url='https://example.invalid/signed/'+str(number),profile_ref=str(profile)))
    task=queue.pause(task['task_id']);tasks.append(task['task_id'])
    with queue.tx() as db:
        for key,value in values.items():
            ciphertext=store.cipher.encrypt(json.dumps(value).encode())
            db.execute('INSERT INTO task_answer_events(task_id,field_key,ciphertext,answer_version,source,task_revision,created) VALUES(?,?,?,?,?,?,?)',(task['task_id'],key,ciphertext,1,'user_explicit_task',task['revision'],queue.clock()))
    assert store.load(task['task_id'])==values
print(json.dumps({'ok':True,'tasks':tasks}))
'''

VERIFY_STATE = r'''
import json, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);state=Path(sys.argv[2])
from executor.autonomy.queue import TaskQueue
from executor.facts.answers import TaskAnswerStore
queue=TaskQueue(state);store=TaskAnswerStore(queue)
values={'availability.year':2028,'availability.ready':True,'availability.rate':1.25,'identity.full_name':'SYNTHETIC PERSON'}
for task in json.loads(sys.argv[3]):
    answer=store.load(task);assert answer==values
    assert {key:type(value) for key,value in answer.items()} == {key:type(value) for key,value in values.items()}
    assert queue.get(task)['run_state']=='PAUSED'
print(json.dumps({'ok':True}))
'''

CLI_SCRIPT = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('executor.autonomy.cli',run_name='__main__',alter_sys=True)"
ROLLBACK_FIXTURE = ENTRY_DIAGNOSTICS + r'''
import runpy
sys.path.insert(0, sys.argv.pop(1))
from executor.autonomy import consumer
for name in ('rollback_macos_app', '_rollback_macos_app_unlocked',
             '_bundle_transaction_identity', '_candidate_starts', '_isolated_bundle_startup'):
    _trace_function(consumer, name)
runpy.run_module('executor.autonomy.cli', run_name='__main__', alter_sys=True)
'''


class Oracle:
    def __init__(self, root):
        self.root = root
        self.started = time.monotonic()
        self.checked = set()
        self.phases = {}
        self.home = root / 'home'; self.home.mkdir(mode=0o700)
        self.state = self.home / 'Library/Application Support/AI投递经理/autonomy'
        self.target = self.home / 'Applications' / APP_NAME
        self.env = {**os.environ, 'HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1',
                    'APPLICATION_EXECUTOR_BROWSER_MODE': 'isolated', 'BROWSER': '/usr/bin/false'}
        for key in ('PYTHONHOME', 'PYTHONPATH'):
            self.env.pop(key, None)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]

    def phase(self, value):
        self.phases[value] = round(time.monotonic() - self.started, 2)
        print(json.dumps({'phase': value, 'elapsed_seconds': self.phases[value],
                          'certification': 'NOT_CERTIFIED'}), flush=True)

    def child_diagnostics(self, outcome, stdout, stderr):
        # This owner has only disposable synthetic children. Preserve bounded
        # diagnostic tails, never command arguments, keys or fixture files.
        def decoded(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
        stdout, stderr = decoded(stdout), decoded(stderr)
        if outcome != 'completed' or 'JAE_ORACLE_STAGE ' in stderr:
            print(json.dumps({'oracle_child_diagnostics':outcome,
                'stdout_tail':stdout[-4096:] if outcome != 'completed' else '',
                'stderr_tail':stderr[-32768:],
                'certification':'NOT_CERTIFIED'}, sort_keys=True),
                file=sys.stderr, flush=True)

    def run(self, argv, *, timeout=180, expected_returncode=0):
        if type(expected_returncode) is not int or expected_returncode not in (0, 1):
            raise ValueError("oracle only admits exact success or explicit refusal")
        remaining = WALL_SECONDS - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TimeoutError('signed DMG oracle 30-minute wall budget exhausted')
        try:
            result = subprocess.run(list(map(str, argv)), cwd=self.root, env=self.env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=min(timeout, remaining), check=False)
        except subprocess.TimeoutExpired as error:
            self.child_diagnostics('timeout', error.stdout, error.stderr)
            raise
        self.child_diagnostics('completed' if result.returncode == expected_returncode
                               else 'unexpected_exit', result.stdout, result.stderr)
        if result.returncode != expected_returncode:
            raise AssertionError('oracle child failed: ' + result.stdout[-2500:] + result.stderr[-2500:])
        return result.stdout

    def app_run(self, app, code, *args, timeout=180, expected_returncode=0):
        source = app / 'Contents/Resources/release'
        raw = self.run([app / 'Contents/Resources/runtime/bin/python', '-I', '-B', '-c',
                        code, source, *args], timeout=timeout,
                       expected_returncode=expected_returncode)
        # The production CLI emits an indented JSON object. Parse exactly the
        # complete document; arbitrary logs or multiple objects must not hide
        # an unsuccessful command behind a selected trailing JSON line.
        result = json.loads(raw)
        if type(result) is not dict:
            raise ValueError('oracle child must return one JSON object')
        return result

    def cli(self, command, *, app=None, expected_refusal=None, timeout=180):
        code = ROLLBACK_FIXTURE if command == ['restore-app'] and expected_refusal is None else CLI_SCRIPT
        result = self.app_run(app or self.target, code, '--port', self.port, *command,
                              expected_returncode=1 if expected_refusal is not None else 0,
                              timeout=timeout)
        if expected_refusal is not None:
            assert result.get('ok') is False and result.get('reason') == expected_refusal, result
        return result

    def health(self):
        value = self.cli(['health'])
        assert value['ok'] is True and value['worker_active'] is None
        assert value['final_click_actor'] == 'user'
        manifest = json.loads((self.target / 'Contents/Resources/release/release-source-manifest.json').read_bytes())
        assert value['loaded_source_sha256'] == manifest['source_sha256']
        return value

    def stop(self):
        result = self.cli(['stop'])
        assert result.get('ok') is True, result
        assert not (self.state / 'service.json').exists()

    def reopen(self):
        result = self.cli(['native-entry', '--native-smoke'])
        assert result.get('ok') is True and result.get('native_window') is True and result.get('native_page') is True, result
        self.health()

    def snapshot(self):
        # Logical SQLite rows include ciphertext bytes; service registries and
        # WAL bookkeeping may legitimately differ after a clean stop/restart.
        with sqlite3.connect(self.state / 'tasks.sqlite3') as db:
            tables = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
            rows = [(name, db.execute('SELECT * FROM "' + name.replace('"','""') + '" ORDER BY rowid').fetchall()) for name in tables]
        files = [self.state / 'task-answers.key', self.state.parent / 'synthetic-profile.json']
        return rows, [(path.read_bytes(), path.stat().st_mode, path.stat().st_ino) for path in files]

    def verify_state(self, expected, tasks):
        assert self.snapshot() == expected
        assert self.app_run(self.target, VERIFY_STATE, self.state, json.dumps(tasks))['ok']
        assert self.snapshot() == expected
        self.checked.update({'paused_tasks_preserved','encrypted_typed_answers_preserved','answer_key_profile_preserved'})

    @contextmanager
    def mounted(self, image, label):
        mount = self.root / ('mount-' + label); mount.mkdir()
        raw = self.run(['/usr/bin/hdiutil','attach','-readonly','-nobrowse','-owners','off',
                        '-mountpoint',mount,'-plist',image], timeout=60)
        data = plistlib.loads(raw.encode())
        assert any(item.get('mount-point') == str(mount) for item in data['system-entities'])
        try:
            assert os.statvfs(mount).f_flag & os.ST_RDONLY
            assert set(mount.iterdir()) == {mount / APP_NAME}
            yield mount / APP_NAME
        finally:
            self.run(['/usr/bin/hdiutil','detach',mount], timeout=60)

    def sign_version(self, source, number, runtime, host=None):
        from executor.autonomy import consumer, release
        from executor.autonomy.app_distribution import ARCHIVE_NAME, RECEIPT_NAME, _archive_app
        from scripts import prepare_macos_signing as prep, finalize_macos_signing as final
        build = self.root / ('unsigned-' + str(number)); build.mkdir()
        result = consumer.install_macos_app(source, destination=build / 'Applications',
            task_state_root=build / 'empty-state', standalone_runtime=runtime,
            native_presentation=True, native_host=host)
        assert result.get('ok') is True and result.get('presentation') == 'native', result
        app = build / 'Applications' / APP_NAME
        # Version is unsigned build input. No signed payload gets edited here.
        info = app / 'Contents/Info.plist'
        value = plistlib.loads(info.read_bytes()); value['CFBundleVersion'] = str(100 + number)
        info.write_bytes(plistlib.dumps(value, sort_keys=True))
        assert consumer._trusted_bundle(app)
        distribution = self.root / ('distribution-' + str(number)); distribution.mkdir()
        archive = distribution / ARCHIVE_NAME; _archive_app(app, archive)
        manifest = json.loads((app / 'Contents/Resources/runtime' / release.RUNTIME_MANIFEST_NAME).read_bytes())
        unsigned_runtime = app / 'Contents/Resources/runtime'
        unsigned_paths = sorted(path.relative_to(unsigned_runtime).as_posix()
            for path in unsigned_runtime.rglob('*') if path.is_file()
            and path.relative_to(unsigned_runtime).as_posix() != release.RUNTIME_MANIFEST_NAME)
        assert unsigned_paths == sorted(entry['path'] for entry in manifest['files'])
        unsigned_wheels = public_wheels(unsigned_runtime)
        receipt = {'format':'jae-macos-distribution-v1','archive':ARCHIVE_NAME,
            'archive_sha256':digest(archive),'app_name':APP_NAME,
            'source_sha256':release.source_manifest(app / 'Contents/Resources/release')['source_sha256'],
            'runtime_sha256':manifest['runtime_sha256'],'requirements_sha256':manifest['requirements_sha256'],
            'signing':'unsigned','certification':'NOT_CERTIFIED','final_click_actor':'user',
            'task_state':'excluded','build_host_metadata':'excluded','presentation':'native'}
        (distribution / RECEIPT_NAME).write_bytes(encoded(receipt))
        workspace = self.root / ('signed-' + str(number))
        pin = digest(distribution / RECEIPT_NAME)
        identity = prep.prepare_signing_workspace(distribution, workspace,
            expected_receipt_sha256=pin, required_publisher_policy=POLICY)
        signed = workspace / APP_NAME
        bridge = json.loads((signed / prep.BRIDGE).read_bytes())
        catalog = [entry for entry in bridge['signing_order']]
        assert len(catalog) > 2
        assert any('site-packages/' in item['path'] for item in catalog)
        kwargs = dict(expected_receipt_sha256=pin,
            expected_prepared_identity_sha256=digest(workspace / prep.IDENTITY),
            expected_policy_sha256=hashlib.sha256(prep._encoded(POLICY)).hexdigest(),
            required_publisher_policy=POLICY)
        for item in catalog:
            self.run(['/usr/bin/codesign','--force','--sign','-','--timestamp=none',
                      '--identifier',item['required_identifier'],signed / item['path']], timeout=30)
        prepared = final.prepare_current_signed_payload(distribution, workspace, **kwargs)
        assert prepared['phase'] == 'NESTED_SIGNED_CURRENT_PAYLOAD'
        self.run(['/usr/bin/codesign','--force','--sign','-','--timestamp=none',
                  '--identifier',BUNDLE_ID,signed], timeout=30)
        final_identity = final.finalize_current_signed_payload(distribution, workspace, **kwargs)
        assert len(final_identity['signing_deltas']) == len(catalog)
        assert final_identity['consumer_admission'] == 'NOT_ADMITTED'
        from executor.autonomy.signed_payload import read_current_payload
        current = read_current_payload(signed)
        assert current['runtime']['runtime_sha256'] != manifest['runtime_sha256']
        observed = self.app_run(signed, READERS, signed)
        coverage = coverage_record(unsigned_paths,
            [entry['path'] for entry in current['runtime']['files']],unsigned_wheels,observed)
        return signed, app, len(catalog), coverage

    def copy_signed(self, app, target):
        from executor.autonomy.consumer import _bundle_transaction_identity
        from executor.autonomy.bundle_copy import copy_bundle_payload
        identity = _bundle_transaction_identity(app)
        assert identity is not None
        target.mkdir(mode=0o700)
        entry = target.stat()
        copy_bundle_payload(app, target, identity, expected_target_identity=(entry.st_dev,entry.st_ino))
        copied = _bundle_transaction_identity(target)
        assert copied is not None and copied[2] == identity[2]
        return copied

    def test_xattr(self, path, *, remove=False):
        # Python's os.setxattr/removexattr are Linux-only APIs. Use the actual
        # macOS tool and touch only this synthetic attribute, never quarantine
        # or all attributes. The subprocess remains in the shared wall budget.
        if platform.system() != 'Darwin':
            raise ValueError('synthetic xattr exercise requires real macOS')
        if not Path(path).resolve(strict=True).is_relative_to(self.root):
            raise ValueError('synthetic xattr target outside private fixture')
        if remove:
            self.run(['/usr/bin/xattr','-d','com.example.jae-oracle',path],timeout=30)
        else:
            self.run(['/usr/bin/xattr','-w','com.example.jae-oracle','SYNTHETIC',path],timeout=30)

    def negatives(self, app, pristine):
        from executor.autonomy import consumer, publisher_policy
        from executor.autonomy.signed_payload import RELATIVE_PATH
        result = self.run([sys.executable,'-I','-B','-c',PRODUCTION_REFUSAL,pristine,app,json.dumps(POLICY)])
        assert json.loads(result)['ok']; self.checked.update({'production_rejects_adhoc','missing_policy'})
        try:
            publisher_policy.verify_publisher(app,{**POLICY,'team_id':'WRONGTEAM1'})
        except ValueError as error:
            assert str(error) == 'signed_payload_publisher_refused'
        else:
            raise AssertionError('wrong test publisher admitted')
        self.checked.add('wrong_policy')
        negative = self.root / 'negative' / APP_NAME; negative.parent.mkdir()
        self.copy_signed(app, negative)
        cases = [('missing_current',negative / RELATIVE_PATH),
                 ('tampered_payload',negative / 'Contents/Resources/release/executor/__init__.py'),
                 ('extra_file',negative / 'Contents/Resources/runtime/extra-test-file'),
                 ('extra_xattr',negative / 'Contents/Resources/runtime/bin/python')]
        for case,path in cases:
            saved = path.read_bytes() if path.exists() else None
            if case == 'missing_current': path.unlink()
            elif case == 'extra_xattr': self.test_xattr(path)
            else: path.write_bytes((saved or b'') + b'\nSYNTHETIC DAMAGE\n')
            assert consumer._bundle_transaction_identity(negative) is None, case
            before = sorted(str(p.relative_to(negative)) for p in negative.rglob('*'))
            refused = consumer.install_macos_bundle(negative,destination=self.root / 'must-not-install',task_state_root=self.root / 'must-not-initialize')
            assert refused['ok'] is False and not (self.root / 'must-not-install').exists()
            assert not (self.root / 'must-not-initialize').exists()
            assert before == sorted(str(p.relative_to(negative)) for p in negative.rglob('*'))
            if case == 'extra_xattr': self.test_xattr(path,remove=True)
            elif saved is None: path.unlink()
            else: path.write_bytes(saved)
            assert consumer._trusted_bundle(negative)
            self.checked.add(case)

    def build_image(self, signed, number):
        from executor.autonomy.macos_installer_image import build_installer_image, IMAGE_NAME
        self.phase('build_verify_readonly_dmg_' + str(number))
        image_root = self.root / ('image-' + str(number))
        report = build_installer_image(output=image_root,signed_app=signed,required_publisher_policy=POLICY)
        assert report['signing'] == 'static_publisher_verified' and report['consumer_admission'] == 'NOT_ADMITTED'
        assert report['certification'] == 'NOT_CERTIFIED'
        return image_root / IMAGE_NAME

    def hide_build_roots(self, roots):
        for source in roots:
            source.rename(source.with_name(source.name + '-unavailable'))
            assert not source.exists()

    def journey(self, versions, expected_identities, source_roots, build_version):
        from executor.autonomy.consumer import _bundle_transaction_identity
        # Load and exercise the unchanged real image builder before moving the
        # source roots. Each later sealed app stays at its own signed-N path.
        images = [self.build_image(versions[0], 1)]
        # Move the adapted build paths aside. Signed readers run with -I from
        # each app-owned source/runtime; reader module paths and health source
        # digests prove what actually loaded. The original repository and input
        # runtime remain readable. A relocated unsigned app is used only for
        # the explicit cross-kind refusal, never to support signed activation.
        self.hide_build_roots(source_roots)
        self.checked.add('checkout_independent')
        self.phase('first_install_readonly_dmg')
        with self.mounted(images[0], 'first') as mounted:
            actual = _bundle_transaction_identity(mounted)
            assert actual is not None and actual[2] == expected_identities[0][2]
            self.checked.add('readonly_dmg_current_identity')
            cancel = self.app_run(mounted, ENTRY_FIXTURE, 'cancel', self.port)
            assert cancel.get('cancelled') is True and cancel.get('installed') is False
            assert cancel['actual_native_reopens'] == 0
            assert not self.target.exists() and not self.state.exists()
            self.checked.add('real_cocoa_cancel')
            first = self.app_run(mounted, ENTRY_FIXTURE, 'accept', self.port, timeout=600)
            assert first.get('installed') is True and first.get('replaced') is False
            assert first.get('reopen_requested') is True and first['actual_native_reopens'] == 1, first
            assert _bundle_transaction_identity(self.target)[2] == expected_identities[0][2]
            self.checked.add('fixture_explicit_first_install')
            self.health(); self.reopen(); self.checked.add('native_reopen_health')
        self.phase('unsigned_candidate_refused_before_signed_service_retirement')
        service_before = (self.state / 'service.json').read_bytes()
        target_before = _bundle_transaction_identity(self.target)
        unsigned = self.root / 'unsigned-1-unavailable/Applications' / APP_NAME
        mismatch = self.app_run(unsigned, ENTRY_FIXTURE, 'update', self.port)
        assert mismatch.get('ok') is False and mismatch.get('reason') == 'installer_bundle_kind_mismatch', mismatch
        assert mismatch['actual_native_reopens'] == 0
        assert (self.state / 'service.json').read_bytes() == service_before
        assert _bundle_transaction_identity(self.target) == target_before
        self.health()
        assert (self.state / 'service.json').read_bytes() == service_before
        self.checked.add('unsigned_candidate_preserves_signed_service')
        self.stop()
        tasks = self.app_run(self.target, SEED_STATE, self.state)['tasks']
        state_before = self.snapshot()
        self.reopen()
        before_service = json.loads((self.state / 'service.json').read_bytes())
        self.phase('real_stopped_service_update')
        later_roots = build_version(2)
        images.append(self.build_image(versions[1], 2))
        self.hide_build_roots(later_roots)
        with self.mounted(images[1], 'second') as mounted:
            update = self.app_run(mounted, ENTRY_FIXTURE, 'update', self.port, timeout=600)
            assert update.get('ok') is True and update.get('updated') is True and update['actual_native_reopens'] == 1, update
        after_service = json.loads((self.state / 'service.json').read_bytes())
        assert before_service != after_service
        assert before_service['pid'] != after_service['pid']
        deadline = time.monotonic() + 5
        while True:
            try: os.kill(before_service['pid'],0)
            except ProcessLookupError: break
            if time.monotonic() >= deadline:
                raise AssertionError('retired actual service still alive')
            time.sleep(.05)
        self.health(); self.verify_state(state_before,tasks)
        self.checked.add('real_service_retired_update')
        previous = self.target.parent / ('.' + APP_NAME + '.previous')
        failed = self.target.parent / ('.' + APP_NAME + '.failed')
        assert _bundle_transaction_identity(self.target)[2] == expected_identities[1][2]
        assert _bundle_transaction_identity(previous)[2] == expected_identities[0][2]
        self.checked.add('retained_old_signed_version')
        self.stop()
        self.phase('retained_copy_refusals_and_signed_rollback')
        damaged = previous / 'Contents/Resources/native-host/AIApplicationWindow'
        original = damaged.read_bytes(); damaged.write_bytes(original+b'\nSYNTHETIC DAMAGE\n')
        bad_before = digest(damaged)
        refused = self.cli(['restore-app'], expected_refusal='rollback_unavailable')
        assert refused.get('ok') is False and refused.get('reason') == 'rollback_unavailable', refused
        assert digest(damaged) == bad_before and _bundle_transaction_identity(self.target)[2] == expected_identities[1][2]
        assert self.snapshot() == state_before
        damaged.write_bytes(original)
        assert _bundle_transaction_identity(previous)[2] == expected_identities[0][2]
        self.checked.add('damaged_retained_refused')
        failed.mkdir(); marker = failed / 'synthetic-occupied'; marker.write_bytes(b'KEEP')
        refused = self.cli(['restore-app'], expected_refusal='rollback_unavailable')
        assert refused.get('ok') is False and refused.get('reason') == 'rollback_unavailable', refused
        assert marker.read_bytes() == b'KEEP' and self.snapshot() == state_before
        assert _bundle_transaction_identity(previous)[2] == expected_identities[0][2]
        assert _bundle_transaction_identity(self.target)[2] == expected_identities[1][2]
        failed.rename(self.root / 'retained-occupied-slot')
        self.checked.add('occupied_slot_refused')
        restored = self.cli(['restore-app'], timeout=600)
        assert restored.get('ok') is True and restored.get('restored') is True, restored
        assert _bundle_transaction_identity(self.target)[2] == expected_identities[0][2]
        assert _bundle_transaction_identity(failed)[2] == expected_identities[1][2]
        self.reopen();self.verify_state(state_before,tasks)
        self.checked.add('explicit_signed_rollback_health')
        failed.rename(self.root / 'retained-second-signed.app')
        trace = self.root / 'fault-serve-trace'
        trace.unlink(missing_ok=True)
        self.phase('signed_final_path_failure_and_actual_restoration')
        later_roots = build_version(3)
        images.append(self.build_image(versions[2], 3))
        self.hide_build_roots(later_roots)
        with self.mounted(images[2], 'third') as mounted:
            fault = self.app_run(mounted, ENTRY_FIXTURE, 'update', self.port, timeout=600)
        assert fault.get('ok') is False and fault.get('reason') == 'post_activation_unhealthy', fault
        assert fault['actual_native_reopens'] == 0
        attempts = trace.read_text().splitlines()
        assert 'staging' in attempts and 'final' in attempts
        assert attempts.index('staging') < attempts.index('final')
        self.checked.add('final_path_failure_after_staging_health')
        assert _bundle_transaction_identity(self.target)[2] == expected_identities[0][2]
        assert _bundle_transaction_identity(failed)[2] == expected_identities[2][2]
        assert not previous.exists()
        self.reopen();self.verify_state(state_before,tasks)
        self.checked.update({'real_failed_activation_restoration','failed_signed_version_retained'})
        self.stop()
        return images


def worker(repo, runtime, root, receipt):
    sys.dont_write_bytecode = True
    oracle = Oracle(root)
    os.environ.update(oracle.env)
    oracle.phase('prepare_test_only_source')
    pristine = root / 'unpatched-production';copy_unmanifested_source(repo,pristine)
    source = root / 'source-1';copy_unmanifested_source(repo,source)
    adapt_test_source(source,root)
    sys.path.insert(0,str(source))
    from executor.autonomy.consumer import _bundle_transaction_identity
    versions, unsigned, catalogs, identities, coverage = [], [], [], [], []
    def build_version(number):
        # Build every complete version when the existing journey needs it.
        # A failed first startup never spends time building future candidates.
        assert number == len(versions) + 1 and number in (1,2,3)
        if number > 1:
            current_source = root / ('source-' + str(number))
            copy_unmanifested_source(root / 'source-1-unavailable',current_source)
            with (current_source / 'executor/__init__.py').open('a') as handle:
                handle.write('\n# Independent synthetic signed version ' + str(number) + '\n')
            if number == 3:
                inject_final_path_failure(current_source,oracle.target,root / 'fault-serve-trace')
        else:
            current_source = source
        oracle.phase('build_sign_complete_version_' + str(number))
        retained_unsigned = root / 'unsigned-1-unavailable/Applications' / APP_NAME
        signed, original, count, covered = oracle.sign_version(current_source,number,
            runtime if number == 1 else retained_unsigned / 'Contents/Resources/runtime',
            None if number == 1 else retained_unsigned / 'Contents/Resources/native-host')
        versions.append(signed);unsigned.append(original);catalogs.append(count);coverage.append(covered)
        identities.append(_bundle_transaction_identity(signed))
        return [current_source, root / ('unsigned-' + str(number))]
    first_roots = build_version(1)
    oracle.phase('production_and_damage_refusals')
    oracle.negatives(versions[0],pristine)
    images = oracle.journey(versions,identities,[*first_roots,pristine],build_version)
    assert len(versions) == len(catalogs) == len(images) == 3
    assert len(set(catalogs)) == 1 and len({identity[2] for identity in identities}) == 3
    oracle.checked.add('three_complete_signed_versions')
    if oracle.checked != REQUIRED:
        raise AssertionError('incomplete signed DMG owner: ' + repr(sorted(REQUIRED-oracle.checked)))
    oracle.phase('complete')
    result = {'format':FORMAT,'status':'SYNTHETIC_SIGNED_DMG_ACCEPTANCE_CHECKED',
        'certification':'NOT_CERTIFIED','publisher':'ADHOC_TEST_COPY_ONLY',
        'consumer_admission':'NOT_ADMITTED','checked':sorted(oracle.checked),
        'signed_versions':3,'readonly_images':len(images),
        'macho_objects_per_version':catalogs,'coverage_per_version':coverage,
        'elapsed_seconds':round(time.monotonic()-oracle.started,2),'phases':oracle.phases}
    receipt.write_bytes(encoded(result));print(json.dumps(result,sort_keys=True),flush=True)


def validate_receipt(value):
    if (not isinstance(value,dict) or value.get('format') != FORMAT
            or value.get('status') != 'SYNTHETIC_SIGNED_DMG_ACCEPTANCE_CHECKED'
            or value.get('certification') != 'NOT_CERTIFIED'
            or value.get('publisher') != 'ADHOC_TEST_COPY_ONLY'
            or value.get('consumer_admission') != 'NOT_ADMITTED'
            or value.get('checked') != sorted(REQUIRED)
            or value.get('signed_versions') != 3
            or type(value.get('readonly_images')) is not int or value['readonly_images'] != 3
            or type(value.get('elapsed_seconds')) not in (int,float)
            or not 0 < value['elapsed_seconds'] < WALL_SECONDS
            or not isinstance(value.get('macho_objects_per_version'),list)
            or len(value['macho_objects_per_version']) != 3
            or any(type(n) is not int or n <= 2 for n in value['macho_objects_per_version'])
            or len(set(value['macho_objects_per_version'])) != 1
            or 'complete' not in value.get('phases',{})):
        raise ValueError('signed DMG acceptance receipt incomplete')
    coverage = value.get('coverage_per_version')
    if not isinstance(coverage,list) or len(coverage) != 3:
        raise ValueError('signed DMG acceptance coverage incomplete')
    for record in coverage:
        if (not isinstance(record,dict) or set(record) != {'unsigned_runtime_files',
                'current_runtime_files','added_runtime_files','wheel_count','wheels'}
                or type(record['unsigned_runtime_files']) is not int or record['unsigned_runtime_files'] <= 0
                or type(record['current_runtime_files']) is not int
                or record['current_runtime_files'] != record['unsigned_runtime_files'] + 2
                or record['added_runtime_files'] != ['release-runtime-manifest.json','signing-input-bridge.json']
                or type(record['wheel_count']) is not int or record['wheel_count'] <= 0
                or not isinstance(record['wheels'],list) or len(record['wheels']) != record['wheel_count']
                or any(not isinstance(wheel,list) or len(wheel) != 2
                       or any(type(part) is not str or not part for part in wheel)
                       for wheel in record['wheels'])
                or record['wheels'] != sorted(record['wheels'])
                or len({name for name,version in record['wheels']}) != record['wheel_count']
                or record != coverage[0]):
            raise ValueError('signed DMG acceptance coverage incomplete')
    return value


def service_diagnostic_tail(root):
    """Read only the private synthetic log; never keys, profiles or ACK values."""
    path = root / 'first-use-service-diagnostic.log'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            entry = os.fstat(fd)
            if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid()
                    or entry.st_nlink != 1 or entry.st_mode & 0o077):
                return None
            os.lseek(fd, max(0, entry.st_size - 32768), os.SEEK_SET)
            return os.read(fd, 32768).decode('utf-8', errors='replace')
        finally:
            os.close(fd)
    except OSError:
        return None


def observe_failed_service(root, global_deadline):
    """An already failed acceptance stays failed; observe without replay/signals.

The same-run real child may still be finishing authentication after its parent
refused. Its synthetic file is read until ACK completion or its original serve
raises, at most 180 seconds and never beyond the original total wall limit.
The existing fixture-only process cleanup still runs immediately afterwards.
"""
    started = time.monotonic()
    deadline = min(global_deadline, started + FAILURE_OBSERVATION_SECONDS)
    reason = 'global_budget_exhausted'
    while time.monotonic() < deadline:
        tail = service_diagnostic_tail(root)
        if tail is None:
            reason = 'no_service_trace'
            break
        terminal = None
        for line in tail.splitlines():
            if not line.startswith('JAE_ORACLE_STAGE '):
                continue
            try:
                event = json.loads(line.removeprefix('JAE_ORACLE_STAGE '))
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('stage') == '_FirstUseStartup.acknowledge' and event.get('event') == 'return':
                terminal = 'observed_ack_completion'
            if event.get('stage') == 'first_use_recovery.serve_first_use' and event.get('event') in ('return','raise'):
                terminal = 'observed_service_' + event['event']
        if terminal is not None:
            reason = terminal
            break
        reason = 'bounded_observation_exhausted'
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    print(json.dumps({'oracle_failed_service_observation':reason,
        'elapsed_seconds':round(time.monotonic()-started,3),
        'acceptance':'FAILED_UNCHANGED','certification':'NOT_CERTIFIED'}, sort_keys=True),
        file=sys.stderr, flush=True)


def cleanup_test_processes(root):
    """Only processes naming this fresh private fixture root are eligible."""
    raw = subprocess.check_output(['/bin/ps','-axo','pid=,command='],text=True,timeout=5)
    owned = []
    for line in raw.splitlines():
        fields = line.strip().split(None,1)
        if len(fields) == 2 and str(root) + '/' in fields[1]:
            pid = int(fields[0])
            if pid != os.getpid(): owned.append(pid)
    for pid in owned:
        try: os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass
    deadline = time.monotonic() + 5
    while owned and time.monotonic() < deadline:
        remaining = []
        for pid in owned:
            try: os.kill(pid,0);remaining.append(pid)
            except ProcessLookupError: pass
        owned = remaining
        if owned: time.sleep(.1)
    for pid in owned:
        try: os.kill(pid,signal.SIGKILL)
        except ProcessLookupError: pass


def terminate_tree(pid):
    """Reap descendants even if the real consumer starts a new session."""
    raw = subprocess.check_output(['/bin/ps','-axo','pid=,ppid='],text=True,timeout=5)
    pairs = [tuple(map(int,line.split())) for line in raw.splitlines() if line.strip()]
    owned = {pid}
    while True:
        more = {child for child,parent in pairs if parent in owned} - owned
        if not more: break
        owned.update(more)
    for child in reversed(sorted(owned)):
        try: os.kill(child,signal.SIGKILL)
        except ProcessLookupError: pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--standalone-runtime',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--worker-root',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--repo',type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('this owner requires a real Apple Silicon Mac; it cannot skip to success')
    repo = (args.repo or Path(__file__).resolve().parents[1]).resolve()
    runtime = args.standalone_runtime.resolve(strict=True)
    receipt = args.receipt.absolute()
    if receipt.exists():
        parser.error('acceptance receipt must be a fresh path')
    if args.worker_root:
        worker(repo,runtime,args.worker_root.resolve(strict=True),receipt)
        return 0
    with tempfile.TemporaryDirectory(prefix='jae-signed-dmg-oracle-') as directory:
        root = Path(directory).resolve(strict=True)
        private_receipt = root / 'receipt.json'
        # Fixed parent-owned wall limit, not a configurable test selector. The
        # whole source/build/sign/image/consumer journey is inside this process.
        command = [sys.executable,'-I','-B',str(Path(__file__).resolve()),
            '--standalone-runtime',str(runtime),'--receipt',str(private_receipt),
            '--worker-root',str(root),'--repo',str(repo)]
        parent_started = time.monotonic()
        process = subprocess.Popen(command,start_new_session=True)
        code = None
        try:
            code = process.wait(timeout=WALL_SECONDS)
        except subprocess.TimeoutExpired:
            terminate_tree(process.pid);process.wait(timeout=10)
            raise RuntimeError('signed DMG acceptance exceeded 30 minutes; WIP, not acceptance') from None
        finally:
            try:
                if code not in (None, 0):
                    observe_failed_service(root, parent_started + WALL_SECONDS)
            except Exception:
                print('Failed-service observation unavailable; acceptance remains failed.', file=sys.stderr, flush=True)
            finally:
                cleanup_test_processes(root)
            tail = service_diagnostic_tail(root)
            if tail is not None:
                print(json.dumps({'oracle_service_diagnostics':tail,
                    'certification':'NOT_CERTIFIED'}, sort_keys=True), file=sys.stderr, flush=True)
            # A failed image producer may leave its read-only test volume. Do
            # not delete its mountpoint before detaching it.
            for mount in [*root.glob('mount-*'),*root.glob('.jae-installer-image-*/mounted')]:
                if mount.exists() and os.path.ismount(mount):
                    subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],check=True,timeout=60)
        if code != 0:
            raise RuntimeError('signed DMG acceptance child failed; WIP, not acceptance')
        result = validate_receipt(json.loads(private_receipt.read_bytes()))
        receipt.write_bytes(encoded(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
