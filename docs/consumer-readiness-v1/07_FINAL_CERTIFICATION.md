<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="07_final_certification" class="chapter"><h1 id="07_final_certification-最终认证与真人验收计划">最终认证与真人验收计划</h1>
<h2 id="07_final_certification-1-发布标签">1. 发布标签</h2>
<p>DESIGN_ONLY → IMPLEMENTING → AUTOMATED_CANDIDATE → REAL_ACCEPTANCE_PENDING → PERSONAL_RC → PERSONAL_CONSUMER_READY。</p>
<p>当前 certification 仍为 DESIGN_ONLY / NOT_CERTIFIED，但工程执行已整包预授权。main CI 通过、docs 齐全或 9 轮代码合并都不自动进入 consumer-ready；对外/对自己都要区分工程完成、自动认证、真人认证和最终日用证据。</p>
<h2 id="07_final_certification-1a-final-convergence">1A. Final convergence 是唯一集中等待阶段</h2>
<p>JCR-01～08 以及 JCR-09 的自动部分不因真人、权限、付费、真实账号、真实短信、真实设备或外部服务证据缺失而等待。所有这类事项进入 <code>DEFERRED_FINAL_GATES.md</code>，相关 case 保持 UNVERIFIED/NOT_RUN，但其他开发继续。</p>
<p>进入 final convergence 前必须先耗尽所有可自动工作：包括 production implementation、synthetic/isolated E2E、AUTO/AUTO_MAC/PUBLIC、migration/rollback、fault injection、release/update/recovery、公开 drift、privacy/no-submit/protected invariants，以及可自动解决的全部 <code>ENGINEERING_DEBT</code>。只有剩余集合全部是不可由无人工程合法完成的 owner/live/external evidence 时，才进入 <code>REAL_ACCEPTANCE_PENDING</code>。</p>
<p>24h soak 和 5-day normal-use observation 都是最终认证门槛，不是中途开发停机点。候选满足条件后尽早启动 soak，并在其运行期间继续其他独立工作；5-day observation 只在 PERSONAL_RC 之后按真实日用自然发生。</p>
<p><strong>Cadence note：</strong>100 golden、1000 state sequences、fault 重复、24h soak、完整 Mac/release 矩阵是最终自动认证集合，不再作为 JCR-06～08 每个开发增量的重复门槛。它们仍必须在 JCR-09 final convergence 完成，不能因为前期少跑而从最终证书中删除。</p>
<h2 id="07_final_certification-2-最后叫-owner-前必须完成">2. 最后叫 owner 前必须完成</h2>
<p>所有核心AUTO矩阵通过；100完整黄金任务由独立oracle验证；1000状态序列和关键fault重复；Macapp启动、session、更新/回退真实进程认证；24hsoak；privacy/submit/protected invariants通过；公开站点drift已查；exact candidate SHA/builddigest锁定；具体3种平台机制与真实目标候选范围准备好；脱敏诊断/安全停止/回退已经可用。</p>
<p>严禁先让owner投一个岗位看看，再以“真实反馈”替代这些门槛。可用的公共网页、旧已脱敏案例、合成framework fixtures应先用尽。</p>
<h2 id="07_final_certification-3-计划中的13个集中时段">3. 计划中的1–3个集中时段</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>时段</th>
<th>owner真正需要做什么</th>
<th>开发/产品自动负责什么</th>
<th>出口</th>
</tr>
</thead>
<tbody>
<tr>
<td>1：本机启用与首个平台</td>
<td>从app完成首次权限/凭证/私人资料核对；选择一个确实要投的岗位；必要SMS或安全动作；查看最终review</td>
<td>导入校验、服务和专用浏览器自启、实际模型契约、普通OTP接收、表单与review、真实证据私有存储</td>
<td>不需要Terminal/DC；一个完整realjourney；不能自动处理的挑战说明准确</td>
</tr>
<tr>
<td>2：跨平台与日常流</td>
<td>使用其他认证机制的3–5个合适任务；在同名/新事实/声明处作真实决定；自主选择是否最终提交</td>
<td>公司+role/landing/list/search、已有登录/SMS、dynamicrepeat/附件/未知事实记忆、A/B任务恢复</td>
<td>至少3种机制、总计4–6任务有证据；无基础填表遗漏、错目标、重复问已知事实</td>
</tr>
<tr>
<td>3：保留的恢复/更新或一次复验</td>
<td>在合适非敏感时机同意一次sleep/reboot/更新演示；或核验上一时段发现的真实站点特例</td>
<td>自动恢复原任务与事实、证书重新核验、失败回滚、把特例转成回归</td>
<td>无工程救援；剩余真实边界闭环</td>
</tr>
</tbody>
</table></div>
<p>这些是合并的验收时段，不是保证网站永远只让用户验证三次。不可自动化的挑战将来仍可能出现。实际时长取决于站点和用户目标，不为了压缩时长跳过必要review。</p>
<p>没有真实愿意投的合适岗位时不虚假申请、不占用志愿、不创建不需要的账号。最终提交绝不为测试强迫用户；用户未选择实际提交时，post-submit真实观测保持未验证，并在证书中明确范围，不能伪造服务器回执。</p>
<h2 id="07_final_certification-4-真实异常处理">4. 真实异常处理</h2>
<p>先判断是平台安全挑战、缺真实事实、外部站点变更，还是基础产品 bug。前三者停止对应真实动作并进入 deferred ledger，不阻塞其他工程；基础 bug 立即停止受影响任务的自动写入，保留已完成工作，用脱敏最小证据做 fixture 并修复。修好并完整自动回归前不要求 owner 继续试。</p>
<p>计划保留一个复验时段而非假装一定一次过。若超过三个时段仍需owner反复找基础bug，说明前置测试/架构门槛失败，应重新进入工程闭环，不再把产品称为“即将完成”。这不是机械限制安全验证码次数。</p>
<h2 id="07_final_certification-5-短期真实日用">5. 短期真实日用</h2>
<p>realacceptance后形成PERSONAL_RC，随后5个实际使用日按本人正常投递需求使用，不安排额外工程QA。任务不必每天提交，但有正常打开/暂停/恢复/资料重用和队列使用证据。无需Terminal、DesktopCommander或临时开发补丁才可完成已认证范围任务；无错目标/虚构事实/秘密外发/假READY；外部CAPTCHA和新决定按设计处理。</p>
<p>若期间需要工程救援，记录根因、修复与回归，再对受影响范围重新验证。5日观察不是99.9%可用性统计证明，只是个人长期使用前最低现实检验。未完成观察时保持PERSONAL_RC。</p>
<h2 id="07_final_certification-6-最终证书必须包含">6. 最终证书必须包含</h2>
<div class="table-wrap"><table>
<thead>
<tr>
<th>项目</th>
<th>必需内容</th>
</tr>
</thead>
<tbody>
<tr>
<td>构建</td>
<td>remote main commit、artifact digest、依赖锁版本、Macapp/bootstrap/worker版本</td>
</tr>
<tr>
<td>环境</td>
<td>macOS/架构、Chrome版本、专用profile来源与所有权证明、外部provider contract版本</td>
</tr>
<tr>
<td>支持矩阵</td>
<td>三种实际机制、公司/ATS/查询/认证/表单特性、限制；证据不含秘密</td>
</tr>
<tr>
<td>自动证据</td>
<td>对应run/seed、case expected/actual、fault ledger、privacy/no-submit counts、migration/rollback</td>
</tr>
<tr>
<td>真实证据</td>
<td>验收时段、真实任务的脱敏身份和结果、需要人工的类型、未验证事项</td>
</tr>
<tr>
<td>完整性</td>
<td>所有mandatory case状态，不适用的证据，未知不作PASS</td>
</tr>
<tr>
<td>隐私/安全</td>
<td>数据外发范围、权限、无法防护的威胁、无越界证据；非仅布尔自证</td>
</tr>
<tr>
<td>更新/运维</td>
<td>用户入口可自诊断/回退；DC/Terminal非routine dependency</td>
</tr>
<tr>
<td>决议</td>
<td>consumer-ready / limited RC / blocked；决定理由、剩余风险、复验触发条件</td>
</tr>
</tbody>
</table></div>
<p>发布后浏览器/站点/模型合同有重大变化，不自动继承旧认证。运行中发现关键身份/表单合同漂移应降级为安全等待/不支持，保留其他认证范围；不要静默相信过时selector。</p>
<h2 id="07_final_certification-7-到那时实际日常使用">7. 到那时实际日常使用</h2>
<p>打开“AI投递经理”，说“投递OPPO AI产品经理”或贴URL。系统先证明目标，显示具体岗位；唯一匹配可以继续，有真实歧义才让用户选。系统自行处理已有登录/可支持的SMS、资料、附件与表单，并给出实际完成/等待结果。</p>
<p>用户只在新客观事实、主观选择、未授权新声明、无法自动化安全挑战，以及最终意愿/点击时出现。普通已确认资料不重问，正常重启/断网/更新不变成技术任务。最终review展示具体值、附件、项目覆盖和必要例外，用户点击真实网站最终按钮，系统只读记录结果。</p>
<p>不能承诺覆盖全互联网、消除所有安全验证、睡眠/关机中继续执行，或取消本人责任。可以合理承诺的是：<strong>在已认证支持范围内，不需要本人承担开发、诊断和重复资料工作；系统不确定时说清楚、不造假、不越界。</strong></p>
</section>
