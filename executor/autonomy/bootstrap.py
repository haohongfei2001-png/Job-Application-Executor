"""Independent, loopback-only recovery page for an unavailable supervisor."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .loopback_http import LoopbackHTTPServer
from .queue import private_dir
from .process_entry import isolated_cli_command
from .release import read_release_identity
from .consumer_presentation import ConsumerSurface, loopback_origin, present_surface


def _state_path(root: str | Path) -> Path:
    return private_dir(root) / "bootstrap.json"


def _reason(code: str) -> str:
    return {
        "service_start_failed": "本地服务启动失败。现有任务没有被修改。",
        "health_timeout": "本地服务未能通过健康检查。现有任务没有被修改。",
        "worker_stopping_at_safe_checkpoint": "服务仍在等待安全停止点。请稍后重试。",
        "release_mismatch": "新应用尚未接管旧版服务。现有任务没有被修改；请在安全停止后重试。",
        "release_unverified": "应用文件未通过完整性校验。现有任务没有被修改；请重新安装可信版本。",
    }.get(code, "本地服务暂时不可用。现有任务没有被修改。")


def _version(root: str | Path | None = None) -> str:
    source = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]
    identity = read_release_identity(source)
    if identity.get("status") == "verified":
        return identity["source_sha256"][:12]
    # An installed app with a missing or damaged manifest is unverified. Never
    # infer its identity from Git metadata on the host or invoke Git for it.
    from .release import is_packaged_source
    if is_packaged_source(source):
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source,
            capture_output=True, text=True, timeout=3,
        )
        sha = result.stdout.strip()
        return sha[:12] if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _safe_diagnostics(reason: str, version: str) -> dict:
    """A copy-safe bootstrap report that needs no running supervisor."""
    known = {
        "service_start_failed": "retry_service",
        "health_timeout": "retry_service",
        "worker_stopping_at_safe_checkpoint": "retry_after_safe_checkpoint",
        "release_mismatch": "retry_after_old_service_stops",
        "release_unverified": "reinstall_verified_app",
    }
    code = reason if reason in known else "service_unavailable"
    source = Path(__file__).resolve().parents[2]
    identity = read_release_identity(source)
    verified = identity.get("status") == "verified"
    digest = identity.get("source_sha256", "") if verified else ""
    return {
        "format": "application-executor-bootstrap-diagnostics-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "loaded_version": version,
        "loaded_source_verified": verified,
        "loaded_source_sha256": digest,
        "reason": code,
        "recovery_action": known.get(code, "retry_service"),
        "applicant_values_in_report": False,
        "final_click_actor": "user",
        "submit_capability": False,
    }


_PREPARATION_ITEMS = (
    ("live_browser_mode", "实时浏览器模式", "当前处于隔离验证模式；不会开始真实申请。"),
    ("chrome_installed", "Chrome 安装", "需要已安装的 Chrome；本页不会下载或安装软件。"),
    ("existing_cdp_session", "专用浏览器连接", "专用投递浏览器连接尚未就绪；本页不会打开浏览器或接管其他窗口。"),
    ("profile_configured", "个人资料配置", "个人资料尚未配置；本页不收集或保存资料。"),
    ("profile_exists", "资料文件可用", "已配置的资料文件不可用；现有资料不会被修改。"),
    ("profile_loadable", "资料可安全读取", "资料尚未通过读取检查；不会以缺失值或猜测值填表。"),
    ("deepseek_available", "模型本地配置", "现有模型配置尚不可用；本页不会申请 key、调用模型或产生费用。"),
    ("supervisor_running", "本地服务", "服务尚未通过本次恢复检查；可明确点击下方按钮重试。"),
)


def _preparation_snapshot() -> dict:
    """Only fixed booleans from the existing local, read-only preflight.

    A missing business dependency must not prevent this recovery surface from
    opening. Never include settings, paths, applicant values, credentials,
    provider response text or exception details in the page/report.
    """
    try:
        from .preflight import collect_live_preflight
        result = collect_live_preflight(supervisor_running=False)
        observed = result.get("checks") if type(result) is dict else None
        observed = observed if type(observed) is dict else {}
    except Exception:
        observed = {}
    checks = {key: observed.get(key) if type(observed.get(key)) is bool else None
              for key, _label, _help in _PREPARATION_ITEMS}
    # This is an unavailable-service recovery surface, never a live readiness
    # certificate, even if a malformed collector claims all checks passed.
    checks["supervisor_running"] = False
    if checks["profile_configured"] is not True or checks["profile_exists"] is not True:
        if checks["profile_loadable"] is True:
            checks["profile_loadable"] = None
    return {"scope": "local_configuration_only", "checks": checks,
            "ready_for_live_e2e": False, "external_connection_verified": False,
            "settings_mutation_available": False, "submit_capability": False}


def _preparation_markup(snapshot: dict) -> str:
    rows = []
    for key, label, guidance in _PREPARATION_ITEMS:
        passed = snapshot["checks"][key]
        state = "可用" if passed is True else ("待处理" if passed is False else "未核验")
        help_text = "本地配置可用；未验证真实账号、额度或招聘网站。" if passed is True else guidance
        rows.append(f'<li data-check="{key}"><strong>{label}</strong> · '
                    f'<span class="check-state">{state}</span><p>{help_text}</p></li>')
    return "".join(rows)


def _page(reason: str, token: str) -> str:
    safe_reason = html.escape(_reason(reason))
    safe_token = html.escape(token, quote=True)
    version = _version()
    preparation = _preparation_snapshot()
    preparation_html = _preparation_markup(preparation)
    diagnostics = {**_safe_diagnostics(reason, version), "preparation": preparation}
    report = html.escape(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 投递经理 · 恢复</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f6f7f9;color:#111;margin:0}}
main{{max-width:540px;margin:10vh auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:28px}}
h1{{font-size:21px}}h2{{font-size:17px}}p{{line-height:1.6}}li p{{margin:4px 0 12px;font-size:13px;color:#475569}}pre,textarea{{width:100%;box-sizing:border-box;white-space:pre-wrap;overflow-wrap:anywhere}}textarea{{min-height:180px}}button{{background:#111;color:white;border:0;border-radius:9px;padding:11px 17px;cursor:pointer}}
</style></head><body><main><h1>AI 投递经理</h1><p>{safe_reason}</p>
<section aria-labelledby="preparation-title"><h2 id="preparation-title">打开应用前的准备</h2>
<p>本页可在投递服务不可用时查看。检查只读取现有本地配置；不保存资料、不读取任务内容、不申请权限，也不访问招聘网站。</p>
<p>配置可用不代表可以投递：真实账号、模型连接与额度、网站兼容性尚未核验。最终提交始终由你本人点击。</p>
<ul id="preparation-checks">{preparation_html}</ul>
<p>刷新本页可重新读取配置状态。不会自动重试服务或重复申请。</p></section>
<p>可以重试本地服务。重试不会重放结果不明的浏览器写入，也不会提交申请。</p>
<form action="/retry?token={safe_token}" method="post"><button type="submit">重试并打开面板</button></form>
<p><small>本地版本：{version}</small></p>
<details id="diagnostics-details"><summary>查看安全诊断</summary><p>报告不含申请人资料、验证码或凭证；分享前请先核对。</p><pre id="safe-diagnostics">{report}</pre><button id="copy-diagnostics" type="button">复制诊断</button>
<p id="copy-result" role="status" aria-live="polite"></p>
<textarea id="manual-diagnostics" aria-label="手动复制安全诊断" readonly hidden></textarea></details>
</main><script>
const copyButton=document.getElementById('copy-diagnostics'),details=document.getElementById('diagnostics-details'),
copyResult=document.getElementById('copy-result'),manualReport=document.getElementById('manual-diagnostics');
let copyEpoch=0;
function clearCopy(){{copyEpoch++;copyResult.textContent='';manualReport.value='';manualReport.hidden=true;copyButton.disabled=false;}}
details.addEventListener('toggle',()=>{{if(!details.open)clearCopy();}});
copyButton.addEventListener('click',async function(){{
  if(!details.open||this.disabled)return;
  const epoch=++copyEpoch,report=document.getElementById('safe-diagnostics').textContent;
  this.disabled=true;manualReport.value='';manualReport.hidden=true;copyResult.textContent='正在复制…';
  try{{
    await navigator.clipboard.writeText(report);
    if(epoch!==copyEpoch||!details.open)return;
    copyResult.textContent='已复制安全诊断';
  }}catch(e){{
    if(epoch!==copyEpoch||!details.open)return;
    copyResult.textContent='复制结果未确认；可选择下方报告手动复制。';
    manualReport.value=report;manualReport.hidden=false;manualReport.focus();manualReport.select();
  }}finally{{if(epoch===copyEpoch)this.disabled=false;}}
}});
</script></body></html>"""


