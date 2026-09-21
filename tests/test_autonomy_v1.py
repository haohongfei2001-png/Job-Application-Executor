import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest

from executor import browser
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.otp import OtpBroker, extract_code
from executor.autonomy.supervisor import Supervisor, create_server, local_token
from executor.autonomy.worker import Worker, ProcessLock
from executor.models import ApplicationPlan, ApplicationStage, FieldResolution, ResolutionStatus


def spec(tmp_path, **kwargs):
    profile = tmp_path / 'fake-profile.json'
    if not profile.exists():
        profile.write_text(json.dumps({'identity': {'full_name': 'Synthetic Applicant'}}))
    return TaskSpec(company='Synthetic Co', role='Tester', target_url='https://example.test/apply', profile_ref=str(profile), **kwargs)


def waiting(q, tmp_path, suffix=''):
    s = spec(tmp_path).model_copy(update={'target_url': 'https://example.test/apply' + suffix})
    t = q.enqueue(s)
    claimed = q.claim('worker')
    q.checkpoint(t['task_id'], claimed['owner'], 'NEEDS_USER_ACTION', blocker='otp_waiting', release=True)
    return t['task_id']


def test_queue_restart_duplicate_and_target_protection(tmp_path, monkeypatch):
    q = TaskQueue(tmp_path / 'runtime')
    t = q.enqueue(spec(tmp_path))
    again = TaskQueue(q.root)
    assert again.get(t['task_id'])['stage'] == 'DISCOVERED'
    assert again.enqueue(spec(tmp_path))['task_id'] == t['task_id']
    monkeypatch.setattr('executor.autonomy.queue.assert_target_not_protected', lambda _: (_ for _ in ()).throw(RuntimeError('protected')))
    with pytest.raises(RuntimeError):
        again.enqueue(spec(tmp_path))


def test_atomic_claim_stale_recovery_and_fencing(tmp_path):
    now = [100.]
    q = TaskQueue(tmp_path / 'runtime', clock=lambda: now[0])
    tid = q.enqueue(spec(tmp_path))['task_id']
    def claim(i):
        return TaskQueue(q.root, clock=lambda: now[0]).claim(str(i), 20)
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(c is not None for c in claims) == 1
    old = next(c for c in claims if c)
    q.checkpoint(tid, old['owner'], 'FORM_FILLED')
    now[0] += 21
    recovered = TaskQueue(q.root, clock=lambda: now[0]).claim('new')
    assert recovered['checkpoint'] == 'FORM_FILLED'
    with pytest.raises(RuntimeError):
        q.checkpoint(tid, old['owner'], 'VALIDATED')
    assert not q.renew(tid, old['owner'])
    q.checkpoint(tid, recovered['owner'], 'NEEDS_USER_INPUT', blocker='unknown_facts', release=True)
    q.resume(tid)
    assert q.claim('resume')['checkpoint'] == 'FORM_FILLED'


def test_retry_bound_cancel_and_human_gate(tmp_path):
    now = [1.]
    q = TaskQueue(tmp_path / 'runtime', clock=lambda: now[0])
    tid = q.enqueue(spec(tmp_path, max_attempts=2))['task_id']
    for _ in range(2):
        t = q.claim('w')
        q.checkpoint(tid, t['owner'], 'ERROR', blocker='retry_pending', release=True)
        now[0] += 100
    assert q.claim('w') is None
    assert q.get(tid)['blocker'] == 'retry_exhausted'
    with pytest.raises(ValueError):
        q.resume(tid)
    q.cancel(tid)
    assert q.claim('w') is None
    with pytest.raises(ValueError):
        q.resume(tid)


@pytest.mark.parametrize('message,expected', [('您的验证码是 482913，五分钟有效','482913'), ('Your verification code is 7294.', '7294'), ('OTP 12345678', '12345678'), ('482913 and 7294', None), ('123456789', None)])
def test_chinese_english_extraction(message, expected):
    assert extract_code(message) == expected


