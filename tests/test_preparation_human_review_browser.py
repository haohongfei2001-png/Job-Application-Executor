"""Hosted-Mac private-child composition against an independent loopback server.

The test-only bootstrap relocates the contract and drives a native dialog. No
production selector, parent confirm/send command, fake native admission, real
recipient, or physical-human claim is involved.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from executor.autonomy import task_preparation as task_module
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.preparation import private_child as private
from executor.preparation import request_classifier as classifier
from executor.preparation.process_identity import OwnedProcessIdentity
from test_preparation_final_journal_v1 import rows
from test_preparation_request_classifier_browser import body
from test_qiyunfang_preparation_browser import replica

NATIVE_MAC = pytest.mark.skipif(
    sys.platform != 'darwin', reason='Real headed Chrome/private native child requires hosted Mac')

SESSION = 'SYNTHETIC_PRIVATE_REVIEW_SESSION_' + 's' * 32
APPLICANT = 'SYNTHETIC_PRIVATE_REVIEW_APPLICANT'
CANARY = 'SYNTHETIC_PRIVATE_REVIEW_PROTECTED_CANARY'


FIXED_BOOTSTRAP = ('import sys;sys.path.insert(0,sys.argv[1]);'
                   'from executor.preparation.private_child import worker_main;'
                   'worker_main([int(x) for x in sys.argv[2:]])')


def fixture_popen(real_popen, replacement, launches):
    """Intercept only the known private child, never independent OS probes."""
    prefix = [sys.executable, '-I', '-B', '-c', FIXED_BOOTSTRAP,
              str(Path(private.__file__).resolve().parents[2])]

    def launch(args, **options):
        matched = (isinstance(args, (list, tuple)) and len(args) == 11
                   and list(args[:6]) == prefix
                   and all(isinstance(fd, str) and fd.isdecimal() for fd in args[6:])
                   and tuple(map(int, args[6:])) == options.get('pass_fds'))
        if not matched:
            return real_popen(args, **options)
        launches.append(tuple(args))
        replacement_args = list(args)
        replacement_args[4] = replacement
        return real_popen(replacement_args, **options)

    return launch


def test_fixture_bootstrap_does_not_rewrite_independent_process_probes():
    calls = []
    launches = []
    sentinel = object()

    def real(args, **options):
        calls.append((args, options))
        return sentinel

    launch = fixture_popen(real, 'SYNTHETIC_FIXTURE_BOOTSTRAP', launches)
    ps = ['ps', '-ww', '-p', '123', '-o', 'uid=', '-o', 'ppid=',
          '-o', 'lstart=', '-o', 'stat=', '-o', 'command=']
    options = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE, 'text': True}
    assert launch(ps, **options) is sentinel
    assert calls[-1][0] is ps and calls[-1][1] == options
    assert ps[4] == '-o' and launches == []
    command = [sys.executable, '-I', '-B', '-c', FIXED_BOOTSTRAP,
               str(Path(private.__file__).resolve().parents[2]), '10', '11', '12', '13', '14']
    child_options = {'pass_fds': (10, 11, 12, 13, 14), 'close_fds': True}
    assert launch(command, **child_options) is sentinel
    assert calls[-1][0] == command[:4] + ['SYNTHETIC_FIXTURE_BOOTSTRAP'] + command[5:]
    assert calls[-1][1] == child_options and launches == [tuple(command)]
    assert command[4] == FIXED_BOOTSTRAP
    near_match = list(command)
    near_match[4] += ';pass'
    assert launch(near_match, **child_options) is sentinel
    assert calls[-1][0] is near_match and len(launches) == 1

# This code is reachable only through the test's replacement of the fixed -c
# bootstrap. Production receives neither fixture paths nor decision selectors.
FACTORY = r'''
import json
from pathlib import Path
from executor.autonomy.queue import TaskQueue
from executor.autonomy import task_preparation
from executor.preparation import observation,runner,request_classifier,human_request,final_journal,human_review
from executor.preparation.controller import PreparationController
from executor.preparation.flow import PreparationFlow
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.native_admission import NativePreparationAdmission
from executor.preparation.qiyunfang import digest
from test_preparation_request_classifier_browser import SEND

CONFIG = json.loads(Path(__file__).with_suffix('.json').read_text())
BASE = CONFIG['base']
PROOF = Path(CONFIG['proof'])
GATE = Path(CONFIG['gate'])
for module in (task_preparation, observation, runner, request_classifier, human_request, human_review):
    if hasattr(module, 'CONTRACT_URL'): module.CONTRACT_URL = BASE + '/'
for module in (request_classifier, human_request, final_journal, human_review):
    if hasattr(module, 'FINAL_URL'): module.FINAL_URL = BASE + '/final'

def make(root, valid):
    queue = TaskQueue(root)
    evidence = {'native_admitted': False, 'opened': [], 'closed': [], 'fixture_decision': False, 'post_confirm_mutation': False}
    def save():
        temporary = PROOF.with_suffix('.tmp')
        temporary.write_text(json.dumps(evidence))
        temporary.replace(PROOF)
    admission = NativePreparationAdmission(still_authorized=lambda: controller._alive())
    class Review(human_review.HumanReviewCoordinator):
        def __init__(self, owner):
            assert owner.context.pages == []
            assert admission.admit(owner) is True
            receipt = admission._receipt
            assert receipt['native_proxy_blocked'] is True
            assert receipt['protocols'] == ['http', 'https', 'ws', 'wss']
            evidence['native_admitted'] = True
            evidence['process_sha'] = owner.identity.process_sha
            super().__init__(owner, admission.admit, forwarding_admission=lambda: True)
            self.fixture_owner = owner
            self.fixture_started = False
            self.fixture_observing = False
            save()
        def begin(self, flow):
            result = super().begin(flow)
            if result['status'] == 'MANUAL_REVIEW_ACTIVE':
                self.fixture_page = flow.page
                self.fixture_cdp = self.fixture_owner.context.new_cdp_session(flow.page)
                self.fixture_cdp.send('Page.enable')
                def opened(event):
                    evidence['opened'].append({k: event.get(k) for k in ('type', 'frameId', 'hasBrowserHandler')})
                    save()
                def closed(event):
                    evidence['closed'].append({k: event.get(k) for k in ('result', 'frameId')})
                    save()
                self.fixture_cdp.on('Page.javascriptDialogOpening', opened)
                self.fixture_cdp.on('Page.javascriptDialogClosed', closed)
                flow.page.evaluate(SEND, {'url': BASE + '/final', 'body': CONFIG['body']})
                self.fixture_started = True
            return result
        def tick(self):
            bridge = getattr(self, 'bridge', None)
            if (self.fixture_started and bridge is not None and bridge._dialog is not None
                    and bridge._state == 'DIALOG_OPEN' and evidence['opened']
                    and GATE.exists() and not evidence['fixture_decision']):
                evidence['fixture_decision'] = True
                save()
                # Only this synthetic test fixture may drive the native modal.
                if CONFIG['decision'] == 'cancel': bridge._dialog.dismiss()
                else:
                    bridge._dialog.accept()
                    if CONFIG['decision'] == 'post_confirm_mutation':
                        # Renderer mutation is deliberately between the native
                        # paired close and the real drain/forward guard.
                        self.fixture_page.evaluate("""() => {
                            document.querySelector('[data-formid="0"] input').value =
                                'SYNTHETIC_MUTATED_AFTER_CONFIRM';
                        }""")
                        evidence['post_confirm_mutation'] = True
                        save()
            return super().tick()
        def close(self):
            result = super().close()
            save()
            return result
    def flow(authority, owner, **kwargs):
        def document(route):
            assert route.request.method == 'GET'
            response = owner.client.get(BASE + '/', max_redirects=0, max_retries=0)
            try: route.fulfill(response=response)
            finally: response.dispose()
        owner.context.route(BASE + '/', document)
        page = owner.context.new_page()
        page.goto(BASE + '/')
        return PreparationFlow(authority, owner, page,
            resources_sha=digest('SYNTHETIC_PRIVATE_HUMAN_REVIEW'), **kwargs)
    controller = PreparationController(queue, valid, flow_factory=flow,
        owner_factory=lambda: DisposablePreparationSession(headless=False, channel='chrome'),
        write_admission=admission.admit, upload_admission=admission.admit,
        human_review_factory=Review)
    return controller
'''


def eventually(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Synthetic native-child evidence did not arrive before deadline')


@NATIVE_MAC
@pytest.mark.parametrize('decision', ['accept', 'cancel', 'revoke', 'redirect', 'lost_response', 'post_confirm_mutation'])
def test_private_human_review_exact_request_or_zero_and_closed_owner(
        tmp_path, monkeypatch, capfd, decision):
    received = []
    gets = []
    html = replica().encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            gets.append(self.path)
            assert self.path == '/'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=UTF-8')
            self.send_header('Set-Cookie', 'session=SYNTHETIC_REVIEW_SESSION; HttpOnly; SameSite=Lax; Path=/')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers['Content-Length'])),
                             self.headers.get('Cookie'), self.headers.get('X-Application-Token')))
            if decision == 'lost_response':
                # The recipient independently received the exact bytes, but
                # closes without any HTTP response. The caller cannot know
                # whether the remote effect happened and must never retry.
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if decision == 'redirect':
                self.send_response(307)
                self.send_header('Location', '/redirect-target')
            else:
                self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(task_module, 'CONTRACT_URL', base + '/')
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'fields': {'identity.full_name': {'value': APPLICANT}}}))
    profile.chmod(0o600)
    original_profile = profile.read_bytes()
    queue = TaskQueue(tmp_path / 'state')
    task = queue.enqueue(TaskSpec(company=task_module.CONTRACT_COMPANY,
        role=classifier.CONTRACT_ROLE, target_url=base + '/', profile_ref=str(profile)))
    fields = [{'id': int(field.field_id), 'type': int(field.data_type), 'must': field.required_marker,
               'val': APPLICANT if field.field_id == '0' else classifier.CONTRACT_ROLE if field.field_id == '8' else CANARY}
              for field in classifier.FIELDS if field.field_id.isdigit()]
    expected_body = body(rows=fields)
    proof = tmp_path / 'proof.json'
    gate = tmp_path / 'fixture-dialog-gate'
    harness = tmp_path / 'private_review_fixture.py'
    harness.write_text(FACTORY)
    harness.with_suffix('.json').write_text(json.dumps({'base': base, 'proof': str(proof),
        'gate': str(gate), 'decision': decision, 'body': expected_body}))
    real_popen = subprocess.Popen
    tests = Path(__file__).parent.resolve()
    launches = []

    replacement = ('import sys;sys.path.insert(0,sys.argv[1]);'
                   'sys.path.insert(0,' + repr(str(tests)) + ');'
                   'sys.path.insert(0,' + repr(str(tmp_path)) + ');'
                   'from private_review_fixture import make;'
                   'from executor.preparation.private_child import worker_main;'
                   'worker_main([int(x) for x in sys.argv[2:]],controller_factory=make)')
    launch = fixture_popen(real_popen, replacement, launches)

    monkeypatch.setattr(private.subprocess, 'Popen', launch)
    allowed = [True]
    checks = []
    owner = None
    public_results = []
    try:
        owner = private.PrivatePreparationChild(queue,
            lambda session: checks.append(session) is None and session == SESSION and allowed[0])
        offer = owner.open(task['task_id'], task['revision'], SESSION, ['0', '8'])
        assert offer['live_write_available'] is True
        prepared = owner.approve(offer['nonce'], offer['scope_sha'], SESSION, approve_transmission=True)
        assert prepared['status'] == 'PREPARED_UNVERIFIED'
        assert len(queue.field_actions(task['task_id'])) == 2
        started = owner.begin_human_review(SESSION)
        public_results.append(started)
        assert started['status'] == 'MANUAL_REVIEW_ACTIVE'
        assert started['submit_capability'] is False
        assert started['automatic_retry'] is False
        assert started['server_application_verified'] is False
        evidence = eventually(lambda: json.loads(proof.read_text()) if proof.exists()
                              and json.loads(proof.read_text())['opened'] else None)
        assert evidence['native_admitted'] is True
        assert len(evidence['opened']) == 1
        opening = evidence['opened'][0]
        assert opening['type'] == 'confirm' and opening['hasBrowserHandler'] is True
        assert received == [] and rows(queue) == []
        assert evidence['fixture_decision'] is False
        before = len(checks)
        if decision == 'revoke':
            allowed[0] = False
            eventually(lambda: len(checks) > before)
        # Fixture synchronization is local test state, never a parent command
        # capable of confirming a real request in production.
        gate.touch()
        if decision != 'revoke':
            expected = ('RETURNED_UNVERIFIED' if decision == 'accept' else
                        'UNKNOWN_OUTCOME' if decision in {'redirect', 'lost_response'} else 'CANCELLED')
            def finished():
                status = owner.status()
                public_results.append(status)
                return status if status.get('final_status') == expected else None
            eventually(finished)
        assert owner.shutdown(timeout=25)
        public_results.append(owner.status())
        assert owner.status()['status'] == 'CLOSED'
        assert owner._process.returncode == 0 and len(launches) == 1
        assert not queue.preparation_in_flight()
        assert len(checks) > before
        evidence = json.loads(proof.read_text())
        identity = OwnedProcessIdentity.load(queue.root, evidence['process_sha'])
        assert identity.absence() == {'status': 'ABSENT', 'process_sha': evidence['process_sha']}
        if decision != 'revoke':
            assert evidence['closed'] == [{'result': decision != 'cancel', 'frameId': opening['frameId']}]
        assert evidence['post_confirm_mutation'] is (decision == 'post_confirm_mutation')
        assert gets == ['/']  # No follow-up redirect navigation or retry.
        attempted = decision in {'accept', 'redirect', 'lost_response'}
        assert received == ([('/final', expected_body.encode(), 'session=SYNTHETIC_REVIEW_SESSION',
                              'SYNTHETIC_TOKEN')] if attempted else [])
        final_rows = rows(queue)
        assert len(final_rows) == (1 if attempted else 0)
        if attempted:
            assert final_rows[0]['outcome'] == ('RETURNED_UNVERIFIED' if decision == 'accept' else 'UNKNOWN_OUTCOME')
        assert profile.read_bytes() == original_profile
        exposed = json.dumps(public_results + final_rows + queue.field_actions(task['task_id'])
                             + queue.run_attempts(task['task_id'])) + ''.join(capfd.readouterr())
        for secret in (APPLICANT, CANARY, SESSION, expected_body, 'SYNTHETIC_MUTATED_AFTER_CONFIRM'):
            assert secret not in exposed
        assert not {'confirm', 'send', 'accept'}.intersection(private._OPERATIONS)
        print('PRIVATE_HUMAN_REVIEW ' + json.dumps({'decision': decision,
            'native_dialog_events': True, 'final_post_count': len(received),
            'durable_final_slots': len(final_rows), 'closed_exact_owner': True,
            'physical_human_proven': False}), flush=True)
    finally:
        if owner is not None:
            assert owner.shutdown(timeout=25)
        server.shutdown()
        server.server_close()
        serving.join()
