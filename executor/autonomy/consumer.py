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
# Increase for each subsequently adopted source release. Equal sequence with
# different bytes is intentionally not an ordinary installer update.
RELEASE_SEQUENCE = 9


def _owned_bundle_file(path: Path) -> bool:
    """Refuse aliases before reading an installed bundle control file."""
    try:
        entry = path.stat(follow_symlinks=False)
        return (stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1
                and entry.st_uid == os.geteuid())
    except OSError:
        return False


def _owned_bundle_bytes(path: Path) -> bytes:
    """Bind a no-follow descriptor to one owned inode through its read."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()):
            raise ValueError("bundle_file_invalid")
        observed = path.stat(follow_symlinks=False)
        if (observed.st_dev, observed.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("bundle_file_changed")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read()
        after = os.fstat(fd)
        current = path.stat(follow_symlinks=False)
        if ((after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                or after.st_nlink != 1 or after.st_uid != os.geteuid()
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("bundle_file_changed")
        return content
    finally:
        os.close(fd)


def _owned_bundle_text(path: Path) -> str:
    return _owned_bundle_bytes(path).decode("utf-8")


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
        if not _owned_bundle_file(executable):
            return False
        launcher = _owned_bundle_text(executable)
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



def _native_packaged_launcher_v1() -> str:
    # Keep the existing isolated owned-Python boundary. URLs/tickets stay stdin
    # on the verified native child; no ambient browser fallback is requested.
    return _packaged_launcher().replace(
        ' "$RELEASE_ROOT" launch >>"$LOG_FILE" 2>&1',
        ' "$RELEASE_ROOT" native-launch "$@" >>"$LOG_FILE" 2>&1')


def _native_packaged_launcher_v2() -> str:
    return _native_packaged_launcher_v1().replace(
        ' "$RELEASE_ROOT" native-launch "$@"',
        ' "$RELEASE_ROOT" native-entry "$@"').replace(
        'if [[ $STATUS -ne 0 ]]; then',
        'if [[ $STATUS -ne 0 && $STATUS -ne 2 ]]; then').replace(
        'AI 投递经理没有成功就绪。现有任务不会被提交或丢失。请在 ChatGPT 中检查启动状态。',
        'AI 投递经理尚未确认安装或打开。请保留原应用和任务，重新打开应用查看本机说明；不要删除旧文件或强行覆盖。')


def _native_packaged_launcher() -> str:
    # Run fixed Apple platform checks before an architecture-specific runtime,
    # log directory or task authority is touched. Retained v1/v2 bytes stay valid.
    preflight = """if [[ "$(/usr/bin/uname -m)" != "arm64" ]]; then
  /usr/bin/osascript -e 'display dialog "此安装包适用于 Apple Silicon（M 系列）Mac，不能用于 Intel Mac。没有安装或修改任务。" buttons {"关闭"} default button "关闭" with icon caution'
  exit 2
