from __future__ import annotations

import json
import ast
import hashlib
import os
import socket
import sqlite3
import uuid
import tempfile
import time
import urllib.error
import urllib.request
import plistlib
import shlex
import shutil
import subprocess
import stat
import sys
from pathlib import Path

from .release import (copy_source_candidate, copy_runtime_candidate,
                      verify_runtime_candidate, verify_source_candidate)


from .standalone_runtime import (STANDALONE_MARKER, copy_standalone_runtime_candidate,
                                 verify_standalone_runtime)
from .process_entry import CLI_ENTRY_SCRIPT


APP_NAME = "AI 投递经理"
BUNDLE_ID = "com.local.job-application-executor.ai-application-manager"

def _legacy_launcher(repo: Path) -> str:
    repo_q = shlex.quote(str(repo))
    return f"""#!/bin/zsh
set -u
REPO_ROOT={repo_q}
PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$HOME/Library/Logs/AI投递经理"
LOG_FILE="$LOG_DIR/launcher.log"
/bin/mkdir -p "$LOG_DIR"

if [[ ! -x "$PYTHON" ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理缺少运行环境。请重新安装本地入口。" buttons {{"好"}} default button "好" with icon caution'
  exit 1
fi

cd "$REPO_ROOT" || exit 1
"$PYTHON" -m executor.autonomy.cli launch >>"$LOG_FILE" 2>&1
STATUS=$?
if [[ $STATUS -ne 0 ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理没有成功就绪。现有任务不会被提交或丢失。请在 ChatGPT 中检查启动状态。" buttons {{"好"}} default button "好" with icon caution'
fi
exit $STATUS
"""


def _legacy_bundle_matches(executable: Path) -> bool:
    try:
        launcher = executable.read_text(encoding="utf-8")
        assignments = [line for line in launcher.splitlines()
                       if line.startswith("REPO_ROOT=")]
        if len(assignments) != 1:
            return False
        quoted = assignments[0].removeprefix("REPO_ROOT=")
        parsed = shlex.split(quoted)
        if len(parsed) != 1 or not Path(parsed[0]).is_absolute():
            return False
        repo = Path(parsed[0])
        return repo.name == "Job-Application-Executor" and (
            shlex.quote(str(repo)) == quoted
            and launcher == _legacy_launcher(repo)
        )
    except (OSError, UnicodeError, ValueError):
        return False


REMEDIATION_MESSAGES = {
    "use_live_browser_mode": "当前不是实时浏览器模式。",
    "install_google_chrome": "没有找到 Google Chrome。",
    "start_dedicated_chrome_cdp": "专用投递浏览器没有准备好。",
    "configure_profile_path": "个人资料尚未配置。",
    "restore_profile_file": "个人资料文件不存在。",
    "repair_profile_file": "个人资料文件无法安全读取。",
    "configure_deepseek_key": "DeepSeek 已加载配置尚不可用。",
    "load_provider_on_user_request": "DeepSeek 配置尚未加载；首次处理你的请求时加载。",
    "start_supervisor": "本地投递服务没有启动。",
}


def humanize_preflight(result: dict) -> str:
    if result.get("ready_for_live_e2e"):
        return "已就绪"
    codes = [
        code for code in result.get("remediation", [])
        if isinstance(code, str)
    ]
    messages = [REMEDIATION_MESSAGES.get(code, "有一项启动检查未通过。") for code in codes]
    if not messages:
        return "启动检查未通过。"
    return " ".join(messages)


def _packaged_launcher_v1(repo: Path) -> str:
    repo_q = shlex.quote(str(repo))

    return f"""#!/bin/zsh
set -u
REPO_ROOT={repo_q}
RELEASE_ROOT="$(cd "$(dirname "$0")/../Resources/release" && pwd -P)"
PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$HOME/Library/Logs/AI投递经理"
LOG_FILE="$LOG_DIR/launcher.log"
/bin/mkdir -p "$LOG_DIR"

if [[ ! -x "$PYTHON" ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理缺少运行环境。请重新安装本地入口。" buttons {{"好"}} default button "好" with icon caution'
  exit 1
fi

cd "$RELEASE_ROOT" || exit 1
export PYTHONPATH="$RELEASE_ROOT"
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B -m executor.autonomy.cli launch >>"$LOG_FILE" 2>&1
STATUS=$?
if [[ $STATUS -ne 0 ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理没有成功就绪。现有任务不会被提交或丢失。请在 ChatGPT 中检查启动状态。" buttons {{"好"}} default button "好" with icon caution'
fi
exit $STATUS
"""


def _packaged_launcher_v2() -> str:
    # Exact historical template: recognition for upgrade, not startup proof.
    return """#!/bin/zsh
set -u
RELEASE_ROOT="$(cd "$(dirname "$0")/../Resources/release" && pwd -P)"
RUNTIME_ROOT="$(cd "$(dirname "$0")/../Resources/runtime" && pwd -P)"
PYTHON="$RUNTIME_ROOT/bin/python"
LOG_DIR="$HOME/Library/Logs/AI投递经理"
LOG_FILE="$LOG_DIR/launcher.log"
/bin/mkdir -p "$LOG_DIR"

if [[ ! -x "$PYTHON" ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理缺少运行环境。请重新安装本地入口。" buttons {"好"} default button "好" with icon caution'
  exit 1
fi

cd "$RELEASE_ROOT" || exit 1
export PYTHONPATH="$RELEASE_ROOT"
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B -m executor.autonomy.cli launch >>"$LOG_FILE" 2>&1
STATUS=$?
if [[ $STATUS -ne 0 ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理没有成功就绪。现有任务不会被提交或丢失。请在 ChatGPT 中检查启动状态。" buttons {"好"} default button "好" with icon caution'
fi
exit $STATUS
"""


