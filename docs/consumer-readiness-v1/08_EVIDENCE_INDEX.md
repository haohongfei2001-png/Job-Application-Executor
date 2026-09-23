<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="08_evidence_index" class="chapter"><h1 id="08_evidence_index-证据索引与审计边界">证据索引与审计边界</h1>
<p>本包使用 E00–E25 引用对应固定版本实现、测试或历史说明。代码证据均绑定 <code>72dd892c144699f8a9e9988265bfb20f50b3db4e</code>，不能因未来 main 改变自动转为新版本证据。</p>
<h2 id="08_evidence_index-已执行未执行">已执行、未执行</h2>
<p>已执行：GitHub 授权只读读取；核验 exact-head CI 和实际日志；读取关键执行/安全/恢复/UI/更新实现及测试；在本工作容器运行 3 个有合成替身的源码片段探针。</p>
<p>未执行：完整仓库 pytest 重跑；用户 Mac 进程/浏览器检查；真实短信或登录；真站填表/附件上传/最终点击；实际 crash/sleep/reboot 注入；用户私人资料内容审核；任何 GitHub 写入或代码部署。下文故障表为代码级路径推演，不冒充上述未执行实验。</p>
<p>审计是按用户结果覆盖面的产品/可靠性评估，不是对每一行或所有可能网站行为的数学证明。长文件的阅读范围在各条明确；重要结论由直接实现和测试分支支持，而非仅由文件名推断。</p>
<h2 id="08_evidence_index-e00">E00</h2>
<h3 id="08_evidence_index-远端基线提交开放-prci">远端基线、提交、开放 PR、CI</h3>
<p>开始和收口阶段重新核验 main；SHA 未变。开放 PR 查询返回空数组。实际读取 Actions job 日志：240 passed in 56.33s；此数字是远端 CI，不是本次容器重跑。</p>
<ul>
<li><a href="https://api.github.com/repos/haohongfei2001-png/Job-Application-Executor/branches/main" rel="noopener noreferrer">main</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/commit/72dd892c144699f8a9e9988265bfb20f50b3db4e" rel="noopener noreferrer">72dd892c144699f8a9e9988265bfb20f50b3db4e</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35848476087" rel="noopener noreferrer">35848476087</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35848476087/job/107140245000" rel="noopener noreferrer">107140245000</a></li>
</ul>
<h2 id="08_evidence_index-e01">E01</h2>
<h3 id="08_evidence_index-readme-与产品边界">README 与产品边界</h3>
<p>读取 README 与完整 review playbook。后者要求审查实际表单/服务器草稿、项目清单及提交后只读验证；不能把文档要求当作运行时实现。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/README.md" rel="noopener noreferrer">README.md</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/APPLICATION_REVIEW_PLAYBOOK.md" rel="noopener noreferrer">docs/APPLICATION_REVIEW_PLAYBOOK.md</a></li>
</ul>
<h2 id="08_evidence_index-e02">E02</h2>
<h3 id="08_evidence_index-消费端意图与控制">消费端意图与控制</h3>
<p>读取 manager 核心路径与后半执行逻辑、完整设计说明；CREATE_TASK 精确 URL 条件、单 NEEDS_USER_ACTION 快捷恢复、模型上下文/提议/策略拒绝。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/manager.py" rel="noopener noreferrer">executor/autonomy/manager.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/DEEPSEEK_APPLICATION_AGENT.md" rel="noopener noreferrer">docs/DEEPSEEK_APPLICATION_AGENT.md</a></li>
</ul>
<h2 id="08_evidence_index-e03">E03</h2>
<h3 id="08_evidence_index-执行器和-ready-转换">执行器和 READY 转换</h3>
<p>读取构造/运行/认证/填表及末段 READY 分支。READY 在 build_final_review 前设置；最终审查结果没有成为该转换的充分门槛。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/application.py" rel="noopener noreferrer">executor/application.py</a></li>
</ul>
<h2 id="08_evidence_index-e04">E04</h2>
<h3 id="08_evidence_index-最终-review-与项目覆盖">最终 review 与项目覆盖</h3>
<p>完整读取。coverage 基于 plan 字段和标题子串；review 构造输出不是对网站草稿的独立认证。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/review.py" rel="noopener noreferrer">executor/review.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_review_v1.py" rel="noopener noreferrer">tests/test_review_v1.py</a></li>
</ul>
<h2 id="08_evidence_index-e05">E05</h2>
<h3 id="08_evidence_index-通用浏览器适配器">通用浏览器适配器</h3>
<p>分段读取完整核心实现：原生控件快照、填充、验证、认证、导航、截图。对 select 不同值仅产生 warning；默认范围不等于全部动态控件。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/adapters/generic_web.py" rel="noopener noreferrer">executor/adapters/generic_web.py</a></li>
</ul>
<h2 id="08_evidence_index-e06">E06</h2>
<h3 id="08_evidence_index-浏览器会话与标签页">浏览器会话与标签页</h3>
<p>完整读取 browser.py；测试文件主要由目录和现有 CI 确认，未据文件名推断逐条覆盖。latest_page 使用全局最后标签；connect 按精确 URL 找页。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/browser.py" rel="noopener noreferrer">executor/browser.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_browser_runtime_v1.py" rel="noopener noreferrer">tests/test_browser_runtime_v1.py</a></li>
</ul>
<h2 id="08_evidence_index-e07">E07</h2>
<h3 id="08_evidence_index-队列持久化与恢复">队列持久化与恢复</h3>
<p>分段读取队列和完整说明：SQLite claim、lease、owner fencing、safe checkpoint、pause provenance 与预算。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/queue.py" rel="noopener noreferrer">executor/autonomy/queue.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/LOCAL_AUTONOMY.md" rel="noopener noreferrer">docs/LOCAL_AUTONOMY.md</a></li>
</ul>
<h2 id="08_evidence_index-e08">E08</h2>
<h3 id="08_evidence_index-worker--operational-audit">Worker / operational audit</h3>
<p>读取 Worker、运行/返回态/异常与 OperationalAudit：单 worker、内存答案、守卫、阻塞映射；普通答案持久化与消费者工作流断开。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/worker.py" rel="noopener noreferrer">executor/autonomy/worker.py</a></li>
</ul>
<h2 id="08_evidence_index-e09">E09</h2>
<h3 id="08_evidence_index-supervisor-安全与-ui-session">Supervisor 安全与 UI session</h3>
<p>分两段完整读取：Host/Origin/token、ticket/session、route、mutation lock；UI session 为一小时内存态，过期页面要求 CLI 重开。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/supervisor.py" rel="noopener noreferrer">executor/autonomy/supervisor.py</a></li>
</ul>
<h2 id="08_evidence_index-e10">E10</h2>
<h3 id="08_evidence_index-界面与卡片">界面与卡片</h3>
<p>读取 HTML/CSS/JS，结合用户提供截图。静态任务卡、非持久对话、固定 readiness 文案，没有完整本地最终复核工作台。截图不能证明本机加载 SHA。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/dashboard.py" rel="noopener noreferrer">executor/autonomy/dashboard.py</a></li>
</ul>
<h2 id="08_evidence_index-e11">E11</h2>
<h3 id="08_evidence_index-启动与-onboarding">启动与 onboarding</h3>
<p>完整读取。app 为引用 repo/.venv 的 launcher；预检失败不打开 UI；启动与业务执行准备度被耦合。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/consumer.py" rel="noopener noreferrer">executor/autonomy/consumer.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/cli.py" rel="noopener noreferrer">executor/autonomy/cli.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/preflight.py" rel="noopener noreferrer">executor/autonomy/preflight.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/CONSUMER_ENTRY.md" rel="noopener noreferrer">docs/CONSUMER_ENTRY.md</a></li>
</ul>
<h2 id="08_evidence_index-e12">E12</h2>
<h3 id="08_evidence_index-本地诊断">本地诊断</h3>
<p>读取完整。诊断为允许字段摘要；缺失故障时间线/归因；available 不是在线探测，版本为 checkout 而非加载进程证明。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/diagnostics.py" rel="noopener noreferrer">executor/autonomy/diagnostics.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/LOCAL_DIAGNOSTICS_UPDATER.md" rel="noopener noreferrer">docs/LOCAL_DIAGNOSTICS_UPDATER.md</a></li>
</ul>
<h2 id="08_evidence_index-e13">E13</h2>
<h3 id="08_evidence_index-更新器">更新器</h3>
<p>分段读取。保留锁、原子状态、ff-only、OTP 与 mutation fence；当前不是独立候选部署/依赖升级/自动回滚器。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/updater.py" rel="noopener noreferrer">executor/autonomy/updater.py</a></li>
</ul>
<h2 id="08_evidence_index-e14">E14</h2>
<h3 id="08_evidence_index-otp-broker-与-transport">OTP broker 与 transport</h3>
<p>完整读取。broker 内存 TTL/单次消费有保护；relay 的短时 request/claim/cancel 与 Mac Messages fallback 的依赖不构成持续登录闭环。未读取实际私密 relay 配置。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/autonomy/otp.py" rel="noopener noreferrer">executor/autonomy/otp.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/otp/bridge.py" rel="noopener noreferrer">executor/otp/bridge.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/LOCAL_AUTONOMY.md" rel="noopener noreferrer">docs/LOCAL_AUTONOMY.md</a></li>
</ul>
<h2 id="08_evidence_index-e15">E15</h2>
<h3 id="08_evidence_index-字段映射与事实获取">字段映射与事实获取</h3>
<p>读取规则/alias、mapper 请求、完整 FieldResolver 及 profile。模型输出受 allowed_keys 限制，但结构上下文/重复行不足，已有值并非真值证明。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/resolver.py" rel="noopener noreferrer">executor/resolver.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/profile.py" rel="noopener noreferrer">executor/profile.py</a></li>
</ul>
<h2 id="08_evidence_index-e16">E16</h2>
<h3 id="08_evidence_index-资料证据项目和用户覆盖">资料证据、项目和用户覆盖</h3>
<p>读取导入标签/项目解析与后半历史/override/写入部分；未对用户私人 MAX/简历内容逐项重新审计。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/evidence.py" rel="noopener noreferrer">executor/evidence.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/MIGRATION_AUDIT_2026-09-18.md" rel="noopener noreferrer">docs/MIGRATION_AUDIT_2026-09-18.md</a></li>
</ul>
<h2 id="08_evidence_index-e17">E17</h2>
<h3 id="08_evidence_index-岗位-resolver">岗位 resolver</h3>
<p>完整读取。现有公司 resolver 只覆盖 Schneider、OPPO；存在提前返回 exact title、位置过滤不足和 Manager 内重复落地逻辑。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/target_resolver.py" rel="noopener noreferrer">executor/target_resolver.py</a></li>
</ul>
<h2 id="08_evidence_index-e18">E18</h2>
<h3 id="08_evidence_index-按钮语义与旧规则">按钮语义与旧规则</h3>
<p>完整读取。按钮正则与新 resolver/adapter 各有职责和重复；不存在 submit 方法不代表所有 next/apply 控件都天然无提交副作用。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/executor/field_classifier.py" rel="noopener noreferrer">executor/field_classifier.py</a></li>
</ul>
<h2 id="08_evidence_index-e19">E19</h2>
<h3 id="08_evidence_index-隔离-browser-与-subprocess-e2e">隔离 browser 与 subprocess E2E</h3>
<p>分段读取完整测试核心及尾部：真实 Chromium、小型 HTML、独立 daemon、OTP HTTP 和 submit 计数；重启在 READY 后。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_autonomy_v1.py" rel="noopener noreferrer">tests/test_autonomy_v1.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_generic_browser_v1.py" rel="noopener noreferrer">tests/test_generic_browser_v1.py</a></li>
</ul>
<h2 id="08_evidence_index-e20">E20</h2>
<h3 id="08_evidence_index-consumer-测试">Consumer 测试</h3>
<p>完整读取 consumer_entry 测试，结合 CI 与预检实现；大量 launch 依赖替换，dashboard 以字符串断言为主。不能称为实际 Mac 应用验收。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_consumer_entry_v1.py" rel="noopener noreferrer">tests/test_consumer_entry_v1.py</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_live_preflight_v1.py" rel="noopener noreferrer">tests/test_live_preflight_v1.py</a></li>
</ul>
<h2 id="08_evidence_index-e21">E21</h2>
<h3 id="08_evidence_index-更新和诊断测试">更新和诊断测试</h3>
<p>读取主要段落 1–850；真实临时 Git/锁/HTTP 测试存在，完整更新成功路径替换 Git 与进程调用。未声称所有尾部逐行审计。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_local_operations_v1.py" rel="noopener noreferrer">tests/test_local_operations_v1.py</a></li>
</ul>
<h2 id="08_evidence_index-e22">E22</h2>
<h3 id="08_evidence_index-manager-policy-测试">Manager policy 测试</h3>
<p>阅读显式意图、exact URL、待答字段与 fake provider 等关键段落；覆盖策略合同，不等于真人自然语言和真实网站闭环。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/tests/test_manager_v1.py" rel="noopener noreferrer">tests/test_manager_v1.py</a></li>
</ul>
<h2 id="08_evidence_index-e23">E23</h2>
<h3 id="08_evidence_index-ci-定义">CI 定义</h3>
<p>读取 workflow；依赖入口由 workflow/目录确认。当前 Ubuntu/Python3.12/Chromium 单 job，未见消费级 macOS 发布/安装全链路认证。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/.github/workflows/application-executor-ci.yml" rel="noopener noreferrer">.github/workflows/application-executor-ci.yml</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/requirements.txt" rel="noopener noreferrer">requirements.txt</a></li>
</ul>
<h2 id="08_evidence_index-e24">E24</h2>
<h3 id="08_evidence_index-历史验收记录">历史验收记录</h3>
<p>完整读取。历史 116 测试通过和真实机空队列保护不能替代当前多平台产品认证。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/AUTONOMY_VALIDATION.md" rel="noopener noreferrer">docs/AUTONOMY_VALIDATION.md</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/blob/72dd892c144699f8a9e9988265bfb20f50b3db4e/docs/LIVE_E2E_ACCEPTANCE.md" rel="noopener noreferrer">docs/LIVE_E2E_ACCEPTANCE.md</a></li>
</ul>
<h2 id="08_evidence_index-e25">E25</h2>
<h3 id="08_evidence_index-近期-pr提交历史">近期 PR/提交历史</h3>
<p>阅读近期 8 个 PR 的状态/标题/描述和提交历史；并非逐个完整 diff 的二次审计。当前实现判断仍以 pinned main 为准。</p>
<ul>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/1" rel="noopener noreferrer">1</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/2" rel="noopener noreferrer">2</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/3" rel="noopener noreferrer">3</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/4" rel="noopener noreferrer">4</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/5" rel="noopener noreferrer">5</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/6" rel="noopener noreferrer">6</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/7" rel="noopener noreferrer">7</a></li>
<li><a href="https://github.com/haohongfei2001-png/Job-Application-Executor/pull/8" rel="noopener noreferrer">8</a></li>
</ul>
<h2 id="08_evidence_index-本次隔离片段实验-p01p03">本次隔离片段实验 P01–P03</h2>
<p><a href="evidence/source_excerpt_probes.py">evidence/source_excerpt_probes.py</a> 中只复制 <code>review._norm/_covered</code>、<code>GenericWebAdapter.validate</code> 与 <code>browser.latest_page</code> 的实际片段；用最小合成类型/页面替身驱动相关分支。结果位于 <a href="evidence/source_excerpt_probes.json">JSON</a>。</p>
<p>P01：两个明确不同而标题包含的项目被 <code>_covered</code> 判为已覆盖。P02：期望北京、实际上海的 select 结果仍 <code>ok=true</code>，只有 warning。P03：最后打开的无关标签被选中，而非传入的任务页。</p>
<p>这些反例证明局部判据不足；没有证明真实网站已经发生错填，也没有执行真实浏览器崩溃实验。注册后必须把反例转为直接 import 产品实现的集成/浏览器测试，不能永久以复制函数的测试替代。</p>
<h2 id="08_evidence_index-技术设计参考不是当前产品已具备的能力">技术设计参考（不是当前产品已具备的能力）</h2>
<ul>
<li><a href="https://developer.chrome.com/blog/remote-debugging-port" rel="noopener noreferrer">Chrome 官方：remote debugging 安全变更</a>：支持保留专用非默认 profile 的方向，不应让自动化接管日常默认 profile。</li>
<li><a href="https://playwright.dev/python/docs/locators" rel="noopener noreferrer">Playwright 官方：Locators</a>：可用 role/label/作用域定位与每次行动重新解析，减少全局 nth 依赖；不保证任意控件都可安全处理。</li>
<li><a href="https://playwright.dev/python/docs/trace-viewer" rel="noopener noreferrer">Playwright 官方：Trace viewer</a>：为合成环境保留 DOM/网络/截图调试证据；真实私人申请默认不上传原始 trace。</li>
<li><a href="https://developer.apple.com/documentation/webkit/wkwebview" rel="noopener noreferrer">Apple：WKWebView</a> 与 <a href="https://developer.apple.com/documentation/servicemanagement/smappservice" rel="noopener noreferrer">SMAppService</a>：薄壳/受控本地服务设计参考。具体安装、签名和权限行为必须在目标 macOS 认证；本审计未验证 Apple API 在用户电脑上的运行状态。</li>
</ul>
<h2 id="08_evidence_index-github-发布证据注意事项">GitHub 发布证据注意事项</h2>
<p>基线分支信息返回 <code>protected=false</code>。计划可以要求新的 required checks / release manifest，但不能假称本次已设置分支保护。任何额外付费、签名账号或安装权限应先报告实际必要性；个人本机 v1 不以购买企业级发布设施为前提。</p>
</section>