fi
OS_VERSION="$(/usr/bin/sw_vers -productVersion)"
OS_MAJOR="${OS_VERSION%%.*}"
if [[ "$OS_MAJOR" != <-> || "$OS_MAJOR" -lt 13 ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理需要 macOS 13 或更新版本。没有安装或修改任务。" buttons {"关闭"} default button "关闭" with icon caution'
  exit 2
fi
"""
    return _native_packaged_launcher_v2().replace('set -u\n', 'set -u\n' + preflight, 1)


def _is_native_packaged_launcher(value: str) -> bool:
    # Retained versions keep their exact historical source-bound launcher.
    return type(value) is str and value in {_native_packaged_launcher_v1(), _native_packaged_launcher_v2(), _native_packaged_launcher()}


def _native_bundle_matches(release: Path, directory: Path, *, required_publisher_policy=None) -> bool:
    """Bind retained native images to THEIR owned source, including rollback."""
    from .macos_host import verify_native_host

    try:
        if not verify_source_candidate(release):
            return False
        source = release / "executor" / "autonomy" / "macos_host.py"
        if not _owned_bundle_file(source):
            return False
        module = ast.parse(_owned_bundle_text(source))
        declarations = [node for node in module.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "HOST_SOURCE"
                                for target in node.targets)]
        if len(declarations) != 1:
            return False
        body = ast.literal_eval(declarations[0].value)
        if not isinstance(body, str):
            return False
        options = {} if required_publisher_policy is None else {'required_publisher_policy': required_publisher_policy}
        return verify_native_host(directory,
            expected_source_sha256=hashlib.sha256(body.encode()).hexdigest(), **options)
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
        if not _owned_bundle_file(executable):
            return False
        launcher = _owned_bundle_text(executable)
        return (runtime.is_dir() and (launcher == _packaged_launcher()
            or (_is_native_packaged_launcher(launcher)
                and _native_bundle_matches(runtime.parent / "release", runtime.parent / "native-host"))))
    except (OSError, UnicodeError):
        return False


def _packaged_bundle_matches(executable: Path, runtime: Path) -> bool:
    try:
        if not _owned_bundle_file(executable):
            return False
        launcher = _owned_bundle_text(executable)
        if runtime.exists():
            return (launcher in {_packaged_launcher(), _packaged_launcher_v2()}
                    or _is_native_packaged_launcher(launcher))
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


def _trusted_bundle(app: Path, *, required_publisher_policy=None) -> bool:
    """Accept only an existing app bundle with our identity and executable."""
    if app.is_symlink() or not app.is_dir():
        return False
    from .signed_payload import has_current_payload
    if required_publisher_policy is not None and not has_current_payload(app):
        return False
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    release = resources / "release"
    runtime = resources / "runtime"
    if has_current_payload(app) and (not release.is_dir() or not runtime.is_dir()):
        return False
    info_path = contents / "Info.plist"
    executable = macos / "AIApplicationManager"
    options = {} if required_publisher_policy is None else {'required_publisher_policy': required_publisher_policy}
    if (any(path.is_symlink() for path in (contents, macos, resources, release, runtime))
            or not _owned_bundle_file(info_path)
            or not _owned_bundle_file(executable)):
        return False
    try:
        info = plistlib.loads(_owned_bundle_bytes(info_path))
        return (
            isinstance(info, dict)
            and info.get("CFBundleIdentifier") == BUNDLE_ID
            and info.get("CFBundleExecutable") == "AIApplicationManager"
            and bool(executable.stat().st_mode & stat.S_IXUSR)
            and (verify_source_candidate(release)
                 and (verify_runtime_candidate(runtime, release, **options) if runtime.exists() else True)
                 and _packaged_bundle_matches(executable, runtime)
                 and (_native_bundle_matches(release, resources / "native-host", **options)
                      if _is_native_packaged_launcher(_owned_bundle_text(executable))
                      else not ((resources / "native-host").exists() or (resources / "native-host").is_symlink()))
             if release.exists() else _legacy_bundle_matches(executable))
        )
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return False



def _bundle_transaction_identity(app: Path, *, required_publisher_policy=None) -> tuple | None:
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
        from .signed_payload import has_current_payload, RELATIVE_PATH
        if has_current_payload(app):
            paths.append(app / RELATIVE_PATH)
        if any(path.is_symlink() or not path.is_dir() for path in directories):
            raise ValueError("bundle_directory_invalid")
        evidence = []
        for path in paths:
            entry = path.stat(follow_symlinks=False)
            if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
                    or entry.st_uid != os.geteuid()):
                raise ValueError("bundle_file_invalid")
            evidence.append((path.relative_to(app).as_posix(),
                             stat.S_IMODE(entry.st_mode),
                             hashlib.sha256(_owned_bundle_bytes(path)).hexdigest()))
        return (metadata.st_dev, metadata.st_ino, tuple(evidence))

    try:
        before = capture()
        options = {} if required_publisher_policy is None else {'required_publisher_policy': required_publisher_policy}
        if not _trusted_bundle(app, **options) or capture() != before:
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
    if _owned_bundle_file(executable) and _legacy_bundle_matches(executable):
        if not _owned_bundle_file(executable):
            raise ValueError("legacy_launcher_changed")
        launcher = _owned_bundle_text(executable)
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
    return any(_legacy_root_has_state(base, root, target) for base, root in roots)


def _legacy_root_has_state(base: Path, root: Path, target: Path | None = None) -> bool:
    # Compare only after refusing aliases in every legacy path component.
    # A missing parent is ordinary absence; an existing alias is ambiguous.
    components = [root]
    parent = root
    while parent != base:
        parent = parent.parent
        components.append(parent)
    # The historical launcher supplies base; an alias above it can move
    # the entire old authority even when runtime/autonomy itself is plain.
    # Refuse before resolve() or scanning lock-only state as empty.
    if any(parent.is_symlink() for parent in (*components, *base.parents)):
        raise ValueError("legacy_state_path_invalid")
    if target is not None and root.resolve() == target.resolve():
        return False
    if not root.exists():
        return False
    if not root.is_dir():
        raise ValueError("legacy_state_path_invalid")
    for child in root.iterdir():
        if child.name in {"worker.lock", "migration.lock", "native-window.lock"}:
            # Only an owned ordinary single-link inode can be ignored as
            # empty legacy lock state. An alias must not certify an empty
            # authority without ever reading its payload.
            metadata = child.stat(follow_symlinks=False)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()):
                raise ValueError("legacy_state_path_invalid")
            continue
        # Any remaining state (including unknown files, keys and service
        # records) requires explicit transactional transfer. No read/log.
        return True
    return False


def _candidate_starts(python: Path, release: Path, *, required_publisher_policy=None) -> bool:
    """Require the staged service to answer authenticated loopback health."""
    from .release import source_manifest

    runtime = python.parent.parent
    from .signed_payload import app_for_runtime, has_current_payload
    from .publisher_policy import resolve_policy
    app = app_for_runtime(runtime, release)
    if app is not None and has_current_payload(app):
        try:
            required_publisher_policy = resolve_policy(required_publisher_policy)
        except ValueError:
            return False
        if not _trusted_bundle(app, required_publisher_policy=required_publisher_policy):
            return False
    elif required_publisher_policy is not None:
        return False
    options = {} if required_publisher_policy is None else {'required_publisher_policy': required_publisher_policy}
    marker = runtime / STANDALONE_MARKER
    if (marker.exists() or marker.is_symlink()) and not verify_standalone_runtime(runtime, release, **options):
        return False
    expected_digest = source_manifest(release)["source_sha256"]
    script = (
        "import pathlib,runpy,sys;sys.dont_write_bytecode=True;"
        f"root=pathlib.Path({str(release)!r}).resolve();"
        "sys.path.insert(0,str(root));"
        "from executor.autonomy.release import installed_dependencies_match;"
        "import json;"
        "policy=json.loads(sys.argv[3]) if len(sys.argv)>3 else None;"
        "assert (installed_dependencies_match(root) if policy is None else "
        "installed_dependencies_match(root,required_publisher_policy=policy));"
        "sys.argv=['executor.autonomy.cli','--runtime',sys.argv[1],"
        "'--port',sys.argv[2],'serve'];"
        "runpy.run_module('executor.autonomy.cli',run_name='__main__')"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="jae-candidate-health-") as directory:
            # This is our newly created, disposable health journal, not a
            # caller's task authority. macOS temp paths may traverse /var's
            # alias; pass its existing canonical location to strict serve.
            runtime = Path(directory).resolve(strict=True)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            env = {**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"}
            process = subprocess.Popen(
                [str(python), "-I", "-B", "-c", script, str(runtime), str(port)]
                + ([json.dumps(required_publisher_policy, sort_keys=True)] if required_publisher_policy is not None else []),
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


def _activate_first_install_exclusive(staging: Path, app: Path) -> None:
    """Darwin no-replace rename anchored to one admitted application directory."""
    import ctypes
    import errno
    if sys.platform != "darwin" or staging.parent != app.parent:
        raise OSError(errno.ENOTSUP, "first_install_exclusive_rename_unavailable")
    parent = _app_transaction_directory(app.parent)
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        observed, visible = os.fstat(fd), parent.stat(follow_symlinks=False)
        if (not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.geteuid()
                or (observed.st_dev, observed.st_ino) != (visible.st_dev, visible.st_ino)):
            raise OSError(errno.EINVAL, "first_install_parent_changed")
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        operation = library.renameatx_np
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        # Apple's sys/stdio.h: RENAME_EXCL=0x4. Unsupported filesystems refuse.
        if operation(fd, os.fsencode(staging.name), fd, os.fsencode(app.name), 0x4) != 0:
            raise OSError(ctypes.get_errno(), "first_install_exclusive_rename_refused")
    except AttributeError:
        raise OSError(errno.ENOTSUP, "first_install_exclusive_rename_unavailable") from None
    finally:
        os.close(fd)


def _approved_first_install_matches(approved, staging: Path | None = None) -> bool:
    if approved is None:
        return True
    candidate, identity = approved
    if _bundle_transaction_identity(candidate) != identity:
        return False
    if staging is None:
        return True
    staged = _bundle_transaction_identity(staging)
    if staged is None:
        return False
    # Staging may normalize control-file modes/plist formatting, but all
    # approved source/runtime/native manifests and launcher bytes stay exact.
    bound = lambda value: {name: digest for name, mode, digest in value[2]
                           if name != "Contents/Info.plist"}
    return bound(staged) == bound(identity)


def install_macos_bundle(
    candidate_app: str | Path, *,
    destination: str | Path | None = None,
    task_state_root: str | Path | None = None,
    platform: str | None = None,
    _first_install: bool = False,
    _legacy_additional_origins: tuple[Path, ...] = (),
    _approved_identity: tuple | None = None,
    _preserve_bundle: bool = False,
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
        if type(_preserve_bundle) is not bool:
            raise ValueError("bundle_mode_invalid")
        approved = (candidate, _approved_identity) if _first_install or _preserve_bundle else None
        if (_first_install or _preserve_bundle) and (type(_approved_identity) is not tuple or not _approved_first_install_matches(approved)):
            return {"ok": False, "reason": "first_install_source_changed"}
        release = candidate / "Contents" / "Resources" / "release"
        runtime = candidate / "Contents" / "Resources" / "runtime"
        if not _trusted_bundle(candidate) or not verify_standalone_runtime(runtime, release):
            raise ValueError("bundle_unverified")
        launcher = candidate / "Contents" / "MacOS" / "AIApplicationManager"
        if not _owned_bundle_file(launcher):
            raise ValueError("bundle_unverified")
        native = _is_native_packaged_launcher(_owned_bundle_text(launcher))
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
        native_presentation=native, _approved_bundle=approved,
        _bundle_payload=approved if _preserve_bundle else None,
        _native_launcher=_owned_bundle_text(launcher) if native else None,
        _legacy_origin=Path(__file__).absolute().parents[2],
        _first_install=_first_install, _legacy_additional_origins=_legacy_additional_origins, **options)


def install_macos_app(
    repo_root: str | Path,
    *,
    destination: str | Path | None = None,
    platform: str | None = None,
    task_state_root: str | Path | None = None,
    standalone_runtime: str | Path | None = None,
    native_presentation: bool = False,
    native_host: str | Path | None = None,
    _legacy_origin: Path | None = None,
    _first_install: bool = False,
    _legacy_additional_origins: tuple[Path, ...] = (),
    _native_launcher: str | None = None,
    _approved_bundle: tuple | None = None,
    _bundle_payload: tuple | None = None,
) -> dict:
    if (platform or sys.platform) != "darwin":
        return _install_macos_app_unlocked(repo_root, destination=destination, platform=platform)
    if (type(_first_install) is not bool or type(_legacy_additional_origins) is not tuple
            or len(_legacy_additional_origins) > 2
            or any(not isinstance(p, Path) or not p.is_absolute() for p in _legacy_additional_origins)
            or _native_launcher is not None and (not native_presentation or not _is_native_packaged_launcher(_native_launcher))):
        return {"ok": False, "reason": "first_install_intent_invalid"}
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
            if _first_install:
                from .first_install import _read_first_fence
                if _read_first_fence(apps_dir) is not None:
                    return {"ok": False, "reason": "first_install_continuity_unconfirmed"}
                target_app = apps_dir / f"{APP_NAME}.app"
                if target_app.exists() or target_app.is_symlink():
                    return {"ok": False, "reason": "first_install_target_occupied"}
                if _legacy_root_has_state(state_root, state_root):
                    return {"ok": False, "reason": "first_install_state_continuity_required"}
            with task_state_guard(state_root):
                backup_evidence = {}
                result = _install_macos_app_unlocked(
                    repo_root, destination=apps_dir, platform=platform,
                    task_state_root=state_root, standalone_runtime=standalone_runtime,
                    native_presentation=native_presentation, native_host=native_host,
                    _state_backup_evidence=backup_evidence, _legacy_origin=_legacy_origin,
                    _first_install=_first_install, _legacy_additional_origins=_legacy_additional_origins,
                    _native_launcher=_native_launcher, _approved_bundle=_approved_bundle,
                    _bundle_payload=_bundle_payload)
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
    _legacy_origin: Path | None = None,
    _first_install: bool = False,
    _legacy_additional_origins: tuple[Path, ...] = (),
    _native_launcher: str | None = None,
    _approved_bundle: tuple | None = None,
    _bundle_payload: tuple | None = None,
    _installer_guard=None,
    _expected_current_identity: tuple | None = None,
) -> dict:
    """Stage a source-and-runtime snapshot, then atomically activate the Mac app."""
    if _installer_guard is not None:
        from .consumer_installer import _UpdateAuthority
        if type(_installer_guard) is not _UpdateAuthority:
            raise ValueError("installer_authority_invalid")
        _installer_guard.verify()
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
    if _first_install and (app.exists() or app.is_symlink()):
        return {"ok": False, "reason": "first_install_target_occupied"}
    if _first_install and (task_state_root is None or _legacy_root_has_state(task_state_root, task_state_root)):
        return {"ok": False, "reason": "first_install_state_continuity_required"}
    if (app.exists() or app.is_symlink()) and not _trusted_bundle(app):
        return {
            "ok": False,
            "reason": "untrusted_app_path",
            "message": "现有应用包无法核对；没有替换或删除任何应用。",
        }
    current_identity = _bundle_transaction_identity(app) if app.exists() else None
    if _expected_current_identity is not None and current_identity != _expected_current_identity:
        return {"ok": False, "reason": "current_identity_unverified"}
    if app.exists() and current_identity is None:
        return {"ok": False, "reason": "current_identity_unverified",
                "message": "当前应用身份无法核对；没有替换或删除任何应用。"}
    # Source-path health alone cannot prove continuity with a historical
    # repo-local journal. Refuse before staging or candidate startup so the
    # original app and its complete private/WAL state remain the authority.
    # Delivered intake replaces repo with staged payload. Retain the trusted
    # invoking source separately; pure builds do not pass this origin.
    legacy_sources = ((repo,) if _legacy_origin is None else (repo, _legacy_origin)) + _legacy_additional_origins
    try:
        if task_state_root is None or any(
                _legacy_state_migration_needed(source, app, task_state_root)
                for source in legacy_sources):
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
    if not _approved_first_install_matches(_approved_bundle):
        return {"ok": False, "reason": "first_install_source_changed"}
    staging = apps_dir / f".{APP_NAME}.app.installing"
    try:
        staging.mkdir()
    except FileExistsError:
        return {
            "ok": False,
            "reason": "staging_pending",
            "message": "发现未完成的安装暂存目录；没有删除或替换任何应用。",
        }

    if _bundle_payload is not None:
        from .bundle_copy import copy_bundle_payload
        try:
            source_app, source_identity = _bundle_payload
            if (source_app / "Contents/Resources/release" != repo
                    or _approved_bundle != _bundle_payload):
                raise ValueError("installer_payload_invalid")
            copy_bundle_payload(source_app, staging, source_identity)
        except (OSError, ValueError, TypeError):
            return {"ok": False, "reason": "bundle_snapshot_failed",
                    "message": "安装文件核对未完成；原应用未替换，暂存内容已保留。"}
        release = staging / "Contents/Resources/release"
        runtime = staging / "Contents/Resources/runtime"
    else:
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
        launcher = (_native_launcher or _native_packaged_launcher()) if native_presentation else _packaged_launcher()

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
            "CFBundleVersion": str(RELEASE_SEQUENCE),
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

    if not _approved_first_install_matches(_approved_bundle, staging):
        return {"ok": False, "reason": "first_install_source_changed"}
    candidate_identity = _bundle_transaction_identity(staging)
    if candidate_identity is None:
        return {"ok": False, "reason": "candidate_identity_unverified",
                "message": "候选应用身份无法核对；候选保留供诊断，现有应用没有被替换。"}

    if _installer_guard is not None:
        _installer_guard.verify()
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
    if _first_install and (task_state_root is None or _legacy_root_has_state(task_state_root, task_state_root)):
        return {"ok": False, "reason": "first_install_state_continuity_required"}
    if _installer_guard is not None:
        _installer_guard.verify()
    if (task_state_root is None or not _prepare_task_state_release(
            runtime / "bin" / "python", release, task_state_root, apps_dir, backup_evidence)):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "candidate_state_incompatible",
            "message": "新版本无法完整保留现有任务状态；现有应用和任务保持不变。",
        }

    if _installer_guard is not None:
        _installer_guard.verify()
    # Candidate startup and compatibility may take time. A historical journal
    # can arrive after the initial admission check; refuse before the first
    # app move rather than activating an empty, split task authority.
    try:
        if _first_install and _legacy_root_has_state(task_state_root, task_state_root):
            return {"ok": False, "reason": "first_install_state_continuity_required"}
        late_legacy = any(_legacy_state_migration_needed(source, app, task_state_root)
                          for source in legacy_sources)
    except (OSError, UnicodeError, ValueError):
        return {"ok": False, "reason": "legacy_state_unavailable",
                "message": "无法安全核对旧版任务状态；现有应用和任务保持不变。"}
    if late_legacy:
        return {"ok": False, "reason": "legacy_state_migration_required",
                "message": "发现旧版任务状态，尚未完成安全迁移；现有应用、任务和已保存答案保持不变。"}

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

    if not _approved_first_install_matches(_approved_bundle, staging):
        return {"ok": False, "reason": "first_install_source_changed"}
    if _installer_guard is not None:
        _installer_guard.verify()
    replaced = current_identity is not None
    if _first_install and replaced:
        return {"ok": False, "reason": "first_install_target_occupied"}
    if _first_install:
        from .first_install import _create_first_fence
        try:
            _create_first_fence(apps_dir, candidate_identity)
        except (OSError, ValueError):
            return {"ok": False, "reason": "first_install_continuity_unconfirmed"}
    try:
        if replaced:
            app.rename(rollback)
        if _first_install:
            _activate_first_install_exclusive(staging, app)
        else:
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
                # A successful restore rename is not final-path health proof.
                # Candidate/current code can change owned slots during health;
                # keep the complete staging evidence unless all roles remain.
                restored = (
                    _bundle_transaction_identity(app) == current_identity
                    and _bundle_transaction_identity(staging) == candidate_identity
                    and not rollback.exists() and not rollback.is_symlink()
                    and not failed.exists() and not failed.is_symlink()
                    and _isolated_bundle_startup(app)
                    and _candidate_starts(
                        app / "Contents" / "Resources" / "runtime" / "bin" / "python",
                        app / "Contents" / "Resources" / "release")
                    and _bundle_transaction_identity(app) == current_identity
                    and _bundle_transaction_identity(staging) == candidate_identity
                    and not rollback.exists() and not rollback.is_symlink()
                    and not failed.exists() and not failed.is_symlink()
                )
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
        if _first_install:
            # This entry owns no previous version. Preserve an unconfirmed
            # active candidate in place; no replace-capable recovery rename.
            return {"ok": False, "reason": "first_install_activation_unconfirmed",
                    "installed": None, "activation_unconfirmed": True}
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
            # The first recovery rename can yield to another actor. Re-admit
            # both owned roles and the empty activation slot before moving
            # the retained release; preserve evidence on any disagreement.
            if (app.exists() or app.is_symlink()
                    or not _bundle_location_matches(failed, candidate_identity)
                    or (replaced and _bundle_transaction_identity(rollback) != current_identity)
                    or (not replaced and (rollback.exists() or rollback.is_symlink()))):
                return {
                    "ok": False,
                    "reason": "post_activation_recovery_required",
                    "message": "恢复移动期间版本身份或目标位置发生变化；所有版本保留当前位置，需要核对恢复。",
                }
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
                    or not _bundle_location_matches(failed, candidate_identity)
                    or rollback.exists() or rollback.is_symlink()
                    or not _isolated_bundle_startup(app)
                    or (restored_runtime.exists() and not _candidate_starts(
                        restored_runtime / "bin" / "python", restored_release))
                    or _bundle_transaction_identity(app) != current_identity
                    or not _bundle_location_matches(failed, candidate_identity)
                    or rollback.exists() or rollback.is_symlink()):
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
    if _installer_guard is not None:
        _installer_guard.verify()
    if _first_install:
        # Final-path health can yield after the pre-activation check. A selected
        # old authority arriving here still forbids opening a new service.
        try:
            if (_legacy_root_has_state(task_state_root, task_state_root)
                    or any(_legacy_state_migration_needed(old, app, task_state_root)
                           for old in legacy_sources)):
                return {"ok": False, "reason": "first_install_continuity_unconfirmed",
                        "installed": None, "activation_unconfirmed": True}
        except (OSError, ValueError, UnicodeError):
            return {"ok": False, "reason": "first_install_continuity_unconfirmed",
                    "installed": None, "activation_unconfirmed": True}
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
            # Restore only means usable after isolated actual final-path health,
            # with both retained identities and the recovery slot unchanged.
            if (_bundle_transaction_identity(app) != current_identity
                    or _bundle_transaction_identity(previous) != previous_identity
                    or failed.exists() or failed.is_symlink()
                    or not _isolated_bundle_startup(app)
                    or not _candidate_starts(
                        app / "Contents" / "Resources" / "runtime" / "bin" / "python",
                        app / "Contents" / "Resources" / "release")
                    or _bundle_transaction_identity(app) != current_identity
                    or _bundle_transaction_identity(previous) != previous_identity
                    or failed.exists() or failed.is_symlink()):
                raise OSError("recovery_health_or_identity_unverified")
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
            if (app.exists() or app.is_symlink()
                    or not _bundle_location_matches(previous, previous_identity)
                    or _bundle_transaction_identity(failed) != current_identity):
                return {
                    "ok": False,
                    "reason": "rollback_recovery_required",
                    "message": "恢复移动期间版本身份或目标位置发生变化；所有版本保留当前位置，需要核对恢复。",
                }
            failed.rename(app)
        except OSError:
            return {
                "ok": False,
                "reason": "rollback_recovery_required",
                "message": "回退版本启用后无法启动；两个版本已保留，需要人工核对恢复。",
            }
        # Returning the original directory is not proof that its final-path
        # runtime still starts. Recheck its exact identity and isolated entry,
        # then verify actual health at the restored path. The rejected old app
        # is quarantined evidence: preserve its owned location, without
        # requiring unhealthy or changed bytes to become a certified release.
        if (_bundle_transaction_identity(app) != current_identity
                or not _bundle_location_matches(previous, previous_identity)
                or failed.exists() or failed.is_symlink()
                or not _isolated_bundle_startup(app)
                or not _candidate_starts(active_runtime / "bin" / "python", active_release)
                or _bundle_transaction_identity(app) != current_identity
                or not _bundle_location_matches(previous, previous_identity)
                or failed.exists() or failed.is_symlink()):
            return {
                "ok": False, "reason": "rollback_recovery_required",
                "message": "原应用已移回，但启动健康、版本身份或恢复位置无法确认；所有版本保留，需要核对恢复。",
            }
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