def _packaged_launcher() -> str:
    command = (
        '"$PYTHON" -I -B -c ' + shlex.quote(CLI_ENTRY_SCRIPT)
        + ' "$RELEASE_ROOT" launch >>"$LOG_FILE" 2>&1'
    )
    return _packaged_launcher_v2().replace(
        'export PYTHONPATH="$RELEASE_ROOT"\n'
        'export PYTHONDONTWRITEBYTECODE=1\n'
        '"$PYTHON" -B -m executor.autonomy.cli launch >>"$LOG_FILE" 2>&1',
        command,
    )



def _native_packaged_launcher() -> str:
    # Keep the existing isolated owned-Python boundary. URLs/tickets stay stdin
    # on the verified native child; no ambient browser fallback is requested.
    return _packaged_launcher().replace(
        ' "$RELEASE_ROOT" launch >>"$LOG_FILE" 2>&1',
        ' "$RELEASE_ROOT" native-launch "$@" >>"$LOG_FILE" 2>&1')


def _native_bundle_matches(release: Path, directory: Path) -> bool:
    """Bind retained native images to THEIR owned source, including rollback."""
    from .macos_host import verify_native_host

    try:
        if not verify_source_candidate(release):
            return False
        source = release / "executor" / "autonomy" / "macos_host.py"
        if source.is_symlink() or not source.is_file():
            return False
        module = ast.parse(source.read_text(encoding="utf-8"))
        declarations = [node for node in module.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "HOST_SOURCE"
                                for target in node.targets)]
        if len(declarations) != 1:
            return False
        body = ast.literal_eval(declarations[0].value)
        if not isinstance(body, str):
            return False
        return verify_native_host(directory,
            expected_source_sha256=hashlib.sha256(body.encode()).hexdigest())
    except (OSError, UnicodeError, ValueError, TypeError, SyntaxError):
        return False


def _stage_native_host(python: Path, release: Path, target: Path) -> bool:
    # Compile through the verified staged runtime/source, never import a
    # candidate module into the manager/installer process or use a checkout path.
    script = (
        "import pathlib,sys;sys.dont_write_bytecode=True;"
        "sys.path.insert(0,str(pathlib.Path(sys.argv[1]).resolve()));"
        "from executor.autonomy.macos_host import build_native_host;"
        "build_native_host(sys.argv[2])"
    )
    try:
        if not verify_runtime_candidate(python.parent.parent, release):
            return False
        result = subprocess.run([str(python), "-I", "-B", "-c", script,
                                 str(release), str(target)],
            cwd=release, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=75, check=False)
        return result.returncode == 0 and _native_bundle_matches(release, target)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _copy_native_host_candidate(release: Path, source: Path, target: Path) -> bool:
    """Stage a prebuilt window bound to the candidate's verified owned source.

    No compiler, host import or image execution. Hold ordinary source files
    open without following links, recheck identity, and verify staged digests.
    The installer still owns runtime health, task backup and atomic activation.
    """
    from .macos_host import HOST_RECEIPT

    handles = []
    created = accepted = False
    names = ("AIApplicationWindow", HOST_RECEIPT)
    try:
        source, target = Path(source).absolute(), Path(target).absolute()
        if (target.exists() or any(part.is_symlink() for part in (target, *target.parents))
                or not _native_bundle_matches(release, source)):
            return False
        for name in names:
            path = source / name
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            handle = os.fdopen(fd, "rb")
            handles.append((name, handle, os.fstat(handle.fileno())))
            saved = handles[-1][2]
            if not stat.S_ISREG(saved.st_mode) or saved.st_nlink != 1:
                return False
        target.mkdir(mode=0o700)
        stage_stat = target.lstat()
        stage_identity = (stage_stat.st_dev, stage_stat.st_ino)
        created = True
        for name, handle, saved in handles:
            destination = target / name
            with destination.open("xb") as output:
                shutil.copyfileobj(handle, output)
                output.flush()
                os.fsync(output.fileno())
            destination.chmod(0o755 if name == "AIApplicationWindow" else 0o600)
            current, visible = os.fstat(handle.fileno()), (source / name).lstat()
            identity = lambda value: (value.st_dev, value.st_ino, value.st_mode,
                                      value.st_nlink, value.st_size, value.st_mtime_ns,
                                      value.st_ctime_ns)
            if identity(current) != identity(saved) or identity(visible) != identity(saved):
                raise ValueError("native_candidate_changed")
        current_stage = target.lstat()
        if (target.is_symlink() or (current_stage.st_dev, current_stage.st_ino) != stage_identity
                or not _native_bundle_matches(release, source)
                or not _native_bundle_matches(release, target)):
            raise ValueError("native_candidate_invalid")
        accepted = True
        return True
    except (OSError, ValueError, TypeError):
        return False
    finally:
        for _name, handle, _saved in handles:
            handle.close()
        # A refusal removes only the fresh staging directory created here.
        # Never overwrite, unlink or clean a preexisting candidate/target.
        if created and not accepted:
            try:
                current_stage = target.lstat()
                if not target.is_symlink() and (current_stage.st_dev, current_stage.st_ino) == stage_identity:
                    for name in names:
                        (target / name).unlink(missing_ok=True)
                    target.rmdir()
            except OSError:
                pass  # Preserve any unrecognized or concurrently replaced path.


def _isolated_bundle_startup(app: Path) -> bool:
    """Historical owned bundles are upgradeable, never isolated-start certified."""
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    runtime = app / "Contents" / "Resources" / "runtime"
    try:
        launcher = executable.read_text(encoding="utf-8")
        return (runtime.is_dir() and (launcher == _packaged_launcher()
            or (launcher == _native_packaged_launcher()
                and _native_bundle_matches(runtime.parent / "release", runtime.parent / "native-host"))))
    except (OSError, UnicodeError):
        return False