def test_otp_expiry_single_consumption_and_no_persistence(tmp_path, capsys):
    q = TaskQueue(tmp_path / 'runtime')
    tid = waiting(q, tmp_path)
    now = [0.]
    broker = OtpBroker(q, clock=lambda: now[0])
    assert broker.push(message='验证码 482913', task_id=tid)['accepted']
    assert broker.consume(tid) == '482913'
    assert broker.consume(tid) is None
    broker.push(message='OTP 7294', hint='example.test')
    now[0] = 300
    assert broker.consume(tid) is None
    for path in q.root.rglob('*'):
        if path.is_file():
            assert b'482913' not in path.read_bytes()
            assert b'7294' not in path.read_bytes()
    assert '482913' not in str(capsys.readouterr())
    assert OtpBroker(TaskQueue(q.root)).consume(tid) is None


def test_ambiguous_match_never_guesses(tmp_path):
    q = TaskQueue(tmp_path / 'runtime')
    tid = waiting(q, tmp_path)
    waiting(q, tmp_path, '/other')
    broker = OtpBroker(q)
    assert not broker.push(message='OTP 482913', hint='example.test')['accepted']
    assert not broker.push(message='OTP 482913')['accepted']
    assert broker.consume(tid) is None
    assert all(t['stage'] == 'NEEDS_USER_ACTION' for t in q.tasks())


