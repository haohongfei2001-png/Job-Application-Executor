from __future__ import annotations

import hashlib
import json
import os
import shutil
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from executor.autonomy.app_distribution import (
    ARCHIVE_NAME, RECEIPT_NAME, _archive_app, build_macos_distribution,
)
from executor.autonomy.consumer import APP_NAME, _candidate_starts
from executor.autonomy.process_entry import CLI_ENTRY_SCRIPT
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.release import (
    copy_source_candidate, source_manifest, verify_runtime_candidate,
    verify_source_candidate,
)
from executor.autonomy.standalone_runtime import (
    copy_standalone_runtime_candidate, verify_standalone_runtime,
)


@pytest.mark.parametrize("name", [
    "Contents/Resources/runtime/task-answers.key",
    "Contents/Resources/runtime/tasks.sqlite3-wal",
    "Contents/Resources/release/.env",
    "Contents/Resources/applicant-note.txt",
])
def test_archive_refuses_private_or_undeclared_payload_before_publication(tmp_path, name):
    app = tmp_path / (APP_NAME + ".app")
    payload = app / name
    payload.parent.mkdir(parents=True)
    payload.write_text("CANARY_PRIVATE_DISTRIBUTION", encoding="utf-8")
    archive = tmp_path / ARCHIVE_NAME
    with pytest.raises(ValueError, match="distribution_private_payload"):
        _archive_app(app, archive)
    assert not archive.exists()
    assert payload.read_text() == "CANARY_PRIVATE_DISTRIBUTION"


def test_build_refuses_existing_output_and_alias_without_overwriting(tmp_path):
    source = Path(__file__).resolve().parents[1]
    output = tmp_path / "existing"
    output.mkdir()
    canary = output / RECEIPT_NAME
    canary.write_text("existing artifact")
    with pytest.raises(ValueError, match="distribution_output_unavailable"):
        build_macos_distribution(source, standalone_runtime=tmp_path / "runtime",
                                 output_dir=output, platform="darwin")
    assert canary.read_text() == "existing artifact"
    alias = tmp_path / "alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="distribution_path_alias"):
        build_macos_distribution(source, standalone_runtime=tmp_path / "runtime",
                                 output_dir=alias / "new", platform="darwin")
    assert sorted(path.name for path in output.iterdir()) == [RECEIPT_NAME]



def test_candidate_failure_leaves_no_ready_artifact_or_source_state_change(tmp_path, monkeypatch):
    from executor.autonomy import app_distribution as distribution

    repo = tmp_path / "checkout"
    copy_source_candidate(Path(__file__).resolve().parents[1], repo)
    state = repo / "runtime" / "autonomy"
    state.mkdir(parents=True)
    canary = state / "auth.token"
    canary.write_text("CANARY_UNCHANGED_SOURCE_AUTH")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    output = tmp_path / "failed-output"
    attempts = []
    def reject(source, **kwargs):
        attempts.append(source)
        assert source != repo
        assert verify_source_candidate(source)
        assert not (source / "runtime").exists()
        assert kwargs["standalone_runtime"] == runtime
        assert kwargs["task_state_root"] != state
        return {"ok": False, "reason": "candidate_start_failed"}
    monkeypatch.setattr(distribution, "install_macos_app", reject)
    with pytest.raises(ValueError, match="distribution_candidate_failed"):
        build_macos_distribution(repo, standalone_runtime=runtime,
                                 output_dir=output, platform="darwin")
    assert len(attempts) == 1
    assert not output.exists()
    assert canary.read_text() == "CANARY_UNCHANGED_SOURCE_AUTH"
    assert verify_source_candidate(repo) is False  # Repo-local state is excluded, not erased.


def test_archive_refuses_member_alias_without_reading_private_target(tmp_path):
    app = tmp_path / (APP_NAME + ".app")
    runtime = app / "Contents" / "Resources" / "runtime"
    runtime.mkdir(parents=True)
    private = tmp_path / "private-key"
    private.write_text("CANARY_UNREAD_ALIAS")
    (runtime / "module.py").symlink_to(private)
    archive = tmp_path / ARCHIVE_NAME
    with pytest.raises(ValueError, match="distribution_member_invalid"):
        _archive_app(app, archive)
    assert not archive.exists()
    assert private.read_text() == "CANARY_UNREAD_ALIAS"