def _packaged_bundle_matches(executable: Path, runtime: Path) -> bool:
    try:
        launcher = executable.read_text(encoding="utf-8")
        if runtime.exists():
            return launcher in {_packaged_launcher(), _packaged_launcher_v2(), _native_packaged_launcher()}
        assignments = [line for line in launcher.splitlines()
                       if line.startswith("REPO_ROOT=")]
        if len(assignments) != 1:
            return False
        quoted = assignments[0].removeprefix("REPO_ROOT=")
        parsed = shlex.split(quoted)
        if len(parsed) != 1 or not Path(parsed[0]).is_absolute():
            return False
        repo = Path(parsed[0])
        return (repo.name == "Job-Application-Executor"
                and shlex.quote(str(repo)) == quoted
                and launcher == _packaged_launcher_v1(repo))
    except (OSError, UnicodeError, ValueError):
        return False


def _trusted_bundle(app: Path) -> bool:
    """Accept only an existing app bundle with our identity and executable."""
    if app.is_symlink() or not app.is_dir():
        return False
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    release = resources / "release"
    runtime = resources / "runtime"
    info_path = contents / "Info.plist"
    executable = macos / "AIApplicationManager"
    if (any(path.is_symlink() for path in (contents, macos, resources, release, runtime))
            or info_path.is_symlink() or executable.is_symlink()
            or not executable.is_file()):
        return False
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
        return (
            isinstance(info, dict)
            and info.get("CFBundleIdentifier") == BUNDLE_ID
            and info.get("CFBundleExecutable") == "AIApplicationManager"
            and bool(executable.stat().st_mode & stat.S_IXUSR)
            and (verify_source_candidate(release)
                 and (verify_runtime_candidate(runtime, release) if runtime.exists() else True)
                 and _packaged_bundle_matches(executable, runtime)
                 and (_native_bundle_matches(release, resources / "native-host")
                      if executable.read_text(encoding="utf-8") == _native_packaged_launcher()
                      else not ((resources / "native-host").exists() or (resources / "native-host").is_symlink()))
             if release.exists() else _legacy_bundle_matches(executable))
        )
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return False



def _bundle_transaction_identity(app: Path) -> tuple | None:
    """Bind verified payloads and the owned bundle inode across transaction waits.

    Manifest integrity alone accepts a different, freshly resealed release.
    Startup and journal compatibility prove only the identity they examined.
    This private in-memory fence is neither signing nor a health certificate.
    """
    def capture():
        if any(path.is_symlink() for path in (app, *app.parents)):
            raise ValueError("bundle_alias")
        metadata = app.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("bundle_invalid")
        contents = app / "Contents"
        resources = contents / "Resources"
        release = resources / "release"
        runtime = resources / "runtime"
        directories = [contents, contents / "MacOS"]
        if resources.exists() or resources.is_symlink():
            directories.append(resources)
        paths = [contents / "Info.plist",
                 contents / "MacOS" / "AIApplicationManager"]
        if release.exists() or release.is_symlink():
            directories.append(release)
            paths.append(release / "release-source-manifest.json")
        if runtime.exists() or runtime.is_symlink():
            directories.append(runtime)
            paths.append(runtime / "release-runtime-manifest.json")
        native = resources / "native-host"
        if native.exists() or native.is_symlink():
            directories.append(native)
            paths.append(native / "native-host-manifest.json")
        if any(path.is_symlink() or not path.is_dir() for path in directories):
            raise ValueError("bundle_directory_invalid")
        evidence = []
        for path in paths:
            entry = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(entry.st_mode):
                raise ValueError("bundle_file_invalid")
            evidence.append((path.relative_to(app).as_posix(),
                             stat.S_IMODE(entry.st_mode),
                             hashlib.sha256(path.read_bytes()).hexdigest()))
        return (metadata.st_dev, metadata.st_ino, tuple(evidence))

    try:
        before = capture()
        if not _trusted_bundle(app) or capture() != before:
            return None
        return before
    except (OSError, UnicodeError, ValueError, TypeError):
        return None



def _bundle_location_matches(app: Path, identity: tuple | None) -> bool:
    """Only move the directory inode already owned by this transaction.

    An unhealthy payload in the same owned directory can be quarantined. A
    replaced/aliased directory is different authority, even if freshly sealed.
    This is a recovery fence, not a healthy-payload or signing certificate.
    """
    if identity is None:
        return False
    try:
        if any(path.is_symlink() for path in (app, *app.parents)):
            return False
        entry = app.stat(follow_symlinks=False)
        return (stat.S_ISDIR(entry.st_mode)
                and (entry.st_dev, entry.st_ino) == identity[:2])
    except (OSError, TypeError):
        return False


