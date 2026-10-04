from __future__ import annotations

import hashlib
import json
import os
import shutil
import shlex
import socket
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


@pytest.mark.parametrize("name", [
    "Contents/Resources/release/executor/module.py",
    "Contents/Resources/runtime/lib/module.py",
])
def test_archive_refuses_external_hardlinked_member_before_streaming(tmp_path, name):
    app = tmp_path / (APP_NAME + ".app")
    member = app / name
    member.parent.mkdir(parents=True)
    private = tmp_path / "private-payload"
    private.write_text("CANARY_OUTSIDE_DISTRIBUTION", encoding="utf-8")
    os.link(private, member)
    archive = tmp_path / ARCHIVE_NAME
    with pytest.raises(ValueError, match="distribution_member_invalid"):
        _archive_app(app, archive)
    assert not archive.exists()
    assert private.read_text(encoding="utf-8") == "CANARY_OUTSIDE_DISTRIBUTION"


def test_archive_refuses_member_swapped_to_external_alias_after_preflight(
    tmp_path, monkeypatch
):
    from executor.autonomy import app_distribution as distribution

    app = tmp_path / (APP_NAME + ".app")
    member = app / "Contents/Resources/runtime/lib/module.py"
    member.parent.mkdir(parents=True)
    member.write_text("ORIGINAL_BUNDLE_MEMBER", encoding="utf-8")
    private = tmp_path / "private-payload"
    private.write_text("CANARY_OUTSIDE_DISTRIBUTION", encoding="utf-8")
    original_members = distribution._bundle_members

    def swap_after_preflight(root):
        members = original_members(root)
        member.unlink()
        member.symlink_to(private)
        return members

    monkeypatch.setattr(distribution, "_bundle_members", swap_after_preflight)
    with pytest.raises(ValueError, match="distribution_member_changed"):
        _archive_app(app, tmp_path / ARCHIVE_NAME)
    assert private.read_text(encoding="utf-8") == "CANARY_OUTSIDE_DISTRIBUTION"


def test_archive_refuses_member_changed_during_streaming(tmp_path, monkeypatch):
    app = tmp_path / (APP_NAME + ".app")
    member = app / "Contents/Resources/runtime/lib/module.py"
    member.parent.mkdir(parents=True)
    member.write_text("ORIGINAL_BUNDLE_MEMBER", encoding="utf-8")
    original_addfile = tarfile.TarFile.addfile

    def mutate_after_stream(archive, info, fileobj=None):
        result = original_addfile(archive, info, fileobj)
        if info.name.endswith("/runtime/lib/module.py"):
            member.write_text("CHANGED_MEMBER_AFTER_STREAM", encoding="utf-8")
        return result

    monkeypatch.setattr(tarfile.TarFile, "addfile", mutate_after_stream)
    with pytest.raises(ValueError, match="distribution_member_changed"):
        _archive_app(app, tmp_path / ARCHIVE_NAME)


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
    home = tmp_path / "relocated consumer home"
    home.mkdir()
    relocated = home / "Applications"
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


@pytest.mark.parametrize("control", ["runtime_manifest", "launcher"])
def test_distribution_intake_refuses_control_alias_swapped_after_payload_verification(
    tmp_path, monkeypatch, control
):
    from executor.autonomy import app_distribution as module

    root = APP_NAME + ".app"
    manifest = root + "/Contents/Resources/runtime/release-runtime-manifest.json"
    launcher = root + "/Contents/MacOS/AIApplicationManager"
    members = [
        (root, "directory", ""),
        (root + "/Contents", "directory", ""),
        (root + "/Contents/MacOS", "directory", ""),
        (launcher, "file", "web-launcher"),
        (root + "/Contents/Resources", "directory", ""),
        (root + "/Contents/Resources/release", "directory", ""),
        (root + "/Contents/Resources/runtime", "directory", ""),
        (manifest, "file", json.dumps({
            "runtime_sha256": "0" * 64, "requirements_sha256": "0" * 64,
        })),
    ]
    directory, receipt = _intake_artifact(tmp_path, members)
    private = tmp_path / "private-control"
    private.write_text(members[-1][2] if control == "runtime_manifest"
                       else "web-launcher")
    target_name = manifest if control == "runtime_manifest" else launcher

    monkeypatch.setattr(module, "_trusted_bundle", lambda *_args: True)
    monkeypatch.setattr(module, "verify_source_candidate", lambda *_args: True)
    monkeypatch.setattr(module, "source_manifest",
                        lambda *_args: {"source_sha256": "0" * 64})
    def swap_after_verification(runtime, _release):
        target = runtime.parents[1] / "MacOS/AIApplicationManager" if (
            control == "launcher") else runtime / "release-runtime-manifest.json"
        assert target == runtime.parents[2] / target_name.removeprefix(root + "/")
        target.unlink()
        target.symlink_to(private)
        return True
    monkeypatch.setattr(module, "verify_runtime_candidate", swap_after_verification)
    with pytest.raises(ValueError, match="distribution_intake_refused:(identity|presentation)"):
        with module.stage_macos_distribution(directory):
            pytest.fail("control alias after verification yielded a candidate")
    assert private.read_text() == (members[-1][2] if control == "runtime_manifest"
                                   else "web-launcher")
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


# Reuse the complete existing native/standalone app fixture. This regression
# does not replace the distribution, interpreter, installer or candidate health.
from test_macos_host_v1 import native_installed_app


