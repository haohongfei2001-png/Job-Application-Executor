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
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:16px 0}
.metric{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
.metric b{display:block;font-size:20px}.task{border:1px solid #e5e7eb;border-radius:12px;padding:11px;margin:8px 0;background:#fff}
.task .title{font-weight:650}.task .meta{font-size:12px;color:#64748b;margin-top:4px}.stage{font-size:11px;border-radius:999px;padding:3px 7px;background:#eef2ff;display:inline-block;margin-top:7px}
.taskcontrols{display:flex;gap:6px;margin-top:9px}.taskcontrols button{font-size:12px;padding:6px 9px;background:#f1f5f9;color:#111;border:1px solid #d7dce2}
.factinput{display:flex;gap:5px;margin-top:8px}.factinput input{min-width:0;flex:1;border:1px solid #cbd5e1;border-radius:7px;padding:7px}.factinput button{font-size:12px;padding:6px 8px;background:#e2e8f0;color:#111}
.newtask{display:grid;gap:7px;margin:12px 0 17px}.newtask input{width:100%;border:1px solid #cbd5e1;border-radius:7px;padding:8px}.newtask button{padding:9px}
.chat{padding:20px 24px;overflow:auto}.bubble{max-width:820px;padding:11px 13px;border-radius:13px;margin:8px 0;white-space:pre-wrap;line-height:1.5}
.me{margin-left:auto;background:#111;color:#fff}.ai{background:#fff;border:1px solid #e5e7eb}.actions{font-size:12px;color:#64748b;margin-top:5px}
.composer{background:#fff;border-top:1px solid #e5e7eb;padding:14px 20px;display:flex;gap:10px}
textarea{flex:1;min-height:52px;max-height:160px;resize:vertical;border:1px solid #cbd5e1;border-radius:12px;padding:12px;font:inherit}
button{border:0;border-radius:10px;background:#111;color:#fff;padding:0 18px;font-weight:600;cursor:pointer}
button:disabled{opacity:.45}.empty{color:#94a3b8;font-size:13px}.error{color:#b91c1c}.toast{position:fixed;right:22px;bottom:88px;max-width:420px;background:#111;color:#fff;padding:11px 14px;border-radius:10px;box-shadow:0 10px 30px #0003;display:none;z-index:20;font-size:13px;line-height:1.45}
@media(max-width:820px){.shell{grid-template-columns:1fr}aside{display:none}}
</style>
</head>
<body>
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
    <strong>添加明确岗位</strong>
    <input name="company" maxlength="150" placeholder="公司" required>
    <input name="role" maxlength="200" placeholder="岗位名称" required>
    <input name="target_url" type="url" maxlength="2000" placeholder="完整岗位链接" required>
    <button type="submit">添加任务</button>
  </form>
  <div id="tasks" class="empty">正在读取任务…</div>
</aside>
<main>
<header>
  <h1>AI 投递经理</h1>
  <div class="statusbar">
    <div><span class="dot"></span><span id="health">本地服务</span></div>
    <span id="readiness" class="readiness" role="status">正在检查运行条件…</span>
    <button id="diagnostics" class="headerbtn" type="button">复制诊断</button>
    <button id="update" class="headerbtn" type="button">检查并更新</button>
  </div>
</header>
<div id="chat" class="chat">
  <div class="bubble ai">请在左侧填写明确岗位；任务控制可在任务卡片操作，私人资料也请在任务卡片本地填写。遇到需要你决定或安全验证的地方会停下来，最终提交由你本人完成。</div>
</div>
<div class="composer">
  <textarea id="message" placeholder="查看任务状态；添加岗位请使用左侧表单，私人资料请在任务卡片填写…"></textarea>
  <button id="send">发送</button>
</div>
</main>
</div>
<div id="toast" class="toast"></div>
<script>
const tasksEl=document.getElementById('tasks'),chat=document.getElementById('chat'),msg=document.getElementById('message'),send=document.getElementById('send'),diagnosticsBtn=document.getElementById('diagnostics'),updateBtn=document.getElementById('update'),toast=document.getElementById('toast');
const newTaskForm=document.getElementById('newtask');
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
function render(state){
  const counts={running:0,need:0,ready:0,done:0};
  (state.tasks||[]).forEach(t=>counts[stageGroup(t.stage)]++);
  Object.entries(counts).forEach(([k,v])=>document.getElementById(k).textContent=v);
  document.getElementById('health').textContent='本地服务已连接';
  if(!(state.tasks||[]).length){tasksEl.className='empty';tasksEl.textContent='暂无任务';return}
  tasksEl.className='';
  tasksEl.innerHTML=(state.tasks||[]).map(t=>`
    <div class="task">
      <div class="title">${esc(t.company)} · ${esc(t.role)}</div>
      <div class="meta">${esc(t.target_host||'')} ${t.blocker?'· '+esc(humanBlocker(t.blocker)):''}</div>
      <span class="stage">${esc(humanStage(t.stage))}</span>
      ${t.stage==='NEEDS_USER_INPUT'?(t.unresolved_keys||[]).map(key=>`
        <label class="factinput"><span>${esc(key)}</span><input autocomplete="off" aria-label="${esc(key)}">
          <button type="button" data-answer="true" data-key="${esc(key)}" data-task="${esc(t.task_id)}" data-revision="${t.revision}">本地填写</button></label>`).join(''):''}
      <div class="taskcontrols">
        ${!['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION','READY_TO_SUBMIT','SUBMITTED','VERIFIED','CANCELLED'].includes(t.stage)?`<button type="button" data-action="PAUSE" data-task="${esc(t.task_id)}" data-revision="${t.revision}">暂停</button>`:''}
        ${['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION'].includes(t.stage)?`<button type="button" data-action="RESUME" data-task="${esc(t.task_id)}" data-revision="${t.revision}">继续</button>`:''}
        ${!['SUBMITTED','VERIFIED','CANCELLED','READY_TO_SUBMIT'].includes(t.stage)?`<button type="button" data-action="CANCEL" data-task="${esc(t.task_id)}" data-revision="${t.revision}">取消</button>`:''}
      </div>
    </div>`).join('');
}
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-action]');if(!button)return;
  button.disabled=true;
  const action=button.dataset.action,task_id=button.dataset.task;
  const expected_revision=Number(button.dataset.revision);
  const command_id='ui-'+crypto.randomUUID();
  try{
    const r=await fetch('/ui/api/command',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({command_id,task_id,action,expected_revision})});
    if(!r.ok)throw new Error();
    notify({PAUSE:'任务已暂停',RESUME:'任务已继续',CANCEL:'任务已取消'}[action]);
    await state();
  }catch(e){notify('任务状态已变化，请刷新后重试。');await state()}
});
newTaskForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=newTaskForm.querySelector('button');button.disabled=true;
  const data=Object.fromEntries(new FormData(newTaskForm).entries());
  try{
    const r=await fetch('/ui/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'same-origin',body:JSON.stringify(data)});
    if(!r.ok)throw new Error();
    newTaskForm.reset();notify('任务已添加；目标核验完成前不会自动写入招聘网站。');await state();
  }catch(e){notify('无法安全添加任务，请核对岗位链接、配置与已有任务。')}
  finally{button.disabled=false}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-answer]');if(!button)return;
  const input=button.previousElementSibling,value=input.value;
  if(!value.trim()){notify('请先填写答案。');return}
  button.disabled=true;
  try{
    const r=await fetch('/ui/api/user-input',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({task_id:button.dataset.task,field_key:button.dataset.key,
        value,expected_revision:Number(button.dataset.revision)})});
    if(!r.ok)throw new Error();
    input.value='';
    notify('答案已在本地交给当前任务。');await state();
  }catch(e){
    try{
      const r=await fetch('/ui/api/state',{credentials:'same-origin'}),data=await r.json();
      const task=(data.tasks||[]).find(t=>t.task_id===button.dataset.task);
      if(task&&task.stage==='NEEDS_USER_INPUT'&&(task.unresolved_keys||[]).includes(button.dataset.key)){
        button.dataset.revision=String(task.revision);
        notify('任务状态已变化；答案仍在本地，请核对后重试。');
      }else{notify('任务不再等待这个答案；未提交输入。')}
    }catch(_){notify('本地服务暂不可用；答案仍在输入框中。')}
  }finally{button.disabled=false}
});
function notify(text){
  toast.textContent=text;toast.style.display='block';
  clearTimeout(notify.timer);notify.timer=setTimeout(()=>{toast.style.display='none'},4200);
}
function updateLabel(update){
  const status=(update||{}).status||'idle';
  const busy=['checking','updating','restarting'].includes(status);
  const restartRequired=status==='restart_required';
  updateBtn.disabled=busy;
  msg.disabled=busy||restartRequired;
  send.disabled=busy||restartRequired;
  if(busy){
    updateBtn.textContent=status==='checking'?'正在检查…':status==='updating'?'正在更新…':'正在重启…';
  }else if(restartRequired){
    updateBtn.textContent='重试重启';
  }else{
    updateBtn.textContent='检查并更新';
  }
}
async function copyDiagnostics(){
  diagnosticsBtn.disabled=true;
  try{
    const r=await fetch('/ui/api/diagnostics',{credentials:'same-origin'});
    const data=await r.json();
    if(!r.ok)throw new Error();
    const text=JSON.stringify(data,null,2);
    try{
      await navigator.clipboard.writeText(text);
    }catch(copyError){
      const helper=document.createElement('textarea');
      helper.value=text;helper.setAttribute('readonly','');helper.style.position='fixed';helper.style.opacity='0';
      document.body.appendChild(helper);helper.select();
      if(!document.execCommand('copy'))throw copyError;
      helper.remove();
    }
    notify('诊断信息已复制。可以直接粘贴给 ChatGPT。');
  }catch(e){
    notify('复制诊断失败；现有任务未被修改。');
  }finally{diagnosticsBtn.disabled=false}
}
async function startUpdate(){
  updateBtn.disabled=true;updateBtn.textContent='正在检查…';
  try{
    const r=await fetch('/ui/api/update',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      credentials:'same-origin',
      body:'{}'
    });
    const data=await r.json();
    if(!r.ok){
      const reason={
        worker_active:'当前正在执行真实任务，请等任务停在安全节点后再更新。',
        runnable_task_pending:'还有可立即执行的任务，请先暂停或等它停在安全节点。',
        otp_in_flight:'正在等待或处理短信验证码，此时不能更新。',
        update_in_progress:'已经有一次更新正在进行，不会重复启动。',
        stale_update_recovered:'检测到上次更新被中断，已解除锁定；可以重新检查更新。',
        not_on_main:'当前代码不在 main 分支，已拒绝自动更新。',
        tracked_changes_present:'本地有未提交代码修改，已拒绝自动更新。',
        unexpected_origin:'GitHub 来源不符合预期，已拒绝自动更新。'
      }[data.reason]||'当前不能安全更新。';
      notify(reason);updateBtn.disabled=false;updateBtn.textContent='检查并更新';return;
    }
    notify('正在检查 GitHub 并安全更新。若有新版本，服务会自动重启并重新打开面板。');
    setTimeout(pollUpdate,900);
  }catch(e){
    notify('更新请求失败；当前版本和任务均保持不变。');
    updateBtn.disabled=false;updateBtn.textContent='检查并更新';
  }
}
async function pollUpdate(){
  try{
    const r=await fetch('/ui/api/update-status',{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();updateLabel(data);
    if(['checking','updating','restarting'].includes(data.status)){
      setTimeout(pollUpdate,1000);return;
    }
    if(data.status==='up_to_date')notify('已经是最新版本。');
    if(data.status==='success')notify('更新完成，正在打开新版本。');
    if(data.status==='restart_required'){
      notify('代码已更新，但本地服务还需要重新启动。点击“重试重启”即可继续。');
    }
    if(data.status==='failed'){
      const reason={
        worker_active:'更新取消：当前仍有任务在执行。',
        runnable_task_pending:'更新取消：还有可立即执行的任务。',
        otp_in_flight:'更新取消：短信验证码流程正在进行。',
        not_on_main:'更新取消：当前不在 main 分支。',
        tracked_changes_present:'更新取消：本地有未提交的代码修改。',
        unexpected_origin:'更新取消：GitHub 来源不符合预期。',
        repository_changed_during_update:'更新取消：检查期间本地代码状态发生了变化。',
        remote_changed_during_update:'更新取消：检查期间远端 main 又发生了变化。',
        stale_update_recovered:'检测到上次更新被中断，已解除更新锁定。',
        fast_forward_required:'更新取消：远端无法安全快进到本地。',
        service_stop_failed:'代码已检查，但服务没有安全停止。',
        service_start_failed:'代码已更新，但服务未能自动重启；重新打开 AI 投递经理即可重试启动。'
      }[data.reason]||'更新没有完成；当前任务和已有版本保持安全。';
      notify(reason);
    }
  }catch(e){
    // During a successful restart this old session disappears. The updater
    // opens a new authenticated UI, so no destructive retry is attempted here.
  }
}
diagnosticsBtn.onclick=copyDiagnostics;
updateBtn.onclick=startUpdate;
async function readiness(){
  const label=document.getElementById('readiness');
  try{
    const r=await fetch('/ui/api/readiness',{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();
    label.textContent=data.ready_for_live_e2e?'已就绪 · 最终提交由你确认':data.message||'运行条件待检查';
  }catch(e){label.textContent='无法检查运行条件；任务不会自动提交'}
}
async function state(){
  try{
    const r=await fetch('/ui/api/state',{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();
    if(![...tasksEl.querySelectorAll('.factinput input')].some(input=>input.value))render(data);
    updateLabel(data.update);
  }catch(e){document.getElementById('health').textContent='连接异常'}
}
function bubble(text,kind,actions){
  const d=document.createElement('div');d.className='bubble '+kind;d.textContent=text;
  if(actions&&actions.length){const a=document.createElement('div');a.className='actions';a.textContent=actions.map(x=>`${x.action}: ${x.status}`).join(' · ');d.appendChild(a)}
  chat.appendChild(d);chat.scrollTop=chat.scrollHeight;
}
async function submit(){
  const text=msg.value.trim();if(!text)return;
  bubble('消息已在本地处理','me');msg.value='';send.disabled=true;
  try{
    const r=await fetch('/ui/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({message:text})});
    const data=await r.json();
    if(!r.ok)throw new Error(data.error||'request failed');
    bubble(data.reply||'已处理。','ai',data.actions||[]);render(data);
  }catch(e){bubble('请求失败；现有任务未被修改。','ai')}
  finally{
    try{
      const r=await fetch('/ui/api/update-status',{credentials:'same-origin'});
      const update=r.ok?await r.json():{status:'idle'};
      updateLabel(update);
    }catch(e){send.disabled=false;msg.disabled=false}
    msg.focus();
  }
}
send.onclick=submit;msg.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit()}});
state();readiness();setInterval(state,2500);setInterval(readiness,10000);
</script>
</body>
</html>"""
