# TS 常驻复盘 Web 与 Obsidian 交易日记桥接

日期：2026-08-31。实施起点：`main@9474b6cdbacf19e228769e2a8a04a474058c7d84`。

这是本次合并范围的架构、实施与验收权威报告，替代增量状态架构报告第 20 节中尚未部署的 Python Web 服务方案。旧节保留为决策历史，不表示曾安装或仍采用旧服务。两处 `开发路径图.md` 均不自动更新。

## 1. 目标、批准与边界

用户已明确批准：统一日/周 Web UI 改用 TypeScript；在本机 `127.0.0.1:8765` 用 LaunchAgent 持久运行；保留 Python 交易计算和 SQLite；加入确认后的 Obsidian 单向同步；本仓库后续 Git 身份改为 `archerthegoat`，公开代码归于 `main` 并推送。

不包含：改写 Git 历史、强推或删除旧分支；写券商或确认真实交易；市赚率行情/财报接入；扩大采集源；自动清理历史；云端同步或备份 Vault；安装 Obsidian 社区插件；移动数据库；修改开发路径图。服务批准不是“复盘完成”，也不确认任何计划草案。

Bridge 最终字段和模板的准确文本仍须按现有知识交接契约批准。第 3 节为本次准确文本提案；在其批准之前，只实施已获批的 TS/服务和 Git 范围，不启用正式 Bridge 或改 Vault 模板。

## 2. 事实、方案与责任

现有 SQLite v3 已保存事实分区、分析缓存、周度版本与计划，但不包含当前 UI 的全部整理文案。DB 中旧周度展示字段不能覆盖已校正的美股展示内容。保存严格的派生展示快照是必要边界，不是第二个交易数据库。

- Python 数据端：保留现有严格 Schema、时区/证据验证、EMA/ATR、SQLite 增量逻辑。新增小型标准输入/输出适配器，投影或验证 account-free 展示数据；显式周度 DB 导出只读，不迁移或改 journal。规则不在 TS 中重复实现。
- TS 展示端：真正用 TS 渲染同一套 HTML/CSS、发布不可变版本、重建及提供只读 HTTP。不是 TS 包装 Python HTML，也不新增日周切换、第二周度页面、React 或移动端设计。
- 持久服务：固定版本 TS 源码、模板和必要数据适配器安装到 `~/Library/Application Support/MarsTradingCenter/web-ui/code/<content-id>/`；Node 24.12+ 原生 erasable TypeScript，运行不依赖 npm、临时目录或当前 Git 分支。发布验证沿用系统 `/usr/bin/python3`（最低 3.9，本机 3.9.6），服务读取已发布 HTML 不启动 Python。迁移过来的其他 Python 路径仍含 Intel 二进制，不能仅凭文件存在就采用；不在本轮改全局 Python 安装。
- 发布快照：`web-ui/publications/<id>/{view.json,index.html,manifest.json}`；先验证并回读，最后原子替换索引。相同内容幂等；日度默认复用原周度及原生成时间；历史入口固定，根入口表示当前记录。所有本地私有目录 0700、文件 0600，拒绝链接、其他所有者和非固定路径。
- 知识中心接收端：源代码可在本公开仓库维护，但运行身份、允许写入路径与接收回执属于个人知识中心；安装在知识中心的 Vault 外专用目录。交易发布端和 HTTP 服务不能直接写 Vault。接收端只处理第 3 节脱敏包，不访问券商、不写回 SQLite 或计划。

选择轻量 Node 标准库而非全栈框架，是因为当前页面的交互已经由原生 details、radio 和 checkbox 完成，TS 的责任是展示与本地生命周期。Node 的类型剥离不提供类型检查，另用锁定版本 TypeScript 执行 `tsc --noEmit`。

## 3. Bridge 准确文本提案（待单独确认）

### 3.1 交接格式

正式包名为 `confirmed-investment-review.v1`。根字段固定如下，拒绝未知字段；文本必须通过隐私门，不能把私有字段塞进自由文本：