def _legacy_state_migration_needed(repo: Path, app: Path, target: Path) -> bool:
    """Do not activate a new default journal while an old authority is stranded.

    Only inspect the finite legacy locations defined by recognized launchers.
    Never copy private files, reinterpret journals or infer an empty database
    from its main file: committed authority may still be in SQLite WAL.
    """
    roots = [(repo, repo / "runtime" / "autonomy")]
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    if executable.is_file() and _legacy_bundle_matches(executable):
        launcher = executable.read_text(encoding="utf-8")
        assignments = [line for line in launcher.splitlines() if line.startswith("REPO_ROOT=")]
        if len(assignments) != 1:
            raise ValueError("legacy_launcher_changed")
        parsed = shlex.split(assignments[0].removeprefix("REPO_ROOT="))
        if len(parsed) != 1:
            raise ValueError("legacy_launcher_changed")
        old_repo = Path(parsed[0])
        if (not old_repo.is_absolute() or old_repo.name != "Job-Application-Executor"
                or launcher != _legacy_launcher(old_repo)):
            raise ValueError("legacy_launcher_changed")
        roots.append((old_repo, old_repo / "runtime" / "autonomy"))
    roots.append((app, app / "Contents" / "Resources" / "release" / "runtime" / "autonomy"))
    for base, root in roots:
        # Compare only after refusing aliases in every legacy path component.
        # A missing parent is ordinary absence; an existing alias is ambiguous.
        components = [root]
        parent = root
        while parent != base:
            parent = parent.parent
            components.append(parent)
        if any(parent.is_symlink() for parent in components):
            raise ValueError("legacy_state_path_invalid")
        if root.resolve() == target.resolve():
            continue
        if not root.exists():
            continue
        if not root.is_dir():
            raise ValueError("legacy_state_path_invalid")
        for child in root.iterdir():
            if child.name in {"worker.lock", "migration.lock", "native-window.lock"}:
                if child.is_symlink() or not child.is_file():
                    raise ValueError("legacy_state_path_invalid")
                continue
            # Any remaining state (including unknown files, keys and service
            # records) requires explicit transactional transfer. No read/log.
            return True
    return False


