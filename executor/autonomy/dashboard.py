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
#session-expired{margin:0;padding:16px 22px;background:#fff7ed;color:#9a3412;border-bottom:1px solid #fed7aa;line-height:1.5}
.toast{position:fixed;right:22px;bottom:88px;max-width:420px;background:#111;color:#fff;padding:11px 14px;border-radius:10px;box-shadow:0 10px 30px #0003;display:none;z-index:20;font-size:13px;line-height:1.45}
@media(max-width:820px){.shell{grid-template-columns:1fr}aside{display:block;max-height:45vh;border-right:0;border-bottom:1px solid #e5e7eb}main{min-height:55vh}}
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
  <p id="profile-status" role="status">正在读取设置…</p>
  <label for="profile-file">选择资料文件（JSON，最多 256 KB）</label>
  <input id="profile-file" type="file" accept=".json,application/json" disabled>
  <div class="diagnostics-actions">
    <button id="profile-close" class="headerbtn" type="button">关闭</button>
    <button id="profile-save" type="button" disabled>保存资料</button>
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
  updateEpoch++;updateReadBusy=false;updateRefresh.disabled=true;
  updateObservation.textContent='';if(updateDialog.open)closeUpdateDialog();
  providerLoadEpoch++;providerLoad.disabled=true;providerRefresh.disabled=true;
  providerRefreshObservation=null;providerLoadStatus.textContent='';
  currentTaskId=null;savedView=null;updateTaskContext();
  clearProfileSelection();if(profileDialog.open)profileDialog.close();
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
  acceptSavedView(state);
  const counts={running:0,need:0,ready:0,done:0};
  (state.tasks||[]).forEach(t=>counts[stageGroup(t.stage)]++);
  Object.entries(counts).forEach(([k,v])=>document.getElementById(k).textContent=v);
  document.getElementById('health').textContent='本地服务已连接';
  const focusedTask=document.activeElement?.dataset?.taskSelect;
  if(!(state.tasks||[]).length){
    tasksEl.className='empty';tasksEl.textContent='暂无任务';updateTaskContext();
    if(focusedTask!==undefined)taskContext.focus();
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
      <div class="taskcontrols">
        ${t.stage==='READY_TO_SUBMIT'&&t.review_values_available?`<button type="button" data-review-values="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}" aria-expanded="false" aria-controls="review-${esc(t.task_id)}">查看完整复核值</button>`:''}
        ${t.stage==='READY_TO_SUBMIT'?`<button type="button" data-observe-submission="true" data-task="${esc(t.task_id)}">只读查看提交结果</button>`:''}
        ${t.stage==='READY_TO_SUBMIT'&&t.can_confirm_submission?`<button type="button" data-confirm-submission="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}">我已在招聘网站亲自提交</button>`:''}
        ${!['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION','READY_TO_SUBMIT','SUBMITTED','VERIFIED','CANCELLED'].includes(t.stage)?`<button type="button" data-action="PAUSE" data-task="${esc(t.task_id)}" data-revision="${t.revision}">暂停</button>`:''}
        ${['unknown_outcome','browser_ownership_unknown','user_paused_from_unknown_outcome','user_paused_from_browser_ownership_unknown'].includes(t.blocker)?`<button type="button" data-action="OBSERVE" data-task="${esc(t.task_id)}">只读核对</button>`:''}
      ${['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION'].includes(t.stage)&&t.blocker!=='otp_waiting'&&!['unknown_outcome','browser_ownership_unknown','user_paused_from_unknown_outcome','user_paused_from_browser_ownership_unknown','auth_return_unverified','account_identity_unverified','draft_persistence_unverified'].includes(t.blocker)?`<button type="button" data-action="RESUME" data-task="${esc(t.task_id)}" data-revision="${t.revision}">继续</button>`:''}
        ${!['SUBMITTED','VERIFIED','CANCELLED','READY_TO_SUBMIT'].includes(t.stage)?`<button type="button" data-action="CANCEL" data-task="${esc(t.task_id)}" data-revision="${t.revision}">取消</button>`:''}
      </div>
      ${t.stage==='READY_TO_SUBMIT'?`<div id="review-${esc(t.task_id)}" class="review private-review" data-private-review-panel role="region" aria-label="完整申请复核" tabindex="-1" hidden></div>`:''}
    </div>`).join('');
  updateTaskContext();
  if(focusedTask!==undefined){
    const restored=[...tasksEl.querySelectorAll('[data-task-select]')].find(button=>button.dataset.taskSelect===focusedTask);
    (restored||taskContext).focus({preventScroll:true});
  }
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
    if(result.task_id){newTaskForm.reset();await state()}
  }catch(e){notify('暂时无法安全查找岗位；请核对公司、岗位和官方链接。')}
  finally{button.disabled=uiSessionExpired}
});
function showDiscovery(result,request){
  const discovery=result.discovery||{};
  if(result.task_id){pendingDiscovery=null;candidatesEl.replaceChildren();notify(discovery.status==='VERIFIED'?'已核验并添加明确岗位；准备草稿前会再次检查授权。':'已添加待核验任务；不会自动写入招聘网站。');return}
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
    if(result.task_id){newTaskForm.reset();await state()}
  }catch(e){notify('候选已变化或暂时无法核验，请重新查找。');button.disabled=uiSessionExpired}
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