def test_hosted_mac_fresh_delivered_install_retains_old_checkout_authority(
    native_installed_app, tmp_path
):
    """No prior app may make a populated invoking checkout disappear from admission."""
    from contextlib import closing
    import sqlite3
    import socket
    import stat
    from executor.autonomy.consumer import _native_bundle_matches
    from executor.autonomy.release import MANIFEST_NAME
    from test_task_state_compatibility_v1 import legacy_state, encrypted_answers

    fixture = native_installed_app
    candidate = fixture["app"]
    candidate_source = candidate / "Contents/Resources/release"
    assert _trusted_bundle(candidate)
    assert _native_bundle_matches(candidate_source, candidate / "Contents/Resources/native-host")
    delivery = tmp_path / "complete-first-delivery"
    delivery.mkdir()
    archive = delivery / ARCHIVE_NAME
    _archive_app(candidate, archive)
    with archive.open("rb") as stream:
        archive_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    runtime_identity = json.loads((candidate / "Contents/Resources/runtime/release-runtime-manifest.json").read_text())
    receipt = {
        "format": "jae-macos-distribution-v1", "archive": ARCHIVE_NAME,
        "archive_sha256": archive_digest, "app_name": APP_NAME + ".app",
        "source_sha256": source_manifest(candidate_source)["source_sha256"],
        "runtime_sha256": runtime_identity["runtime_sha256"],
        "requirements_sha256": runtime_identity["requirements_sha256"],
        "signing": "unsigned", "certification": "NOT_CERTIFIED",
        "final_click_actor": "user", "task_state": "excluded",
        "build_host_metadata": "excluded", "presentation": "native",
    }
    (delivery / RECEIPT_NAME).write_text(json.dumps(receipt))
    archive_before = (archive.stat().st_ino, archive.stat().st_size, archive.stat().st_mtime_ns)

    # A real ordinary checkout invokes the documented first-install CLI.
    # Remove only this test-created snapshot marker to model an actual checkout;
    # otherwise is_packaged_source would deliberately select the new app root.
    home = tmp_path / "first-consumer-home"
    home.mkdir(mode=0o700)
    old_checkout = home / "Job-Application-Executor"
    copy_source_candidate(Path(__file__).resolve().parents[1], old_checkout)
    (old_checkout / MANIFEST_NAME).unlink()
    old_state = old_checkout / "runtime/autonomy"
    old_state.parent.mkdir()
    apps = home / "Applications"
    app = apps / (APP_NAME + ".app")
    target = home / "Library/Application Support/AI投递经理/autonomy"
    assert not apps.exists() and not target.exists()

    def private_inventory():
        return {p.relative_to(old_state).as_posix(): (
            p.read_bytes(), stat.S_IMODE(p.stat().st_mode), p.stat().st_ino)
            for p in old_state.rglob("*") if p.is_file()}

    with closing(legacy_state(old_state)) as db:
        key = encrypted_answers(old_state, db)
        profile = old_state / "profile.json"
        profile.write_text(json.dumps({"complete": [f"{i}: SYNTHETIC_OLD_PROFILE" for i in range(1000)]}))
        profile.chmod(0o600)
        spec = TaskSpec(company="Synthetic legacy owner", role="Synthetic role",
                        target_url="https://example.invalid/jobs/old-authority",
                        profile_ref=str(profile), live_authorized=False)
        db.execute("UPDATE tasks SET spec=? WHERE task_id='synthetic-task'", (spec.model_dump_json(),))
        db.commit()
        assert (old_state / "tasks.sqlite3-wal").stat().st_size > 0
        old_rows = db.execute("SELECT * FROM tasks").fetchall()
        old_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        before = private_inventory()
        def assert_private_preserved():
            identical = private_inventory() == before
            assert identical, "complete synthetic old private bytes/modes/inodes changed"
        env = {"HOME": str(home), "PATH": os.defpath, "LANG": "en_US.UTF-8",
               "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated", "BROWSER": "/usr/bin/false"}
        # No --runtime or destination override: exercise ordinary authority choice.
        installed = subprocess.run([sys.executable, "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
            str(old_checkout), "install-distribution", "--distribution", str(delivery)],
            cwd=old_checkout, env=env, capture_output=True, text=True, timeout=90)
        result = json.loads(installed.stdout)
        output = installed.stdout + installed.stderr
        assert key.decode() not in output and "SYNTHETIC_OLD_PROFILE" not in output
        assert_private_preserved()

        # If the current code unexpectedly activates, observe the genuine new
        # default UI/journal, then gracefully retire only that synthetic service.
        # Preserve a value-free diagnostic; never turn this path into an xfail.
        native_opened = False
        new_task_count = None
        if result.get("ok") is True:
            assert _trusted_bundle(app)
            active_source = app / "Contents/Resources/release"
            owned_python = app / "Contents/Resources/runtime/bin/python"
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            command = [str(owned_python), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
                       str(active_source), "--port", str(port)]
            try:
                opened = subprocess.run(command + ["native-launch", "--native-smoke"],
                    cwd=home, env=env, capture_output=True, text=True, timeout=40)
                native_result = json.loads(opened.stdout)
                native_opened = opened.returncode == 0 and native_result.get("opened") is True
                assert key.decode() not in opened.stdout + opened.stderr
                assert native_opened, "synthetic first-launch observation did not complete"
                with sqlite3.connect((target / "tasks.sqlite3").as_uri() + "?mode=ro", uri=True) as fresh:
                    new_task_count = fresh.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            finally:
                stopped = subprocess.run(command + ["stop"], cwd=home, env=env,
                    capture_output=True, text=True, timeout=20)
                assert stopped.returncode == 0, "synthetic service cleanup not confirmed"
            assert not (target / "service.json").exists()

        # Check complete bytes before any reader changes SQLite WAL reader marks.
        assert_private_preserved()
        rows_preserved = db.execute("SELECT * FROM tasks").fetchall() == old_rows
        answers_preserved = db.execute("SELECT * FROM task_answer_events").fetchall() == old_answers
        key_preserved = (old_state / "task-answers.key").read_bytes() == key
        assert rows_preserved and answers_preserved and key_preserved, "old logical/key authority changed"
        assert (archive.stat().st_ino, archive.stat().st_size, archive.stat().st_mtime_ns) == archive_before
        assert json.loads((delivery / RECEIPT_NAME).read_text()) == receipt
        summary = {"install_ok": result.get("ok"), "reason": result.get("reason"),
                   "app_activated": app.exists(), "native_opened": native_opened,
                   "old_task_count": len(old_rows), "new_default_task_count": new_task_count,
                   "old_private_state_preserved": True}
        assert result.get("ok") is False and result.get("reason") == "legacy_state_migration_required", json.dumps(summary)
        assert installed.returncode == 1 and not app.exists()
        assert not any((target / name).exists() for name in
                       ("tasks.sqlite3", "task-answers.key", "auth.token", "service.json"))


def test_hosted_mac_first_entry_rebinds_source_and_installs_without_checkout_or_compiler(
    native_installed_app, tmp_path, monkeypatch
):
    from executor.autonomy import consumer
    from executor.autonomy.release import MANIFEST_NAME
    f = native_installed_app
    candidate = f['app']
    release = candidate/'Contents/Resources/release'
    launcher = candidate/'Contents/MacOS/AIApplicationManager'
    approved = consumer._bundle_transaction_identity(candidate)
    assert approved is not None
    original_launcher = launcher.read_bytes()
    real_probe = consumer.verify_standalone_runtime
    def changed_during_probe(*args):
        assert real_probe(*args)
        launcher.write_text(consumer._native_packaged_launcher_v1())
        return True
    with monkeypatch.context() as scoped:
        scoped.setattr(consumer,'verify_standalone_runtime',changed_during_probe)
        refused = consumer.install_macos_bundle(candidate, destination=tmp_path/'probe-target',
            task_state_root=tmp_path/'probe-state', _first_install=True, _approved_identity=approved)
    assert refused['reason']=='first_install_source_changed'
    assert not (tmp_path/'probe-target'/(APP_NAME+'.app')).exists()
    launcher.write_bytes(original_launcher)
    assert consumer._bundle_transaction_identity(candidate)==approved

    candidate_cli=release/'executor/autonomy/cli.py'
    original_cli,original_manifest=candidate_cli.read_bytes(),(release/MANIFEST_NAME).read_bytes()
    real_copy=consumer.copy_source_candidate
    def changed_during_copy(source,destination):
        result=real_copy(source,destination)
        if source==release:
            candidate_cli.write_bytes(original_cli+b'\n# SYNTHETIC_RESEALED_SOURCE_CHANGE\n')
            (release/MANIFEST_NAME).write_text(json.dumps(source_manifest(release),sort_keys=True,separators=(',',':'))+'\n')
        return result
    with monkeypatch.context() as scoped:
        scoped.setattr(consumer,'copy_source_candidate',changed_during_copy)
        refused=consumer.install_macos_bundle(candidate,destination=tmp_path/'copy-target',
            task_state_root=tmp_path/'copy-state',_first_install=True,_approved_identity=approved)
    assert refused['reason']=='first_install_source_changed'
    assert not (tmp_path/'copy-target'/(APP_NAME+'.app')).exists()
    assert (tmp_path/'copy-target'/('.'+APP_NAME+'.app.installing')).exists()
    candidate_cli.write_bytes(original_cli);(release/MANIFEST_NAME).write_bytes(original_manifest)
    assert consumer._bundle_transaction_identity(candidate)==approved

    home=tmp_path/'fresh-first-install-home';home.mkdir(mode=0o700)
    downloads=home/'Downloads';downloads.mkdir()
    downloaded=downloads/(APP_NAME+'.app');candidate.rename(downloaded)
    release=downloaded/'Contents/Resources/release'
    python=downloaded/'Contents/Resources/runtime/bin/python'
    target=home/'Applications'/(APP_NAME+'.app')
    state=home/'Library/Application Support/AI投递经理/autonomy'
    assert not target.exists() and not state.exists()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    f['repo'].rename(tmp_path/'removed-build-checkout')
    # The real Cocoa oracle closes without approving an installation. This
    # synthetic process then supplies one explicit fixture-only first-use
    # intent. Physical button/picker approval remains a separate owner gate.
    bootstrap=r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import first_install,macos_host,cli,consumer
