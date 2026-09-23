<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="05_rounds" class="chapter"><h1 id="05_rounds-开发轮次与工程闭环">开发轮次与工程闭环</h1>
<p>包：<code>JAE-CONSUMER-READINESS-v1</code>。本文件是可注册的执行合同，不是本轮开始实现的授权。</p>
<h2 id="05_rounds-0-统一规则">0. 统一规则</h2>
<p>每一轮沿用 remote main + 当前有效 docs/STATUS 作为事实源。只保留一个 writer；有正在工作的 PR 时接续该 PR，不新建平行实现。每轮必须有可运行的生产路径、针对性自动证据、历史安全回归、迁移/回退验证、exact-head CI、合入后集成证据与 receipt。单纯类/接口/页面存在不算完成。</p>
<p>本包不要求一轮后真人试站。JCR-01～08 owner interaction 默认 NO。开发者能通过合成输入、真实隔离浏览器、真实进程、公开只读站点、macOS runner 完成的验证不得转交 owner。只有真实世界权限/付费/新产品边界无法解决时才阻塞并指出最小动作，不能把一般 debug 当成 owner blocker。</p>
<p>所有新路径必须受原 no-submit/secret/protected-target 不变量约束。新的矩阵在整个包完成前允许 NOT_RUN，但每轮自己负责的 gate 必须通过；不把未来未实施项隐藏成 skip 后宣称整个包通过。</p>
<h2 id="05_rounds-1-依赖关系">1. 依赖关系</h2>
<pre><code class="language-text">JCR-01 统一任务/命令合同 + 独立验收底座
   └─ JCR-02 浏览器归属、bootstrap、自诊断/恢复
         ├─ JCR-03 岗位发现与精确目标绑定
         └─ JCR-04 持久事实、证据与申请记录
                  （顺序执行，非要求并行 writer）
