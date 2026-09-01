# TS 常驻复盘 Web 与 Obsidian 交易日记桥接

日期：2026-08-31。实施起点：`main@9474b6cdbacf19e228769e2a8a04a474058c7d84`。

这是本次合并范围的架构、实施与验收权威报告，替代增量状态架构报告第 20 节中尚未部署的 Python Web 服务方案。旧节保留为决策历史，不表示曾安装或仍采用旧服务。两处 `开发路径图.md` 均不自动更新。

## 1. 目标、批准与边界

用户已明确批准：统一日/周 Web UI 改用 TypeScript；在本机 `127.0.0.1:8765` 用 LaunchAgent 持久运行；保留 Python 交易计算和 SQLite；加入确认后的 Obsidian 单向同步；本仓库后续 Git 身份改为 `archerthegoat`，公开代码归于 `main` 并推送。

不包含：改写 Git 历史、强推或删除旧分支；写券商或确认真实交易；超出第 10 节限定估值范围的采集；自动清理历史；云端同步或备份 Vault；安装 Obsidian 社区插件；移动数据库；修改开发路径图。市赚率行情/财报接入不在首次 TS 迁移范围内，后经第 10 节批准追加。服务批准不是“复盘完成”，也不确认任何计划草案。

Bridge 最终字段和模板的准确文本现已获用户批准：用户确认采用已展示的日记内容、同步规则和隐私边界，并明确日记不需要精确交易数据。这只批准 Bridge 实现，不是任何一份复盘的“复盘完成”，也不确认交易执行。接收端代码、隔离测试及 Vault 外安装现已完成，但没有生成真实确认包、outbox 或日记写入；第 9 节记录实施前复核历史，第 10 节记录 Bridge/v4 实施及部署历史，第 11 节记录随后工具/周期与 v5 增量。不能把文本批准、安装或测试通过当成真实同步或人工验收通过。

## 2. 事实、方案与责任

首次 TS 迁移时的 SQLite v3 已保存事实分区、分析缓存、周度版本与计划，但不包含当前 UI 的全部整理文案。本轮已按第 10 节追加迁移至 v4，不改旧表记录。DB 中旧周度展示字段不能覆盖已校正的美股展示内容。保存严格的派生展示快照是必要边界，不是第二个交易数据库。

- Python 数据端：保留现有严格 Schema、时区/证据验证、EMA/ATR、SQLite 增量逻辑。新增小型标准输入/输出适配器，投影或验证 account-free 展示数据；显式周度 DB 导出只读，不迁移或改 journal。规则不在 TS 中重复实现。
- TS 展示端：真正用 TS 渲染同一套 HTML/CSS、发布不可变版本、重建及提供只读 HTTP。不是 TS 包装 Python HTML，也不新增日周切换、第二周度页面、React 或移动端设计。
- 持久服务：固定版本 TS 源码、模板和必要数据适配器安装到 `~/Library/Application Support/MarsTradingCenter/web-ui/code/<content-id>/`；Node 24.12+ 原生 erasable TypeScript，运行不依赖 npm、临时目录或当前 Git 分支。发布验证沿用系统 `/usr/bin/python3`（最低 3.9，本机 3.9.6），服务读取已发布 HTML 不启动 Python。迁移过来的其他 Python 路径仍含 Intel 二进制，不能仅凭文件存在就采用；不在本轮改全局 Python 安装。
- 发布快照：`web-ui/publications/<id>/{view.json,index.html,manifest.json}`；先验证并回读，最后原子替换索引。相同内容幂等；日度默认复用原周度及原生成时间；历史入口固定，根入口表示当前记录。所有本地私有目录 0700、文件 0600，拒绝链接、其他所有者和非固定路径。
- 知识中心接收端：源代码可在本公开仓库维护，但运行身份、允许写入路径与接收回执属于个人知识中心；安装在知识中心的 Vault 外专用目录。交易发布端和 HTTP 服务不能直接写 Vault。接收端只处理第 3 节脱敏包，不访问券商、不写回 SQLite 或计划。

选择轻量 Node 标准库而非全栈框架，是因为当前页面的交互已经由原生 details、radio 和 checkbox 完成，TS 的责任是展示与本地生命周期。Node 的类型剥离不提供类型检查，另用锁定版本 TypeScript 执行 `tsc --noEmit`。

## 3. Bridge 准确文本（用户已确认，正式接通仍待完成）

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