from executor.autonomy.runtime_paths import default_runtime
source=Path(sys.argv[1]);state=default_runtime(source);port=int(sys.argv[2])
original=macos_host.present_native_first_install;prompts=[];children=[]
def forbidden_compile(*args,**kwargs):raise AssertionError("installed path required compiler")
consumer._stage_native_host=forbidden_compile
def choose(directory,**options):
 if options:return {'action':'cancel'}
 assert original(directory,smoke=True)=={'action':'cancel'}
 prompts.append(True)
 return {'action':'install','continuity':'first_use'}
macos_host.present_native_first_install=choose
real=first_install.subprocess.Popen
def launch(args,**options):
 reopen='native-entry' in args
 child=real(args+(['--native-smoke'] if reopen else []),**options)
 if reopen:children.append(child)
 return child
first_install.subprocess.Popen=launch
result={}
try:
 result=first_install.launch_native_entry(state,port)
 assert result.get('ok') is True and result.get('installed') is True
 assert len(prompts)==len(children)==1 and children[0].wait(timeout=40)==0
 assert cli.request(state,port,'/health')['ok'] is True
 assert consumer._trusted_bundle(Path.home()/'Applications'/(consumer.APP_NAME+'.app'))
 print(json.dumps({'installed':True,'prompt_observed':True,'reopened_actual_native_ui':True,
                   'native_ready_claim_from_request':result['native_ready_verified']}))
finally:
 stopped=cli._stop_owned_service(state,port)
 assert stopped.get('ok') is True or stopped.get('reason')=='service_record_missing'