```text
schema_version: "confirmed-investment-review.v1"
review_type: "daily" | "weekly"
review_key: "daily:YYYY-MM-DD" | "weekly:YYYY-MM-DD:YYYY-MM-DD"
review_date: YYYY-MM-DD
period_start: YYYY-MM-DD
period_end: YYYY-MM-DD
confirmation_status: "confirmed"
confirmation_version: positive integer
supersedes_confirmation_version: positive integer | null
confirmed_at: RFC3339 timestamp
source_revision: positive integer
facts_hash: SHA-256
plan_hash: SHA-256
facts_as_of: RFC3339 timestamp
generated_at: RFC3339 timestamp
data_status: "complete" | "partial" | "empty" | "stale"
gap_categories: string[]
sections: {
  executions: string[],
  plan_actual: string[],
  holdings_understanding: string[],
  events: string[],
  facts: string[],
  interpretation: string[],
  conditions: string[],
  pending: string[],
  lessons: string[],
  confirmed_plan_summary: string[]
}
weekly_metrics: null | {
  coverage_rate: number | null,
  execution_rate: number | null,
  plan_win_rate: number | null,
  eligible_episode_count: nonnegative integer,
  covered_episode_count: nonnegative integer,
  assessable_episode_count: nonnegative integer,
  compliant_episode_count: nonnegative integer,
  resolved_episode_count: nonnegative integer,
  successful_episode_count: nonnegative integer,
  review_needed_count: nonnegative integer,
  data_status: "complete" | "partial" | "empty" | "stale" | "blocked",
  gap: string | null
}
payload_hash: SHA-256
```

日度区间起止等于 review_date，weekly_metrics 为 null；周度 review_date 等于 period_end。所有日期遵循 America/New_York。比例为 0—1，必须与其分子分母一致；零分母为 null，不能显示成 0%。blocked 指标没有事实计数、比例均 null，且带缺口；不能把确认升级为数据完整。source_revision/facts_hash/plan_hash、区间、时间、确认版本必须在只读 SQLite 交叉验证。当前库没有足够权威绑定的日度记录时，拒绝正式 enqueue，不凭页面或聊天构造确认凭证。

payload_hash 为去掉本字段后、键排序、UTF-8、无额外空白的确定性 JSON 的 SHA-256。包最大 256 KiB，单条文本不超过 2000 字符，各列表最多 100 条。文本禁止账户金额、精确仓位/交易数量、成交价或计划价、成本/费用、盈亏、任何券商 ID、凭据、原始响应、完整机器计划和私有路径；只保留脱敏执行结论、认知和规则。无损脱敏做不到时停止，不能静默删改成“已确认”。

### 3.2 流程、所有权和版本

1. 用户对当前事实/计划版本明确“复盘完成”后，生产端核验 SQLite 中对应确认与上述字段；本次服务授权不能创建这个确认。
2. 生产端把正式包原子放入固定 owner-only、非 Git/Vault 的 `~/Library/Application Support/MarsTradingCenter/bridge/outbox/`，不在页面打开或每日读取时自动入队。
3. 知识中心受限接收命令验证 Schema、隐私、hash、路径和版本后写入唯一目标日记；接收端不改数据库。采用显式调用，没有额外 watcher、常驻同步进程或轮询任务。
4. 接收端通过 `obsidian vault=Mars知识库vault read path=<精确相对路径>` 独立回读并对比正文，成功后在 Vault 外落回执。CLI 退出成功但内容不一致不是同步成功。

日度路径：`25 投资交易/10 每日复盘/YYYY-MM-DD 交易复盘.md`。
周度路径：`25 投资交易/20 周度复盘/YYYY-MM-DD 至 YYYY-MM-DD 周度复盘.md`。
不迁移、不覆盖现存无托管标记的日记。同版本同 hash 为幂等回读；同版本不同 hash 冲突；旧版本不覆盖新版本；更新必须明确 supersedes 当前版本。

### 3.3 日记模板准确变更

现有 `90 模板与系统/模板/投资交易复盘模板.md` 的 frontmatter 字段原样保留，但不把 payload_hash、facts_hash、私有路径或技术错误放进可见正文。正式生成日记使用本格式；模板默认 pending/blocked 仍保留，手动建模板不是自动确认。

```diff
-## 昨日操作摘要
+## 成交与计划执行

-> 只写脱敏后的标的、工具类别、动作类别、对冲角色、计划关联和验证状态。不得写精确数量、价格、成本、费用或任何 ID。
+> 只记录已成交行为及是否遵守事前计划；没有成交就明确无成交。不得写精确数量、价格、成本、费用、盈亏或任何券商 ID。

+## 周度执行质量
+
+> 仅周度填写计划覆盖率、按计划执行率、计划胜率和需具体复盘的数量；保留分母及缺口，日度不重算。日度生成的正式日记省略本节。
+
+-
```

