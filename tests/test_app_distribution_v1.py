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
from executor.autonomy.consumer import APP_NAME, _candidate_starts, _trusted_bundle
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
    # Intake the SAME full delivered archive; do not build a smaller fixture.
    # No extracted payload is executed and no installed app/state is touched.
    from executor.autonomy.app_distribution import stage_macos_distribution
    before_archive = (output / ARCHIVE_NAME).stat()
    with stage_macos_distribution(output) as admitted:
        staged = admitted
        assert admitted == admitted.resolve(strict=True)
        assert not any(part.is_symlink() for part in (admitted, *admitted.parents))
        assert _trusted_bundle(admitted)
        staged_release = admitted / "Contents/Resources/release"
        staged_runtime = admitted / "Contents/Resources/runtime"
        assert source_manifest(staged_release)["source_sha256"] == receipt["source_sha256"]
        assert verify_runtime_candidate(staged_runtime, staged_release)
        staged_identity = json.loads((staged_runtime / "release-runtime-manifest.json").read_text())
        assert staged_identity["runtime_sha256"] == receipt["runtime_sha256"]
        assert staged_identity["requirements_sha256"] == receipt["requirements_sha256"]
        assert not (staged_release / "runtime").exists()
        assert not list(admitted.rglob("tasks.sqlite3"))
        assert queue.get(task["task_id"]) == before
    assert not staged.exists()
    after_archive = (output / ARCHIVE_NAME).stat()
    assert (after_archive.st_ino, after_archive.st_size, after_archive.st_mtime_ns) == (
        before_archive.st_ino, before_archive.st_size, before_archive.st_mtime_ns)
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
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
        # Actual app UI now reads only; retain the real authenticated backend
        # refusal as a separate negative, with no direct updater invocation.
        probe_phase('update_refusal')
        update=page.get_by_role('button',name='更新状态',exact=True)
        observed_requests=[]
        page.on('request',lambda request:observed_requests.append(
            (request.method,urlsplit(request.url).path)))
        with page.expect_response(lambda response:
                urlsplit(response.url).path=='/ui/api/update-status') as outcome:
            update.click()
        assert outcome.value.status==200
        assert outcome.value.request.method=='GET'
        assert outcome.value.json()['status']=='idle'
        expect(page.locator('#update-dialog')).to_be_visible()
        expect(page.locator('#update-observation')).to_contain_text('这不代表已检查新版本')
        assert all(path!='/ui/api/update' for method,path in observed_requests)
        assert all(method=='GET' for method,path in observed_requests)
        page.locator('#update-close').click()
        expect(page.locator('#update-dialog')).not_to_be_visible()
        assert page.locator('#update-observation').inner_text()==''
        expect(update).to_be_focused()
        expect(update).to_be_enabled()
        refused_update=context.request.post(base+'/ui/api/update',
            headers={'Content-Type':'application/json','Origin':base},data='{}')
        assert refused_update.status==409
        assert refused_update.json()['reason']=='packaged_update_not_ready'
        # No new session/ticket/auth refresh or install/stop/restart operation.
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