def _candidate_starts(python: Path, release: Path) -> bool:
    """Require the staged service to answer authenticated loopback health."""
    from .release import source_manifest

    runtime = python.parent.parent
    marker = runtime / STANDALONE_MARKER
    if (marker.exists() or marker.is_symlink()) and not verify_standalone_runtime(runtime, release):
        return False
    expected_digest = source_manifest(release)["source_sha256"]
    script = (
        "import pathlib,runpy,sys;sys.dont_write_bytecode=True;"
        f"root=pathlib.Path({str(release)!r}).resolve();"
        "sys.path.insert(0,str(root));"
        "from executor.autonomy.release import installed_dependencies_match;"
        "assert installed_dependencies_match(root);"
        "sys.argv=['executor.autonomy.cli','--runtime',sys.argv[1],"
        "'--port',sys.argv[2],'serve'];"
        "runpy.run_module('executor.autonomy.cli',run_name='__main__')"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="jae-candidate-health-") as directory:
            runtime = Path(directory)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            env = {**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"}
            process = subprocess.Popen(
                [str(python), "-I", "-B", "-c", script, str(runtime), str(port)],
                cwd=release,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                token_file = runtime / "auth.token"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        return False
                    if token_file.is_file():
                        try:
                            token = token_file.read_text(encoding="utf-8").strip()
                            if token:
                                request = urllib.request.Request(
                                    f"http://127.0.0.1:{port}/health",
                                    headers={"Authorization": f"Bearer {token}"},
                                )
                                with opener.open(request, timeout=0.5) as response:
                                    health = json.load(response)
                                return (
                                    isinstance(health, dict)
                                    and health.get("ok") is True
                                    and health.get("loaded_source_sha256") == expected_digest
                                    and health.get("final_click_actor") == "user"
                                )
                        except (OSError, UnicodeError, ValueError, urllib.error.URLError):
                            pass
                    time.sleep(0.05)
                return False
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False

def _app_transaction_directory(destination: str | Path) -> Path:
    """Refuse an aliased app authority before mkdir, locks or private snapshots."""
    directory = Path(destination).expanduser().absolute()
    if any(part.is_symlink() for part in (directory, *directory.parents)):
        raise ValueError("app_transaction_path_invalid")
    return directory.resolve()


def _acquire_app_transaction_lock(apps_dir: Path) -> int:
    """Keep install and rollback mutually exclusive through final-path health."""
    from .state_compatibility import _private_lock_fd

    apps_dir = _app_transaction_directory(apps_dir)
    apps_dir.mkdir(parents=True, exist_ok=True)
    _app_transaction_directory(apps_dir)
    return _private_lock_fd(apps_dir / f".{APP_NAME}.app.transaction.lock")



def _prepare_task_state_release(python: Path, release: Path, state: Path,
                                apps: Path, evidence: dict) -> bool:
    """Verify the candidate against a durable capsule inside the held fences.

    Empty/new state retains the existing compatibility path. Existing task
    authority gets a private standalone backup before either app bundle moves.
    No journal is restored, no old writer is signalled and no private profile,
    credential, service token or log enters this scoped capsule.
    """
    from .state_compatibility import (
        _stage_task_state_backup_locked, task_state_candidate_compatible,
        task_state_backup_candidate_compatible,
    )
    database = state / "tasks.sqlite3"
    if not database.exists() and not database.is_symlink():
        return task_state_candidate_compatible(python, release, state)
    capsule = apps / (".jae-task-state-backup-" + uuid.uuid4().hex)
    try:
        receipt = _stage_task_state_backup_locked(state, capsule)
        # Retain the original returned receipt outside the capsule's own
        # manifest, including on later refusal/recovery. Never erase a backup
        # to make a failed release look successful.
        evidence.update({"path": str(capsule), "receipt": receipt})
        return task_state_backup_candidate_compatible(python, release, capsule, receipt)
    except (ImportError, OSError, ValueError, TimeoutError, sqlite3.Error):
        return False


def install_macos_bundle(
    candidate_app: str | Path, *,
    destination: str | Path | None = None,
    task_state_root: str | Path | None = None,
    platform: str | None = None,
) -> dict:
    """Feed a verified delivered app through the existing release transaction.

    A native distribution carries its compiled image, owned source and standalone
    Python. It never requires a checkout, Xcode or a second task-state authority.
    Unsigned integrity checks do not confer signing or consumer certification.
    """
    if (platform or sys.platform) != "darwin":
        return {"ok": False, "reason": "macos_required"}
    try:
        candidate = Path(candidate_app).expanduser().absolute()
        if any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise ValueError("bundle_alias")
        release = candidate / "Contents" / "Resources" / "release"
        runtime = candidate / "Contents" / "Resources" / "runtime"
        if not _trusted_bundle(candidate) or not verify_standalone_runtime(runtime, release):
            raise ValueError("bundle_unverified")
        launcher = candidate / "Contents" / "MacOS" / "AIApplicationManager"
        native = launcher.read_text(encoding="utf-8") == _native_packaged_launcher()
        apps = _app_transaction_directory(destination if destination is not None else Path.home() / "Applications")
        if candidate.resolve() == (apps / (APP_NAME + ".app")).resolve():
            return {"ok": False, "reason": "bundle_candidate_is_active",
                    "message": "候选已是当前应用；没有启动更新或修改任务。"}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"ok": False, "reason": "bundle_candidate_invalid",
                "message": "交付应用无法核对；现有应用和任务保持不变。"}
    options = {"native_host": candidate / "Contents" / "Resources" / "native-host"} if native else {}
    return install_macos_app(release, destination=apps, platform=platform,
        task_state_root=task_state_root, standalone_runtime=runtime,
        native_presentation=native, **options)


def install_macos_app(
    repo_root: str | Path,
    *,
    destination: str | Path | None = None,
    platform: str | None = None,
    task_state_root: str | Path | None = None,
    standalone_runtime: str | Path | None = None,
    native_presentation: bool = False,
    native_host: str | Path | None = None,
) -> dict:
    if (platform or sys.platform) != "darwin":
        return _install_macos_app_unlocked(repo_root, destination=destination, platform=platform)
    try:
        apps_dir = _app_transaction_directory(
            destination if destination is not None else Path.home() / "Applications")
        lock_fd = _acquire_app_transaction_lock(apps_dir)
    except BlockingIOError:
        return {"ok": False, "reason": "update_in_progress",
                "message": "应用安装或回退正在进行；没有修改当前应用。"}
    except (OSError, ValueError):
        return {"ok": False, "reason": "update_lock_unavailable",
                "message": "无法安全锁定应用目录；没有修改当前应用。"}
    try:
        from .runtime_paths import default_runtime
        from .state_compatibility import task_state_guard

        state_root = (Path(task_state_root).expanduser() if task_state_root is not None
                      else default_runtime(apps_dir / f"{APP_NAME}.app" / "Contents" / "Resources" / "release"))
        try:
            with task_state_guard(state_root):
                backup_evidence = {}
                result = _install_macos_app_unlocked(
                    repo_root, destination=apps_dir, platform=platform,
                    task_state_root=state_root, standalone_runtime=standalone_runtime,
                    native_presentation=native_presentation, native_host=native_host,
                    _state_backup_evidence=backup_evidence)
                return {**result, "task_state_backup": backup_evidence} if backup_evidence else result
        except BlockingIOError:
            return {"ok": False, "reason": "task_state_in_use",
                    "message": "任务服务仍在使用当前状态；请先在应用中停止服务，当前应用保持不变。"}
        except (OSError, ValueError):
            return {"ok": False, "reason": "task_state_unavailable",
                    "message": "无法安全核对任务状态；当前应用保持不变。"}
    finally:
        os.close(lock_fd)


def _install_macos_app_unlocked(
    repo_root: str | Path,
    *,
    destination: str | Path | None = None,
    platform: str | None = None,
    task_state_root: Path | None = None,
    standalone_runtime: str | Path | None = None,
    native_presentation: bool = False,
    native_host: str | Path | None = None,
    _state_backup_evidence: dict | None = None,
) -> dict:
    """Stage a source-and-runtime snapshot, then atomically activate the Mac app."""
    current_platform = platform or sys.platform
    if current_platform != "darwin":
        return {
            "ok": False,
            "reason": "macos_required",
            "message": "AI 投递经理.app 只在 macOS 上安装。",
        }

    if type(native_presentation) is not bool:
        return {"ok": False, "reason": "presentation_mode_invalid",
                "message": "应用呈现方式无法核对；现有应用保持不变。"}
    if native_host is not None and (not native_presentation or standalone_runtime is None):
        return {"ok": False, "reason": "native_prebuilt_mode_invalid",
                "message": "预编译窗口需要独立运行环境和原生呈现；现有应用保持不变。"}
    repo = Path(repo_root).expanduser().resolve()
    python = repo / ".venv" / "bin" / "python"
    if standalone_runtime is None and not python.is_file():
        return {
            "ok": False,
            "reason": "venv_missing",
            "message": "没有找到项目虚拟环境，请先完成本地依赖安装。",
        }

    apps_dir = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else Path.home() / "Applications"
    )
    apps_dir.mkdir(parents=True, exist_ok=True)
    app = apps_dir / f"{APP_NAME}.app"
    if (app.exists() or app.is_symlink()) and not _trusted_bundle(app):
        return {
            "ok": False,
            "reason": "untrusted_app_path",
            "message": "现有应用包无法核对；没有替换或删除任何应用。",
        }
    current_identity = _bundle_transaction_identity(app) if app.exists() else None
    if app.exists() and current_identity is None:
        return {"ok": False, "reason": "current_identity_unverified",
                "message": "当前应用身份无法核对；没有替换或删除任何应用。"}
    # Source-path health alone cannot prove continuity with a historical
    # repo-local journal. Refuse before staging or candidate startup so the
    # original app and its complete private/WAL state remain the authority.
    try:
        if task_state_root is None or _legacy_state_migration_needed(repo, app, task_state_root):
            return {
                "ok": False,
                "reason": "legacy_state_migration_required",
                "message": "发现旧版任务状态，尚未完成安全迁移；现有应用、任务和已保存答案保持不变。",
            }
    except (OSError, UnicodeError, ValueError):
        return {
            "ok": False,
            "reason": "legacy_state_unavailable",
            "message": "无法安全核对旧版任务状态；现有应用和任务保持不变。",
        }
    staging = apps_dir / f".{APP_NAME}.app.installing"
    try:
        staging.mkdir()
    except FileExistsError:
        return {
            "ok": False,
            "reason": "staging_pending",
            "message": "发现未完成的安装暂存目录；没有删除或替换任何应用。",
        }

    macos = staging / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    release = staging / "Contents" / "Resources" / "release"
    runtime = staging / "Contents" / "Resources" / "runtime"
    try:
        copy_source_candidate(repo, release)
    except (OSError, ValueError):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "source_snapshot_failed",
            "message": "无法准备完整的新版本；现有应用没有被替换。",
        }
    try:
        if standalone_runtime is None:
            copy_runtime_candidate(repo / ".venv", runtime, release)
        else:
            copy_standalone_runtime_candidate(standalone_runtime, runtime, release)
    except (OSError, ValueError):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "runtime_snapshot_failed",
            "message": "无法准备独立运行环境；现有应用没有被替换。",
        }
    executable = macos / "AIApplicationManager"
    host_target = staging / "Contents" / "Resources" / "native-host"
    native_ready = (not native_presentation or (
        _copy_native_host_candidate(release, Path(native_host).expanduser(), host_target)
        if native_host is not None else
        _stage_native_host(runtime / "bin" / "python", release, host_target)))
    if not native_ready:
        shutil.rmtree(staging)
        return {"ok": False, "reason": "native_candidate_failed",
                "message": "原生窗口候选未通过校验；现有应用和任务保持不变。"}
    launcher = _native_packaged_launcher() if native_presentation else _packaged_launcher()

    executable.write_text(launcher, encoding="utf-8")
    executable.chmod(
        executable.stat().st_mode
        | stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH
    )

    info = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": "AIApplicationManager",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
    }
    with (staging / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle, sort_keys=True)

    if (not _trusted_bundle(staging) or not verify_source_candidate(release)
            or not verify_runtime_candidate(runtime, release)):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "candidate_invalid",
            "message": "新应用包未通过本机校验；现有应用没有被替换。",
        }

    candidate_identity = _bundle_transaction_identity(staging)
    if candidate_identity is None:
        return {"ok": False, "reason": "candidate_identity_unverified",
                "message": "候选应用身份无法核对；候选保留供诊断，现有应用没有被替换。"}

    if (not _candidate_starts(runtime / "bin" / "python", release)
            or not verify_source_candidate(release)
            or not verify_runtime_candidate(runtime, release)):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "candidate_start_failed",
            "message": "新版本无法用当前运行环境启动；现有应用没有被替换。",
        }

    rollback = apps_dir / f".{APP_NAME}.app.previous"
    if rollback.exists() or rollback.is_symlink():
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "rollback_pending",
            "message": "旧版应用或回退副本需要先核对；现有应用没有被替换。",
        }
    failed = apps_dir / f".{APP_NAME}.app.failed"
    if failed.exists() or failed.is_symlink():
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "failed_candidate_pending",
            "message": "已有待诊断的失败版本；没有替换或删除任何应用。",
        }
    # Health uses an empty temporary journal. Separately prove the staged
    # queue can initialize a WAL-aware copy of the actual shared journal.
    # The daemon lock remains held until activation/recovery has finished.
    backup_evidence = _state_backup_evidence if _state_backup_evidence is not None else {}
    if (task_state_root is None or not _prepare_task_state_release(
            runtime / "bin" / "python", release, task_state_root, apps_dir, backup_evidence)):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "candidate_state_incompatible",
            "message": "新版本无法完整保留现有任务状态；现有应用和任务保持不变。",
        }

    # Health/compatibility can execute candidate code. Rebind both identities
    # and all occupied slots before the first move; never delete changed input.
    if (_bundle_transaction_identity(staging) != candidate_identity
            or (current_identity is not None
                and _bundle_transaction_identity(app) != current_identity)
            or (current_identity is None and (app.exists() or app.is_symlink()))
            or rollback.exists() or rollback.is_symlink()
            or failed.exists() or failed.is_symlink()):
        return {"ok": False, "reason": "activation_identity_changed",
                "message": "检查期间应用身份或位置发生变化；没有启用候选，所有版本保留供核对。"}

    replaced = current_identity is not None
    try:
        if replaced:
            app.rename(rollback)
        staging.rename(app)
    except OSError:
        # A filesystem error does not preserve the authority observed BEFORE
        # rename. Re-admit retained input and the entire recovery destination;
        # never overwrite an empty foreign directory/dangling link or delete
        # a changed staging candidate while handling an activation failure.
        absent = not app.exists() and not app.is_symlink()
        retained_absent = not rollback.exists() and not rollback.is_symlink()
        unchanged_staging = _bundle_transaction_identity(staging) == candidate_identity
        unmoved = (replaced and retained_absent
                   and _bundle_transaction_identity(app) == current_identity)
        moved = (replaced and absent
                 and _bundle_transaction_identity(rollback) == current_identity)
        safe = (unchanged_staging and not failed.exists() and not failed.is_symlink()
                and ((unmoved or moved) if replaced else (absent and retained_absent)))
        if not safe:
            return {
                "ok": False, "reason": "rollback_required",
                "message": "启用失败时版本身份或恢复位置发生变化；所有版本保留当前位置，需要核对恢复。",
            }
        restored = not replaced or unmoved
        if moved:
            try:
                rollback.rename(app)
                restored = _bundle_transaction_identity(app) == current_identity
            except OSError:
                restored = False
        # Recovery failure preserves the candidate for diagnosis as well.
        if restored and _bundle_transaction_identity(staging) == candidate_identity:
            shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "activation_failed" if restored else "rollback_required",
            "rollback_path": str(rollback) if rollback.exists() else None,
            "message": (
                "新版本未启用，原应用保持可用。"
                if restored else "新版本未启用；所有版本已保留，需要核对恢复。"
            ),
        }
    # Recheck at the final bundle path: moving a virtualenv can invalidate
    # interpreter/framework references even when staging health succeeded.
    active_release = app / "Contents" / "Resources" / "release"
    active_runtime = app / "Contents" / "Resources" / "runtime"
    if (_bundle_transaction_identity(app) != candidate_identity
            or not _candidate_starts(active_runtime / "bin" / "python", active_release)
            or _bundle_transaction_identity(app) != candidate_identity):
        # Health may have yielded to another actor. Never overwrite a newly
        # occupied slot, move an unowned app inode, or activate changed retained
        # payloads. Preserve every version for recovery without further moves.
        if (not _bundle_location_matches(app, candidate_identity)
                or failed.exists() or failed.is_symlink()
                or (replaced and _bundle_transaction_identity(rollback) != current_identity)
                or (not replaced and (rollback.exists() or rollback.is_symlink()))):
            return {
                "ok": False,
                "reason": "post_activation_recovery_required",
                "message": "健康检查期间版本身份或恢复位置发生变化；所有版本保持当前位置，需要核对恢复。",
            }
        try:
            app.rename(failed)
            if replaced:
                rollback.rename(app)
        except OSError:
            if (not app.exists() and not app.is_symlink()
                    and _bundle_location_matches(failed, candidate_identity)):
                try:
                    failed.rename(app)
                except OSError:
                    pass
            return {
                "ok": False,
                "reason": "post_activation_recovery_required",
                "rollback_path": str(rollback) if rollback.exists() else None,
                "message": "启用后健康检查未通过，两个版本已保留，需要人工核对恢复。",
            }
        if replaced:
            restored_release = app / "Contents" / "Resources" / "release"
            restored_runtime = app / "Contents" / "Resources" / "runtime"
            if (_bundle_transaction_identity(app) != current_identity
                    or not _isolated_bundle_startup(app)
                    or (restored_runtime.exists() and not _candidate_starts(
                        restored_runtime / "bin" / "python", restored_release))
                    or _bundle_transaction_identity(app) != current_identity):
                return {
                    "ok": False,
                    "reason": "post_activation_recovery_required",
                    "failed_candidate_path": str(failed),
                    "message": "新版本启动失败，旧版已恢复但尚未证明安全启动；两个版本已保留，需要人工核对恢复。",
                }
        return {
            "ok": False,
            "reason": "post_activation_unhealthy",
            "rollback_path": None,
            "failed_candidate_path": str(failed),
            "message": ("新版本启用后未通过健康检查；已恢复上一版本。"
                        if replaced else "新版本启用后未通过健康检查；失败候选已保留供诊断。"),
        }
    # A healthy active candidate does not prove that its known-good retained
    # release or recovery slot stayed unchanged while startup yielded.
    if ((replaced and _bundle_transaction_identity(rollback) != current_identity)
            or (not replaced and (rollback.exists() or rollback.is_symlink()))
            or failed.exists() or failed.is_symlink()):
        return {
            "ok": False,
            "reason": "post_activation_recovery_required",
            "message": "候选可启动，但保留版本或恢复位置发生变化；所有版本保留当前位置，更新尚未确认完成。",
        }
    return {
        "ok": True,
        "installed": True,
        **({"presentation": "native"} if native_presentation else {}),
        "replaced": replaced,
        "app_path": str(app),
        "rollback_path": str(rollback) if replaced else None,
        "message": "AI 投递经理已安装。以后直接双击应用即可。",
    }


