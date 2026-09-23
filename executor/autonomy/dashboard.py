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
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:16px 0}
.metric{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
.metric b{display:block;font-size:20px}.task{border:1px solid #e5e7eb;border-radius:12px;padding:11px;margin:8px 0;background:#fff}
.task .title{font-weight:650}.task .meta{font-size:12px;color:#64748b;margin-top:4px}.stage{font-size:11px;border-radius:999px;padding:3px 7px;background:#eef2ff;display:inline-block;margin-top:7px}
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
  <div id="tasks" class="empty">正在读取任务…</div>
</aside>
<main>
<header>
  <h1>AI 投递经理</h1>
  <div class="statusbar">
    <div><span class="dot"></span><span id="health">本地服务</span></div>
    <button id="diagnostics" class="headerbtn" type="button">复制诊断</button>
    <button id="update" class="headerbtn" type="button">检查并更新</button>
  </div>
</header>
<div id="chat" class="chat">
  <div class="bubble ai">把职位链接发给我并说“投递这个岗位”，或者直接说“继续”“暂停”“取消”。我会处理能自动完成的步骤；遇到需要你决定或安全验证的地方会停下来。最终提交由你确认。</div>
</div>
<div class="composer">
  <textarea id="message" placeholder="告诉我你想投哪个岗位，或直接说“继续这个岗位”…"></textarea>
  <button id="send">发送</button>
</div>
</main>
</div>
<div id="toast" class="toast"></div>
<script>
const tasksEl=document.getElementById('tasks'),chat=document.getElementById('chat'),msg=document.getElementById('message'),send=document.getElementById('send'),diagnosticsBtn=document.getElementById('diagnostics'),updateBtn=document.getElementById('update'),toast=document.getElementById('toast');
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
  document.getElementById('health').textContent=state.final_click_actor==='user'?'已就绪 · 最终提交由你确认':'本地服务';
  if(!(state.tasks||[]).length){tasksEl.className='empty';tasksEl.textContent='暂无任务';return}
  tasksEl.className='';
  tasksEl.innerHTML=(state.tasks||[]).map(t=>`
    <div class="task">
      <div class="title">${esc(t.company)} · ${esc(t.role)}</div>
      <div class="meta">${esc(t.target_host||'')} ${t.blocker?'· '+esc(humanBlocker(t.blocker)):''}</div>
      <span class="stage">${esc(humanStage(t.stage))}</span>
    </div>`).join('');
}
function notify(text){
  toast.textContent=text;toast.style.display='block';
  clearTimeout(notify.timer);notify.timer=setTimeout(()=>{toast.style.display='none'},4200);
}
function updateLabel(update){
  const status=(update||{}).status||'idle';
  if(['checking','updating','restarting'].includes(status)){
    updateBtn.disabled=true;
    updateBtn.textContent=status==='checking'?'正在检查…':status==='updating'?'正在更新…':'正在重启…';
  }else{
    updateBtn.disabled=false;
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
    if(data.status==='failed')notify('更新没有完成；当前任务和已有版本保持安全。');
  }catch(e){
    // During a successful restart this old session disappears. The updater
    // opens a new authenticated UI, so no destructive retry is attempted here.
  }
}
diagnosticsBtn.onclick=copyDiagnostics;
updateBtn.onclick=startUpdate;
async function state(){
  try{
    const r=await fetch('/ui/api/state',{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();render(data);updateLabel(data.update);
  }catch(e){document.getElementById('health').textContent='连接异常'}
}
function bubble(text,kind,actions){
  const d=document.createElement('div');d.className='bubble '+kind;d.textContent=text;
  if(actions&&actions.length){const a=document.createElement('div');a.className='actions';a.textContent=actions.map(x=>`${x.action}: ${x.status}`).join(' · ');d.appendChild(a)}
  chat.appendChild(d);chat.scrollTop=chat.scrollHeight;
}
async function submit(){
  const text=msg.value.trim();if(!text)return;
  bubble(text,'me');msg.value='';send.disabled=true;
  try{
    const r=await fetch('/ui/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({message:text})});
    const data=await r.json();
    if(!r.ok)throw new Error(data.error||'request failed');
    bubble(data.reply||'已处理。','ai',data.actions||[]);render(data);
  }catch(e){bubble('请求失败；现有任务未被修改。','ai')}
  finally{send.disabled=false;msg.focus()}
}
send.onclick=submit;msg.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit()}});
state();setInterval(state,2500);
</script>
</body>
</html>"""
