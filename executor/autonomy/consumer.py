from __future__ import annotations

import json
import os
import socket
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
    "configure_deepseek_key": "DeepSeek 服务尚未连接。",
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


def _packaged_launcher() -> str:
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


def _packaged_bundle_matches(executable: Path, runtime: Path) -> bool:
    try:
        launcher = executable.read_text(encoding="utf-8")
        if runtime.exists():
            return launcher == _packaged_launcher()
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
             if release.exists() else _legacy_bundle_matches(executable))
        )
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return False


def _candidate_starts(python: Path, release: Path) -> bool:
    """Require the staged service to answer authenticated loopback health."""
    from .release import source_manifest

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

def install_macos_app(
    repo_root: str | Path,
    *,
    destination: str | Path | None = None,
    platform: str | None = None,
) -> dict:
    """Stage a source-and-runtime snapshot, then atomically activate the Mac app."""
    current_platform = platform or sys.platform
    if current_platform != "darwin":
        return {
            "ok": False,
            "reason": "macos_required",
            "message": "AI 投递经理.app 只在 macOS 上安装。",
        }

    repo = Path(repo_root).expanduser().resolve()
    python = repo / ".venv" / "bin" / "python"
    if not python.is_file():
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
        copy_runtime_candidate(repo / ".venv", runtime, release)
    except (OSError, ValueError):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "runtime_snapshot_failed",
            "message": "无法准备独立运行环境；现有应用没有被替换。",
        }
    executable = macos / "AIApplicationManager"
    launcher = _packaged_launcher()

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
    if (app.exists() or app.is_symlink()) and not _trusted_bundle(app):
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "untrusted_app_path",
            "message": "现有应用包无法核对；没有替换或删除任何应用。",
        }
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
    replaced = app.exists()
    try:
        if replaced:
            app.rename(rollback)
        staging.rename(app)
    except OSError:
        restored = not replaced
        if replaced and rollback.exists() and not app.exists():
            try:
                rollback.rename(app)
                restored = True
            except OSError:
                restored = False
        if staging.exists():
            shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "activation_failed" if restored else "rollback_required",
            "rollback_path": str(rollback) if rollback.exists() else None,
            "message": (
                "新版本未启用，原应用保持可用。"
                if restored else "新版本未启用；旧版保存在回退位置，需要人工恢复。"
            ),
        }
    # Recheck at the final bundle path: moving a virtualenv can invalidate
    # interpreter/framework references even when staging health succeeded.
    active_release = app / "Contents" / "Resources" / "release"
    active_runtime = app / "Contents" / "Resources" / "runtime"
    if (not _trusted_bundle(app)
            or not _candidate_starts(active_runtime / "bin" / "python", active_release)):
        try:
            app.rename(failed)
            if replaced:
                rollback.rename(app)
        except OSError:
            if failed.exists() and not app.exists():
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
        return {
            "ok": False,
            "reason": "post_activation_unhealthy",
            "rollback_path": None,
            "failed_candidate_path": str(failed),
            "message": ("新版本启用后未通过健康检查；已恢复上一版本。"
                        if replaced else "新版本启用后未通过健康检查；失败候选已保留供诊断。"),
        }
    return {
        "ok": True,
        "installed": True,
        "replaced": replaced,
        "app_path": str(app),
        "rollback_path": str(rollback) if replaced else None,
        "message": "AI 投递经理已安装。以后直接双击应用即可。",
    }


def rollback_macos_app(destination: str | Path) -> dict:
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
        try:
            failed.rename(app)
            reason = "rollback_activation_failed"
            message = "旧版没有启用，当前版本已恢复。"
        except OSError:
            reason = "manual_recovery_required"
            message = "两个版本仍保留在原位置与失败位置，需要人工恢复。"
        return {"ok": False, "reason": reason, "message": message}
    return {
        "ok": True,
        "restored": True,
        "app_path": str(app),
        "failed_candidate_path": str(failed),
        "message": "已恢复上一版本。失败的候选版本保留供诊断。",
    }