def rollback_macos_app(destination: str | Path, *, task_state_root: str | Path | None = None) -> dict:
    try:
        apps_dir = _app_transaction_directory(destination)
        if not apps_dir.is_dir():
            return {"ok": False, "reason": "rollback_unavailable",
                    "message": "回退副本不完整或路径已被占用；没有修改当前应用。"}
        lock_fd = _acquire_app_transaction_lock(apps_dir)
    except BlockingIOError:
        return {"ok": False, "reason": "update_in_progress",
                "message": "应用安装或回退正在进行；没有修改当前应用。"}
    except (OSError, ValueError):
        return {"ok": False, "reason": "update_lock_unavailable",
                "message": "无法安全锁定应用目录；没有修改当前应用。"}
    try:
        from .runtime_paths import default_runtime
        from .state_compatibility import task_state_guard

        state_root = (Path(task_state_root).expanduser() if task_state_root is not None
                      else default_runtime(apps_dir / f"{APP_NAME}.app" / "Contents" / "Resources" / "release"))
        try:
            with task_state_guard(state_root):
                backup_evidence = {}
                result = _rollback_macos_app_unlocked(
                    apps_dir, task_state_root=state_root,
                    _state_backup_evidence=backup_evidence)
                return {**result, "task_state_backup": backup_evidence} if backup_evidence else result
        except BlockingIOError:
            return {"ok": False, "reason": "task_state_in_use",
                    "message": "任务服务仍在使用当前状态；请先在应用中停止服务，当前应用保持不变。"}
        except (OSError, ValueError):
            return {"ok": False, "reason": "task_state_unavailable",
                    "message": "无法安全核对任务状态；当前应用和回退副本保持不变。"}
    finally:
        os.close(lock_fd)