'''
    env={'HOME':str(home),'PATH':os.defpath,'LANG':'en_US.UTF-8',
         'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated','BROWSER':'/usr/bin/false',
         'PYTHONHOME':'/nonexistent-synthetic-home','PYTHONPATH':'/nonexistent-synthetic-path'}
    opened=subprocess.run([str(python),'-I','-B','-c',bootstrap,str(release),str(port)],
                          cwd=home,env=env,capture_output=True,text=True,timeout=180)
    assert opened.returncode==0,'source-bound first-install native journey did not complete'
    assert json.loads(opened.stdout)=={'installed':True,'prompt_observed':True,
        'reopened_actual_native_ui':True,'native_ready_claim_from_request':False}
    assert _trusted_bundle(target) and _trusted_bundle(downloaded)
    from executor.autonomy.first_install import _read_first_fence
    assert _read_first_fence(target.parent)['status']=='complete'
    assert source_manifest(target/'Contents/Resources/release')==source_manifest(release)
    assert not (state/'service.json').exists()
    assert not list(release.rglob('__pycache__'))


def test_hosted_mac_legacy_native_command_contract_is_preserved_through_new_installer(
    native_installed_app,tmp_path
):
    from executor.autonomy import consumer
    from executor.autonomy.release import MANIFEST_NAME
    f=native_installed_app;candidate=f['app'];release=candidate/'Contents/Resources/release'
    # Synthetic legacy command contract: a real complete bundle whose CLI
    # supports native-launch but has no native-entry command. This is not a
    # claim that the fixture is an exact historical released binary.
    cli_file=release/'executor/autonomy/cli.py'
    text=cli_file.read_text()
    parser='    entry = commands.add_parser("native-entry")\n    entry.add_argument("--native-smoke", action="store_true")\n    entry.add_argument("--first-install-continuity-fd", type=int, help=argparse.SUPPRESS)\n'
    branch='        elif args.command == "native-entry":\n            from .first_install import launch_native_entry\n            result = launch_native_entry(args.runtime, args.port, smoke=args.native_smoke,\n                                         continuity_fd=args.first_install_continuity_fd)\n'
    assert parser in text and branch in text
    cli_file.write_text(text.replace(parser,'').replace(branch,''))
    legacy_consumer=release/'executor/autonomy/consumer.py'
    legacy_consumer.write_text(legacy_consumer.read_text()+'\n_native_packaged_launcher = _native_packaged_launcher_v1\n')
    (release/MANIFEST_NAME).write_text(json.dumps(source_manifest(release),sort_keys=True,separators=(',',':'))+'\n')
    launcher=candidate/'Contents/MacOS/AIApplicationManager'
    launcher.write_text(consumer._native_packaged_launcher_v1())
    assert _trusted_bundle(candidate)
    home=tmp_path/'legacy-contract-home';home.mkdir()
    apps=home/'Applications';state=home/'Library/Application Support/AI投递经理/autonomy'
    result=consumer.install_macos_bundle(candidate,destination=apps,task_state_root=state)
    assert result['ok'] is True and result['installed'] is True
    app=apps/(APP_NAME+'.app')
    assert (app/'Contents/MacOS/AIApplicationManager').read_bytes()==launcher.read_bytes()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    source=app/'Contents/Resources/release';python=app/'Contents/Resources/runtime/bin/python'
    command=[str(python),'-I','-B','-c',CLI_ENTRY_SCRIPT,str(source),'--port',str(port)]
    env={'HOME':str(home),'PATH':os.defpath,'LANG':'en_US.UTF-8',
         'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated','BROWSER':'/usr/bin/false'}
    try:
        absent=subprocess.run(command+['native-entry','--native-smoke'],env=env,capture_output=True,text=True,timeout=10)
        assert absent.returncode!=0
        opened=subprocess.run(command+['native-launch','--native-smoke'],env=env,capture_output=True,text=True,timeout=40)
        assert opened.returncode==0 and json.loads(opened.stdout)['opened'] is True
    finally:
        stopped=subprocess.run(command+['stop'],env=env,capture_output=True,text=True,timeout=20)
        assert stopped.returncode==0


@pytest.mark.parametrize('late_authority',[False,True])
def test_hosted_mac_pending_recovery_revalidates_actual_child_before_private_initialization(
    native_installed_app,tmp_path,late_authority
):
    from executor.autonomy import consumer,first_install
    f=native_installed_app;app=f['app'];source=app/'Contents/Resources/release'
    python=app/'Contents/Resources/runtime/bin/python'
    identity=consumer._bundle_transaction_identity(app);assert identity is not None
    # Actual installer has already created the empty private authority/locks
    # before the simulated activation-to-initial-child interruption checkpoint.
    assert f['state'].is_dir() and f['state'].stat().st_mode & 0o777==0o700
    assert {path.name for path in f['state'].iterdir()}=={'native-window.lock','worker.lock','migration.lock'}
    assert all((f['state']/name).stat().st_mode & 0o777==0o600
               for name in ('native-window.lock','worker.lock','migration.lock'))
    first_install._create_first_fence(app.parent,identity)
    f['repo'].rename(tmp_path/'removed-recovery-checkout')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    probe=r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import first_install,first_use_recovery,macos_host,consumer,cli
from executor.autonomy.runtime_paths import default_runtime
source=Path(sys.argv[1]);state=default_runtime(source);port=int(sys.argv[2]);late=sys.argv[3]=='1'
app=source.parent.parent.parent;fence=first_install._fence_path(app.parent)
before=fence.stat().st_ino,fence.read_bytes();prompts=[];children=[]
real_prompt=macos_host.present_native_first_use_recovery
choice={'action':'cancel'}
def prompt(directory,**options):
 assert real_prompt(directory,smoke=True)=={'action':'cancel'}
 prompts.append(True);return dict(choice)
macos_host.present_native_first_use_recovery=prompt
def forbidden_compile(*a,**k):raise AssertionError('recovery required compiler')
consumer._stage_native_host=forbidden_compile
real_start=first_use_recovery.start_first_use
real_popen=first_use_recovery.subprocess.Popen
def popen(args,**options):
 child=real_popen(args,**options)
 if 'first-use-serve' in args:children.append(child)
 return child
first_use_recovery.subprocess.Popen=popen
def start(root,port,record):
 if late:(Path(root)/'task-answers.key').write_bytes(b'SYNTHETIC_LATE_FIRST_USE_AUTHORITY')
 return real_start(root,port,record)
first_use_recovery.start_first_use=start
try:
 cancelled=first_install.launch_native_entry(state,port,smoke=True)
 assert cancelled.get('cancelled') is True and (fence.stat().st_ino,fence.read_bytes())==before
 assert not any((state/name).exists() for name in ('tasks.sqlite3','auth.token','service.log','service.json'))
 choice={'action':'resume_first_use'}
 result=first_install.launch_native_entry(state,port,smoke=True)
 assert len(prompts)==2 and len(children)==1
 if late:
  assert result.get('ok') is False and children[0].wait(timeout=10)!=0
  assert (state/'task-answers.key').read_bytes()==b'SYNTHETIC_LATE_FIRST_USE_AUTHORITY'
  assert not any((state/name).exists() for name in ('tasks.sqlite3','auth.token','service.log','service.json','native-window.json'))
  assert (fence.stat().st_ino,fence.read_bytes())==before
 else:
  assert result.get('ok') is True and result.get('native_page') is True
  assert first_install._read_first_fence(app.parent)['status']=='complete'
  observed=cli._service_record(state/'service.json')
  assert observed['pid']==children[0].pid and cli.request(state,port,'/v1/service-identity')==observed
  assert cli.request(state,port,'/health')['ok'] is True
  reopened=first_install.launch_native_entry(state,port,smoke=True)
  assert reopened.get('ok') is True and reopened.get('native_page') is True
  assert cli._service_record(state/'service.json')==observed and len(children)==1
 print(json.dumps({'prompt_observed':True,'cancel_preserved':True,'late_authority':late,
                   'actual_native_recovery':not late,'actual_child_refused':late}))
finally:
 stopped=cli._stop_owned_service(state,port)
 assert stopped.get('ok') is True or stopped.get('reason')=='service_record_missing'
 for child in children:child.wait(timeout=10)
'''
    env={'HOME':str(f['home']),'PATH':os.defpath,'LANG':'en_US.UTF-8',
         'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated','BROWSER':'/usr/bin/false',
         'PYTHONHOME':'/nonexistent-synthetic-home','PYTHONPATH':'/nonexistent-synthetic-path'}
    outcome=subprocess.run([str(python),'-I','-B','-c',probe,str(source),str(port),'1' if late_authority else '0'],
        cwd=f['home'],env=env,capture_output=True,text=True,timeout=180)
    assert outcome.returncode==0,'complete native pending-recovery journey failed'
    assert json.loads(outcome.stdout)=={'prompt_observed':True,'cancel_preserved':True,
        'late_authority':late_authority,'actual_native_recovery':not late_authority,
        'actual_child_refused':late_authority}
    assert _trusted_bundle(app) and not (f['state']/'service.json').exists()
    assert not list(source.rglob('__pycache__'))


def _installer_distribution_from_native_app(app, directory):
    from executor.autonomy.release import RUNTIME_MANIFEST_NAME
    directory.mkdir()
    _archive_app(app,directory/ARCHIVE_NAME)
    runtime=json.loads((app/'Contents/Resources/runtime'/RUNTIME_MANIFEST_NAME).read_text())
    receipt={'format':'jae-macos-distribution-v1','archive':ARCHIVE_NAME,
        'archive_sha256':hashlib.sha256((directory/ARCHIVE_NAME).read_bytes()).hexdigest(),
        'app_name':APP_NAME+'.app','source_sha256':source_manifest(app/'Contents/Resources/release')['source_sha256'],
        'runtime_sha256':runtime['runtime_sha256'],'requirements_sha256':runtime['requirements_sha256'],
        'signing':'unsigned','certification':'NOT_CERTIFIED','final_click_actor':'user',
        'task_state':'excluded','build_host_metadata':'excluded','presentation':'native'}
    (directory/RECEIPT_NAME).write_text(json.dumps(receipt))
    return directory


def test_single_installer_builder_refuses_wrong_platform_before_read_or_output(tmp_path,monkeypatch):
    from executor.autonomy import macos_installer_image as image
    monkeypatch.setattr(image.platform,'system',lambda:'Linux')
    monkeypatch.setattr(image.subprocess,'run',lambda *a,**k:pytest.fail('unsupported platform launched image tool'))
    output=tmp_path/'image'
    with pytest.raises(ValueError,match='installer_build_requires_apple_silicon'):
        image.build_installer_image(tmp_path/'missing',output)
    assert not output.exists()