JCR-02 + 03 + 04 → JCR-05 认证/OTP完整生命周期
JCR-03 + 04 + 05 → JCR-06 结构化表单执行与恢复
JCR-06 + 04 → JCR-07 最终证书、复核和手动边界
JCR-01…07 → JCR-08 完整消费入口、应用壳和事务式发布
JCR-01…08 → JCR-09 集成认证、最后真人验收、短期日用
</code></pre>
<p>顺序上的重要限制：测试、诊断、恢复先于大规模站点功能；主体 UI/read model 在前几轮逐步接入，不到第八轮才发现状态无法呈现；第八轮负责完成交付形态，不是突然把前七轮全部重写。</p>
<h2 id="05_rounds-jcr-01--authoritative-task-contract--outcome-test-foundation">JCR-01 — Authoritative Task Contract &amp; Outcome Test Foundation</h2>
<p><strong>Objective</strong>：建立可证明的任务/命令模型与独立 oracle，使后续每一项能力都能用真实结果验收，并把已发现的假 READY/错页/错城市反例变成防回归门槛。</p>
<p><strong>Files/surfaces</strong>：现有 <code>executor/autonomy/{manager,queue,worker,supervisor}.py</code>、<code>executor/models.py</code>、<code>tests/</code>、CI；新增或等价 <code>executor/commands/</code>、<code>executor/tasks/</code>、<code>tests/fixtures/</code>、<code>tests/e2e/</code>、<code>tests/state_machine/</code>、本包 STATUS/receipts。</p>
<p><strong>Implementation scope</strong>：</p>
<ul>
<li>typed command envelope、task selection/context、expected revision、idempotent receipt；本地 pause/cancel/status 独立于模型等待。</li>
<li>phase/run_state/wait_reason/paused_from 的最小权威 schema 和合法转移；迁移旧状态与 retry provenance，保留同一 task IDs。</li>
<li>独立 SyntheticATS 服务、golden profile/selection policy、submit/SMS/upload counters；实际 UI→service→browser 的 harness 接入，不只 direct runner API。</li>
<li>加入本次三个反例的 direct-import tests；将已知不安全允许行为改成保守拒绝/明确待审作为最小安全修复，完整领域实现留后轮。</li>
<li>初步模型/日志/诊断出口 canary 与所有历史安全不变量；拆分 CI 层级和统一 evidence schema。</li>
</ul>
<p><strong>Automated tests</strong>：状态序列/非法转移/同一 command 重发/丢响应；A/B任务指代与否定“不要暂停”；模型超时期间 local control；三反例；现有 no-submit/auth/privacy/queue tests；一个经生产 UI 完成的简单合成任务，独立 server oracle。</p>
<p><strong>Acceptance gate</strong>：所有命令可查是否受理；local control 不被 provider 网络锁阻塞；错误项目/城市/页不会继续被现有 gate 误认为充分通过；兼容迁移保留任务与保护记录；基础 golden journey 正确、submit count 0。F类基础出口门槛通过，后续新增出口须继续扩展。</p>
<p><strong>Stop condition</strong>：错误目标/最终动作/秘密越界，或无法无损解释旧状态迁移，立即暂停该变更排查；普通代码/测试问题自行修复。不得进入 live profile 验证。</p>
<p><strong>Owner interaction</strong>：NO。</p>
<p><strong>Rollback</strong>：添加字段可被旧版忽略；旧可执行 schema 的一致备份；回退命令入口时不得放回已经明确发现的 unsafe 自动行为，必要时保持保守只读/阻塞。</p>
<h2 id="05_rounds-jcr-02--owned-browser-bootstrap--recovery">JCR-02 — Owned Browser, Bootstrap &amp; Recovery</h2>
<p><strong>Objective</strong>：浏览器、任务页、人工接管和本地服务有明确归属；服务/浏览器中断后可安全恢复，系统坏了仍能看到诊断。</p>
<p><strong>Files/surfaces</strong>：<code>executor/browser.py</code>、autonomy <code>worker/cli/supervisor/diagnostics</code>；新增 <code>browser_runtime/</code>、<code>runtime/</code>、最小 app/bootstrap 与 task read model；crash tests。</p>
<p><strong>Implementation scope</strong>：</p>
<ul>
<li>证明专用 profile/process/session ownership；task/tab/popup lineage、session epoch、目标观察绑定；去除生产链路 global latest-page 选择。</li>
<li>Guard 覆盖 task revision/lease/document，人工操作 lease 和取消协调。</li>
<li>稳定 bootstrap、基础本地健康/设置/诊断入口先可用，模型/资料缺失也可打开；安全自动启动/重启自有服务和浏览器。</li>
<li>action attempt/outcome metadata、UNKNOWN_OUTCOME 协调策略；恢复先观察，不盲重放写入。</li>
<li>typed safe errors + 时间/相关 ID/加载版本；正确区分 endpoint alive 与可用。</li>
</ul>
<p><strong>Automated tests</strong>：真正启动/kill隔离服务、浏览器、fixture server；关闭任务tab/开无关tab/重定向弹窗；pause during operation；UI session重连；基本 Mac hosted启动；检查原始用户Chrome默认路径永远不被访问。</p>
<p><strong>Acceptance gate</strong>：合成任务在中途 kill/restart 后 actual draft 和任务身份正确；无重复基础写入；ordinary facts 本轮尚未新实现的持久能力标 pending，不冒充完成；服务不可用时 bootstrap 能报告 safe cause 并恢复；G类会话/进程基础门槛通过。</p>
<p><strong>Stop condition</strong>：无法确认本应用浏览器归属、会话所指岗位不明、外部结果未知时安全停下；不要关闭/修改无关用户窗口。真实机器新权限不是自动索要的 debug 手段。</p>
<p><strong>Owner interaction</strong>：NO。真实本机权限留最后；使用合成 Mac 环境验证系统接口。</p>
<p><strong>Rollback</strong>：保持旧 profile不变，元数据可回退；由bootstrap切回上个健康服务，保留新task/receipt；旧 global-last-page 禁止恢复为自动默认。</p>
<h2 id="05_rounds-jcr-03--job-discovery--verified-target-identity">JCR-03 — Job Discovery &amp; Verified Target Identity</h2>
<p><strong>Objective</strong>：“投递公司+岗位”“给首页/列表”成为真实可执行入口，仍保留强目标和授权边界。</p>
<p><strong>Files/surfaces</strong>：<code>target_resolver.py</code>、<code>manager.py</code>、<code>queue.py</code> retarget、adapter registry；新增 <code>discovery/</code>、site contract registry、candidate-selection UI、versioned discovery fixtures。</p>
<p><strong>Implementation scope</strong>：</p>
<ul>
<li>DiscoveryRequest→readonly queries/page classification→CandidateSet→VerifiedJobTarget；Manager移除重复公司逻辑。</li>
<li>保留并迁移 OPPO/Schneider 公共解析资产；增加通用官方入口/ATS route contract，不把某个shared ATS等同单公司。</li>
<li>搜索、筛选、分页/懒加载完整性，按location/campaign/employment校验；歧义可选，未找到/下线明确。</li>
<li>官方→ATS/登录重定向后同目标证明；task identity使用 tenant/job/campaign 等归一化键。</li>
<li>公开只读调查冻结至少三种候选平台机制、能力范围、来源、限制；先测公共页面，不访问私人申请。</li>
</ul>
<p><strong>Automated tests</strong>：无URL intent、landing/list/detail、multiple exact titles、locationignored反例、fragment routing、下线/恶意redirect、sameID跨tenant、protected aliases；真实公开只读contract smoke，结果保存结构摘要无私人数据。</p>
<p><strong>Acceptance gate</strong>：B类功能在合成/公开支持路径成立；公司+岗位指令无需用户粘精确URL；唯一性证据不足时不写；有明确平台支持矩阵和后续认证任务类型。</p>
<p><strong>Stop condition</strong>：不能确定唯一岗位/官方归属时返回候选或unsupported；不得“帮用户改投另一个”。网站登录/验证码不是本轮必须让owner做的测试。</p>
<p><strong>Owner interaction</strong>：NO；在最终真实验收选择实际愿意投的岗位时才要人作现实决策。</p>
<p><strong>Rollback</strong>：保留原task identity和source chain，resolver version可回退；禁止将已核验task重新解释成不同岗位。</p>
<h2 id="05_rounds-jcr-04--durable-applicant-facts--structured-evidence">JCR-04 — Durable Applicant Facts &amp; Structured Evidence</h2>
<p><strong>Objective</strong>：consumer端回答真正可恢复，已知事实不重复询问；项目/科研和来源成为明确、可追溯的记录。</p>
<p><strong>Files/surfaces</strong>：<code>profile.py</code>、<code>evidence.py</code>、<code>resolver.py</code>、<code>worker.py</code> answers、旧 app_cli continuation；新增 <code>facts/</code>、私有 answer store、profile scope UI、结构化 fixtures。</p>
<p><strong>Implementation scope</strong>：</p>
<ul>
<li>task-scoped答案持久化；明确可复用确认；policy/一次法律声明分离；API/OTP key继续不进入事实库。</li>
<li>atomic versioned writes、evidence refs、冲突与有效期；导入旧 canonical/profile overrides/任务答案策略。</li>
<li>stable IDs 的education/projects/research；有依据的排除/不适用，保留原证据。</li>
<li>区分未知事实、映射失败、缺权限、材料解析失败；批量问题带本地上下文和可选记忆scope。</li>
<li>local rich view与脱敏export分开；避免将用户事实输入经原rawchat发给模型。</li>
</ul>
<p><strong>Automated tests</strong>：既有源/用户覆盖优先；重建/重启/更新后不重问；一次答案不跨站复用；冲突不覆盖；bad JSON/write crash原子性；项目标题重叠、日期精度、简历解析误分类；privacycanary。</p>
<p><strong>Acceptance gate</strong>：E类自动测试全部通过；明确保存的事实重启后可用；相同有效scope重复提问0；项目/研究不静默丢失；一处权威事实存储，旧入口也通过它写入。</p>
<p><strong>Stop condition</strong>：证据冲突或缺客观值时保留未知，不填虚构值；真实MAX/简历不是本轮基础测试材料。</p>
<p><strong>Owner interaction</strong>：NO。最终onboarding核对现有私人资料和需要授权的复用政策，不每轮问资料。</p>
<p><strong>Rollback</strong>：迁移前快照与schema版本；旧字段兼容读取；保留所有新确认journal，不以旧JSON覆盖丢失确认。</p>
<h2 id="05_rounds-jcr-05--auth--otp-lifecycle">JCR-05 — Auth &amp; OTP Lifecycle</h2>
<p><strong>Objective</strong>：认证成为端到端子系统；短信等待、模式选择、人工挑战、迟到码和恢复都可解释、可续跑。</p>
<p><strong>Files/surfaces</strong>：adapter中的auth helpers、<code>application.py</code> auth orchestration、<code>autonomy/otp.py</code>、<code>otp/bridge.py</code>；新增 <code>auth/</code>和transport contracts、handoff UI、fake SMS/relay sources。</p>
<p><strong>Implementation scope</strong>：AuthAttempt持久元数据、短期内存code；active-mode/uniquecontrol证据；受保护初始send与授权resend；localpush/MacMessages/已启用relay解耦；异步等待不占worker永久锁；完成后return-target验证。</p>
<p>保留全部现有SMS严格边界和QR营销误报修复；把密码/CAPTCHA/人脸/设备挑战交本人。不要把“让owner手动验证码输入”设计成每次都必须发生；只在transport失败/安全验证不可自动完成时调用人。</p>
<p><strong>Automated tests</strong>：15个C类场景的statefulauthfixtures；跨taskcode、authattempt切换、发送后crash/迟到；同屏多mode/分段OTP支持声明；consent rerender/marketing不勾/无auto resend；secret全出口扫描。</p>
<p><strong>Acceptance gate</strong>：C类合成结果通过；普通可自动SMS登陆完整回原岗位；unknown send不重复发；自动code不落盘/不进模型；humanhandoff明确且自动重检；现有auth negative tests全部保留或等价增强。</p>
<p><strong>Stop condition</strong>：真实安全挑战/密码/新SMS费用或未授权relay，不自动操作；无法定位验证码所属attempt则等待，不能猜。</p>
<p><strong>Owner interaction</strong>：NO；真实手机桥与权限最终集中验证。</p>
<p><strong>Rollback</strong>：authattempt版本可识别；中断清除code不复用，普通task/facts保留；回退不自动发新验证码。</p>
<h2 id="05_rounds-jcr-06--structured-forms--reliable-draft-execution">JCR-06 — Structured Forms &amp; Reliable Draft Execution</h2>
<p><strong>Objective</strong>：从“原生控件列表填充”升级为能正确处理常见招聘表单、重复经历、附件和保存反馈的执行闭环。</p>
<p><strong>Files/surfaces</strong>：<code>adapters/generic_web.py</code>、<code>application.py</code>、<code>resolver.py</code>、site adapters；新增 <code>forms/</code> drivers、结构图、attachments/save observers、realistic fixtures。</p>
<p><strong>Implementation scope</strong>：FormObservation与FillPlan分离；sections/rows/fields/dependencies；scoped locator、动态重绘、native/ARIA及已冻结平台的组件drivers；typed salary/location/date；上传完成证据；parser修复；多页完整性/validation/autosave；恢复不新增重复row/附件。</p>
<p>已有GenericWebAdapter保留为可组合默认driver，不继续把所有站点认证/字段/导航写入一个大文件。复用之前已验证的站点能力；不在本轮引入任意模型执行JS。</p>
<p><strong>Automated tests</strong>：D类全部结构/组件fixture；实际React/Vue controlled页面与本地API；依赖字段/虚拟选项/iframe/open shadow支持范围；每个重要写入前后kill；结果用独立serverdraft比较。</p>
<p><strong>Acceptance gate</strong>：认证范围的完整合成任务所有必需记录/值/附件/保存结果正确；错select不只warning；隐藏要求/observation error不假READY；不存在虚构经历或重复副作用；unsupported被明确分类而非借问owner遮盖。</p>
<p><strong>Stop condition</strong>：新外站动作effect不明/无可靠driver、网站限制不能保持真实事实时安全停；不能以auto-fillJS强行越过页面逻辑。</p>
<p><strong>Owner interaction</strong>：NO；大量合成与已脱敏公开结构，私人站点最后验收。</p>
<p><strong>Rollback</strong>：driver按版本切回；新observations保留引用；未证明兼容的draft先只读恢复，不把旧locator重放。</p>
<h2 id="05_rounds-jcr-07--independent-final-review--human-submit-boundary">JCR-07 — Independent Final Review &amp; Human Submit Boundary</h2>
<p><strong>Objective</strong>：READY真正意味着“系统已完成可以自动验证的完整性审查，只剩本人意愿确认和最终点击”。</p>
<p><strong>Files/surfaces</strong>：<code>review.py</code>、<code>application.py</code> READY分支、task readiness、UI复核、protected_targets；新增certificate/observer/post-submit read-only contract。</p>
<p><strong>Implementation scope</strong>：独立当前draft观察与ReviewCertificate；明确项目/附件/字段/声明/日期/薪酬/目标/保存状态检查；证书依赖版本与失效机制；本地完整值/差异审阅；用户最终点击的人工ownership；提交后只读记录和保护。</p>
<p>旧“生成checklist”改为“逐项证据+需人做的最后决定”。无private值的diagnostics与本人复核分离。就绪状态可以只读重核，但不得通过新接口实现自动final-click。</p>
<p><strong>Automated tests</strong>：H类全部；与实际draft故意不一致的计划；隐含默认/未展开要求/附件失败/缺研究；certificate过期；Continue实际提交/Enter默认提交对抗fixture；fake human actor模拟最终点击，automation actor提交计数为0。</p>
<p><strong>Acceptance gate</strong>：所有false-ready反例被拒绝；expectedreview与actualdraft一致；无必须事项UNKNOWN/FAIL时才READY；任何更改正确使证书失效；用户点击后只读验证分级、保护identity生效。</p>
<p><strong>Stop condition</strong>：有任何无法证明的mandatory项时保持未就绪；不能用“反正user最后会看”豁免检查。</p>
<p><strong>Owner interaction</strong>：NO；实际最终点击只在JCR-09由owner自主决定。</p>
<p><strong>Rollback</strong>：旧证书作废，保留只读review；绝不回到“先READY后review”的批准路径。</p>
<h2 id="05_rounds-jcr-08--consumer-app--transactional-local-release">JCR-08 — Consumer App &amp; Transactional Local Release</h2>
<p><strong>Objective</strong>：让正常安装、运行、发现异常、更新与恢复都在app内完成；Desktop Commander不再是运维依赖。</p>
<p><strong>Files/surfaces</strong>：<code>consumer/cli/preflight/dashboard/supervisor/diagnostics/updater</code>、<code>ui/</code>、<code>app/</code>、<code>runtime/</code>、<code>release/</code>、CI/macOS与installer tests。</p>
<p><strong>Implementation scope</strong>：</p>
<ul>
<li>将已有任务读模型/初期bootstrap完成为薄Mac壳；singlewindow、持久context、taskcard操作、unknownfact/security/finalreview工作台；状态真实、通知去重和focus规则。</li>
<li>GUI onboarding、本地凭证/权限说明、可选login item；缺外部服务仍可看设置/诊断。</li>
<li>staged immutable versions、锁定依赖、candidate startup test、source/digest/certification、data migration/backup、atomic activation和known-good rollback；保留现有fences。</li>
<li>诊断可预览/复制 safe报告，包含真实loadedversion/原因/时间/恢复动作；update失败旧版仍可用。</li>
<li>完成旧单HTMLdashboard/原地git更新/重复事实入口的退役或只读兼容，明确禁止两套writer。</li>
</ul>
<p><strong>Automated tests</strong>：真实macOSapp/服务/Chromefixture交互，键盘/缩放/标签；expiredsession；缺profile/key/daemon端口占用；真实两版本依赖升级/失败/migration/kill/updater并发；A/U类门槛和privacy全部通过。</p>
<p><strong>Acceptance gate</strong>：app可独立打开和自诊断，不以业务服务健康为前提；没有routine Terminal/DC路径；更新不依赖开发工作树干净才能日用；candidate加载SHA正确；badrelease能回退；本人能在UI看见完整真实review而不是工程枚举。</p>
<p><strong>Stop condition</strong>：需要新付费承诺/发布签名账号/扩大系统权限且无授权时说明最小必需条件；不得悄悄采购或弱化鉴权。</p>
<p><strong>Owner interaction</strong>：NO（开发）。本机首次权限/安装确认保留到最后，不能以没有owner日常电脑权限为由不做hostedMac合成验证。</p>
<p><strong>Rollback</strong>：由独立bootstrap切回known-good；数据journal/schema兼容验证，不删除新确认事实；app入口保持可用。</p>
<h2 id="05_rounds-jcr-09--integrated-certification--final-personal-acceptance">JCR-09 — Integrated Certification &amp; Final Personal Acceptance</h2>
<p><strong>Objective</strong>：将上述子系统在真实产品入口串联认证；真人验收只验证成熟系统的最后一公里，而不是初次找基础bug。</p>
<p><strong>Files/surfaces</strong>：全生产入口/合同与matrix；<code>tests/e2e/</code>、fault/soak/macOS/release jobs、支持平台manifest、<code>07_FINAL_CERTIFICATION.md</code>、receipts和最终STATUS。修复仍按根因回到对应子系统，不临时旁路。</p>
<p><strong>Implementation scope</strong>：先自动执行100黄金任务、1000状态序列、关键fault重复、24hsoak、privacy和Mac安装/更新/恢复；公开只读再查站点drift；冻结exact candidate build及platformcontracts；安排最后1–3个owner时段，收集真实兼容性证据；最后短期正常日用。</p>
<p><strong>Automated tests</strong>：完整矩阵所有AUTO/AUTO_MAC/PUBLIC层；不是只跑某个focusedbrowser test。任何fix后跑受影响全链及历史安全门槛，必要时重签candidate。</p>
<p><strong>Acceptance gate</strong>：所有mandatory矩阵有对应证据；至少3种真实招聘机制、4–6合适真实任务覆盖已声明范围；安全/假READY事故0；真实权限/短信/最终手动边界证据明确；5个正常使用日不需工程救援。不同真实平台出现不可自动化安全挑战不算产品bug，但要正确handoff。</p>
<p><strong>Stop condition</strong>：自动基础门槛未过不得叫owner验收；真实证据缺失则UNVERIFIED而非COMPLETE；严重外部变化/真实账号安全/权限由owner判断。发现基础bug先fixture复现与自动修复，不无限请求重复真人测试。</p>
<p><strong>Owner interaction</strong>：YES，最多计划3个集中时段，内容见07。正常日用5日不是额外工程QA作业；不要求为达标额外申请不想投的岗位。</p>
<p><strong>Rollback</strong>：候选失败恢复前一已知安全版本和原任务；新版本未认证前不改consumer-ready标签。若没有已认证旧版，回到受限/工程Alpha并明确范围，不回报虚假完成。</p>
<h2 id="05_rounds-2-每轮必须提交的闭环证据">2. 每轮必须提交的闭环证据</h2>
<p>objective/result；base/head/merged SHA；实际修改文件；旧路径保留/退役清单；对应matrix IDs；actual entrypoints；测试命令与结果/CI链接/seed；最小失败与修复证据；迁移/回退；是否真实访问/写入外站；owner interaction次数及原因；剩余未验证事项；下一轮授权/未启动状态。</p>
<p>一轮可包含多次实现/诊断/修复和多个commit，不必切成用户每次都要回应的微轮次。执行模型可按本包直接继续本轮普通工程问题；遇到真正权限/付费/产品方向问题才停。不存在“commit了所以需要owner真人验收”的默认协议。</p>
</section>
