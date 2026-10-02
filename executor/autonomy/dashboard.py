from __future__ import annotations

DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 投递经理</title>
<style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111;background:#f6f7f9}
*{box-sizing:border-box}body{margin:0}.shell{display:grid;grid-template-columns:320px 1fr;min-height:100vh}
aside{background:#fff;border-right:1px solid #e5e7eb;padding:20px;overflow:auto}
main{display:grid;grid-template-rows:auto 1fr auto;min-height:100vh}
header{padding:18px 22px;background:#fff;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between;gap:14px}
h1{font-size:18px;margin:0}.statusbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#16a34a;margin-right:7px}
.headerbtn{border:1px solid #d7dce2;background:#fff;color:#111;padding:7px 11px;border-radius:9px;font-weight:600;font-size:13px}
.headerbtn:hover{background:#f8fafc}.headerbtn:disabled{opacity:.45;cursor:default}
.readiness{font-size:12px;color:#92400e;max-width:360px;line-height:1.35}
.readiness-checks{display:grid;gap:8px;list-style:none;padding:0;margin:12px 0}
.readiness-checks li{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #e2e8f0;padding:6px 0;font-size:13px}
.readiness-checks .pass{color:#166534}.readiness-checks .pending{color:#92400e}
.readiness-summary{font-size:13px;line-height:1.5;color:#475569}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:16px 0}
.metric{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
.metric b{display:block;font-size:20px}.task{border:1px solid #e5e7eb;border-radius:12px;padding:11px;margin:8px 0;background:#fff}
.task .title{font-weight:650}.task[data-current-task="true"]{border-color:#64748b;box-shadow:0 0 0 1px #64748b}.task-select{width:100%;display:block;padding:7px 0;text-align:left;background:transparent;color:inherit;border-radius:5px;font:inherit;font-weight:650}.task-select:focus-visible{outline:3px solid #2563eb;outline-offset:3px}.task-context{font-size:12px;line-height:1.45;color:#475569;margin:10px 0;overflow-wrap:anywhere}.task .meta{font-size:12px;color:#64748b;margin-top:4px}.stage{font-size:11px;border-radius:999px;padding:3px 7px;background:#eef2ff;display:inline-block;margin-top:7px}
.review{font-size:12px;line-height:1.5;margin-top:9px;padding:9px;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0}.review.warning{background:#fff7ed;border-color:#fed7aa;color:#9a3412}
.private-review{max-height:52vh;overflow:auto;white-space:pre-wrap}.private-review table{width:100%;border-collapse:collapse;margin-top:8px}.private-review th,.private-review td{border:1px solid #cbd5e1;padding:6px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
.taskcontrols{display:flex;gap:6px;margin-top:9px}.taskcontrols button{font-size:12px;padding:6px 9px;background:#f1f5f9;color:#111;border:1px solid #d7dce2}
.task-view-controls{flex-wrap:wrap}.task-view-controls button{min-height:44px}
.factinput{display:flex;gap:5px;margin-top:8px}.factinput input,.factinput select{min-width:0;flex:1;border:1px solid #cbd5e1;border-radius:7px;padding:7px}.factinput button{font-size:12px;padding:6px 8px;background:#e2e8f0;color:#111}
.factinput .remember-fact{flex:0 0 16px;width:16px;min-width:16px;padding:0}
.otpinput{display:flex;gap:5px;margin-top:8px}.otpinput input{min-width:0;flex:1;border:1px solid #cbd5e1;border-radius:7px;padding:7px}.otpinput button{font-size:12px;padding:6px 8px;background:#e2e8f0;color:#111}.otpnote{font-size:12px;color:#64748b;line-height:1.4;margin-top:7px}
.newtask{display:grid;gap:7px;margin:12px 0 17px}.newtask input{width:100%;border:1px solid #cbd5e1;border-radius:7px;padding:8px}.newtask button{padding:9px}
.candidates{display:grid;gap:7px;margin-bottom:16px}.candidate{border:1px solid #d7dce2;border-radius:9px;padding:9px;font-size:12px}.candidate button{display:block;margin-top:7px;padding:6px 9px;font-size:12px}.candidate-note{font-size:12px;color:#92400e}
.chat{padding:20px 24px;overflow:auto}.bubble{max-width:820px;padding:11px 13px;border-radius:13px;margin:8px 0;white-space:pre-wrap;line-height:1.5}
.me{margin-left:auto;background:#111;color:#fff}.ai{background:#fff;border:1px solid #e5e7eb}.actions{font-size:12px;color:#64748b;margin-top:5px}
.composer{background:#fff;border-top:1px solid #e5e7eb;padding:14px 20px;display:flex;gap:10px}
textarea{flex:1;min-height:52px;max-height:160px;resize:vertical;border:1px solid #cbd5e1;border-radius:12px;padding:12px;font:inherit}
button{border:0;border-radius:10px;background:#111;color:#fff;padding:0 18px;font-weight:600;cursor:pointer}
button:disabled{opacity:.45}.empty{color:#94a3b8;font-size:13px}.error{color:#b91c1c}.diagnostics-dialog{width:min(680px,calc(100vw - 32px));max-height:80vh;border:1px solid #cbd5e1;border-radius:14px;padding:22px;box-shadow:0 18px 60px #0003}
.diagnostics-dialog::backdrop{background:#0f172a99}.diagnostics-dialog h2{font-size:18px;margin:0 0 8px}.diagnostics-dialog p{font-size:13px;line-height:1.5;color:#475569}
.diagnostics-dialog pre{max-height:48vh;overflow:auto;padding:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}
.diagnostics-actions{display:flex;justify-content:flex-end;gap:8px}.diagnostics-actions button{min-height:36px}
.preparation-checks{list-style:none;margin:12px 0;padding:0}.preparation-checks li{display:flex;justify-content:space-between;gap:14px;border-bottom:1px solid #e2e8f0;padding:9px 0;font-size:13px;line-height:1.5}.preparation-checks strong{color:#475569;text-align:right;min-width:0;overflow-wrap:anywhere}.preparation-checks .missing{color:#9a3412}.preparation-manual{font-size:13px;line-height:1.7;padding-left:22px}.preparation-dialog h3{font-size:15px;margin:20px 0 8px}.preparation-dialog a{display:inline-block;min-height:44px;padding:12px 0;color:#1d4ed8}.preparation-dialog .diagnostics-actions{position:sticky;bottom:-22px;background:white;padding:12px 0}.preparation-dialog .diagnostics-actions button{min-height:44px}
/* Private approval controls remain readable and touchable inside the same dialog. */
.preparation-dialog button{min-height:44px;line-height:1.4;padding:10px 14px;white-space:normal;text-align:left}
.preparation-native-step{border:1px solid #d7dce2;border-radius:12px;padding:16px;margin:16px 0;background:#f8fafc}
.preparation-native-step h3{margin:0 0 10px}.preparation-native-step p{overflow-wrap:anywhere}
.preparation-native-step label{display:grid;grid-template-columns:20px minmax(0,1fr);gap:10px;align-items:start;font-size:14px;line-height:1.6;margin:14px 0}
.preparation-native-step input[type=checkbox]{width:20px;height:20px;margin:2px 0 0}
.preparation-native-step button:not([hidden]){display:block;margin-top:12px}
.preparation-dialog .preparation-secondary{background:#f1f5f9;color:#334155;border:1px solid #cbd5e1}
#preparation-review-hide{margin-top:14px}#preparation-native-status{min-height:20px}
@media(max-width:600px){.preparation-native-step{padding:12px}.preparation-native-step button{width:100%}}
.preparation-checks label{display:flex;align-items:center;gap:8px;min-height:28px;margin:0}.preparation-checks input[type=checkbox]{width:18px;height:18px;flex:0 0 auto;margin:0}
#session-expired{margin:0;padding:16px 22px;background:#fff7ed;color:#9a3412;border-bottom:1px solid #fed7aa;line-height:1.5}
.toast{position:fixed;right:22px;bottom:88px;max-width:420px;background:#111;color:#fff;padding:11px 14px;border-radius:10px;box-shadow:0 10px 30px #0003;display:none;z-index:20;font-size:13px;line-height:1.45}
@media(max-width:820px){.shell{grid-template-columns:1fr}aside{display:block;max-height:45vh;border-right:0;border-bottom:1px solid #e5e7eb}main{min-height:55vh}}
#candidates>button[data-existing-task]{min-height:44px}
.profile-editor-fields{display:grid;gap:12px;margin:18px 0}.profile-editor-field{display:grid;gap:5px;font-size:13px}.profile-editor-field input,.profile-editor-field textarea{width:100%;min-width:0;border:1px solid #cbd5e1;border-radius:7px;padding:9px;font:inherit}.profile-editor-field small{color:#64748b}.profile-editor-dialog .diagnostics-actions{flex-wrap:wrap;position:sticky;bottom:-22px;background:white;padding:12px 0}.profile-editor-dialog button{min-height:44px}.profile-editor-dialog input[type=file]{display:block;max-width:100%;margin:10px 0}.profile-editor-dialog details{font-size:12px;overflow-wrap:anywhere;margin:12px 0}.profile-editor-dialog p[role=status]{min-height:20px}
</style>
</head>
<body>
<p id="session-expired" role="alert" tabindex="-1" hidden>面板会话已失效。请重新打开 AI 投递经理；未发送的输入仍保留在此页，可先复制。已有任务不会因重新打开而自动重试，最终提交仍由你本人完成。</p>
<div class="shell">
<aside>
  <h1>任务</h1>
  <div class="metrics">
    <div class="metric"><span>进行中</span><b id="running">0</b></div>
    <div class="metric"><span>需要你</span><b id="need">0</b></div>
    <div class="metric"><span>等你确认</span><b id="ready">0</b></div>
    <div class="metric"><span>已结束</span><b id="done">0</b></div>
  </div>
  <form id="newtask" class="newtask" autocomplete="off">
    <strong>查找并添加岗位</strong>
    <input name="company" maxlength="150" placeholder="公司" required>
    <input name="role" maxlength="200" placeholder="岗位名称" required>
    <input name="location" maxlength="150" placeholder="地点（可选）">
    <input name="campaign" maxlength="150" placeholder="招聘批次（可选）">
    <input name="employment_type" maxlength="100" placeholder="用工类型（可选）">
    <input name="target_url" type="url" maxlength="2000" placeholder="官方招聘页或岗位链接（可选）">
    <button type="submit">查找岗位</button>
  </form>
  <div id="candidates" class="candidates"></div>
  <p id="task-context" class="task-context" role="status" tabindex="-1">尚未选中任务。可用键盘浏览任务卡片。</p>
  <div class="taskcontrols task-view-controls" aria-label="本地查看位置">
    <button id="remember-task-view" type="button" disabled>记住当前任务</button>
    <button id="forget-task-view" type="button" disabled>忘记查看位置</button>
    <button id="refresh-task-view" type="button" disabled>重新读取查看位置</button>
  </div>
  <p id="saved-task-view" class="task-context" role="status">查看位置尚未读取。未发送输入不会保存。</p>
  <div id="tasks" class="empty" role="region" aria-label="任务列表">正在读取任务…</div>
</aside>
<main>
<header>
  <h1>AI 投递经理</h1>
  <div class="statusbar">
    <div><span class="dot"></span><span id="health">本地服务</span></div>
    <span id="readiness" class="readiness" role="status">正在检查运行条件…</span>
    <button id="profile-setup" class="headerbtn" type="button">资料设置</button>
    <button id="readiness-details" class="headerbtn" type="button">运行条件</button>
    <button id="diagnostics" class="headerbtn" type="button">查看诊断</button>
    <button id="update" class="headerbtn" type="button">更新状态</button>
  </div>
</header>
<div id="chat" class="chat">
  <div class="bubble ai">可按公司和岗位查找官方招聘信息；同名岗位会请你选择。任务控制可在任务卡片操作，私人资料请在任务卡片本地填写。最终提交由你本人完成。</div>
</div>
<div class="composer">
  <textarea id="message" placeholder="查看任务状态；添加岗位请使用左侧表单查找，私人资料请在任务卡片填写…"></textarea>
  <button id="send">发送</button>
</div>
</main>
</div>
<dialog id="profile-dialog" class="diagnostics-dialog" aria-labelledby="profile-title">
  <h2 id="profile-title">资料设置</h2>
  <p>选择个人资料 JSON 文件，内容仅保存在本机，用于之后添加的任务。已有任务继续使用原资料。密码、验证码和 API 密钥请勿放入资料文件；最终提交由你本人完成。</p>
  <button id="profile-editor-open" type="button" disabled>编辑基本资料与简历</button>
  <p id="profile-status" role="status">正在读取设置…</p>
  <label for="profile-file">选择资料文件（JSON，最多 256 KB）</label>
  <input id="profile-file" type="file" accept=".json,application/json" disabled>
  <div class="diagnostics-actions">
    <button id="profile-close" class="headerbtn" type="button">关闭</button>
    <button id="profile-save" type="button" disabled>保存资料</button>
  </div>
</dialog>
<dialog id="profile-editor-dialog" class="diagnostics-dialog profile-editor-dialog" aria-labelledby="profile-editor-title">
  <h2 id="profile-editor-title">基本资料与简历</h2>
  <p><strong>仅用于以后新任务，已有任务仍使用原资料</strong></p>
  <p>这里在本机保存你明确修改的内容，不会发送到招聘网站。已记录不代表资料正确，请本人核对。证件号码不在此展示或编辑。</p>
  <p id="profile-editor-status" role="status"></p>
  <div id="profile-editor-fields" class="profile-editor-fields"></div>
  <p id="profile-editor-resume-status"></p>
  <label for="profile-editor-resume">替换以后新任务的简历（PDF、DOCX 或 DOC）</label>
  <input id="profile-editor-resume" type="file" accept=".pdf,.docx,.doc" disabled>
  <p>文件与资料合计最多 20 MiB。只保存所选文件，不预览或解析；旧简历的解析记录不会自动更新，也不会代你上传到网站。</p>
  <button id="profile-editor-clear-resume" type="button" disabled>撤销本次文件选择</button>
  <details id="profile-editor-version-details" hidden><summary>本机版本信息</summary><p id="profile-editor-version"></p></details>
  <div class="diagnostics-actions">
    <button id="profile-editor-close" class="headerbtn" type="button">关闭</button>
    <button id="profile-editor-reconcile" type="button" hidden disabled>重新读取并核对</button>
    <button id="profile-editor-save" type="button" disabled>保存给以后新任务</button>
  </div>
</dialog>
<dialog id="readiness-dialog" class="diagnostics-dialog" aria-labelledby="readiness-title">
  <h2 id="readiness-title">运行条件</h2>
  <p>这里仅显示本机运行条件的检查结果，不读取或展示个人资料内容。最终提交仍由你本人完成。</p>
  <div id="readiness-summary" class="readiness-summary" role="status">正在检查…</div>
  <ul id="readiness-checks" class="readiness-checks"></ul>
  <p>需要时可读取已配置的模型凭证。此检查不会向模型发送请求，也不会开始或恢复任务；如系统要求新的权限，请自行决定。</p>
  <p id="provider-load-status" role="status"></p>
  <button id="provider-load" type="button" disabled>读取已配置凭证</button>
  <button id="provider-refresh" type="button" disabled>重新读取已配置凭证</button>
  <div class="diagnostics-actions">
    <button id="readiness-close" class="headerbtn" type="button">关闭</button>
  </div>
</dialog>
<dialog id="diagnostics-dialog" class="diagnostics-dialog" aria-labelledby="diagnostics-title">
  <h2 id="diagnostics-title">诊断预览</h2>
  <p>此报告仅显示运行状态和恢复代码。请核对内容，再决定是否复制分享。</p>
  <pre id="diagnostics-report"></pre>
  <div class="diagnostics-actions">
    <button id="diagnostics-close" class="headerbtn" type="button">关闭</button>
    <button id="diagnostics-copy" type="button">复制报告</button>
  </div>
</dialog>
<dialog id="update-dialog" class="diagnostics-dialog" aria-labelledby="update-title">
  <h2 id="update-title">更新状态</h2>
  <p>旧版 Git 更新入口已停用。此页只读取兼容状态，不会安装版本、重启服务或恢复任务。</p>
  <p id="update-observation" role="status">尚未读取更新状态。</p>
  <p>安装包更新仍需完成应用内的安全交接；旧版状态记录不能证明当前安装包已更新或已是最新版本。</p>
  <div class="diagnostics-actions">
    <button id="update-close" class="headerbtn" type="button">关闭</button>
    <button id="update-refresh" type="button">刷新状态</button>
  </div>
</dialog>
<dialog id="preparation-dialog" class="diagnostics-dialog preparation-dialog" aria-labelledby="preparation-title">
  <h2 id="preparation-title">本地准备清单</h2>
  <p>这里只检查当前任务原先绑定的本机资料，不展示个人值、证件号码或文件位置。本机已记录不代表内容正确、已填入网站或已准备好提交。</p>
  <p id="preparation-status" role="status"></p>
  <div id="preparation-observation" hidden>
    <h3>本机资料版本</h3>
    <p id="preparation-profile"></p>
    <p id="preparation-resume"></p>
    <p>资料设置只影响之后添加的任务，不会替换此任务原先绑定的资料。版本摘要只用于区分本机内容。</p>
    <section id="preparation-contract" hidden aria-label="已缓存的公开表单清单">
      <h3>启云方公开表单清单</h3>
      <p>武汉启云方科技有限公司 · 应用实施工程师（武汉）</p>
      <p>以下字段来自 2026-09-30 的公开页面观察缓存，并非当前网站回读。页面星号与必填属性不一致，必填规则尚未核实。</p>
      <ul id="preparation-checks" class="preparation-checks"></ul>
      <p>本机已记录（仍需核对）：只表示发现记录。缺少记录：未发现可用记录。需本人核对：无法据此判断内容是否适用。以上均不证明网站已保存。</p>
      <p>英语两项只检查本地六级记录；缺少记录不代表没有英语证书。CET4、IELTS、TOEFL 等其他证书及当前网站选项需本人核对。</p>
      <p>身份证号码、投递岗位1、投递岗位2、简历上传、验证码、隐私条款及提交操作需本人在网站核对。投递岗位2仅在本人确认需要时填写，不会自动添加。</p>
      <button id="preparation-review-open" type="button">核对拟填写内容（仅本机显示）</button>
      <p id="preparation-review-status" role="status"></p>
      <section id="preparation-review" hidden aria-label="此任务的私密拟填写内容">
        <p>以下是此任务原资料的实际值与明确匹配的官网选项，仅在本机显示。尚未发送到官网，也没有授权填写。不能明确匹配的项目留给本人核对，不会猜测。</p>
        <ul id="preparation-review-fields" class="preparation-checks"></ul>
        <p>勾选要核对的常规资料。只读检查不会发送个人资料；填写前还会检查本机条件并请你确认。身份证、岗位2、验证码、协议和最终提交由本人处理，简历上传单独确认。</p>
        <button id="preparation-site-check" class="preparation-secondary" type="button" disabled>只读核对当前官网（不填写）</button>
        <p id="preparation-site-status" role="status"></p>
        <button id="preparation-native-open" type="button" disabled>检查受控填写条件</button>
        <p id="preparation-native-status" role="status"></p>
        <section id="preparation-native-consent" class="preparation-native-step" hidden aria-label="填写与简历上传分别确认">
          <h3>确认本次填写</h3>
          <p id="preparation-native-warning"></p>
          <label><span id="preparation-fill-choice"></span>我已核对上方勾选的实际值，同意仅将这些常规资料填写到启云方本岗位</label>
          <button id="preparation-fill-approve" type="button" disabled>确认本次填写</button>
          <button id="preparation-resume-review" type="button" hidden disabled>单独核对本次简历上传</button>
          <div id="preparation-resume-consent" class="preparation-native-step" hidden>
            <h3>确认简历上传</h3>
            <p id="preparation-resume-detail"></p>
            <p>仅上传当前任务绑定的原始 PDF 或 DOCX，最多 4 MiB，并遵守官网更低的限制；不转换文件。上传回包不证明附件留存成功。</p>
            <label><span id="preparation-upload-choice"></span>我同意将这份原始简历上传到武汉启云方科技有限公司</label>
            <button id="preparation-upload-approve" type="button" disabled>确认这份简历上传</button>
          </div>
          <button id="preparation-human-review" type="button" hidden disabled>显示官网验证码，进入人工收尾</button>
          <p>以上确认均不授权证件、验证码、协议或最终提交。关闭、隐藏资料或更换任务会结束本次准备，结果不明时不会自动重试。</p>
        </section>
        <button id="preparation-review-hide" class="preparation-secondary" type="button">隐藏个人值</button>
      </section>
      <a id="preparation-source" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">尝试打开已核对的官方页面</a>
      <p>官网地址：<span id="preparation-source-address">https://www.qiyunfang.com/h-col-124.html</span><br>如本机窗口无法打开链接，请在自己的浏览器打开这个官网地址。</p>
    </section>
    <p id="preparation-unmatched" hidden>当前任务与已核对的启云方岗位不完全匹配，因此不展示或套用该站的字段清单与链接。请自行核对任务的官方来源。</p>
    <h3>接下来由本人核对</h3>
    <ol class="preparation-manual">
      <li>先核对当前岗位；若官网要求登录，再由本人核对账号。本应用尚未核验账号或服务端草稿。</li>
      <li>按网站当前要求核对真实资料、必填项及简历版本；需要上传时由本人操作。</li>
      <li>由本人处理验证码、隐私条款和其他确认，再逐项检查网站实际保留的完整内容。</li>
      <li>最终提交始终只能由本人完成。这份清单不是可提交认证，也不会开始、继续或重试任务。</li>
    </ol>
  </div>
  <div class="diagnostics-actions">
    <button id="preparation-close" class="headerbtn" type="button">关闭</button>
    <button id="preparation-refresh" type="button" disabled>刷新检查</button>
  </div>
</dialog>
<div id="toast" class="toast" role="status" aria-live="polite" aria-atomic="true"></div>
<script>
const tasksEl=document.getElementById('tasks'),chat=document.getElementById('chat'),msg=document.getElementById('message'),send=document.getElementById('send'),diagnosticsBtn=document.getElementById('diagnostics'),diagnosticsDialog=document.getElementById('diagnostics-dialog'),diagnosticsReport=document.getElementById('diagnostics-report'),diagnosticsCopy=document.getElementById('diagnostics-copy'),diagnosticsClose=document.getElementById('diagnostics-close'),updateBtn=document.getElementById('update'),toast=document.getElementById('toast');
const readinessBtn=document.getElementById('readiness-details'),readinessDialog=document.getElementById('readiness-dialog'),readinessSummary=document.getElementById('readiness-summary'),readinessChecks=document.getElementById('readiness-checks'),readinessClose=document.getElementById('readiness-close');
const profileBtn=document.getElementById('profile-setup'),profileDialog=document.getElementById('profile-dialog'),
  profileFile=document.getElementById('profile-file'),profileStatus=document.getElementById('profile-status'),
  profileSave=document.getElementById('profile-save'),profileClose=document.getElementById('profile-close');
let profileEpoch=0,profileVersion=null,profileBusy=false;
function validProfileState(data){
  return data&&/^[0-9a-f]{64}$/.test(data.settings_version)
    &&typeof data.profile_selected==='boolean'&&data.submit_capability===false;
}
function profileControls(){
  profileFile.disabled=uiSessionExpired||profileBusy||!profileVersion;
  profileSave.disabled=profileFile.disabled||profileFile.files.length!==1;
  document.getElementById('profile-editor-open').disabled=profileFile.disabled||profileFile.files.length!==0;
}
function clearProfileSelection(){
  profileEpoch++;profileVersion=null;profileBusy=false;profileFile.value='';
  profileStatus.textContent='';profileControls();
}
async function openProfileSetup(){
  if(uiSessionExpired)return;
  clearProfileSelection();
  const epoch=profileEpoch;
  profileStatus.textContent='正在读取设置…';profileDialog.showModal();profileClose.focus();
  try{
    const response=await uiRequest('/ui/api/profile-setup',{credentials:'same-origin'});
    const data=await response.json();
    if(uiSessionExpired||epoch!==profileEpoch||!profileDialog.open)return;
    if(!response.ok||!validProfileState(data))throw new Error();
    profileVersion=data.settings_version;
    profileStatus.textContent=data.profile_selected?'已选择本机资料，可选择新文件供之后的任务使用。':'尚未选择资料，请选择文件并明确保存。';
  }catch(_){
    if(!uiSessionExpired&&epoch===profileEpoch&&profileDialog.open)
      profileStatus.textContent='无法读取设置，请关闭后重新打开；没有修改资料。';
  }finally{if(epoch===profileEpoch)profileControls();}
}
profileBtn.onclick=()=>void openProfileSetup();
function closeProfileSetup(){
  clearProfileSelection();
  if(profileDialog.open)profileDialog.close();
}
profileClose.onclick=closeProfileSetup;
profileDialog.addEventListener('cancel',()=>clearProfileSelection());
profileDialog.addEventListener('close',()=>{
  // close is a queued event; it must not clear a newer, reopened dialog.
  if(profileDialog.open)return;
  clearProfileSelection();
  if(!uiSessionExpired)profileBtn.focus();
});
profileFile.onchange=()=>{profileControls();};
profileSave.onclick=async()=>{
  if(uiSessionExpired||profileBusy||!profileVersion||profileFile.files.length!==1)return;
  const epoch=profileEpoch,version=profileVersion,file=profileFile.files[0];
  if(file.size>256*1024){profileStatus.textContent='资料文件超过 256 KB，请选择完整且符合大小限制的文件。';return;}
  profileBusy=true;profileControls();
  try{
    const text=await file.text();
    if(uiSessionExpired||epoch!==profileEpoch||!profileDialog.open)return;
    const response=await uiRequest('/ui/api/profile-setup',{method:'POST',
      headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({profile_json:text,expected_settings_version:version})});
    const data=await response.json();
    if(uiSessionExpired||epoch!==profileEpoch||!profileDialog.open)return;
    if(!response.ok||!validProfileState(data)||!data.profile_selected)throw new Error();
    const readback=await uiRequest('/ui/api/profile-setup',{credentials:'same-origin'});
    const observed=await readback.json();
    if(uiSessionExpired||epoch!==profileEpoch||!profileDialog.open)return;
    if(!readback.ok||!validProfileState(observed)||!observed.profile_selected
      ||observed.settings_version!==data.settings_version)throw new Error();
    profileVersion=observed.settings_version;profileFile.value='';
    profileStatus.textContent='资料已保存在本机，之后添加的任务将使用这份资料。已有任务保持原资料。';
    void readiness();
  }catch(_){
    if(!uiSessionExpired&&epoch===profileEpoch&&profileDialog.open){
      profileVersion=null;profileFile.value='';
      profileStatus.textContent='资料保存未确认。请关闭后重新打开并检查文件与设置；不会自动重试。';
    }
  }finally{if(epoch===profileEpoch){profileBusy=false;profileControls();}}
};

const editorDialog=document.getElementById('profile-editor-dialog'),editorFields=document.getElementById('profile-editor-fields'),
  editorStatus=document.getElementById('profile-editor-status'),editorResume=document.getElementById('profile-editor-resume'),
  editorResumeStatus=document.getElementById('profile-editor-resume-status'),editorSave=document.getElementById('profile-editor-save'),
  editorClose=document.getElementById('profile-editor-close'),editorReconcile=document.getElementById('profile-editor-reconcile'),
  editorClearResume=document.getElementById('profile-editor-clear-resume'),editorVersion=document.getElementById('profile-editor-version'),
  editorVersionDetails=document.getElementById('profile-editor-version-details');
const editorFieldSpec=[['identity.full_name','姓名','text'],['identity.phone','电话号码','text'],
  ['identity.email','邮箱号码','text'],['identity.gender','性别','text'],['education.highest.degree','最高学历','text'],
  ['education.highest.school','毕业院校','text'],['education.highest.college','学院','text'],
  ['education.highest.major','专业','text'],['education.highest.graduation_date','毕业时间','text'],
  ['language.cet6.level','英语证书情况（本地六级记录）','text'],['language.cet6.score','英语考级分数（本地六级记录）','text_or_number'],
  ['preferences.preferred_cities','期望工作城市（每行一个）','text_list']];
let editorEpoch=0,editorState=null,editorBusy=false,editorUncertain=false;
const editorHash=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
function editorSupported(type,value){
  if(value===null)return true;
  if(type==='text_list')return Array.isArray(value)&&value.length<=32
    &&value.every(v=>typeof v==='string'&&[...v].length<=200&&!/[\u0000-\u001f]/.test(v));
  if(typeof value==='string')return [...value].length<=2048&&!/[\u0000-\u0008\u000b-\u001f]/.test(value);
  return type==='text_or_number'&&typeof value==='number'&&Number.isFinite(value)&&Math.abs(value)<=1e12;
}
function validEditorState(data){
  if(!data||data.schema_version!==1||!editorHash(data.settings_version)
    ||!(data.profile_version===null||editorHash(data.profile_version))
    ||!['canonical','new','legacy'].includes(data.mode)||data.future_tasks_only!==true||data.submit_capability!==false
    ||!['ready','reconciliation_required'].includes(data.admission_status)||!Array.isArray(data.fields)
    ||!data.resume||!['missing','recorded_locally','unsupported'].includes(data.resume.status)
    ||!(data.resume.kind===null||['resume_pdf','resume_docx','resume_doc'].includes(data.resume.kind)))return false;
  if(data.mode==='legacy')return data.fields.length===0;
  return data.fields.length===editorFieldSpec.length&&editorFieldSpec.every(([key,_,type],index)=>{
    const field=data.fields[index];
    if(!field||field.key!==key||field.type!==type||!['missing','supported','unsupported'].includes(field.status)
      ||typeof field.editable!=='boolean')return false;
    if(field.status==='unsupported')return field.editable===false&&field.value===null;
    if(!field.editable)return false;
    return editorSupported(type,field.value);
  });
}
const editorText=(value,type)=>value===null?'':type==='text_list'?value.join('\n'):String(value);
function editorChanges(){
  const edits={};
  if(!editorState)return edits;
  for(const input of editorFields.querySelectorAll('[data-editor-key]')){
    const field=editorState.fields.find(item=>item.key===input.dataset.editorKey);
    if(!field?.editable||input.value===editorText(field.value,field.type))continue;
    edits[field.key]=input.value===''?null:field.type==='text_list'?input.value.split('\n'):input.value;
  }
  return edits;
}
function editorControls(){
  const blocked=uiSessionExpired||editorBusy||editorUncertain||!editorState||editorState.mode==='legacy'
    ||editorState.admission_status!=='ready';
  for(const input of editorFields.querySelectorAll('[data-editor-key]'))
    input.disabled=blocked||input.dataset.editorEditable!=='true';
  editorResume.disabled=blocked;
  editorSave.disabled=blocked||(!Object.keys(editorChanges()).length&&!editorResume.files.length);
  editorClearResume.disabled=blocked||!editorResume.files.length;
  editorReconcile.hidden=!editorUncertain;
  editorReconcile.disabled=uiSessionExpired||editorBusy||!editorUncertain;
}
function clearEditor(){
  editorEpoch++;editorState=null;editorBusy=false;editorUncertain=false;
  editorFields.replaceChildren();editorResume.value='';editorResumeStatus.textContent='';editorStatus.textContent='';
  editorVersion.textContent='';editorVersionDetails.hidden=true;editorVersionDetails.open=false;editorControls();
}
function renderEditor(data){
  editorState=data;editorFields.replaceChildren();editorResume.value='';
  editorUncertain=data.admission_status!=='ready';
  if(data.mode!=='legacy')editorFieldSpec.forEach(([key,label,type],index)=>{
    const field=data.fields[index],multiline=type==='text_list'||(typeof field.value==='string'&&field.value.includes('\n')),
      wrapper=document.createElement('label'),caption=document.createElement('span'),
      input=document.createElement(multiline?'textarea':'input'),note=document.createElement('small');
    wrapper.className='profile-editor-field';caption.textContent=label;
    input.id='profile-editor-field-'+index;input.dataset.editorKey=key;input.dataset.editorEditable=String(field.editable);
    input.autocomplete='off';input.spellcheck=false;if(!multiline)input.type='text';
    input.maxLength=type==='text_list'?12831:4096;
    input.value=editorText(field.value,type);input.addEventListener('input',editorControls);
    note.id=input.id+'-status';input.setAttribute('aria-describedby',note.id);
    note.textContent=field.status==='unsupported'?'此项记录暂不支持在这里修改，原记录将保留；此处不展示。':
      field.status==='missing'?'缺少记录，请本人填写并核对。':'已记录在本机，仍需本人核对。';
    wrapper.append(caption,input,note);editorFields.append(wrapper);
  });
  const kinds={resume_pdf:'PDF',resume_docx:'DOCX',resume_doc:'DOC'};
  editorResumeStatus.textContent=data.resume.status==='recorded_locally'?
    '当前简历：本机已记录 '+(kinds[data.resume.kind]||'文件')+'，内容仍需本人核对。':
    data.resume.status==='missing'?'当前简历：尚无本机记录。':'当前简历记录暂不支持在此核对；未选择替换时原记录保持不变。';
  editorVersion.textContent='资料版本：'+(data.profile_version||'尚未保存')+'；设置版本：'+data.settings_version;
  editorVersionDetails.hidden=false;
  editorStatus.textContent=editorUncertain?'上次保存状态需要核对，请使用“重新读取并核对”；不会自动重试。':
    data.mode==='legacy'?'旧版资料格式暂不支持逐项编辑，请关闭后通过资料 JSON 导入完整的新版资料。原资料没有修改。':
    data.mode==='new'?'尚无资料。填写后明确保存，之后的新任务才会使用。':'已读取本机资料。仅保存你明确修改的项目；清空某项会移除该项记录。';
  editorControls();
}
async function openEditor(){
  if(uiSessionExpired||profileBusy||!profileVersion||profileFile.files.length)return;
  closeProfileSetup();clearEditor();const epoch=editorEpoch;
  editorBusy=true;editorStatus.textContent='正在读取本机资料…';editorDialog.showModal();editorClose.focus();editorControls();
  try{
    const response=await uiRequest('/ui/api/profile-editor',{credentials:'same-origin'}),data=await response.json();
    if(uiSessionExpired||epoch!==editorEpoch||!editorDialog.open)return;
    if(!response.ok||!validEditorState(data))throw new Error();
    renderEditor(data);
  }catch(_){if(!uiSessionExpired&&epoch===editorEpoch&&editorDialog.open)
    editorStatus.textContent='无法读取资料，请关闭后重新打开；没有修改资料。';
  }finally{if(epoch===editorEpoch){editorBusy=false;editorControls();}}
}
document.getElementById('profile-editor-open').onclick=()=>void openEditor();
function closeEditor(){clearEditor();if(editorDialog.open)editorDialog.close();}
editorClose.onclick=closeEditor;
editorDialog.addEventListener('cancel',clearEditor);
editorDialog.addEventListener('close',()=>{if(editorDialog.open)return;clearEditor();if(!uiSessionExpired)profileBtn.focus();});
editorResume.onchange=()=>{
  if(editorResume.files.length)editorStatus.textContent='已选择本次替换文件，尚未保存。请确认文件内容与资料一致后再保存。';
  editorControls();
};
editorClearResume.onclick=()=>{editorResume.value='';editorStatus.textContent='已撤销本次文件选择，原简历记录保持不变。';editorControls();};
function editorUnconfirmed(){
  editorUncertain=true;editorResume.value='';
  editorStatus.textContent='保存尚未确认，可能已经写入本机。请重新读取并核对；不会自动重试，也不会更换已有任务的资料。';
}
editorSave.onclick=async()=>{
  if(editorSave.disabled||uiSessionExpired||!editorState)return;
  const epoch=editorEpoch,state=editorState,edits=editorChanges(),file=editorResume.files[0];
  const extension=file?.name.toLowerCase().match(/\.(pdf|docx|doc)$/)?.[1];
  if(file&&(!extension||!file.size||file.size>=20*1024*1024)){
    editorStatus.textContent='请选择非空 PDF、DOCX 或 DOC 文件，文件与资料合计须小于 20 MiB。';return;
  }
  const kinds={pdf:'resume_pdf',docx:'resume_docx',doc:'resume_doc'};
  const metadata={schema_version:1,expected_settings_version:state.settings_version,expected_profile_version:state.profile_version,
    edits,resume_action:file?'replace':'keep',resume_kind:file?kinds[extension]:null};
  if(Object.entries(edits).some(([key,value])=>!editorSupported(editorFieldSpec.find(item=>item[0]===key)[2],value))){
    editorStatus.textContent='请检查字段格式：每项最多 2048 个字符，城市最多 32 行且每行最多 200 个字符。';return;
  }
  const encoded=JSON.stringify(metadata);
  if(new TextEncoder().encode(encoded).length>32*1024){editorStatus.textContent='本次资料内容过长，请减少内容后重新检查。';return;}
  editorBusy=true;editorControls();let posted=false;
  try{
    const form=new FormData();form.append('metadata',new Blob([encoded],{type:'application/json'}),'metadata.json');
    if(file)form.append('resume',file.slice(0,file.size,'application/octet-stream'),'resume.'+extension);
    const envelope=new Response(form),contentType=envelope.headers.get('Content-Type'),body=await envelope.arrayBuffer();
    if(uiSessionExpired||epoch!==editorEpoch||!editorDialog.open)return;
    if(body.byteLength>20*1024*1024){editorStatus.textContent='文件与资料合计超过 20 MiB，请选择较小文件后再保存。';return;}
    posted=true;
    const response=await uiRequest('/ui/api/profile-editor',{method:'POST',credentials:'same-origin',headers:{'Content-Type':contentType},body});
    const data=await response.json();
    if(uiSessionExpired||epoch!==editorEpoch||!editorDialog.open)return;
    if(response.status===400&&data.error==='invalid_request'){
      editorStatus.textContent='本次请求格式被拒绝，资料未保存。请检查资料大小、字段格式、城市列表及文件后再决定是否保存。';return;
    }
    if(response.status===409&&data.error==='state_conflict'){
      editorUncertain=true;editorResume.value='';
      editorStatus.textContent='本次保存因资料版本变化或暂不可用而被拒绝。请重新读取并核对，再决定是否修改；不会自动重试。';return;
    }
    if(!response.ok||data.save_status!=='saved'||!validEditorState(data))throw new Error();
    const readback=await uiRequest('/ui/api/profile-editor',{credentials:'same-origin'}),observed=await readback.json();
    if(uiSessionExpired||epoch!==editorEpoch||!editorDialog.open)return;
    if(!readback.ok||!validEditorState(observed)||observed.settings_version!==data.settings_version
      ||observed.profile_version!==data.profile_version||observed.admission_status!=='ready')throw new Error();
    renderEditor(observed);editorStatus.textContent='已保存在本机并重新读取确认。仅用于以后新任务，已有任务仍使用原资料。';void readiness();
  }catch(_){if(!uiSessionExpired&&epoch===editorEpoch&&editorDialog.open){
    if(posted)editorUnconfirmed();else editorStatus.textContent='无法准备本次保存，尚未发送保存请求。请检查资料与文件后再试。';
  }}finally{if(epoch===editorEpoch){editorBusy=false;editorControls();}}
};
editorReconcile.onclick=async()=>{
  if(editorReconcile.disabled||!editorUncertain||uiSessionExpired)return;
  const epoch=editorEpoch;editorBusy=true;editorControls();
  try{
    const response=await uiRequest('/ui/api/profile-editor/reconcile',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},body:'{}'}),data=await response.json();
    if(uiSessionExpired||epoch!==editorEpoch||!editorDialog.open)return;
    if(!response.ok||data.reconciliation_status!=='reconciled'||!validEditorState(data)||data.admission_status!=='ready')throw new Error();
    renderEditor(data);editorStatus.textContent='已重新读取本机当前记录。请逐项核对后再决定是否修改；刚才未确认的修改不会自动重试。';
  }catch(_){if(!uiSessionExpired&&epoch===editorEpoch&&editorDialog.open)
    editorStatus.textContent='当前记录仍未确认，请关闭并重新打开应用后检查。不会自动重试保存。';
  }finally{if(epoch===editorEpoch){editorBusy=false;editorControls();}}
};

const newTaskForm=document.getElementById('newtask');
const candidatesEl=document.getElementById('candidates');let pendingDiscovery=null;
const recoveryObservations=new Map();
const providerLoad=document.getElementById('provider-load'),providerLoadStatus=document.getElementById('provider-load-status');
let providerLoadBusy=false,providerLoadUnknown=false,providerLoadState='unknown',providerLoadEpoch=0;
const providerRefresh=document.getElementById('provider-refresh');
let providerRefreshBusy=false,providerRefreshUnknown=false,providerRefreshObservation=null;
const updateDialog=document.getElementById('update-dialog'),
  updateObservation=document.getElementById('update-observation'),
  updateClose=document.getElementById('update-close'),updateRefresh=document.getElementById('update-refresh');
let updateEpoch=0,updateReadBusy=false,legacyUpdateObservation={status:'idle'};
let uiSessionExpired=false;
let currentTaskId=null;
const preparationDialog=document.getElementById('preparation-dialog'),
  preparationStatus=document.getElementById('preparation-status'),
  preparationObservation=document.getElementById('preparation-observation'),
  preparationProfile=document.getElementById('preparation-profile'),
  preparationResume=document.getElementById('preparation-resume'),
  preparationContract=document.getElementById('preparation-contract'),
  preparationChecks=document.getElementById('preparation-checks'),
  preparationUnmatched=document.getElementById('preparation-unmatched'),
  preparationSource=document.getElementById('preparation-source'),
  preparationClose=document.getElementById('preparation-close'),
  preparationRefresh=document.getElementById('preparation-refresh');
const preparationOfficialSource='https://www.qiyunfang.com/h-col-124.html';
const preparationFieldLabels={
  'identity.full_name':'姓名','identity.phone':'电话号码','identity.email':'邮箱号码',
  'identity.gender':'性别','education.highest.degree':'最高学历',
  'education.highest.school':'毕业院校','education.highest.college':'学院',
  'education.highest.major':'专业','education.highest.graduation_date':'毕业时间',
  'language.cet6.level':'英语证书情况（本地六级记录）','language.cet6.score':'英语考级分数（本地六级记录）',
  'preferences.preferred_cities':'期望工作城市'
};
const preparationStatusLabels={recorded_locally:'本机已记录（仍需核对）',missing:'缺少记录',needs_review:'需本人核对'};
let preparationEpoch=0,preparationBinding=null,preparationBusy=false,preparationStale=false;
let preparationTaskRevisions=new Map();
function clearPreparationContent(){
  preparationObservation.hidden=true;preparationContract.hidden=true;preparationUnmatched.hidden=true;
  preparationProfile.textContent='';preparationResume.textContent='';preparationChecks.replaceChildren();
  preparationSource.removeAttribute('href');preparationStatus.textContent='';
  clearPrivatePreparation();
}
let privatePreparationEpoch=0;
const privatePreparation=document.getElementById('preparation-review'),
  privatePreparationFields=document.getElementById('preparation-review-fields'),
  privatePreparationStatus=document.getElementById('preparation-review-status'),
  privatePreparationOpen=document.getElementById('preparation-review-open');
const preparationSiteCheck=document.getElementById('preparation-site-check'),
  preparationSiteStatus=document.getElementById('preparation-site-status');
let preparationSiteRequest=null,preparationSiteTimer=null,preparationSitePoll=null,privatePreparationValues=null;
const preparationRetirements=new Set();
function trackPreparationRetirement(promise){
  preparationRetirements.add(promise);
  promise.finally(()=>preparationRetirements.delete(promise));
  return promise;
}
function cancelSitePreflight(){
  const request=preparationSiteRequest;preparationSiteRequest=null;
  if(preparationSiteTimer)clearTimeout(preparationSiteTimer);preparationSiteTimer=null;
  if(preparationSitePoll)clearTimeout(preparationSitePoll);preparationSitePoll=null;
  preparationSiteCheck.disabled=true;
  if(!request)return Promise.resolve(true);
  return trackPreparationRetirement(uiRequest('/ui/api/preparation-session/cancel',{method:'POST',credentials:'same-origin',cache:'no-store',keepalive:true,
    headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:request.request_id,task_id:request.task_id,
      expected_revision:request.expected_revision})}).then(async response=>{
        const result=await response.json();
        return response.ok&&result.status==='CANCELLATION_REQUESTED'&&result.submit_capability===false;
      }).catch(()=>false));
}
function clearPrivatePreparation(){
  cancelNativePreparation();
  cancelSitePreflight();preparationSiteStatus.textContent='';
  privatePreparationValues=null;
  privatePreparationEpoch++;privatePreparation.hidden=true;privatePreparationFields.replaceChildren();
  privatePreparationStatus.textContent='';privatePreparationOpen.disabled=false;
}
document.getElementById('preparation-review-hide').onclick=clearPrivatePreparation;
window.addEventListener('pagehide',()=>closePreparationDialog(false));
privatePreparationOpen.onclick=async()=>{
  if(!preparationBinding||preparationBusy||preparationStale||uiSessionExpired||privatePreparationOpen.disabled)return;
  clearPrivatePreparation();
  const epoch=preparationEpoch,binding=preparationBinding,privateEpoch=privatePreparationEpoch;
  privatePreparationOpen.disabled=true;privatePreparationStatus.textContent='正在读取此任务原资料的拟填写内容…';
  try{
    const response=await uiRequest('/ui/api/preparation-review?task_id='+encodeURIComponent(binding.taskId)
      +'&expected_revision='+binding.revision,{credentials:'same-origin',cache:'no-store'});
    const data=await response.json();
    if(!preparationIsCurrent(epoch,binding)||privateEpoch!==privatePreparationEpoch)return;
    const labels={'0':'姓名','4':'电话号码','5':'邮箱号码','2':'性别','14':'最高学历',
      '6':'毕业院校','20':'学院','7':'专业','19':'毕业时间','15':'英语证书情况','16':'英语考级分数',
      '8':'投递岗位1','17':'期望工作城市'};
    if(!response.ok||data?.mode!=='PRIVATE_MAPPING_REVIEW'||data.state!=='AWAITING_LIVE_PREFLIGHT'
      ||data.task_id!==binding.taskId||data.task_revision!==binding.revision
      ||data.contract_version!=='qiyunfang-public-form-2026-10-01-v1'||data.observed_at!=='2026-10-01'
      ||typeof data.profile_version!=='string'||!/^[a-f0-9]{64}$/.test(data.profile_version)
      ||!(data.resume_version===null||typeof data.resume_version==='string'&&/^[a-f0-9]{64}$/.test(data.resume_version))
      ||!data.capabilities||!['live_write','submit','account_verified','server_draft_verified'].every(key=>data.capabilities[key]===false)
      ||!Array.isArray(data.proposals)||data.proposals.length!==Object.keys(labels).length
      ||new Set(data.proposals.map(item=>item?.field_id)).size!==data.proposals.length
      ||data.proposals.some(item=>!item||!Object.hasOwn(labels,item.field_id)
        ||!['proposed','manual','missing'].includes(item.status)||item.requires_explicit_selection!==true
        ||(item.status==='proposed'?!(typeof item.value==='string'&&item.value.length>0&&item.value.length<=100
          ||item.field_id==='17'&&Array.isArray(item.value)&&item.value.length>0&&item.value.length<=3
          &&item.value.every(value=>['武汉','深圳','成都'].includes(value))):item.value!==null)))throw new Error();
    for(const item of data.proposals){
      const row=document.createElement('li'),label=document.createElement(item.status==='proposed'?'label':'span'),value=document.createElement('strong');
      label.textContent=labels[item.field_id];
      value.textContent=item.status==='proposed'?(Array.isArray(item.value)?item.value.join('、'):item.value):
        item.status==='missing'?'缺少记录，须本人填写':'无法明确匹配，须本人核对';
      if(item.status==='proposed'){
        const choice=document.createElement('input');choice.type='checkbox';choice.dataset.fieldId=item.field_id;
        choice.setAttribute('aria-label','选择核对'+labels[item.field_id]);
        choice.onchange=()=>{if(preparationSiteRequest){cancelSitePreflight();preparationSiteStatus.textContent='选择已变化，请重新核对官网。';}
          cancelNativePreparation();
          preparationSiteCheck.disabled=!privatePreparationFields.querySelector('input:checked');
          nativeOpen.disabled=preparationSiteCheck.disabled;};
        label.prepend(choice);
      }
      row.append(label,value);privatePreparationFields.append(row);
    }
    privatePreparationValues=data;
    privatePreparation.hidden=false;privatePreparationStatus.textContent='已显示本机拟填写值；未操作官网。';
  }catch(_){
    if(preparationIsCurrent(epoch,binding)&&privateEpoch===privatePreparationEpoch){
      clearPrivatePreparation();privatePreparationStatus.textContent='无法读取或资料已变化。未操作官网，请关闭后重新检查。';
    }
  }finally{
    if(preparationIsCurrent(epoch,binding)&&privateEpoch===privatePreparationEpoch)privatePreparationOpen.disabled=false;
  }
};
preparationSiteCheck.onclick=async()=>{
  if(!preparationBinding||!privatePreparationValues||privatePreparation.hidden||uiSessionExpired||preparationSiteCheck.disabled)return;
  cancelNativePreparation();nativeOpen.disabled=true;
  const selected=[...privatePreparationFields.querySelectorAll('input:checked')].map(item=>item.dataset.fieldId);
  if(!selected.length)return;
  const epoch=preparationEpoch,privateEpoch=privatePreparationEpoch,binding=preparationBinding;
  const expectedPlan=privatePreparationValues.proposals.filter(item=>selected.includes(item.field_id))
    .map(item=>({field_id:item.field_id,value:item.value}));
  const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
  const request={request_id:[...bytes].map(value=>value.toString(16).padStart(2,'0')).join(''),
    task_id:binding.taskId,expected_revision:binding.revision,selected_ids:selected,
    profile_version:privatePreparationValues.profile_version,resume_version:privatePreparationValues.resume_version};
  preparationSiteRequest=request;preparationSiteCheck.disabled=true;
  preparationSiteStatus.textContent='正在本机后台只读核对官网；不会填入、上传或提交资料…';
  const current=()=>preparationSiteRequest===request&&preparationIsCurrent(epoch,binding)&&privateEpoch===privatePreparationEpoch;
  try{
    const response=await uiRequest('/ui/api/preparation-session/open',{method:'POST',credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    const result=await response.json();
    if(!current())return;
    if(!response.ok||result.mode!=='READ_ONLY_PUBLIC_PREFLIGHT'||result.status!=='EMPTY_FORM_VERIFIED'
      ||result.request_id!==request.request_id||result.task_id!==binding.taskId||result.task_revision!==binding.revision
      ||result.source_url!=='https://www.qiyunfang.com/h-col-124.html'||!Array.isArray(result.plan)
      ||result.plan.length!==selected.length||!result.capabilities
      ||result.profile_version!==request.profile_version||result.resume_version!==request.resume_version
      ||result.plan.some((item,index)=>!item||Object.keys(item).sort().join(',')!=='field_id,value'
        ||item.field_id!==expectedPlan[index].field_id||JSON.stringify(item.value)!==JSON.stringify(expectedPlan[index].value))
      ||!['live_write','submit','account_verified','server_draft_verified'].every(key=>result.capabilities[key]===false)
      ||!Number.isInteger(result.expires_in_seconds)||result.expires_in_seconds<1||result.expires_in_seconds>120)throw new Error();
    preparationSiteStatus.textContent='当前官网表单已通过只读核对，本轮没有填写。自动填写仍等待本机验收；关闭或隐藏个人值会结束这次检查。';
    nativeOpen.disabled=false;
    preparationSiteTimer=setTimeout(()=>{if(current()){clearPrivatePreparation();privatePreparationStatus.textContent='只读核对已到期，已隐藏个人值。';}},result.expires_in_seconds*1000);
    const poll=async()=>{
      if(!current())return;
      try{
        const response=await uiRequest('/ui/api/preparation-session/status',{method:'POST',credentials:'same-origin',cache:'no-store',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:request.request_id,task_id:request.task_id,
            expected_revision:request.expected_revision})});
        const state=await response.json();if(!current())return;
        if(!response.ok||state.status!=='OFFERED'||state.live_write_available!==false)throw new Error();
        preparationSitePoll=setTimeout(poll,1000);
      }catch(_){if(current()){clearPrivatePreparation();privatePreparationStatus.textContent='只读检查已结束或状态无法确认，已隐藏个人值。';}}
    };
    preparationSitePoll=setTimeout(poll,1000);
  }catch(_){
    if(current()){clearPrivatePreparation();privatePreparationStatus.textContent='资料或官网状态无法核对，已隐藏个人值，没有填写。前次浏览器未确认关闭时不会另开，请稍后重新检查。';}
  }
};
const nativeOpen=document.getElementById('preparation-native-open'),nativeStatus=document.getElementById('preparation-native-status'),
  nativeConsent=document.getElementById('preparation-native-consent'),nativeWarning=document.getElementById('preparation-native-warning'),
  fillConsent=makeNativeChoice('preparation-fill-consent'),fillApprove=document.getElementById('preparation-fill-approve'),
  resumeReview=document.getElementById('preparation-resume-review'),resumeConsent=document.getElementById('preparation-resume-consent'),
  resumeDetail=document.getElementById('preparation-resume-detail'),uploadConsent=makeNativeChoice('preparation-upload-consent'),
  uploadApprove=document.getElementById('preparation-upload-approve'),
  humanReview=document.getElementById('preparation-human-review');
function makeNativeChoice(id){const input=document.createElement('input');input.type='checkbox';input.id=id;return input;}
let nativeRequest=null,nativeOffer=null,nativeUploadOffer=null,nativePoll=null,nativeExpiry=null,nativeBusy=false;
function nativeBase(request){return {request_id:request.request_id,task_id:request.task_id,expected_revision:request.expected_revision};}
function cancelNativePreparation(){
  const request=nativeRequest;nativeRequest=nativeOffer=nativeUploadOffer=null;nativeBusy=false;
  if(nativePoll)clearTimeout(nativePoll);if(nativeExpiry)clearTimeout(nativeExpiry);nativePoll=nativeExpiry=null;
  nativeConsent.hidden=true;resumeConsent.hidden=true;resumeReview.hidden=true;humanReview.hidden=true;humanReview.disabled=true;fillConsent.remove();uploadConsent.remove();
  fillConsent.checked=uploadConsent.checked=false;fillConsent.disabled=uploadConsent.disabled=false;
  fillApprove.disabled=uploadApprove.disabled=resumeReview.disabled=nativeOpen.disabled=true;
  nativeWarning.textContent=resumeDetail.textContent=nativeStatus.textContent='';
  if(!request)return Promise.resolve(true);
  return trackPreparationRetirement(uiRequest('/ui/api/native-preparation/cancel',{method:'POST',credentials:'same-origin',cache:'no-store',keepalive:true,
    headers:{'Content-Type':'application/json'},body:JSON.stringify(nativeBase(request))}).then(async response=>{
      const result=await response.json();
      return response.ok&&result.status==='CANCELLATION_REQUESTED'&&result.submit_capability===false;
    }).catch(()=>false));
}
async function nativeCall(action,request,extra={}){
  const response=await uiRequest('/ui/api/native-preparation/'+action,{method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({...nativeBase(request),...extra})});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error==='native_preparation_runtime_not_admitted'?'native-unavailable':'state-conflict');
  return result;
}
function nativeCurrent(request){return nativeRequest===request&&!uiSessionExpired&&preparationDialog.open
  &&request.epoch===preparationEpoch&&preparationBinding===request.binding&&currentTaskId===request.task_id
  &&(request.fillPending===true||preparationTaskRevisions.get(request.task_id)===request.binding.revision)
  &&request.privateEpoch===privatePreparationEpoch&&!privatePreparation.hidden;}
function nativeFail(request,message){if(nativeCurrent(request)){clearPrivatePreparation();privatePreparationStatus.textContent=message;}}
function nativeDeadline(request,seconds){
  if(nativeExpiry)clearTimeout(nativeExpiry);
  nativeExpiry=setTimeout(()=>nativeFail(request,'本次私密确认已到期，个人值已隐藏；没有自动重试。'),seconds*1000);
}
function nativeWatch(request){
  if(nativePoll)clearTimeout(nativePoll);
  nativePoll=setTimeout(async()=>{
    if(!nativeCurrent(request))return;
    try{
      const state=await nativeCall('status',request);if(!nativeCurrent(request))return;
      if(state.submit_capability===false&&state.status==='HUMAN_RESULT'&&['RETURNED_UNVERIFIED','UNKNOWN_OUTCOME'].includes(state.final_status)){
        nativeStatus.textContent=state.final_status==='RETURNED_UNVERIFIED'
          ?'本人确认的请求已返回，但不代表申请成功。请在原生浏览器核对官网结果；窗口仅保留查看，不能再次发送，最迟五分钟后关闭。'
          :'请求结果无法确认。请仅在原生浏览器核对，不要重复发送；窗口最迟五分钟后关闭。';
        nativeWatch(request);return;
      }
      if(state.submit_capability===false&&['RETURNED_UNVERIFIED','UNKNOWN_OUTCOME','CANCELLED'].includes(state.final_status)){
        clearPrivatePreparation();privatePreparationStatus.textContent=state.final_status==='RETURNED_UNVERIFIED'
          ?'本人确认的请求已返回，但申请是否成功尚未核验；不会自动再次发送。'
          :'人工收尾已取消或结果无法确认；没有自动重试，请本人核对官网。';return;
      }
      if(state.submit_capability!==false||!['OFFERED','PREPARING','PREPARED_UNVERIFIED','REVIEWING_RESUME','RESUME_OFFERED','UPLOADING_RESUME','STARTING_HUMAN_REVIEW','HUMAN_REVIEW','HUMAN_RESULT'].includes(state.status))throw new Error();
      nativeWatch(request);
    }catch(_){nativeFail(request,'准备状态已结束或无法确认，个人值已隐藏；不要据此重复填写或上传。');}
  },1000);
}
function validNativeOffer(result,request,selected){
  return result.mode==='PRIVATE_NATIVE_FILL_OFFER'&&result.request_id===request.request_id
    &&result.task_id===request.task_id&&result.task_revision===request.expected_revision
    &&result.source_url===preparationOfficialSource&&result.profile_version===privatePreparationValues.profile_version
    &&result.resume_version===privatePreparationValues.resume_version&&result.submit_capability===false
    &&typeof result.nonce==='string'&&/^[A-Za-z0-9_-]{32,128}$/.test(result.nonce)
    &&typeof result.scope_sha==='string'&&/^[a-f0-9]{64}$/.test(result.scope_sha)
    &&Number.isInteger(result.expires_in_seconds)&&result.expires_in_seconds>0&&result.expires_in_seconds<=120
    &&JSON.stringify(result.plan)===JSON.stringify(selected);
}
async function waitPreparationRetirement(retirement){
  let timer;
  try{return await Promise.race([retirement,new Promise(resolve=>{
    timer=setTimeout(()=>resolve([false]),10000);
  })]);}finally{clearTimeout(timer);}
}
nativeOpen.onclick=async()=>{
  if(nativeOpen.disabled||!preparationBinding||!privatePreparationValues||privatePreparation.hidden||uiSessionExpired)return;
  cancelNativePreparation();cancelSitePreflight();
  const retirement=Promise.all([...preparationRetirements]);
  const selectedIds=[...privatePreparationFields.querySelectorAll('input:checked')].map(input=>input.dataset.fieldId);
  const selected=privatePreparationValues.proposals.filter(item=>selectedIds.includes(item.field_id)).map(item=>({field_id:item.field_id,value:item.value}));
  if(!selectedIds.length||selected.length!==selectedIds.length)return;
  const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
  const request={request_id:[...bytes].map(byte=>byte.toString(16).padStart(2,'0')).join(''),task_id:preparationBinding.taskId,
    expected_revision:preparationBinding.revision,binding:preparationBinding,epoch:preparationEpoch,privateEpoch:privatePreparationEpoch};
  nativeRequest=request;nativeStatus.textContent='正在结束前次检查并准备本机原生浏览器；尚未授权填写…';
  try{
    const retired=await waitPreparationRetirement(retirement);
    if(!nativeCurrent(request))return;
    if(retired.some(closed=>closed!==true))throw new Error();
    const result=await nativeCall('open',request,{selected_ids:selectedIds,profile_version:privatePreparationValues.profile_version,
      resume_version:privatePreparationValues.resume_version});
    if(!nativeCurrent(request))return;if(!validNativeOffer(result,request,selected))throw new Error();
    nativeOffer={nonce:result.nonce,scope_sha:result.scope_sha};
    document.getElementById('preparation-fill-choice').append(fillConsent);nativeConsent.hidden=false;
    nativeWarning.textContent='请再次核对上方勾选的实际值。确认后会向武汉启云方科技有限公司的应用实施工程师（武汉）表单填写这些常规资料；输入本身可能传送资料。这不是最终提交。';
    nativeStatus.textContent='条件与当前表单已核对，等待你单独确认本次填写。';
    nativeDeadline(request,result.expires_in_seconds);nativeWatch(request);
  }catch(error){nativeFail(request,error.message==='native-unavailable'?'本机浏览器安全检查未通过，未填写或上传；只读检查仍可使用。':'条件或资料无法确认，未自动重试。请重新核对准备状态。');}
};
fillConsent.onchange=()=>{fillApprove.disabled=!fillConsent.checked||!nativeOffer||nativeBusy;};
fillApprove.onclick=async()=>{
  const request=nativeRequest,offer=nativeOffer;
  if(!request||!offer||!nativeCurrent(request)||nativeBusy||!fillConsent.checked||fillApprove.disabled)return;
  nativeBusy=true;request.fillPending=true;nativeOffer=null;fillApprove.disabled=fillConsent.disabled=true;
  nativeStatus.textContent='正在执行这一份已确认的填写计划…';
  try{
    const result=await nativeCall('approve-fill',request,{...offer,approve_transmission:true});
    if(!nativeCurrent(request))return;
    if(result.status!=='PREPARED_UNVERIFIED'||result.submit_capability!==false||result.server_draft_verified!==false
      ||!Number.isSafeInteger(result.task_revision)||result.task_revision<=request.expected_revision
      ||!Number.isSafeInteger(preparationTaskRevisions.get(request.task_id))
      ||preparationTaskRevisions.get(request.task_id)>result.task_revision)throw new Error();
    request.binding.revision=result.task_revision;preparationTaskRevisions.set(request.task_id,result.task_revision);
    request.fillPending=false;nativeBusy=false;fillConsent.checked=false;resumeReview.hidden=false;resumeReview.disabled=false;humanReview.hidden=false;humanReview.disabled=false;
    nativeStatus.textContent='已执行本次填写；尚未核实网站草稿、附件留存或申请提交。简历上传需要下面的单独确认。';
    nativeDeadline(request,900);
  }catch(_){nativeFail(request,'本次填写结果无法确认，个人值已隐藏；不会自动重试，也不能当作已提交。');}
};
resumeReview.onclick=async()=>{
  const request=nativeRequest;if(!request||!nativeCurrent(request)||nativeBusy||resumeReview.disabled)return;
  nativeBusy=true;resumeReview.disabled=true;
  try{
    const result=await nativeCall('review-resume',request);if(!nativeCurrent(request))return;
    const material=result.resume;
    if(result.submit_capability!==false||result.recipient!=='https://www.qiyunfang.com/ajax/advanceUpload.jsp'
      ||typeof result.nonce!=='string'||!/^[A-Za-z0-9_-]{32,128}$/.test(result.nonce)
      ||typeof result.scope_sha!=='string'||!/^[a-f0-9]{64}$/.test(result.scope_sha)
      ||!material||material.resume_sha256!==privatePreparationValues.resume_version
      ||!['resume_pdf','resume_docx'].includes(material.kind)||!Number.isInteger(material.byte_count)
      ||material.byte_count<1||material.byte_count>4*1024*1024
      ||material.destination_filename!==(material.kind==='resume_pdf'?'resume.pdf':'resume.docx')
      ||!Number.isInteger(result.expires_in_seconds)||result.expires_in_seconds<1||result.expires_in_seconds>120)throw new Error();
    nativeBusy=false;nativeUploadOffer={nonce:result.nonce,scope_sha:result.scope_sha};
    document.getElementById('preparation-upload-choice').append(uploadConsent);resumeConsent.hidden=false;
    resumeDetail.textContent='接收方：武汉启云方科技有限公司；原文件类型：'+(material.kind==='resume_pdf'?'PDF':'DOCX')
      +'；大小：'+material.byte_count+' 字节；发送文件名：'+material.destination_filename+'；本机版本：'+material.resume_sha256;
    nativeDeadline(request,result.expires_in_seconds);
  }catch(_){nativeFail(request,'无法确认这份原始简历与上传条件，未自动重试；没有转换或替换简历。');}
};
uploadConsent.onchange=()=>{uploadApprove.disabled=!uploadConsent.checked||!nativeUploadOffer||nativeBusy;};
uploadApprove.onclick=async()=>{
  const request=nativeRequest,offer=nativeUploadOffer;
  if(!request||!offer||!nativeCurrent(request)||nativeBusy||!uploadConsent.checked||uploadApprove.disabled)return;
  nativeBusy=true;nativeUploadOffer=null;uploadApprove.disabled=true;uploadConsent.disabled=true;
  nativeStatus.textContent='正在发送这份单独确认的原始简历…';
  try{
    const result=await nativeCall('approve-resume',request,{...offer,approve_upload:true});if(!nativeCurrent(request))return;
    if(result.status!=='RETURNED_UNVERIFIED'||result.server_attachment_verified!==false||result.automatic_retry!==false||result.submit_capability!==false)throw new Error();
    nativeBusy=false;uploadConsent.checked=false;
    nativeStatus.textContent='上传请求已返回，但网站是否实际保留附件尚未验证。没有最终提交，不能当作投递成功；不会自动再次上传。';
  }catch(_){nativeFail(request,'上传结果无法确认，个人值已隐藏；可能已经发送，不会自动重传或最终提交。');}
};

humanReview.onclick=async()=>{
  const request=nativeRequest;
  if(!request||!nativeCurrent(request)||nativeBusy||humanReview.disabled)return;
  nativeBusy=true;humanReview.disabled=true;
  try{
    const result=await nativeCall('begin-human-review',request);if(!nativeCurrent(request))return;
    if(result.submit_capability!==false||result.automatic_retry!==false||result.server_application_verified!==false
      ||!['UNAVAILABLE','MANUAL_REVIEW_ACTIVE'].includes(result.status))throw new Error();
    nativeBusy=false;
    if(result.status==='UNAVAILABLE'){
      nativeStatus.textContent='当前原生窗口不具备人工收尾条件；本窗口不会发送最终申请。证件、验证码、协议和最终提交仍由本人处理，填写或上传完成不代表已投递。';
    }else{
      resumeReview.disabled=uploadApprove.disabled=true;
      nativeStatus.textContent='请在原生浏览器亲自核对资料并处理证件、显示的官网验证码及协议。只有你在原生确认框最后确认，才发送这一份请求；应用不会代点确认。不要刷新验证码或重复提交；取消或关闭会结束本次收尾，不会自动重试。';
    }
  }catch(_){nativeFail(request,'人工收尾条件无法确认；不会发送最终申请或自动重试。');}
};

function clearPreparationObservation(){
  preparationEpoch++;preparationBinding=null;preparationBusy=false;preparationStale=false;
  clearPreparationContent();preparationRefresh.disabled=true;
}
function preparationTrigger(taskId){
  return [...tasksEl.querySelectorAll('[data-task-preparation]')].find(button=>button.dataset.task===taskId);
}
function closePreparationDialog(restoreFocus=true){
  const taskId=preparationBinding?.taskId;
  clearPreparationObservation();
  if(preparationDialog.open)preparationDialog.close();
  if(restoreFocus&&!uiSessionExpired)(preparationTrigger(taskId)||taskContext).focus();
}
function reconcilePreparationTasks(tasks){
  // Task revisions only increase; late state reads cannot undo a witnessed native handover.
  preparationTaskRevisions=new Map((tasks||[]).map(task=>[task.task_id,
    Number.isSafeInteger(task.revision)?Math.max(task.revision,preparationTaskRevisions.get(task.task_id)||0):task.revision]));
  // Unsent answers can deliberately hold the card DOM at its old revision.
  // Refresh only this read-only trigger; never rebind its mutation controls.
  for(const button of tasksEl.querySelectorAll('[data-task-preparation]')){
    const revision=preparationTaskRevisions.get(button.dataset.task);
    button.disabled=!Number.isSafeInteger(revision)||revision<0;
    if(!button.disabled)button.dataset.revision=String(revision);
  }
  if(preparationBinding&&((preparationTaskRevisions.get(preparationBinding.taskId)!==preparationBinding.revision
      &&!(nativeRequest?.fillPending===true&&nativeRequest.binding===preparationBinding))
      ||currentTaskId!==preparationBinding.taskId)){
    closePreparationDialog();notify('任务已变化，本地准备清单已清除；请从任务卡片重新查看。');
  }
}
function preparationIsCurrent(epoch,binding){
  return !uiSessionExpired&&preparationDialog.open&&epoch===preparationEpoch
    &&preparationBinding===binding&&currentTaskId===binding.taskId
    &&preparationTaskRevisions.get(binding.taskId)===binding.revision;
}
function validPreparation(data,binding){
  const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const contract=data?.contract,profile=data?.profile,resume=data?.resume,caps=data?.capabilities;
  if(!data||data.task_id!==binding.taskId||data.task_revision!==binding.revision
    ||data.mode!=='LOCAL_PREPARATION_ONLY'||!caps
    ||!['live_write','submit','account_verified','server_draft_verified'].every(key=>caps[key]===false)
    ||!profile||!['available','unavailable'].includes(profile.status)
    ||(profile.status==='available'?!digest(profile.version):profile.version!==null)
    ||!resume||!['local_version_matches','local_version_differs','unverified','missing','unavailable'].includes(resume.status)
    ||(['missing','unavailable'].includes(resume.status)?resume.version!==null:!digest(resume.version))
    ||!contract||typeof contract.matched!=='boolean'||contract.requiredness!=='UNVERIFIED'
    ||!Array.isArray(data.items))return false;
  if(!contract.matched)return contract.id===null&&contract.source_url===null&&contract.observed_at===null
    &&contract.coverage==='unavailable'&&contract.freshness==='unavailable'&&data.items.length===0;
  const keys=data.items.map(item=>item?.key);
  return contract.id==='qiyunfang-wuhan-implementation-v1'&&contract.source_url===preparationOfficialSource
    &&contract.observed_at==='2026-09-30'&&contract.coverage==='observed_fields_only'
    &&contract.freshness==='cached_observation'&&keys.length===Object.keys(preparationFieldLabels).length
    &&new Set(keys).size===keys.length&&data.items.every(item=>Object.hasOwn(preparationFieldLabels,item.key)
      &&Object.hasOwn(preparationStatusLabels,item.status));
}
function renderPreparation(data){
  // Labels, notes, manual instructions and hrefs never come from response text.
  // Only admitted enum values and validated, shortened content digests render.
  preparationProfile.textContent=data.profile.status==='available'?
    '此任务绑定的资料：本机可读取。当前版本摘要前 12 位：'+data.profile.version.slice(0,12)+'。':
    '此任务绑定的资料：暂不可读取，需本人核对。';
  const resumeLabels={local_version_matches:'本机简历与资料记录的版本一致，仍需本人核对内容。',
    local_version_differs:'本机简历与资料记录的版本不同，请本人核对后再使用。',
    unverified:'已读取本机简历，但没有可核实的记录版本，需本人核对。',
    missing:'缺少本机简历记录，需本人准备。',unavailable:'本机简历暂不可读取，需本人核对。'};
  preparationResume.textContent=resumeLabels[data.resume.status]+(data.resume.version?
    ' 当前版本摘要前 12 位：'+data.resume.version.slice(0,12)+'。':'')+' 这不证明网站已接收或保存简历。';
  preparationContract.hidden=!data.contract.matched;preparationUnmatched.hidden=data.contract.matched;
  if(data.contract.matched){
    for(const item of data.items){
      const row=document.createElement('li'),label=document.createElement('span'),status=document.createElement('strong');
      label.textContent=preparationFieldLabels[item.key];status.textContent=preparationStatusLabels[item.status];
      status.className=item.status;row.append(label,status);preparationChecks.append(row);
    }
    preparationSource.href=preparationOfficialSource;
  }
  preparationObservation.hidden=false;
  preparationStatus.textContent='已完成一次本机只读检查。以下是最近一次本地观察，不是网站草稿或可提交认证。';
  preparationDialog.scrollTop=0;
}
async function readTaskPreparation(){
  if(uiSessionExpired||!preparationDialog.open||!preparationBinding||preparationBusy||preparationStale)return;
  const epoch=preparationEpoch,binding=preparationBinding;
  if(!preparationIsCurrent(epoch,binding))return;
  preparationBusy=true;preparationRefresh.disabled=true;clearPreparationContent();
  preparationStatus.textContent='正在检查此任务的本机资料…';
  try{
    const response=await uiRequest('/ui/api/task-preparation?task_id='+encodeURIComponent(binding.taskId)
      +'&expected_revision='+binding.revision,{credentials:'same-origin',cache:'no-store'});
    if(!preparationIsCurrent(epoch,binding))return;
    if(response.status===409){preparationStale=true;throw new Error();}
    if(!response.ok)throw new Error();
    const data=await response.json();
    if(!preparationIsCurrent(epoch,binding))return;
    if(!validPreparation(data,binding))throw new Error();
    renderPreparation(data);
  }catch(_){
    if(preparationIsCurrent(epoch,binding)){
      clearPreparationContent();
      preparationStatus.textContent=preparationStale?
        '任务或资料版本已变化，本次观察已清除。请关闭后从任务卡片重新查看。':
        '本地准备检查暂不可用；没有修改任务。可明确点击刷新检查重试。';
    }
  }finally{
    if(epoch===preparationEpoch){preparationBusy=false;preparationRefresh.disabled=uiSessionExpired||preparationStale;}
  }
}
function openTaskPreparation(taskId,revision){
  if(uiSessionExpired||!Number.isSafeInteger(revision)||revision<0
    ||preparationTaskRevisions.get(taskId)!==revision)return;
  if(preparationDialog.open&&preparationBinding?.taskId===taskId&&preparationBinding.revision===revision)return;
  if(preparationDialog.open)closePreparationDialog(false);
  clearPreparationObservation();currentTaskId=taskId;updateTaskContext();
  preparationBinding={taskId,revision};preparationDialog.showModal();preparationClose.focus({preventScroll:true});
  preparationDialog.scrollTop=0;
  void readTaskPreparation();
}
tasksEl.addEventListener('click',event=>{
  const button=event.target.closest('button[data-task-preparation]');if(!button)return;
  openTaskPreparation(button.dataset.task,Number(button.dataset.revision));
});
preparationClose.onclick=()=>closePreparationDialog();
preparationRefresh.onclick=readTaskPreparation;
preparationDialog.addEventListener('cancel',event=>{event.preventDefault();closePreparationDialog();});
preparationDialog.addEventListener('close',()=>{
  // A queued close event cannot erase a newer dialog opened in the same turn.
  if(!preparationDialog.open)clearPreparationObservation();
});
const taskContext=document.getElementById('task-context');
const rememberView=document.getElementById('remember-task-view');
const forgetView=document.getElementById('forget-task-view');
const refreshView=document.getElementById('refresh-task-view');
const savedViewLabel=document.getElementById('saved-task-view');
let savedView=null,viewInitialized=false,viewPending=false,viewUnknown=false;
function validView(value){
  return value&&Object.keys(value).length===2&&Number.isSafeInteger(value.revision)&&value.revision>=0
    &&(value.task_id===null||typeof value.task_id==='string'&&/^[A-Za-z0-9_.:-]{1,120}$/.test(value.task_id));
}
function updateSavedViewControls(){
  const unavailable=uiSessionExpired||viewPending||viewUnknown||!validView(savedView);
  rememberView.disabled=unavailable||!currentTaskId;
  forgetView.disabled=unavailable||savedView.task_id===null;
  refreshView.disabled=uiSessionExpired||viewPending;
  const message=uiSessionExpired?'面板会话已失效，请重新打开应用。':
    viewUnknown?'保存结果未确认；请重新读取查看位置，不会自动重试。':
    !validView(savedView)?'查看位置尚未读取。未发送输入不会保存。':
    savedView.task_id===null?'未记住查看位置。未发送输入不会保存。':
    '已记住一个任务的查看位置。只保存任务标识，不会自动操作或保存未发送输入。';
  if(savedViewLabel.textContent!==message)savedViewLabel.textContent=message;
}
function acceptSavedView(state){
  if(uiSessionExpired)return;
  if(!validView(state?.ui_context))return;
  savedView={...state.ui_context};
  if(!viewInitialized){
    viewInitialized=true;
    currentTaskId=(state.tasks||[]).some(task=>task.task_id===savedView.task_id)?savedView.task_id:null;
  }
  updateSavedViewControls();
}
async function saveTaskView(taskId){
  if(uiSessionExpired||viewPending||viewUnknown||!validView(savedView))return;
  const expected=savedView.revision;
  viewPending=true;updateSavedViewControls();
  try{
    const response=await uiRequest('/ui/api/task-view-context',{method:'POST',
      headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({task_id:taskId,expected_revision:expected})});
    if(!response.ok)throw new Error();
    const result=await response.json();
    if(uiSessionExpired||!validView(result)||result.task_id!==taskId||result.revision!==expected+1)throw new Error();
    const readback=await state();
    if(!readback||!validView(readback.ui_context)||readback.ui_context.task_id!==taskId
      ||readback.ui_context.revision!==result.revision)throw new Error();
    viewUnknown=false;
    notify(taskId===null?'已忘记查看位置。现有任务和输入保持不变。':'已记住当前任务；下次打开只恢复查看位置。');
  }catch(_){
    if(!uiSessionExpired){viewUnknown=true;notify('查看位置保存未确认；请重新读取后核对，不会自动重试。');}
  }finally{viewPending=false;updateSavedViewControls();}
}
rememberView.onclick=()=>{if(currentTaskId)void saveTaskView(currentTaskId);};
forgetView.onclick=()=>void saveTaskView(null);
refreshView.onclick=async()=>{
  if(uiSessionExpired||viewPending)return;
  viewPending=true;updateSavedViewControls();
  const result=await state();
  if(!uiSessionExpired){
    if(result&&validView(result.ui_context)){viewUnknown=false;notify('已重新读取查看位置；没有重复保存或操作任务。');}
    else notify('无法确认查看位置；现有任务和输入保持不变。');
  }
  viewPending=false;updateSavedViewControls();
};
function updateTaskContext(){
  const cards=[...tasksEl.querySelectorAll('[data-task-card]')];
  const current=cards.find(card=>card.dataset.taskCard===currentTaskId);
  if(!current)currentTaskId=null;
  for(const card of cards){
    const selected=card===current;
    card.dataset.currentTask=String(selected);
    card.querySelector('[data-task-select]').setAttribute('aria-pressed',String(selected));
  }
  const title=current?.querySelector('[data-task-select]').textContent;
  const message=uiSessionExpired?'面板会话已失效，请重新打开应用。':
    title?'当前查看：'+title+'。任务操作请使用该卡片按钮。':'尚未选中任务。可用键盘浏览任务卡片。';
  if(taskContext.textContent!==message)taskContext.textContent=message;
  updateSavedViewControls();
}
tasksEl.addEventListener('click',event=>{
  const button=event.target.closest('button[data-task-select]');
  if(!button||uiSessionExpired)return;
  if(preparationBinding&&preparationBinding.taskId!==button.dataset.taskSelect)closePreparationDialog(false);
  currentTaskId=button.dataset.taskSelect;updateTaskContext();
});
tasksEl.addEventListener('keydown',event=>{
  const button=event.target.closest('button[data-task-select]');
  if(!button||uiSessionExpired||!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
  const buttons=[...tasksEl.querySelectorAll('button[data-task-select]')];
  const index=buttons.indexOf(button);if(index<0)return;
  event.preventDefault();
  const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:
    Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowUp'?-1:1)));
  buttons[next]?.focus();
});
function expireUISession(){
  if(uiSessionExpired)return;
  uiSessionExpired=true;
  closePreparationDialog(false);preparationTaskRevisions.clear();
  updateEpoch++;updateReadBusy=false;updateRefresh.disabled=true;
  updateObservation.textContent='';if(updateDialog.open)closeUpdateDialog();
  providerLoadEpoch++;providerLoad.disabled=true;providerRefresh.disabled=true;
  providerRefreshObservation=null;providerLoadStatus.textContent='';
  currentTaskId=null;savedView=null;updateTaskContext();
  clearProfileSelection();if(profileDialog.open)profileDialog.close();
  closeEditor();
  recoveryObservations.clear();tasksEl.querySelectorAll('[data-field-recovery]').forEach(panel=>panel.remove());
  const notice=document.getElementById('session-expired');
  notice.hidden=false;
  document.getElementById('health').textContent='面板会话已失效';
  document.getElementById('readiness').textContent='请重新打开应用，任务不会自动提交';
  document.querySelector('.dot').style.background='#b45309';
  // Keep unsent inputs available for selection/copy. A new app open obtains
  // its own one-use ticket; this page never renews auth or replays an action.
  document.querySelectorAll('.shell button').forEach(button=>button.disabled=true);
  document.querySelectorAll('.shell input,.shell textarea').forEach(input=>{input.disabled=false;input.readOnly=true;});
  document.querySelectorAll('.shell select').forEach(select=>select.disabled=true);
  for(const panel of tasksEl.querySelectorAll('[data-private-review-panel]')){
    panel.replaceChildren();panel.hidden=true;
    delete panel.dataset.privateReviewOpen;delete panel.dataset.revision;
  }
  tasksEl.querySelectorAll('[data-review-values]').forEach(button=>button.setAttribute('aria-expanded','false'));
  diagnosticsReport.textContent='';
  if(diagnosticsDialog.open)diagnosticsDialog.close();
  if(readinessDialog.open)readinessDialog.close();
  clearTimeout(notify.timer);toast.style.display='none';
  notice.focus();
}
async function uiRequest(path,options){
  if(uiSessionExpired)throw new Error('ui_session_expired');
  const response=await fetch(path,options);
  if(response.status===401){expireUISession();throw new Error('ui_session_expired');}
  // A different in-flight read may have discovered expiry meanwhile. Its
  // formerly authorized result must not refresh stale private values/actions.
  if(uiSessionExpired)throw new Error('ui_session_expired');
  return response;
}

const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stageText={
  DISCOVERED:'已加入',
  PROFILE_RESOLVED:'正在准备资料',
  FORM_FILLED:'正在填写',
  VALIDATED:'正在检查',
  NEEDS_USER_INPUT:'需要你回答',
  NEEDS_USER_ACTION:'需要你操作',
  BLOCKED:'已暂停',
  ERROR:'需要处理',
  READY_TO_SUBMIT:'等你最终确认',
  SUBMITTED:'已提交',
  VERIFIED:'已完成',
  CANCELLED:'已取消'
};
const blockerText={
  security_challenge:'需要安全验证',
  otp_waiting:'正在等待验证码',
  otp_ambiguous:'验证码需要确认',
  auth_return_unverified:'登录后未回到原岗位，已安全暂停',
  account_identity_unverified:'当前登录账号尚未核实，已安全暂停',
  draft_persistence_unverified:'草稿保存结果尚未核实，已安全暂停',
  sms_setup:'正在准备短信验证',
  unknown_facts:'需要补充信息',
  session_unavailable:'浏览器连接中断',
  live_not_authorized:'尚未授权实时执行',
  isolated_external_target:'隔离测试模式只支持本地合成站',
  browser_ownership_unknown:'浏览器页面归属不明，等待安全核对',
  user_paused_from_browser_ownership_unknown:'浏览器页面归属不明，等待安全核对',
  anonymous_preparation_unverified:'独立填写准备尚未验证，不能自动继续或提交',
  user_paused_from_anonymous_preparation_unverified:'独立填写准备已暂停，等待只读核对',
  unknown_outcome:'上次写入结果不明，等待只读核对',
  user_paused_from_unknown_outcome:'上次写入结果不明，等待只读核对',
  validation:'需要检查表单',
  profile_changed:'资料已更新，需要重新核对申请',
  retry_pending:'正在重试',
  retry_exhausted:'需要处理后再继续',
  user_paused:'你已暂停'
};
function humanStage(stage){return stageText[stage]||stage||''}
function humanBlocker(blocker){return blockerText[blocker]||blocker||''}
function stageGroup(stage){
  if(['NEEDS_USER_INPUT','NEEDS_USER_ACTION','BLOCKED','ERROR'].includes(stage))return 'need';
  if(stage==='READY_TO_SUBMIT')return 'ready';
  if(['SUBMITTED','VERIFIED','CANCELLED'].includes(stage))return 'done';
  return 'running';
}
function privateValue(value){return typeof value==='string'?value:JSON.stringify(value)??''}
function privateReviewHtml(review){
  const fields=(review.fields||[]).map(f=>`<tr><th>${esc(f.label||f.key)}</th><td>${esc(privateValue(f.expected))}</td><td>${esc(privateValue(f.observed))}</td></tr>`).join('');
  const files=(review.attachments||[]).map(a=>`<li>${esc(a.slot)}：${esc(a.filename)} · 本地 ${esc(a.canonical_sha256)} · 草稿 ${esc(a.observed_sha256)}</li>`).join('');
  const coverage=review.project_coverage||{};
  return `<strong>上次独立核验的完整值</strong> <button type="button" class="headerbtn" data-close-private-review="true">隐藏完整值</button><p>这是当时的只读快照；页面若被编辑，请重新核验，不能据此认定当前值仍相同。</p>
    <p>目标：${esc(review.target_url)}</p>
    <p>账号：${esc(review.account?.key||'未展示')} · ${esc(privateValue(review.account?.canonical_value||''))}（上次与活动账号匹配）</p>
    <table><thead><tr><th>字段</th><th>申请意图</th><th>草稿实际保留值</th></tr></thead><tbody>${fields}</tbody></table>
    <p>附件：</p>${files?`<ul>${files}</ul>`:'<p>无</p>'}
    <p>项目核验：${esc(coverage.status||'未知')} · 规范项目 ${esc((coverage.canonical_projects||[]).join('、'))} · 明确排除 ${esc((coverage.explicit_exclusions||[]).join('、'))}</p>
    <p>服务端结构化行只保存身份和摘要；请在招聘页面核对每条内容。</p>`;
}
function recoveryHtml(task){
  const saved=recoveryObservations.get(task.task_id);
  if(!saved)return '';
  if(task.stage!=='BLOCKED'||saved.revision!==task.revision||!['unknown_outcome','browser_ownership_unknown','user_paused_from_unknown_outcome','user_paused_from_browser_ownership_unknown'].includes(task.blocker)){recoveryObservations.delete(task.task_id);return ''}
  const fields=saved.field_actions;
  return '<div class="review warning" data-field-recovery role="status">上次只读核对的历史字段操作记录：'+fields.total+' 项；其中 '+fields.attempted+' 项中断后结果未知，'+fields.unknown_outcome+' 项已标为结果未知，'+fields.dom_readback_unverified+' 项仅有当时页面回读，'+fields.unrecognized+' 项记录无法解释。服务端保存和草稿身份仍未证明；任务保持暂停，不会自动重放，最终提交仍由你本人完成。</div>';
}
function acceptRecoveryObservation(taskId,observed){
  if(!observed||typeof observed!=='object'||Array.isArray(observed))return false;
  const fields=observed.field_actions,keys=['attempted','dom_readback_unverified','unknown_outcome','unrecognized','total'];
  if(!Number.isSafeInteger(observed.revision)||observed.revision<0||observed.task_id!==taskId||observed.journal_scope!=='recorded_local_intents_only'||observed.server_persistence_verified!==false||observed.draft_identity_verified!==false||observed.replay_allowed!==false||observed.submit_capability!==false||!fields||typeof fields!=='object'||Array.isArray(fields)||Object.keys(fields).some(k=>!keys.includes(k))||!keys.every(k=>Number.isSafeInteger(fields[k])&&fields[k]>=0)||fields.total!==keys.slice(0,4).reduce((n,k)=>n+fields[k],0))return false;
  recoveryObservations.set(taskId,{revision:observed.revision,field_actions:{...fields}});return true;
}
function render(state){
  if(uiSessionExpired)return;
  const focusedPreparation=document.activeElement?.hasAttribute('data-task-preparation')?document.activeElement.dataset.task:null;
  acceptSavedView(state);
  reconcilePreparationTasks(state.tasks);
  const counts={running:0,need:0,ready:0,done:0};
  (state.tasks||[]).forEach(t=>counts[stageGroup(t.stage)]++);
  Object.entries(counts).forEach(([k,v])=>document.getElementById(k).textContent=v);
  document.getElementById('health').textContent='本地服务已连接';
  const focusedTask=document.activeElement?.dataset?.taskSelect;
  if(!(state.tasks||[]).length){
    tasksEl.className='empty';tasksEl.textContent='暂无任务';updateTaskContext();
    if(focusedTask!==undefined||focusedPreparation)taskContext.focus();
    return;
  }
  tasksEl.className='';
  tasksEl.innerHTML=(state.tasks||[]).map(t=>`
    <div class="task" data-task-card="${esc(t.task_id)}">
      <div class="title"><button class="task-select" type="button" data-task-select="${esc(t.task_id)}" aria-label="查看任务 · ${esc(t.company)} · ${esc(t.role)}" aria-pressed="false">${esc(t.company)} · ${esc(t.role)}</button></div>
      <div class="meta">${esc(t.target_host||'')} ${t.blocker?'· '+esc(humanBlocker(t.blocker)):''}</div>
      <span class="stage">${esc(humanStage(t.stage))}</span>
      ${recoveryHtml(t)}
      ${t.stage==='READY_TO_SUBMIT'?(t.review_summary?.status==='last_verified'?`
        <div class="review">上次独立核验：${Number(t.review_summary.field_count)||0} 项填写、${Number(t.review_summary.row_count)||0} 条经历、${Number(t.review_summary.attachment_count)||0} 个附件，${Number(t.review_summary.check_count)||0} 项检查通过。请在申请页面再次核对完整内容；最终提交只能由你本人点击。</div>`:
        '<div class="review warning">核验摘要不可用，请勿提交。任务需要重新核验。</div>'):''}
      ${t.stage==='NEEDS_USER_INPUT'?(t.question_context?.status==='current'?(`<div class="otpnote">以下为当前招聘网站问题原文，仅供辨认；请依据本人真实情况回答。</div>`+(t.question_context.items||[]).map(q=>`
        <label class="factinput"><span>${esc(q.label)}${q.required?' · 必填':''}</span>${(t.boolean_keys||[]).includes(q.key)?`<select aria-label="${esc(q.label)}"><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>`:`<input autocomplete="off" aria-label="${esc(q.label)}">`}
          <button type="button" data-answer="true" data-key="${esc(q.key)}" data-task="${esc(t.task_id)}" data-revision="${t.revision}">本地填写</button>
          ${(t.reusable_keys||[]).includes(q.key)?'<input type="checkbox" class="remember-fact" aria-label="保存为可复用事实"><span>经我确认后记住，供以后申请使用</span>':''}</label>`).join('')):'<div class="otpnote">当前招聘网站问题原文无法核对；为避免答错字段，已暂停本地填写。请重新核对任务。</div>'):''}
      ${t.blocker==='otp_waiting'&&t.auth_attempt_id?`
        <div class="otpnote">${t.otp_source==='configured_unverified'?'已配置自动接收，正在等待；若接收失败可在此输入。':'自动接收来源未验证；可在此本地输入。'}验证码不会发送给 AI 或保存在任务中。</div>
        <label class="otpinput"><input type="password" inputmode="numeric" autocomplete="off" maxlength="8" aria-label="当前任务验证码" data-otp-task="${esc(t.task_id)}"><button type="button" data-otp-send="true" data-task="${esc(t.task_id)}" data-attempt="${esc(t.auth_attempt_id)}">本地输入验证码</button></label>
        ${t.resend_eligible?(t.resend_wait_seconds===0?`<button class="headerbtn" type="button" data-otp-resend="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}">授权重发一次</button>`:`<div class="otpnote">重发冷却中：约 ${Number(t.resend_wait_seconds)||0} 秒</div>`):''}
      `:t.blocker==='otp_waiting'?`<div class="otpnote">本次验证码等待已过期或身份不明；旧码不会再被接受。</div>
        ${t.resend_eligible?(t.resend_wait_seconds===0?`<button class="headerbtn" type="button" data-otp-resend="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}">授权重发一次</button>`:`<div class="otpnote">重发冷却中：约 ${Number(t.resend_wait_seconds)||0} 秒</div>`):''}`:''}
      ${t.blocker==='security_challenge'?'<div class="otpnote">请在任务专用浏览器由本人完成安全验证；完成后继续，系统会重新核对目标。</div>':''}
      ${t.blocker==='auth_return_unverified'?'<div class="otpnote">系统无法证明登录后仍在原岗位。请核对页面；此任务不会自动重发短信或继续写入。</div>':''}
      ${t.blocker==='account_identity_unverified'?'<div class="otpnote">系统无法证明当前账号属于申请人。此站点表单保持只读，直到有受验证的站点账号识别能力。</div>':''}
      <div class="taskcontrols task-view-controls">
        <button type="button" data-task-preparation="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}">查看本地准备清单</button>
        ${t.stage==='READY_TO_SUBMIT'&&t.review_values_available?`<button type="button" data-review-values="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}" aria-expanded="false" aria-controls="review-${esc(t.task_id)}">查看完整复核值</button>`:''}
        ${t.stage==='READY_TO_SUBMIT'?`<button type="button" data-observe-submission="true" data-task="${esc(t.task_id)}">只读查看提交结果</button>`:''}
        ${t.stage==='READY_TO_SUBMIT'&&t.can_confirm_submission?`<button type="button" data-confirm-submission="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}">我已在招聘网站亲自提交</button>`:''}
        ${!['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION','READY_TO_SUBMIT','SUBMITTED','VERIFIED','CANCELLED'].includes(t.stage)?`<button type="button" data-action="PAUSE" data-task="${esc(t.task_id)}" data-revision="${t.revision}">暂停</button>`:''}
        ${['unknown_outcome','browser_ownership_unknown','user_paused_from_unknown_outcome','user_paused_from_browser_ownership_unknown'].includes(t.blocker)?`<button type="button" data-action="OBSERVE" data-task="${esc(t.task_id)}">只读核对</button>`:''}
      ${['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION'].includes(t.stage)&&t.blocker!=='otp_waiting'&&!['unknown_outcome','browser_ownership_unknown','user_paused_from_unknown_outcome','user_paused_from_browser_ownership_unknown','auth_return_unverified','account_identity_unverified','draft_persistence_unverified','anonymous_preparation_unverified','user_paused_from_anonymous_preparation_unverified'].includes(t.blocker)?`<button type="button" data-action="RESUME" data-task="${esc(t.task_id)}" data-revision="${t.revision}">继续</button>`:''}
        ${!['SUBMITTED','VERIFIED','CANCELLED','READY_TO_SUBMIT'].includes(t.stage)?`<button type="button" data-action="CANCEL" data-task="${esc(t.task_id)}" data-revision="${t.revision}">取消</button>`:''}
      </div>
      ${t.stage==='READY_TO_SUBMIT'?`<div id="review-${esc(t.task_id)}" class="review private-review" data-private-review-panel role="region" aria-label="完整申请复核" tabindex="-1" hidden></div>`:''}
    </div>`).join('');
  updateTaskContext();
  if(focusedTask!==undefined){
    const restored=[...tasksEl.querySelectorAll('[data-task-select]')].find(button=>button.dataset.taskSelect===focusedTask);
    (restored||taskContext).focus({preventScroll:true});
  }
  else if(focusedPreparation)(preparationTrigger(focusedPreparation)||taskContext).focus({preventScroll:true});
}
tasksEl.addEventListener('click',event=>{
  const button=event.target.closest('button[data-close-private-review]');if(!button)return;
  const panel=button.closest('[data-private-review-panel]');
  const trigger=panel.closest('.task').querySelector('button[data-review-values]');
  panel.innerHTML='';panel.hidden=true;
  delete panel.dataset.privateReviewOpen;delete panel.dataset.revision;
  trigger?.setAttribute('aria-expanded','false');trigger?.focus();
  state();
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-review-values]');if(!button)return;
  button.disabled=true;
  try{
    const r=await uiRequest('/ui/api/review-values?task_id='+encodeURIComponent(button.dataset.task),{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    if(data.revision!==Number(button.dataset.revision))throw new Error();
    const panel=button.closest('.task').querySelector('[data-private-review-panel]');
    panel.innerHTML=privateReviewHtml(data.review);
    panel.hidden=false;
    button.setAttribute('aria-expanded','true');
    panel.focus();
    panel.dataset.privateReviewOpen=button.dataset.task;
    panel.dataset.revision=button.dataset.revision;
  }catch(e){notify('完整复核值已过期或暂不可用；请勿据此提交。');await state()}
  finally{button.disabled=uiSessionExpired}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-confirm-submission]');if(!button)return;
  if(!window.confirm('请确认你已经在招聘网站亲自点击最终提交。这里仅记录你的确认并保护该岗位，不会代你点击提交。'))return;
  button.disabled=true;
  try{
    const r=await uiRequest('/ui/api/human-submission',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({task_id:button.dataset.task,expected_revision:Number(button.dataset.revision),user_confirmed:true})});
    if(!r.ok)throw new Error();
    const receipt=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    notify(receipt.page_signal?'已记录你的提交确认；页面有成功提示，但服务器结果尚未独立核实。':'已记录你的提交确认；服务器结果尚未独立核实，请保留招聘网站回执。');
    await state();
  }catch(e){notify('确认未记录；请核对当前任务及招聘网站，再重试。');await state()}
  finally{button.disabled=uiSessionExpired}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-observe-submission]');if(!button)return;
  button.disabled=true;
  try{
    const r=await uiRequest('/ui/api/submission-observation?task_id='+encodeURIComponent(button.dataset.task),{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const observed=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    notify(observed.status==='PAGE_SIGNAL_OBSERVED'
      ?'页面出现提交成功提示；这只是页面信号，尚未核实服务器结果。'
      :'未看到可信的提交结果；任务状态未改变。请在招聘站点自行核对。');
  }catch(e){notify('只读查看暂不可用；任务状态未改变。')}
  finally{button.disabled=uiSessionExpired}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-action]');if(!button)return;
  button.disabled=true;
  const action=button.dataset.action,task_id=button.dataset.task;
  if(action==='OBSERVE'){
    try{
      const r=await uiRequest('/ui/api/observe?task_id='+encodeURIComponent(task_id),{credentials:'same-origin'});
      if(!r.ok)throw new Error();
      const observed=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
      if(!acceptRecoveryObservation(task_id,observed))throw new Error();await state();
      notify(observed.status==='BOUND_DOCUMENT_OBSERVED'?'已找到原任务页面；草稿和写入结果仍待证明，任务保持暂停。':'原任务页面尚无法核实；任务保持暂停。');
    }catch(e){notify('只读核对暂不可用；任务保持暂停。')}
    finally{button.disabled=uiSessionExpired}
    return;
  }
  const expected_revision=Number(button.dataset.revision);
  const command_id='ui-'+crypto.randomUUID();
  try{
    const r=await uiRequest('/ui/api/command',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({command_id,task_id,action,expected_revision})});
    if(!r.ok)throw new Error();
    notify({PAUSE:'任务已暂停',RESUME:'任务已继续',CANCEL:'任务已取消'}[action]);
    await state();
  }catch(e){notify('任务状态已变化，请刷新后重试。');await state()}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-otp-send],button[data-otp-resend]');if(!button)return;
  event.preventDefault();button.disabled=true;
  if(button.dataset.otpSend){
    const input=button.parentElement.querySelector('input[data-otp-task]');
    const message=input.value.trim();input.value='';
    if(!/^\d{4,8}$/.test(message)){notify('请输入当前短信中的 4 到 8 位验证码。');button.disabled=uiSessionExpired;return}
    try{
      const r=await uiRequest('/ui/api/otp',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
        body:JSON.stringify({task_id:button.dataset.task,attempt_id:button.dataset.attempt,message})});
      if(!r.ok)throw new Error();notify('验证码已通过本地专用通道交给当前任务。');await state();
    }catch(e){notify('验证码未被当前尝试接受；请核对短信和任务状态。');await state()}
  }else{
    try{
      const r=await uiRequest('/ui/api/otp-resend',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
        body:JSON.stringify({task_id:button.dataset.task,command_id:'ui-resend-'+crypto.randomUUID(),expected_revision:Number(button.dataset.revision)})});
      if(!r.ok)throw new Error();notify('已授权当前任务重发一次；系统会重新核对控件与冷却状态。');await state();
    }catch(e){notify('无法安全重发；请检查冷却时间和当前任务。');await state()}
  }
  button.disabled=uiSessionExpired;
});
newTaskForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=newTaskForm.querySelector('button');button.disabled=true;
  const data=Object.fromEntries(new FormData(newTaskForm).entries());
  try{
    const r=await uiRequest('/ui/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'same-origin',body:JSON.stringify(data)});
    if(!r.ok)throw new Error();
    const result=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');showDiscovery(result,data);
    if(result.task_id){if(result.task_binding!=='existing_different')newTaskForm.reset();await state()}
  }catch(e){notify('暂时无法安全查找岗位；请核对公司、岗位和官方链接。')}
  finally{button.disabled=uiSessionExpired}
});
function showDiscovery(result,request){
  const discovery=result.discovery||{};
  if(result.task_id){
    pendingDiscovery=null;candidatesEl.replaceChildren();
    if(result.task_binding==='existing_different'){
      const message='同一目标已有任务，仍保留原岗位、资料和授权；本次没有替换或启动任务。';
      const note=document.createElement('p');note.className='candidate-note';
      note.id='existing-task-note';note.textContent=message;
      candidatesEl.append(note);
      if(typeof result.task_id==='string'&&/^[A-Za-z0-9_.:-]{1,120}$/.test(result.task_id)){
        const review=document.createElement('button');review.type='button';
        review.setAttribute('aria-describedby','existing-task-note');
        review.dataset.existingTask=result.task_id;review.textContent='查看已有任务';
        candidatesEl.append(review);
      }
      notify(message);
    }else{
      notify(discovery.status==='VERIFIED'?'任务已保留在列表中；请从卡片核对岗位，准备草稿前仍会检查授权。':'待核验任务已保留在列表中；不会自动写入招聘网站。');
    }
    return;
  }
  pendingDiscovery={request,discovery};
  const labels={AMBIGUOUS:'发现多个同名岗位，请按地点、批次和用工类型选择。',INCOMPLETE:'公开列表覆盖范围尚未证实，暂不选择或写入。',UNAVAILABLE:'未找到符合全部条件的在招岗位。',UNSUPPORTED:'此公司或链接暂不在已验证的发现范围内。'};
  candidatesEl.innerHTML='<div class="candidate-note">'+esc(labels[discovery.status]||'岗位尚未核验。')+'</div>'+
    (discovery.candidates||[]).map(c=>'<div class="candidate"><b>'+esc(c.title)+'</b><br>'+esc(c.location||'地点未注明')+' · '+esc(c.campaign||'批次未注明')+' · '+esc(c.employment_type||'类型未注明')+'<br>职位 '+esc(c.job_id)+(discovery.status==='AMBIGUOUS'?'<button type="button" data-candidate="'+esc(c.candidate_id)+'">选择此岗位</button>':'')+'</div>').join('');
}
candidatesEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-candidate]');if(!button||!pendingDiscovery)return;
  button.disabled=true;
  try{
    const data={...pendingDiscovery.request,selected_candidate_id:button.dataset.candidate};
    const r=await uiRequest('/ui/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(data)});
    if(!r.ok)throw new Error();
    const result=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');showDiscovery(result,pendingDiscovery?.request||data);
    if(result.task_id){if(result.task_binding!=='existing_different')newTaskForm.reset();await state()}
  }catch(e){notify('候选已变化或暂时无法核验，请重新查找。');button.disabled=uiSessionExpired}
});
candidatesEl.addEventListener('click',event=>{
  const button=event.target.closest('button[data-existing-task]');
  if(!button||uiSessionExpired)return;
  const card=[...tasksEl.querySelectorAll('[data-task-card]')]
    .find(item=>item.dataset.taskCard===button.dataset.existingTask);
  const select=card?.querySelector('[data-task-select]');
  if(!select){notify('原任务暂未显示，请刷新后从任务列表核对。');return;}
  // Reuse the existing read-only task-selection action. No queue control,
  // authorization, automatic retry or profile rebinding is performed.
  select.click();select.focus();select.scrollIntoView({block:'nearest'});
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-answer]');if(!button)return;
  event.preventDefault();
  const input=button.previousElementSibling,value=input.value;
  const remember=button.nextElementSibling?.classList.contains('remember-fact')&&button.nextElementSibling.checked;
  if(!value.trim()){notify('请先填写答案。');return}
  button.disabled=true;
  try{
    const r=await uiRequest('/ui/api/user-input',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({task_id:button.dataset.task,field_key:button.dataset.key,
        value,expected_revision:Number(button.dataset.revision),remember})});
    if(!r.ok)throw new Error();
    const result=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    input.value='';
    notify(remember?(result.task?.fact_reuse_status==='SAVED'?'答案已在本地保存为经确认的可复用事实。':'答案已交给当前任务；可复用保存待重试。'):'答案已在本地交给当前任务。');await state();
  }catch(e){
    try{
      const r=await uiRequest('/ui/api/state',{credentials:'same-origin'}),data=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
      const task=(data.tasks||[]).find(t=>t.task_id===button.dataset.task);
      if(task&&task.stage==='NEEDS_USER_INPUT'&&(task.unresolved_keys||[]).includes(button.dataset.key)){
        button.dataset.revision=String(task.revision);
        notify('任务状态已变化；答案仍在本地，请核对后重试。');
      }else{notify('任务不再等待这个答案；未提交输入。')}
    }catch(_){notify('本地服务暂不可用；答案仍在输入框中。')}
  }finally{button.disabled=uiSessionExpired}
});
function notify(text){
  if(uiSessionExpired)return;
  if(toast.style.display==='block'&&toast.textContent===text)return;
  toast.textContent=text;toast.style.display='block';
  clearTimeout(notify.timer);notify.timer=setTimeout(()=>{toast.style.display='none'},4200);
}
function updateLabel(update){
  if(uiSessionExpired){updateBtn.disabled=true;msg.readOnly=true;send.disabled=true;return;}
  // Retain every legacy mutation fence. Historical records cannot authorize
  // an install, clear a restart fence or prove the current packaged version.
  const status=update&&typeof update.status==='string'?update.status:'idle';
  legacyUpdateObservation={status};
  const fenced=['checking','updating','restarting','restart_required'].includes(status);
  updateBtn.disabled=false;updateBtn.textContent='更新状态';
  msg.disabled=fenced;send.disabled=fenced;
  const messages={
    idle:'没有正在进行的旧版更新记录；这不代表已检查新版本。',
    checking:'旧版记录显示正在检查；任务写入保持暂停，此页不会启动或重试更新。',
    updating:'旧版记录显示可能正在更新；任务写入保持暂停，此页不会安装或重试。',
    restarting:'旧版记录显示重启尚未确认；任务写入保持暂停，此页不会重启服务。',
    restart_required:'旧版记录尚需恢复核验；任务写入保持暂停，请重新打开应用并查看诊断，此页不会重试重启。',
    success:'记录包含旧版更新成功状态；不能据此确认当前安装包版本。',
    up_to_date:'记录包含旧版版本检查结果；不能据此确认当前安装包已是最新版本。',
    failed:'记录包含旧版更新失败状态；此页不会重试，也不能据此确认当前安装包版本。'
  };
  if(updateDialog.open)updateObservation.textContent=messages[status]||
    '更新状态未确认；此页不会自动重试或改变任务。';
}
async function previewDiagnostics(){
  diagnosticsBtn.disabled=true;
  try{
    const r=await uiRequest('/ui/api/diagnostics',{credentials:'same-origin'});
    const data=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    if(!r.ok)throw new Error();
    diagnosticsReport.textContent=JSON.stringify(data,null,2);
    diagnosticsDialog.showModal();
    diagnosticsClose.focus();
  }catch(e){
    notify('无法读取诊断；现有任务未被修改。');
  }finally{diagnosticsBtn.disabled=uiSessionExpired}
}
async function copyDiagnostics(){
  diagnosticsCopy.disabled=true;
  const report=diagnosticsReport.textContent;
  try{
    if(!diagnosticsDialog.open||!report)throw new Error();
    try{
      await navigator.clipboard.writeText(report);
    }catch(copyError){
      const helper=document.createElement('textarea');
      helper.value=report;helper.setAttribute('readonly','');helper.style.position='fixed';helper.style.opacity='0';
      document.body.appendChild(helper);helper.select();
      try{if(!document.execCommand('copy'))throw copyError}
      finally{helper.remove()}
    }
    notify('诊断信息已复制。');
  }catch(e){
    notify('复制诊断失败；现有任务未被修改。');
  }finally{diagnosticsCopy.disabled=false}
}
diagnosticsDialog.addEventListener('close',()=>{
  diagnosticsReport.textContent='';
  if(!uiSessionExpired)diagnosticsBtn.focus();
});
diagnosticsClose.onclick=()=>{diagnosticsReport.textContent='';diagnosticsDialog.close()};
diagnosticsCopy.onclick=copyDiagnostics;
async function startUpdate(){
  if(uiSessionExpired||updateDialog.open)return;
  updateEpoch++;updateDialog.showModal();updateClose.focus();
  updateLabel(legacyUpdateObservation);
  await pollUpdate();
}
async function pollUpdate(){
  // Compatibility name retained for old callers. One explicit read only:
  // no Git/update POST, retry, service restart or timer is admitted here.
  if(uiSessionExpired||!updateDialog.open||updateReadBusy)return;
  const epoch=updateEpoch;updateReadBusy=true;updateRefresh.disabled=true;
  try{
    const response=await uiRequest('/ui/api/update-status',{credentials:'same-origin'});
    if(!response.ok)throw new Error();
    const data=await response.json();
    if(uiSessionExpired||epoch!==updateEpoch||!updateDialog.open)return;
    if(!data||typeof data!=='object'||Array.isArray(data)
      ||!['idle','checking','updating','restarting','restart_required','success','up_to_date','failed'].includes(data.status))throw new Error();
    updateLabel(data);
  }catch(_){
    if(!uiSessionExpired&&epoch===updateEpoch&&updateDialog.open)
      updateObservation.textContent='更新状态未确认；原有任务安全限制保持不变，不会自动重试。';
  }finally{
    if(epoch===updateEpoch){updateReadBusy=false;updateRefresh.disabled=uiSessionExpired;}
  }
}
function clearUpdateObservation(){
  updateEpoch++;updateReadBusy=false;updateObservation.textContent='';
  updateRefresh.disabled=uiSessionExpired;
}
function closeUpdateDialog(){
  // close events are queued; end the epoch before any late read or reopen.
  clearUpdateObservation();updateDialog.close();
  if(!uiSessionExpired)updateBtn.focus();
}
updateDialog.addEventListener('cancel',event=>{
  event.preventDefault();closeUpdateDialog();
});
updateDialog.addEventListener('close',()=>{
  // A queued close from a prior epoch must not clear a newly opened dialog.
  if(updateDialog.open)return;
  clearUpdateObservation();if(!uiSessionExpired)updateBtn.focus();
});
updateClose.onclick=closeUpdateDialog;
updateRefresh.onclick=pollUpdate;
diagnosticsBtn.onclick=previewDiagnostics;
updateBtn.onclick=startUpdate;
const readinessLabels={
  live_browser_mode:'浏览器模式',chrome_installed:'Chrome 已安装',
  existing_cdp_session:'专用浏览器连接',profile_configured:'资料位置已配置',
  profile_exists:'资料文件可用',profile_loadable:'资料文件可读取',
  deepseek_available:'DeepSeek 配置已加载',supervisor_running:'本地服务运行中'
};
function updateProviderLoadControl(){
  providerLoad.disabled=uiSessionExpired||providerLoadBusy||providerRefreshBusy||providerRefreshUnknown||providerLoadUnknown||providerLoadState!=='not_loaded';
  providerRefresh.disabled=uiSessionExpired||providerLoadBusy||providerRefreshBusy
    ||providerRefreshUnknown||!providerRefreshObservation
    ||!['available','unavailable'].includes(providerLoadState);
}
providerLoad.onclick=async()=>{
  if(uiSessionExpired||providerLoadBusy||providerRefreshBusy||providerRefreshUnknown||providerLoadUnknown||providerLoadState!=='not_loaded'||!readinessDialog.open)return;
  const epoch=providerLoadEpoch;
  providerLoadBusy=true;updateProviderLoadControl();
  providerLoadStatus.textContent='正在读取已配置凭证；没有调用模型或操作任务。';
  try{
    const response=await uiRequest('/ui/api/provider-load',{method:'POST',
      headers:{'Content-Type':'application/json'},credentials:'same-origin',body:'{}'});
    if(!response.ok)throw new Error();
    const result=await response.json();
    if(uiSessionExpired||epoch!==providerLoadEpoch||!readinessDialog.open)return;
    if(result?.provider_state_basis!=='loaded_configuration'||typeof result.loaded!=='boolean'
      ||!['available','unavailable'].includes(result.provider_state)
      ||result.final_click_actor!=='user'||result.submit_capability!==false)throw new Error();
    providerLoadState=result.provider_state;
    providerLoadStatus.textContent=result.loaded&&result.provider_state==='available'?
      '已加载本机配置；尚未验证模型连接，没有开始任务。':
      '已配置凭证暂不可用；没有开始任务。请核对现有模型配置或系统提示。';
    await readiness();await observeProviderRefresh(epoch);
  }catch(_){
    if(!uiSessionExpired&&epoch===providerLoadEpoch&&readinessDialog.open){
      providerLoadUnknown=true;
      providerLoadStatus.textContent='检查结果未确认；不会自动重试或操作任务。可关闭此页后重新查看状态。';
    }
  }finally{
    providerLoadBusy=false;updateProviderLoadControl();
  }
};

function validProviderRefresh(value,receipt=false){
  const keys=['configuration_version','refresh_revision','provider_state','provider_state_basis',
    'final_click_actor','submit_capability',...(receipt?['refresh_status']:[])];
  return value&&typeof value==='object'&&!Array.isArray(value)
    &&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key))
    &&typeof value.configuration_version==='string'&&/^[a-f0-9]{64}$/.test(value.configuration_version)
    &&Number.isSafeInteger(value.refresh_revision)&&value.refresh_revision>=0
    &&['not_loaded','available','unavailable'].includes(value.provider_state)
    &&value.provider_state_basis==='loaded_configuration'
    &&value.final_click_actor==='user'&&value.submit_capability===false
    &&(!receipt||['refreshed','failed'].includes(value.refresh_status))
    &&(!receipt||value.refresh_status!=='failed'||value.provider_state==='not_loaded')
    &&(!receipt||value.refresh_status!=='refreshed'||value.provider_state!=='not_loaded');
}
async function observeProviderRefresh(epoch){
  try{
    const response=await uiRequest('/ui/api/provider-refresh',{credentials:'same-origin'});
    if(!response.ok)throw new Error();
    const value=await response.json();
    if(uiSessionExpired||epoch!==providerLoadEpoch||!readinessDialog.open)return;
    if(!validProviderRefresh(value))throw new Error();
    providerRefreshObservation=value;
  }catch(_){
    if(!uiSessionExpired&&epoch===providerLoadEpoch&&readinessDialog.open)
      providerRefreshObservation=null;
  }finally{updateProviderLoadControl();}
}
providerRefresh.onclick=async()=>{
  if(uiSessionExpired||providerLoadBusy||providerRefreshBusy||providerRefreshUnknown
    ||!readinessDialog.open||!providerRefreshObservation
    ||!['available','unavailable'].includes(providerLoadState))return;
  const epoch=providerLoadEpoch,expected=providerRefreshObservation;
  providerRefreshBusy=true;providerRefreshObservation=null;updateProviderLoadControl();
  providerLoadStatus.textContent='正在重新读取现有配置；没有调用模型或操作任务。';
  try{
    const response=await uiRequest('/ui/api/provider-refresh',{method:'POST',
      headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({expected_settings_version:expected.configuration_version,
        expected_refresh_revision:expected.refresh_revision})});
    if(!response.ok)throw new Error();
    const result=await response.json();
    if(uiSessionExpired||epoch!==providerLoadEpoch||!readinessDialog.open)return;
    if(!validProviderRefresh(result,true)||result.configuration_version!==expected.configuration_version
      ||result.refresh_revision!==expected.refresh_revision+1)throw new Error();
    const readback=await uiRequest('/ui/api/provider-refresh',{credentials:'same-origin'});
    if(!readback.ok)throw new Error();
    const current=await readback.json();
    if(uiSessionExpired||epoch!==providerLoadEpoch||!readinessDialog.open)return;
    if(!validProviderRefresh(current)||current.configuration_version!==result.configuration_version
      ||current.refresh_revision!==result.refresh_revision||current.provider_state!==result.provider_state)throw new Error();
    providerRefreshObservation=current;providerLoadState=current.provider_state;
    providerLoadStatus.textContent=result.refresh_status==='failed'?
      '重新读取失败，旧客户端已停止使用；没有开始任务。请核对现有配置或系统提示。':
      current.provider_state==='available'?
        '已重新读取本机配置；尚未验证模型连接，没有开始任务。':
        '已重新读取配置，但凭证暂不可用；没有开始任务。请核对现有配置或系统提示。';
    await readiness();
  }catch(_){
    if(!uiSessionExpired&&epoch===providerLoadEpoch&&readinessDialog.open){
      providerRefreshUnknown=true;providerRefreshObservation=null;
      providerLoadStatus.textContent='刷新结果未确认；不会自动重试。可关闭此页后重新查看状态。';
    }
  }finally{providerRefreshBusy=false;updateProviderLoadControl();}
};
function renderReadinessDetails(data){
  providerLoadState=['not_loaded','available','unavailable'].includes(data?.provider_state)?data.provider_state:'unknown';
  updateProviderLoadControl();
  readinessChecks.replaceChildren();
  const checks=data&&data.checks&&typeof data.checks==='object'?data.checks:{};
  for(const [key,label] of Object.entries(readinessLabels)){
    const row=document.createElement('li'),name=document.createElement('span'),result=document.createElement('strong');
    name.textContent=label;
    const passed=checks[key]===true;
    result.textContent=passed?'已通过':'待处理';result.className=passed?'pass':'pending';
    row.append(name,result);readinessChecks.appendChild(row);
  }
  readinessSummary.textContent=data?.ready_for_live_e2e===true?
    '运行条件已就绪。最终提交仍由你本人完成。':
    (typeof data?.message==='string'&&data.message?'尚未就绪：'+data.message:'运行条件待检查；任务不会自动提交。');
}
readinessBtn.onclick=()=>{providerLoadEpoch++;providerLoadUnknown=false;providerRefreshUnknown=false;
  providerRefreshObservation=null;providerLoadStatus.textContent='';
  readinessDialog.showModal();updateProviderLoadControl();readinessClose.focus();
  void readiness();void observeProviderRefresh(providerLoadEpoch);};
readinessClose.onclick=()=>readinessDialog.close();
readinessDialog.addEventListener('close',()=>{providerLoadEpoch++;providerRefreshObservation=null;
  providerLoadStatus.textContent='';updateProviderLoadControl();
  if(!uiSessionExpired)readinessBtn.focus();});
async function readiness(){
  if(uiSessionExpired)return;
  const label=document.getElementById('readiness');
  try{
    const r=await uiRequest('/ui/api/readiness',{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    label.textContent=data.ready_for_live_e2e?'已就绪 · 最终提交由你确认':data.message||'运行条件待检查';
    renderReadinessDetails(data);
  }catch(e){
    if(uiSessionExpired)return;
    label.textContent='无法检查运行条件；任务不会自动提交';
    readinessSummary.textContent='无法读取检查结果；任务不会自动提交。';
    readinessChecks.replaceChildren();
  }
}
async function state(){
  if(uiSessionExpired)return;
  try{
    const r=await uiRequest('/ui/api/state',{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    acceptSavedView(data);
    // Reconcile even while an unsent answer or private review prevents a card rerender.
    reconcilePreparationTasks(data.tasks);
    const openReview=tasksEl.querySelector('[data-private-review-open]');
    if(openReview){
      const current=(data.tasks||[]).find(t=>t.task_id===openReview.dataset.privateReviewOpen);
      if(!current||current.stage!=='READY_TO_SUBMIT'||current.revision!==Number(openReview.dataset.revision)||!current.review_values_available)render(data);
    }else if(![...tasksEl.querySelectorAll('.factinput input:not([type=checkbox]),.factinput select,.otpinput input')].some(input=>input.value))render(data);
    updateLabel(data.update);return data;
  }catch(e){if(!uiSessionExpired)document.getElementById('health').textContent='连接异常';return null;}
}
function bubble(text,kind,actions){
  const d=document.createElement('div');d.className='bubble '+kind;d.textContent=text;
  if(actions&&actions.length){const a=document.createElement('div');a.className='actions';a.textContent=actions.map(x=>`${x.action}: ${x.status}`).join(' · ');d.appendChild(a)}
  chat.appendChild(d);chat.scrollTop=chat.scrollHeight;
}
async function submit(){
  if(uiSessionExpired)return;
  const unsent=msg.value,text=unsent.trim();if(!text)return;
  send.disabled=true;
  try{
    const r=await uiRequest('/ui/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({message:text})});
    const data=await r.json();if(uiSessionExpired)throw new Error('ui_session_expired');
    if(!r.ok)throw new Error(data.error||'request failed');
    bubble('消息已在本地处理','me');if(msg.value===unsent)msg.value='';
    bubble(data.reply||'已处理。','ai',data.actions||[]);render(data);
  }catch(e){if(!uiSessionExpired)bubble('请求未确认；输入仍保留，请核对任务状态后再决定是否重试。','ai')}
  finally{
    try{
      const r=await uiRequest('/ui/api/update-status',{credentials:'same-origin'});
      const update=r.ok?await r.json():{status:'idle'};
      updateLabel(update);
    }catch(e){updateLabel(legacyUpdateObservation)}
    if(!uiSessionExpired)msg.focus();
  }
}
send.onclick=submit;msg.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit()}});
state();readiness();setInterval(state,2500);setInterval(readiness,10000);
</script>
</body>
</html>"""
