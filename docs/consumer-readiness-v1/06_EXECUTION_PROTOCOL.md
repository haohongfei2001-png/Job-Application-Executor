<!-- Canonical execution protocol. The 2026-09-23 continuous-execution amendment supersedes prior per-round stop/authorization semantics where they conflict. -->
<section id="06_execution_protocol" class="chapter"><h1 id="06_execution_protocol-注册与执行协议">注册与执行协议</h1>

<h2 id="06_execution_protocol-1-注册与当前授权">1. 注册与当前授权</h2>
<p><code>JAE-CONSUMER-READINESS-v1</code> 已注册。当前 owner 已对 JCR-01～JCR-09 的<strong>工程开发、合成/隔离测试、公开只读验证、PR/CI/合并与文档收口</strong>给予整包预授权；不再逐轮等待新的 owner “继续”消息。</p>
<p>该预授权<strong>不</strong>授权真实招聘账号副作用、真实申请写入、真实短信发送、未授权私人资料读取、新付费承诺、新 system-level 权限或最终 submit。最终 submit 永远由用户本人完成。</p>

<h2 id="06_execution_protocol-2-产品权威">2. 产品权威</h2>
<p>当前用户明确目标/安全边界 &gt; 本包已批准 scope/architecture/invariants &gt; 本轮 contract &gt; implementation convenience。历史文档中与当前 scope 或本连续执行协议冲突的产品合同显式视为 superseded，不删除历史。已完成的正确安全工作不可退回。</p>

<h2 id="06_execution_protocol-3-连续无人执行">3. 连续无人执行</h2>
<p>执行模型的默认职责是持续推进，而不是等待 owner 管理开发。每完成一个 coding/test/CI/review/merge 阶段都重新读取 remote main、open PR、active writer、CI、STATUS、当前 round 与 deferred ledger，并继续下一项可执行工作。</p>
<p><strong>核心规则：</strong><code>block the unsafe action, not the development package</code>。任何单点 blocker 默认只阻塞它直接依赖的动作或验收证据，不阻塞其他工程工作。</p>
<p>CI 等待、review 等待、网络瞬断、普通实现争议、测试失败、locator drift、兼容性 bug、merge conflict、依赖问题、重构、fixture 修复、需要多次 commit 或某轮完成，都不是 package-level 停止理由。能够自行诊断和修复的必须自行处理。</p>

<h2 id="06_execution_protocol-4-每轮开始-read_first">4. 每轮开始 READ_FIRST</h2>
<p>读取当前 main/branch/HEAD、open PR/active writer、exact-head CI、README/STATUS、当前 round contract、对应 acceptance IDs、上一轮 receipt、架构不变量、<code>DEFERRED_FINAL_GATES.md</code> 与受影响生产路径。若已有正确实现或现有 PR，接续它，不创建平行 writer。</p>
<p>轮次仍作为审计和集成边界：应尽可能逐轮实现、验证、PR、合并、receipt 和 STATUS。但“上一轮存在不影响后续独立工作的 deferred 项”不再强制整个 package 空等。</p>

<h2 id="06_execution_protocol-5-工程动作授权">5. 工程动作授权</h2>
<p>可自行定位普通 bug、选择等价模块结构、维护 tests/fixtures、在隔离环境跑浏览器/进程、使用 synthetic/fake services、公开只读站点、hosted macOS、修复 CI、处理 review、合并已满足 gate 的 PR，并继续下一轮。</p>
<p>不得自行改变目标产品、安全不变量、真实投递对象、隐私 scope、付费承诺或新增 system-level 权限。真实账户动作必须有准确对象与副作用授权；公开只读解析不等于发送 SMS/登录/上传/草稿写入授权。</p>

<h2 id="06_execution_protocol-6-deferred-final-gate">6. Deferred Final Gate 协议</h2>
<p>所有当前无法无人完成、或暂时不能安全证明的事项统一进入 <code>DEFERRED_FINAL_GATES.md</code>，不得散落为“等 owner 回复”状态。每条至少记录来源 round、类型、直接依赖、已完成替代证据、未完成证据、继续开发时采用的安全降级、最终收口条件。</p>
<p>允许的类型包括：</p>
<ul>
<li><code>FINAL_LIVE</code>：真实账号、真实岗位、真实短信、真实设备权限、真人 review/acceptance；</li>
<li><code>EXTERNAL</code>：第三方服务/平台暂不可用、权限、签名账号、付费前置；</li>
<li><code>ENGINEERING_DEBT</code>：暂时未能解决、但可以隔离且不妨碍其他独立工作的工程问题；</li>
<li><code>REAL_DATA_MIGRATION</code>：缺少真实旧数据安全证明，但 synthetic/compatibility work 可继续。</li>
</ul>
<p><strong>DEFERRED 绝不等于 PASS。</strong>相关 acceptance case 保持 NOT_RUN/UNVERIFIED/PENDING，不能伪造认证。安全或副作用不明的 runtime path 必须禁用、只读或明确 unsupported；随后继续其他开发。</p>
<p>如果一个 deferred 项是某个下游功能的严格依赖，则该依赖路径不得伪称成立；但仍应推进不依赖它的其他模块、fixtures、UI、release、诊断、测试和集成工作。</p>