def test_api_auth_local_binding_routes_and_redacted_errors(tmp_path):
    q = TaskQueue(tmp_path / 'runtime')
    supervisor = Supervisor(q, token='x'*40)
    with pytest.raises(ValueError):
        create_server(supervisor, host='0.0.0.0')
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    def req(path, data=None, headers=None):
        r = urllib.request.Request(f'http://127.0.0.1:{port}' + path,
             data=json.dumps(data).encode() if data is not None else None,
             headers=headers or {'Authorization': 'Bearer ' + 'x'*40, 'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(r) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)
    try:
        assert req('/health', headers={'Authorization':'bad'})[0] == 401
        assert req('/health')[1]['final_click_actor'] == 'user'
        assert req('/health', headers={'Authorization':'Bearer ' + 'x'*40, 'Origin':'https://evil.test'})[0] == 403
        status, task = req('/v1/tasks', spec(tmp_path).model_dump())
        assert status == 200
        tid = task['task_id']
        assert req('/v1/tasks/'+tid)[0] == 200
        assert req('/v1/tasks/'+tid+'/submit', {})[0] == 404
        assert req('/v1/tasks', {**spec(tmp_path).model_dump(), 'submit_authorized': True})[0] == 400
        assert req('/v1/tasks', {'password':'CANARY_SECRET_23456'}) == (400, {'error':'invalid_request'})
        assert req('/v1/tasks/'+tid+'/cancel', {})[1]['stage'] == 'CANCELLED'
        assert req('/v1/events')[1]['events']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('kind', ['captcha', 'qr', 'slider', 'face', 'password'])
def test_security_challenge_maps_to_user_action(tmp_path, monkeypatch, kind):
    class FakeRunner:
        def __init__(self, url, profile, settings, **kw):
            self.plan = ApplicationPlan(execution_id=kw['execution_id'], target_url=url, site_id='fake')
        def run(self):
            self.plan.stage = ApplicationStage.BLOCKED
            self.plan.metadata['auth_kind'] = kind
            return self.plan
    q = TaskQueue(tmp_path / 'runtime')
    tid = q.enqueue(spec(tmp_path))['task_id']
    Worker(q, runner_factory=FakeRunner).run_once()
    assert q.get(tid)['stage'] == 'NEEDS_USER_ACTION'
    assert q.get(tid)['blocker'] == 'security_challenge'


def test_synthetic_browser_e2e_pause_resume_review_no_click_or_pii(tmp_path, monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError('must never touch live 9333')
    monkeypatch.setattr(browser, 'ensure_chrome', forbidden)
    monkeypatch.setattr(browser, '_alive', forbidden)
    html = tmp_path / 'fake-form.html'
    html.write_text('''<label>Full name <input id="name" required></label>
      <label>Unknown objective fact <input id="unknown_fact" required></label>
      <button onclick="document.body.dataset.clicked='yes'">确认提交</button>''')
    q = TaskQueue(tmp_path / 'runtime')
    t = q.enqueue(spec(tmp_path).model_copy(update={'target_url':html.as_uri()}))
    worker = Worker(q)
    assert worker.run_once()
    task = q.get(t['task_id'])
    assert task['stage'] == 'NEEDS_USER_INPUT'
    assert task['checkpoint'] == 'FORM_FILLED'
    assert 'unknown_fact' in task['details']['unresolved_keys']
    answer = 'CANARY_PRIVATE_VALUE'
    worker.user_input(t['task_id'], {'unknown_fact': answer})
    assert worker.run_once()
    task = q.get(t['task_id'])
    assert task['stage'] == 'READY_TO_SUBMIT'
    assert task['owner'] is None
    assert task['details']['final_review']['final_click_actor'] == 'user'
    assert not worker.run_once()
    with pytest.raises(ValueError):
        q.resume(t['task_id'])
    for p in q.root.rglob('*'):
        if p.is_file():
            assert answer.encode() not in p.read_bytes()
            assert b'Synthetic Applicant' not in p.read_bytes()
    from executor.adapters.generic_web import GenericWebAdapter
    with pytest.raises(RuntimeError, match='user click'):
        GenericWebAdapter(html.as_uri()).submit()


def test_process_lock_and_token_permissions(tmp_path):
    with ProcessLock(tmp_path / 'worker.lock'):
        with pytest.raises(RuntimeError):
            with ProcessLock(tmp_path / 'worker.lock'):
                pass
    token = local_token(tmp_path)
    assert local_token(tmp_path) == token
    assert (tmp_path / 'auth.token').stat().st_mode & 0o077 == 0
    (tmp_path / 'auth.token').chmod(0o644)
    with pytest.raises(ValueError):
        local_token(tmp_path)


def test_url_secrets_rejected_and_cancel_fences_worker(tmp_path):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TaskSpec(company='Test', role='Test', target_url='https://example.test?token=secret', profile_ref='fake.json')
    q = TaskQueue(tmp_path / 'runtime')
    tid = q.enqueue(spec(tmp_path))['task_id']
    task = q.claim('worker')
    q.cancel(tid)
    assert not q.renew(tid, task['owner'])
    with pytest.raises(RuntimeError):
        q.checkpoint(tid, task['owner'], 'FORM_FILLED')
    with pytest.raises(ValueError):
        q.checkpoint(tid, task['owner'], 'SUBMITTED')

def test_stale_paused_owner_is_resumable(tmp_path):
    now = [10.]
    q = TaskQueue(tmp_path / 'runtime', clock=lambda: now[0])
    tid = q.enqueue(spec(tmp_path))['task_id']
    task = q.claim('crashes-after-pause', lease_seconds=10)
    q.checkpoint(tid, task['owner'], 'NEEDS_USER_INPUT', blocker='unknown_facts')
    now[0] += 11
    assert q.resume(tid)['owner'] is None
    assert q.claim('recovered')


def test_checkpoint_url_recovery_never_persists_redirect_tokens(tmp_path):
    q = TaskQueue(tmp_path / 'runtime')
    tid = q.enqueue(spec(tmp_path))['task_id']
    t = q.claim('worker')
    q.checkpoint(tid, t['owner'], 'FORM_FILLED', page_url='https://example.test/resume?step=2')
    assert TaskQueue(q.root).get(tid)['checkpoint_url'].endswith('step=2')
    q.checkpoint(tid, t['owner'], 'FORM_FILLED', page_url='https://example.test/resume?token=CANARY_SECRET')
    assert b'CANARY_SECRET' not in q.path.read_bytes()
    q.checkpoint(tid, t['owner'], 'FORM_FILLED', page_url='https://other.test/resume')
    assert TaskQueue(q.root).get(tid)['checkpoint_url'] == 'https://example.test/resume?step=2'


def test_same_target_job_id_spelling_and_display_labels_are_idempotent(tmp_path):
    q = TaskQueue(tmp_path / 'runtime')
    a = spec(tmp_path).model_copy(update={'target_url':'https://example.test/apply?postId=one'})
    b = a.model_copy(update={'job_id':'one', 'campaign':'different', 'role':'Other label'})
    assert q.enqueue(a)['task_id'] == q.enqueue(b)['task_id']
    with pytest.raises(ValueError):
        TaskSpec.model_validate({**a.model_dump(), 'job_id':'conflicting'})


def test_live_denied_and_missing_session_never_open_browser(tmp_path, monkeypatch):
    monkeypatch.setenv('APPLICATION_EXECUTOR_BROWSER_MODE', 'live')
    def forbidden(*_a, **_kw):
        raise AssertionError('unauthorized browser creation')
    monkeypatch.setattr(browser, 'ensure_chrome', forbidden)
    q = TaskQueue(tmp_path / 'runtime')
    tid = q.enqueue(spec(tmp_path))['task_id']
    worker = Worker(q, runner_factory=forbidden)
    worker.run_once()
    assert q.get(tid)['blocker'] == 'live_not_authorized'
    q2 = TaskQueue(tmp_path / 'authorized')
    tid2 = q2.enqueue(spec(tmp_path, live_authorized=True))['task_id']
    monkeypatch.setattr(browser, '_alive', lambda: False)
    Worker(q2, runner_factory=forbidden).run_once()
    assert q2.get(tid2)['blocker'] == 'session_unavailable'
    with pytest.raises(RuntimeError, match='unavailable'):
        browser.connect(existing_only=True)


def test_otp_ingestion_resumes_and_replay_is_rejected(tmp_path):
    q = TaskQueue(tmp_path / 'runtime')
    tid = waiting(q, tmp_path)
    supervisor = Supervisor(q, token='x'*40)
    response = supervisor.dispatch('POST', '/v1/otp', {'task_id':tid, 'message':'验证码 482913'})
    assert response['accepted']
    assert q.get(tid)['stage'] == 'DISCOVERED'
    assert supervisor.worker.broker.consume(tid) == '482913'
    t = q.claim('still-waiting')
    q.checkpoint(tid, t['owner'], 'NEEDS_USER_ACTION', blocker='otp_waiting', release=True)
    assert not supervisor.worker.broker.push(task_id=tid, message='OTP 482913')['accepted']


def test_worker_exception_text_never_persisted_and_retries_bounded(tmp_path):
    def broken(*_a, **_kw):
        raise RuntimeError('CANARY_PASSWORD_482913 applicant@example.test')
    now = [0.]
    q = TaskQueue(tmp_path / 'runtime', clock=lambda: now[0])
    tid = q.enqueue(spec(tmp_path, max_attempts=2))['task_id']
    worker = Worker(q, runner_factory=broken)
    worker.run_once()
    now[0] += 10
    worker.run_once()
    assert q.get(tid)['blocker'] == 'retry_exhausted'
    assert not worker.run_once()
    assert b'CANARY_PASSWORD' not in q.path.read_bytes()
    assert b'applicant@example.test' not in q.path.read_bytes()


def test_cli_has_no_final_submit_command(tmp_path, capsys):
    from executor.autonomy.cli import main
    with pytest.raises(SystemExit) as result:
        main(['--runtime', str(tmp_path), 'submit'])
    assert result.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err


def test_isolated_launch_failure_stops_playwright(monkeypatch):
    class FakePW:
        stopped = False
        @property
        def chromium(self):
            return self
        def launch(self, **kwargs):
            raise RuntimeError('synthetic launch failure')
        def start(self):
            return self
        def stop(self):
            self.stopped = True
    pw = FakePW()
    monkeypatch.setattr(browser, 'sync_playwright', lambda: pw)
    with pytest.raises(RuntimeError):
        browser.connect('file:///synthetic')
    assert pw.stopped

@pytest.mark.parametrize('body', ['请拖动滑块完成验证', '图形验证码', 'Slide to verify', '请扫码登录'])
def test_real_dom_security_controls_do_not_become_ordinary_otp(tmp_path, body):
    from executor.adapters.generic_web import GenericWebAdapter
    html = tmp_path / 'security.html'
    html.write_text('<meta charset="utf-8">' + body + '<label>验证码<input id="otp"></label>')
    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.auth_challenge_kind() in {'captcha','other'}
        assert adapter.page.locator('#otp').input_value() == ''


def test_daemon_subprocess_api_otp_input_restart_end_to_end(tmp_path):
    import os
    import subprocess
    import sys
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from executor.autonomy.cli import request

    fixture = {'authenticated': False, 'submits': 0}
    form = '''<meta charset="utf-8"><label>Full name <input id="name" required></label>
      <label>Unknown objective fact <input id="unknown_fact" required></label>
      <button type="button" onclick="fetch('/submit',{method:'POST'})">确认提交</button>'''
    login = '''<meta charset="utf-8"><form id="login"><label>验证码
      <input id="otp" autocomplete="one-time-code" oninput="if(this.value==='482913')fetch('/otp-authenticated').then(()=>location.reload())"></label></form>'''
    class FixtureHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            if self.path == '/otp-authenticated':
                fixture['authenticated'] = True
            body = (form if fixture['authenticated'] else login).encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            fixture['submits'] += 1
            self.send_response(200)
            self.end_headers()
    site = ThreadingHTTPServer(('127.0.0.1',0), FixtureHandler)
    thread = threading.Thread(target=site.serve_forever, daemon=True)
    thread.start()
    root = tmp_path / 'daemon'
    log = tmp_path / 'daemon.log'
    env = {**os.environ, 'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated'}
    command = [sys.executable,'-m','executor.autonomy.cli','--runtime',str(root),'--port','0','serve']
    child = None
    def start():
        with log.open('ab') as stream:
            p = subprocess.Popen(command, env=env, stdout=stream, stderr=stream)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            assert p.poll() is None, 'isolated supervisor exited'
            if (root/'service.json').exists():
                return p, json.loads((root/'service.json').read_text())['port']
            time.sleep(.05)
        raise AssertionError('supervisor startup timeout')
    def wait_for(port, tid, stage):
        deadline = time.monotonic()+20
        while time.monotonic()<deadline:
            t = request(root,port,'/v1/tasks/'+tid)
            if t['stage']==stage and not t['owner']:
                return t
            assert t['stage'] != 'ERROR', t['blocker']
            time.sleep(.1)
        raise AssertionError('task stage timeout: '+t['stage'])
    try:
        child, port = start()
        s = spec(tmp_path).model_copy(update={'target_url':f'http://127.0.0.1:{site.server_address[1]}/apply'})
        tid = request(root,port,'/v1/tasks',s.model_dump())['task_id']
        assert wait_for(port,tid,'NEEDS_USER_ACTION')['blocker']=='otp_waiting'
        assert request(root,port,'/v1/otp',{'task_id':tid,'message':'验证码 482913'})['accepted']
        wait_for(port,tid,'NEEDS_USER_INPUT')
        request(root,port,'/v1/tasks/'+tid+'/user-input',{'answers':{'unknown_fact':'FAKE_PRIVATE_ANSWER'}})
        wait_for(port,tid,'READY_TO_SUBMIT')
        child.terminate()
        child.wait(timeout=10)
        child, port = start()
        assert request(root,port,'/v1/tasks/'+tid)['stage']=='READY_TO_SUBMIT'
        assert request(root,port,'/health')['final_click_actor']=='user'
        assert fixture['submits']==0
        assert fixture['authenticated']
        for path in [*root.rglob('*'), log]:
            if path.is_file() and path.name != 'auth.token':
                data=path.read_bytes()
                assert b'482913' not in data
                assert b'FAKE_PRIVATE_ANSWER' not in data
                assert b'Synthetic Applicant' not in data
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        site.shutdown()
        site.server_close()
        thread.join()
