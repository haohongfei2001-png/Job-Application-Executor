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
header{padding:18px 22px;background:#fff;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between}
h1{font-size:18px;margin:0}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#16a34a;margin-right:7px}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:16px 0}
.metric{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
.metric b{display:block;font-size:20px}.task{border:1px solid #e5e7eb;border-radius:12px;padding:11px;margin:8px 0;background:#fff}
.task .title{font-weight:650}.task .meta{font-size:12px;color:#64748b;margin-top:4px}.stage{font-size:11px;border-radius:999px;padding:3px 7px;background:#eef2ff;display:inline-block;margin-top:7px}
.chat{padding:20px 24px;overflow:auto}.bubble{max-width:820px;padding:11px 13px;border-radius:13px;margin:8px 0;white-space:pre-wrap;line-height:1.5}
.me{margin-left:auto;background:#111;color:#fff}.ai{background:#fff;border:1px solid #e5e7eb}.actions{font-size:12px;color:#64748b;margin-top:5px}
.composer{background:#fff;border-top:1px solid #e5e7eb;padding:14px 20px;display:flex;gap:10px}
textarea{flex:1;min-height:52px;max-height:160px;resize:vertical;border:1px solid #cbd5e1;border-radius:12px;padding:12px;font:inherit}
button{border:0;border-radius:10px;background:#111;color:#fff;padding:0 18px;font-weight:600;cursor:pointer}
button:disabled{opacity:.45}.empty{color:#94a3b8;font-size:13px}.error{color:#b91c1c}
@media(max-width:820px){.shell{grid-template-columns:1fr}aside{display:none}}
</style>
</head>
<body>
<div class="shell">
<aside>
  <h1>任务</h1>
  <div class="metrics">
    <div class="metric"><span>运行/待处理</span><b id="running">0</b></div>
    <div class="metric"><span>需要你</span><b id="need">0</b></div>
    <div class="metric"><span>待提交</span><b id="ready">0</b></div>
    <div class="metric"><span>完成/取消</span><b id="done">0</b></div>
  </div>
  <div id="tasks" class="empty">正在读取任务…</div>
</aside>
<main>
<header>
  <h1>AI 投递经理</h1>
  <div><span class="dot"></span><span id="health">本地服务</span></div>
</header>
<div id="chat" class="chat">
  <div class="bubble ai">你可以直接说：继续某个岗位、取消某个岗位、回答一个待确认字段，或粘贴精确职位链接开始申请。最终提交永远由你本人完成。</div>
</div>
<div class="composer">
  <textarea id="message" placeholder="给 DeepSeek 投递经理下达指令…"></textarea>
  <button id="send">发送</button>
</div>
</main>
</div>
<script>
const tasksEl=document.getElementById('tasks'),chat=document.getElementById('chat'),msg=document.getElementById('message'),send=document.getElementById('send');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
  document.getElementById('health').textContent=state.final_click_actor==='user'?'运行中 · 最终提交由你完成':'本地服务';
  if(!(state.tasks||[]).length){tasksEl.className='empty';tasksEl.textContent='暂无任务';return}
  tasksEl.className='';
  tasksEl.innerHTML=(state.tasks||[]).map(t=>`
    <div class="task">
      <div class="title">${esc(t.company)} · ${esc(t.role)}</div>
      <div class="meta">${esc(t.target_host||'')} ${t.blocker?'· '+esc(t.blocker):''}</div>
      <span class="stage">${esc(t.stage)}</span>
    </div>`).join('');
}
async function state(){
  try{
    const r=await fetch('/ui/api/state',{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    render(await r.json());
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
