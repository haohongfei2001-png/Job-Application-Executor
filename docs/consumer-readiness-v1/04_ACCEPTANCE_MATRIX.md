<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="04_acceptance_matrix" class="chapter"><h1 id="04_acceptance_matrix-最终-consumer-grade-acceptance-matrix">最终 Consumer-Grade Acceptance Matrix</h1>
<p>本矩阵是拟冻结的客观结果合同，不是已通过报告。基线审计不等于这份新矩阵认证。配套 <a href="acceptance-matrix.json">JSON</a> 适合逐轮关联用例和 receipts。</p>
<p>所有项目初始 <strong>NOT_RUN</strong>。<code>AUTO</code> 是合成/隔离；<code>PUBLIC</code> 是公开只读；<code>AUTO_MAC</code> 是真实 macOS 自动环境；<code>FINAL_LIVE</code> 是最后集中真人/真实站点证据，不是每行单独找 owner。</p>
<p>所有必选项必须 PASS。特定站点确实不存在相应组件，允许该“站点×场景”标 <code>NOT_APPLICABLE_WITH_EVIDENCE</code>；整个 v1 必需能力仍需在另一认证场景证明。单纯 unsupported、安全停下、测试没访问到，不能标 N/A 或自动完成 PASS。</p>
<p>FAIL 中的任一条件出现即失败；PASS 为该行所有相关条件的合取。检查安全与完成两条轴：正确拒绝未知副作用可以 safety PASS，但不把未完成申请算作 completion PASS。0 自动提交、0 错目标、0 伪造事实、0 秘密越界、0 假READY 为全局强制门槛。</p>
<h2 id="04_acceptance_matrix-a--启动与运行">A — 启动与运行</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>A-01 / JCR-08</td>
<td>首次安装/资料 onboarding<br/>AUTO_MAC + FINAL_LIVE</td>
<td>从 app 完成运行环境、资料选择/核对、凭证和权限说明；不用 Terminal；原资料不被覆盖</td>
<td>要求开发目录/.venv 手工修复；未配置也声称可执行</td>
</tr>
<tr>
<td>A-02 / JCR-08</td>
<td>日常重新打开<br/>AUTO_MAC + FINAL_LIVE</td>
<td>单应用窗口恢复任务及最近有效上下文；任务/事实不变</td>
<td>反复新开 manager 页、丢上下文或重复新建任务</td>
</tr>
<tr>
<td>A-03 / JCR-08</td>
<td>重启后打开<br/>AUTO_MAC + FINAL_LIVE</td>
<td>app 自行启动受控依赖；持久任务和确认事实恢复；旧 review 重新验证</td>
<td>要用户启动 daemon/CDP、把任务标丢失或直接信旧证书</td>
</tr>
<tr>
<td>A-04 / JCR-08</td>
<td>Chrome 未开<br/>AUTO_MAC + FINAL_LIVE</td>
<td>只启动/重用本应用专用 profile，验证归属，不改变日常 Chrome</td>
<td>误用默认 profile、占用用户标签或只给端口错误</td>
</tr>
<tr>
<td>A-05 / JCR-08</td>
<td>服务未开/崩溃<br/>AUTO_MAC + FINAL_LIVE</td>
<td>bootstrap 界面仍可用，自动启动健康服务且有版本证明</td>
<td>设置/诊断随服务一同消失</td>
</tr>
<tr>
<td>A-06 / JCR-08</td>
<td>模型不可用<br/>AUTO_MAC + FINAL_LIVE</td>
<td>明确显示原因/范围；本地 pause/cancel/status 可用，已有任务不丢</td>
<td>key 存在就显示模型正常；控制命令等模型超时</td>
</tr>
<tr>
<td>A-07 / JCR-08</td>
<td>profile 缺失/损坏/冲突<br/>AUTO_MAC + FINAL_LIVE</td>
<td>可在 app 修复；区分三种原因，未证明前不填关键事实</td>
<td>只弹通用失败并要求 ChatGPT 或终端分析</td>
</tr>
<tr>
<td>A-08 / JCR-08</td>
<td>OTP source 不可用<br/>AUTO_MAC + FINAL_LIVE</td>
<td>任务解释自动接收不可用并提供独立本地 OTP 输入；无模型外发</td>
<td>无限等待或把验证码输入普通模型聊天</td>
</tr>
<tr>
<td>A-09 / JCR-08</td>
<td>GitHub/更新网络失败<br/>AUTO_MAC + FINAL_LIVE</td>
<td>旧健康版照常可用，任务保存，显示重试建议和安全诊断</td>
<td>半升级不可启动或暗示任务未变化但无证据</td>
</tr>
<tr>
<td>A-10 / JCR-08</td>
<td>网络/VPN 分域故障<br/>AUTO_MAC + FINAL_LIVE</td>
<td>分别识别 ATS/模型/更新/短信服务，不连续消耗无意义重试</td>
<td>将全局故障误归用户事实缺失或永远转圈</td>
</tr>
<tr>
<td>A-11 / JCR-08</td>
<td>UI session 超时/服务重启<br/>AUTO_MAC + FINAL_LIVE</td>
<td>应用自行恢复可信 UI 通道/明确本地重开，不需 CLI，不放松鉴权</td>
<td>一小时后要求 CLI，或为恢复删除鉴权</td>
</tr>
<tr>
<td>A-12 / JCR-08</td>
<td>权限拒绝/磁盘不足<br/>AUTO_MAC + FINAL_LIVE</td>
<td>保存可用旧状态，精确解释权限/空间问题，重试有界</td>
<td>写半个 profile/队列/版本后报成功</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-b--发现和目标身份">B — 发现和目标身份</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>B-01 / JCR-03</td>
<td>精确职位 URL<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>核验职位/公司/地点/批次/tenant 后接受；不改变用户目标</td>
<td>凭 URL 外形或页面标题模糊匹配直接写</td>
</tr>
<tr>
<td>B-02 / JCR-03</td>
<td>公司+岗位自然语言<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>自行只读发现并形成唯一精确目标，适用门户不要求用户找 URL</td>
<td>入口因缺 URL 拒绝，或模型编造 URL</td>
</tr>
<tr>
<td>B-03 / JCR-03</td>
<td>招聘首页<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>正确区分 landing 与 form，通过官方路径定位目标</td>
<td>首页扫码宣传被误认为 auth，或填在站点搜索框</td>
</tr>
<tr>
<td>B-04 / JCR-03</td>
<td>岗位列表/站内搜索<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>应用约束进入筛选/分页，候选覆盖完整性有证据</td>
<td>见一个同名结果即称唯一</td>
</tr>
<tr>
<td>B-05 / JCR-03</td>
<td>同名多个岗位<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>给出区别项；用户选择前不新建可写申请</td>
<td>擅自选第一个/职位 ID 最小者/不同城市</td>
</tr>
<tr>
<td>B-06 / JCR-03</td>
<td>地点/批次/用工类型约束<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>精确保留当前指令约束，候选匹配同时满足</td>
<td>忽略 location/campaign 或拿社招替校招</td>
</tr>
<tr>
<td>B-07 / JCR-03</td>
<td>职位下线<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>明确 unavailable，保留任务上下文，不代投其他岗位</td>
<td>换相近岗位或无限重新打开</td>
</tr>
<tr>
<td>B-08 / JCR-03</td>
<td>第三方 ATS 跳转<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>官方来源链和 ATS tenant/job identity 被验证并绑定</td>
<td>同域/同标题就默认为同雇主或所有跨域都受信</td>
</tr>
<tr>
<td>B-09 / JCR-03</td>
<td>登录后 URL 改变<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>恢复到同一目标/草稿，不新建另一任务</td>
<td>停在个人中心、另一职位或新草稿并报成功</td>
</tr>
<tr>
<td>B-10 / JCR-03</td>
<td>SPA/hash/查询路由<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>按已支持 contract 保留必要路由并隔离秘密，观察导航结果</td>
<td>剪掉 job 路由导致错位，或记录 access token</td>
</tr>
<tr>
<td>B-11 / JCR-03</td>
<td>无可靠解析/未支持站点<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>声明能力范围，保留只读结果，只在确实需要时请用户给具体 URL</td>
<td>要求所有情况手工找 URL，或宣称通用后盲点</td>
</tr>
<tr>
<td>B-12 / JCR-03</td>
<td>已提交目标/URL别名<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>归一化同一 tenant/job/campaign 身份后拒绝写入</td>
<td>换链接/显示名称能绕过 protected target</td>
</tr>
<tr>
<td>B-13 / JCR-03</td>
<td>相同 job ID 跨 tenant<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>确认为不同身份，不错误去重，也不混用授权</td>
<td>只看 job ID 或 host 导致跨公司串任务</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-c--登录与otp">C — 登录与OTP</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>C-01 / JCR-05</td>
<td>已登录<br/>AUTO + FINAL_LIVE</td>
<td>验证账户/目标后不再发送 SMS</td>
<td>有可用 session 仍发码或使用错误账号</td>
</tr>
<tr>
<td>C-02 / JCR-05</td>
<td>已证明的 SMS 登录<br/>AUTO + FINAL_LIVE</td>
<td>本地取已确认 phone，正确模式/协议下发送一次并记录 outcome</td>
<td>填入页面外手机号或无授权发送</td>
</tr>
<tr>
<td>C-03 / JCR-05</td>
<td>普通 OTP 自动匹配<br/>AUTO + FINAL_LIVE</td>
<td>仅有效 task+attempt+origin+时间窗 code 被填入正确控件</td>
<td>唯一 code 但匹配到错误请求/任务</td>
</tr>
<tr>
<td>C-04 / JCR-05</td>
<td>迟到 OTP<br/>AUTO + FINAL_LIVE</td>
<td>旧 attempt code 不进入新请求，当前有效等待可自动接续</td>
<td>2 秒结束后无人处理或旧码被新会话使用</td>
</tr>
<tr>
<td>C-05 / JCR-05</td>
<td>过期/重复/多条 OTP<br/>AUTO + FINAL_LIVE</td>
<td>过期不填、重复不重用、歧义明确等待，不猜码</td>
<td>消费二次/选择最新看似正确码</td>
</tr>
<tr>
<td>C-06 / JCR-05</td>
<td>用户授权重发<br/>AUTO + FINAL_LIVE</td>
<td>明确一次授权+cooldown+发送结果记录；无自动循环</td>
<td>自动重发、重复短信请求或绕过限频</td>
</tr>
<tr>
<td>C-07 / JCR-05</td>
<td>send 后 crash/响应丢失<br/>AUTO + FINAL_LIVE</td>
<td>恢复先协调 request outcome；未知效果不再发</td>
<td>把未知当未发送，重新点击初始发送</td>
</tr>
<tr>
<td>C-08 / JCR-05</td>
<td>同屏多登录模式<br/>AUTO + FINAL_LIVE</td>
<td>根据 active proven SMS 模式执行；其他模式保持不动</td>
<td>扫码宣传触发错误 blocker 或对 inactive password 填写</td>
</tr>
<tr>
<td>C-09 / JCR-05</td>
<td>CAPTCHA/slider/image<br/>AUTO + FINAL_LIVE</td>
<td>零自动解决/绕过；正确窗口接管、完成后重检</td>
<td>把挑战验证码当普通 OTP 或等待文案不指出操作</td>
</tr>
<tr>
<td>C-10 / JCR-05</td>
<td>QR/face/hardware<br/>AUTO + FINAL_LIVE</td>
<td>本人在真实网站完成，自动写入暂停、后续身份重新验证</td>
<td>模拟/绕过或切到无关页面</td>
</tr>
<tr>
<td>C-11 / JCR-05</td>
<td>password<br/>AUTO + FINAL_LIVE</td>
<td>不索要给模型、不自动处理密码，指向真实登录窗口</td>
<td>密码进入 chat、日志、模型或队列</td>
</tr>
<tr>
<td>C-12 / JCR-05</td>
<td>登录过期/登录返回<br/>AUTO + FINAL_LIVE</td>
<td>保留 facts/draft，重认证后返回同一任务</td>
<td>重新提问已有事实、丢原岗位或继续旧证书</td>
</tr>
<tr>
<td>C-13 / JCR-05</td>
<td>OTP transport 权限/断线<br/>AUTO + FINAL_LIVE</td>
<td>本地 source 可独立工作或清楚降级；不会误报在线</td>
<td>Mac Messages 必须依赖坏 relay 或源不可达仍绿</td>
</tr>
<tr>
<td>C-14 / JCR-05</td>
<td>本地 OTP 输入<br/>AUTO + FINAL_LIVE</td>
<td>专用受保护通道，code 内存、TTL、单次消费、无普通聊天存档</td>
<td>为了方便把 code 发送 DeepSeek</td>
</tr>
<tr>
<td>C-15 / JCR-05</td>
<td>分段 OTP 控件<br/>AUTO + FINAL_LIVE</td>
<td>仅受认证 driver 确证的分段控件可填并验证；否则明确安全接管</td>
<td>凭多个数字 input 猜位置</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-d--表单">D — 表单</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>D-01 / JCR-06</td>
<td>native input / select<br/>AUTO + FINAL_LIVE</td>
<td>实际网站值与 canonical/批准的表示一致</td>
<td>select 错值只 warning，或 false/0 被当空</td>
</tr>
<tr>
<td>D-02 / JCR-06</td>
<td>React/Vue controlled rerender<br/>AUTO + FINAL_LIVE</td>
<td>框架状态和重新渲染后的控件均保留期望值</td>
<td>只验证 fill 调用完成</td>
</tr>
<tr>
<td>D-03 / JCR-06</td>
<td>动态字段<br/>AUTO + FINAL_LIVE</td>
<td>触发后新增要求进入观察/计划/验证，遗漏阻止 READY</td>
<td>旧快照不含新字段便忽略</td>
</tr>
<tr>
<td>D-04 / JCR-06</td>
<td>依赖省市/学校专业<br/>AUTO + FINAL_LIVE</td>
<td>父级修改后重新匹配子级，错城市/专业 fail</td>
<td>子级重置却继续用旧 plan</td>
</tr>
<tr>
<td>D-05 / JCR-06</td>
<td>education repeated rows<br/>AUTO + FINAL_LIVE</td>
<td>正确学校/学位/时间绑定正确记录且无重复</td>
<td>两行同 name 都写入首行</td>
</tr>
<tr>
<td>D-06 / JCR-06</td>
<td>work/project/research 分类<br/>AUTO + FINAL_LIVE</td>
<td>真实项目保持项目/研究，不凭 resume parser 变正式工作</td>
<td>补不存在实习，或漏掉研究</td>
</tr>
<tr>
<td>D-07 / JCR-06</td>
<td>项目结构化完整性<br/>AUTO + FINAL_LIVE</td>
<td>每个适用 canonical record included/excluded-with-reason/not-applicable-with-proof</td>
<td>标题包含算同项目或 silently omit</td>
</tr>
<tr>
<td>D-08 / JCR-06</td>
<td>新增/删除/重排经历恢复<br/>AUTO + FINAL_LIVE</td>
<td>恢复后 record identity 对齐，无额外重复行</td>
<td>重放 add 生成重复记录或删别人的行</td>
</tr>
<tr>
<td>D-09 / JCR-06</td>
<td>简历上传<br/>AUTO + FINAL_LIVE</td>
<td>正确版本/hash、文件类型和大小检查，服务端或可靠草稿回读证明上传完成</td>
<td>basename/input.value 即当附件成功</td>
</tr>
<tr>
<td>D-10 / JCR-06</td>
<td>照片/多个附件<br/>AUTO + FINAL_LIVE</td>
<td>照片与简历槽位不同、必要文件完整，失败能局部恢复</td>
<td>所有 file input 都上传 resume</td>
</tr>
<tr>
<td>D-11 / JCR-06</td>
<td>自定义 dropdown/虚拟列表<br/>AUTO + FINAL_LIVE</td>
<td>受支持 driver 按语义 exact choice 与读回验证</td>
<td>substring first 或滚动错位后无验证</td>
</tr>
<tr>
<td>D-12 / JCR-06</td>
<td>日期精度<br/>AUTO + FINAL_LIVE</td>
<td>保持已知年/月/日，不凭网站要求自动补真值</td>
<td>编一号/当月或把结束至今变虚假日期</td>
</tr>
<tr>
<td>D-13 / JCR-06</td>
<td>薪资单位/范围<br/>AUTO + FINAL_LIVE</td>
<td>币种、年/月、税前/后由已确认策略确定，换算可追溯</td>
<td>20万变20元或新岗位默用旧薪资承诺</td>
</tr>
<tr>
<td>D-14 / JCR-06</td>
<td>合规/签名/一次声明<br/>AUTO + FINAL_LIVE</td>
<td>scope/文本/批准条件吻合才执行；新承诺由用户决定</td>
<td>宽泛正则或已有默认勾选替本人决定</td>
</tr>
<tr>
<td>D-15 / JCR-06</td>
<td>主观问题<br/>AUTO + FINAL_LIVE</td>
<td>基于已确认事实生成/复用有来源的答案，按政策审阅</td>
<td>模型为了连贯编造经历或结果</td>
</tr>
<tr>
<td>D-16 / JCR-06</td>
<td>未知客观事实<br/>AUTO + FINAL_LIVE</td>
<td>检索本地证据仍缺才有上下文询问，批量回答且可选记忆scope</td>
<td>映射失败冒充事实缺失，反复问学校手机号</td>
</tr>
<tr>
<td>D-17 / JCR-06</td>
<td>required-but-hidden/折叠区<br/>AUTO + FINAL_LIVE</td>
<td>按契约展开/检查必须区段和站点反馈，未证实不 READY</td>
<td>只检查当前 visible native controls</td>
</tr>
<tr>
<td>D-18 / JCR-06</td>
<td>DOM observation failure<br/>AUTO + FINAL_LIVE</td>
<td>明确 observation error 并重试/阻塞，空数组不成为通过证据</td>
<td>evaluate exception 当空表单，在 final button 前 READY</td>
</tr>
<tr>
<td>D-19 / JCR-06</td>
<td>field/form/server validation<br/>AUTO + FINAL_LIVE</td>
<td>全部错误被定位和保留，修复后独立读回通过</td>
<td>只查 required，不查网站明确错误</td>
</tr>
<tr>
<td>D-20 / JCR-06</td>
<td>autosave<br/>AUTO + FINAL_LIVE</td>
<td>观察保存完成/重新进入后值仍正确，未知保存结果保留</td>
<td>固定 sleep 后声称已保存</td>
</tr>
<tr>
<td>D-21 / JCR-06</td>
<td>multi-page / returning<br/>AUTO + FINAL_LIVE</td>
<td>前页和末页跨步完整性、草稿 revision、附件持续有效</td>
<td>只核对当前页，前页误丢仍 READY</td>
</tr>
<tr>
<td>D-22 / JCR-06</td>
<td>unsupported component<br/>AUTO + FINAL_LIVE</td>
<td>明确能力限制并保持已完成部分，不向用户询问不存在的新事实</td>
<td>无穷重试或随机用 JS 写值绕过语义</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-e--事实与记忆">E — 事实与记忆</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>E-01 / JCR-04</td>
<td>已确认事实重用<br/>AUTO + FINAL_LIVE</td>
<td>相同有效 scope/version 无冲突时重复提问 0</td>
<td>重启/新任务后再问系统已保存的事实</td>
</tr>
<tr>
<td>E-02 / JCR-04</td>
<td>一次性回答<br/>AUTO + FINAL_LIVE</td>
<td>私有 application-scoped 持久化可恢复，不跨申请传播</td>
<td>重启丢掉或静默变全局事实</td>
</tr>
<tr>
<td>E-03 / JCR-04</td>
<td>可复用回答<br/>AUTO + FINAL_LIVE</td>
<td>用户明确批准后成为有来源/时间/版本的 canonical 覆盖</td>
<td>未经同意永久记住或旧文档覆盖新确认</td>
</tr>
<tr>
<td>E-04 / JCR-04</td>
<td>冲突历史<br/>AUTO + FINAL_LIVE</td>
<td>保留冲突/来源，明确选择依据，不用模型猜</td>
<td>随机取高confidence或网站默认覆盖真值</td>
</tr>
<tr>
<td>E-05 / JCR-04</td>
<td>site-specific representation<br/>AUTO + FINAL_LIVE</td>
<td>表示映射可逆/不造事实，原资料不被改写</td>
<td>网站选项限制反向污染 canonical truth</td>
</tr>
<tr>
<td>E-06 / JCR-04</td>
<td>简历/MAX 导入差异<br/>AUTO + FINAL_LIVE</td>
<td>结构/字段提取结果可核对，缺失与解析失败有区分</td>
<td>含特定分隔符才认识项目却报告完整</td>
</tr>
<tr>
<td>E-07 / JCR-04</td>
<td>科研/项目排除scope<br/>AUTO + FINAL_LIVE</td>
<td>单岗位排除理由保留，canonical inventory 不删</td>
<td>产品岗默认遗漏研究、本科项目排除影响其他申请</td>
</tr>
<tr>
<td>E-08 / JCR-04</td>
<td>atomic fact writes<br/>AUTO + FINAL_LIVE</td>
<td>故障后旧版或完整新版之一可读，version/conflict一致</td>
<td>半个 JSON、文件截断或确认事件丢失</td>
</tr>
<tr>
<td>E-09 / JCR-04</td>
<td>profile 更新影响 task<br/>AUTO + FINAL_LIVE</td>
<td>更新受影响字段和 review版本，不悄悄保持旧证书</td>
<td>事实改了但 READY 仍指向旧值</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-f--安全隐私">F — 安全隐私</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>F-01 / JCR-01</td>
<td>模型秘密外发<br/>AUTO</td>
<td>canary OTP/password/cookie/API key 不出现于任何 provider 请求</td>
<td>只凭 safe_task_view 名字或安全布尔自证</td>
</tr>
<tr>
<td>F-02 / JCR-01</td>
<td>DOM/聊天敏感边界<br/>AUTO</td>
<td>不可信 label/options/chat 经最小化；认证phone和秘密不外发</td>
<td>因为未发送 value 字段就忽略邻近 PII</td>
</tr>
<tr>
<td>F-03 / JCR-01</td>
<td>queue/log/export 秘密<br/>AUTO</td>
<td>扫描普通持久层/异常/剪贴板/日志无秘密；事实只在授权私有store</td>
<td>OTP/hash/password 留磁盘或私人事实进 public report</td>
</tr>
<tr>
<td>F-04 / JCR-01</td>
<td>HTTP 本地边界<br/>AUTO</td>
<td>Host/Origin/session/ticket/token、过期和重放安全回归通过</td>
<td>为解决 session expiry 开放未授权接口</td>
</tr>
<tr>
<td>F-05 / JCR-01</td>
<td>截图/trace/HAR<br/>AUTO</td>
<td>真站默认不导出；合成证据扫描后可分享</td>
<td>关闭文字日志却上传有 cookie/手机号截图</td>
</tr>
<tr>
<td>F-06 / JCR-01</td>
<td>Git/fixture 泄漏<br/>AUTO</td>
<td>私有目录禁止入库，所有发布/测试artifacts带脱敏manifest</td>
<td>真实简历/DOM/profile被测试便利性带入repo</td>
</tr>
<tr>
<td>F-07 / JCR-01</td>
<td>protected submitted targets<br/>AUTO</td>
<td>enqueuing/resume/retarget/执行前所有路径同一保护判据</td>
<td>只有其中一个入口检查保护</td>
</tr>
<tr>
<td>F-08 / JCR-01</td>
<td>恶意 DOM prompt injection<br/>AUTO</td>
<td>页面文本不能扩权、换目标、改事实或授权最终提交</td>
<td>模型照页面“忽略规则”执行</td>
</tr>
<tr>
<td>F-09 / JCR-01</td>
<td>专用 profile 权限/归属<br/>AUTO</td>
<td>loopback 连接验证自有session，不读默认cookies</td>
<td>任意监听相同端口服务被当成Chrome</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-g--恢复与并发">G — 恢复与并发</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>G-01 / JCR-02</td>
<td>browser crash 中途<br/>AUTO + FINAL_LIVE</td>
<td>重建受控会话并重新观察同一 draft，零重复副作用</td>
<td>只验证 CDP 再在线，未证明原任务</td>
</tr>
<tr>
<td>G-02 / JCR-02</td>
<td>daemon kill 中途<br/>AUTO + FINAL_LIVE</td>
<td>任务/普通事实恢复，旧所有者失权，OTP不复用</td>
<td>只在READY后重启验证数据库行还在</td>
</tr>
<tr>
<td>G-03 / JCR-02</td>
<td>sleep/wake<br/>AUTO + FINAL_LIVE</td>
<td>唤醒后 epoch/时间窗变化被处理，过期auth/review失效</td>
<td>休眠期间显示持续工作或直接重放</td>
</tr>
<tr>
<td>G-04 / JCR-02</td>
<td>reboot recovery<br/>AUTO + FINAL_LIVE</td>
<td>重新打开app无工程操作，任务/事实/保护记录完整</td>
<td>要求手工daemon启动、重新输入已知事实</td>
</tr>
<tr>
<td>G-05 / JCR-02</td>
<td>network loss / response lost<br/>AUTO + FINAL_LIVE</td>
<td>读回区分已保存/未保存/未知，commandid不重复副作用</td>
<td>错误提示一概“没有改变”或重复save/add</td>
</tr>
<tr>
<td>G-06 / JCR-02</td>
<td>tab closed/refreshed<br/>AUTO + FINAL_LIVE</td>
<td>恢复正确owner target/draft，不修改无关tab</td>
<td>global latest-page 接手其他窗口</td>
</tr>
<tr>
<td>G-07 / JCR-02</td>
<td>site timeout<br/>AUTO + FINAL_LIVE</td>
<td>有限重试/退避，实际进度保留，清楚原因</td>
<td>无限循环或将超时当作空表单通过</td>
</tr>
<tr>
<td>G-08 / JCR-02</td>
<td>DeepSeek timeout/stale response<br/>AUTO + FINAL_LIVE</td>
<td>本地控制及时，陈旧提议不改变已取消/已暂停任务</td>
<td>持有写锁直到模型返回再处理暂停</td>
</tr>
<tr>
<td>G-09 / JCR-02</td>
<td>retry exhausted<br/>AUTO + FINAL_LIVE</td>
<td>明确根因与可验证修复动作；新恢复尝试有预算与历史</td>
<td>建议用户清空任务/无限重试/重新提交</td>
</tr>
<tr>
<td>G-10 / JCR-02</td>
<td>partial form / upload / repeated rows<br/>AUTO + FINAL_LIVE</td>
<td>独立oracle确认恢复后一次正确结果，无漏项/重复项</td>
<td>队列checkpoint存在即声称完整恢复</td>
</tr>
<tr>
<td>G-11 / JCR-02</td>
<td>多任务/多窗口<br/>AUTO + FINAL_LIVE</td>
<td>严格task+tab+fact scope；并发命令有CAS/receipt，单writer</td>
<td>A事实或OTP落入B任务</td>
</tr>
<tr>
<td>G-12 / JCR-02</td>
<td>human handoff race<br/>AUTO + FINAL_LIVE</td>
<td>人工期间自动不写；结束先重新观察，取消有效</td>
<td>用户滑块或改字段时worker同时写</td>
</tr>
<tr>
<td>G-13 / JCR-02</td>
<td>错序 events / UI reconnect<br/>AUTO + FINAL_LIVE</td>
<td>同一权威revision读模型收敛，丢响应可查receipt</td>
<td>UI回滚状态、重复新建申请或变绿</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-h--最终复核">H — 最终复核</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>H-01 / JCR-07</td>
<td>完整 review certificate<br/>AUTO + FINAL_LIVE</td>
<td>独立实际观察生成，所有mandatory检查PASS才READY</td>
<td>READY先设置，review只是提醒用户查</td>
</tr>
<tr>
<td>H-02 / JCR-07</td>
<td>最终 company/job/account<br/>AUTO + FINAL_LIVE</td>
<td>证书与当前站点身份和草稿吻合，详细可见</td>
<td>仅exact_target_review_required=true</td>
</tr>
<tr>
<td>H-03 / JCR-07</td>
<td>必填/未知/冲突/默认<br/>AUTO + FINAL_LIVE</td>
<td>遗漏/未知/未证实默认/冲突任一存在不能READY</td>
<td>无error字段就PASS或默认是站点给的便信</td>
</tr>
<tr>
<td>H-04 / JCR-07</td>
<td>structured projects/research<br/>AUTO + FINAL_LIVE</td>
<td>稳定ID匹配当前行，覆盖/排除/不适用有证据</td>
<td>标题包含匹配或只读plan</td>
</tr>
<tr>
<td>H-05 / JCR-07</td>
<td>resume/attachments/date/salary<br/>AUTO + FINAL_LIVE</td>
<td>actual receipt与canonical及授权表示一致且可在本地看见</td>
<td>只显示文件名和一张checklist</td>
</tr>
<tr>
<td>H-06 / JCR-07</td>
<td>证书失效<br/>AUTO + FINAL_LIVE</td>
<td>编辑/换job/profile/update/sessionexpiry令旧review失效，只读重新核验</td>
<td>一到READY永久就绪不能检查</td>
</tr>
<tr>
<td>H-07 / JCR-07</td>
<td>真正手动最终点击<br/>AUTO + FINAL_LIVE</td>
<td>自动化没有最终动作，app只打开真实页面，由用户执行；有责任区分</td>
<td>用脚本代用户点或把“查看并提交”按钮代理提交</td>
</tr>
<tr>
<td>H-08 / JCR-07</td>
<td>用户提交后的只读观测<br/>AUTO + FINAL_LIVE</td>
<td>区分page signal/user-confirmed/server-verified；登记保护，零再次写入</td>
<td>未提交报告已投，或resume补填造成修改/重复投递</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-u--产品化与更新">U — 产品化与更新</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>U-01 / JCR-08</td>
<td>受控完整更新<br/>AUTO_MAC + FINAL_LIVE</td>
<td>绑定认证SHA/digest，隔离准备依赖与候选，健康后切换</td>
<td>直接把main pull到运行目录且不验依赖</td>
</tr>
<tr>
<td>U-02 / JCR-08</td>
<td>坏依赖/启动失败回退<br/>AUTO_MAC + FINAL_LIVE</td>
<td>旧版本继续可用；bootstrap可用，任务事实可读</td>
<td>需DC或Terminal才能再次打开</td>
</tr>
<tr>
<td>U-03 / JCR-08</td>
<td>migration 失败/中断<br/>AUTO_MAC + FINAL_LIVE</td>
<td>事务或备份恢复到可证明一致状态，无普通事实丢失</td>
<td>回滚旧快照静默删除更新后的确认事实</td>
</tr>
<tr>
<td>U-04 / JCR-08</td>
<td>paused/OTP/running 更新<br/>AUTO_MAC + FINAL_LIVE</td>
<td>遵守写入与OTP边界；任务schema/保护记录兼容</td>
<td>重启丢一次答案、码/请求串新attempt</td>
</tr>
<tr>
<td>U-05 / JCR-08</td>
<td>更新进程 kill / repeat-click<br/>AUTO_MAC + FINAL_LIVE</td>
<td>锁/原子状态/恢复fence正确，用户可无工程知识恢复</td>
<td>永远checking或多个updater竞争</td>
</tr>
<tr>
<td>U-06 / JCR-08</td>
<td>diagnostics 独立可用<br/>AUTO_MAC + FINAL_LIVE</td>
<td>服务死时也能导出safe原因、时间、真实loadedversion和恢复结果</td>
<td>只报告supervisor=true或checkout SHA</td>
</tr>
<tr>
<td>U-07 / JCR-08</td>
<td>应用状态与进度<br/>AUTO_MAC + FINAL_LIVE</td>
<td>健康/任务/等待/用户动作分开，不制造假进度</td>
<td>永久绿色就绪，任务卡无下一步</td>
</tr>
<tr>
<td>U-08 / JCR-08</td>
<td>任务详情与上下文<br/>AUTO_MAC + FINAL_LIVE</td>
<td>可选任务、看到需要回答什么、复核什么，重开不丢context</td>
<td>只读卡片+通用聊天框，让人猜“这个”</td>
</tr>
<tr>
<td>U-09 / JCR-08</td>
<td>通知/焦点/浏览器接管<br/>AUTO_MAC + FINAL_LIVE</td>
<td>正常后台不抢焦点；重要动作去重通知，打开正确站点并返回</td>
<td>每步弹窗或密码输入中突然换页</td>
</tr>
<tr>
<td>U-10 / JCR-08</td>
<td>运行时版本证明<br/>AUTO_MAC + FINAL_LIVE</td>
<td>服务自报构建标识与candidate一致，旧进程不能冒充新版</td>
<td>git HEAD改变就宣称更新成功</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-z--最终整体验收">Z — 最终整体验收</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>ID / 责任轮次</th>
<th>场景 / 证据方式</th>
<th>PASS</th>
<th>FAIL</th>
</tr>
</thead>
<tbody>
<tr>
<td>Z-01 / JCR-09</td>
<td>多平台真实认证<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>至少3实际机制支持矩阵有真站证据，至少4–6合适任务分布覆盖关键路径</td>
<td>三公司同模板充数或只有一个简单站点</td>
</tr>
<tr>
<td>Z-02 / JCR-09</td>
<td>100黄金端到端<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>100个冻结、多样化完整任务实际draft正确且安全问题0</td>
<td>只内部stage通过或失败用skip消失</td>
</tr>
<tr>
<td>Z-03 / JCR-09</td>
<td>state/recovery 强化<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>1000seed状态序列、关键故障10次/平台3次，失败历史完整</td>
<td>只一次成功记录或长期flaky靠重跑掩盖</td>
</tr>
<tr>
<td>Z-04 / JCR-09</td>
<td>24h合成soak<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>多任务、重连、更新、中断后无孤儿/持续资源增长/假完成</td>
<td>长跑卡住由人手工救回才继续</td>
</tr>
<tr>
<td>Z-05 / JCR-09</td>
<td>支持范围与公开drift<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>最新公开只读合同证据明确；外部不可达与实现失败分开记录</td>
<td>最后真实验收时才第一次访问公开首页</td>
</tr>
<tr>
<td>Z-06 / JCR-09</td>
<td>1–3集中owner验收<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>前置全自动通过，真人只作权限/真实决策/安全/最终确认</td>
<td>让owner每轮测，或基础bug反复“再试一次”</td>
</tr>
<tr>
<td>Z-07 / JCR-09</td>
<td>短期正常日用<br/>AUTO + PUBLIC + FINAL_LIVE</td>
<td>候选通过后5个实际使用日不需Terminal/DC/开发补丁才能完成受支持任务</td>
<td>日常还靠聊天控制电脑逐步修补</td>
</tr>
</tbody>
</table></div>
<h2 id="04_acceptance_matrix-收口记录">收口记录</h2>
<p>共 <strong>118 个结果合同</strong>。每个 receipt 至少含 case ID、实现 SHA/build digest、平台/站点合同版本、前提、expected/actual、evidence refs、run ID/seed、PASS/FAIL、缺陷记录与复验历史。不得仅把整份 pytest summary 粘贴到所有行。</p>
<p>真实验收中如证据不充分，保持 UNVERIFIED/NOT_RUN，不能把“没看见问题”当 PASS。未完成项要保留到最后证书；没有 full consumer-ready 就明确 release candidate/受限支持。</p>
</section>
