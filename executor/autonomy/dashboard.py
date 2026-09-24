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
.review{font-size:12px;line-height:1.5;margin-top:9px;padding:9px;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0}.review.warning{background:#fff7ed;border-color:#fed7aa;color:#9a3412}
.private-review{max-height:52vh;overflow:auto;white-space:pre-wrap}.private-review table{width:100%;border-collapse:collapse;margin-top:8px}.private-review th,.private-review td{border:1px solid #cbd5e1;padding:6px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
.taskcontrols{display:flex;gap:6px;margin-top:9px}.taskcontrols button{font-size:12px;padding:6px 9px;background:#f1f5f9;color:#111;border:1px solid #d7dce2}
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
  <div class="bubble ai">可按公司和岗位查找官方招聘信息；同名岗位会请你选择。任务控制可在任务卡片操作，私人资料请在任务卡片本地填写。最终提交由你本人完成。</div>
</div>
<div class="composer">
  <textarea id="message" placeholder="查看任务状态；添加岗位请使用左侧表单查找，私人资料请在任务卡片填写…"></textarea>
  <button id="send">发送</button>
</div>
</main>
</div>
<div id="toast" class="toast"></div>
<script>
const tasksEl=document.getElementById('tasks'),chat=document.getElementById('chat'),msg=document.getElementById('message'),send=document.getElementById('send'),diagnosticsBtn=document.getElementById('diagnostics'),updateBtn=document.getElementById('update'),toast=document.getElementById('toast');
const newTaskForm=document.getElementById('newtask');
const candidatesEl=document.getElementById('candidates');let pendingDiscovery=null;
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
    <p>附件：${files?`<ul>${files}</ul>`:'无'}</p>
    <p>项目核验：${esc(coverage.status||'未知')} · 规范项目 ${esc((coverage.canonical_projects||[]).join('、'))} · 明确排除 ${esc((coverage.explicit_exclusions||[]).join('、'))}</p>
    <p>服务端结构化行只保存身份和摘要；请在招聘页面核对每条内容。</p>`;
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
      ${t.stage==='READY_TO_SUBMIT'?(t.review_summary?.status==='last_verified'?`
        <div class="review">上次独立核验：${Number(t.review_summary.field_count)||0} 项填写、${Number(t.review_summary.row_count)||0} 条经历、${Number(t.review_summary.attachment_count)||0} 个附件，${Number(t.review_summary.check_count)||0} 项检查通过。请在申请页面再次核对完整内容；最终提交只能由你本人点击。</div>`:
        '<div class="review warning">核验摘要不可用，请勿提交。任务需要重新核验。</div>'):''}
      ${t.stage==='NEEDS_USER_INPUT'?(t.unresolved_keys||[]).map(key=>`
        <label class="factinput"><span>${esc(key)}</span>${(t.boolean_keys||[]).includes(key)?`<select aria-label="${esc(key)}"><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>`:`<input autocomplete="off" aria-label="${esc(key)}">`}
          <button type="button" data-answer="true" data-key="${esc(key)}" data-task="${esc(t.task_id)}" data-revision="${t.revision}">本地填写</button>
          ${(t.reusable_keys||[]).includes(key)?'<input type="checkbox" class="remember-fact" aria-label="保存为可复用事实"><span>经我确认后记住，供以后申请使用</span>':''}</label>`).join(''):''}
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
        ${t.stage==='READY_TO_SUBMIT'&&t.review_values_available?`<button type="button" data-review-values="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}">查看完整复核值</button>`:''}
        ${t.stage==='READY_TO_SUBMIT'?`<button type="button" data-observe-submission="true" data-task="${esc(t.task_id)}">只读查看提交结果</button>`:''}
        ${t.stage==='READY_TO_SUBMIT'&&t.can_confirm_submission?`<button type="button" data-confirm-submission="true" data-task="${esc(t.task_id)}" data-revision="${t.revision}">我已在招聘网站亲自提交</button>`:''}
        ${!['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION','READY_TO_SUBMIT','SUBMITTED','VERIFIED','CANCELLED'].includes(t.stage)?`<button type="button" data-action="PAUSE" data-task="${esc(t.task_id)}" data-revision="${t.revision}">暂停</button>`:''}
        ${['unknown_outcome','browser_ownership_unknown','user_paused_from_unknown_outcome','user_paused_from_browser_ownership_unknown'].includes(t.blocker)?`<button type="button" data-action="OBSERVE" data-task="${esc(t.task_id)}">只读核对</button>`:''}
      ${['BLOCKED','NEEDS_USER_INPUT','NEEDS_USER_ACTION'].includes(t.stage)&&t.blocker!=='otp_waiting'&&!['unknown_outcome','browser_ownership_unknown','user_paused_from_unknown_outcome','user_paused_from_browser_ownership_unknown','auth_return_unverified','account_identity_unverified','draft_persistence_unverified'].includes(t.blocker)?`<button type="button" data-action="RESUME" data-task="${esc(t.task_id)}" data-revision="${t.revision}">继续</button>`:''}
        ${!['SUBMITTED','VERIFIED','CANCELLED','READY_TO_SUBMIT'].includes(t.stage)?`<button type="button" data-action="CANCEL" data-task="${esc(t.task_id)}" data-revision="${t.revision}">取消</button>`:''}
      </div>
      ${t.stage==='READY_TO_SUBMIT'?'<div class="review private-review" data-private-review-panel hidden></div>':''}
    </div>`).join('');
}
tasksEl.addEventListener('click',event=>{
  const button=event.target.closest('button[data-close-private-review]');if(!button)return;
  const panel=button.closest('[data-private-review-panel]');
  panel.innerHTML='';panel.hidden=true;
  delete panel.dataset.privateReviewOpen;delete panel.dataset.revision;
  state();
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-review-values]');if(!button)return;
  button.disabled=true;
  try{
    const r=await fetch('/ui/api/review-values?task_id='+encodeURIComponent(button.dataset.task),{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const data=await r.json();
    if(data.revision!==Number(button.dataset.revision))throw new Error();
    const panel=button.closest('.task').querySelector('[data-private-review-panel]');
    panel.innerHTML=privateReviewHtml(data.review);
    panel.hidden=false;
    panel.dataset.privateReviewOpen=button.dataset.task;
    panel.dataset.revision=button.dataset.revision;
  }catch(e){notify('完整复核值已过期或暂不可用；请勿据此提交。');await state()}
  finally{button.disabled=false}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-confirm-submission]');if(!button)return;
  if(!window.confirm('请确认你已经在招聘网站亲自点击最终提交。这里仅记录你的确认并保护该岗位，不会代你点击提交。'))return;
  button.disabled=true;
  try{
    const r=await fetch('/ui/api/human-submission',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({task_id:button.dataset.task,expected_revision:Number(button.dataset.revision),user_confirmed:true})});
    if(!r.ok)throw new Error();
    const receipt=await r.json();
    notify(receipt.page_signal?'已记录你的提交确认；页面有成功提示，但服务器结果尚未独立核实。':'已记录你的提交确认；服务器结果尚未独立核实，请保留招聘网站回执。');
    await state();
  }catch(e){notify('确认未记录；请核对当前任务及招聘网站，再重试。');await state()}
  finally{button.disabled=false}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-observe-submission]');if(!button)return;
  button.disabled=true;
  try{
    const r=await fetch('/ui/api/submission-observation?task_id='+encodeURIComponent(button.dataset.task),{credentials:'same-origin'});
    if(!r.ok)throw new Error();
    const observed=await r.json();
    notify(observed.status==='PAGE_SIGNAL_OBSERVED'
      ?'页面出现提交成功提示；这只是页面信号，尚未核实服务器结果。'
      :'未看到可信的提交结果；任务状态未改变。请在招聘站点自行核对。');
  }catch(e){notify('只读查看暂不可用；任务状态未改变。')}
  finally{button.disabled=false}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-action]');if(!button)return;
  button.disabled=true;
  const action=button.dataset.action,task_id=button.dataset.task;
  if(action==='OBSERVE'){
    try{
      const r=await fetch('/ui/api/observe?task_id='+encodeURIComponent(task_id),{credentials:'same-origin'});
      if(!r.ok)throw new Error();
      const observed=await r.json();
      notify(observed.status==='BOUND_DOCUMENT_OBSERVED'?'已找到原任务页面；草稿和写入结果仍待证明，任务保持暂停。':'原任务页面尚无法核实；任务保持暂停。');
    }catch(e){notify('只读核对暂不可用；任务保持暂停。')}
    finally{button.disabled=false}
    return;
  }
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
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-otp-send],button[data-otp-resend]');if(!button)return;
  event.preventDefault();button.disabled=true;
  if(button.dataset.otpSend){
    const input=button.parentElement.querySelector('input[data-otp-task]');
    const message=input.value.trim();input.value='';
    if(!/^\d{4,8}$/.test(message)){notify('请输入当前短信中的 4 到 8 位验证码。');button.disabled=false;return}
    try{
      const r=await fetch('/ui/api/otp',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
        body:JSON.stringify({task_id:button.dataset.task,attempt_id:button.dataset.attempt,message})});
      if(!r.ok)throw new Error();notify('验证码已通过本地专用通道交给当前任务。');await state();
    }catch(e){notify('验证码未被当前尝试接受；请核对短信和任务状态。');await state()}
  }else{
    try{
      const r=await fetch('/ui/api/otp-resend',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
        body:JSON.stringify({task_id:button.dataset.task,command_id:'ui-resend-'+crypto.randomUUID(),expected_revision:Number(button.dataset.revision)})});
      if(!r.ok)throw new Error();notify('已授权当前任务重发一次；系统会重新核对控件与冷却状态。');await state();
    }catch(e){notify('无法安全重发；请检查冷却时间和当前任务。');await state()}
  }
  button.disabled=false;
});
newTaskForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=newTaskForm.querySelector('button');button.disabled=true;
  const data=Object.fromEntries(new FormData(newTaskForm).entries());
  try{
    const r=await fetch('/ui/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'same-origin',body:JSON.stringify(data)});
    if(!r.ok)throw new Error();
    const result=await r.json();showDiscovery(result,data);
    if(result.task_id){newTaskForm.reset();await state()}
  }catch(e){notify('暂时无法安全查找岗位；请核对公司、岗位和官方链接。')}
  finally{button.disabled=false}
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
    const r=await fetch('/ui/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(data)});
    if(!r.ok)throw new Error();
    const result=await r.json();showDiscovery(result,pendingDiscovery?.request||data);
    if(result.task_id){newTaskForm.reset();await state()}
  }catch(e){notify('候选已变化或暂时无法核验，请重新查找。');button.disabled=false}
});
tasksEl.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-answer]');if(!button)return;
  event.preventDefault();
  const input=button.previousElementSibling,value=input.value;
  const remember=button.nextElementSibling?.classList.contains('remember-fact')&&button.nextElementSibling.checked;
  if(!value.trim()){notify('请先填写答案。');return}
  button.disabled=true;
  try{
    const r=await fetch('/ui/api/user-input',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({task_id:button.dataset.task,field_key:button.dataset.key,
        value,expected_revision:Number(button.dataset.revision),remember})});
    if(!r.ok)throw new Error();
    const result=await r.json();
    input.value='';
    notify(remember?(result.task?.fact_reuse_status==='SAVED'?'答案已在本地保存为经确认的可复用事实。':'答案已交给当前任务；可复用保存待重试。'):'答案已在本地交给当前任务。');await state();
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
    const openReview=tasksEl.querySelector('[data-private-review-open]');
    if(openReview){
      const current=(data.tasks||[]).find(t=>t.task_id===openReview.dataset.privateReviewOpen);
      if(!current||current.stage!=='READY_TO_SUBMIT'||current.revision!==Number(openReview.dataset.revision)||!current.review_values_available)render(data);
    }else if(![...tasksEl.querySelectorAll('.factinput input:not([type=checkbox]),.factinput select,.otpinput input')].some(input=>input.value))render(data);
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