其他正文标题原样保留。接收端生成的正文（从状态与缺口到 Revision log）整体位于唯一的 `<!-- trading-review:managed:start -->` 与 `<!-- trading-review:managed:end -->` 之间；标题和 frontmatter 不随更新重写。文末增加 `## 我的补充`，位于托管块外。

模板末尾准确追加：

```markdown
## 我的补充

> 此处由我自由记录；同步不会覆盖这部分。

-
```

机器状态以 Vault 外回执为权威，正文只显示人可读确认版本、数据时间和缺口。更新只替换未被人工改动的托管块；其余字节保持不变。发现人工改过托管块、标记重复/缺失、文件身份或内容并发变化时返回 conflict，不自动覆盖。Markdown 中的嵌入、HTML、链接和控制字符不从交接自由文本执行。

## 4. 一致性、权限、失败与运维

| 情况 | 可观察状态 / 处理 |
|---|---|
| 发布输入非法、旧时间倒退、并发写者 | 拒绝发布；当前索引不变。写锁不凭超时自动抢占，残留锁须核实后恢复 |
| 写文件中断 | 索引只引用已完成且 hash 一致的版本；未索引半成品可按完全相同内容恢复 |
| HTTP 工件不存在/损坏 | 503，不静默切换日期；健康信息只给发布身份/源时间，无持仓 |
| HTTP 越界路径、跨站请求或写方法 | 404 / 403 / 405；不提供 JSON、DB、日志、源码、目录或写 API |
| 本机其他程序访问 | localhost 不是身份认证；只绑定 loopback，Host/Origin/Fetch-Site 限制、无 CORS、CSP 禁脚本外联；不承诺防御已控制本机用户的程序 |
| 新日度、旧周度 | 保留周度时间及内容；没有新周度数据不是自动错误，也不能假更新 |
| 未确认、绑定不匹配或隐私拒绝 | 不正式 enqueue、不写 Vault、不产生 synced 回执 |
| 已写日记但 CLI 不可用 / 回读不一致 | written_pending_readback；重试回读，不再次追加或虚报 synced |
| 确认版本重复、冲突或并发人工修改 | 同 hash 幂等；冲突保留原日记，等待人工处理，不新造另一份同名日记 |
| 接收在写后、回执前中断 | 先识别待完成目标及内容 hash，只恢复回读，不重复覆盖或追加 |
| 端口冲突 | 不杀未知 PID，不改端口。仅交接本任务已知旧预览 |
| Node/Python/权限/安装不满足 | 报告未安装或未切换，保留旧服务和历史；不全局安装或放宽权限 |

LaunchAgent `com.marstradingcenter.web-ui` 使用绝对 Node 路径、固定安装、RunAtLoad/KeepAlive、Umask 077、重启节流和脱敏日志。登录会话内工作；退出登录、关机、休眠不保证可访问，不是系统 daemon。提供 status/start/stop/restart/uninstall；uninstall 仅撤下本 Agent，历史和 DB 不删。

维护责任归本地项目维护者：显式发布与安装升级、查看脱敏失败、磁盘管理及冲突决定。本次无定时采集、自动备份、自动清理、远程暴露或 Mars Reader 配置修改。模板/Bridge 更新不转移机器计划权威，Obsidian 编辑永不反向执行交易。

## 5. 阶段、迁移、回滚与替代方案

先记录方案与准确 Bridge 审批，再迁移 TS renderer/发布/服务并做合成与真实私有投影比对。Bridge 在准确文本批准后单独实现及验证；不为证明接入擅自确认真实复盘。

安装使用内容寻址固定代码和原子 current 索引。保留原 Python 临时预览文件、不修改源输入；只停止本任务核实的旧进程并启动新 Agent。切换失败撤下新 Agent，用原预览恢复同端口。发布回滚只切换当前/上一版指针；代码回滚显式安装保留旧版本，不回滚 SQLite、清理日记或改写 Git 历史。Bridge 停用只停止调用接收端，所有已写日记和回执保留；修正通过新确认版本，不删除历史。

未选择：全栈框架和第二 DB（没有所需新增能力）；从 SQLite 编造全部文案（现有字段不足）；把全量私有包长期复制（隐私越界）；Web 写 Vault 和双向同步（所有权、并发及交易安全风险）；新增常驻轮询（确认后按需调用已够用）；重写作者历史（用户只授权未来身份）。

