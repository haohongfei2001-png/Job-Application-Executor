<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="01_audit" class="chapter"><h1 id="01_audit-完整产品与端到端可靠性审计">完整产品与端到端可靠性审计</h1>
<p>基线：<code>main@72dd892c144699f8a9e9988265bfb20f50b3db4e</code>。证据编号见 <a href="#08_evidence_index">08_EVIDENCE_INDEX.md</a>。凡“应当”“建议”“目标”“PASS”均为拟定产品要求，不是当前实现状态。</p>
<h2 id="01_audit-1-结论与成熟度">1. 结论与成熟度</h2>
<p>当前系统是<strong>有严格边界和真实执行能力的工程 Alpha</strong>：已不是只有 mock 的概念原型，但也不是稍加视觉包装即可每天放心使用的产品。它比较擅长“在已知精确 URL、可识别的简单表单和预先配置的本地环境下执行保守动作，遇到异常停止”，尚未形成“从用户意图到完整申请结果的自主闭环”。</p>
<p>本次核验的远端 CI 为 240 passed / 56.33s。它证明当前测试集合通过，不证明多个真实招聘平台能完成完整 consumer journey。本次没有测量真实成功率，因此不提供“已完成 80%”“还需三天”等无依据数字。[E00,E19–E24]</p>
<p>距离 v1 的主要工作不是再补十几个公司名正则，而是补齐四个横向闭环：<strong>目标身份、执行与恢复、事实与完整性、产品运行与发布</strong>。可保留技术栈和安全内核，用 9 轮有明确成果的产品化工程完成，不需要另起云平台。</p>
<h3 id="01_audit-11-十个系统性缺口">1.1 十个系统性缺口</h3>
<div class="table-wrap"><table>
<thead>
<tr>
<th>优先级</th>
<th>系统性缺口</th>
<th>用户后果</th>
<th>关键证据</th>
</tr>
</thead>
<tbody>
<tr>
<td>发布阻断</td>
<td>READY 未由完整、独立的最终复核决定</td>
<td>以为只差点击，实际还存在遗漏/未经证实的数据</td>
<td>E03–E05</td>
</tr>
<tr>
<td>发布阻断</td>
<td>标签页、会话、岗位身份没有贯穿绑定</td>
<td>可能定位到无关页；恢复后不能证明仍是原申请</td>
<td>E06,E07,E17</td>
</tr>
<tr>
<td>发布阻断</td>
<td>关键按钮依赖文字而非认证动作语义</td>
<td>禁止 submit 方法仍不能证明 next/apply/Enter 无最终副作用</td>
<td>E05,E18</td>
</tr>
<tr>
<td>核心体验阻断</td>
<td>消费端 CREATE 强制用户当前消息给 exact URL</td>
<td>“投递 OPPO AI 产品经理”不能按理想体验工作</td>
<td>E02,E17</td>
</tr>
<tr>
<td>核心体验阻断</td>
<td>通用表单主要是原生控件列表，不是结构化表单模型</td>
<td>重复经历、依赖字段、异步上传和自定义控件不可靠</td>
<td>E05,E15,E16</td>
</tr>
<tr>
<td>核心体验阻断</td>
<td>消费端已确认事实仍是内存回答</td>
<td>重启/更新后再问；CLI 的复用能力没有成为产品能力</td>
<td>E08,E16</td>
</tr>
<tr>
<td>核心体验阻断</td>
<td>认证没有完整 attempt 与 transport 生命周期</td>
<td>短时等待、迟到短信、重发、返回原岗位仍需人工串联</td>
<td>E14,E05</td>
</tr>
<tr>
<td>核心体验阻断</td>
<td>命令、任务、健康和 UI 状态混杂</td>
<td>“继续这个”上下文弱，绿色就绪不是实际可执行，暂停可能被模型等待阻塞</td>
<td>E02,E09,E10</td>
</tr>
<tr>
<td>运维阻断</td>
<td>launcher / diagnostics / updater 未形成独立恢复面</td>
<td>服务坏了打不开诊断，更新后的坏依赖不能自动回到可用版本</td>
<td>E11–E13</td>
</tr>
<tr>
<td>认证阻断</td>
<td>测试的判据与用户结果之间存在断层</td>
<td>基础问题持续由真人应用暴露</td>
<td>E19–E24</td>
</tr>
</tbody>
</table></div>
<p>“发布阻断”是必须修复/证伪的门槛，不表示本次观察到真实错误提交或隐私泄漏。对于能构造安全反例的路径，即使还未发生真实事故，也不能认证可用。</p>
<h2 id="01_audit-2-真实产品模型与职责">2. 真实产品模型与职责</h2>
<h3 id="01_audit-21-当前模型">2.1 当前模型</h3>
<p>当前实际上有三层并存：原始/迁移后的 CLI 申请执行器、Local Autonomy 队列守护进程、后来增加的对话 Manager 与 launcher。新的界面包住了旧执行链，但目标发现、资料确认、浏览器会话、复核、更新仍分别存在于不同入口里。[E01,E02,E07–E13,E16]</p>
<p>DeepSeek 不是全程自由控制电脑的 agent。它负责生成有限的任务控制提议，另一个 mapper 解释字段对应哪个 canonical key。决定能否执行、取哪个真实值、调用哪个浏览器动作的是本地确定性逻辑。这种分工应保留，而不是为了显得智能让模型直接执行任意 JavaScript 或臆造资料。[E02,E15]</p>
<h3 id="01_audit-22-从意图到提交前的当前-journey">2.2 从意图到提交前的当前 journey</h3>
<div class="table-wrap"><table>
<thead>
<tr>
<th>步骤</th>
<th>当前主责</th>
<th>实际发生的行为</th>
<th>断点</th>
</tr>
</thead>
<tbody>
<tr>
<td>打开入口</td>
<td>launcher + local runtime</td>
<td>启动 Chrome、服务、预检，然后普通浏览器打开 UI</td>
<td>配置缺失时 UI 也不开；原服务中途死亡没有独立产品恢复面</td>
</tr>
<tr>
<td>理解“投递/继续/暂停”</td>
<td>DeepSeek + ManagerController</td>
<td>typed decisions，经当前消息正则和任务条件批准</td>
<td>部分清晰命令仍依赖模型；缺乏持久选中任务和版本绑定</td>
</tr>
<tr>
<td>找岗位</td>
<td>Manager + target_resolver</td>
<td>创建通常要求 URL，OPPO 首页有特例补救</td>
<td>没有独立的发现→候选→证据→任务流程</td>
</tr>
<tr>
<td>持久化任务</td>
<td>TaskQueue</td>
<td>URL/标识去重、SQLite、租约、保护目标检查</td>
<td>durable task 不等于 durable browser draft 或 action result</td>
</tr>
<tr>
<td>获取浏览器</td>
<td>Worker + browser.py</td>
<td>活跃 CDP、exact URL 页、last page</td>
<td>无严格任务标签归属/重定向证明；死浏览器让用户恢复</td>
</tr>
<tr>
<td>认证</td>
<td>ApplicationExecutor + GenericWebAdapter + OTP bridge</td>
<td>检测验证码/密码/人机验证；保守发送一次初始 SMS；读取 broker</td>
<td>auth mode、attempt、迟到短信和恢复路径不完整</td>
</tr>
<tr>
<td>解析/填写</td>
<td>adapter + FieldResolver + canonical profile</td>
<td>原生字段快照、规则/语义映射、确定性真实值、原生 fill/select</td>
<td>重复行与复杂组件不成为一等结构；映射失败容易变成问事实</td>
</tr>
<tr>
<td>保存/验证</td>
<td>adapter + ApplicationExecutor</td>
<td>读回当前可见控件，部分核对；遇到 blocker 保存草稿</td>
<td>可见范围不等于完整表单；异步持久化、隐藏/遗漏区段不足</td>
</tr>
<tr>
<td>最终复核</td>
<td>review helper + 用户</td>
<td>生成 checklist/项目覆盖，并已置 READY</td>
<td>checklist 不是已完成检查；UI 没提供完整复核和差异</td>
</tr>
<tr>
<td>用户最终点击以后</td>
<td>用户；旧接口具备部分验证逻辑</td>
<td>文档允许只读验证；consumer 队列 READY 不自动续跑</td>
<td>“已投递”证据和 protected registry 的消费级闭环不完整</td>
</tr>
</tbody>
</table></div>
<h3 id="01_audit-23-责任混乱的根源">2.3 责任混乱的根源</h3>
<p>Manager 既解释自然语言，又掺入 OPPO resolver/retarget 细节；ApplicationExecutor、adapter 各自承担认证判定；TaskQueue 的业务状态、wait reason、暂停前来源混在 blocker 字符串中；UI 使用安全标记推导服务就绪；review 同时承担“需要用户检查的说明”和“足够可提交的证明”。这些不是命名问题，而是导致跨步骤不一致的实际边界问题。[E02,E03,E05,E07,E09,E10]</p>
<p>新架构的核心要求是：<strong>意图可以来自模型，事实来自证据，状态来自唯一控制器，成功来自观察到的网页/草稿结果，展示来自同一份任务读模型。</strong></p>
<h2 id="01_audit-3-a--启动安装与运行准备">3. A — 启动、安装与运行准备</h2>
<p>首次安装不是真正自包含：<code>.app</code> 引用仓库绝对路径和已有 <code>.venv</code>，缺环境时提示重新安装。没有完整资料导入、凭证/短信来源配置、权限解释和预览流程。日常双击已能启动可逆依赖，值得保留；但服务缺配置时连 UI 都不开，使用者无法在产品里修复自己。[E11]</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>场景</th>
<th>当前判断</th>
<th>消费级要求</th>
</tr>
</thead>
<tbody>
<tr>
<td>第一次安装</td>
<td>依赖开发目录/虚拟环境；非独立安装体验</td>
<td>安装包包含或自动配置受支持运行环境；无 Terminal onboarding；资料和凭证只在本地</td>
</tr>
<tr>
<td>日常打开</td>
<td>可重用/启动 Chrome 和服务，但打开普通浏览器新页</td>
<td>单一应用窗口、恢复上次任务；不增加重复 manager 窗口</td>
</tr>
<tr>
<td>Mac 重启</td>
<td>文档明确服务到登出/重启为止；再打开 app 可重新启动</td>
<td>重启后打开 app 即恢复；可选登录启动需用户同意；不声称休眠/关机时工作</td>
</tr>
<tr>
<td>Chrome 未开/崩溃</td>
<td>launcher 可启动；Worker 只接受现有 session，失败则 block</td>
<td>应用管理自己的浏览器，安全重建、重新观察原任务，不接管普通 Chrome</td>
</tr>
<tr>
<td>daemon 未开/崩溃</td>
<td>launcher 生命周期接口存在；无完整独立健康面</td>
<td>app/bootstrap 始终可显示任务缓存、故障和恢复；自动重启受控服务</td>
</tr>
<tr>
<td>DeepSeek 不可用</td>
<td>available 主要是 key 存在，不是网络或余额验证</td>
<td>显示凭证已配置/服务连通/最近调用结果；离线暂停、取消、已知事实处理可继续</td>
</tr>
<tr>
<td>profile 缺失/损坏</td>
<td>基本 JSON loadable 检查，不是完整/无冲突验证</td>
<td>产品内导入/修复；明确缺资料与解析失败，已有任务不丢</td>
</tr>
<tr>
<td>OTP transport 不可用</td>
<td>不在当前完整 readiness 检查中</td>
<td>按任务能力显示短信接收可用/降级；可安全手工填入本地控件，不发给模型</td>
</tr>
<tr>
<td>GitHub 更新失败</td>
<td>有守卫和 restart_required</td>
<td>仍能使用旧健康版本、看到原因并重试；不能用“检查 ChatGPT”代替修复路径</td>
</tr>
<tr>
<td>网络/VPN 异常</td>
<td>多处通用异常/阻塞</td>
<td>区分招聘站、模型、更新源、短信源；不重置已确认事实，不消耗无限重试</td>
</tr>
</tbody>
</table></div>
<h2 id="01_audit-4-b--岗位发现与精确定位">4. B — 岗位发现与精确定位</h2>
<p>**过度依赖 exact URL 是明确事实。**消费端 schema/policy 主动拒绝没有当前消息 exact URL 的任务创建；文档把发现交给上游 PJSDAS/Gmail。因此这不是偶尔 resolver 失败，而是产品入口合同与目标体验不一致。[E02]</p>
<p>正确修法不是取消目标约束：应当取消“URL 必须由用户亲自提供”，保留“执行器必须收到已经证明身份的精确目标”。公司+岗位名先变成只读发现任务；官方搜索/列表/详情证据构成 VerifiedJobTarget，然后才能填表。</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>场景</th>
<th>现有薄弱点</th>
<th>结构性修法</th>
</tr>
</thead>
<tbody>
<tr>
<td>精确职位 URL</td>
<td>URL 看起来像职位不等于已核验公司/地点/批次</td>
<td>页面身份核对后建立 job_id+tenant+campaign+location 绑定</td>
</tr>
<tr>
<td>公司+岗位名</td>
<td>CREATE 入口不能完成；resolver 只有有限公司</td>
<td>discovery 接口和受支持门户目录；正确返回候选或无法可靠定位</td>
</tr>
<tr>
<td>招聘首页</td>
<td>OPPO landing 有特例而非通用页面分类</td>
<td>landing/list/detail/auth/form/success page type；基于证据转移</td>
</tr>
<tr>
<td>岗位列表</td>
<td>未形成可验证的查询、分页、筛选完整性</td>
<td>记录搜索词、筛选、分页终点/来源；不得见到一个 exact title 就宣布唯一</td>
</tr>
<tr>
<td>同名多岗</td>
<td>标题 exact 不足以判唯一；OPPO location 参数缺乏有效落实</td>
<td>显示地点、业务、毕业批次、职位 ID；用户仅作真正选择</td>
</tr>
<tr>
<td>岗位下线</td>
<td>没有统一 unavailable 结果</td>
<td>明确下线；不替换为“相近岗位”</td>
</tr>
<tr>
<td>第三方 ATS</td>
<td>checkpoint 偏同主机，target 同源与登录跳转未统一</td>
<td>验证官方→ATS 重定向链，记录雇主/tenant身份；不广泛信任跨域</td>
</tr>
<tr>
<td>登录后 URL 改变</td>
<td>重开原 URL/取最后页不保证同一申请</td>
<td>任务绑定的会话/弹窗/草稿 ID，重新核实岗位</td>
</tr>
<tr>
<td>SPA</td>
<td>native snapshot + 固定等待，task URL 又限制 fragment</td>
<td>将安全路由和秘密参数分开解析；不把所有 hash 路由视为安全或一律不能用</td>
</tr>
</tbody>
</table></div>
<p>公开只读验证能提早发现页面变化和查询失败，但不能证明登录后的表单可写或自动认证可用。[E06,E07,E17]</p>
<h2 id="01_audit-5-c--认证与身份">5. C — 认证与身份</h2>
<p>现有 SMS 路径比简单“填验证码”更成熟：唯一 OTP/手机号/初始发送控件、explicit auth container、标准协议授权、再次证明重渲染后的发送控件、禁止自动重发和最终提交，都有真实 DOM 测试。应拆为明确认证子系统，不应丢弃这些守卫。[E05,E14,E19]</p>
<p>但当前仍有三种错位。第一，显式同屏密码/短信/扫码模式没有完整的 active-mode 状态；密码可使整页保守退为人类处理，即便存在可确认的 SMS。第二，OTP 接收没有贯穿发送事件和重试的 attempt identity；在内存单次消费并不证明跨进程只发送一次。第三，BrokerBridge 对已有 relay 的短时调用与取消，不构成用户收到迟到短信后自动续跑的持续监听。[E05,E14]</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>认证场景</th>
<th>必须达到的用户结果</th>
</tr>
</thead>
<tbody>
<tr>
<td>已登录</td>
<td>验证当前账户和目标，直接进入表单，不再发码</td>
</tr>
<tr>
<td>手机短信</td>
<td>从已确认 phone 本地填入；发送行为有授权、限频和结果记录</td>
</tr>
<tr>
<td>自动 OTP</td>
<td>匹配 task + auth_attempt + site + 时间窗；只在内存进入确证的 OTP 字段</td>
</tr>
<tr>
<td>迟到/错误/多条 OTP</td>
<td>不误用于新 attempt 或另一任务；需要重发时说明理由；不把旧码重复猜测</td>
</tr>
<tr>
<td>resend</td>
<td>本 v1 只允许用户明确批准的单次重发，记录 cooldown；不得默默循环</td>
</tr>
<tr>
<td>QR/password/CAPTCHA/slider/face/security key</td>
<td>不绕过；定位正确窗口，告诉用户做什么；完成后自己检测并恢复适当步骤</td>
</tr>
<tr>
<td>多种登录方式</td>
<td>可以选择已经证实和授权的 SMS 模式；不是因为存在扫码文字就永远停下</td>
</tr>
<tr>
<td>登录过期</td>
<td>重新认证而不是重建任务；草稿/事实不丢；旧 readiness 失效</td>
</tr>
<tr>
<td>登录后没回岗位</td>
<td>恢复 VerifiedJobTarget，不能停在个人中心却说正在投递</td>
</tr>
</tbody>
</table></div>
<h3 id="01_audit-otp-隐私限定">OTP 隐私限定</h3>
<p>broker memory-only 值得保留，但不能声称 OTP 在整个设备/生态中从未落盘：系统短信库本来保存短信，已有 relay 的保存行为也需要单独审计。产品应保证<strong>执行器不新增 OTP 持久副本，不传给 DeepSeek，不写队列/日志/诊断/截图/仓库</strong>。现有 relay 的真正网络位置和隐私配置在本次未读取，不能假定它是纯本地。[E14]</p>
<h2 id="01_audit-6-d--表单执行">6. D — 表单执行</h2>
<p>现有 adapter 有实际填充和读回，不是“只发 fill 不验证”。但主要发现范围是可见且启用的 native input/textarea/select，数量有上限。它不拥有完整的 section/row/dependency graph；字段 locator 的 ID/name/global nth 与 first match 在重复行和重绘下没有强身份。控件可填不等于经历填对、上传成功、草稿保存。[E05]</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>表面</th>
<th>当前风险</th>
<th>要求</th>
</tr>
</thead>
<tbody>
<tr>
<td>普通 input/select</td>
<td>可用基础良好；select 包含匹配/不一致 warning 过宽</td>
<td>对 exact enum 和授权 normalization 作明确映射，错误城市是 FAIL</td>
</tr>
<tr>
<td>React/Vue controlled</td>
<td><code>.fill</code> 触发标准交互，但固定 sleep 不证明 state/网络保存</td>
<td>重渲染后重取 scoped locator、值和保存状态；真实 controlled fixture</td>
</tr>
<tr>
<td>动态/依赖字段</td>
<td>静态列表不能表示选择省后城市重置</td>
<td>每次依赖变化重建局部结构和验证期望值</td>
</tr>
<tr>
<td>教育/工作/项目多行</td>
<td>相同 name 或 index 可能选错行；无完整增删/对齐</td>
<td>stable record ID + section/row identity；不能把项目自动当正式工作</td>
</tr>
<tr>
<td>自定义下拉/日期/地区</td>
<td>不只 native select；虚拟化/级联控件不覆盖</td>
<td>capability drivers，明确支持范围；不支持≠缺用户个人事实</td>
</tr>
<tr>
<td>日期</td>
<td>month/day 不得靠猜补齐</td>
<td>记录 precision，确实缺日/月且网站要求时才问</td>
</tr>
<tr>
<td>工资</td>
<td>数字、月/年、税前/后和币种语义未形成强类型</td>
<td>明确用户选择与单位转换，不自动用通用默认覆盖</td>
</tr>
<tr>
<td>简历/照片</td>
<td>设置文件、basename 不代表服务器资产就绪</td>
<td>检查文件类型/大小/hash/正确资产 + 上传完成/服务器 draft 附件标识</td>
</tr>
<tr>
<td>简历解析</td>
<td>原文要求其不可信，但修复完整性未自动完成</td>
<td>与 canonical structured inventory 比较误分类/遗漏，修复后读回</td>
</tr>
<tr>
<td>合规/声明</td>
<td>standing policy 有价值，但泛化过度会误用</td>
<td>独立 scoped consent/声明版本；新承诺、法律判断、签名本人决定</td>
</tr>
<tr>
<td>主观问题</td>
<td>当前不是可靠的基于资料撰写子系统</td>
<td>仅基于明确事实形成候选答案、按授权策略复用；不得为减少提问编经历</td>
</tr>
<tr>
<td>未知客观事实</td>
<td>存在正确保守停止，但映射失败和事实缺失混用</td>
<td>先查已确认事实/文档，再判断是否需要人；批量有上下文提问</td>
</tr>
<tr>
<td>required-but-hidden</td>
<td>未出现在 native-visible 范围不代表无要求</td>
<td>展开必要区段、依赖/站点错误、契约判定；无法证实不可 READY</td>
</tr>
<tr>
<td>validation error</td>
<td>当前 missing/error 不是完整网站 validator</td>
<td>field/form/server errors 都保留局部归因，修复后独立复查</td>
</tr>
<tr>
<td>autosave / 多页</td>
<td>固定等待/plan 累积不能证明前页仍保存</td>
<td>以服务器 draft 或重新进入页面的读回结果核验；跨页完成清单</td>
</tr>
</tbody>
</table></div>
<h3 id="01_audit-本次反例">本次反例</h3>
<p>本容器运行的源码片段 P02：期望城市“北京”，实际 select“上海”，现有 <code>validate()</code> 仍返回 <code>ok=true</code>，只记录 warning。它不代表已错填某个真实岗位，但足以证明 validator 不能作为当前可靠提交判据。[E05；evidence/source_excerpt_probes.json]</p>
<h2 id="01_audit-7-e--个人资料证据与记忆">7. E — 个人资料、证据与记忆</h2>
<p>正确基础包括：canonical key、证据来源、显式用户覆盖、较低权重历史资料、文件 hash 和项目清单。资料不应被随意改成另一个 schema/云数据库，更不能用模型总结覆盖真实原始事实。[E15,E16]</p>
<p>当前产品缺口在读写闭环。CLI 的 application-scoped answer / promote-profile 设计与消费者 Worker 的内存答案不同；消费者告诉系统的普通事实在服务重启后可消失。把 OTP 的 memory-only 原则扩展到所有个人回答，牺牲了用户要求的可靠记忆。隐私需要权限、最小化、scope 和受控本地持久化，不等于普通事实不得保存。[E07,E08,E16]</p>
<p>新的事实分类必须明确：一次性答案（只此申请）；可复用客观事实（用户明确确认保存）；偏好/薪资策略（有作用域和有效期）；一次声明/法律承诺（不偷渡成永久政策）；模型推导或站点既有值（不是已确认事实）。冲突要保留来源和版本，新确认不能被旧简历覆盖。</p>
<p>项目/科研必须使用稳定记录身份，不再仅靠标题包含判覆盖。研究经历不因投产品岗而消失；用户针对单一申请的排除不得删除 canonical inventory。没有项目区的站点应记录真实的 NOT_APPLICABLE，而不是强迫伪造结构；有区但没填是另一回事。</p>
<p>重复提问应按原因诊断：profile 确实没有；资料导入没提取；字段语义未识别；重复行上下文丢失；曾经回答但未持久化；scope/版本冲突。这些不能全变成“请问你的学校是什么”。</p>
<h2 id="01_audit-8-f--安全与隐私">8. F — 安全与隐私</h2>
<p>现有 localhost Host/Origin/token 检查、UI 一次 ticket、HttpOnly cookie、0700/0600 本地操作数据、credential-like 拒绝、敏感值屏蔽、禁止最终提交，有重要保留价值。不能把这些摘要标记当作全路径信息流审计。[E07–E15]</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>数据</th>
<th>当前边界/风险</th>
<th>v1 要求</th>
</tr>
</thead>
<tbody>
<tr>
<td>OTP</td>
<td>broker 内存短期；relay/OS 另有边界</td>
<td>不新落盘/不模型/不导出；attempt 匹配；用户验证码输入独立本地通道</td>
</tr>
<tr>
<td>手机号</td>
<td>canonical 本地可填；自由聊天可能直接被发给模型</td>
<td>优先本地事实表单；输入脱敏分类与外发预览，禁止认证 phone 外发</td>
</tr>
<tr>
<td>cookies / password / API keys</td>
<td>专用 Chrome、Keychain 等已有基础</td>
<td>不进入模型、queue、diagnostics、Git；可撤销本机授权，不复制默认浏览器 profile</td>
</tr>
<tr>
<td>applicant facts</td>
<td>canonical 本地持久化，脱敏 export</td>
<td>本地完整复核可见；copy-safe export 采用 schema allowlist，而非靠字段名或布尔声明自证</td>
</tr>
<tr>
<td>DOM labels/options</td>
<td>虽不发送 value 字段，label 邻近文本可能带个人内容</td>
<td>在模型边界做语义数据分类/最小化；站点 DOM 属不可信数据，不是指令</td>
</tr>
<tr>
<td>logs / errors</td>
<td>删除所有异常文字保护隐私，却失去原因</td>
<td>保存 safe error class、子系统、时间、命令/事件 ID；不保存原始异常 request body</td>
</tr>
<tr>
<td>screenshots / trace</td>
<td>通用 adapter 可全页截图；daemon operational audit 禁用</td>
<td>合成环境可完整 trace；真站默认关闭导出，明确授权、脱敏复核后才分享</td>
</tr>
<tr>
<td>browser ownership</td>
<td>loopback 不等于证明连接到本应用进程</td>
<td>进程/profile/session 归属证明、私有权限；不误接端口占用服务</td>
</tr>
<tr>
<td>GitHub</td>
<td>当前忽略私人目录和 artifacts 是正确边界</td>
<td>public-safe fixtures/代码/摘要；私人真站截图、DOM、Cookie、简历绝不提交</td>
</tr>
</tbody>
</table></div>
<p>无法从本次代码审阅推断“已经泄漏”，也不能推断“绝不会泄漏”。发布门槛应包含带 canary 的全出口测试和威胁模型：恶意网页/提示注入、错误目标/重定向、错误本地客户端、秘密进入日志，以及释放后的旧 UI/worker 请求。对已完全攻陷用户 OS 的防护不作为个人 v1 可以保证的安全性质。</p>
<h2 id="01_audit-9-g--故障与恢复路径推演">9. G — 故障与恢复路径推演</h2>
<p>**本节是固定版本的代码级故障推演，不是已经在用户 Mac 执行过的故障注入。**本次实测仅有 P01–P03；真正系统级注入属于 JCR-01/02/05/08/09 的自动门槛。</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>故障/中断</th>
<th>当前可从代码推出的行为/限制</th>
<th>v1 安全结果</th>
</tr>
</thead>
<tbody>
<tr>
<td>Browser crash</td>
<td>Worker 对 CDP 不可用 block，launcher 另能重开</td>
<td>自动恢复自有浏览器；目标/草稿重新证明后续跑，不让用户理解 CDP</td>
</tr>
<tr>
<td>daemon crash</td>
<td>SQLite/lease 保留任务，内存答案/OTP 失去；无人负责常态重启</td>
<td>bootstrap 重启；事实恢复；OTP 作废并重新协商，不能重放已过期码</td>
</tr>
<tr>
<td>Mac sleep</td>
<td>租约和外部页面/session 的有效性可能分离；无完整 wake 协调证据</td>
<td>唤醒事件标记 session epoch，读回后续跑；休眠期间不宣称执行</td>
</tr>
<tr>
<td>reboot</td>
<td>服务和 cookie memory 消失；任务留存</td>
<td>app 冷启动恢复；旧 review 失效并只读复核，稳定事实不重问</td>
</tr>
<tr>
<td>network loss</td>
<td>部分通用 ERROR/backoff，缺外部副作用结果判别</td>
<td>区分断网前/后、已知未发生/已发生/未知；未知效果不盲目重发</td>
</tr>
<tr>
<td>tab closed</td>
<td>URL 匹配可能开新页，不保证同草稿</td>
<td>从 task-owned session/draft 再定位；不改别的标签</td>
</tr>
<tr>
<td>page refresh</td>
<td>原生控件重新发现，但 plan/row identity 可能不一致</td>
<td>放弃旧 locator/观测版本，重新结构化对齐</td>
</tr>
<tr>
<td>session expired</td>
<td>auth 重新 block，任务原目标/草稿不一定已贯通</td>
<td>挂起表单写入→正确安全接管→返回原岗位</td>
</tr>
<tr>
<td>site timeout</td>
<td>短 sleep 和通用异常可能导致假空页或重复动作</td>
<td>有限自恢复；保留 task + facts + progress；不返回假 READY</td>
</tr>
<tr>
<td>DeepSeek timeout</td>
<td>fail closed 保状态是好的；全局 mutation lock 可阻塞控制</td>
<td>remote 调用不占本地控制锁；暂停/取消独立；陈旧提议失效</td>
</tr>
<tr>
<td>OTP late arrival</td>
<td>短 relay wait 可结束；没 first-class attempt</td>
<td>等待可持续且超时明确；旧码不进新请求，按授权重发一次</td>
</tr>
<tr>
<td>retry exhaustion</td>
<td>停止且不能简单 resume 是安全的，但只有笼统原因</td>
<td>指向具体可修条件；修复后新受控 recovery attempt，不清空历史</td>
</tr>
<tr>
<td>partial form</td>
<td>checkpoint 是 URL+stage，不是外部副作用账本</td>
<td>对当前 draft 与意图做差异恢复；重复行/附件/短信不重复造成副作用</td>
</tr>
<tr>
<td>update paused task</td>
<td>guard 阻止活跃/OTP更新；普通 answers 仍可能因重启消失</td>
<td>凭据暂态安全终止；普通事实持久；schema 兼容；任务 identity 不变</td>
</tr>
<tr>
<td>code/dependency update fail</td>
<td>ff 后 restart_required，无完整 known-good 回退</td>
<td>候选隔离检查，不健康不激活；已激活失败原子回退且不丢新事实</td>
</tr>
</tbody>
</table></div>
<p>本次 P03 证明 <code>latest_page()</code> 在给定“任务页 + 后开无关页”时选择后者。这是标签归属不足的确定性反例，不应等真人发现后再局部排除某个网页。[E06；探针结果]</p>
<h2 id="01_audit-10-h--ready_to_submit-与最终复核">10. H — READY_TO_SUBMIT 与最终复核</h2>
<p>当前最关键的差距是：**“自动程序必须停止”不等于“申请已经值得用户放心提交”。**在当前代码中，检测到 final control 后先置 READY，再构造 review；review 可以含 <code>REVIEW_REQUIRED</code>。项目覆盖来自 plan 值和标题子串，附件清单来自配置 basename，具体复核工作仍大量委托用户。[E03,E04]</p>
<p>最终应检查：精确公司/职位 ID/地点/批次、当前登录身份、全部已知必填与潜在必需区段、教育/工作/项目/研究的分类和完整性、日期精度、工资单位、声明范围、未知/冲突事实、意外默认值、附件服务器状态、保存结果。最终页面没有可验证证据时，状态必须是“仍需检查/部分就绪”，不能是 READY。</p>
<p>具体证书及逐项客观门槛见架构与 acceptance matrix。用户的最后一步应该是判断“这确实是我愿意提交的申请”，不是重新逐项查找自动化是否漏了一个项目或把城市选错。</p>
<h2 id="01_audit-11-consumer-ux不只是配色">11. Consumer UX：不只是配色</h2>
<p>截图的主要问题不是米黄色，而是<strong>没有清楚地回答：系统正在做什么、为什么停在这里、现在确实需要我做什么</strong>。顶部绿色“已就绪”与任务卡 security/validation blocker 并存；服务健康、任务状态与安全原则混为一谈。代码中卡片不能提供任务级操作和完整复核，聊天状态留在页面 DOM，重新打开无法重建真正的上下文。[E09,E10]</p>
<h3 id="01_audit-111-目标信息架构">11.1 目标信息架构</h3>
<p>主窗口只保留三个层级：当前任务/需要我的事项；任务详情与可执行对话；较低优先级的资料/设置/诊断。不是四个大数字占据关键位置。队列多任务不等于同时控制多个申请：单一浏览器 writer，多个持久任务，可跳过等待任务处理另一个安全任务。</p>
<p>每项等待显示：具体公司/职位、已完成结果、等待类型、下一步按钮和恢复方式。举例：“牛客登录页面要求滑块验证。点击‘打开验证窗口’完成后，我会重新检查并继续。”不是“NEEDS_USER_ACTION/security_challenge”，也不是让人输入“继续”来猜任务。</p>
<p>对话仍是自然主入口，但关键操作同时有可靠按钮和本地表单：暂停、取消、切换具体任务、填写未知事实、决定是否记住、查看复核、打开真实网站。输入受理马上返回 command receipt；进度根据真实 observation/result 更新，不用虚构百分比。</p>
<h3 id="01_audit-112-错误与通知">11.2 错误与通知</h3>
<p>将“离线等待自动重试”“你需要决定”“你需要完成安全验证”“当前控件尚不支持”“系统故障需要诊断”分开。不可把未知保存结果报告为“一切没变”。重复通知去重；不因每个字段/重试弹窗。系统正常后台工作不抢焦点，只有验证码手动接管、安全验证和最终提交前请求注意。</p>
<h3 id="01_audit-113-应用形态">11.3 应用形态</h3>
<p>建议薄 macOS shell 承载本地 manager UI，同时提供独立 bootstrap/诊断/服务生命周期；招聘站继续使用专用 Chrome，不嵌进同一个 WKWebView，不复制 Cookie，不换浏览器内核。主窗口不需要暴露 localhost 地址和完整 Chrome chrome，但登录/最终提交时真实网站域名必须清楚可见。</p>
<p>浏览器不必永久与经理物理嵌在一个窗口。**任务级视觉关联和准确接管比“看起来一体”更重要。**正确任务窗口聚焦、清楚返回按钮、人工期间暂停写入、完成后自动重新观察，已经足以实现低摩擦体验。PWA/Chrome app mode 可过渡，但仅隐藏地址栏不能修 daemon/更新/权限问题。[技术参考见 E-index]</p>
<h2 id="01_audit-12-已经实现但产品没有完成的清单">12. “已经实现但产品没有完成”的清单</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>已有资产</th>
<th>不成立的等号</th>
<th>未完成的结果</th>
</tr>
</thead>
<tbody>
<tr>
<td>.app launcher</td>
<td>launcher = 独立 Mac app</td>
<td>安装、自修复、单窗口、离线设置/诊断</td>
</tr>
<tr>
<td>preflight</td>
<td>key/file/port 存在 = 准备好执行</td>
<td>网络、资料覆盖/冲突、附件、OTP source、当前 session</td>
</tr>
<tr>
<td>DeepSeek Manager</td>
<td>typed tool call = 自然语言经理</td>
<td>上下文绑定、拒绝原因、异步执行回执、离线控制</td>
</tr>
<tr>
<td>resolver</td>
<td>能搜 OPPO = 能自主定位岗位</td>
<td>landing/list/分页/筛选/候选歧义/third-party identity</td>
</tr>
<tr>
<td>generic adapter</td>
<td>原生输入可填 = 招聘网站通用</td>
<td>结构化经历、自定义/依赖/异步/多页</td>
</tr>
<tr>
<td>profile keys</td>
<td>资料存在 = 用户不会被重复问</td>
<td>持久 consumer answer、版本、context、有效 scope</td>
</tr>
<tr>
<td>evidence</td>
<td>source 引用 = 每个当前值都正确</td>
<td>冲突、缺失、失效时间、导入失败和错误解析</td>
</tr>
<tr>
<td>project coverage</td>
<td>标题命中 = 实际项目填全</td>
<td>真实重复行、项目身份、内容/时间、排除理由</td>
</tr>
<tr>
<td>attachment refs</td>
<td>文件输入/名字 = 上传成功</td>
<td>upload receipt、正确版本、draft 保存</td>
</tr>
<tr>
<td>validation</td>
<td>可见 native fields 通过 = 所有必需信息正确</td>
<td>隐藏/未展开/前页/异步服务端校验</td>
</tr>
<tr>
<td>final checklist</td>
<td>提醒人去查 = 系统已经查过</td>
<td>独立 observed review certificate</td>
</tr>
<tr>
<td>no submit API</td>
<td>submit() 抛错 = 任意点击都无最终副作用</td>
<td>next/apply/Enter/确认必须受动作语义边界约束</td>
</tr>
<tr>
<td>OTP broker</td>
<td>收到内存 code = 自动登录完成</td>
<td>send/wait/late/mode/resend/attempt/return-target 生命周期</td>
</tr>
<tr>
<td>SMS orchestration</td>
<td>初始发送测试通过 = crash 不重复发码</td>
<td>durable request metadata + unknown-effect reconcile</td>
</tr>
<tr>
<td>queue</td>
<td>多个 task rows = 安全多任务</td>
<td>浏览器与人工接管归属、命令任务绑定</td>
</tr>
<tr>
<td>checkpoint</td>
<td>stage/URL 保存 = 真实崩溃恢复</td>
<td>外部草稿/重复行/附件结果重新证明</td>
</tr>
<tr>
<td>retries</td>
<td>重试有上限 = 自动恢复</td>
<td>故障分类、恢复计划、未知副作用不重放</td>
</tr>
<tr>
<td>diagnostics</td>
<td>摘要可以复制 = AI 足以定位本机问题</td>
<td>safe root cause、时间线、加载版本、独立故障入口</td>
</tr>
<tr>
<td>updater</td>
<td>ff-only/restart = 消费级更新</td>
<td>依赖/schema/staged activation/rollback/失败仍可用</td>
</tr>
<tr>
<td>green header</td>
<td>程序知道 user-click = 当前任务已就绪</td>
<td>真实状态模型与 readiness 证据</td>
</tr>
<tr>
<td>tests</td>
<td>240 pass = consumer reliable</td>
<td>独立判据、macOS/真实浏览器/多平台最终认证</td>
</tr>
<tr>
<td>protected targets</td>
<td>已知 registry 拒绝 = 新提交也不会重复</td>
<td>用户最终动作后只读验证与保护登记</td>
</tr>
</tbody>
</table></div>
<h2 id="01_audit-13-保留强化拆分退役">13. 保留、强化、拆分、退役</h2>
<p>**保留：**local-first、确定性 policy 和已知事实优先、ApplicationExecutor 的执行骨架、SQLite claim/租约/fencing、单 live worker、OTP 的短生命周期、保护已提交目标、隔离浏览器测试、Host/Origin/token 防护、更新锁/原子状态/安全 fence。对这些保留回归测试，边界加固而非整块重写。[E02–E15,E19–E23]</p>
<p>**结构性拆分：**Manager 中的 discovery；GenericWebAdapter 中的认证/通用组件/页面观察；TaskQueue 的任务业务状态与暂停来源；profile 的证据导入和消费级确认流程；review 的说明与客观 gate；UI 静态 HTML 与任务读模型；runtime bootstrap 与可更新业务版本。</p>
<p>**有条件退役：**全局 last-page / unscoped nth 默认选择、把 readiness 写死的 UI、因缺 key 拒绝展示修复界面的启动合同、消费者普通事实 memory-only 合同、生产目录原地 pull 的主更新路径；旧 Schneider/legacy CLI 中重复事实/策略只保留明确兼容隔离，不让新主链路调用两套规则。没有证明已迁移和通过历史安全回归前不删除旧实现。</p>
<p>正则可以继续用于可测试的标签别名和候选特征；**不能让越来越长的正则列表继续承担岗位身份、认证状态、表单结构和外部动作副作用的全部模型。**这就是从局部 patch 累积走向子系统的必要改造。</p>
<h2 id="01_audit-14-对最终目标的十项明确回答">14. 对最终目标的十项明确回答</h2>
<ol>
<li>距离：已经有可利用的执行地基，但缺少一整层端到端产品化与认证；不是仅差 UI 的尾声。</li>
<li>最大缺口：见前述十项；前三项安全/正确性 gate 必须优先。</li>
<li>不值得重写：SQLite/fencing、local-first、typed policy、OTP 最小秘密面、专用测试浏览器、受保护目标和现有有效回归。</li>
<li>必须结构性重构：目标发现与绑定、浏览器归属、结构化表单、auth lifecycle、durable facts、independent review、bootstrap/update 和 UI read model。</li>
<li>未来日常使用：打开 app→一句话交代目标→真实进度/后台处理→仅必要决策/验证→本地复核→用户点网站最终提交。</li>
<li>人仍须做：首次私密资料/权限配置、歧义岗位选择、新事实/主观偏好/新承诺、安全验证、最终审阅和点击。</li>
<li>不应再做：Terminal/端口/服务定位、日志拼装、已知资料重复输入、正常崩溃恢复、每轮测试、受支持门户的常规 URL 搜寻。</li>
<li>Desktop Commander：不应成为正常运行或维护依赖，只可作为开发和特别故障的可选工具。</li>
<li>真人验收：可将计划中的验收集中到最后 1–3 个时段，条件是前面门槛真实通过、选择适当真实岗位；不能保证任何外部平台的挑战都只出现三次。</li>
<li>v1 consumer-ready：所有强制结果门槛满足、明确支持矩阵获得真实证据，候选安装/恢复/更新通过，再经过短期正常日用确认无需工程介入；不是九个 PR 合并当天自动宣布。</li>
</ol>
</section>