def test_operator_build_defaults_to_native_with_explicit_fallback_without_publication(
    tmp_path, monkeypatch, capsys
):
    import importlib.util
    from executor.autonomy import app_distribution

    script = Path(__file__).resolve().parents[1] / "scripts/build_macos_app.py"
    spec = importlib.util.spec_from_file_location("jae_operator_build", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(app_distribution, "build_macos_distribution",
        lambda source, **options: calls.append((source, options)) or
        {"signing": "unsigned", "certification": "NOT_CERTIFIED", "final_click_actor": "user"})
    monkeypatch.chdir(tmp_path)
    runtime, output = tmp_path / "runtime", tmp_path / "artifact"
    args = ["--standalone-runtime", str(runtime), "--output", str(output)]
    assert module.main(args) == 0
    assert calls[-1] == (script.parent.parent,
        {"standalone_runtime": runtime, "output_dir": output, "native_presentation": True})
    assert module.main(args + ["--native-presentation"]) == 0
    assert calls[-1] == (script.parent.parent,
        {"standalone_runtime": runtime, "output_dir": output, "native_presentation": True})
    assert module.main(args + ["--web-fallback"]) == 0
    assert calls[-1] == (script.parent.parent,
        {"standalone_runtime": runtime, "output_dir": output, "native_presentation": False})
    assert len(calls) == 3 and not output.exists()
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    assert all(json.loads(line) == {"signing": "unsigned", "certification": "NOT_CERTIFIED",
                                  "final_click_actor": "user"} for line in lines)


@pytest.mark.parametrize("mode", [None, 0, 1, "native"])
def test_distribution_rejects_non_boolean_native_mode_before_creating_output(tmp_path, mode):
    output = tmp_path / "invalid-artifact"
    with pytest.raises(ValueError, match="distribution_presentation_invalid"):
        build_macos_distribution(tmp_path / "source", standalone_runtime=tmp_path / "runtime",
                                 output_dir=output, platform="darwin", native_presentation=mode)
    assert not output.exists()


def test_hosted_mac_native_archive_relocates_and_launches_exact_verified_window_without_build_state(
    tmp_path, capsys, monkeypatch
):
    if sys.platform != "darwin":
        pytest.skip("Actual native archive acceptance uses hosted macOS Cocoa/WebKit")
    from executor.autonomy.consumer import _trusted_bundle, _native_bundle_matches

    repo = tmp_path / "source"
    copy_source_candidate(Path(__file__).resolve().parents[1], repo)
    prepared = tmp_path / "prepared-runtime"
    copy_standalone_runtime_candidate(Path(os.environ["JAE_STANDALONE_RUNTIME"]), prepared, repo)
    private = repo / "runtime/autonomy"
    queue = TaskQueue(private)
    task = queue.enqueue(TaskSpec(company="Synthetic native archive", role="Engineer",
        target_url="https://example.invalid/jobs/archive", profile_ref="synthetic-profile.json"))
    original = queue.get(task["task_id"])
    (private / "task-answers.key").write_text("CANARY_NATIVE_PRIVATE_KEY")
    (repo / ".env").write_text("CANARY_NATIVE_BUILD_CREDENTIAL")
    (repo / "profile.json").write_text('{"name":"CANARY_NATIVE_BUILD_PROFILE"}')
    output = tmp_path / "native-artifact"
    # Exercise the exact delivered operator with its normal (no-mode-flag) args.
    # The complete archive, relocation and Cocoa oracles below stay unchanged.
    import importlib.util

    script = repo / "scripts/build_macos_app.py"
    script.parent.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/build_macos_app.py", script)
    spec = importlib.util.spec_from_file_location("jae_native_delivery_operator", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--standalone-runtime", str(prepared), "--output", str(output)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["presentation"] == "native"
    assert receipt["signing"] == "unsigned" and receipt["certification"] == "NOT_CERTIFIED"
    assert receipt["task_state"] == "excluded" and receipt["final_click_actor"] == "user"
    assert queue.get(task["task_id"]) == original
    assert (private / "task-answers.key").read_text() == "CANARY_NATIVE_PRIVATE_KEY"
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
    assert sorted(path.name for path in output.iterdir()) == [ARCHIVE_NAME, RECEIPT_NAME]
    with (output / ARCHIVE_NAME).open("rb") as archive_file:
        assert hashlib.file_digest(archive_file, "sha256").hexdigest() == receipt["archive_sha256"]
    relocated = tmp_path / "Relocated Applications"
    relocated.mkdir()
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
        native_members = [m for m in members if "/native-host/" in m.name]
        assert sorted(Path(m.name).name for m in native_members) == [
            "AIApplicationWindow", "native-host-manifest.json"]
        archive.extractall(relocated, filter="data")
    from executor.autonomy import app_distribution as distribution_module
    from executor.autonomy.app_distribution import stage_macos_distribution
    # Reproduce a system temp ancestor alias deterministically with the SAME
    # full native delivery. Only the newly created private staging root may
    # canonicalize; native image admission must still refuse an alias spelling.
    staging_root = tmp_path / "canonical-staging-parent"
    staging_root.mkdir()
    staging_alias = tmp_path / "system-staging-alias"
    staging_alias.symlink_to(staging_root, target_is_directory=True)
    with monkeypatch.context() as scoped:
        scoped.setattr(distribution_module.tempfile, "tempdir", str(staging_alias))
        with stage_macos_distribution(output) as admitted:
            staged = admitted
            assert admitted == admitted.resolve(strict=True)
            assert not any(part.is_symlink() for part in (admitted, *admitted.parents))
            assert admitted.is_relative_to(staging_root)
            assert _trusted_bundle(admitted)
            assert not _trusted_bundle(staging_alias / admitted.relative_to(staging_root))
            assert _native_bundle_matches(admitted / "Contents/Resources/release",
                admitted / "Contents/Resources/native-host")
            assert source_manifest(admitted / "Contents/Resources/release")["source_sha256"] == receipt["source_sha256"]
            assert queue.get(task["task_id"]) == original
    assert not staged.exists()
    assert list(staging_root.iterdir()) == [] and staging_alias.is_symlink()
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
    shutil.rmtree(repo)
    shutil.rmtree(prepared)
    app = relocated / (APP_NAME + ".app")
    release = app / "Contents/Resources/release"
    runtime = app / "Contents/Resources/runtime"
    assert _trusted_bundle(app)
    assert _native_bundle_matches(release, app / "Contents/Resources/native-host")
    assert source_manifest(release)["source_sha256"] == receipt["source_sha256"]
    identity = json.loads((runtime / "release-runtime-manifest.json").read_text())
    assert identity["runtime_sha256"] == receipt["runtime_sha256"]
    assert identity["requirements_sha256"] == receipt["requirements_sha256"]
    assert verify_standalone_runtime(runtime, release)
    home = tmp_path / "consumer-home"
    home.mkdir()
    state = home / "Library/Application Support/AI投递经理/autonomy"
    user_queue = TaskQueue(state)
    user_task = user_queue.enqueue(TaskSpec(company="Synthetic consumer", role="Engineer",
        target_url="https://example.invalid/jobs/consumer", profile_ref="synthetic-profile.json"))
    before = user_queue.pause(user_task["task_id"])
    assert before["run_state"] == "PAUSED" and before["attempts"] == 0
    poison = tmp_path / "ambient"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('unowned startup')")
    env = {**os.environ, "HOME": str(home), "PYTHONHOME": str(poison),
           "PYTHONPATH": str(poison), "BROWSER": "/usr/bin/false",
           "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"}
    command = [str(runtime / "bin/python"), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
               str(release), "--runtime", str(state), "--port", "9344"]
    try:
        launch = subprocess.run(["/bin/zsh", str(app / "Contents/MacOS/AIApplicationManager"),
                                 "--native-smoke"], cwd=poison, env=env,
                                capture_output=True, text=True, timeout=30)
        actual = (home / "Library/Logs/AI投递经理/launcher.log").read_text()
        assert launch.returncode == 0, actual
        assert json.loads(actual) == {"ok": True, "opened": True, "native_window": True,
                                    "native_page": True, "final_click_actor": "user"}
        assert '/ui-login?' not in actual and 'ticket=' not in actual and 'Bearer ' not in actual
        health = subprocess.run(command + ["health"], cwd=poison, env=env,
            capture_output=True, text=True, timeout=10)
        assert health.returncode == 0 and json.loads(health.stdout)["ok"] is True
        assert user_queue.get(user_task["task_id"]) == before
        assert not list(release.rglob("__pycache__"))
        assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
    finally:
        stopped = subprocess.run(command + ["stop"], cwd=poison, env=env,
            capture_output=True, text=True, timeout=15)
        assert stopped.returncode == 0
    assert not (state / "service.json").exists()


@pytest.mark.parametrize("flags", [
    ["--native-presentation", "--web-fallback"],
    ["--web-fallback", "--native-presentation"],
])
def test_operator_build_refuses_conflicting_presentation_before_artifact_work(
    tmp_path, monkeypatch, flags
):
    import importlib.util
    from executor.autonomy import app_distribution

    script = Path(__file__).resolve().parents[1] / "scripts/build_macos_app.py"
    spec = importlib.util.spec_from_file_location("jae_operator_conflict", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(app_distribution, "build_macos_distribution",
        lambda *_args, **_kwargs: pytest.fail("ambiguous mode created an artifact"))
    output = tmp_path / "not-created"
    with pytest.raises(SystemExit) as stopped:
        module.main(["--standalone-runtime", str(tmp_path / "runtime"),
                     "--output", str(output), *flags])
    assert stopped.value.code == 2
    assert not output.exists()


def _intake_artifact(tmp_path, members):
    import io
    directory = tmp_path / "delivery"
    directory.mkdir()
    with tarfile.open(directory / ARCHIVE_NAME, "w:gz") as archive:
        for name, kind, value in members:
            info = tarfile.TarInfo(name)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if kind == "directory" else 0o644
            if kind == "directory":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind in ("symlink", "hardlink"):
                info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                info.linkname = value
                archive.addfile(info)
            elif kind == "device":
                info.type = tarfile.CHRTYPE
                archive.addfile(info)
            elif kind == "contiguous":
                info.type = tarfile.CONTTYPE
                data = value.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                data = value.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    receipt = {"format": "jae-macos-distribution-v1", "archive": ARCHIVE_NAME,
        "archive_sha256": hashlib.sha256((directory / ARCHIVE_NAME).read_bytes()).hexdigest(),
        "app_name": APP_NAME + ".app", "source_sha256": "0" * 64,
        "runtime_sha256": "0" * 64, "requirements_sha256": "0" * 64,
        "signing": "unsigned", "certification": "NOT_CERTIFIED",
        "final_click_actor": "user", "task_state": "excluded",
        "build_host_metadata": "excluded"}
    (directory / RECEIPT_NAME).write_text(json.dumps(receipt))
    return directory, receipt


@pytest.mark.parametrize("attack", [
    "parent", "absolute", "backslash", "other-root", "duplicate", "symlink",
    "hardlink", "device", "contiguous", "private", "undeclared", "missing-parent",
])
def test_distribution_intake_rejects_actual_archive_attacks_without_payload_execution(
    tmp_path, monkeypatch, attack
):
    from executor.autonomy import app_distribution as module
    root = APP_NAME + ".app"
    members = [(root, "directory", "")]
    attacks = {
        "parent": (root + "/../CANARY_INTAKE_ESCAPE", "file", "escape"),
        "absolute": (str(tmp_path / "CANARY_INTAKE_ESCAPE"), "file", "escape"),
        "backslash": (root + "/..\\CANARY_INTAKE_ESCAPE", "file", "escape"),
        "other-root": ("other.app", "directory", ""),
        "duplicate": (root, "directory", ""),
        "symlink": (root + "/alias", "symlink", str(tmp_path / "private-key")),
        "hardlink": (root + "/alias", "hardlink", str(tmp_path / "private-key")),
        "device": (root + "/device", "device", ""),
        "contiguous": (root + "/contiguous", "contiguous", "noncanonical regular type"),
        "private": (root + "/task-answers.key", "file", "CANARY_PRIVATE_PAYLOAD"),
        "undeclared": (root + "/unknown", "file", "CANARY_UNDECLARED"),
        "missing-parent": (root + "/not-created/file", "file", "CANARY_UNDECLARED"),
    }
    members.append(attacks[attack])
    private = tmp_path / "private-key"
    private.write_text("CANARY_EXISTING_PRIVATE")
    directory, receipt = _intake_artifact(tmp_path, members)
    monkeypatch.setattr(module, "_trusted_bundle",
        lambda *_args: pytest.fail("invalid intake reached bundle authority"))
    # _bundle_members refuses the otherwise structurally regular unknown file.
    with pytest.raises(ValueError):
        with module.stage_macos_distribution(directory):
            pytest.fail("unsafe archive yielded a candidate")
    assert private.read_text() == "CANARY_EXISTING_PRIVATE"
    assert not (tmp_path / "CANARY_INTAKE_ESCAPE").exists()
    assert json.loads((directory / RECEIPT_NAME).read_text()) == receipt


@pytest.mark.parametrize("field,value", [
    ("archive_sha256", "1" * 64), ("source_sha256", "BAD"),
    ("runtime_sha256", None), ("requirements_sha256", True),
    ("archive", "../private-key"), ("app_name", "other.app"),
    ("signing", "signed"), ("certification", "PASS"),
    ("final_click_actor", "assistant"), ("task_state", "included"),
    ("build_host_metadata", "included"), ("presentation", "web"),
    ("extra", "CANARY_UNKNOWN"),
])
def test_distribution_intake_refuses_receipt_drift_before_candidate_or_state(
    tmp_path, monkeypatch, field, value
):
    from executor.autonomy import app_distribution as module
    directory, receipt = _intake_artifact(tmp_path, [(APP_NAME + ".app", "directory", "")])
    receipt[field] = value
    (directory / RECEIPT_NAME).write_text(json.dumps(receipt))
    monkeypatch.setattr(module, "_trusted_bundle",
        lambda *_args: pytest.fail("invalid receipt reached bundle authority"))
    with pytest.raises(ValueError):
        with module.stage_macos_distribution(directory):
            pytest.fail("invalid receipt yielded a candidate")
    assert json.loads((directory / RECEIPT_NAME).read_text()) == receipt


def test_distribution_intake_refuses_duplicate_receipt_keys_and_aliases(tmp_path):
    from executor.autonomy.app_distribution import stage_macos_distribution
    directory, receipt = _intake_artifact(tmp_path, [(APP_NAME + ".app", "directory", "")])
    path = directory / RECEIPT_NAME
    path.write_text(json.dumps(receipt)[:-1] + ', "archive": "' + ARCHIVE_NAME + '"}')
    with pytest.raises(ValueError):
        with stage_macos_distribution(directory):
            pytest.fail("duplicate receipt yielded")
    path.unlink()
    private = tmp_path / "private-receipt"
    private.write_text(json.dumps(receipt))
    path.symlink_to(private)
    with pytest.raises(ValueError):
        with stage_macos_distribution(directory):
            pytest.fail("receipt alias yielded")
    assert private.read_text() == json.dumps(receipt)


@pytest.mark.parametrize("bound", ["compressed", "expanded", "members"])
def test_distribution_intake_enforces_stream_and_member_bounds(tmp_path, monkeypatch, bound):
    from executor.autonomy import app_distribution as module
    directory, receipt = _intake_artifact(tmp_path, [
        (APP_NAME + ".app", "directory", ""),
        (APP_NAME + ".app/unknown", "file", "bounded malformed archive" * 1000),
    ])
    # Small limits only exercise negative bound branches; both positive tests
    # above consume the complete real prepared distribution with normal limits.
    limits = {"compressed": "MAX_DISTRIBUTION_BYTES",
              "expanded": "MAX_DISTRIBUTION_EXPANDED_BYTES",
              "members": "MAX_DISTRIBUTION_MEMBERS"}
    monkeypatch.setattr(module, limits[bound], 1)
    monkeypatch.setattr(module, "_trusted_bundle",
        lambda *_args: pytest.fail("over-budget intake reached bundle authority"))
    with pytest.raises(ValueError):
        with module.stage_macos_distribution(directory):
            pytest.fail("over-budget archive yielded a candidate")
    assert json.loads((directory / RECEIPT_NAME).read_text()) == receipt


def test_distribution_intake_refuses_archive_alias_without_reading_private_target(tmp_path):
    from executor.autonomy.app_distribution import stage_macos_distribution
    directory, receipt = _intake_artifact(tmp_path, [(APP_NAME + ".app", "directory", "")])
    archive = directory / ARCHIVE_NAME
    original = archive.read_bytes()
    archive.unlink()
    private = tmp_path / "private-archive"
    private.write_bytes(original)
    archive.symlink_to(private)
    with pytest.raises(ValueError):
        with stage_macos_distribution(directory):
            pytest.fail("archive alias yielded")
    assert private.read_bytes() == original


def test_distribution_intake_rejects_nonzero_data_after_tar_end(tmp_path, monkeypatch):
    import gzip
    from executor.autonomy import app_distribution as module
    directory, receipt = _intake_artifact(tmp_path, [(APP_NAME + ".app", "directory", "")])
    archive = directory / ARCHIVE_NAME
    archive.write_bytes(gzip.compress(gzip.decompress(archive.read_bytes()) + b"CANARY_TRAILING_PAYLOAD"))
    receipt["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / RECEIPT_NAME).write_text(json.dumps(receipt))
    monkeypatch.setattr(module, "_trusted_bundle",
        lambda *_args: pytest.fail("trailing payload reached bundle authority"))
    with pytest.raises(ValueError):
        with module.stage_macos_distribution(directory):
            pytest.fail("trailing archive yielded")


@pytest.mark.parametrize("directory", [True, False])
def test_actual_stdlib_unicode_pax_round_trip_binds_the_writer_directory_slash(directory):
    import io
    from executor.autonomy.app_distribution import _distribution_pax_matches
    name = APP_NAME + ".app" + ("" if directory else "/Contents/原话完整文件")
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
        member.mode = 0o755 if directory else 0o644
        member.uid = member.gid = member.mtime = 0
        member.uname = member.gname = ""
        member.pax_headers = {}
        archive.addfile(member)
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode="r:") as archive:
        actual = archive.next()
        assert actual.name == name
        assert actual.pax_headers == {"path": name + ("/" if directory else "")}
        assert _distribution_pax_matches(actual) is True


@pytest.mark.parametrize("headers", [
    {"path": APP_NAME + ".app"},
    {"path": APP_NAME + ".app" + "//"},
    {"path": APP_NAME + ".app" + "/../other/"},
    {"path": APP_NAME + ".app" + "/", "mtime": "0"},
])
def test_distribution_pax_admission_refuses_noncanonical_paths_and_metadata(headers):
    from executor.autonomy.app_distribution import _distribution_pax_matches
    member = tarfile.TarInfo(APP_NAME + ".app")
    member.type = tarfile.DIRTYPE
    member.pax_headers = headers
    assert _distribution_pax_matches(member) is False


@pytest.mark.parametrize("result", [
    {"ok": True, "replaced": False, "presentation": "native"},
    {"ok": True, "replaced": True, "presentation": "native"},
    {"ok": False, "reason": "task_state_in_use"},
    {"ok": False, "reason": "update_in_progress"},
    {"ok": False, "reason": "candidate_start_failed"},
    {"ok": False, "reason": "post_activation_verification_failed", "restored": True},
])
def test_distribution_transaction_handoff_keeps_candidate_alive_and_exact_authority(
    tmp_path, monkeypatch, result
):
    # Boundary routing only. The complete hosted-Mac journey exercises actual
    # archive admission, install/state backup/rollback/health without stubs.
    from contextlib import contextmanager
    from executor.autonomy import app_distribution as module, consumer

    delivered, apps, state = tmp_path / "delivery", tmp_path / "Applications", tmp_path / "state"
    canary = tmp_path / "private-state"
    canary.write_bytes(b"UNCHANGED_TASK_AUTHORITY")
    candidate = tmp_path / "private-stage.app"
    calls, cleaned = [], []
    @contextmanager
    def admit(supplied):
        assert supplied == delivered
        candidate.mkdir()
        try:
            yield candidate
        finally:
            candidate.rmdir()
            cleaned.append(True)
    def transact(app, **options):
        assert app == candidate and app.is_dir()
        calls.append(options)
        return result
    monkeypatch.setattr(module, "stage_macos_distribution", admit)
    monkeypatch.setattr(consumer, "install_macos_bundle", transact)
    observed = module.install_macos_distribution(delivered, destination=apps,
        task_state_root=state, platform="darwin")
    assert observed is result
    assert calls == [{"destination": apps, "task_state_root": state, "platform": "darwin"}]
    assert cleaned == [True] and not candidate.exists()
    assert canary.read_bytes() == b"UNCHANGED_TASK_AUTHORITY"


def test_distribution_transaction_cleans_stage_on_installer_exception_without_replay(
    tmp_path, monkeypatch
):
    from contextlib import contextmanager
    from executor.autonomy import app_distribution as module, consumer

    stage, calls = tmp_path / "private-stage.app", []
    @contextmanager
    def admit(_supplied):
        stage.mkdir()
        try:
            yield stage
        finally:
            stage.rmdir()
    def transact(app, **options):
        assert app == stage and stage.is_dir()
        calls.append(options)
        raise RuntimeError("transaction_failure")
    monkeypatch.setattr(module, "stage_macos_distribution", admit)
    monkeypatch.setattr(consumer, "install_macos_bundle", transact)
    with pytest.raises(RuntimeError, match="^transaction_failure$"):
        module.install_macos_distribution(tmp_path / "delivery", platform="darwin")
    assert calls == [{"destination": None, "task_state_root": None, "platform": "darwin"}]
    assert not stage.exists()


def test_distribution_transaction_refuses_actual_bad_archive_before_install_or_state(
    tmp_path, monkeypatch
):
    from executor.autonomy import app_distribution as module, consumer

    directory, receipt = _intake_artifact(tmp_path, [
        (APP_NAME + ".app", "directory", ""),
        (APP_NAME + ".app/../escape", "file", "CANARY"),
    ])
    apps = tmp_path / "Applications"
    apps.mkdir()
    current = apps / (APP_NAME + ".app")
    current.mkdir()
    (current / "identity").write_bytes(b"PRESERVE_CURRENT_APP")
    state = tmp_path / "private-state"
    state.mkdir()
    (state / "task-answers.key").write_bytes(b"PRESERVE_PRIVATE_KEY")
    monkeypatch.setattr(consumer, "install_macos_bundle",
        lambda *_args, **_kwargs: pytest.fail("bad delivery entered transaction"))
    result = module.install_macos_distribution(directory, destination=apps,
        task_state_root=state, platform="darwin")
    assert result == {"ok": False, "reason": "distribution_candidate_invalid",
                     "message": "交付包无法核对；现有应用和任务保持不变。"}
    assert (current / "identity").read_bytes() == b"PRESERVE_CURRENT_APP"
    assert (state / "task-answers.key").read_bytes() == b"PRESERVE_PRIVATE_KEY"
    assert not (tmp_path / "escape").exists()
    assert json.loads((directory / RECEIPT_NAME).read_text()) == receipt
    assert sorted(path.name for path in apps.iterdir()) == [APP_NAME + ".app"]
    assert sorted(path.name for path in state.iterdir()) == ["task-answers.key"]


def test_distribution_transaction_non_mac_is_inert_before_intake(tmp_path, monkeypatch):
    from executor.autonomy import app_distribution as module

    monkeypatch.setattr(module, "stage_macos_distribution",
        lambda *_args: pytest.fail("unsupported platform entered intake"))
    assert module.install_macos_distribution(tmp_path / "missing",
        platform="linux") == {"ok": False, "reason": "macos_required"}


@pytest.mark.parametrize("runtime_form", [None, "separate", "equals"])
def test_distribution_cli_keeps_default_or_explicit_task_authority(
    tmp_path, monkeypatch, capsys, runtime_form
):
    from executor.autonomy import cli, app_distribution as module

    delivered, apps, state = tmp_path / "delivery", tmp_path / "Applications", tmp_path / "state"
    calls = []
    monkeypatch.setattr(module, "install_macos_distribution",
        lambda path, **options: calls.append((path, options)) or {"ok": True})
    prefix = (["--runtime", str(state)] if runtime_form == "separate"
              else ["--runtime=" + str(state)] if runtime_form == "equals" else [])
    assert cli.main(prefix + ["install-distribution", "--distribution", str(delivered),
        "--destination", str(apps)]) == 0
    assert calls == [(delivered, {"destination": apps,
        "task_state_root": None if runtime_form is None else state})]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_distribution_cli_reports_fixed_refusal_once_without_private_exception(
    tmp_path, monkeypatch, capsys
):
    from executor.autonomy import cli, app_distribution as module

    calls = []
    result = {"ok": False, "reason": "distribution_candidate_invalid",
              "message": "交付包无法核对；现有应用和任务保持不变。"}
    monkeypatch.setattr(module, "install_macos_distribution",
        lambda path, **options: calls.append(path) or result)
    assert cli.main(["install-distribution", "--distribution", str(tmp_path / "CANARY_PRIVATE")]) == 1
    assert len(calls) == 1 and json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("name", [RECEIPT_NAME, ARCHIVE_NAME])
def test_distribution_intake_refuses_external_hardlinked_input_before_staging(
    tmp_path, name
):
    from executor.autonomy.app_distribution import stage_macos_distribution
    directory, _receipt = _intake_artifact(tmp_path, [(APP_NAME + ".app", "directory", "")])
    supplied = directory / name
    before = supplied.read_bytes()
    supplied.unlink()
    private = tmp_path / ("private-" + name)
    private.write_bytes(before)
    os.link(private, supplied)
    with pytest.raises(ValueError, match="distribution_intake_refused"):
        with stage_macos_distribution(directory):
            pytest.fail("external hardlink yielded an installed candidate")
    assert supplied.stat().st_ino == private.stat().st_ino
    assert private.read_bytes() == before