@pytest.mark.skipif(sys.platform!='darwin',reason='Real readonly DMG and Cocoa installation require hosted macOS')
def test_hosted_mac_single_dmg_install_open_update_preserves_private_authority(native_installed_app,tmp_path):
    """Actual bundle/DMG/service; only local native decision input is synthetic."""
    import plistlib
    from contextlib import contextmanager
    from executor.autonomy import consumer
    from executor.autonomy.macos_installer_image import build_installer_image,IMAGE_NAME
    from executor.autonomy.release import MANIFEST_NAME
    from executor.autonomy.worker import Worker
    from executor.facts.answers import TaskAnswerStore
    fixture=native_installed_app
    source_app=fixture['app']
    first=build_installer_image(_installer_distribution_from_native_app(source_app,tmp_path/'delivery-one'),tmp_path/'image-one')
    assert first['signing']=='unsigned' and first['certification']=='NOT_CERTIFIED'
    home=tmp_path/'consumer-home';home.mkdir(mode=0o700)
    state=home/'Library/Application Support/AI投递经理/autonomy'
    target=home/'Applications'/(APP_NAME+'.app')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    environment={'HOME':str(home),'PATH':os.defpath,'LANG':'en_US.UTF-8',
        'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated','BROWSER':'/usr/bin/false',
        'PYTHONHOME':'/nonexistent-fixture','PYTHONPATH':'/nonexistent-fixture'}
    @contextmanager
    def mounted(image,name):
        mount=tmp_path/name;mount.mkdir()
        result=subprocess.run(['/usr/bin/hdiutil','attach','-readonly','-nobrowse','-owners','off',
            '-mountpoint',str(mount),'-plist',str(image)],capture_output=True,timeout=60)
        assert result.returncode==0,'synthetic installer image mount failed'
        observed=plistlib.loads(result.stdout)
        assert any(item.get('mount-point')==str(mount) for item in observed['system-entities'])
        try:
            app=mount/(APP_NAME+'.app')
            assert consumer._trusted_bundle(app)
            yield app
        finally:
            closed=subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],capture_output=True,timeout=60)
            assert closed.returncode==0,'synthetic installer mount remained in use'
    bootstrap=r'''
import json,sys,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import first_install,consumer,macos_host,cli
from executor.autonomy.runtime_paths import default_runtime
source=Path(sys.argv[1]);port=int(sys.argv[2]);mode=sys.argv[3]
state=default_runtime(source);target=Path.home()/'Applications'/(consumer.APP_NAME+'.app')
assert Path(cli.__file__).is_relative_to(source)
children=[];choices=[];allow_first=[False]
original_first=macos_host.present_native_first_install
original_installer=macos_host.present_native_installer
def first(directory,**options):
 if options:return {'action':'cancel'}
 assert original_first(directory,smoke=True)=={'action':'cancel'}
 choices.append('first')
 return {'action':'install','continuity':'first_use'} if allow_first[0] else {'action':'cancel'}
def existing(directory,kind,**options):
 assert original_installer(directory,kind,smoke=True)=={'action':'cancel'}
 choices.append(kind)
 return {'action':'update' if kind=='update' else 'open'}
macos_host.present_native_first_install=first
macos_host.present_native_installer=existing
consumer._stage_native_host=lambda *a,**k:(_ for _ in ()).throw(AssertionError('installer invoked compiler'))
real=first_install.subprocess.Popen
def launch(args,**options):
 native=('native-entry' in args or str(args[0]).endswith('/Contents/MacOS/AIApplicationManager'))
 child=real(args+(['--native-smoke'] if native else []),**options)
 if native:children.append(child)
 return child
first_install.subprocess.Popen=launch
try:
 if mode=='fresh':
  result=first_install.launch_native_entry(state,port)
  assert result=={'ok':True,'cancelled':True,'installed':False,'final_click_actor':'user','submit_capability':False}
  assert not target.exists() and not state.exists() and children==[]
  allow_first[0]=True
 result=first_install.launch_native_entry(state,port)
 assert result.get('ok') is True and result.get('installed') is True,result
 assert len(children)==1 and children[-1].wait(timeout=40)==0
 assert cli.request(state,port,'/health')['ok'] is True
 assert consumer._trusted_bundle(target)
 if mode=='update':assert result['updated'] is True and result['replaced'] is True
 inode=target.stat().st_ino
 result=first_install.launch_native_entry(state,port)
 assert result.get('ok') is True and result['reopen_requested'] is True
 assert len(children)==2 and children[-1].wait(timeout=40)==0 and target.stat().st_ino==inode
 assert choices==(['first','first','open'] if mode=='fresh' else ['update','open']),choices
 print(json.dumps({'mode':mode,'actual_native_opened':True,'single_file_installer':True,'synthetic_decisions':True}))
finally:
 stopped=cli._stop_owned_service(state,port)
 assert stopped.get('ok') is True or stopped.get('reason')=='service_record_missing'
'''
    fixture['repo'].rename(tmp_path/'removed-build-checkout')
    with mounted(tmp_path/'image-one'/IMAGE_NAME,'mounted-one') as app:
        source=app/'Contents/Resources/release';python=app/'Contents/Resources/runtime/bin/python'
        run=subprocess.run([str(python),'-I','-B','-c',bootstrap,str(source),str(port),'fresh'],
            cwd=home,env=environment,capture_output=True,text=True,timeout=240)
        assert run.returncode==0,'single DMG first install/native reopen did not complete: '+run.stderr[-1800:]
        assert json.loads(run.stdout)['actual_native_opened'] is True
    # A new consumer enters ordinary fields/resume in the actual installed UI,
    # without supplying a JSON profile or importing the source-side editor.
    profile_before = _installed_profile_onboarding(target,state,port,home,environment,create=True)
    queue=TaskQueue(state);worker=Worker(queue)
    task=queue.enqueue(TaskSpec(company='Synthetic DMG',role='Engineer',target_url='https://example.test/dmg',
        profile_ref=json.loads(profile_before[0])['profile_path']))
    queue.pause(task['task_id'])
    # Synthetic journal fixture; real encryption and decoder, never owner data.
    answers=TaskAnswerStore(queue)
    with queue.tx() as db:
        for field,value in [('family.primary.role','SYNTHETIC_ANSWER'),('preferences.accept_travel',False)]:
            db.execute('INSERT INTO task_answer_events(task_id,field_key,ciphertext,answer_version,source,task_revision,created) VALUES(?,?,?,?,?,?,?)',
                (task['task_id'],field,answers.cipher.encrypt(json.dumps(value).encode()),1,'user_explicit_task',1,2))
    before=queue.tasks();typed=answers.load(task['task_id']);key=(state/'task-answers.key').read_bytes()
    profile_before=_installed_missing_resume_recovery(target,state,port,home,environment,profile_before)
    assert queue.tasks()==before and answers.load(task['task_id'])==typed
    assert _installed_profile_onboarding(target,state,port,home,environment,create=False,
        expected_resume=profile_before[2])==profile_before
    # The second complete candidate changes only a synthetic source version.
    source=source_app/'Contents/Resources/release'
    with (source/'executor/__init__.py').open('a') as handle:handle.write('\n# SYNTHETIC_DMG_SECOND_VERSION\n')
    (source/MANIFEST_NAME).write_text(json.dumps(source_manifest(source),sort_keys=True,separators=(',',':'))+'\n')
    info_path=source_app/'Contents/Info.plist';info=plistlib.loads(info_path.read_bytes())
    info['CFBundleVersion']=str(int(info['CFBundleVersion'])+1);info_path.write_bytes(plistlib.dumps(info))
    assert consumer._trusted_bundle(source_app)
    second=build_installer_image(_installer_distribution_from_native_app(source_app,tmp_path/'delivery-two'),tmp_path/'image-two')
    assert first['source_sha256']!=second['source_sha256']
    # Actual old app opens/closes its native window and leaves its owned service.
    old=subprocess.run([str(target/'Contents/Resources/runtime/bin/python'),'-I','-B','-c',CLI_ENTRY_SCRIPT,
        str(target/'Contents/Resources/release'),'--runtime',str(state),'--port',str(port),'native-entry','--native-smoke'],
        cwd=home,env=environment,capture_output=True,text=True,timeout=90)
    assert old.returncode==0,'old installed native app did not open'
    with mounted(tmp_path/'image-two'/IMAGE_NAME,'mounted-two') as app:
        source=app/'Contents/Resources/release';python=app/'Contents/Resources/runtime/bin/python'
        run=subprocess.run([str(python),'-I','-B','-c',bootstrap,str(source),str(port),'update'],
            cwd=home,env=environment,capture_output=True,text=True,timeout=240)
        assert run.returncode==0,'single DMG update/native reopen did not complete: '+run.stderr[-1800:]
        assert json.loads(run.stdout)['actual_native_opened'] is True
    assert TaskQueue(state).tasks()==before
    assert TaskAnswerStore(TaskQueue(state)).load(task['task_id'])==typed
    assert (state/'task-answers.key').read_bytes()==key
    assert consumer._trusted_bundle(target)
    assert consumer._trusted_bundle(target.parent/('.'+APP_NAME+'.app.previous'))
    assert source_manifest(target/'Contents/Resources/release')['source_sha256']==second['source_sha256']
    assert not (state/'service.json').exists()
    assert _installed_profile_onboarding(target,state,port,home,environment,create=False,
        expected_resume=profile_before[2])==profile_before
    assert TaskQueue(state).tasks()==before
    assert TaskAnswerStore(TaskQueue(state)).load(task['task_id'])==typed