def test_real_archive_relocates_without_checkout_runtime_or_private_build_state(
    tmp_path, monkeypatch
):
    source = Path(os.environ["JAE_STANDALONE_RUNTIME"])
    repo = tmp_path / "checkout"
    copy_source_candidate(Path(__file__).resolve().parents[1], repo)
    prepared = tmp_path / "prepared-runtime"
    copy_standalone_runtime_candidate(source, prepared, repo)
    # Real legacy task authority and credentials in the source checkout must
    # neither block a pure build nor enter the distributed app.
    private = repo / "runtime" / "autonomy"
    queue = TaskQueue(private)
    task = queue.enqueue(TaskSpec(company="Synthetic", role="Engineer",
        target_url="https://example.invalid/jobs/build-fixture",
        profile_ref="synthetic-profile.json"))
    before = queue.get(task["task_id"])
    (repo / ".env").write_text("CANARY_BUILD_CREDENTIAL")
    (private / "task-answers.key").write_text("CANARY_BUILD_ANSWER_KEY")
    (repo / "profile.json").write_text('{"name":"CANARY_BUILD_PROFILE"}')
    output = tmp_path / "unsigned-build"
    receipt = build_macos_distribution(
        repo, standalone_runtime=prepared, output_dir=output, platform="darwin")
    assert queue.get(task["task_id"]) == before
    assert (private / "task-answers.key").read_text() == "CANARY_BUILD_ANSWER_KEY"
    assert sorted(path.name for path in output.iterdir()) == [ARCHIVE_NAME, RECEIPT_NAME]
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
    assert receipt["signing"] == "unsigned"
    assert receipt["certification"] == "NOT_CERTIFIED"
    assert receipt["task_state"] == "excluded"
    assert receipt["final_click_actor"] == "user"
    digest = hashlib.sha256()
    with (output / ARCHIVE_NAME).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    assert receipt["archive_sha256"] == digest.hexdigest()
    extracted = tmp_path / "Relocated Applications"
    extracted.mkdir()
    with tarfile.open(output / ARCHIVE_NAME, "r:gz") as archive:
        members = archive.getmembers()
        assert members
        for member in members:
            assert member.name == APP_NAME + ".app" or member.name.startswith(APP_NAME + ".app/")
            assert member.isfile() or member.isdir()
            assert member.uid == member.gid == member.mtime == 0
            assert member.uname == member.gname == ""
            assert not any(part in {".env", "profile.json", "tasks.sqlite3",
                "tasks.sqlite3-wal", "tasks.sqlite3-shm", "task-answers.key",
                "auth.token", "service.json"} for part in Path(member.name).parts)
        archive.extractall(extracted, filter="data")
    shutil.rmtree(repo)
    shutil.rmtree(prepared)
    app = extracted / (APP_NAME + ".app")
    release = app / "Contents" / "Resources" / "release"
    runtime = app / "Contents" / "Resources" / "runtime"
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('unowned startup')")
    monkeypatch.setenv("PYTHONHOME", str(poison))
    monkeypatch.setenv("PYTHONPATH", str(poison))
    assert verify_source_candidate(release)
    assert source_manifest(release)["source_sha256"] == receipt["source_sha256"]
    assert verify_runtime_candidate(runtime, release)
    identity = json.loads((runtime / "release-runtime-manifest.json").read_text())
    assert identity["runtime_sha256"] == receipt["runtime_sha256"]
    assert identity["requirements_sha256"] == receipt["requirements_sha256"]
    assert verify_standalone_runtime(runtime, release)
    assert _candidate_starts(runtime / "bin" / "python", release)
    # Installer-health journals and tokens are temporary build resources.
    assert not (release / "runtime").exists()
    assert not (runtime / "auth.token").exists()
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt

    # Launch the real relocated app entry, not a fixture CLI or direct health
    # helper. The browser oracle follows the exact issued ticket into the real
    # authenticated consumer UI without opening the hosted runner's desktop.
    # It substitutes only the URL-opening transport, never app/service code.
    home = tmp_path / "consumer-home"
    home.mkdir()
    report = tmp_path / "opened-consumer.jsonl"
    browser_probe = tmp_path / "browser_probe.py"
    phase_report = tmp_path / "browser-phase.json"
    browser_probe.write_text("""import json,os,sys,time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
began=time.monotonic()
current_phase='probe_imported'
def probe_phase(name,error_class=None):
    global current_phase
    current_phase=name
    # Fixed stage names/timing/exception class only: never record URLs,
    # tickets, cookies, tokens, inputs, diagnostics or exception messages.
    Path(os.environ['JAE_TEST_BROWSER_PHASE']).write_text(json.dumps({
        'phase':name,'elapsed':round(time.monotonic()-began,3),
        'error_class':error_class}),encoding='utf-8')
probe_phase(current_phase)
url=sys.argv[1]
parsed=urlsplit(url)
assert parsed.scheme=='http' and parsed.hostname=='127.0.0.1'
assert parsed.path=='/ui-login' and parsed.query.startswith('ticket=')
base=parsed.scheme+'://'+parsed.netloc
probe_phase('playwright_start')
with sync_playwright() as playwright:
    probe_phase('chromium_launch')
    browser=playwright.chromium.launch(headless=True)
    try:
        context=browser.new_context(viewport={'width':1024,'height':900})
        page=context.new_page()
        errors=[]
        external=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        context.on('request',lambda request:external.append(request.url)
            if urlsplit(request.url).hostname!='127.0.0.1' else None)
        probe_phase('ticket_navigation')
        response=page.goto(url,wait_until='domcontentloaded')
        assert response.status==200 and page.url==base+'/ui'
        expect(page.get_by_role('button',name='查看诊断',exact=True)).to_be_visible()
        probe_phase('cookie_contract')
        cookies=context.cookies()
        assert len(cookies)==1 and cookies[0]['name']=='application_executor_session'
        assert cookies[0]['httpOnly'] is True and cookies[0]['sameSite']=='Strict'
        probe_phase('authenticated_state')
        state=context.request.get(base+'/ui/api/state')
        assert state.status==200 and state.json()['tasks']==[]
        probe_phase('authenticated_readiness')
        readiness=context.request.get(base+'/ui/api/readiness')
        assert readiness.status==200 and isinstance(readiness.json(),dict)
        probe_phase('keyboard_diagnostics_open')
        diagnostics=page.get_by_role('button',name='查看诊断',exact=True)
        diagnostics.focus()
        page.keyboard.press('Enter')
        expect(page.locator('#diagnostics-dialog')).to_be_visible()
        expect(page.locator('#diagnostics-report')).not_to_be_empty()
        actual_report=json.loads(page.locator('#diagnostics-report').inner_text())
        actual_payload=actual_report['packaged_release']
        assert actual_report['repository']['branch']=='packaged'
        assert actual_report['repository']['worktree_clean'] is None
        assert actual_report['loaded_source']['verified_at_start'] is True
        assert actual_payload['source_verified_now'] is True
        assert actual_payload['source_sha256']==actual_report['loaded_source']['sha256']
        assert actual_payload['loaded_source_matches_disk'] is True
        assert actual_payload['runtime_verified_now'] is True
        assert len(actual_payload['runtime_sha256'])==64
        assert len(actual_payload['requirements_sha256'])==64
        assert actual_payload['interpreter_owned'] is True
        assert actual_payload['verification_scope']=='payload_integrity_only'
        assert actual_payload['signed_distribution_certified'] is False
        assert actual_report['safety']['submit_capability'] is False
        assert actual_report['safety']['final_click_actor']=='user'
        probe_phase('keyboard_diagnostics_close')
        close=page.locator('#diagnostics-close')
        close.focus()
        page.keyboard.press('Enter')
        expect(page.locator('#diagnostics-dialog')).not_to_be_visible()
        # Actual consumer action and feedback; no direct invocation of an updater.
        probe_phase('update_refusal')
        update=page.get_by_role('button',name='检查并更新',exact=True)
        with page.expect_response(lambda response:
                urlsplit(response.url).path=='/ui/api/update') as outcome:
            update.click()
        assert outcome.value.status==409
        assert outcome.value.json()['reason']=='packaged_update_not_ready'
        expect(page.locator('#toast')).to_contain_text('当前安装包尚不支持安全更新')
        expect(update).to_be_enabled()
        probe_phase('consumed_ticket_refusal')
        assert context.request.get(url).status==401
        unauthenticated=browser.new_context()
        assert unauthenticated.request.get(base+'/ui').status==401
        unauthenticated.close()
        # Losing the actual HttpOnly session reaches the real supervisor 401
        # boundary, through the same relocated executable/browser entry path.
        probe_phase('fill_unsent_company')
        page.locator('input[name="company"]').fill('UNSENT_COMPANY')
        probe_phase('fill_unsent_message')
        page.locator('#message').fill('UNSENT_LOCAL_MESSAGE')
        # Register before deleting the real HttpOnly cookie: a background
        # poll may truthfully expire/disable the UI before another button click.
        # Accept the first real authenticated API401, without stubbing a route,
        # stopping polling, refreshing auth or forcing a disabled action.
        probe_phase('real_cookie_loss')
        with page.expect_response(lambda response:
                urlsplit(response.url).path.startswith('/ui/api/')
                and response.status==401) as expired:
            context.clear_cookies()
            page.evaluate('readiness()')
        probe_phase('first_real_api401')
        assert expired.value.status==401
        # The page deliberately stops consuming an unauthorized fetch at its
        # headers. Read the same real read-only API through the browser's
        # cookie jar; do not await that abandoned page response's CDP body.
        probe_phase('expired_api_body_readback')
        assert expired.value.request.method=='GET'
        expired_body=context.request.get(expired.value.url)
        assert expired_body.status==401
        assert expired_body.json()['error']=='ui_session_required'
        probe_phase('expired_notice_focus')
        expect(page.locator('#session-expired')).to_be_visible()
        expect(page.locator('#session-expired')).to_be_focused()
        assert page.locator('input[name="company"]').input_value()=='UNSENT_COMPANY'
        assert page.locator('#message').input_value()=='UNSENT_LOCAL_MESSAGE'
        assert page.locator('#message').get_attribute('readonly') is not None
        expect(page.locator('#send')).to_be_disabled()
        expect(update).to_be_disabled()
        probe_phase('expired_diagnostics_refusal')
        refused_diagnostics=context.request.get(base+'/ui/api/diagnostics')
        assert refused_diagnostics.status==401
        assert refused_diagnostics.json()['error']=='ui_session_required'
        assert context.request.get(base+'/ui').status==401
        assert context.request.get(url).status==401
        assert not errors and not external
        probe_phase('browser_assertions_complete')
    except BaseException as error:
        probe_phase(current_phase,type(error).__name__)
        raise
    finally:
        browser.close()
probe_phase('browser_closed')
# Retain only booleans; no ticket, cookie, token, URL or private diagnostic value.
with Path(os.environ['JAE_TEST_BROWSER_REPORT']).open('a',encoding='utf-8') as file:
    file.write(json.dumps({'ui':True,'state':True,'readiness':True,
        'keyboard_diagnostics':True,'update_refused':True,
        'session_required':True,'ticket_one_use':True,'expired_session_preserves_input':True,
        'no_external_request':True})+'\\n')
""", encoding="utf-8")
    browser_cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (
        Path.home() / ("Library/Caches/ms-playwright" if sys.platform == "darwin"
                       else ".cache/ms-playwright")))
    env = {**os.environ, "HOME": str(home),
           "PLAYWRIGHT_BROWSERS_PATH": str(browser_cache),
           "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated",
           "BROWSER": shlex.join([str(runtime / "bin" / "python"), "-I", "-B", str(browser_probe), "%s"]),
           "JAE_TEST_BROWSER_REPORT": str(report),
           "JAE_TEST_BROWSER_PHASE": str(phase_report),
           "PYTHONHOME": str(poison), "PYTHONPATH": str(poison)}
    shell = "/bin/zsh" if sys.platform == "darwin" else "/bin/bash"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    state_root = home / "Library" / "Application Support" / "AI投递经理" / "autonomy"
    command = [str(runtime / "bin" / "python"), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
               str(release), "--runtime", str(state_root), "--port", "9344"]
    try:
        first = None
        for count in (1, 2):
            phase_report.unlink(missing_ok=True)
            try:
                launched = subprocess.run([shell, str(executable)], cwd=poison,
                    env=env, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired as error:
                phase = {"phase": "probe_not_started"}
                if phase_report.is_file():
                    observed = json.loads(phase_report.read_text(encoding="utf-8"))
                    phase = {key: observed[key] for key in
                             ("phase", "elapsed", "error_class") if key in observed}
                raise AssertionError("actual app deadline; app_open=" + str(count)
                                     + "; safe_browser_phase=" + json.dumps(phase)) from error
            log = home / "Library" / "Logs" / "AI投递经理" / "launcher.log"
            assert launched.returncode == 0, log.read_text() if log.exists() else launched.stderr
            assert report.is_file(), "actual app must deliver its one-use UI ticket to the browser oracle"
            opened = [json.loads(line) for line in report.read_text().splitlines()]
            assert len(opened) == count
            assert all(all(row.values()) for row in opened)
            service = json.loads((state_root / "service.json").read_text())
            assert service["port"] == 9344 and service["pid"] > 0
            if first is None:
                first = service
            else:
                assert service == first, "second app open must reuse exactly the same service writer"
            health = subprocess.run(command + ["health"], cwd=poison, env=env,
                capture_output=True, text=True, timeout=10)
            assert health.returncode == 0
            actual = json.loads(health.stdout)
            assert actual["ok"] is True and actual["worker_active"] is None
            assert actual["loaded_source_sha256"] == receipt["source_sha256"]
            assert actual["final_click_actor"] == "user"
            assert not (state_root / "update-state.json").exists()
            assert not (state_root / "updater.log").exists()
            assert not (state_root / "bootstrap.json").exists()
            assert verify_source_candidate(release)
            assert verify_standalone_runtime(runtime, release)
    finally:
        stopped = subprocess.run(command + ["stop"], cwd=poison, env=env,
            capture_output=True, text=True, timeout=15)
        assert stopped.returncode == 0, "real packaged service must stop at its safe checkpoint"
    assert not (state_root / "service.json").exists()
    assert not list(release.rglob("__pycache__"))
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
