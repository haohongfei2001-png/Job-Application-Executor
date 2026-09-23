<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="06_execution_protocol" class="chapter"><h1 id="06_execution_protocol-注册与执行协议">注册与执行协议</h1>
<h2 id="06_execution_protocol-1-注册仅文档操作">1. 注册（仅文档操作）</h2>
<p>获得文档注册授权后，把本包放入 <code>docs/consumer-readiness-v1/</code>。先核验 remote main、open PR、README与近期代码变化，必要时更新本包的 audit delta；本包基线不是永久真相。不要改用户私人config/profile/runtime/浏览器。</p>
<p>注册提交只能包含审计/设计/合同/STATUS与合成证据，不包含个人截图、短信、简历或本机路径。初始状态为 REGISTERED / IMPLEMENTATION_NOT_STARTED。注册不代表 JCR-01 已获执行授权，不自动启动真实网站操作。</p>
<h2 id="06_execution_protocol-2-产品权威">2. 产品权威</h2>
<p>当前用户明确目标/安全边界 &gt; 本包已批准scope/architecture/invariants &gt; 本轮contract &gt; implementation convenience。历史文档中与当前scope冲突的产品合同需显式标为superseded，不删除历史。已完成的正确安全工作不可因为新计划而退回。</p>
<h2 id="06_execution_protocol-3-每轮开始-read_first">3. 每轮开始 READ_FIRST</h2>
<p>当前 main/branch/HEAD、open PR/activewriter、exact-head CI、注册目录README/STATUS、本轮合同、对应acceptance IDs、上一轮receipt、架构不变量、受影响生产路径。只读检查是否已有正确实现或别的writer，避免重复劳动和并行覆盖。</p>
<p>轮次执行以“独立验证的用户结果”为单位，不以文件数/commit数为单位。若授权连续执行多轮，仍逐轮合并、生成证据与停止条件检查，不因细小失败结束整个项目，也不越过真人/权限边界。</p>
<h2 id="06_execution_protocol-4-工程动作授权">4. 工程动作授权</h2>
<p>在被授权轮次内：可自行定位普通bug、选等价模块结构、维护tests/fixtures、在隔离环境跑浏览器/进程、修复CI、完善文档和PR。不得自行改目标产品、安全不变量、真实投递对象、隐私scope、付费承诺或新增system-level权限。</p>
<p>真实账户动作必须有准确对象与可见副作用授权。公开只读解析不是发送SMS/登录/上传/草稿写入授权。最终submit始终本人点击；任何 owner “继续开发”都不能覆盖此边界。</p>
<h2 id="06_execution_protocol-5-test-policy">5. Test policy</h2>
<p>保留原有效回归；历史bug加directproductionpath regression，不只复制源码片段。UI测试从actualconsumer入口走，oracle读真实fixture结果。所有GitHub证据默认synthetic/copy-safe。</p>
<p>允许变更已被批准取代的旧产品合同，例如“缺key禁止打开设置UI”“普通事实永不持久”。变更须说明旧断言问题、新契约和安全等价证明。禁止无理由降级、skip历史安全测试、降低真实性要求或通过改expected迎合错误实现。</p>
<p>未来轮次尚未完成的case必须留NOT_RUN并有责任轮次；只可宣称当前轮次退出条件通过，不能把新包完整矩阵称为全绿。测试flaky要定位，不以多次重跑挑成功掩盖。</p>
<h2 id="06_execution_protocol-6-completion-gate">6. Completion gate</h2>
<p>本轮完成 = objective实际成立 + 对应必需自动gate通过 + 历史不变量通过 + 可重复migration/rollback + 同一head CI证据 + remote main集成核验 + receipt/STATUS远端回读。</p>
<p>若没有合并权限，状态只能 READY_TO_MERGE，不编造已合并。若CI被额度/外部服务阻塞，记录代码已准备和gate未完成，不把发布阻塞混作实现失败；可以按已批准依赖继续不依赖该gate的设计/测试工作，但不得给release签证。</p>
<h2 id="06_execution_protocol-7-什么情况下停止">7. 什么情况下停止</h2>
<p>必须停：真实岗位/事实/安全挑战/新法律承诺需要人；权限或付费承诺未授权；另一个writer正在修改同一表面；缺乏旧真实数据迁移安全证明；外部副作用不明；本轮已满足出口且下一轮未授权。</p>
<p>不应停：普通测试失败、locator过期、类型错误、dependency compatibility可在已有权限内修复、需要多次commit或要再次读CI。先bounded diagnosis并修复，再报告实际原因；不把“怎么停了”变成默认恢复协议。</p>
<h2 id="06_execution_protocol-8-给执行窗口的最小指令模板尚未执行">8. 给执行窗口的最小指令（模板，尚未执行）</h2>
<blockquote>
<p>连接 <code>haohongfei2001-png/Job-Application-Executor</code>，重核验 remote main、open PR、当前writer及CI，读取 <code>docs/consumer-readiness-v1/</code> 的README、STATUS、架构/不变量、JCR-01合同和验收矩阵。本次授权只执行JCR-01，沿生产路径完成实现、独立自动验证、必要修复、PR与合入后核验；普通工程问题自行处理。不得读取私人申请资料、操作真实招聘账号/短信、最终提交、新增付费或扩大权限。不得把owner当逐轮测试员，不得弱化安全/真实性测试。退出条件未成立不宣称完成；成立后更新receipt/STATUS并停在下一轮未授权。</p>
</blockquote>
<p>本模板假定包已注册且授权执行JCR-01；当前审计交付并没有执行它。用户可选自己指定的 GPT-5.6 Sol High/Extra High 环境；本包不依赖某一模型名称的可用性或额度承诺。</p>
</section>