def _installed_profile_onboarding(target,state,port,home,environment,*,create,expected_resume=None):
    """Actual installed service/UI, synthetic facts and isolated browser only."""
    from playwright.sync_api import sync_playwright, expect
    from executor.autonomy import cli
    from executor.autonomy.first_use_recovery import _owned_request
    source=target/'Contents/Resources/release'
    command=[str(target/'Contents/Resources/runtime/bin/python'),'-I','-B','-c',CLI_ENTRY_SCRIPT,
        str(source),'--runtime',str(state),'--port',str(port),'native-entry','--native-smoke']
    base='http://127.0.0.1:'+str(port)
    writes,external,errors=[],[],[]
    resume=expected_resume if expected_resume is not None else b'%PDF-1.4\nSYNTHETIC_INSTALLED_ONBOARDING\n%%EOF'
    try:
        opened=subprocess.run(command,cwd=home,env=environment,capture_output=True,text=True,timeout=90)
        assert opened.returncode==0,'installed onboarding native entry did not open'
        health=_owned_request(state,port,'/health')
        assert health['ok'] is True and health['loaded_source_sha256']==source_manifest(source)['source_sha256']
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1000,'height':850})
                def local_only(route):
                    if route.request.url.startswith(base+'/'):
                        route.continue_()
                    else:
                        external.append(route.request.url);route.abort()
                context.route('**/*',local_only)
                def open_page():
                    page=context.new_page()
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda request:writes.append(request.url) if request.method=='POST' else None)
                    ticket=_owned_request(state,port,'/v1/ui-ticket',{})['ticket']
                    page.goto(base+'/ui-login?ticket='+ticket)
                    return page
                page=open_page()
                if create:
                    expect(page.locator('#profile-onboarding')).to_be_visible()
                    page.locator('#profile-onboarding-open').click()
                    expect(page.locator('#profile-editor-field-0')).to_be_enabled()
                    page.locator('#profile-editor-field-0').fill('SYNTHETIC_INSTALLED_PERSON')
                    page.locator('#profile-editor-resume').set_input_files({
                        'name':'synthetic-resume.pdf','mimeType':'application/pdf','buffer':resume})
                    assert writes==[] and not (state/'settings.json').exists()
                    page.locator('#profile-editor-save').click()
                    expect(page.locator('#profile-editor-status')).to_contain_text('已保存在本机并重新读取确认')
                    page.locator('#profile-editor-close').click()
                    expect(page.locator('#profile-onboarding')).not_to_be_visible()
                    assert '已就绪' not in page.locator('#readiness').inner_text()
                    assert writes==[base+'/ui/api/profile-editor']
                    page.close()
                    page=open_page()
                page.locator('#profile-setup').click()
                expect(page.locator('#profile-editor-open')).to_be_enabled()
                assert not page.locator('#profile-file').is_visible()
                page.locator('#profile-editor-open').click()
                expect(page.locator('#profile-editor-field-0')).to_have_value('SYNTHETIC_INSTALLED_PERSON')
                expect(page.locator('#profile-editor-resume-status')).to_contain_text('本机已记录 PDF')
                assert page.locator('#profile-editor-resume').input_value()==''
                assert page.evaluate('localStorage.length + sessionStorage.length')==0
                assert external==[] and errors==[]
                if not create:assert writes==[]
            finally:
                browser.close()
        settings_bytes=(state/'settings.json').read_bytes()
        selected=json.loads(settings_bytes)['profile_path']
        assert Path(selected).parent==state
        profile_bytes=Path(selected).read_bytes();profile=json.loads(profile_bytes)
        assert profile['fields']['identity.full_name']['value']=='SYNTHETIC_INSTALLED_PERSON'
        asset=profile['assets']['resume'];assert Path(asset['path']).parent==state
        assert Path(asset['path']).read_bytes()==resume
        assert asset['sha256']==hashlib.sha256(resume).hexdigest()
        return settings_bytes,profile_bytes,resume
    finally:
        stopped=cli._stop_owned_service(state,port)
        assert stopped.get('ok') is True or stopped.get('reason')=='service_record_missing'