<h2 id="06_execution_protocol-7-test-policy">7. Test policy</h2>
<p>保留原有效回归；历史 bug 加 direct production-path regression，不只复制源码片段。UI 测试从 actual consumer 入口走，oracle 读取真实 fixture 结果。所有 GitHub 证据默认 synthetic/copy-safe。</p>
<p>允许变更已被批准取代的旧产品合同，但必须说明旧断言问题、新契约和安全等价证明。禁止无理由降级、skip 历史安全测试、降低真实性要求、通过改 expected 迎合错误实现，或把 deferred 证据写成 PASS。</p>
<p>未来轮次尚未完成的 case 保持 NOT_RUN 并有责任轮次；测试 flaky 要定位，不以反复重跑挑成功。</p>

<h2 id="06_execution_protocol-8-round-advance">8. Round closure 与继续推进</h2>
<p>一个 round 只有在 objective 实际成立、对应自动 gate 通过、历史不变量通过、migration/rollback 可重复、exact-head CI 与 main 集成证据成立、receipt/STATUS 远端回读后，才标记 <code>COMPLETE</code>。</p>
<p>若仅剩 FINAL_LIVE / EXTERNAL 等最终证据，或存在已安全隔离且不影响后续独立工作的 deferred 项，可以保持 <code>IN_PROGRESS_WITH_DEFERRED</code> / <code>ADVANCE_ALLOWED_WITH_DEFERRED</code>，并继续后续可执行工作；不得为了线性状态漂亮而让整个 package 停住。</p>
<p>若没有合并权限，保留 READY_TO_MERGE 并继续所有不依赖该 merge 的安全工作。若 CI 被额度/外部服务阻塞，记录 gate 未完成并继续其他工作；不得给 release 签证。</p>

<h2 id="06_execution_protocol-9-唯一package级停止点">9. 唯一 package-level 停止点</h2>
<p>JCR-01～08 不设 owner-wait 停止点。任何 per-round “Stop condition” 均解释为<strong>停止不安全动作、拒绝该路径、记录 deferred，并继续开发</strong>，不再解释为停止整个 Work execution。</p>
<p>JCR-09 先完成所有 AUTO / AUTO_MAC / PUBLIC / synthetic / fault / soak 等无需 owner 的工作，并清理全部可自动解决的 <code>ENGINEERING_DEBT</code>。只有当 dependency graph 中已经不存在任何剩余可无人执行的工作，而余项全部属于真实 owner/live/permission/paid/external convergence 时，才允许 package 停在 <code>REAL_ACCEPTANCE_PENDING</code> 或明确的 final convergence blocker。</p>
<p>因此，逐轮授权、真人资料、验证码、安全挑战、真实设备权限、付费、签名、24h soak、5-day normal-use observation 均不得在能够继续其他工程工作时造成空等。它们集中在 JCR-09 final convergence 中收口。</p>

<h2 id="06_execution_protocol-10-执行窗口最小指令">10. 给执行窗口的最小指令</h2>
<blockquote>
<p>连接 <code>haohongfei2001-png/Job-Application-Executor</code>，以 remote main、STATUS、当前 round contract 和 deferred ledger 为事实源。JCR-01～09 工程开发已整包预授权。持续完成实现、独立验证、PR、CI、合并、receipt 和 STATUS；普通问题自行修复。遇到真实账号/资料/权限/付费/安全挑战/外部副作用或暂不可证明的问题，不执行危险动作，把证据记入 <code>DEFERRED_FINAL_GATES.md</code>，采用 synthetic/isolated/public/hosted 替代并继续所有独立工作。不得把 deferred 写成 PASS。只有 JCR-09 已耗尽全部可无人执行工作后，才允许停在 final convergence。</p>
</blockquote>
</section>
