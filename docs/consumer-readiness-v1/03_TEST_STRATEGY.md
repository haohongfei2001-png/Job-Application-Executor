<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="03_test_strategy" class="chapter"><h1 id="03_test_strategy-测试与验收体系让开发者发现问题而不是让用户逐轮找-bug">测试与验收体系：让开发者发现问题，而不是让用户逐轮找 bug</h1>
<h2 id="03_test_strategy-1-当前测试的真实价值与边界">1. 当前测试的真实价值与边界</h2>
<p>现有 CI 使用隔离 Chromium，包含 native DOM、真实 supervisor subprocess、HTTP OTP 和最终提交请求计数，这些是真实价值。队列原子 claim、过期所有者 fencing、pause/retry、update locks、Origin/token、OTP canary 等也不是空测试。[E19–E23]</p>
<p>但当前 subprocess E2E 在已经到 READY 后才重启；consumer launch 通过替换真实依赖测试调用顺序；update success 主要替换 git/subprocess；review 测试允许 READY plan 搭配 REVIEW_REQUIRED。它们分别证明局部合同，而非用户需要的跨组件结果。[E04,E19–E21]</p>
<p>核心改变：**被测程序不能自己给自己发合格证。**任何“任务完成”必须有独立 fixture server/draft observer 的真实结果，不只看内部 stage 或生成的 plan。</p>
<h2 id="03_test_strategy-2-测试环境与网络边界">2. 测试环境与网络边界</h2>
<p>T0：纯函数/状态模型，fake clock/seed，无网络。T1：真实浏览器 + 本地 fixture server，合成人名/简历/OTP，无真人资料。T2：真实服务进程 + UI + 浏览器 + 存储 + 更新 bootstrap，仍全合成。T3：公开网站只读 contract smoke，不登录、不发码、不上传、不投递。T4：用户授权的真实账号/岗位最终验收。</p>
<p>默认测试不允许访问 live profile、9333、Keychain、Messages 数据库、私人 config 或外部模型；进程层/网络层双重拒绝，而不只靠环境变量。T3 使用独立 profile 和经批准的只读操作清单；T4 必须明确任务和允许副作用，不拿已经提交岗位练习。</p>
<p>合成浏览器 trace/HAR/video 可留 CI；真实站点资料默认仅本地临时，必须按 capture policy 脱敏后才能入库。不得把 Playwright trace 的“方便看网络”误当作可以上传所有真实请求。</p>
<h2 id="03_test_strategy-3-十六层覆盖">3. 十六层覆盖</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>层</th>
<th>自动化内容与独立判据</th>
<th>当前缺口/加强方向</th>
<th>真人参与</th>
</tr>
</thead>
<tbody>
<tr>
<td>1 deterministic unit</td>
<td>URL identity、facts precedence、scope、类型、日期/薪资、canonical normalization、按钮策略</td>
<td>把包含匹配/布尔转换等反例直接 import 实现测试</td>
<td>无</td>
</tr>
<tr>
<td>2 state-machine</td>
<td>generated legal/illegal command sequences，pause/cancel/stale/retry/restart/interleave</td>
<td>从枚举合法到转移、版本、等待来源和不变量；模型与实现独立</td>
<td>无</td>
</tr>
<tr>
<td>3 browser DOM</td>
<td>小型标准 native/ARIA 控件正反例，scoped locator、重绘</td>
<td>保留现有真实 Chromium；添加隐藏/多个按钮/错误label/observer failure</td>
<td>无</td>
</tr>
<tr>
<td>4 realistic saved-page</td>
<td>结构保真的脱敏真实页 + 本地 API replacement + 期望结果</td>
<td>不把小 HTML 视为真实 ATS；fixtures versioned、来源/时间/限制清楚</td>
<td>公开页无需；私有页只在最终时段授权采集</td>
</tr>
<tr>
<td>5 ATS/site contract</td>
<td>landing/list/detail/auth/form/save/read-review 的 identity/effect contract</td>
<td>每个平台独立 contract version 和 drift detector，不是到处公司正则</td>
<td>公开只读无；登录态最后</td>
</tr>
<tr>
<td>6 synthetic E2E</td>
<td>实际 UI 输入自然语言→command→daemon→browser→fakeATS→local review</td>
<td>server 记录真正 draft、attachments、requests；结果与 canonical golden 对比</td>
<td>无</td>
</tr>
<tr>
<td>7 replay</td>
<td>脱敏 observation/event/receipt 回放、重复/乱序/丢响应</td>
<td>重放数据/决策不是重放外部点击；验证旧版本兼容</td>
<td>无</td>
</tr>
<tr>
<td>8 crash/recovery</td>
<td>在操作前/发出后/回执前杀 worker/browser/service、丢 tab、重启，验证实际 draft</td>
<td>不止 lease fake clock；独立进程和持久 fixture server；完整任务恢复</td>
<td>无；真 Mac sleep/reboot 最后少量</td>
</tr>
<tr>
<td>9 auth/OTP</td>
<td>active-mode、验证码迟到/早到/多条/过期、SMS outcome unknown、resume/consent</td>
<td>stateful fake SMS/auth server；禁止错误 attempt 和重复发送</td>
<td>无；设备权限/真 SMS 最后</td>
</tr>
<tr>
<td>10 privacy leak</td>
<td>每种秘密/个人数据类型 canary，检查模型请求、queue、日志、HAR、export、Git artifacts</td>
<td>不是仅搜索“token”字段；新增类型后所有出口自动扫描</td>
<td>无</td>
</tr>
<tr>
<td>11 update/restart</td>
<td>构建两个真实 candidate 环境、依赖变化、migration、断网/kill/rollback</td>
<td>不替换所有 subprocess；真实 loaded SHA 自检与旧版本存活</td>
<td>自动 Mac；首次本机安装最后</td>
</tr>
<tr>
<td>12 multi-task</td>
<td>A/B task、多个 UI、late model、pause during claim、human handoff、update race</td>
<td>one writer 不等于不会跨 task；错误最后标签必须 fail</td>
<td>无</td>
</tr>
<tr>
<td>13 accessibility/DOM</td>
<td>labels/role/locales/order/viewport/zoom/open shadow/iframe、dynamic replacement</td>
<td>行/组件 identity 不依赖固定全局 nth；unsupported 明确</td>
<td>无</td>
</tr>
<tr>
<td>14 adversarial UI</td>
<td>“Continue”实际提交、仿冒最终按钮、同名/同URL跨租户、页面提示注入、错误前缀</td>
<td>危险动作必须拒绝或由已认证 driver证明；不能只 expand regex</td>
<td>无</td>
</tr>
<tr>
<td>15 production smoke</td>
<td>低频公开页面/官方列表/身份/只读搜索/contract fingerprint</td>
<td>监测 drift、网络和解析；不能证明已登录申请端到端成功</td>
<td>无；外部查询额度必须既有授权</td>
</tr>
<tr>
<td>16 real browser acceptance</td>
<td>真实 macOS app + Chrome + profile + SMS + 实际准备到 READY + user-click boundary</td>
<td>不用 mock 替代最后一公里；全部真实差异转为私有/脱敏回归证据</td>
<td>最后 1–3 个集中时段</td>
</tr>
</tbody>
</table></div>
<h2 id="03_test_strategy-4-独立-oracle判据">4. 独立 oracle（判据）</h2>
<p>SyntheticATS 维护自己的服务器 draft：job/tenant/account identity；每个 education/work/project record；实际存储值；attachment IDs/hash；validation errors；draft revision；SMS request ledger；submission ledger。测试驱动用户界面，最后直接查询 fixture server 的观测状态，与独立 golden applicant/selection policy 比较。</p>
<p>核心断言不是 <code>task.stage == READY</code>，而是 <code>actual_draft == expected_true_draft</code>，所有必要区段经过覆盖审查、不存在额外工作经历/隐含承诺/无来源默认值，且 automation submission count 为 0。随后检查 READY certificate 与实际结果一致。</p>
<p>真实平台不能假设能读服务器 API。由已批准 driver 从页面/可用申请历史/草稿响应取证；标注证据等级，无法读取不能编造。最终 owner 实际看见与证书相同的申请；用户只是确认意愿，不担任基础字段校验员。</p>
<p>模型调用使用三类替身：正确 typed proposal、超时/错 JSON/unknown key、恶意或陈旧 proposal。另建冻结意图语料含否定、前后任务、歧义、跨句表达；批量真实 provider contract 只用合成任务和无个人资料，受既有额度约束，不把 provider key 放进 PR fixtures。</p>
<h2 id="03_test_strategy-5-必须先加入的十个黄金回归">5. 必须先加入的十个黄金回归</h2>
<p>G01：无 URL 的“投递公司+岗位”→公开搜索→唯一精确身份→完整准备。G02：首页/列表→筛选/分页后确认唯一，不擅自选同名第一条。G03：先打开 A 任务，后开 B 无关 tab，A 后续写入只落 A。G04：期望北京、select 被重绘成上海，必须拒绝 READY。</p>
<p>G05：两个标题包含但不同 ID 的项目，不能互相覆盖。G06：简历解析把项目误当工作，系统修复或阻塞，不静默提交。G07：等待 DeepSeek 超时时本地暂停立即生效，迟到提议不能写入。G08：填表中 kill worker/browser，重启后不再问已确认事实、不新增重复经历/附件。</p>
<p>G09：发送 SMS 后未得到回执即 crash，恢复不能自动再发；late OTP 只能属于原 attempt。G10：升级候选带坏依赖或 migration 失败，旧 app 仍能打开并恢复原任务，copy-safe report 没有秘密。</p>
<p>另外保留完整历史 no-submit/auth/privacy/protected-target 测试，不用这十条替换原有 240 项。</p>
<h2 id="03_test_strategy-6-故障注入协议">6. 故障注入协议</h2>
<p>对每个重要外部动作定义注入点：guard 前、intent 已落盘后、请求已发送但无响应、观察成功但 receipt 未落盘、receipt 已落盘但 UI 未收、人工接管中。注入 process exit/timeout/network drop/tab close/DOM replacement。每例读取动作台账和 fixture server 的真实结果。</p>
<p>不要求外站提供 exactly-once；要求系统不会把 UNKNOWN_OUTCOME 当作“肯定没发生”，也不会盲目重放。恢复允许安全等待，但仅安全等待不算该场景自动完成通过，必须分别统计 safety PASS 与 task-completion PASS。</p>
<p>Mac sleep/reboot 的大部分状态逻辑由 fake clock+session epoch+process restart 自动覆盖；在 hosted macOS 用自有进程测试安装、浏览器、Keychain stub、停止/唤醒事件适配。真实硬件休眠/重启和实际权限的少量证据放在最后，不让用户每轮重启电脑。</p>
<h2 id="03_test_strategy-7-capturedsanitized-fixtures">7. Captured/sanitized fixtures</h2>
<p>每个 fixture 带 manifest：origin class、capture date、contract version、捕获方法、私有/公开级别、变换记录、有效字段/交互、遗漏限制、golden expected outcome、synthetic replacement values、network allowlist。保留结构，不保留用户身份/完整 URL secrets/cookie/localStorage/真实附件。</p>
<p>转换规则：移除秘密/账号/真实申请号、phone/email/address、隐藏输入中的 token、data 属性、script 内嵌 JSON、截图中的文字和二维码；将所有 endpoint 改为本地模拟服务；禁止残留脚本访问真实域。自动扫描后人工或独立工具复核脱敏结果。对无法可靠脱敏的资料，保留私有本地 fixture 或重新构造同结构合成案例，不上传 Git。</p>
<p>捕获静态 DOM 不能模拟完整 React 生命周期，因此必须补 controlled-state implementation 和 fake API。不要把保存的截图或 HTML 可打开当作该站点合同已认证。</p>
<h2 id="03_test_strategy-8-ci-分层预算与失败证据">8. CI 分层、预算与失败证据</h2>
<p><strong>2026-09-24 cadence amendment（仅适用于尚未完成的轮次）：</strong>开发期不再在每个 draft push 上重跑完整 400+ 测试。Inner loop 由执行器运行受影响的 deterministic/state/browser/oracle 回归；GitHub draft CI 只保留 task/privacy/no-submit foundation、编译与 diff 基础门。一个 JCR 轮次的实现稳定、准备合并时才将 PR 标记 Ready，并在 exact head 跑一次完整 suite；合并到 main 后保留集成证据。</p>
<p>JCR-06～08 的广覆盖矩阵应批量到轮次收口：不因每新增一个控件/driver 就重跑所有历史 fixture。错误目标、自动最终提交、秘密外泄、事实伪造、未验证外部写重放、错误账号/attempt 等安全硬门仍需在受影响改动发生时立即用 targeted regression 验证，不能后移。</p>
<p>JCR-09 final convergence 才集中执行 100 条完整黄金任务、1,000 状态序列、关键 fault 重复、24h soak、完整公开 drift、macOS update/recovery 与最终 release 候选矩阵。真实账号/短信/设备仍按 07 的集中验收处理。已完成 JCR-01～05 的证据不因本 amendment 重跑。</p>
<p>所有失败保留：exact SHA、环境/seed、用例 ID、预期 vs 实际、最后成功动作、safe error、合成 trace、最小复现。不能只给“18 failed，请 owner 看看”。定位旧 locator 失效与真实行为回归再修；禁止整批 skip、把 expected 改成当前错误值。</p>
<p>测试本身必须有 fault canary：故意换错城市/项目/目标/上传失败应让 oracle 报错。若 broken implementation 仍全绿，判据无效，不能认证。</p>
<h2 id="03_test_strategy-9-拟定自动门槛不是当前实测指标">9. 拟定自动门槛（不是当前实测指标）</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>项目</th>
<th>最低门槛</th>
</tr>
</thead>
<tbody>
<tr>
<td>安全硬门槛</td>
<td>错误目标写入、自动最终提交、秘密越界、伪造事实、虚假 READY：0；任何一次都阻断发布</td>
</tr>
<tr>
<td>黄金完整流程</td>
<td>冻结 100 个具备真实 fixture server 的任务，包含全部主要分支，100/100 结果正确；任务计数不是同一空白表重复 100 次</td>
</tr>
<tr>
<td>状态与命令</td>
<td>至少 1,000 个有记录 seed 的操作序列，含非法/并发/过期；所有不变量成立</td>
</tr>
<tr>
<td>核心恢复/并发</td>
<td>每个关键注入用例连续 10 次；macOS 平台关键链路至少 3 次；一次失败保留，不选择最好一次</td>
</tr>
<tr>
<td>重复提问</td>
<td>当前版本/适用 scope 内已确认且无冲突的事实，重复询问 0；新冲突或语义不能确定必须另分类</td>
</tr>
<tr>
<td>owner 开发期 QA</td>
<td>JCR-01～08 不要求真人申请/验证码/字段核对；基础故障自行用 fixture 复现</td>
</tr>
<tr>
<td>UI 响应</td>
<td>本地受理/暂停/取消回执目标 ≤1s，不等待模型；已在途动作另标执行状态</td>
</tr>
<tr>
<td>启动性能</td>
<td>目标参考 Mac、依赖齐全下 warm UI p95≤3s，cold UI≤15s；UI 先显示，外部服务不可用不阻止显示</td>
</tr>
<tr>
<td>自动恢复</td>
<td>在 OS 已唤醒且网络可用时，浏览器恢复目标≤60s、daemon恢复/租约协调≤90s；未知外部副作用允许明确等待，不造成功</td>
</tr>
<tr>
<td>持续性</td>
<td>自动合成 soak≥24h，含延迟、重连、多个任务和升级；无死循环/泄漏增长/任务孤儿</td>
</tr>
</tbody>
</table></div>
<p>如果实际参考硬件基准证明性能阈值不合理，应在认证前通过有数据的变更记录调整，不让工程问题演化成 owner 反复测试。安全与正确性零容忍门槛不得通过调阈值放宽。</p>
<h2 id="03_test_strategy-10-让-owner-只做必要参与">10. 让 owner 只做必要参与</h2>
<p>owner 负责现实中的选择、授权和最终提交，不负责告诉开发者某个按钮失效。最终三段验收安排见 07：首次本机 setup/auth；多平台完整任务；必要的升级/中断或一次复验。若发现基础问题，开发者先捕获最小安全证据、修复、自动回归通过，再使用剩余集中时段，不能要求用户无限“再试一次”。</p>
</section>