def _installed_missing_resume_recovery(target,state,port,home,environment,previous):
    """Lose only a known synthetic managed leaf; repair through installed UI."""
    from playwright.sync_api import sync_playwright, expect
    from executor.autonomy import cli
    from executor.autonomy.first_use_recovery import _owned_request
    source=target/'Contents/Resources/release'
    old_profile=Path(json.loads(previous[0])['profile_path'])
    assert old_profile.parent==state and old_profile.read_bytes()==previous[1]
    original=json.loads(previous[1]);missing=Path(original['assets']['resume']['path'])
    assert missing.parent==state and missing.read_bytes()==previous[2]
    retained=home/'retained-synthetic-original-resume.pdf'
    assert not retained.exists();missing.rename(retained)
    command=[str(target/'Contents/Resources/runtime/bin/python'),'-I','-B','-c',CLI_ENTRY_SCRIPT,
        str(source),'--runtime',str(state),'--port',str(port),'native-entry','--native-smoke']
    base='http://127.0.0.1:'+str(port)
    replacement=b'%PDF-1.4\nSYNTHETIC_EXPLICIT_REPAIR\n%%EOF'
    writes,external,errors=[],[],[]
    try:
        opened=subprocess.run(command,cwd=home,env=environment,capture_output=True,text=True,timeout=90)
        assert opened.returncode==0,'installed missing-resume repair entry did not open'
        health=_owned_request(state,port,'/health')
        assert health['ok'] is True and health['loaded_source_sha256']==source_manifest(source)['source_sha256']
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1000,'height':850})
                def local_only(route):
                    if route.request.url.startswith(base+'/'):route.continue_()
                    else:external.append(route.request.url);route.abort()
                context.route('**/*',local_only)
                page=context.new_page()
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda request:writes.append(request.url) if request.method=='POST' else None)
                ticket=_owned_request(state,port,'/v1/ui-ticket',{})['ticket']
                page.goto(base+'/ui-login?ticket='+ticket)
                page.locator('#profile-setup').click()
                expect(page.locator('#profile-editor-open')).to_be_enabled()
                page.locator('#profile-editor-open').click()
                expect(page.locator('#profile-editor-status')).to_contain_text('简历文件缺失')
                expect(page.locator('#profile-editor-resume')).to_be_enabled()
                expect(page.locator('#profile-editor-save')).to_be_disabled()
                page.locator('#profile-editor-resume').set_input_files({
                    'name':'replacement.pdf','mimeType':'application/pdf','buffer':replacement})
                assert writes==[] and (state/'settings.json').read_bytes()==previous[0]
                page.locator('#profile-editor-save').click()
                expect(page.locator('#profile-editor-status')).to_contain_text('已保存在本机并重新读取确认')
                assert writes==[base+'/ui/api/profile-editor'] and external==[] and errors==[]
                expect(page.locator('#profile-editor-field-0')).to_have_value('SYNTHETIC_INSTALLED_PERSON')
            finally:browser.close()
        new_settings=(state/'settings.json').read_bytes()
        new_profile=Path(json.loads(new_settings)['profile_path'])
        assert new_profile.parent==state and new_profile!=old_profile
        new_bytes=new_profile.read_bytes();updated=json.loads(new_bytes)
        assert updated['fields']==original['fields']
        assert old_profile.read_bytes()==previous[1] and retained.read_bytes()==previous[2]
        assert not missing.exists()
        new_resume=Path(updated['assets']['resume']['path'])
        assert new_resume.parent==state and new_resume!=missing and new_resume.read_bytes()==replacement
        assert updated['assets']['resume']['sha256']==hashlib.sha256(replacement).hexdigest()
        return new_settings,new_bytes,replacement
    finally:
        stopped=cli._stop_owned_service(state,port)
        assert stopped.get('ok') is True or stopped.get('reason')=='service_record_missing'