## 6. 一标签桌面与日记人工验收

准确提交/安装和发布身份见第 7 节，实施前 PENDING。沿用一个内置 Browser 标签，正常桌面面板，100% 缩放，无手机/窄屏验收；起始持仓标签选中、筛选关闭。私有本轮真实内容用于页面；错误和冲突只用隔离合成样本，不破坏真实资料。约 5 分钟；关闭 Codex/重新登录另行可选验证。

| 顺序 | 操作 | 预期 |
|---|---|---|
| 1 | 同标签打开 `http://127.0.0.1:8765/`，随后 `/w35-20260831/` | 同一套日周内容，一个主视图；日/周时间清楚。历史链接固定，无账户/后台字段 |
| 2 | 看市场/判断/成交，再切持仓与买入计划、打开并清空筛选 | 只看实际美股成交；持仓计划与未持有候选分开；无额外周度操作或盈亏板块；五类只显示已确认标签，不杜撰 |
| 3 | 展开两周各一天及一个空日 | 星期日期、双时区和直白情景影响；空日“暂无已收录事件”；无来源控件或无实际值的“预期”标题 |
| 4 | Tab/Shift+Tab 到筛选和展开项，Space/Enter 切换，滚动长详情 | 可见焦点、顺序合理，不跳页首、不丢焦点、无页面级横向溢出。持仓意向仍是待确认 |
| 5 | 刷新并看更新说明；有条件时看 Console/Network | 刷新不获取行情或更新时间；无外部业务请求、脚本/CSP 错误；没有虚假 loading |
| 6 | 地址换 `/daily-dashboard.json`、`/not-available/` 后返回 `/` | 均 404，无私有文件或目录。503/403/405、stale、取消、冲突由隔离自动证据补充 |
| 7 | 仅在今后明确“复盘完成”后运行已验证 Bridge 命令，再打开该唯一日记 | 标题/区间正确；执行与规则为主，周度指标含口径与缺口，无账户价格/数量/盈亏；确认后 partial 仍 partial |
| 8 | 手写“我的补充”，同版本再同步；以后新确认版本同步 | 同版本不重复日记/段落，手写字节保留，新版本只更新机器块；托管块冲突在合成测试验证，真实日记不为测试故意破坏 |
| 9 | 恢复持仓、关闭多余展开项，保留原标签 | 无多余标签、无交易确认副作用；停止服务用 stop，恢复用 start；不删除资料 |

无生产确认包时，第 7—8 步 NOT RUN，不假造生产成功。Obsidian CLI 不可用、版本/隐私拒绝、半写中断、无托管标记、人工/并发冲突、分母为零用隔离测试；这些是自动证据，不替代人的验收。

| 人工项 | 记录 | 备注 |
|---|---|---|
| 统一页面与桌面交互 | PENDING | |
| 空态、陈旧、错误路由与 Network/Console | PENDING | |
| 关闭 Codex 后访问 | PENDING | |
| 注销/重启后服务 | NOT RUN | |
| 首份真实确认日记与手写保留 | NOT RUN | 当前基础设施批准不是复盘确认 |

## 7. 实施与证据