def _rollback_macos_app_unlocked(destination: str | Path, *, task_state_root: Path,
                                 _state_backup_evidence: dict | None = None) -> dict:
    """Restore the retained app without deleting the failed candidate."""
    apps_dir = Path(destination).expanduser().resolve()
    app = apps_dir / f"{APP_NAME}.app"
    previous = apps_dir / f".{APP_NAME}.app.previous"
    failed = apps_dir / f".{APP_NAME}.app.failed"
    if (not _trusted_bundle(app) or not _trusted_bundle(previous)
            or failed.exists() or failed.is_symlink()):
        return {
            "ok": False,
            "reason": "rollback_unavailable",
            "message": "回退副本不完整或路径已被占用；没有修改当前应用。",
        }
    # A pre-packaged legacy launcher depends on the mutable checkout and may
    # write task state with an older schema. Its identity alone cannot prove a
    # safe rollback after the packaged app has run. Preserve both bundles.
    previous_release = previous / "Contents" / "Resources" / "release"
    previous_runtime = previous / "Contents" / "Resources" / "runtime"
    if not previous_runtime.is_dir():
        return {
            "ok": False,
            "reason": "legacy_rollback_unsupported",
            "message": "旧版应用缺少独立运行环境，无法证明任务状态兼容；当前应用保持不变。",
        }
    current_identity = _bundle_transaction_identity(app)
    previous_identity = _bundle_transaction_identity(previous)
    if current_identity is None or previous_identity is None:
        return {"ok": False, "reason": "rollback_identity_unverified",
                "message": "应用与回退副本身份无法核对；两个版本保持原位。"}
    if not _isolated_bundle_startup(previous):
        return {
            "ok": False,
            "reason": "rollback_startup_isolation_unsupported",
            "message": "旧版入口无法证明隔离启动；当前应用和回退副本保持不变。",
        }
    if not _candidate_starts(
        previous_runtime / "bin" / "python", previous_release
    ):
        return {
            "ok": False,
            "reason": "rollback_unhealthy",
            "message": "回退副本无法启动；当前应用保持不变。",
        }
    backup_evidence = _state_backup_evidence if _state_backup_evidence is not None else {}
    if not _prepare_task_state_release(
        previous_runtime / "bin" / "python", previous_release, task_state_root,
        apps_dir, backup_evidence
    ):
        return {
            "ok": False,
            "reason": "rollback_state_incompatible",
            "message": "旧版不能安全保留当前任务记录；当前应用和回退副本保持不变。",
        }
    if (_bundle_transaction_identity(app) != current_identity
            or _bundle_transaction_identity(previous) != previous_identity
            or failed.exists() or failed.is_symlink()):
        return {"ok": False, "reason": "rollback_identity_changed",
                "message": "检查期间应用或回退副本发生变化；没有移动任何版本。"}
    try:
        app.rename(failed)
    except OSError:
        return {
            "ok": False,
            "reason": "rollback_start_failed",
            "message": "无法保存当前版本；没有修改当前应用。",
        }
    try:
        previous.rename(app)
    except OSError:
        if (app.exists() or app.is_symlink()
                or _bundle_transaction_identity(failed) != current_identity
                or _bundle_transaction_identity(previous) != previous_identity):
            return {
                "ok": False, "reason": "manual_recovery_required",
                "message": "回退启用失败时版本身份或恢复位置发生变化；所有版本保留当前位置，需要核对恢复。",
            }
        try:
            failed.rename(app)
            if _bundle_transaction_identity(app) != current_identity:
                raise OSError("recovery_identity_changed")
            reason = "rollback_activation_failed"
            message = "旧版没有启用，当前版本已恢复。"
        except OSError:
            reason = "manual_recovery_required"
            message = "两个版本仍保留在原位置与失败位置，需要人工恢复。"
        return {"ok": False, "reason": reason, "message": message}
    # The final bundle path can change virtualenv behavior. Restore the newer
    # app if the old one stops responding after activation.
    active_release = app / "Contents" / "Resources" / "release"
    active_runtime = app / "Contents" / "Resources" / "runtime"
    if (_bundle_transaction_identity(app) != previous_identity
            or not _candidate_starts(active_runtime / "bin" / "python", active_release)
            or _bundle_transaction_identity(app) != previous_identity):
        if (not _bundle_location_matches(app, previous_identity)
                or previous.exists() or previous.is_symlink()
                or _bundle_transaction_identity(failed) != current_identity):
            return {
                "ok": False,
                "reason": "rollback_recovery_required",
                "message": "健康检查期间版本身份或恢复位置发生变化；所有版本保持当前位置，需要核对恢复。",
            }
        try:
            app.rename(previous)
            failed.rename(app)
        except OSError:
            return {
                "ok": False,
                "reason": "rollback_recovery_required",
                "message": "回退版本启用后无法启动；两个版本已保留，需要人工核对恢复。",
            }
        if _bundle_transaction_identity(app) != current_identity:
            return {"ok": False, "reason": "rollback_recovery_required",
                    "message": "回退失败后原应用身份无法核对；两个版本保留，需要人工核对恢复。"}
        return {
            "ok": False,
            "reason": "rollback_post_activation_unhealthy",
            "message": "回退版本启用后无法启动；已恢复原应用。",
        }
    # Final-path health is only one part of transactional completion. Retain
    # the exact displaced current version and refuse a newly occupied slot.
    if (_bundle_transaction_identity(failed) != current_identity
            or previous.exists() or previous.is_symlink()):
        return {
            "ok": False,
            "reason": "rollback_recovery_required",
            "message": "回退版本可启动，但保留版本或恢复位置发生变化；所有版本保留当前位置，回退尚未确认完成。",
        }
    return {
        "ok": True,
        "restored": True,
        "app_path": str(app),
        "failed_candidate_path": str(failed),
        "message": "已恢复上一版本。失败的候选版本保留供诊断。",
    }