def test_hosted_mac_installed_first_use_short_completion_retains_exact_pending_proof(native_installed_app,tmp_path):
    from executor.autonomy import consumer,first_install
    f=native_installed_app;app=f['app'];source=app/'Contents/Resources/release'
    python=app/'Contents/Resources/runtime/bin/python'
    identity=consumer._bundle_transaction_identity(app);assert identity is not None
    assert {p.name for p in f['state'].iterdir()}=={'native-window.lock','worker.lock','migration.lock'}
    first_install._create_first_fence(app.parent,identity)
    f['repo'].rename(tmp_path/'removed-completion-checkout')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    probe=r'''
import json,os,sys,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import consumer,first_install,first_use_recovery as recovery
from executor.autonomy.runtime_paths import default_runtime
from executor.autonomy.queue import TaskQueue
source=Path(sys.argv[1]);app=source.parent.parent.parent;state=default_runtime(source)
identity=consumer._bundle_transaction_identity(app);assert identity is not None
record=recovery.prepare_recovery(app,state,identity,source.parent/'native-host',confirm=False)
fence=first_install._fence_path(app.parent);before=fence.read_bytes();inode=fence.stat().st_ino
read_fd,write_fd=os.pipe();startup=recovery._FirstUseStartup(state,int(sys.argv[2]),record,write_fd)
real_write=os.write
def short_write(fd,data):
 return real_write(fd,data[:-3] if os.fstat(fd).st_ino==inode else data)
first_install.os.write=short_write
failed=False
try:
 try:
  with startup:
   with startup.worker_guard():TaskQueue(state,_first_use_startup=startup)
 except OSError:failed=True
finally:
 first_install.os.write=real_write
 startup.__exit__(None,None,None);os.close(read_fd)
assert failed and (state/'tasks.sqlite3').is_file()
assert fence.read_bytes().startswith(before) and fence.stat().st_ino==inode
assert first_install._read_first_fence(app.parent)['status']=='pending'
assert recovery._fence_tag(app.parent)==record['fence_tag']
snapshot={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in state.iterdir()}
assert recovery.prepare_recovery(app,state,identity,source.parent/'native-host',confirm=False)['bundle_tag']==record['bundle_tag']
assert snapshot=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in state.iterdir()}
(state/'.first-use-prepared.json').rename(state.parent/'retained-synthetic-prepared-receipt')
snapshot={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in state.iterdir()}
try:recovery.prepare_recovery(app,state,identity,source.parent/'native-host',confirm=False)
except ValueError:pass
else:raise AssertionError('unknown nonempty state was adopted')
assert snapshot=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in state.iterdir()}
assert not any((state/n).exists() for n in ('service.json','service.log','native-window.json'))
assert consumer._bundle_transaction_identity(app)==identity
print(json.dumps({'installed_runtime':True,'pending_identity_preserved':True,'unreceipted_nonempty_refused':True}))
'''
    env={'HOME':str(f['home']),'PATH':os.defpath,'LANG':'en_US.UTF-8',
         'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated',
         'PYTHONHOME':'/nonexistent-synthetic-home','PYTHONPATH':'/nonexistent-synthetic-path'}
    result=subprocess.run([str(python),'-I','-B','-c',probe,str(source),str(port)],
                          cwd=f['home'],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,'installed first-use completion fault contract failed'
    assert json.loads(result.stdout)=={'installed_runtime':True,'pending_identity_preserved':True,
                                      'unreceipted_nonempty_refused':True}


@pytest.mark.parametrize('interruption', ['before_completion', 'short_completion'])
def test_hosted_mac_prepared_first_use_explicit_resume_reuses_objects_and_real_child_ack(
    native_installed_app, tmp_path, interruption
):
    from executor.autonomy import consumer, first_install
    f = native_installed_app; app = f['app']; source = app/'Contents/Resources/release'
    python = app/'Contents/Resources/runtime/bin/python'
    identity = consumer._bundle_transaction_identity(app); assert identity is not None
    assert {p.name for p in f['state'].iterdir()} == {'native-window.lock','worker.lock','migration.lock'}
    first_install._create_first_fence(app.parent, identity)
    f['repo'].rename(tmp_path/'removed-prepared-checkout')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
    probe = r'''
import json,os,sys,hashlib,sqlite3
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import consumer,first_install,first_use_recovery as recovery,macos_host,cli
from executor.autonomy.runtime_paths import default_runtime
from executor.autonomy.queue import TaskQueue
source=Path(sys.argv[1]);app=source.parent.parent.parent;state=default_runtime(source)
port=int(sys.argv[2]);mode=sys.argv[3]
identity=consumer._bundle_transaction_identity(app);assert identity is not None
record=recovery.prepare_recovery(app,state,identity,source.parent/'native-host',confirm=False)
fence=first_install._fence_path(app.parent);pending=fence.read_bytes();inode=fence.stat().st_ino
read_fd,write_fd=os.pipe();startup=recovery._FirstUseStartup(state,port,record,write_fd)
real_write=os.write;real_complete=first_install._complete_first_fence
def short_write(fd,data):
 return real_write(fd,data[:-3] if os.fstat(fd).st_ino==inode else data)
def interrupt(*a,**k):raise OSError('synthetic prepared completion interruption')
if mode=='short_completion':first_install.os.write=short_write
else:first_install._complete_first_fence=interrupt
failed=False
try:
 try:
  with startup:
   with startup.worker_guard():TaskQueue(state,_first_use_startup=startup)
 except OSError:failed=True
finally:
 first_install.os.write=real_write;first_install._complete_first_fence=real_complete
 startup.__exit__(None,None,None);os.close(read_fd)
assert failed and (state/'.first-use-prepared.json').is_file()
assert first_install._read_first_fence(app.parent)['status']=='pending'
assert fence.read_bytes().startswith(pending) and fence.stat().st_ino==inode
assert not any((state/n).exists() for n in ('service.json','service.log','native-window.json'))
def fingerprint(name):
 p=state/name;s=p.stat();return [s.st_dev,s.st_ino,s.st_mode,hashlib.sha256(p.read_bytes()).hexdigest()]
names=['tasks.sqlite3','task-answers.key','auth.token','.first-use-prepared.json']
before={n:fingerprint(n) for n in names};cancel_fence=fence.read_bytes()
real_prompt=macos_host.present_native_first_use_recovery;choice={'action':'cancel'};prompts=[];children=[]
def prompt(directory,**options):
 assert real_prompt(directory,smoke=True)=={'action':'cancel'}
 prompts.append(True);return dict(choice)
macos_host.present_native_first_use_recovery=prompt
def forbidden_compile(*a,**k):raise AssertionError('prepared recovery required compiler')
consumer._stage_native_host=forbidden_compile
real_popen=recovery.subprocess.Popen
def popen(args,**options):
 child=real_popen(args,**options)
 if 'first-use-serve' in args:children.append(child)
 return child
recovery.subprocess.Popen=popen
try:
 cancelled=first_install.launch_native_entry(state,port,smoke=True)
 assert cancelled.get('cancelled') is True and not children
 assert before=={n:fingerprint(n) for n in names} and fence.read_bytes()==cancel_fence
 choice={'action':'resume_first_use'}
 result=first_install.launch_native_entry(state,port,smoke=True)
 assert result.get('ok') is True and result.get('native_page') is True
 assert len(prompts)==2 and len(children)==1 and children[0].poll() is None
 observed=cli._service_record(state/'service.json')
 assert observed['pid']==children[0].pid and cli.request(state,port,'/v1/service-identity')==observed
 assert cli.request(state,port,'/health')['ok'] is True
 assert first_install._read_first_fence(app.parent)['status']=='complete'
 assert fence.stat().st_ino==inode and fence.read_bytes().startswith(pending)
 # The actual worker may execute empty maintenance SQL; it must retain the
 # original database inode and all sensitive objects exactly.
 assert fingerprint('tasks.sqlite3')[:3]==before['tasks.sqlite3'][:3]
 assert {n:fingerprint(n) for n in names[1:]}=={n:before[n] for n in names[1:]}
 with sqlite3.connect('file:'+str(state/'tasks.sqlite3')+'?mode=ro',uri=True) as db:
  for table in ('tasks','events','commands','run_attempts','field_actions','task_answer_events'):
   assert db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
 reopened=first_install.launch_native_entry(state,port,smoke=True)
 assert reopened.get('ok') is True and reopened.get('native_page') is True
 assert cli._service_record(state/'service.json')==observed and len(children)==1
 print(json.dumps({'installed_runtime':True,'prepared_resume':True,'interruption':mode,
  'cancel_preserved':True,'same_objects':True,'actual_child_ack':True,'native_reopen':True,'tasks_executed':0}))
finally:
 stopped=cli._stop_owned_service(state,port)
 assert stopped.get('ok') is True or stopped.get('reason')=='service_record_missing'
 for child in children:child.wait(timeout=10)
'''
    env = {'HOME':str(f['home']), 'PATH':os.defpath, 'LANG':'en_US.UTF-8',
           'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated', 'BROWSER':'/usr/bin/false',
           'PYTHONHOME':'/nonexistent-synthetic-home', 'PYTHONPATH':'/nonexistent-synthetic-path'}
    result = subprocess.run([str(python),'-I','-B','-c',probe,str(source),str(port),interruption],
                            cwd=f['home'], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, 'installed prepared first-use recovery journey failed'
    assert json.loads(result.stdout) == {'installed_runtime':True, 'prepared_resume':True,
        'interruption':interruption, 'cancel_preserved':True, 'same_objects':True,
        'actual_child_ack':True, 'native_reopen':True, 'tasks_executed':0}
    assert _trusted_bundle(app) and not (f['state']/'service.json').exists()
    assert not list(source.rglob('__pycache__'))


def test_hosted_mac_real_build_prepares_noninstallable_signing_workspace(tmp_path, capsys):
    """Real unsigned build/stager/copy; no signing or prepared-payload execution."""
    if sys.platform != "darwin":
        pytest.skip("Real native unsigned builder requires hosted macOS")
    from scripts import build_macos_app
    from scripts import prepare_macos_signing as signing
    from executor.autonomy.consumer import BUNDLE_ID
    from executor.autonomy.app_distribution import stage_macos_distribution
    distribution = tmp_path / "unsigned-distribution"
    workspace = tmp_path / "signing-workspace"
    assert build_macos_app.main([
        "--standalone-runtime", os.environ["JAE_STANDALONE_RUNTIME"],
        "--output", str(distribution), "--signing-workspace", str(workspace),
        "--publisher-team-id", "SYNTHETIC1", "--publisher-bundle-id", BUNDLE_ID,
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["signing_preparation"]["consumer_admission"] == "DISALLOWED"
    identity = json.loads((workspace / signing.IDENTITY).read_text())
    prepared = workspace / (APP_NAME + ".app")
    assert identity["signed_output_identity"] is None
    assert identity["publisher_validation"] == "NOT_PERFORMED"
    assert (workspace / signing.ORIGINAL_RECEIPT).read_bytes() == (distribution / RECEIPT_NAME).read_bytes()
    # Re-admit the complete original output, not a reconstructed smaller app.
    with stage_macos_distribution(distribution) as original:
        names = {p.relative_to(original).as_posix() for p in original.rglob("*")} | {"."}
        baseline = signing._capture(original, names)
        complete = signing._capture(prepared, names | {signing.BRIDGE})
        assert {path: item for path, item in complete.items() if path != signing.BRIDGE} == baseline
        assert complete == identity["files"]
    assert _trusted_bundle(prepared) is False
    from executor.autonomy.consumer import install_macos_bundle
    result = install_macos_bundle(prepared, platform="darwin",
        destination=tmp_path / "must-not-install", task_state_root=tmp_path / "must-not-initialize")
    assert result["ok"] is False
    assert not (tmp_path / "must-not-install").exists()
    assert not (tmp_path / "must-not-initialize").exists()