def serve_bootstrap(root: str | Path, service_port: int, initial_reason: str = "service_unavailable") -> None:
    from .cli import _start_consumer_service, request

    root = Path(root).expanduser().resolve()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status: int, body: str, *, location: str | None = None):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            if location:
                self.send_header("Location", location)
            else:
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if not location:
                self.wfile.write(payload)

        def handle_request(self):
            expected_host = f"127.0.0.1:{self.server.server_address[1]}"
            parsed = urllib.parse.urlsplit(self.path)
            supplied = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            if self.headers.get("Host") != expected_host or not secrets.compare_digest(supplied, token):
                self.respond(403, "<h1>无法访问恢复页</h1>")
                return
            if self.command == "GET" and parsed.path == "/":
                self.respond(200, _page(initial_reason, token))
                return
            if self.command != "POST" or parsed.path != "/retry":
                self.respond(404, "<h1>页面不存在</h1>")
                return
            if self.headers.get("Origin") != "http://" + expected_host:
                self.respond(403, "<h1>无法访问恢复页</h1>")
                return
            if self.headers.get("Content-Length") not in {None, "0"}:
                self.respond(400, "<h1>请求无效</h1>")
                return
            started, health = _start_consumer_service(root, service_port)
            if health.get("ok"):
                try:
                    ticket = request(root, service_port, "/v1/ui-ticket", {})["ticket"]
                    target = ConsumerSurface.dashboard(service_port, ticket).url
                    self.respond(303, "", location=target)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                except (KeyError, ValueError, TypeError, OSError, urllib.error.URLError):
                    pass
            self.respond(503, _page(str(started.get("reason") or "service_unavailable"), token))

        do_GET = handle_request
        do_POST = handle_request

    server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
    state = _state_path(root)
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": server.server_address[1], "token": token}))
    tmp.chmod(0o600)
    tmp.replace(state)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            if json.loads(state.read_text()).get("pid") == os.getpid():
                state.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def open_bootstrap(root: str | Path, service_port: int, reason: str = "service_unavailable", *,
                   presenter=None) -> dict:
    loopback_origin(service_port)
    root = Path(root).expanduser().resolve()
    state = _state_path(root)

    def current() -> dict | None:
        try:
            if state.is_symlink() or state.stat().st_mode & 0o077:
                return None
            record = json.loads(state.read_text())
            pid, port, token = int(record["pid"]), int(record["port"]), str(record["token"])
            if not (0 < port < 65536 and len(token) >= 32):
                return None
            command = subprocess.run(
                ["ps", "-ww", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=3,
            ).stdout
            if "executor.autonomy.cli" not in command or "bootstrap-serve" not in command or str(root) not in command:
                return None
            url = f"http://127.0.0.1:{port}/?token={urllib.parse.quote(token)}"
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status != 200:
                    return None
            return {"pid": pid, "url": url}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, urllib.error.URLError):
            return None

    active = current()
    if active is None:
        state.unlink(missing_ok=True)
        log = root / "bootstrap.log"
        with log.open("ab") as stream:
            log.chmod(0o600)
            child = subprocess.Popen(
                isolated_cli_command("--runtime", str(root), "--port", str(service_port),
                                     "bootstrap-serve", "--reason", reason),
                cwd=Path(__file__).resolve().parents[2],
                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                start_new_session=True,
            )
        for _ in range(50):
            if child.poll() is not None:
                break
            active = current()
            if active and active["pid"] == child.pid:
                break
            time.sleep(0.1)
    if not active:
        return {"ok": False, "opened": False, "reason": "bootstrap_start_failed"}
    try:
        surface = ConsumerSurface.from_url("bootstrap", active["url"],
                                           service_port=service_port)
    except ValueError:
        return {"ok": False, "opened": False, "reason": "bootstrap_presentation_invalid"}
    opened = present_surface(surface, presenter=presenter)
    return {"ok": opened, "opened": opened}