- 架构与实现范围：用户已批准。第 3 节准确 Schema/模板：PENDING。
- 本仓库未来 author/committer 已固定为 archerthegoat + GitHub ID-based noreply；未改全局身份，未重写历史。
- TS 代码：已实施。Python 保留数据验证、投影、EMA/SQLite 与旧 renderer，TS 真正负责当前页面渲染、发布、HTTP、LaunchAgent 维护；原未部署的 Python Web 服务草稿已移除。
- 自动验证：`tsc --noEmit` 通过；17 项 TS 自动测试、168 项 Python 自动测试、项目 Skill 校验通过。TS 与 Python 旧 V2 在四种状态、日度独立/周度日历、五类策略与计划详情上归一化 DOM/文字相同；真实私有投影也比对相同，不仅检查合成样本。
- 已安装并运行：Node v24.20.0 / arm64；用户级 LaunchAgent `com.marstradingcenter.web-ui`。代码包 `356b6b301eeb94555851d979200677e9166cf10c2d252b98a94bb0a2cac940a7`；只监听 `127.0.0.1:8765`。
- 已发布：`3b5f930e2bf424e37edccb79740bd6b32cdad642220cb6722cff5f0ce90a75d5`；HTML SHA-256 `6a86b427741a83703792f46f14e6918448a2585d60531c0b3601a1d30ac94277`。根入口和 `/w35-20260831/` 同页，健康回读一致；私有 JSON 和不存在路径均 404。
- 进程回读：原任务临时预览经原终端正常退出，未按旧 PID 杀进程；新 Agent 从 running PID 30098 经显式 restart 变为 running PID 30634，页面 hash 不变。从上述固定 code 目录执行 rebuild 得到同一 publication id，无源临时文件或仓库读取依赖。
- 无副作用回读：安装和重建后，真实 SQLite、交易中心开发路径图、Obsidian 交易日记模板的文件 hash 均与实施前相同；当前安装包 hash 与源码包相同。知识中心/Vault 没有本轮文件变更。
- Agent 浏览器技术检查：原单标签刷新，正常桌面面板实际 775×796、未设置手机/窄屏模拟，页面 scrollWidth 等于面板宽；一个 main、14 个日期桶、无脚本/外部链接，空候选可见，切换后恢复持仓视图。自动观察到清楚的焦点环；完整键盘、Console/Network、关闭 Codex 和注销后可用性未代做人工验收。
- 发布源码：准备提交并推送 main；最终 Git SHA 与远端 CI 以发布回读为准，不把本地通过当远端 CI 成功。
- 实际 review/plan 确认、真实 Obsidian 日记同步：NOT RUN；不能以安装或 fixture 冒充。
- 原五类策略准确名称、市赚率数据接入和未确认交易条件仍待另行处理。

### 7.1 对抗式复核、已知边界与连续性

规格反证：特意检查“TS 只套壳 Python HTML”“日度重算或删除周度”“提交委托混入成交”“把已持有标的另做买入计划”“模板出现账户字段”“服务批准被当成复盘确认”这些反例。实际 TS renderer 与原结构比对通过，日周保存与不可回退门禁通过；Bridge 未过准确文本门就没有落地接收端或写 Vault，真实计划继续 pending。

工程反证：验证 duplicate JSON、非法 UTF-8/非有限值、符号/硬链接、权限错误、安装包篡改、源时间倒退、并发写者、索引切换前中断、残留锁、HTTP 越界/写入/跨站、缺失/损坏发布和纽约 DST 歧义。残留写锁不按超时自动接管，新增按精确 nonce + 已死亡 PID 的显式恢复；活 PID 或身份不符均拒绝。原来以五类分组总数为五的测试忽略了既有“待分类”组，按真实契约修正为保留五类再保留待分类，未为通过测试丢弃候选。

数据适配器沿用已测系统 Python 3.9.6；安装、服务和文档采用实际可执行的 ARM Node，不改全局默认工具或尝试重写旧机器环境。Node 类型剥离与静态类型检查分开，生产安装不含 npm/node_modules；TS 类型检查工具锁定于 package-lock，仅开发/CI 使用。

未声称完成：真实复盘确认与 Obsidian 首份写入/回读、人工验收、新用户登录与休眠后持续可用、未来每日自动发布。固定服务本身不会重新采集数据。架构中的准确 Bridge 文本仍是 PENDING 提案，不能因为代码推送而冻结。

复用检查已落实在项目 Skill：明确原生运行时、数据验证和 TS 展示的分工、服务不等于数据更新、残留锁失败关闭与显式恢复。不向全局记忆、用户级其他 Skill 或开发路径图写入一次性事实。

## 8. 来源

- [原增量状态架构报告](trading-center-skill-incremental-state.md)，尤其第 18—20 节的已验证 UI 与旧服务历史。
- [项目 Skill](../../skills/trading-center-review/SKILL.md)、[知识交接边界](../../skills/trading-center-review/references/knowledge-handoff-contract.md)、[展示契约](../../skills/trading-center-review/references/dashboard-visualization-contract.md)。
- 知识中心本地权威：`Mars知识库vault/90 模板与系统/架构与验收/Mars 个人知识中心架构与验收.md`、投资交易复盘模板及 Bridge 规划事项；无公开复制个人资料。
- [Node TypeScript](https://nodejs.org/docs/latest-v24.x/api/typescript.html)：原生类型剥离的版本及限制，不替代 tsc。
- [GitHub noreply 与作者归属](https://docs.github.com/en/account-and-profile/reference/email-addresses-reference)：使用账号 ID 和用户名，不用机器邮箱。
- [Apple LaunchAgents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)：用户级生命周期边界。
