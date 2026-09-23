<!-- Pro audit text preserved; STATUS.json is authoritative for current execution state. -->
<section id="09_traceability" class="chapter"><h1 id="09_traceability-findings--design--acceptance-追踪表">Findings → Design → Acceptance 追踪表</h1>
<p>本表不是新缺陷计数的统计推断；它把已发现的系统性问题对应到修法与结果门槛。源码片段 P01–P03 的验证范围见证据文件。后续实现 receipt 必须记录具体case而非仅引用本表。</p>
<div class="table-wrap"><table>
<thead>
<tr>
<th>Finding</th>
<th>问题</th>
<th>证据</th>
<th>结构性修法</th>
<th>责任轮次</th>
<th>验收关联</th>
</tr>
</thead>
<tbody>
<tr>
<td>GAP01</td>
<td>用户必须提供exact URL</td>
<td>E02/E17</td>
<td>DiscoveryRequest→VerifiedJobTarget</td>
<td>JCR-03</td>
<td>B-01～B-13</td>
</tr>
<tr>
<td>GAP02</td>
<td>READY先于完整review</td>
<td>E03/E04</td>
<td>独立ReviewCertificate + invalidation</td>
<td>JCR-07</td>
<td>H-01～H-06</td>
</tr>
<tr>
<td>GAP03</td>
<td>select错值warning-only</td>
<td>E05/P02</td>
<td>typed normalization + actual readback</td>
<td>JCR-01/06</td>
<td>D-01/D-04/H-03</td>
</tr>
<tr>
<td>GAP04</td>
<td>项目substring误覆盖</td>
<td>E04/P01</td>
<td>stable record ID + row observation</td>
<td>JCR-01/04/07</td>
<td>D-07/E-07/H-04</td>
</tr>
<tr>
<td>GAP05</td>
<td>全局最后tab和弱归属</td>
<td>E06/P03</td>
<td>owned browser/tab/session epoch</td>
<td>JCR-02</td>
<td>B-09/G-01/G-06/G-11</td>
</tr>
<tr>
<td>GAP06</td>
<td>消费者普通答案memory-only</td>
<td>E08/E16</td>
<td>scoped private fact journal</td>
<td>JCR-04</td>
<td>E-01～E-04/E-08/E-09</td>
</tr>
<tr>
<td>GAP07</td>
<td>mode/late OTP/relay生命周期</td>
<td>E05/E14</td>
<td>AuthAttempt + independent transports</td>
<td>JCR-05</td>
<td>C-01～C-15</td>
</tr>
<tr>
<td>GAP08</td>
<td>恢复只证明queue不证明draft</td>
<td>E07/E08/E19</td>
<td>action outcome + actual draft reconciliation</td>
<td>JCR-02/06</td>
<td>G-01～G-10</td>
</tr>
<tr>
<td>GAP09</td>
<td>无限大native-field snapshot责任</td>
<td>E05/E15</td>
<td>form graph + component/site drivers</td>
<td>JCR-06</td>
<td>D-01～D-22</td>
</tr>
<tr>
<td>GAP10</td>
<td>命令选错task/模型阻塞控制</td>
<td>E02/E09</td>
<td>bound command + local priority + receipt</td>
<td>JCR-01</td>
<td>G-08/G-11/G-13</td>
</tr>
<tr>
<td>GAP11</td>
<td>启动失败无法进入产品修复</td>
<td>E11/E20</td>
<td>always-available bootstrap UI</td>
<td>JCR-02/08</td>
<td>A-01～A-12</td>
</tr>
<tr>
<td>GAP12</td>
<td>诊断失去cause/loadedversion</td>
<td>E12/E21</td>
<td>safe structured error timeline</td>
<td>JCR-02/08</td>
<td>U-06/U-10</td>
</tr>
<tr>
<td>GAP13</td>
<td>生产原地git更新无完整回退</td>
<td>E13/E21</td>
<td>staged candidate + locked deps + rollback</td>
<td>JCR-08</td>
<td>U-01～U-05</td>
</tr>
<tr>
<td>GAP14</td>
<td>安全标记冒充健康/复核</td>
<td>E09/E10</td>
<td>task/readiness/health separated read model</td>
<td>JCR-01/07/08</td>
<td>H-01/U-07/U-08</td>
</tr>
<tr>
<td>GAP15</td>
<td>next/apply词义≠动作无副作用</td>
<td>E05/E18</td>
<td>certified effect contract + unknown拒绝</td>
<td>JCR-01/06/07</td>
<td>F-08/H-07</td>
</tr>
<tr>
<td>GAP16</td>
<td>模型和artifact出口不是全信息流证明</td>
<td>E02/E05/E12/E14/E15</td>
<td>explicit egress schemas + canary scanning</td>
<td>JCR-01/04/05/08</td>
<td>F-01～F-09</td>
</tr>
<tr>
<td>GAP17</td>
<td>测试合同与用户结果错位</td>
<td>E19～E24</td>
<td>independent server oracle + actual UI/Mac</td>
<td>JCR-01～09</td>
<td>Z-01～Z-07</td>
</tr>
<tr>
<td>GAP18</td>
<td>用户提交后的证据闭环不完整</td>
<td>E01/E08/E10</td>
<td>readonly submission observer + protected IDs</td>
<td>JCR-07</td>
<td>H-08/B-12/F-07</td>
</tr>
</tbody>
</table></div>
</section>
