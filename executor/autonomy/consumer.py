from __future__ import annotations

import plistlib
import shlex
import shutil
import stat
import sys
from pathlib import Path


APP_NAME = "AI 投递经理"
BUNDLE_ID = "com.local.job-application-executor.ai-application-manager"

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


def install_macos_app(
    repo_root: str | Path,
    *,
    destination: str | Path | None = None,
    platform: str | None = None,
) -> dict:
    """Install a thin local .app bundle that launches the existing executor."""
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
    if staging.is_symlink():
        staging.unlink()
    elif staging.exists():
        shutil.rmtree(staging)

    macos = staging / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "AIApplicationManager"
    repo_q = shlex.quote(str(repo))

    launcher = f"""#!/bin/zsh
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

    rollback = apps_dir / f".{APP_NAME}.app.previous"
    if app.is_symlink() or rollback.exists() or rollback.is_symlink():
        shutil.rmtree(staging)
        return {
            "ok": False,
            "reason": "untrusted_app_path" if app.is_symlink() else "rollback_pending",
            "message": "旧版应用或回退副本需要先核对；现有应用没有被替换。",
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
    if (app.is_symlink() or previous.is_symlink() or failed.is_symlink()
            or not app.is_dir() or not previous.is_dir() or failed.exists()):
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