- 架构与实现范围：用户已批准。第 3 节准确 Schema/模板：用户随后明确批准，日记不写精确交易数据；接收端实现、真实同步及人工验收仍未完成，见第 9 节。
- 本仓库未来 author/committer 已固定为 archerthegoat + GitHub ID-based noreply；未改全局身份，未重写历史。
- TS 代码：已实施。Python 保留数据验证、投影、EMA/SQLite 与旧 renderer，TS 真正负责当前页面渲染、发布、HTTP、LaunchAgent 维护；原未部署的 Python Web 服务草稿已移除。
- 自动验证：`tsc --noEmit` 通过；17 项 TS 自动测试、168 项 Python 自动测试、项目 Skill 校验通过。TS 与 Python 旧 V2 在四种状态、日度独立/周度日历、五类策略与计划详情上归一化 DOM/文字相同；真实私有投影也比对相同，不仅检查合成样本。
- 已安装并运行：Node v24.20.0 / arm64；用户级 LaunchAgent `com.marstradingcenter.web-ui`。代码包 `356b6b301eeb94555851d979200677e9166cf10c2d252b98a94bb0a2cac940a7`；只监听 `127.0.0.1:8765`。
- 已发布：`3b5f930e2bf424e37edccb79740bd6b32cdad642220cb6722cff5f0ce90a75d5`；HTML SHA-256 `6a86b427741a83703792f46f14e6918448a2585d60531c0b3601a1d30ac94277`。根入口和 `/w35-20260831/` 同页，健康回读一致；私有 JSON 和不存在路径均 404。
- 进程回读：原任务临时预览经原终端正常退出，未按旧 PID 杀进程；新 Agent 从 running PID 30098 经显式 restart 变为 running PID 30634，页面 hash 不变。从上述固定 code 目录执行 rebuild 得到同一 publication id，无源临时文件或仓库读取依赖。
- 无副作用回读：安装和重建后，真实 SQLite、交易中心开发路径图、Obsidian 交易日记模板的文件 hash 均与实施前相同；当前安装包 hash 与源码包相同。知识中心/Vault 没有本轮文件变更。
- Agent 浏览器技术检查：原单标签刷新，正常桌面面板实际 775×796、未设置手机/窄屏模拟，页面 scrollWidth 等于面板宽；一个 main、14 个日期桶、无脚本/外部链接，空候选可见，切换后恢复持仓视图。自动观察到清楚的焦点环；完整键盘、Console/Network、关闭 Codex 和注销后可用性未代做人工验收。
- 发布源码：代码提交 [2ed8cfd2e0388fc1fd87664023e0da37acf48efb](https://github.com/archerthegoat/trading-review-longbridge/commit/2ed8cfd2e0388fc1fd87664023e0da37acf48efb) 已推送 `main`，远端分支 SHA 回读一致；GitHub API 将该提交的 author 和 committer 均关联到 `archerthegoat`。[对应 push CI](https://github.com/archerthegoat/trading-review-longbridge/actions/runs/33394835354) 回读 `completed / success`。后续本段工程记录提交不改变上述运行代码包或页面。
- 实际 review/plan 确认、真实 Obsidian 日记同步：NOT RUN；不能以安装或 fixture 冒充。
- 原五类策略准确名称、市赚率数据接入和未确认交易条件仍待另行处理。

### 7.1 对抗式复核、已知边界与连续性

规格反证：特意检查“TS 只套壳 Python HTML”“日度重算或删除周度”“提交委托混入成交”“把已持有标的另做买入计划”“模板出现账户字段”“服务批准被当成复盘确认”这些反例。实际 TS renderer 与原结构比对通过，日周保存与不可回退门禁通过；Bridge 未过准确文本门就没有落地接收端或写 Vault，真实计划继续 pending。

工程反证：验证 duplicate JSON、非法 UTF-8/非有限值、符号/硬链接、权限错误、安装包篡改、源时间倒退、并发写者、索引切换前中断、残留锁、HTTP 越界/写入/跨站、缺失/损坏发布和纽约 DST 歧义。残留写锁不按超时自动接管，新增按精确 nonce + 已死亡 PID 的显式恢复；活 PID 或身份不符均拒绝。原来以五类分组总数为五的测试忽略了既有“待分类”组，按真实契约修正为保留五类再保留待分类，未为通过测试丢弃候选。

数据适配器沿用已测系统 Python 3.9.6；安装、服务和文档采用实际可执行的 ARM Node，不改全局默认工具或尝试重写旧机器环境。Node 类型剥离与静态类型检查分开，生产安装不含 npm/node_modules；TS 类型检查工具锁定于 package-lock，仅开发/CI 使用。

未声称完成：真实复盘确认与 Obsidian 首份写入/回读、人工验收、新用户登录与休眠后持续可用、未来每日自动发布。固定服务本身不会重新采集数据。TS 交付时准确 Bridge 文本仍为 PENDING；此后用户的准确文本批准已记录在第 1、3、9 节，不是由代码推送推断出的批准。

复用检查已落实在项目 Skill：明确原生运行时、数据验证和 TS 展示的分工、服务不等于数据更新、残留锁失败关闭与显式恢复。不向全局记忆、用户级其他 Skill 或开发路径图写入一次性事实。

## 8. 来源

- [原增量状态架构报告](trading-center-skill-incremental-state.md)，尤其第 18—20 节的已验证 UI 与旧服务历史。
- [项目 Skill](../../skills/trading-center-review/SKILL.md)、[知识交接边界](../../skills/trading-center-review/references/knowledge-handoff-contract.md)、[展示契约](../../skills/trading-center-review/references/dashboard-visualization-contract.md)。
- 知识中心本地权威：`Mars知识库vault/90 模板与系统/架构与验收/Mars 个人知识中心架构与验收.md`、投资交易复盘模板及 Bridge 规划事项；无公开复制个人资料。
- [Node TypeScript](https://nodejs.org/docs/latest-v24.x/api/typescript.html)：原生类型剥离的版本及限制，不替代 tsc。
- [GitHub noreply 与作者归属](https://docs.github.com/en/account-and-profile/reference/email-addresses-reference)：使用账号 ID 和用户名，不用机器邮箱。
- [Apple LaunchAgents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)：用户级生命周期边界。

## 9. Bridge 文本批准后的独立实施前复核

复核绑定 `main@084e05a0e081bc8707be1d3800fb5c3de082f751` 的干净源码；独立只读审阅，没有读取真实 SQLite、个人日记或账户事实。用户已批准第 3 节日记内容与隐私边界，因此不再把“等待日记文本确认”当作当前阻塞。本节只记录现有事实、复核处理和待讨论的最小补充，不冻结新数据库结构或新同步协议。

### 9.1 复核结果与主 Agent 处理

| 发现 | 核实与处理 |
|---|---|
| 报告起点与当前提交不同 | 不接受为缺陷：第 3 行明确是“实施起点”，不是当前版本声明。此次复核另绑定上述完整提交，保留历史基线。 |
| `confirm()` 本身没有校验完整复盘绑定 | 接受。`trading_review_state.py:2917` 起只检查字段形式并写确认；producer 必须独立检查真实复盘存在、最新确认、事实 hash、版本及 supersedes 链，不能单凭 confirmed 字段入队。 |
| 周度事实 hash 是否包含计划与正文 | 通过。`trading_review_state.py:1886` 起的摘要覆盖 `plan_hash` 和已持久化 `review_items`；它不等于 Bridge 全文 hash。第 3 节独立 `payload_hash`、同版本异文冲突规则仍必须实现并测试。 |
| 日度缺少完整确认绑定 | 接受，为正式日度同步阻塞。`runs` 未保存完整 facts/plan/revision 绑定；`run_incremental_review.py:405` 起只把相关值放入返回 manifest。按已批准规则，当前无法证明对应版本时拒绝 enqueue，不从网页、聊天或旧 manifest 拼造确认。 |
| `source_revision`、`facts_as_of` 的映射 | 接受为待补明确定义。周度本地 revision 可作为候选映射，但不能把报告生成时间当作全部事实的时间。完整日度/周度桥接需明确权威来源与时间语义，而不是在接收端猜测。 |
| 现有 `_text()` 隐私检查不足 | 接受，为已批准实现范围内必须补齐的 validator。原状态库字符串校验不排除全部交易价格、数量和盈亏表达；Bridge 需要独立保守检查，失败保留候选并阻止写入，不静默改成“已确认”正文。 |
| 同步可能覆盖并发人工编辑 | 接受，为写入路径阻塞。receiver 自身锁不与 Obsidian 编辑器共享；最后一次读取与替换之间仍可能出现人工保存。仅校验 hash 和写后回读不能证明未丢失人工编辑。需与用户确认可执行的编辑静默窗口或其他协作协议；目前不写真实日记。 |
| 写后、成功回执前中断 | 接受为实现时须补齐的恢复细节。成功回执只能在独立回读后生成；此前须有可恢复的待完成写入记录和内容身份。沿用不自动删除历史的边界；证据缺失时 fail closed，不猜测已同步。 |
| 依赖变更导致周度陈旧 | 接受。`weekly_review_freshness()` 独立于旧 review 的 data_status；producer 不能仅复制旧 complete/partial 而忽略新鲜度检查。 |

### 9.2 最小补充方向（用户已确认）

1. 为日度及周度补齐可只读核验的“已确认复盘版本”权威绑定，明确事实/计划摘要、来源版本、事实时间和确认时间；这可能需要追加 SQLite 结构，必须先对齐准确变更、迁移备份与回滚，不能借 Bridge 授权静默迁移数据库或补造历史确认。
2. 明确同步时的人工编辑边界。候选方案为目标日记未在编辑时才更新，正在编辑则暂缓；具体如何确认静默窗口仍须讨论。不会新增 Obsidian 社区插件、后台 watcher 或双向回写来隐式解决。

当时状态（实施前历史，不是当前状态）：日记文本与隐私边界 **APPROVED**；独立预审 **发现阻塞，已核实**；正式 Bridge/数据库迁移/Vault 模板变更 **NOT RUN**；真实日记同步与人工验收 **NOT RUN / PENDING**。后续实现见第 10 节。本节工程记录不包含个人持仓管理细节。

## 10. 本次批准的实施边界与补充契约

批准依据：用户在四项范围（持仓/买入候选市赚率、雷达数值、管理原则状态、Bridge 版本绑定及编辑保护）重述后回复“可以”。沿用本报告已批准的单向同步文本、目录、隐私及人工确认门；不把某一持仓的管理意图确认推导成整份复盘确认。

### 10.1 展示与数据

- 保留同一套桌面 TS Web UI；不新建周度页面、估值板块或移动端需求。雷达列为资产/指数、最新值、涨跌幅、状态；移除没有计算依据的强度点。
- 市赚率只附着当前美股持仓或已明确计划买入的未持有公司，期权归并其公司标的。ETF 不套用企业 ROE。计算为 PE(TTM) / 最近可核对年度 ROE 的百分点值；明确显示年度及读取时间，不伪称 ROE(TTM)，不自动标记低估或产生买入指令。负值、零、缺失、报告期未知、过旧均保留明确不可用/陈旧状态。原始响应只在私有运行区短期保留，状态库只收固定标量。
- 单独记录“持仓管理原则已确认”；不会把观察价格改成可执行订单，也不会绕过现有已成交后才允许加仓计划的条件。

### 10.2 SQLite v4（Bridge 实施阶段，现由 v5 继承）：只追加，不改历史

在原有 v3 表之外追加以下责任单一的表；现有备份、事务迁移、列白名单及 owner-only 规则不变。

| 表 | 固定内容与边界 |
| --- | --- |
| `daily_review_sources` | 日度复盘键与递增来源版本、已完成 run、事实/计划摘要、生成时间、事实采集起点、状态；来源未持久化的旧日度记录不补造绑定。 |
| `daily_review_source_dependencies` | 对应日度来源版本的精确分区键、版本、摘要，用于重算事实摘要及发现依赖变化；不存原始响应。 |
| `journal_confirmation_bindings` | 外键关联现有 confirmations，额外绑定类型、来源版本、事实/计划摘要、采集/生成时间、状态和整个脱敏包摘要；旧的弱确认记录不能单独获得 Bridge 写权限。 |
| `valuation_observations` | 明确在范围内的标的、读取时间、PE、ROE 百分点、财年/期末、PR、状态、缺口、来源；无自动扩展同业。 |
| `holding_management_intents` | 带版本的定性管理意图、观察位、确认时间与摘要；明确不包含可执行交易授权，不替代 plan_versions。 |

日度 runner 在全部分区和分析入库且 run 完成后记录来源绑定。周度复用既有 weekly_reviews.revision、事实/计划摘要及精确依赖。`facts_as_of` 定义为被绑定依赖中最早的 `collected_at`（采集起点），不是最新行情时间，也不是报告生成时间；日记用“事实资料采集起点”表述，保留 data_status/gaps。确认和入队都核对来源最新版本、依赖摘要、新鲜度及窗口；blocked 不能导出，partial/empty/stale 不改写为 complete。

正式确认入口先校验完整脱敏候选、真实来源及版本链，再在单一数据库事务内追加 confirmations 与 journal_confirmation_bindings。producer 只读库核验，逐字节内容摘要匹配才写不可变 outbox。不同正文即不同包，不能复用旧确认；同版本不同摘要、旧版重放、版本跳跃、跨日/跨周均拒绝。此轮没有整份复盘完成授权，因此不生成真实确认或真实同步包。

独立复核后补明：确认时的全部来源、依赖、窗口、版本链、正文摘要校验都在同一 `BEGIN IMMEDIATE` 内完成，不只是 INSERT 加锁。候选及已确认包保存在固定私有目录的不可变文件，DB 存完整包摘要；producer 只能读取该确切包，不从页面、聊天或新模型输出重建正文。无 plan_hash、无绑定依赖、未绑定的旧确认链均拒绝，不能补造采集时间或历史确认。只读 producer 的新鲜度是“一致读快照核验时点”的事实，不宣称文件入队与并发 DB writer 的提交线性化；包是明确时间与版本的已确认历史记录，不是实时行情。接收器不会把同步结果提升为当前交易授权。

### 10.3 接收端写入与恢复

已用当前 Obsidian CLI 只读核验 `app.vault.process` 可调用、Vault 名称匹配、能查询 Markdown leaves。[官方 Vault 文档](https://raw.githubusercontent.com/obsidianmd/obsidian-developer-docs/main/en/Plugins/Vault.md)规定 process 的同步回调在读取与写入间保护文件；这取代不安全的外部 read/replace。

- 接收器安装在 Vault 外、知识中心所属的固定代码目录；只接收固定 owner-only outbox 内的确认包，不读取券商，不修改交易数据库。显式 CLI 调用，没有 watcher、自动轮询、新插件或双向同步。
- 目标在任一 Markdown leaf 打开时先暂缓，包括预览模式。更新现有受管日记时，在 Obsidian 原子 process 的同步回调中再次检查打开状态、完整旧内容和唯一受管标记；仅替换受管块，保留 frontmatter、标题和“我的补充”的原字节。候选正文作为 JSON 数据，绝不拼接成可执行代码。
- 首次创建使用 0600 完整临时文件加排他 no-clobber 链接发布，目标已存在即冲突；不覆盖未受管旧日记。随后交由 Obsidian 独立 read 回读，索引尚未就绪则保持待回读，不宣称成功。临时文件只清理本次确切拥有的路径。
- 写之前持久化不可变 intent（包、目标、写前/写后摘要、受管块摘要），文件名同时绑定版本与完整内容摘要，恢复时核对 canonical bytes 和文件名摘要，不接受只在文件内自报的新 hash。写后成功回执只在独立 CLI 回读一致后追加。重试独立核对已确认包的预期受管正文、旧 receipt 的受管块摘要与身份：已写且全部匹配才补回读；仍为未改动旧内容才能重试；其他状态冲突，不猜测、不覆盖。不承诺防御控制本机用户并同时重写所有证据的程序。
- 目标路径、文件身份、所有者、权限、硬链接/符号链接、唯一标记及容量均检查；原始错误只留私有诊断。接收器锁不宣称能锁住其他任意外部程序，现有笔记更新依赖 Obsidian 自身的原子 API；不要并行使用其他脚本修改相同受管文件。

### 10.4 失败、迁移及维护

| 情况 | 结果 |
| --- | --- |
| ROE 不可核对/负利润或净资产异常/ETF | 展示明确不适用或缺口，不算虚假 PR |
| 没有整份复盘确认或包与确认不一致 | 不入队、不写 Vault |
| 依赖变更、来源旧版、确认链冲突 | 拒绝并要求重新复核确认 |
| 日记打开、受管正文被手改、已有无标记日记 | deferred/conflict，原文保留 |
| 写后中断、CLI 回读未完成 | written_pending_readback，可通过同一 intent 恢复 |
| 数据库迁移/网页发布失败 | 保留迁移前备份与旧发布，服务停用时先恢复原安装版本 |

部署前保存私有 SQLite 备份，核对旧表内容未变化。当前库已由第 11 节迁移至 v5；回滚需同时恢复旧代码和相匹配的数据库版本，不能只让旧代码打开 v5。本轮不会删除旧代码、备份、包或回执。UI 显示原有行情时间，估值使用独立读取时间，避免一次外观更新伪装行情刷新。维护责任仍为本地交易研究任务（producer/状态/网页）及知识中心（receiver/Vault），用户负责最终复盘与人工验收。

### 10.5 本次人工验收清单

准确分支：`codex/review-valuation-obsidian`，基线 `084e05a0e081bc8707be1d3800fb5c3de082f751`；本轮改动尚未提交，当前安装/发布摘要以本节末尾最新部署读回为准。只有独立复核闭合并重新部署当前修订后才执行本清单，不能拿旧安装或部署前记录冒充验收对象。一个复用的内置 Browser 标签打开 `http://127.0.0.1:8765/`，桌面视口 1440 × 900，100% 缩放；无需手机/窄屏覆盖。若受桌面面板限制，记录实际宽高后再验收。

1. 刷新首页：仍为同一个日度/周度复盘页面，原行情截至时间没有被此次发布改成当前时间；雷达最新值和涨跌幅清晰可读，没有强度圆点。
2. 从上到下滚动，切换“当前持仓/计划买入”：PR 位于各自标的中，能看到 PE、ROE 财年/期末及独立读取时间；ETF 显示不适用，缺数/陈旧不显示伪造数字；不显示额外账户信息或内部哈希。没有未持有计划时保留空状态。
3. 展开已讨论标的的管理条件：显示管理原则已确认，但观察位不是自动买卖授权，未决定的执行口径继续明确待讨论。
4. 用鼠标及 Tab/Enter 展开/收起计划与日历、切换筛选，检查焦点可见、滚动位置可控、无重复周度板块；确认原事件分桶和直接影响说明仍在。
5. 模板预览：成交与计划执行/周度执行质量/我的补充分区正确，无精确交易数据；本次不应凭空出现一份已确认交易日记。
6. 工程侧通过隔离样本覆盖 complete/partial/empty/stale、加载/回读失败、同包重试、正文篡改、目标打开与写后中断；网页检查无新增运行脚本和外部网络请求。真实 Bridge 需在下一份完整复盘由用户确认后再验收，不能用样本 PASS 替代。

重置：关掉展开项，清除筛选，返回首页顶部；不要删除历史复盘或真实日记。记录：UI PASS/FAIL 与备注 ______；真实 Bridge PASS/FAIL/NOT RUN ______。目前人工验收 **PENDING**。

### 10.6 实施证据与未完成边界

- 实现：TS/Python 同构估值行和雷达、固定标量估值采集/入库、非执行性管理原则、v4 来源/确认绑定、显式 producer/receiver、原子编辑和恢复、Vault 外内容寻址安装器。没有新增自动化、后台采集或交易执行入口。
- 真实状态：v3→v4 先在副本演练，再生成 0600 备份并迁移；旧表逐表行数及逻辑摘要保持不变。仅向两张新表追加限定估值观测和已批准管理原则。confirmations 未变，journal_confirmation_bindings 仍为零。私有证据和数据不进入 Git。
- 部署前历史：已批准 Vault 模板 diff 已应用，真实 Obsidian CLI 全文回读与文件字节内容一致；当时知识中心规划事项记录为“实现已有、部署待授权、真实验收未运行”。后续部署授权已执行并另行读回；未改两处开发路径图，仍没有生成正式交易日记，没有真实 prepare/confirm/enqueue/sync。
- 自动化验证：193 项 Python、22 项 TS 测试、`tsc --noEmit`、Skill 校验与 `git diff --check` 通过。原子 Obsidian 写入仅用隔离样本；真实 CLI 只验证能力与模板回读，不能替代生产同步验收。
- 独立复核发现并补测：语义有效的 intent 篡改不得绕过手改保护；pending 预览不得全局替换用户自由文本。前者增加内容寻址和独立正文/旧回执核对，后者使用显式 draft 渲染。独立复核者再次运行 22 项 Bridge 隔离测试，确认两项原反例均闭合，未发现同修复直接相关的回归；未代做真实同步或人工验收。另修正管理原则确认时间的跨时区比较，不更改真实已入库内容。该记录发生在 Bridge 尚未安装时；后续只安装接收器，仍没有真实 pending intent。不迁移旧试验格式，遇到不含内容摘要的旧 intent 名称继续拒绝。
- 部署门历史（已解除）：权限审查曾要求单独确认新版 8765 代码与 Vault 外接收器安装；当时旧服务 code_id=`356b6b301eeb94555851d979200677e9166cf10c2d252b98a94bb0a2cac940a7`、publication_id=`3b5f930e2bf424e37edccb79740bd6b32cdad642220cb6722cff5f0ce90a75d5`。用户随后明确授权并完成下述部署；此历史记录不再表示当前运行态，也不能通过临时服务或另一个端口绕过后续部署门。
- v4 是 Bridge 阶段的数据库状态，随后已按第 11 节迁移到 v5。数据库回滚必须另行批准，使用验证过且与代码匹配的备份；仅恢复旧网页进程不是回滚数据库。本轮未合并、未 push、未删除历史或私有工件。

已批准模板的局限：第 3.3 节明确保留 frontmatter 且更新时不改写，因此这些属性是首次创建快照，不是当前确认或同步状态；首次的 `ingest_status` 也不会在成功回读后被重写。当前版本看受管正文，当前同步状态看 Vault 外 receipt。独立复核指出属性可能被误读；本轮不擅自删除或改名已批准字段，也不宣称其是实时状态。若要精简为稳定元信息，应单独展示模板准确 diff 后由用户批准，真实 Bridge 验收须明确这一限制。

2026-09-01 首次部署读回（第 11 节修复前历史）：新版 Web 与 Vault 外接收器已按明确授权安装。Web code_id=`fa93d77ce20277335554fe687092ce8c1e7683eef5095ee141a73f09fe6dd488`，publication_id=`5ec7a67ff8612dd083fbe18f20fa8b9db4e9bb2e4108828e87d304c635339eba`；`/healthz` 与首页/新历史路由读回一致，行情时间和周度生成时间未被外观重建改写。接收器 code_id=`1cd2bcbce3c02e6f85fc9054e174550c3ed74ef65bd969ea69a23a2e945fbf32`，安装状态读回一致。真实确认包、outbox 和 Obsidian 日记写入仍为 **NOT RUN**。第 11 节修复版必须记录新的 Web 安装/发布摘要后才是人工验收目标。

当前状态：Bridge 实现及接收器安装 **DONE**；第 11 节独立复核后的 Web/状态修订 **IN PROGRESS**，尚未重新部署；真实 Bridge 首写 **NOT RUN**；本轮人工 UI/Bridge 验收 **PENDING**。历史 PASS 不迁移到修复版。

## 11. 每笔计划的交易工具与观察周期（本轮实施）

### 11.1 用户决定、现状与范围

用户纠正“兑现周期”为“观察/判断所用的 K 线周期”，明确杠杆工具指单股杠杆 ETF；要求每笔交易识别实际工具，并尽量在同一时间周期下判断与触发。用户随后确认本节对应范围，并明确授权第 10 节新版 8765 Web 服务与 Vault 外 Obsidian 接收器部署。此前部署等待授权已解除；不等于具体交易、观察周期或整份复盘已确认。

当前事实：价格构造仅使用获批 Longbridge 已完成日线；计划没有工具/观察周期字段，日度行合并按公司标的匹配；周度 episode 自然键为交易日/公司标的/方向，不能区分同公司正股与期权计划。原始交易聚合仍有脱敏 symbol，缺少可核验的工具分类时不能把普通 OPTION 猜成 LEAP。既有精确期权身份、价格/成本/账户隐私限制不变。

本轮保持现有 TS 单页、每日/周度结构、五类策略与持仓/未持有计划划分。只补工具、观察对象、主观察周期和触发方式，沿现有构造、逐版本确认、成交核对和展示链路实现；不重写 UI 框架，不新增行情能力、采集窗口、后台进程或下单接口，不修改开发路径图或已确认交易内容。

### 11.2 最小契约与所有权

计划新增可选但严格校验的 `execution_context`；旧版缺失保持缺失，不自动推断为日线/正股。新工具计划的完整 context 随该计划草案的 content_hash 绑定、随确认原样复制；改工具、对象、周期或触发方式必须产生新草案并重新确认。

| 字段 | 约束 |
| --- | --- |
| tool_kind | `stock`、`single_stock_leveraged_etf`、`leap_call`；无法识别只在展示/事实中为 `unknown`，不得确认新工具计划。 |
| trade_symbol | 实际正股/ETF ticker；期权使用不含行权价或到期日的标的级 `:OPTION` 投影，不保存完整合约身份。 |
| observation_symbol | 明确观察标的正股或实际 ETF；LEAP 本轮仅对其正股证据构造区间，不给期权权利金虚构等价价位。 |
| observation_timeframe | `1H`、`4H`、`1D`、`1W` 或尚未选择的 null；与持有天数及日/周复盘频率不同。 |
| trigger_timeframe | 默认继承主观察周期；不同时必须有已纳入确切草案的 exception_note。 |
| trigger_basis | `bar_close`、`intrabar_touch` 或尚未决定的 `unconfirmed`；不把盘中触及当成收盘确认。 |
| exception_note | null 或明确的周期/紧急风控例外说明，随草案确认，不在复盘中临时补造。 |

正股 trade_symbol 必须对应计划 underlying；单股杠杆 ETF 必须是不同的明确美股 ticker 且有真实/用户确认的工具映射；LEAP 只能匹配经核验为 leap_call 的期权事实，不用普通 OPTION、正股成交或当前仓位快照代替。实际事实与计划匹配必须同时满足 trade_symbol、tool_kind 和 underlying，不能只因 ETF ticker 一致就接受错误公司映射。source.symbol 明确数值区间对应的 observation_symbol。公司估值仍属于公司，不将其数值当作 ETF 或期权自身估值。

当前自动区间引擎只支持 1D。可展示其他观察周期或未决定状态，但没有同周期已核验数据时不生成/确认自动价格区间，不将 1D 指标改标签。实时 quote 只显示相对区间位置，不构成收盘触发证据。默认周期一致属于校验规则，不替用户给现有持仓指定周期。

工具/周期纯规则集中在专用模块，Python 构造/状态/周度/展示验证复用；TS 只渲染已验证字段。价格构造继续拥有 EMA/ATR/结构计算；状态库拥有不可变版本与交易事实；UI 不从名称关键词猜工具，不向数据库写入。

### 11.3 SQLite v5 与历史兼容

只追加下列固定列扩展表，不重写任何旧交易、周度、计划、确认或估值行，无自由 JSON 列：

- `plan_execution_contexts`：plan_id/plan_version 外键、上述固定 context、证据周期/对象；和 plan_versions 在同一事务写入。没有 context 的原计划仍可历史读取，不能用 legacy 路径覆盖新工具 context。
- `trade_instrument_facts`：精确 trades 分区键/版本、market_date/symbol/side、underlying、tool_kind；绑定到同一已校验聚合行，不包含券商 ID 或具体合约。无新工具事实的旧分区不补造，payload hash 与新扩展一起校验。
- `weekly_execution_bases`：review_key/revision 与工具级复盘契约版本；明确区分成功空的工具级复盘与旧标的级历史，避免从有无 episode 行猜版本。
- `instrument_episode_assessments`：review_key/revision、交易日/实际 symbol/方向、underlying/tool_kind、精确 plan_id/version、判断及触发周期/方式与既有覆盖/执行/结果分类。自然键不再仅为 underlying。

新工具级私有输入显式携带 `plan.execution_basis=instrument-episode.v1`，投影为 `trading-review-weekly-state.v3`；成功空周也保留该基准，不能根据 episode 是否有行猜版本。原 v2 历史读取与回滚入口保留，不重算原统计。工具级符合计划须同时验证工具事实、精确确认计划和周期/触发口径；证据不够则 unassessable，不从盈亏判断。每日交易聚合是日/工具/方向粒度，不宣称恢复了每个原始成交或具体期权合约；同一聚合无法区分多个计划时要求人工核对，不能随意选一条。

新分区可携带固定 `instrument` 元信息，采集输入没有该信息时继续保持缺失；真实字段与来源须核验，不靠当前计划反向补事实。加仓资格和日度计划合入均按实际工具匹配；公司正股成交不能为 ETF/LEAP 管理计划提供买入证据。

先在副本演练 v4→v5，再备份实际库、迁移并比较旧表列与逻辑内容。失败事务回滚，备份保留；不删除旧库、版本、发布或日记。代码回滚不得使用不兼容代码打开 v5；HTTP 旧发布只读 HTML，可独立恢复，数据库回滚须同时选匹配备份与代码。

### 11.4 失败与运营边界

| 状态 | 行为 |
| --- | --- |
| 旧数据无工具或周期 | 展示未确认/无法核对，不默认正股或日线，不伪造缺失计划 |
| 工具与成交不匹配、普通期权冒充 LEAP | 不计计划覆盖，不开放加仓，不附着错误 UI 行 |
| 观察/触发周期不同且无例外 | 拒绝新计划或合规结论；已有例外保留明确说明 |
| 非日线或观察对象与证据不符 | 不计算伪造区间，已有日线参考不冒充主周期计划 |
| 已确认后修改 context | 新草案/新版本，不复用旧 hash 或确认 |
| 无完整复盘确认 | 接收器可安装，但无真实入队/日记同步 |
| 日度事实已完成而来源绑定中断 | 同一 run_id 仅在全部运行身份一致且仍为 pending 时允许幂等重放；复用事实/分析后补来源绑定，但不同来源身份的 generated_at 必须严格前进，较早/同时孤儿 run 或已确认 run 均拒绝 |
| 安装/切换失败 | 核验后恢复本任务旧服务，不杀未知进程、不换端口绕过 |

数据新鲜度沿各来源原时点，部署时间不是行情更新时间；周度内容不因本次 UI 更新而重算。0700/0600 与固定本地目录保持，服务只在 loopback。维护所有权沿第 10 节：交易中心负责计划/producer/Web，知识中心负责 Vault 外 receiver，用户负责具体计划和人工验收。

### 11.5 实施顺序、审查与桌面验收

1. 本节按已批准需求形成完整实施记录，独立非作者审查后核实 findings；不委托 reviewer 作架构选择。
2. 实现规则、追加存储和工具级匹配，再改同一套 TS/Python 展示投影；不以换框架、增加面板或批量补历史解决。
3. 以少量端到端回归验证正股/ETF/LEAP 不串计划、跨周期/未收盘不误判、确认版本不被改写、历史迁移不变；运行原必要回归，不以测试条数作为验收。
4. 复用第 10 节私有合格显示输入补可验证的工具信息，未知周期保持待确认；部署本次合并代码、发布同一首页，安装接收器但不真实 sync。切换前记录旧安装/发布和回滚入口，切换后核验 PID、HTML/hash、healthz、历史路由及私有路径隔离。

人工清单沿第 10.5 节单个内置 Browser 标签，`http://127.0.0.1:8765/`，桌面 1440×900、100% 缩放，无手机覆盖。记录本轮实际安装/发布摘要后执行：刷新首页→查看雷达值及 PR→持仓/计划切换→查看工具/观察对象/周期/触发→展开和收起原计划与事件。预期：同页结构不变、未决定项清楚、无后台字段、无自动买卖；Tab/Shift+Tab/Enter/Space 焦点可见，滚动不跳顶，无整页横向溢出。旧持仓不因展示升级变成已确认交易计划。无候选保留空状态；数据不足/陈旧只在受影响项提示；服务错误仍用原失败页，不加载外部业务请求或自动重试采集。冲突/未收盘/中断仅在隔离样本验证，不破坏真实数据。最后清空筛选、关闭展开项、回到顶部，不删除记录。UI PASS/FAIL/备注：____；真实 Bridge NOT RUN/PASS/FAIL：____。

### 11.6 实施、审查与运行证据

- SQLite v4→v5 已先在副本演练，再由 SQLite backup API 保存一份 0600 旧版备份并迁移实际库；四张旧表之外的 v5 固定表已出现，全部迁移前旧表逻辑摘要保持不变。旧记录没有回填：四张新表当前均为零行。
- 新 v2 计划草案把 execution_context 与 source.symbol 纳入完整 content_hash；确认/到期转换原样保存。ETF 观察实际 ETF 时保留经校验的 source.symbol，不再覆写成 underlying。日度合入、初始买入、管理父计划和 v3 周度覆盖都按实际 symbol/tool/underlying 匹配；v2 周度和 legacy plan 仍可历史读取。紧凑期权合约身份在交易、持仓、计划、旧 v1/v2 入口及迁移/当前库读回拒绝，`:OPTION` 只作为脱敏投影，不能证明 LEAP。统一自由文本门禁同时覆盖计划区间、周期例外、episode 说明、分析缓存、估值缺口、持仓原则与所有前端可见文本；打开或迁移数据库时再扫描固定 Schema 的全部 TEXT 列，发现历史泄漏只阻塞、不改写。
- 工具级周度由显式 execution_basis 选择 v3，空 episode 仍写 `weekly_execution_bases`，不再从列表内容猜版本。日度 run 的确切重放可恢复 finish_run 后来源绑定失败的半完成状态；不同身份或已确认 run 不能复用。
- 自动区间只接受主观察周期 1D、source.symbol 与 observation_symbol 相同的输入。输入中的 provider/period 是采集适配器的来源声明；纯计算器能核对完成标志、每纽约日期唯一、窗口、时序和内容摘要，不能从 OHLCV 数值本身密码学证明远端能力调用。该限制保留在计划工作流，不把声明字段夸成独立来源证明。
- 旧 `StateStore.confirm()` 仍是历史低层 API，但 Bridge producer 未调用它。真实 Bridge 只经过 review_journal_state 的来源版本、完整 payload hash、严格 confirmation binding 与 `复盘完成` 文本门禁；安装接收器不会授予写权限。
- 修复证据：针对空 v3、ETF source、三维工具匹配、旧周度隐私、自由文本期权身份泄漏、迁移阻塞、来源绑定恢复和实际交易对象展示的反例均已加入回归；全量 211 项 Python、23 项 TypeScript、`tsc --noEmit`、Skill 校验与 diff whitespace 校验通过。早先的真实 v5 库只读审计只绑定当时修订：不能用它代替本轮独立复核、当前库重验、重新部署或 Browser 重验。

首次独立复核绑定 `codex/review-valuation-obsidian` 基线 `084e05a0e081bc8707be1d3800fb5c3de082f751` 之后的未提交实现。主 Agent 的裁决与最小处理如下：

| finding | 类型、位置与反例 | 影响 | 裁决/当前状态 |
| --- | --- | --- | --- |
| F1 | 已验证缺陷；`review_bridge_receiver.py:Receiver._sync`，pending 期间同字节新 inode 可被误当自身写入 | 可跳过编辑器保护并写成 receipt | **ACCEPTED/FIXED**；写后追加不可变 proof，无 proof 的同字节一律冲突，创建/更新反例通过 |
| F2 | 已验证缺陷；`trading_review_valuation.validate_valuation` 与 `trading_review_portfolio` 公开读写入口可接收具体期权身份 | 突破 underlying-only 投影 | **ACCEPTED/FIXED**；入库、scope、直接读取与管理原则入口共用 US underlying 校验 |
| F3 | 已验证缺陷；`trading_review_instruments.contains_contract_identity`对小写 compact option 自由文本漏检 | 旧库审计与多个文本入口可泄漏具体合约 | **ACCEPTED/FIXED**；大小写无关检测，仅预先剥离完整 SHA-1/SHA-256 以避免误伤审计摘要 |
| F4 | 已验证合同缺口；第 11.2 允许 `1H/4H/1W/null` 展示且无自动区间，但 `construct_trade_plan.py`、`normalize_plan_version`与 v5 持久化只能生成带 1D 证据/区间的计划 | 已批准的非日线/待定观察状态无可持久实现 | **ACCEPTED/PENDING HUMAN DECISION**；不擅自扩 schema，也不擅自缩窄已批准报告 |
| F5 | 已验证缺陷；`normalize_instrument` 的 unknown 工具可绑无关 trade_symbol | 错误工具可附着到其他标的 | **ACCEPTED/FIXED**；unknown 只允许 underlying 或其脱敏 `:OPTION` 投影 |
| F6 | 已验证缺陷；`process_daily_bundle` / `record_daily_source` 在 finish_run 后绑定中断时，旧孤儿 run 可在较新来源后追加 | 最新 lineage 可被旧事实反转 | **ACCEPTED/FIXED**；重放前与写锁内都核验 run 身份与严格前进的 generated_at |
| F7 | 缺失证据；单标的 `calc-index --fields pe` 未有可核对的 PE(TTM) 能力来源 | 估值口径无法审计 | **ACCEPTED/CLOSED BY EVIDENCE**；官方 calc-index 文档明确 `pe` 为 PE (TTM)，且定向回归核对显式单 symbol 命令与 CLI 不可用时 unavailable |
| F8 | 已验证缺陷；`collect_scoped_valuations.query` 未处理 CLI 缺失/启动失败 | 应降级为 unavailable 的边界会中止收集 | **ACCEPTED/FIXED**；`OSError` 与超时/非法 JSON 同样 fail closed |

修复后新鲜 LunaMax 只读复核以上表和定向反例：F1–F3、F5–F8 全部 **CLOSED**，未发现由这些修复直接引入的 P0–P3 缺陷；F4 保持 **OPEN / PENDING HUMAN DECISION**。它判定当前修订可形成“不部署、不处理 F4”的安全 partial checkpoint，不构成部署放行、真实 Bridge 首写或人工验收。reviewer 的隔离边界未访问真实状态库、Vault、Library 或私有运行目录；主 Agent 的全量 211/23 自动回归与官方文档核对是分开证据。

批准状态：需求与本地部署 **APPROVED**；F1–F3、F5–F8 修复后独立复核 **PASS**；F4 最小展示方案人类批准、独立架构复核与实现自动检查 **PASS**；本轮重新部署 **PENDING**；真实日记首写 **NOT RUN**；人工验收 **PENDING**。

### 11.7 F4 最小处理：复用现有展示合同

用户先选择了独立持久化方案 A，随后在看到 v6 迁移、版本链、hash、时钟和新投影协议的实际复杂度后，明确要求按 Ponytail 收缩，并确认本节的最小方案。目标只剩一个：页面能如实显示当前输入中已经存在的实际工具、观察对象、观察周期、触发周期和触发方式，同时不把它们伪装成交易计划。

本轮决定：

- 不新增 SQLite v6、状态表、迁移、备份协议、生命周期 CLI、后台进程或依赖。
- 复用现有 `ExecutionContext` / `CONTEXT_FIELDS` 和 `trading-review-display.v1`；上下文只来自当次通过 Python fail-closed 校验的日度私有输入，并随内容寻址 publication 固化。它不是跨日独立状态源，也没有单独版本历史。
- renderer 先读 `plan_detail.execution_context`，没有时才读行级 `execution_context`，并始终显示观察周期、触发周期和触发方式。无计划但有合法行级 context 时，只显示“观察口径（非交易计划）”；null 周期显示“待确认”，且不展示 zone、plan coverage、near-trigger、trigger distance、买卖信号或计划检查。
- 工具、trade_symbol、underlying 和 observation_symbol 继续复用现有结构化匹配与具体期权身份拒绝；Python 的美股展示边界允许普通 `.US` ticker 与脱敏 `.US:OPTION` 投影，但仍拒绝具体期权合约。不匹配输入在 Python 边界拒绝，TS 不猜测、不修复。
- 本轮不生成或修改 weekly episode、周度统计或合规结论；既有周度区域只渲染另一路已验证周度状态，不根据当前日度 context 重算或压制。
- 历史 publication 继续返回原不可变 HTML；新输入不会回填旧发布。部署时间不改行情、估值或周度来源时间。
- 最小实现只触碰既有 Python 展示边界/渲染、TS 渲染及各一个定向回归；不改 StateStore、计划构造、确认、Bridge、outbox、receiver 或 Obsidian。

已接受的限制：新的日度输入若没有携带上下文，页面只能显示未知/待确认；本轮不自动沿用上一日上下文。只有真实重复使用证明这造成持续操作成本时，才重新讨论独立持久化，并重新经过人类架构决策。

失败与回滚边界沿现有 v5/display v1 合同：非法 context 整个投影 fail closed；只有 context 的行不得显示计划派生内容；删除本轮展示代码并回到上一 publication 即可回滚，无数据库恢复。自动检查只需证明合法 4H/null 能显示但不产生计划内容、计划 context 优先且触发周期完整、脱敏 LEAP 行保留、非法工具映射被拒绝、既有 plan 展示不回归。人工验收继续执行第 11.5 节的单标签 Browser 清单，重点记录“有计划 / 仅观察口径 / 周期待确认”三种状态；当前真实数据没有的状态记 **NOT RUN**，不为验收修改真实交易或确认。

新鲜非作者 LunaMax 对精确 diff `b9726734c53c711c808ea2eb57acf67e6b034cb9e82eebc61b7dc4adec38572e` 的窄范围只读复核为 **ARCHITECTURE REVIEW PASS**；该结论只覆盖本节合同，不代表实现、部署、Browser 或人工验收通过。

实现仅修改两个 Python 展示文件、一个 TS renderer 和各一个定向测试：无计划 context 现在显示“观察口径（非交易计划）”并压制计划派生内容；plan 内 context 优先；触发周期始终显示；脱敏 `.US:OPTION` 不再被 Python 投影删除。全量 212 项 Python、24 项 TS、`tsc --noEmit`、Skill 校验与 diff whitespace 检查均 **PASS**。这些是自动证据；本轮部署 **NOT RUN**，Browser 与人工验收继续 **PENDING**。

## 12. 收盘口径市场环境判断（2026-09-01 用户已确认）

> 历史基线：本节的页面自行拼接定价信号方案已由第 13 节 LongbridgeAI 收盘环境判断修订取代；保留本节用于说明此前的收盘口径、回滚边界和已发布基线。

### 12.1 目标、决定与替代方案

目标是在不恢复原“盘前判断”冗余内容的前提下，为市场风险雷达补一个稳定、可复盘的整体市场判断。用户明确选择上一美股交易日收盘口径，并批准本次及后续日度复盘只读六个既有代理的完成日线。

当时决定：顶部恢复双栏，左侧保留客观收盘雷达，右侧显示“环境结论、主要定价信号、跨资产确认、下一交易日观察”。它是市场价格隐含的收盘环境判断，不是实时宏观模型、不证明价格变化的因果，也不包含持仓、计划、周度纪律或具体买卖动作。现行展示字段和分析来源以第 13 节为准。

已拒绝：固定盘前时点会迅速过期；持续刷新会引入调度、来源时间和页面身份歧义；把现有夜盘 quote 改标签会伪造收盘口径；本阶段引入 CPI、就业、政策利率和新闻数据会扩大来源、证据与维护边界。以上方案如未来重启，必须重新讨论数据新鲜度、权限、失败和回滚。

### 12.2 数据、展示与责任边界

- 数据只来自 `SPY.US`、`QQQ.US`、`IEF.US`、`GLD.US`、`USO.US`、`IBIT.US` 最近两根已完成 1D bar，固定 `adjust=none`、`session=intraday`；查询最多回看 14 个自然日，每次日度复盘最多一次。不新增标的、数据源、盘前/夜盘 quote、持续调度或账户读取。
- `market.basis=completed_close`、`market.market_date=meta.review_date`，六项 complete 行必须标记“收盘 / 已完成收盘”，bar 时间换算到 America/New_York 后等于 review_date。SPY/QQQ 同日收盘不齐时不形成结论；其他代理缺失时只降低跨资产确认。
- 判断由固定、可复算的收盘投影生成：权益方向以 SPY/QQQ 为核心，IBIT 只作高波动风险偏好确认，IEF 只作国债价格/利率压力代理，GLD 与 USO 保留多义性。所有表述使用“定价、可能、观察”，不写成已证明的宏观驱动。
- Python 是 Schema、日期、字段和失败门；TS/Python renderer 生成同一静态 DOM；HTTP 和浏览器刷新只读取内容寻址 publication，不读取行情。后台 status 继续私有校验，前台不显示状态列或徽标。
- `trading-review-display.v1` 只增加 market 内可选字段，不增加 SQLite 表或迁移。旧 publication 不回填；旧输入无 environment 时继续全宽显示雷达。

来源边界见 [授权与数据边界](../../skills/trading-center-review/references/authorization-and-data-boundary.md)、[Longbridge 看板数据契约](../../skills/trading-center-review/references/longbridge-dashboard-data-contract.md)、[V2 看板契约](../../skills/trading-center-review/references/dashboard-visualization-contract.md) 和 [本地发布边界](../../skills/trading-center-review/references/local-web-service.md)。

### 12.3 状态、边缘情况与失败矩阵

| 情况 | 结果 | 发布行为 |
| --- | --- | --- |
| 六项均有 review_date 与前一完成收盘 | 雷达和环境判断 complete | 可生成新静态页 |
| SPY 或 QQQ 缺失/日期不一致 | 显示“本次不形成市场环境判断” | 可保留 partial 事实，不生成方向性结论 |
| IEF/GLD/USO/IBIT 任一缺失 | 核心权益结论可保留，明确跨资产确认不足 | 整体 market/environment 保持 partial |
| Longbridge 命令失败、超时或非 JSON | 不产生新展示快照 | 当前成功 publication 不变 |
| 缺少前一完成 bar、重复交易日或非正数 close | 对应事实拒绝 | 不用旧 quote、计划或测试数据补齐 |
| 周末、假日或半日市 | 只相信上游 review_date 与 bar 的纽约日期 | 不假定固定 16:00 时间，不用工作日猜交易日 |
| 浏览器刷新、服务重启 | 重新读取同一 publication | 不改变 market_date、行情或周度时间 |

原始响应只在进程内解析或位于 owner-only 私有运行区；Git、HTML 和发布快照只含固定公开代理投影。脚本无账户、交易或写券商能力。维护所有权仍由交易中心任务负责采集/投影/发布，用户负责判断是否有用及人工验收。

### 12.4 实施、迁移、回滚与未决风险

实施为一个独立功能分支检查点：先扩展 optional display contract 和双 renderer，再增加固定六标的刷新器，运行最小定向验证后生成新的私有展示快照与不可变历史路由。无数据库迁移、自动化修改、服务安装、重启、Bridge 或 Vault 变更。

回滚优先把 `/` 切回上一成功 publication；源码回滚使用 revert，不重写历史。旧快照因 optional 字段仍可由新 renderer 读取；若新收盘字段校验失败，发布原子门保持现有页面。私有运行工件不自动删除。

已知限制：单日 ETF 收盘只能表达市场定价，不能覆盖完整宏观 regime；ETF tracking、分红/拆分、黄金/原油/比特币的多义性和盘后新事件都可能使次日环境改变。本组件通过明确日期和条件式观察暴露这一限制，不做实时修正。需求和读取范围 **APPROVED**；4 项定向 Python、1 项 TS/Python DOM 对齐、TypeScript typecheck 与 diff whitespace **PASS**；真实六标的收盘读取、发布和 HTTP hash readback **PASS**；独立架构复核在合并前 **PENDING**；浏览器与人工验收 **PENDING**。

### 12.5 单标签桌面人工验收清单

目标构建：分支 `codex/dashboard-frontend-cleanup`；历史路由 `/market-close-environment-20260901/`；publication_id=`2553ac19ff698c3d7eedec4b3004a4f7a179a61390ab484c758f8446b5c7beeb`。起始状态：只保留一个内置 Browser 标签，打开该路由，正常桌面宽度不小于 775px、100% 缩放，页面顶部，无筛选、无展开项。测试数据为 2026-08-31 ET 六个公开代理收盘；不为验收修改账户、持仓或计划。

1. 顶部日期仍为 2026-08-31 ET，边界条显示“收盘口径 2026-08-31（ET）”，不得显示盘前或夜盘口径。
2. 第一屏左侧有六行市场雷达，列仅为资产/指数、最新值、涨跌幅；右侧有“市场环境判断”，顺序为结论、三组定价信号、跨资产确认、下一交易日观察。不得出现状态列、完成徽标、待确认事项、周度判断或后台字段。
3. 在该桌面宽度双栏应同时可读，不横向溢出、不截字、不出现过密空白；滚动到成交、持仓计划和事件区，确认原有层级与内容未被顶部改动破坏。
4. 用鼠标和键盘分别切换持仓/买入计划、勾选“只看接近触发”“只看待确认”、展开一项计划和一个事件日期；焦点环清楚，页面不跳到错误位置，取消筛选可恢复全部。
5. 刷新同一标签：日期、六项收盘、判断文字和滚动默认状态保持静态；刷新不出现新行情、不改变周度生成时间。返回历史路由后内容仍一致。
6. Loading 不适用（静态本地 HTML）。如果真实输入碰到部分缺失，应按第 12.3 节显示 partial 文案；本次数据没有对应状态则记录 **NOT RUN**，不伪造缺失。命令失败/错误状态应保持上一成功页；stale 仅保留原日期，不升级为新收盘。
7. 只检查桌面场景，不做手机/窄屏验收。Console 不应有异常；Network 在初始本地 HTML 后不应出现外部请求，页面无脚本、iframe、外部字体或图片。
8. 重置：取消两个筛选，切回“当前持仓及计划”，关闭所有 details，滚回顶部；不要删除 publication、私有运行工件或历史路由。

记录：双栏与文案 PASS/FAIL ______；鼠标/键盘/焦点/滚动 PASS/FAIL ______；刷新与历史路由 PASS/FAIL ______；Console/Network PASS/FAIL/NOT RUN ______；partial/error/stale PASS/FAIL/NOT RUN ______；总体验收 PASS/FAIL ______；备注 ______。

## 13. LongbridgeAI 收盘环境判断修订（2026-09-01 用户已确认）

### 13.1 决策与范围

用户指出 Trade Center 的宏观财经日历一直使用既有 Longbridge Skill/适配器链路，并明确要求复用已安装的 LongbridgeAI 分析 Skill；事件日历、宏观字段筛选和下周事件覆盖不在本次改动内。右侧环境判断因此从页面自行拼接的三组代理信号与“跨资产确认”，收缩为一次 LongbridgeAI 公共分析调用的简单结构化结果：收盘结论、最多三条支持事实、一个下一交易日验证条件。

固定输入仍是 `SPY.US`、`QQQ.US`、`IEF.US`、`GLD.US`、`USO.US`、`IBIT.US` 的同一 `review_date` 与前一完成 1D bar；六项齐备才调用 LongbridgeAI。固定提示禁止账户、持仓、订单、资金、买卖指令和无界宏观叙事，要求返回同一日期的 JSON。原始回答和引用只在进程内解析，不进入 Git、HTML 或发布快照。

信任边界是：提示只发送收盘日期和公开市场分析问题，结果只在本地做日期、字段、长度和隐私语义校验后展示。当前检查点没有机械证明 LongbridgeAI 的每条支持事实都能由六个本地收盘字段逐条复算，因此这一点保留为用户可见的信任判断与后续增强项，不把 LongbridgeAI 文案当作新的行情事实来源。

### 13.2 合同、失败与迁移

- `market.environment` 由 `status/headline/evidence/next_session_watch` 组成；`evidence` 最多三条，complete 至少一条。六项收盘任一缺失时保留 partial 雷达和“本次不形成判断”的明确文案，不调用分析 Skill。
- 事件仍由既有 `finance-calendar`、`macrodata` 和已批准的讲话排期链路生成；本次不新增数据提供商、不改变时间范围、不改事件筛选，也不把 LongbridgeAI 当作财经日历来源。
- Longbridge 收盘命令、LongbridgeAI 调用、JSON 字段、日期绑定或隐私校验失败时，不生成新展示快照，上一成功 publication 保持不变。回滚可删除本次 renderer/刷新器改动或把根路由切回上一 publication，无数据库迁移。
- Python 负责输入、日期、隐私、状态与失败门；Python/TS renderer 共享同一静态 DOM；页面不显示后台状态、Skill 名称、原始回答或引用。

### 13.3 实施阶段与验收

本检查点只修改收盘刷新脚本、Python/TS 类型与 renderer、模板样式、相关契约和定向测试；不改 StateStore、事件采集、计划构造、确认、Bridge、Vault、调度或数据库。自动核验只运行最小 Python/TS 定向测试、TypeScript typecheck、语法和 diff 检查，并用一次真实 LongbridgeAI 收盘刷新生成新的私有快照与本地历史 publication。独立架构复核在合并前 **PENDING**；分支推送后由用户在浏览器确认文案、双栏布局、刷新静态性及事件链未受影响，人工验收保持 **PENDING** 直至用户明确记录 PASS。

### 13.4 单标签人工清单（本修订）

目标构建：`codex/dashboard-frontend-cleanup`；从最新本地 publication 打开一个内置 Browser 标签，桌面宽度不小于 775px、100% 缩放。确认：

1. 右侧只出现“市场环境判断”、一段结论、最多三条“支持事实”和“下一交易日验证”；不出现三组定价信号、跨资产确认、后台状态、LongbridgeAI 原始回答或引用。
2. 标题日期与左侧六项雷达都绑定同一收盘日；刷新页面不重新请求行情、不改变文字或周度时间。
3. 左侧雷达仍只有资产/指数、最新值、涨跌幅；事件区仍显示当前周及下周既有事件，讲话事件不被本次判断过滤。
4. 检查成交、持仓计划、两个筛选和事件展开，确认顶部改动未影响原交互；Console 无异常、初始 HTML 后无外部 Network 请求。

记录：LongbridgeAI 文案与日期 PASS/FAIL ______；雷达/事件链 PASS/FAIL ______；刷新与交互 PASS/FAIL/NOT RUN ______；Console/Network PASS/FAIL/NOT RUN ______；总体验收 PASS/FAIL ______；备注 ______。

## 14. 上一交易日成交结构化对齐（2026-09-02 已批准实现边界）

本检查点只收窄既有 operations 展示契约：成交卡行级输出 `side`、`trade_type`、`option_right`、`plan_status`、`plan_status_note` 与 `execution_count`。允许的交易类型保持为 `stock`、`single_stock_leveraged_etf`、`long_call`、`zero_dte_option`、`other_option`、`unknown`；计划状态保持为 `confirmed_plan`、`mismatch`、`outside_plan`、`unknown`。结构化行不再承载旧 `action`、`role`、`state` 或 `plan_relation` 自由文案。

旧展示快照仍可读，但在 Python 展示边界统一降级为中性 `other + unknown + unknown`，名称只由安全 symbol 派生，不能从旧文案推断工具、方向、Long Call 或计划关系。`long_call` 只有在期权右侧、实际工具和成交前已确认且已生效的精确计划一致时才写入；同一 underlying 的不同工具不共享覆盖。0DTE 只用 owner-only 原始执行时间和到期编码在内存中机械判断，输出永不包含具体合约身份、到期日、价格、数量、上游 ID、成本或佣金。确认/生效时间、工具/对象、方向或管理阶段证据不足为 `unknown`，已有生效计划但工具/方向/阶段不一致为 `mismatch`，明确不存在匹配的事前计划才为 `outside_plan`。

`refresh_daily_operations.py` 复用已有 owner-only 成交工件与 SQLite v5 只读连接，严格拒绝混合 review date、未知结构、总数不一致和覆盖既有输出；成功结果通过 `trading-review-display.v1` 校验后才可进入现有 publish 入口。它不调用 Longbridge、不迁移或写数据库、不修改计划、Bridge、Obsidian、自动化或服务绑定。失败保持旧 publication；源码回滚使用精确 revert，页面回滚使用既有上一成功 publication，数据库不回滚。

本实现对应 Python/TypeScript 同一静态 DOM：成交行只渲染安全方向、类型/右侧、计划关系和说明。合成回归覆盖 0DTE Call/Put、普通期权无计划、精确 Long Call 计划、同 underlying 错工具、工具证据不足、计划生效晚于成交、零/缺失/partial/stale/expired、隐私过滤和输入不变性。自动测试不等于浏览器或人工验收；本节人工验收保持 **PENDING**，除非用户针对确切 commit/build 明确记录 PASS。
