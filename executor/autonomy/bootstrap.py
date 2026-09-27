"""Independent, loopback-only recovery page for an unavailable supervisor."""

from __future__ import annotations

import html
import fcntl
import json
from datetime import datetime, timezone
from contextlib import contextmanager
import os
import re
import secrets
import stat
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
from .runtime_paths import private_dir
from .process_entry import isolated_cli_command
from .release import read_release_identity
from .runtime_provenance import packaged_provenance, current_packaged_source
from .consumer_presentation import ConsumerSurface, loopback_origin, present_surface


def _state_path(root: str | Path) -> Path:
    return private_dir(root) / "bootstrap.json"


def _bootstrap_identity_valid(record) -> bool:
    return (type(record) is dict and set(record) == {"pid", "port", "token", "service_port"}
            and type(record["pid"]) is int and 0 < record["pid"] <= 2147483647
            and all(type(record[key]) is int and 0 < record[key] < 65536
                    for key in ("port", "service_port"))
            and type(record["token"]) is str and 32 <= len(record["token"]) <= 128
            and record["token"].isascii()
            and all(char.isalnum() or char in "_-" for char in record["token"]))


def _bootstrap_record(path: Path) -> dict | None:
    # Cold admission needs only stdlib, never a task queue/business import.
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("bootstrap_state_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077):
            raise ValueError("bootstrap_state_invalid")
        raw = handle.read(65537)
    current = path.stat(follow_symlinks=False)
    if (len(raw) > 65536 or (current.st_dev, current.st_ino)
            != (metadata.st_dev, metadata.st_ino)):
        raise ValueError("bootstrap_state_invalid")
    record = json.loads(raw)
    if not _bootstrap_identity_valid(record):
        raise ValueError("bootstrap_state_invalid")
    return record


@contextmanager
def _bootstrap_guard(root: Path):
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("bootstrap_state_invalid")
    root = private_dir(root)
    path = root / "bootstrap-service.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()):
            raise ValueError("bootstrap_state_invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("bootstrap_state_invalid")
        os.fchmod(fd, 0o600)
        yield
    finally:
        os.close(fd)


def _bootstrap_current(state: Path, service_port: int) -> dict | None:
    record = _bootstrap_record(state)
    if record is None or record["service_port"] != service_port:
        return None
    base = f"http://127.0.0.1:{record['port']}"
    query = "?token=" + urllib.parse.quote(record["token"], safe="")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    try:
        # An actual private loopback identity, not PID/argv/page-200 heuristics.
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(base + "/identity" + query, timeout=2) as response:
            if response.status != 200:
                return None
            raw = response.read(65537)
        observed = json.loads(raw)
        if (len(raw) > 65536 or not _bootstrap_identity_valid(observed)
                or observed != record or _bootstrap_record(state) != record):
            return None
        return {"pid": record["pid"], "url": base + "/" + query}
    except (OSError, ValueError, TypeError, UnicodeError, urllib.error.URLError):
        return None


def _reason(code: str) -> str:
    return {
        "service_start_failed": "本地服务启动失败。现有任务没有被修改。",
        "business_dependencies_unavailable": "业务运行依赖暂不可用。恢复页仍可打开；现有任务没有被修改。",
        "health_timeout": "本地服务未能通过健康检查。现有任务没有被修改。",
        "worker_stopping_at_safe_checkpoint": "服务仍在等待安全停止点。请稍后重试。",
        "release_mismatch": "新应用尚未接管旧版服务。现有任务没有被修改；请在安全停止后重试。",
        "release_unverified": "应用文件未通过完整性校验。现有任务没有被修改；请重新安装可信版本。",
    }.get(code, "本地服务暂时不可用。现有任务没有被修改。")


def _version(root: str | Path | None = None) -> str:
    source = Path(root).expanduser().absolute() if root is not None else Path(__file__).resolve().parents[2]
    # The cold page must not block opening a FIFO/directory manifest or consult
    # host Git when an installed app has missing, damaged or aliased payloads.
    from .release import is_packaged_source
    if is_packaged_source(source):
        identity = current_packaged_source(source)
        return identity["source_sha256"][:12] if identity["status"] == "verified" else "unknown"
    identity = read_release_identity(source)
    if identity.get("status") == "verified":
        return identity["source_sha256"][:12]
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
        "business_dependencies_unavailable": "reinstall_verified_app",
        "health_timeout": "retry_service",
        "worker_stopping_at_safe_checkpoint": "retry_after_safe_checkpoint",
        "release_mismatch": "retry_after_old_service_stops",
        "release_unverified": "reinstall_verified_app",
    }
    code = reason if reason in known else "service_unavailable"
    source = Path(__file__).resolve().parents[2]
    identity = current_packaged_source(source)
    payload = packaged_provenance(source)
    if payload is not None and (not payload["source_verified_now"]
            or payload["source_sha256"] != identity.get("source_sha256")):
        identity = {"status": "unverified", "source_sha256": ""}
    verified = identity.get("status") == "verified"
    digest = identity.get("source_sha256", "") if verified else ""
    return {
        "format": "application-executor-bootstrap-diagnostics-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "loaded_version": version,
        "loaded_source_verified": verified,
        "loaded_source_sha256": digest,
        "source_identity_basis": "current_payload_integrity",
        **({"packaged_release": payload} if payload is not None else {}),
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


def _payload_markup(report: dict) -> str:
    payload = report.get("packaged_release") or {}
    rows = []
    for key, label in (
            ("source_verified_now", "应用文件"),
            ("runtime_verified_now", "自带运行环境文件"),
            ("interpreter_owned", "当前解释器来自应用")):
        observed = payload.get(key)
        state = "已核对" if observed is True else ("未通过" if observed is False else "未核验")
        rows.append(f"<li><strong>{label}</strong> · {state}</li>")
    return "".join(rows)


def _page(reason: str, token: str) -> str:
    safe_reason = html.escape(_reason(reason))
    safe_token = html.escape(token, quote=True)
    version = _version()
    preparation = _preparation_snapshot()
    preparation_html = _preparation_markup(preparation)
    diagnostics = {**_safe_diagnostics(reason, version), "preparation": preparation}
    report = html.escape(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    payload_html = _payload_markup(diagnostics)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 投递经理 · 恢复</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f6f7f9;color:#111;margin:0}}
main{{max-width:540px;margin:10vh auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:28px}}
h1{{font-size:21px}}h2{{font-size:17px}}p{{line-height:1.6}}li p{{margin:4px 0 12px;font-size:13px;color:#475569}}pre,textarea{{width:100%;box-sizing:border-box;white-space:pre-wrap;overflow-wrap:anywhere}}textarea{{min-height:180px}}button{{background:#111;color:white;border:0;border-radius:9px;padding:11px 17px;cursor:pointer}}
</style></head><body><main><h1>AI 投递经理</h1><p>{safe_reason}</p>
<section aria-labelledby="payload-title"><h2 id="payload-title">应用运行环境</h2>
<ul id="payload-checks">{payload_html}</ul>
<p>这里只核对当前应用文件；不代表业务依赖已成功加载、服务可用或发布签名已认证。不会运行候选程序，也不会下载或修复文件。</p></section>
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
// Summary activation clears before the browser's default close operation.
details.querySelector('summary').addEventListener('click',()=>{{if(details.open)clearCopy();}});
// Attribute records preserve even close+reopen in the same turn; queued toggle
// events may coalesce and are too late to fence the clipboard result.
new MutationObserver(records=>{{if(records.some(record=>record.oldValue!==null))clearCopy();}})
  .observe(details,{{attributes:true,attributeFilter:['open'],attributeOldValue:true}});
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
    loopback_origin(service_port)
    root = Path(root).expanduser().absolute()
    with _bootstrap_guard(root):
        _serve_bootstrap_locked(root, service_port, initial_reason)


def _serve_bootstrap_locked(root: Path, service_port: int, initial_reason: str) -> None:
    from .cli import _start_consumer_service, request

    root = Path(root).expanduser().resolve()
    token = secrets.token_urlsafe(32)
    state = _state_path(root)
    previous = _bootstrap_record(state)
    if previous is not None and previous["service_port"] != service_port:
        raise ValueError("bootstrap_service_mismatch")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status: int, body: str, *, location: str | None = None,
                    content_type: str = "text/html; charset=utf-8"):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            if location:
                self.send_header("Location", location)
            else:
                self.send_header("Content-Type", content_type)
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
            if self.command == "GET" and parsed.path == "/identity":
                if (self.headers.get("Origin") not in {None, "http://" + expected_host}
                        or urllib.parse.parse_qs(parsed.query) != {"token": [token]}):
                    self.respond(403, "<h1>无法访问恢复页</h1>")
                    return
                self.respond(200, json.dumps(record), content_type="application/json; charset=utf-8")
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
    record = {"pid": os.getpid(), "port": server.server_address[1],
              "token": token, "service_port": service_port}
    tmp = state.with_name(".bootstrap-" + secrets.token_hex(16) + ".tmp")
    try:
        if _bootstrap_record(state) != previous:
            raise ValueError("bootstrap_state_changed")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(state)
        server.serve_forever(poll_interval=0.5)
    finally:
        tmp.unlink(missing_ok=True)
        server.server_close()
        try:
            if _bootstrap_record(state) == record:
                state.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, UnicodeError):
            pass  # A replaced/ambiguous registry is preserved.


def open_bootstrap(root: str | Path, service_port: int, reason: str = "service_unavailable", *,
                   presenter=None) -> dict:
    loopback_origin(service_port)
    root = Path(root).expanduser().absolute()
    try:
        if any(part.is_symlink() for part in (root, *root.parents)):
            raise ValueError("bootstrap_state_invalid")
        state = _state_path(root)
        record = _bootstrap_record(state)
        if record is not None and record["service_port"] != service_port:
            return {"ok": False, "opened": False, "reason": "bootstrap_service_mismatch"}
        active = _bootstrap_current(state, service_port)
        if active is None:
            # A dead exact-format owner can recover only after its lease is
            # released. A hung/live owner never authorizes a second writer.
            with _bootstrap_guard(root):
                if _bootstrap_record(state) != record:
                    raise ValueError("bootstrap_state_changed")
            log = root / "bootstrap.log"
            fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                         | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(fd, "ab") as stream:
                metadata = os.fstat(stream.fileno())
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or metadata.st_uid != os.geteuid()):
                    raise ValueError("bootstrap_state_invalid")
                os.fchmod(stream.fileno(), 0o600)
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
                active = _bootstrap_current(state, service_port)
                if active and active["pid"] == child.pid:
                    break
                time.sleep(0.1)
    except BlockingIOError:
        return {"ok": False, "opened": False, "reason": "bootstrap_owner_busy"}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"ok": False, "opened": False, "reason": "bootstrap_state_invalid"}
    if not active:
        return {"ok": False, "opened": False, "reason": "bootstrap_start_failed"}
    try:
        surface = ConsumerSurface.from_url("bootstrap", active["url"],
                                           service_port=service_port)
    except ValueError:
        return {"ok": False, "opened": False, "reason": "bootstrap_presentation_invalid"}
    opened = present_surface(surface, presenter=presenter)
    return {"ok": opened, "opened": opened}
