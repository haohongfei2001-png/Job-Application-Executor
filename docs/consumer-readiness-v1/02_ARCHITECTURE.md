<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="02_architecture" class="chapter"><h1 id="02_architecture-拟冻结范围与架构决策">拟冻结范围与架构决策</h1>
<p>状态：设计交付，等待注册/执行授权。本文件定义 v1 目标，不声明相关模块已存在。现有代码事实见审计和证据索引。</p>
<h2 id="02_architecture-1-产品承诺与支持范围">1. 产品承诺与支持范围</h2>
<p>面向一名用户、单台主要 macOS 电脑、专用 Chrome、现有 canonical profile 与现有 DeepSeek 配置。允许多个持久任务排队，但同一受控浏览器 profile 只有一个自动写入者。产品允许断网、休眠、平台不支持和用户暂停；不能允许错误目标、虚假事实、自动最终提交或假 READY。</p>
<p>v1 必须认证至少 3 种实际招聘流程/平台机制，不能把同 ATS 上三个公司 logo 当作三种覆盖。优先将 OPPO、牛客招聘流列为候选；第三种由 JCR-03 公开只读调查选定并在支持矩阵冻结。最终使用用户真实愿意申请、尚未提交的岗位；已受保护岗位不做写入验收。若前两种平台因真实限制不适宜，变更需明确理由和同等机制覆盖，不默默换成容易网站。</p>
<p>面向这些认证范围提供：公司+角色、精确 URL、招聘首页/列表、站内搜索、候选消歧、已登录/SMS/人工安全验证、动态结构化表单、附件、资料复用、恢复、最终复核和人工提交后只读记录。未认证站点可只读探索/人工辅助，但必须明确“不在已验证范围”；不能依靠 universal generic 名称宣称所有网站都可靠。</p>
<h2 id="02_architecture-2-non-goals">2. Non-goals</h2>
<p>不自动点击最终提交或最终修改确认；不绕过 CAPTCHA/滑块/人脸/安全设备/密码；不让模型任意操作 shell/CDP/文件；不为缺资料编造事实、日期、经历或新承诺；不新增短信云中转；不做云端存储、团队版、多用户企业权限、微服务、通用 RPA 平台或无限并行浏览器集群；不以向量库替代结构化 canonical 事实；不迁移用户日常 Chrome 默认 profile；不因产品化去重写已有效的安全基础。</p>
<h2 id="02_architecture-3-invariants任何轮次不可削弱">3. Invariants（任何轮次不可削弱）</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID</th>
<th>不变量</th>
<th>验证方式</th>
</tr>
</thead>
<tbody>
<tr>
<td>I01</td>
<td>所有自动路径无最终提交能力；unknown-effect 控件拒绝自动操作</td>
<td>server submit counter、action journal、对抗 UI、真实手动边界</td>
</tr>
<tr>
<td>I02</td>
<td>操作前任务/岗位/tenant/页面归属得到证明</td>
<td>wrong-tab、redirect、同名岗位、stale observation fixtures</td>
</tr>
<tr>
<td>I03</td>
<td>个人事实只能来自用户/受控证据；模型不得产生事实值</td>
<td>fake provider 对抗提议、来源审查、actual draft oracle</td>
</tr>
<tr>
<td>I04</td>
<td>OTP/password/cookies/keys 不进入模型、普通持久层、日志和 export</td>
<td>canary information-flow、network denylist、数据类型边界</td>
</tr>
<tr>
<td>I05</td>
<td>成功必须由观察到的结果证明，而不是 plan 或按钮点击回执</td>
<td>独立 draft oracle、read-back、upload receipt</td>
</tr>
<tr>
<td>I06</td>
<td>READY 仅由有效 ReviewCertificate 推导</td>
<td>missing/unknown/conflict/stale certificate 否定测试</td>
</tr>
<tr>
<td>I07</td>
<td>暂停/取消后不再批准新的写入；已在途操作按结果协调</td>
<td>operation barrier 双侧注入；不得假称可撤销已发请求</td>
</tr>
<tr>
<td>I08</td>
<td>一个 profile 同一时刻最多一个自动 writer；人工接管时自动写入停止</td>
<td>concurrent command/worker/handoff tests</td>
</tr>
<tr>
<td>I09</td>
<td>已确认普通事实按授权 scope 持久，旧证据不能覆盖新确认</td>
<td>restart/update/rebuild/conflict/version tests</td>
</tr>
<tr>
<td>I10</td>
<td>更新不丢任务、事实、保护记录；失败保持/恢复可用版本</td>
<td>migration backup + process-level rollback tests</td>
</tr>
<tr>
<td>I11</td>
<td>受保护已提交目标默认只读；不能通过新 URL 别名重新写入</td>
<td>identity alias/idempotency tests</td>
</tr>
<tr>
<td>I12</td>
<td>原始真实个人 DOM/截图/trace/简历不进 GitHub</td>
<td>fixture manifest、canary、导出门禁</td>
</tr>
<tr>
<td>I13</td>
<td>健康、进度、失败与完成状态真实；无法证明即 unknown，不报绿</td>
<td>service/version/offline/ambiguous reply tests</td>
</tr>
<tr>
<td>I14</td>
<td>任何持久命令可查询是否受理/完成；网络重试不重复副作用</td>
<td>command idempotency、lost-response tests</td>
</tr>
</tbody>
</table></div>
<h2 id="02_architecture-4-adr局部单体不另起平台">4. ADR：局部单体，不另起平台</h2>
<p>保留 Python + Playwright + SQLite。新增子系统仍为同仓库可测试模块，除必要 bootstrap/updater/worker 隔离进程外不引入分布式基础设施。API/UI 只接受业务意图，不开放任意代码执行。拆分按责任，而非为了文件数或抽象层数。</p>
<p>建议模块表面（路径为提案，实施可等价组织）：</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>子系统</th>
<th>建议表面</th>
<th>责任</th>
</tr>
</thead>
<tbody>
<tr>
<td>Commands / Task domain</td>
<td><code>executor/commands/</code>, <code>executor/tasks/</code></td>
<td>typed commands、receipt、任务状态、authorization、priority controls</td>
</tr>
<tr>
<td>Discovery</td>
<td><code>executor/discovery/</code></td>
<td>只读页面分类、官方搜索、候选与 VerifiedJobTarget</td>
</tr>
<tr>
<td>Browser ownership</td>
<td><code>executor/browser_runtime/</code></td>
<td>profile/session/tab/popup ownership、epoch、人工接管、恢复</td>
</tr>
<tr>
<td>Auth</td>
<td><code>executor/auth/</code></td>
<td>active mode、attempt、发送/等待/重发政策、OTP transport</td>
</tr>
<tr>
<td>Facts</td>
<td><code>executor/facts/</code></td>
<td>evidence、versions、scope、conflict、私有回答持久化</td>
</tr>
<tr>
<td>Forms</td>
<td><code>executor/forms/</code></td>
<td>结构图、组件驱动、字段/行、观察/计划/读回</td>
</tr>
<tr>
<td>Review</td>
<td><code>executor/review/</code> 或等价拆分</td>
<td>独立证据、证书、差异、本地可读复核</td>
</tr>
<tr>
<td>Runtime health</td>
<td><code>executor/runtime/</code></td>
<td>service health、错误分类、重启协调、最小诊断</td>
</tr>
<tr>
<td>Release</td>
<td><code>executor/release/</code>, <code>app/</code></td>
<td>稳定 bootstrap、Mac shell、候选构建/激活/回退</td>
</tr>
<tr>
<td>Consumer UI</td>
<td><code>ui/</code></td>
<td>任务读模型、对话/决策/复核，不依赖内部工程枚举</td>
</tr>
</tbody>
</table></div>
<p>不得在新包和旧模块之间建立两套可写真相。兼容 wrapper 只委托同一权威实现；旧入口退役前必须有路由/契约测试。</p>
<h2 id="02_architecture-5-adr意图控制和回执">5. ADR：意图、控制和回执</h2>
<p>建议 CommandEnvelope：<code>command_id</code>、<code>kind</code>、<code>task_id/target_request_id</code>、<code>expected_task_revision</code>、<code>conversation_context_id</code>、<code>payload_ref</code>、<code>authorization_ref</code>、<code>issued_at</code>。这些是语义要求，不要求固定类名。</p>
<p>本地明确控制命令（pause/cancel/resume/status）不等待 DeepSeek；中文否定、冲突意图或不明确指代先澄清。UI 按钮/选中任务附带显式 task ID；“继续 OPPO”不能因另一个任务恰好是唯一 NEEDS_USER_ACTION 而操作另一个任务。没有选中上下文时必须从文本和候选证明目标，不能选列表最后一条。</p>
<p>远程模型调用不持有写入锁。流程为：读取一致快照→调用模型→取得 typed proposal→验证当前任务版本/当前授权→短事务接受或拒绝。暂停/取消应使迟到 proposal 失效。reply 必须由 command receipt 与实际动作结果生成，不在 action denied 后仍说“开始了”。</p>
<p>持久 task/state 与 chat 记录分开。消费端事实补充优先走本地 fact input；原始含敏感值聊天不默认持久或外发。保存脱敏 command history 足以恢复对话上下文；需要显示的私有事实从本地 vault 按权限读取。</p>
<h2 id="02_architecture-6-adr业务状态与等待原因">6. ADR：业务状态与等待原因</h2>
<p>不要继续用越来越多 <code>user_paused_from_...</code> 字符串承载状态机。Task 保存 <code>phase</code>、<code>run_state</code>、<code>wait_reason</code>、<code>paused_from</code>、<code>retry_budget</code>、<code>revision</code>、<code>last_observation_ref</code>、<code>review_ref</code>，通过合法转移表和事务更新。旧状态必须有明确迁移映射和历史 event 保留，不清空重试历史。</p>
<p>phase 示例：DISCOVERY → TARGET_VERIFIED → AUTH → FORM → REVIEW → READY；run_state：RUNNABLE/RUNNING/WAITING/PAUSED/FAILED/CANCELLED；wait_reason：USER_FACT/USER_DECISION/SECURITY/NETWORK/OTP/UNSUPPORTED/REPAIR。安全暂停不等于失败；用户等待不消耗网络重试预算。</p>
<p>ApplicationExecutor 负责一次观察—动作—验证循环，Worker 负责调度/租约，Controller 负责命令与合法转移；不得各自再维护一套互不一致的业务真相。</p>
<h2 id="02_architecture-7-adr目标发现与授权">7. ADR：目标发现与授权</h2>
<p><code>DiscoveryRequest</code> 可由 company+role/location/campaign 或 URL 形成，不立即授权任意网站写入。访问公开招聘页只读观察与搜索；忽略网页里的指令或诱导。结果包含候选、查询条件、来源、取证时间和覆盖边界。</p>
<p><code>VerifiedJobTarget</code> 至少包含 employer identity、ATS tenant、canonical job ID、campaign、title、location、official source chain、detail URL、allowed navigation scope、evidence digest。唯一性必须相对于完整查询条件证明；“找到一个”不是“只有一个”。</p>
<p>用户一句“投递 OPPO AI 产品经理”可授权系统找到唯一符合条件的岗位并准备申请；多地点/多批次等真实歧义必须问。不得为了避免提问擅自替换地点、岗位、用工类别或毕业批次。用户只说“看看”不得创建可写申请。</p>
<h2 id="02_architecture-8-adr浏览器归属与外部副作用">8. ADR：浏览器归属与外部副作用</h2>
<p>每个运行 session 绑定应用自有 profile、受控浏览器进程/实例标记、session epoch。每个 task 绑定 tab/popup lineage、target identity、draft identity；不能使用全局最后 tab。读取失败必须是 observation error，不能当成“表单没有字段”。</p>
<p>每次写入前验证 guard：worker lease + task revision + session epoch + document identity + field/action scope + authorization。DOM 重绘/导航后旧 observation 失效；locator 用 role/label/section/record 与稳定属性组合，不能把 global nth 作为主要身份。</p>
<p>Action receipt 区分 <code>NOT_ATTEMPTED</code>、<code>ATTEMPTED</code>、<code>OBSERVED_SUCCESS</code>、<code>OBSERVED_FAILURE</code>、<code>UNKNOWN_OUTCOME</code>。外站没有幂等 API 时不承诺网络意义上的 exactly-once；以持久意图、动作前后证据和恢复协调避免重复副作用。UNKNOWN 时先只读重新检查，不能盲目重复短信发送/新增经历/上传/保存。</p>
<p>人类操作网站时获取 human handoff ownership，自动写入暂停。任务可在人工接管期间被取消，但不能偷偷继续填写。用户完成挑战后系统只读重新验证；如果用户修改了已审核字段，证书失效重新复核。</p>
<h2 id="02_architecture-9-adr认证与-otp">9. ADR：认证与 OTP</h2>
<p>AuthAttempt 的非秘密元数据可持久：task、origin、active mode、phone fact reference、attempt ID、request timestamp、request outcome、deadline、cooldown、return-target ref。OTP 值及可用于猜解的低熵摘要不落盘；只能绑定到有效 attempt 的内存条目。</p>
<p>Transport 统一接口：本地 authenticated push；获得权限后的 Mac Messages 只读 source；用户已存在并明确启用的 relay。Mac Messages 不应必须先成功访问 relay 才有资格工作。严禁隐式新建云服务或发送整份短信库；source 只读最小匹配窗口。</p>
<p>收到 code 后必须重新证明 active auth context、unique OTP 控件与 target/attempt；分段验证码控件仅在明确认证的 driver 支持。CAPTCHA/password/QR/face/hardware 都不由系统绕过。密码输入由用户在真实站点完成。</p>
<p>发送 SMS 属外部可见副作用，不等于普通读取。默认允许在本任务明确申请授权和有效登录政策下发送一次初始验证码；重发需要额外用户明确授权并遵守 cooldown。未知发送结果不得自动再发送。暂停/取消/reboot/update 使不再有效的 code 清除，任务和普通事实保留。</p>
<h2 id="02_architecture-10-adr个人事实持久化与最少提问">10. ADR：个人事实持久化与最少提问</h2>
<p>保留 canonical profile 和 evidence provenance。新增私有 fact journal / answer store，0700/0600、原子写入/事务、版本与校验，不与 public audit、UI chat export 共用同一个序列化器。凭证使用 Keychain；不要求用户为了所有普通事实另购加密服务。</p>
<p>FactRecord：stable key/record ID、value、source/evidence ref、confirmed_at、scope、validity、precision、supersedes/conflict。一次性回答可在该申请的私有存储持久化以支持重启，但不可跨申请复用；可复用事实必须是明确确认的持久 scope。OTP 永远不属于此 store。</p>
<p>已有 source/import 格式兼容；资料重建先合并 explicit overrides，不静默覆盖。冲突不是随机取最大 confidence；可解释权重与实质冲突分别处理。当前任务绑定 profile version，若事实变化需要重算受影响字段及 review。</p>
<p>结构化项目和研究使用 stable IDs；名称变化是表示变化，不是自动生成/删除一个项目。每条申请记录 included / excluded(reason,scope,owner-policy) / not-applicable(evidence) / unresolved。未判断不算排除。</p>
<h2 id="02_architecture-11-adr结构化表单与能力驱动">11. ADR：结构化表单与能力驱动</h2>
<p><code>FormObservation</code> 包含 page/document epoch、sections、record rows、fields、requiredness evidence、visibility/enabled、dependencies、validation messages、save status、attachment receipts、observed identity。<code>FillPlan</code> 只描述意图，不能充当 observation。</p>
<p>通用 driver 处理常见 native 与标准 ARIA component；site driver 处理受支持平台特例，明确能力清单。不支持的组件不落到通用“请告诉我资料”；系统诊断应说明是控件识别失败，并保留已经完成的工作。</p>
<p>先观察→解析事实与表示→最小写入→等待可验证 postcondition→重新观察。受控组件使用真实输入事件，读回框架重渲染后的状态。repeat sections 使用 row binding/idempotent reconcile；不因恢复多添一条项目。salary/date/location 是有类型的域，不用字符串包含做真实性判据。</p>
<p>校验需要跨页覆盖和 server/网站 validation。网站没有可读 draft API 时使用重新进入/刷新后的可见表单证据，但应明确证据等级；附件存在 input.value 只能是初步信号。无法证明上传保存即不能认证 READY。</p>
<h2 id="02_architecture-12-adr独立最终证书">12. ADR：独立最终证书</h2>
<p><code>ReviewCertificate</code> 由当前实际 FormObservation/draft read-back、facts、target、driver contract生成，不由 executor 计划直接生成。</p>
<p>绑定：task ID、task revision、target identity digest、profile version、draft/form revision、session epoch、adapter contract version、observed_at、evidence refs。输出各检查项 PASS / FAIL / NOT_APPLICABLE_WITH_EVIDENCE / UNKNOWN；只有所有 required checks 为 PASS 或有证明的 NOT_APPLICABLE 才允许 READY。</p>
<p>必须覆盖：目标/公司/地点/批次/账号；所有必需字段与区段；教育、工作、实习、项目、科研及排除理由；解析结果误分类；日期精度；薪酬表示；附件实际版本/上传完成；隐私/声明授权；未解决事实/冲突/未经证实默认值；网站错误和保存结果；最终动作边界。</p>
<p>证书不是永久票据。表单编辑、profile 变化、身份变化、更新涉及相关合同、session 过期或复核证据丢失时失效。任务 READY 可以触发只读 <code>REVALIDATE_REVIEW</code>，不能因此偷偷跨过原先的 manual boundary 或变成无约束 resume。</p>
<p>本地复核界面向本人显示必要的真实值和差异；copy-safe diagnostics 不含这些值。不得为了不泄露日志而让本人只能看“***、共填 30 项”的空泛复核。</p>
<p>用户点击真实站点最终按钮，系统没有提交代理命令。后续 observer 只读读取成功页/申请历史；服务器证据可标 verified，人确认可标 user-confirmed，page-only signal 不冒充 server verified。相同身份的已提交目标进入保护记录，准备新投递不会重复创建。</p>
<h2 id="02_architecture-13-adr自诊断与产品壳">13. ADR：自诊断与产品壳</h2>
<p>稳定 bootstrap 与可更新业务运行时分离；业务导入失败也能打开 app、看到健康/版本/任务摘要、复制安全诊断或恢复旧版本。服务不足不应阻止设置/诊断页面显示。</p>
<p>诊断字段：schema/version、loaded runtime SHA/build digest、checkout/candidate version（分别）、OS/browser compatibility、service heartbeat、task phase/wait reason、last observation age、safe error kind/subsystem/cause chain、相关 command/event IDs 与时间、最近恢复动作/结果。未知值记 unknown，禁止 git 命令失败时把 clean 判为 true。</p>
<p>初期使用 SwiftUI + WKWebView 或功能等价的最小原生 shell 承载本地 UI，继续 Python/Chrome执行；shell 不做招聘站内核，也不拿到任意 shell 执行权限。若编译/本机信任需要人工首次确认，将其放入最后 onboarding 时段。登录项仅明确 opt-in。个人 v1 不以公众分发 App Store/商业签名为前提；若以后公众分发则另列发布工作。</p>
<h2 id="02_architecture-14-adr事务式本地更新与回退">14. ADR：事务式本地更新与回退</h2>
<p>代码事实源仍是 remote main，但正常用户安装源应是<strong>绑定已通过认证 SHA 的候选 release manifest</strong>，不是每次自动把未经消费级认证的 main 直接 pull 进运行目录。</p>
<p>流程：检查来源/摘要→下载/准备隔离版本与锁定依赖→候选 startup self-test + schema compatibility→任务写入 quiesce 和权限检查→一致备份/迁移→原子切换 active version→健康/加载版本/私有数据可读验证→确认成功。候选失败不激活；激活后健康失败由独立 bootstrap 回退。</p>
<p>保留现有 update lock、原子状态文件、OTP/in-flight 阻止更新与 mutation fence。升级失败不能覆盖唯一旧版本。数据库迁移优先向后兼容 expand/contract；破坏性迁移需另外可验证的 snapshot restore。发生新的用户写入后，禁止简单恢复旧快照把新事实丢掉；要么阻止这种回退，要么进行经过验证的兼容转换。</p>
<p>不强制引入远程签名服务。最低要求是可信仓库授权、固定 commit/build manifest、内容校验和门槛证据；分支保护若可用应开启与现有计划兼容的 required checks，无权限时作为明确治理限制记录，不能伪称设置成功。</p>
<h2 id="02_architecture-15-迁移策略">15. 迁移策略</h2>
<p>逐轮添加表/字段和兼容层，迁移旧 task IDs、URLs、protected identity、用户确认事实、暂停原因与 retry history。旧真实 Chrome profile 保留而非重建；默认禁止 fixture 访问它。历史 application records 保留 read-only。</p>
<p>把旧行为断言分为安全不变量（必须保留）与被本包明确替代的产品合同。例如“缺 key 不打开 UI”和“普通回答永不持久化”可经正式变更替换；OTP 不落盘、无自动 submit、保护已提交对象不能随之删除。每轮退出前证明新旧路径不同时可写，并完成 retirement checklist。</p>
</section>
