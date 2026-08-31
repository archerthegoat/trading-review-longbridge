# 交易中心复盘：单页日/周增量、交易计划与 SQLite v3 架构

> 权威位置：`docs/architecture/trading-center-skill-incremental-state.md`  
> 基线：`codex/trading-review-semantics@3e9bafb`  
> 实施分支：`codex/trading-review-incremental-state`  
> 审批记录：用户于 2026-08-30 确认保留既有每日 Web UI V2 作为唯一页面骨架；周度只注入执行质量与下周增量；交易计划使用 Longbridge 已完成日线、EMA20/50/200、右侧确认的 `bottom_reversal`、`draft→confirmed` 门和买入后再生成的 `position_management` 加仓草案；SQLite v2→v3 只追加迁移。  
> 真实迁移授权：2026-08-31，用户针对准确数据库路径、同目录 0600 备份、仅四表追加和版本元数据更新的明确请求回复“允许”；已按此执行并完成回读。  
> 本轮运行授权：2026-08-31，用户确认升级官方 Longbridge 0.28.0 arm64 并重新运行 W35；真实重跑与统一页面证据见第 17 节。  
> 原实施阶段未授权：生产切换、自动化修改、Plugin 安装、yfinance 回退、期权合约价格计划、券商写入、push、PR、发布或知识库迁移；后续 Git 收敛授权另见第 19 节。  
> 人工状态：新单页 UI 与计划构造能力的人工浏览器验收为 `PENDING`。

## 1. 目标与验收对象

本项目不是盈亏看板。目标是形成一条可审计的行为闭环：

1. 用受限、已验证的 Longbridge 已完成日线构造条件式交易计划草案。
2. 由用户确认不可变计划版本，禁止事后补计划或行情变化后静默移动区间。
3. 每日页面检查最新事实与既定计划的关系，但不改写计划，也不写周度 revision。
4. 周度运行统计计划覆盖率、按计划执行率、计划胜率和需复盘交易数。
5. 亏损但按计划止损与盈利但违反计划必须分开判断。
6. 只有具体异常交易进入明细；主界面不重复券商已有的具体盈亏、收益率、TWR、现金流或完整归因。
7. 日度与周度共用同一个 renderer、template、`<main>`、视觉组件和数据说明，不建立独立周度 panel。

验收分为四个独立结果：实现、自动检查、真实运行回读和用户人工浏览器 PASS。自动检查或历史 W35 页面不能替代用户 PASS。

## 2. 当前已验证事实与状态缺口

- 健康基线 commit 为 `3e9bafb`；当前分支和共享工作树已有大量未提交改动与删除，实施必须保留无关内容。
- 既有每日 V2 的权威顺序为：市场风险雷达、Codex 盘前判断、昨日操作摘要、持仓 × 计划、重要事件与时间轴、数据说明。
- 迁移前 renderer 把 daily 与 weekly 放入两个 panel，并以整页 radio 切换；旧周度树展示收益、归因和现金流。本次实现已移除这条渲染路径，旧输出不作为新版本证据。
- owner-only SQLite 位于 `/Users/archer/Library/Application Support/MarsTradingCenter/trading-review.sqlite3`；2026-08-31 经明确授权由 v2 升至 v3，旧事实保持不变，同目录 v2 备份已验证。
- W35 最新 revision 2 为 `partial / current / pending`；历史 revision 1 保留，并因依赖分区新增修订而派生 stale。最新计划模块仍 blocked，没有事前已确认计划或足以分类执行与结果的事实。
- 仓库及工作树中没有可用的 `records/weekly/` 已确认计划，因此 W35 的计划覆盖率、执行率和计划胜率都必须保持 blocked，不能回填。
- 现有脱敏交易聚合没有成交价格；周度评估只能在当次 owner-only 私有运行目录内使用原始执行事实，数据库仅保存分类标签。
- 正式复盘契约现已列入受限的 Longbridge `kline history` 计划证据能力。2026-08-31 经用户确认已升级至匹配持久分区来源契约的 0.28.0 arm64；授权范围内美股 underlying 的已完成日线和 EMA/ATR/结构参考通过数据检查。这些参考不是已构造或已确认的买卖区间，计划参数和已有仓位的事前计划仍有缺口。具体持仓数量仅留私有验收证据，不随源码发布。
- 全局技术分析 Skill 的执行器固定为 yfinance；本项目只复用其数据质量、ATR、关键位 provenance 和失败关闭思想，不调用该执行器，也不修改全局 Skill。

## 3. 已选架构与取舍

### 3.1 单页日/周增量

- HTML 只有一个 `<main>`，既有每日页面是唯一骨架。
- 周度市场背景注入“市场风险雷达”；周度纪律提醒注入“Codex 盘前判断”。
- 本周操作摘要注入“昨日操作摘要”，保持同一行/卡片语言。
- 四个周度执行质量指标、计划复核和下周草案注入“持仓 × 计划”。
- 下周事件作为同一时间轴的新分组注入“重要事件与时间轴”。
- 每日和周度 freshness 在同一个“数据说明”中分别列出。
- 无周度 revision 时显示“尚未生成”；无合格每日 packet 时 weekly 页面合成 blocked，不允许生成周度专用替代页面。

### 3.2 交易计划构造

计划构造由确定性脚本完成数值计算，Codex 只解释证据、组织条件并向用户展示草案。页面不调用 Agent、数据库或远程接口。

每次构造必须提供：

- 唯一 underlying、方向、交易意图和 setup。
- 持有周期或最大等待交易日。
- 最大可接受失效幅度。
- 用户要求的最低风险收益比。
- Longbridge 返回或明确验证的最小价格跳动。

setup 固定为：

- `pullback`：趋势结构中的回踩确认。
- `breakout`：突破阻力后的确认或回踩。
- `range`：确认震荡结构中的支撑/阻力计划。
- `bottom_reversal`：右侧确认的抄底反转；进入超跌或长期支撑只产生观察区，必须出现止跌/反转确认才产生进入区。
- `position_management`：验证实际买入后生成的持仓管理草案。

### 3.3 EMA、ATR 与关键位合同

- 只使用已完成、adjustment 明确的 1D OHLCV；时区必须明确、时间严格递增、价格有限且满足 `low≤open/close≤high`、成交量为正。
- 剔除末尾未完成日线后至少 319 根；不足、乱序、无时区、复权口径不明或字段不完整时 blocked。
- EMA20、EMA50、EMA200 使用 adjusted close 和固定递推公式 `alpha=2/(N+1)`；首个 EMA 以首个 N 日简单均值初始化，后续逐日递推。证据保留窗口、精度和 bars_used。
- ATR14 使用标准 True Range 和 Wilder 平滑；不得由模型估算。
- confirmed swing high/low 使用左右各 2 根已完成日线确认；按 `0.5×ATR14` 聚类，按触碰次数、确认新鲜度和距最新收盘距离排序；每侧最多两个。
- 区间必须按 tick size 向外保守取整，并保存来源关键位、ATR buffer 和 evidence_id。
- 最新 quote 只计算与已确认区间的关系，不改变区间。

### 3.4 计划区间与状态机

`pre_entry` 计划只允许：`observation`、`entry`、`reduce`、`exit`、`invalidation`。禁止 `add`。

~~~text
技术证据 qualified
        │
        ▼
pre_entry draft ──用户确认──> pre_entry confirmed
                                      │
                         Longbridge 实际买入已验证
                                      │
                                      ▼
                       position_management draft
                                      │
                               用户再次确认
                                      ▼
                       position_management confirmed
~~~

- `draft` 不进入计划覆盖率。
- `confirmed` 记录 effective_at、confirmed_at 和 content_hash，之后不可改写。
- 新确认的 CLI 时间必须来自当前时钟的五分钟窗口；只允许已存储相同版本、hash、时间的确认原样重试，禁止补填历史确认时间。
- 新证据只生成新 draft；旧版本只能 `superseded` 或 `expired`。
- `position_management` 必须引用已验证的初始买入 episode 和原建仓计划版本。
- `add` 只允许存在于 `position_management`；实际买入前或再次确认前的加仓不属于计划覆盖。
- 加仓必须基于原逻辑仍成立和新的有利结构，禁止以继续下跌本身作为加仓理由。
- 初始计划必须在入场前给出失效和减仓/退出边界，才能评估风险收益；加仓区间不属于入场前计划。

### 3.5 被拒绝的方案

| 方案 | 不采用原因 |
|---|---|
| Codex 直接给单一买卖价 | 不可复算、假精确、无法区分观察与触发 |
| 直接调用 yfinance 技术分析 Skill | 违反本项目 Longbridge-only 和禁止 provider fallback 的确认边界 |
| 每天随 quote 移动计划区间 | 破坏事前计划权威，周度无法判断执行纪律 |
| 买入前预设加仓区间 | 容易把下跌自动解释为加仓理由，也无法基于实际持仓状态重新评估 |
| 抄底只看超跌或支撑 | 无法排除持续下跌；必须有右侧止跌/反转确认 |
| 周度显示具体盈亏和归因 | 券商已提供，且会掩盖计划覆盖和执行纪律 |
| 为周度另建 panel、renderer 或 template | 与用户要求和既有每日信息架构冲突，形成两套长期漂移实现 |
| 保存原始执行价或完整券商响应 | 扩大隐私面；机械分类后只需保存标签 |
| 用旧 W35 归因推算胜率 | 标的级收益不是闭合 trade episode，且缺少事前计划 |

## 4. 组件、权限和所有权

| 组件 | 责任 | 明确禁止 |
|---|---|---|
| Longbridge 只读适配器 | 受限 kline、quote、positions、指定窗口 order/execution | 下单、改撤单、全市场扫描、其他 provider fallback |
| 技术证据构造器 | EMA、ATR、swing、关键位、setup 质量门和 evidence_id | 读取账户、生成自由价格、跳过数据质量门 |
| 计划构造器 | 生成结构化 draft、风险收益检查和条件式区间 | 自动确认、静默改区间、pre_entry add |
| 用户确认门 | 将特定 plan_id/version/hash 升级为 confirmed | 用一次确认覆盖未来版本或事后补计划 |
| SQLite 状态层 | 分区、计划投影、episode 分类、周度指标、revision | 原始成交价、成本、佣金、券商 ID、通用 JSON 逃生列 |
| daily runner | 更新每日事实和 quote 距离，只读最新周度 revision | 写入 weekly tables、重算计划区间 |
| weekly runner | 评估 episode、追加周度 revision 和执行指标 | 改写旧 revision、刷新历史盈亏表 |
| Web UI renderer | 校验固定 Schema，生成一个离线单页 | DB/网络/Agent 调用、独立周度页面 |
| `records/weekly/` | 私有、已确认计划真相源；当前缺失时明确 blocked | 被数据库或自动化反向覆盖 |
| 用户 | 计划确认、人工浏览器 PASS、生产切换 | 无 |

## 5. 数据源、范围和新鲜度

- kline 只允许当前持仓 underlying 与已确认候选池，单次最多 20 个标的，每标的最多最近 550 个自然日，不扫描全市场。
- 计划证据只使用 Longbridge；复权模式、时区和完成日线语义必须先通过 CLI 能力核验。
- quote 可每日刷新，用于 `below / inside / above / unavailable` 区间关系；它不是计划生成证据。
- 订单和成交按 America/New_York 半开窗口读取；当前 positions 只是读取时快照。
- 数据源与计划证据分别保存 source、as_of、timezone、adjustment、bars_used、evidence_id 和 data_status。
- corporate action 或 adjustment 变化使相关计划 stale，并产生新 draft；不得自动重写 confirmed 版本。

## 6. 固定数据契约

### 6.1 计划草案

`trading-plan-draft.v1` 只允许：元数据、用户约束、技术 evidence、条件、zones、gap 和 data_status。zone 类型固定，价格为有限十进制定点字符串，不允许 raw bars、broker row 或自由 metadata JSON。

每个 draft 必须说明：为什么观察、什么条件才进入、何时失效、计划如何退出、何时到期。`bottom_reversal` 未确认反转时必须输出 observation-only。

### 6.2 每日 dashboard

`trading-review-dashboard.v2` 保持旧字段兼容；计划行可选扩展 plan_id/version/stage/status/setup/evidence/zones。旧 packet 仍可渲染，但只有扩展字段完整时才显示区间卡片。

### 6.3 周度 dashboard 增量

周度 packet 不再含 performance、attribution 或 cash-flow 展示数据。它只包含：

- period、generated_at、freshness、confirmation_status。
- 四个执行质量指标及各自分子、分母、排除数量、data_status 和 gap。
- 本周操作摘要、需复盘 episode、计划复核、下周草案、周度市场/纪律背景和下周事件。

## 7. SQLite schema v3

SQLite 使用 Python 标准库、WAL、foreign_keys、busy_timeout=5000、单写者和 owner-only 路径门。v1/v2 表与行全部保留；v3 只追加：

| 表 | 主键 | 允许字段摘要 |
|---|---|---|
| plan_versions | plan_id + version | stage、underlying、direction、setup、status、generated/effective/confirmed/expires、evidence provenance、ATR、风险约束、hash、父计划与初始买入派生键、data_status |
| plan_zones | plan_id + version + zone_order | zone_kind、low/high、currency、condition、derived_from、data_status |
| trade_episode_assessments | review_key + revision + episode_index | market_date、underlying、side、plan reference、coverage、compliance、outcome、deviation_type、reason、next_rule、data_status |
| weekly_execution_metrics | review_key + revision | eligible、covered、assessable、compliant、resolved、success、open、flat、unverifiable、review_needed、三项 rate、data_status、gap |

约束：

- `plan_zones.add` 只允许引用 `plan_versions.stage=position_management`。
- `position_management` 必须引用父计划与已验证的初始买入派生键；该键不是券商 ID。
- confirmed 计划必须有 confirmed_at/effective_at；draft 不得有。
- 原始成交价格、订单/成交 ID、成本、佣金、完整券商响应、具体期权身份、凭据和通用 raw/metadata JSON 永久禁止。
- `weekly_performance`、`weekly_attributions`、`weekly_cash_flow_aggregates` 保留历史只读；v3 新运行不写入，单页 UI 不读取。

## 8. 周度指标与 episode 定义

eligible trade episode 固定为 `America/New_York 市场日期 × underlying × side`；拆单和期权先聚合到 underlying。取消未成交、FX、IPO 和公司行动排除。

- 计划覆盖率 = 有事前 confirmed 计划的 eligible episode / eligible episode。
- 按计划执行率 = fully compliant episode / assessable covered episode；必须与覆盖率同屏。
- 计划胜率 = success / (success + failure)。open、flat、unverifiable 不进入分母，但显示数量。
- 需复盘包括 non_compliant、计划 failure、盈利但违规、无法机械判断但影响纪律结论的 episode。

分类必须允许：

- `compliant + failure`：按计划止损或失效，但计划结果失败。
- `non_compliant + success`：结果有利但违反计划，仍需复盘。
- `unassessable`：缺成交价/时点或规则无法机械验证，不进入执行率分母。
- `uncovered`：交易前没有 confirmed 计划，不得事后关联。

当前持久化的 episode 只有市场日期，没有精确成交时间。因此只有计划在该纽约市场日全日有效时才能机械认定 covered；当日先确认后成交但缺少可核验时序的情况不猜测覆盖。任意自然语言规则仍由上游基于当次执行证据分类；数据库只检查分类契约、计划版本、事实集合和指标计算，不把无法机械验证的规则自动判定为合规。

## 9. 状态与失败矩阵

| 场景 | 持久化行为 | 页面行为 |
|---|---|---|
| kline 不足 319 根、乱序、无时区或 adjustment 不明 | 不写 qualified evidence/plan | 计划构造 blocked，显示精确 gap |
| bottom_reversal 仅进入支撑/超跌区 | 可写 observation-only draft | 不显示可执行进入区 |
| 风险边界或最低风险收益比缺失 | draft 不可确认 | 显示待补约束 |
| 风险收益不达用户门槛 | 保存 no-action 解释，不生成 confirmed 计划 | 显示“不行动” |
| pre_entry 出现 add zone | Schema 拒绝，事务回滚 | blocked |
| 未验证实际买入却生成 position_management | Schema 拒绝 | blocked |
| quote 过期或缺失 | 保留 confirmed 区间不变 | 距离显示 stale/unavailable |
| corporate action/复权改变 | confirmed 计划保留，派生 stale | 等待新 draft 与用户确认 |
| 跳空越过 entry/stop | 不假设成交或止损价格 | 显示 gap risk、执行状态待事实确认 |
| 原始执行事实缺失 | 只写 unassessable 标签 | 不进入执行率分母 |
| 无事前 confirmed 计划 | 写 uncovered | 覆盖率分母保留该 episode |
| W35 历史 revision 缺计划与成交价 | 不回填、不猜测 | 三项指标 blocked |
| daily 依赖修订 | 不改旧 weekly revision | freshness=stale |
| SQLite v3 迁移失败 | 回滚并保留 v2 与 0600 备份 | blocked，不生成成功新页面 |
| 无合格每日 packet | 不生成周度专用 HTML | blocked |
| HTML Schema/隐私/CSP 失败 | 不写成功 HTML | blocked |

## 10. 安全、可观测性、可访问性与维护

- 原始 Longbridge 响应只在 `/private/tmp/trading-center-review-runtime/<run-date>/<run-id>/`，目录 0700、文件 0600。
- DB、WAL、SHM 和迁移备份 0600；拒绝符号链接、Git、Vault、`/private/tmp` 数据库和权限过宽路径。
- SQL 全参数化；固定枚举和列级白名单；无通用 JSON 逃生列。
- standalone HTML 无 script、iframe、外部字体/图片、fetch/XHR/WebSocket 或页面持久状态。
- manifest 记录 run、period、分区状态、hash、schema version、weekly freshness、evidence/plan version和工件权限，不记录账户数值或券商 ID。
- 原生 radio/checkbox/details 必须有 label、visible focus、键盘可操作和稳定 scroll；窄屏不得产生页面级水平溢出。
- Skill 仓库维护 Schema、迁移、构造器、runner、renderer 和测试；自动化任务维护调度；用户维护计划确认、人工 PASS 和生产切换；知识中心任务独占 Obsidian 写入。
- 首版不自动删除私有运行目录或迁移备份；清理策略另行批准。

## 11. 迁移、回滚与实施阶段

### 11.1 v2→v3

1. 对真实 v2 DB 做只读 schema、权限、table counts、quick_check 和 foreign_key_check 基线。
2. 创建同目录 owner-only SQLite backup。
3. 在单个 `BEGIN IMMEDIATE` 中创建四张 v3 表，更新 schema_meta 与 user_version=3。
4. 校验精确表/列、CHECK/FK、旧表行数与逻辑 hash 不变。
5. 任何失败均回滚；旧自动化继续使用 V1/V2 入口。备份不自动删除。
6. W35 不回填计划指标；旧 P&L 表只保留历史。

### 11.2 源码阶段

- Phase A：更新路线图、架构、数据源与计划契约。
- Phase B：实现 Longbridge 日线质量门、EMA/ATR/关键位和五类 setup 的 draft 构造器。
- Phase C：实现 SQLite v3、计划/episode/指标校验和迁移测试。
- Phase D：把 weekly packet 收敛为增量，并重构 renderer 为一个 `<main>`。
- Phase E：自动测试、真实 DB 迁移回读、W35 truthful shadow、内置 Browser 自动检查。
- Phase F：提交用户人工验收；PASS 后仍需单独批准自动化切换。

## 12. 完整人工验收清单

### 12.1 起始状态

- 分支：`codex/trading-review-incremental-state`；HEAD `3e9bafbbd56994a5005254d272b3fb75f8f9ed6b` 加本次未提交工作树。不能只凭 HEAD 认为构建相同；本次关键源码指纹列于第 15 节。
- 使用实施后 renderer 生成的新私有 HTML；不得打开旧 W35 双 panel 页面。
- 私有运行目录 0700，daily JSON、weekly JSON、HTML 和 manifest 均为 0600。
- 复用一个 Codex 内置 Browser 标签页；先关闭旧本地预览页，不开多个标签。
- 测试数据全部为合成示例，不含真实账户、成交或行情。主页面涵盖 confirmed pre_entry、bottom observation-only、position_management draft；独立状态 fixture 仍使用同一 renderer 和单页骨架，不是新增产品板块。

### 12.2 启动与 URL

内置 Browser 的直接 `file://` 打开受策略限制；本次页面检查使用已验证的 loopback 预览，不尝试绕过文件访问策略。人工检查在仓库终端执行：

~~~bash
/usr/bin/python3 -m http.server 8765 --bind 127.0.0.1 --directory /private/tmp/trading-center-review-runtime/2026-08-31/unified-review-v3/preview
~~~

起始 URL：`http://127.0.0.1:8765/trade-review-dashboard.html`。复用同一标签，按下表替换路径；不刷新市场数据，不写 DB。如果端口被占用，先确认已有进程用途，不终止未知服务。

| 页面路径 | 合成状态与预期 |
|---|---|
| `/trade-review-dashboard.html` | 覆盖率 100%、执行率 66.7%、计划胜率 66.7%、需复盘 2；只代表 fixture，不代表 W35 真实结果 |
| `/blocked-metrics.html` | 四项均“不可计算”，缺口清晰，不显示假 0%/100% |
| `/daily-only.html` | 同一页面显示周度尚未生成 |
| `/empty.html` | 日度空状态不是数据读取失败；周度仍可保留自己的窗口内容 |
| `/partial.html` | 缺口可见，不能显示为完整数据 |
| `/stale.html` | 显示原周度更新时间与依赖陈旧，不伪造新的更新时间 |
| `/management-confirmed.html` | 买入后管理的 UI fixture 为已确认 v2；与主页面待单独确认草案对照，无真实确认写入 |
| `/quote-inside.html` | 仅最新 quote 关系改为区间内；区间、计划版本、证据保持与主页面一致 |

若临时文件已被系统清理，在仓库根目录执行 `PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 tests/build_unified_review_preview.py --output-dir /private/tmp/trading-center-review-runtime/2026-08-31/unified-review-v3/preview` 重建合成页面。该命令不读取真实 DB，不访问券商。

### 12.3 视口与缩放

- 1440×900，100%。
- 1280×800，100%。
- 390×844，100%。
- 每个视口记录 PASS/FAIL、问题位置和截图路径；不使用浏览器缩放掩盖布局问题。

### 12.4 顺序任务

1. 确认页面只有一个 `<main>`，没有“每日/周度”整页切换，也没有独立 weekly panel。
2. 从顶部顺序阅读：市场风险雷达、Codex 盘前判断、昨日操作摘要、持仓 × 计划、重要事件与时间轴、数据说明；顺序与原日度一致。
3. 检查每日日期/cutoff 与最新周度 period/freshness 同时可见且不混淆。
4. 在市场与 Codex 卡片中辨认小型周度背景，不应出现复制的第二套市场或判断模块。
5. 在昨日操作中辨认本周摘要；不得出现具体盈亏、收益率、TWR、现金流或归因。
6. 在持仓 × 计划顶部核对四张卡：覆盖率、执行率、计划胜率、需复盘数；逐张阅读其分子、分母、排除项和状态。
7. W35 历史数据应显示三项指标 blocked，不能显示 0%、100% 或用旧 P&L 推断。
8. 切换“当前持仓/交易计划”，再开启“只看接近触发/只看缺口”；用鼠标、Tab、Shift+Tab、Space/Enter 检查焦点、筛选和恢复。
9. 查看一个 confirmed pre_entry：应有 observation、entry、reduce/exit、invalidation，没有 add。
10. 查看 bottom_reversal observation-only：没有止跌确认时不得显示可执行 entry。
11. 查看 position_management draft UI 示例：可以有 add，但明确待单独确认；同一标签导航至 `/management-confirmed.html` 后才显示已确认 v2。此步骤只验 UI 状态，实际买入证据门由自动化测试验证。
12. 在主页面记录第一条计划的区间与版本，再导航到 `/quote-inside.html`：关系变为区间内，但区间值和版本不动；原始 evidence/hash 的不可变性由自动化测试验证。
13. 阅读需复盘 episode：只含日期、underlying、计划、实际分类、偏差、原因和下一规则，不含成交价或 P&L。
14. 在同一事件时间轴中检查“近日/下周”分组、双时区、状态、来源与风险通道。
15. 展开数据说明，分别核对 daily 与 weekly freshness、数据缺口和边界；不出现内部脚本、agent、reviewer 或 Schema 调试噪音。
16. 从页面中部切换筛选和 details 后确认焦点不跳顶、滚动位置稳定；窄屏长标的、条件和区间不重叠。

### 12.5 状态、数据库与重置

- complete 只给已验证事实方向色；partial/stale/blocked 不冒充 complete；empty 与读取失败严格区分。
- daily-only 显示周度尚未生成；weekly 输入没有 daily 时生成阶段 blocked。
- pre_entry add、无 initial-buy 的 position_management、confirmed 缺 confirmed_at、未知字段和敏感值均在写入前失败。
- 同一 confirmed plan version/hash 重试幂等；新 evidence 只生成新 draft。
- 修改 daily partition 后旧 weekly 只派生 stale，不增加 weekly 行。
- v2 副本迁移 v3 后旧表计数/hash 不变；模拟 DDL 失败后 user_version 仍为 2 且备份可读。
- Console warning/error 与 CSP 违规为 0；页面级水平溢出为 0；Network 不得访问外部站点。浏览器自身请求的本地 favicon 不应误算为远端业务请求。
- 完成后停止 loopback server，恢复测试 fixture/临时状态；不删除真实 DB、备份或 shadow 工件。

记录格式：

| 项目 | 视口/状态 | PASS/FAIL | 备注/截图 |
|---|---|---|---|
| 单页顺序 |  |  |  |
| 执行质量指标 |  |  |  |
| 计划区间与状态机 |  |  |  |
| 键盘/焦点/滚动 |  |  |  |
| 响应式 |  |  |  |
| 状态与数据说明 |  |  |  |
| Console/Network |  |  |  |

用户未明确记录 PASS 前，人工验收保持 `PENDING`。

## 13. 当前风险与历史证据边界

- Longbridge `kline history` 的 adjustment 参数、返回时区和完成日线语义仍需实施阶段实机核验；核验失败不允许用其他 provider 补齐。
- 迁移电脑后的 Longbridge CLI 曾报 `/bin/bash: /usr/local/bin/longbridge: Bad CPU type in executable`；2026-08-31 经用户确认已用官方同版本 arm64 程序恢复，保留 Intel 备份，没有切换数据源。当前待解决的是 0.23.3 运行版本与旧分区 0.28.0 来源契约的差异，不再是 CPU 架构阻塞。
- 真实数据库已为 v3。先前写入升级被本机安全审查拒绝后停止，取得用户针对准确路径和备份/追加范围的“允许”才执行；没有绕过审批。第一次命令在备份只读回读阶段退出 1，迁移本身已提交；随后只读恢复校验通过，未重复迁移，见第 15.2 节。
- 无可验证的最新真实每日 packet，故未把合成 daily 与真实 W35 拼成“真实新单页运行”。W35 只读导出与合成 UI 检查分别保留。
- standalone HTML 是生成时快照，不会联网刷新 freshness。
- 本地 SQLite 不做应用层加密，依赖 owner-only 权限和本机磁盘安全。
- 旧 W35 revision 1 和旧双 panel HTML 是迁移前历史证据，不是新单页 UI 或计划指标的验收证据。
- 当前自动化仍使用旧入口；生产切换不在本次授权内。
- Feishu Wiki 与 Obsidian ingest 仍由各自独立确认门管理。

## 14. 源证据

- `开发路径图.md`
- `skills/trading-center-review/SKILL.md`
- `skills/trading-center-review/references/authorization-and-data-boundary.md`
- `skills/trading-center-review/references/dashboard-visualization-contract.md`
- `skills/trading-center-review/references/longbridge-import-contract.md`
- `skills/trading-center-review/scripts/align_market_watchlist.py`
- `skills/trading-center-review/scripts/render_trade_review_dashboard_v2.py`
- `skills/trading-center-review/scripts/trading_review_state.py`
- `skills/trading-center-review/scripts/construct_trade_plan.py`
- `skills/trading-center-review/scripts/trade_plan_lifecycle.py`
- `skills/trading-center-review/scripts/project_weekly_review.py`
- `skills/trading-center-review/scripts/verify_state_migration.py`
- `tests/build_unified_review_preview.py`
- owner-only SQLite v2 current readback and W35 revision 1
- `codex/trading-review-semantics@3e9bafb`

## 15. 2026-08-31 实现与核验证据

此节记录真实重跑前的工程检查点，不替代第 12 节的用户人工验收，也不表示已切换自动化。其版本升级、真实读取与周度 revision 状态已由第 17 节的后续运行证据更新；合成页面验收与真实页面验收仍须区分。

| 检查 | 结果 | 证据与边界 |
|---|---|---|
| 全量回归 | PASS | 145 tests，0 failures，0 errors，0 skipped；包括原 142 项检查，以及离线 WAL 备份回读、不把主库当 immutable、存在辅助文件时拒绝 immutable 的三项回归 |
| Skill 与 diff 检查 | PASS | project Skill quick_validate；`git diff --check`；没有修改全局 Skill、自动化或无关工作树变更 |
| v2→v3 副本演练 | PASS | 演练时四表追加，旧表计数/逻辑 hash 不变，原 DB 未变；后续真实迁移另记 |
| 真实迁移及只读检查 | PASS | user_version=3、quick_check=ok、FK 错误 0、目录 0700，DB/WAL/SHM/备份 0600；新表为空，旧表事实与 v2 备份及演练前基线一致 |
| W35 只读回读 | PARTIAL | revision 1 保持 partial/current/pending，原更新时间 2026-08-30T15:53:09+08:00；计划指标 blocked、比率 null；未写新 revision |
| 内置 Browser 布局 | 自动检查通过 | 1440×900、1280×800、390×844 无页面水平溢出；一个 main；鼠标切换、筛选和 details 可用；六个基础状态页面 console warn/error 为空 |
| 键盘与焦点 | PARTIAL | 已修复隐藏控件位于页首导致焦点跳顶；控件现放在可见 label 内，鼠标聚焦滚动稳定。自动化 Tab/Space 未证明完整键盘路径，必须人工执行第 12 节 |
| 两个附加 fixture | 生成检查通过 | confirmed-management 与 quote-inside 共用 renderer 并过 schema 校验；不声称人工浏览器 PASS |
| 真实 K 线与计划价格 | PENDING | CLI 原生兼容已恢复；与既存分区一致的 0.28.0 升级待确认，尚未构造真实买卖区间，未采用其他 provider |
| 用户人工验收 | PENDING | 本次新页面与计划能力尚无用户 PASS |

私有证据根目录：`/private/tmp/trading-center-review-runtime/2026-08-31/unified-review-v3/`。

- `migration-rehearsal.json`：副本迁移与只读原库比对，不含原始交易行。
- `migration-live.json`：真实迁移后的恢复校验证据，保留初次退出 1 和精确错误、v2/v3 指纹、已验证备份路径、新表为空及旧事实未变结论；不包含原始交易行。
- `w35-inline-readonly.json`：历史 W35 的白名单增量导出，不是新周度结果。
- `preview/`：八个合成 HTML、两个合成 JSON、`desktop-1440.png` 和 `mobile-390.png`；截图为合成数据，不是账户或实时行情。
- 本地测试 server 在自动检查后停止；预览按第 12.2 节启动，视口 override 已恢复默认。原始 DB、备份和证据未删除。

关键源码 SHA-256（本次未提交构建）：

~~~text
f0a6a4ba4c0c5841ad14638f854293554de1d86ceb1093bd2d7d9db063d9d386  construct_trade_plan.py
5119de7a98f57164aac7f5cd85dff28ba9ac75711907084bdfba3418f98a6390  trade_plan_lifecycle.py
abddaeed833fe3aa5f4b45fb72bbab6f8df0192e5ded814b26797265731cd2bb  trading_review_state.py
a70e57008d475bcbf0a9151dd13acbfde312ad4923c88884c523b56839ba8355  run_incremental_review.py
7d943efd9c407eda0f3c8da4785b658c3a21124212897f795ccb19fad4e6bc7b  project_weekly_review.py
a88100024ba402fa3e354fe8a2cb1d79b28fea753d1e974d7ba843bac1a731ea  render_trade_review_dashboard_v2.py
8d73a06116fd978c267f72c05cdb67a55fa9bf952c4d8800d2fd7e65ff8c6b62  verify_state_migration.py
3272945b0ef5511ba2456b9fe53e57cf9fbd35d92c850c89414ba8d2f513888e  trade-review-dashboard-v2-standalone.html
~~~

### 15.1 对抗式复核与连续性

- 规格复核：主动检查“双 panel 遗留”“盈利违规被掩盖”“止损失败被算为不合规”“买入前加仓”“缺证据显示 0%”和“每日写周度 revision”；对应 public-seam 测试通过。
- 工程复核：检查未确认计划直接入库、篡改草案后确认、确认时间回填、遗漏/伪造 episode、最差入场价风险边界、私有输出路径和迁移失败。新增防线后回归通过。
- 未解决边界明确保留：真实源读取 blocked、日期级 episode 的同日顺序证据不足、自然语言规则的可判定性、完整人工键盘路径 PENDING。真实 DB 升级的精确审批与迁移已完成，不再列作待办。
- 可复用候选：无脚本 radio/checkbox 应与可见 label 同位置，避免键盘焦点跳顶；旧 P&L 历史兼容必须在新投影处显式剔除，而非仅用 CSS 隐藏。只记录在项目报告，不修改全局 Skill 或记忆。
- 无 commit、merge、push、PR、部署或自动化切换。源码回滚仅涉及本次拥有的改动，共享工作树其他未提交内容必须保留。真实数据回滚只能在另行授权后使用已验证 v2 备份；不能删除四表或覆盖正在使用的数据库。

### 15.2 真实迁移回读与备份兼容问题

- 执行前：主库 v2、quick_check=ok、FK 错误 0、目录 0700、DB 0600；迁移器及状态模块指纹与已检验构建一致；不存在本次 live proof。
- 经用户“允许”和本机审批后执行一次 `verify_state_migration.py apply`。迁移事务成功提交四张新表和 v3 元数据，但随后普通只读打开新备份时出现 `sqlite3.OperationalError: unable to open database file`，命令退出 1。此时没有把退出码误当作迁移已回滚，也没有重复运行 apply。
- 核查发现主库为健康 v3；新备份保留 WAL 格式头（读写版本 2/2），不存在 WAL、SHM 或 journal 辅助文件。该已关闭备份使用 `mode=ro&immutable=1` 后可读，为健康 v2。普通只读方式在受限目录中需要辅助文件的问题，不等于备份数据损坏。
- 验证器只对显式指定、0600、非符号链接、且无任何 SQLite 辅助文件的独立备份使用 immutable。主库继续正常 `mode=ro`，绝不忽略活动 WAL；fingerprint 使用一个只读事务并显式关闭连接。
- 恢复校验没有调用迁移或写入主库：四表名称准确且均 0 行；全部旧表计数和逻辑 hash 与备份一致；备份完整指纹又与先前保存的 v2 演练基线一致；精确 Schema、quick_check、FK 与权限检查通过。
- 已验证备份：`/Users/archer/Library/Application Support/MarsTradingCenter/trading-review.sqlite3.backup-20260831T051550696751Z.sqlite3`，0600。保留该文件及历史备份，未删除任何真实记录或工件。
- 迁移只建立存储能力，没有生成新计划、补填 W35 指标、修改周度 revision、读取券商、更新自动化或改变人工验收状态。现存自动化未实跑，不将本次数据库检查描述为生产链路验收。
- 可复用项目内候选：迁移命令失败后必须区分事务提交与后置验证失败，先只读查明实际状态；离线 SQLite WAL 备份的验证不能假设允许创建辅助文件。该修正及三项回归保留在项目中，不修改全局 Skill 或记忆。

## 16. Apple 芯片兼容恢复与本轮重跑预检

用户说明已从 Intel Mac 换至 Apple 芯片 Mac，并确认修复 CLI 后继续 W35 复盘与 W36 计划审阅。核验主机为 arm64，而迁移来的 `/usr/local/bin/longbridge` 指向 0.23.3 的 x86_64 程序。

- 官方来源：[Longbridge v0.23.3 release](https://github.com/longbridge/longbridge-terminal/releases/tag/v0.23.3)。下载 darwin-arm64 包及官方 SHA-256，校验一致后才解包执行；没有运行远端安装脚本，没有安装 Rosetta 或 Plugin。
- 在同目录保留 `longbridge.intel-backup-20260831`，原命令符号链接不变，原位原子替换为同版本 arm64 程序。没有读取凭据、复制凭据、编辑登录配置或修改 Homebrew 元数据、自动化。
- `file` 回读为 Mach-O arm64，`--version` 为 0.23.3；`check --format json` 的 session valid、Global/CN connectivity OK，且实际探测没有 API error。该检查不能替代每项数据读取验证。
- 后续只读探测取得两个目标周的日历与当前持仓结构；完整响应仅在 0700/0600 私有目录。W35 日期为 2026-08-24 至 2026-08-28，W36 为 2026-08-31 至 2026-09-04。没有把当前持仓标成历史周末快照。
- 本轮设置 `LONGBRIDGE_LOG=off`；CLI 在受限默认日志目录创建文件失败后会回退到私有工作目录，内容日志关闭，未放宽默认日志目录权限。
- 只读核验发现既存交易分区来源契约是 `longbridge-cli-0.28.0-weekly.v1:trades`。本次恢复的 0.23.3 结果不能冒充 0.28.0，也不能绕过 cache 的来源版本。已向用户请求把 CLI 进一步升级为官方 0.28.0 arm64，未在无新确认时继续该版本升级。
- 已有周度记录只保留“未找到已确认周初计划及增量”的明确缺口；计划版本表仍为空。没有确认 W36 草案，没有给 W35 倒填计划，也没有追加本轮 weekly revision。

证据位置：

- `/private/tmp/trading-center-review-runtime/2026-08-31/longbridge-arm64.7FHHYi/binary-repair.json`：发行包、程序和 Intel 备份指纹及变更边界。
- 同目录 `readiness.json`：白名单登录/连通性探测，无凭据或账户值。
- `/private/tmp/trading-center-review-runtime/2026-08-31/weekly-w35-rerun.xzZhdH/capture/`：按能力记录的采集状态；raw 子目录不作为对话、Git 或 UI 输出。

此历史检查点状态：架构兼容恢复已验证；当时周度重跑与 W36 计划审阅尚未完成。用户随后批准匹配版本升级，后续真实结果见第 17 节；UI 人工验收仍为 PENDING。任何未来回滚仅针对具体程序备份，不能清理或重置授权目录。

## 17. 2026-08-31 原生 0.28.0 真实周度重跑与交付

### 17.1 变更、来源和边界

- 用户明确批准后，从 [Longbridge 官方 v0.28.0 release](https://github.com/longbridge/longbridge-terminal/releases/tag/v0.28.0) 取得 darwin-arm64 发行包并核对官方 SHA-256。当前命令链接指向 `/usr/local/Caskroom/longbridge-terminal/0.28.0/longbridge`；`file` 和 `--version` 回读为 arm64、0.28.0，登录及 Global/CN 探测通过。
- 新二进制 SHA-256：`33d7f8bd3f32e0bc811d4550c5e696ed6d11df5668647602a950f912f3f22fa8`。0.23.3 arm64 程序和同目录 `longbridge.intel-backup-20260831` 均保留，没有修改凭据、登录配置或 Homebrew 元数据。
- 仅本轮进程设置 `LONGBRIDGE_LOG=off`、`LONGBRIDGE_NO_ANALYTICS=1`、`DO_NOT_TRACK=1`，不改全局配置。禁用遥测的开关及调用位置已核对 [v0.28.0 官方源码](https://github.com/longbridge/longbridge-terminal/blob/v0.28.0/src/analytics/mod.rs)。默认日志目录受限的警告仍如实保留，没有为消除警告而放宽权限。
- 继续使用原 SQLite v3、同一个 renderer 和每日 V2 页面骨架；没有再次迁移、切换自动化、新建周度页面或确认交易计划。CLI 只读调用不包括下单、撤单、资金划转、盈利分析或现金流接口。
- 原始响应只在 owner-only 私有运行目录，未进入本报告、Git 或 HTML；持久层仅写已批准白名单。市场行情有自己的 `as_of`，不能把本次静态页面称为实时行情。

### 17.2 真实结果与缺口

| 对象 | 结果 | 可证明的边界 |
|---|---|---|
| 目标周日历 | 已核验 | W35 为 2026-08-24..28；W36 为 2026-08-31..09-04，均按 America/New_York 窗口 |
| 历史交易分区 | PARTIAL | 四个 complete 分区命中缓存且 hash 核验一致；只重新读取一个 partial 分区。重复可见执行行缺乏独立成交标识，不能认定为独立成交笔数 |
| 当前持仓与市场 | 已取得有限快照 | 当前持仓不是 W35 周末持仓；主报价没有源时间戳，展示采用有源时间戳的隔夜报价并注明场次，不作为可执行计划触发 |
| 周度市场温度 | 已取得 | 五个目标交易日事实齐备；不由温度单项推出交易指令 |
| 已完成日线 | 数据检查通过 | 授权范围内美股 underlying 的已完成日线通过前复权、NY 日期唯一、OHLCV 与新鲜度检查；使用 EMA20/50/200、ATR14 和结构位。具体持仓范围留在私有证据 |
| W36 事件 | PARTIAL | 19 个目标周宏观事件；财报接口返回目标周外内容，不能据此宣称下周没有财报 |
| 历史计划与执行指标 | BLOCKED | 没有事前已确认计划；覆盖率、执行率、计划胜率和需复盘数量不计算，数值为 null，不显示假 0% 或 100% |
| W36 新计划 | 尚未构造 | 只有技术参考和待确认问题。持有/等待期限、最低收益风险比、最大失效幅度、方向/setup、价格步长与有效期没有完整确认；新增计划版本为 0 |
| 周度状态 | 已写入并回读 | revision 2，`partial / current / pending`，依赖变化数为 0；不是业务复盘确认或人工验收 PASS |

期权只展示允许的 underlying 语义，正持仓数量不自动解释为看多；ETF 不冒充上市经营公司，非美股事实不强套首版美股多头价格构造器。已有仓位不能凭当前持仓倒填事前计划，也不能自动生成可执行加仓区间。

### 17.3 一次写入断言失败与恢复

首次写入辅助脚本错误地预期重试分区必然 `reused`，实际先追加了一个修订后才触发 `AssertionError: trade hash mismatch`。只读排查证明交易 payload 与旧修订完全一致，但错误分类由 `trade_row_validation_gap` 精化为 `duplicate_execution_ambiguity`；该分类属于分区修订身份，因此 revision 6→7 合法产生新修订。错误文字不是已证实的事实 hash 不一致。

当时新 weekly revision 尚未写入、collection run 未结束。恢复没有删除记录、重复创建 run 或重新执行完整采集，而是：

1. 根据运行前指纹与明确属于本轮的新增行核验所有既存行未变。
2. 保留分区 revision 7 及其与旧修订一致的白名单事实行；新周度依赖改为已存在的 revision 7。
3. 在同一个 run 上继续持仓、账户、市场、事件投影和周度追加，完成该 run。
4. 回读最新周度 revision 2 和全部依赖，核验旧行未变、当前交易 payload 未变、旧结果表未变，quick_check 与外键检查通过。

最终 `existing_rows_unchanged=true`、`current_trade_payloads_unchanged=true`，但 `trade_fact_tables_unchanged=false`：新分区修订中确实追加了同内容的投影行。不能将本轮描述成“交易表完全零写入”。旧 weekly revision 1 留存并派生 stale；新 revision 2 当前有效。没有新增 plan version、确认记录或交易 episode 分类。

### 17.4 统一页面与自动核验

- 真实页面仍按“市场风险雷达 → Codex 盘前判断 → 昨日操作摘要 → 持仓 × 计划 → 重要事件与时间轴 → 数据说明”的同一套 UI 展示，没有日/周整页切换或第二个周度 panel。
- 本轮未读取资金流证据，私有组装脚本在持久化前剔除了旧投影器固定生成的相关叙述；未用未经读取的资本流向解释行情。这里是本轮工件的保护，不宣称通用投影器已修复。
- 日、周事件同时出现时，仅在渲染副本按相同时间戳和标题去除 4 条重叠项；完整数据库和周度 packet 仍保留全部 19 个宏观事件。页面每个事件只出现一次。
- 真实 HTML 的结构、敏感字段、无脚本/无外部资源、EMA 而非 SMA、不可计算指标、计划缺口、事件去重检查通过；渲染前后 DB 指纹不变。全量回归 145 tests 通过。
- loopback 页面返回 HTTP 200，响应体 SHA-256 与交付文件一致；未授权路径返回 404。以上是自动核验和 HTTP 回读，**不是本轮真实页面的浏览器视觉验收或用户 PASS**。
- 市赚率定义已由用户确认，但新的财务数据口径和接入范围仍在对齐；本轮交付 HTML 与数据库没有加入 PR，不把需求确认冒充实现完成。

本轮真实工件根目录：`/private/tmp/trading-center-review-runtime/2026-08-31/weekly-w35-028.xk166J/`。

| 证据 | 内容 |
|---|---|
| `database-write-verification.json` | 回读、旧行保持、当前交易 payload 保持与新增修订边界 |
| `write-recovery.json` | 初次断言错误及恢复依据 |
| `weekly-readback.json`、`weekly-dashboard.json` | 最新周度状态与完整受限投影；不是原始 broker 行 |
| `weekly-dashboard-inline.json` | 去除重复事件后的渲染副本，不反写数据库 |
| `composition-manifest.json`、`partition-write-manifest.json` | 本轮组装与白名单分区写入证据 |
| `delivery-verification.json` | 单页、隐私、指标、事件与渲染不改库的自动断言；其中需求状态字段为生成时检查点，后续定义确认不改变原始检查凭据 |
| `view/index.html` | 真实统一页面，0600，不是合成 fixture |

最终 HTML SHA-256：`6c5d05f96abff476f01b3545ef57d2bc59942f946c8c90ba030161be8d8fdc9f`。原生升级证据另存 `/private/tmp/trading-center-review-runtime/2026-08-31/longbridge-028-arm64.XqzD4A/native-upgrade.json`。这些临时工件可能被系统清理；持久状态位于批准的 SQLite，不因 HTML 丢失而丢失。不得为重建预览无条件重跑全量券商采集。

### 17.5 本轮真实页面人工检查清单

这是第 12 节合成状态检查之外的真实运行验收。预计 5–8 分钟，仅复用一个 Codex 内置 Browser 标签，不读取源代码，不写数据库，不刷新券商数据。

**构建与起点**：`codex/trading-review-incremental-state`，HEAD `3e9bafbbd56994a5005254d272b3fb75f8f9ed6b` 加已记录的未提交构建；以本节最终 HTML SHA-256 区分工件。URL 为 `http://127.0.0.1:8765/w35-20260831/`，真实 W35 revision 2、W36 待构造计划。页面是采集时快照，行情与事件源时间以页面标注为准。

本轮预览服务只绑定 `127.0.0.1:8765`，只允许上述页面和本地 favicon；不对外提供整个私有目录。服务已启动时直接打开 URL。若服务已停止，在确认端口没有被其他服务占用后，使用原私有脚本启动：

~~~bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 /private/tmp/trading-center-review-runtime/2026-08-31/weekly-w35-028.xk166J/serve_preview.py
~~~

不要同时启动第 12.2 节的合成预览服务器；它使用同一端口和不同工件。若需要验证合成状态，先停止本轮已确认的服务，再单独执行第 12 节，并记录为另一次验收。

| 顺序 | 用户操作 | 预期 |
|---|---|---|
| 1 | 1440×900、100%，在一个内置 Browser 标签打开起始 URL | 正常加载；整体仍是原每日 V2 结构，没有独立周度入口或整页切换；不要求用户辨认源码 |
| 2 | 阅读页头、市场雷达及周度背景 | 日度采集时点与 W35 时间窗口区分；六个市场代理的实际标的、隔夜场次和源时间可辨认；债券价格代理不标成债券收益率 |
| 3 | 顺序读 Codex 判断、昨日操作、本周执行摘要 | 已确认事实、解释与待确认事项区分；成交去重缺口可见，没有账户金额、具体盈亏或未经读取的资金流叙述 |
| 4 | 在“持仓 × 计划”顶部查看四项周度指标 | 全部不可计算且有原因，不能把缺失显示成 0% 或 100%；周度仍 partial、确认待处理 |
| 5 | 鼠标切换当前持仓/交易计划，分别开启“只看接近触发”和“只看缺口”，再恢复默认 | 筛选语义清楚；没有已确认可执行计划时，接近触发的空状态合理；不能由技术参考自动变成买入/加仓指令 |
| 6 | 展开一条技术参考和缺口说明 | EMA20/50/200、结构参考与计划条件区别清楚；W36 参数待确认；底部反转需要右侧确认，已有仓位缺事前父计划不产生已确认加仓区间 |
| 7 | 在页面中部使用 Tab、Shift+Tab、Space/Enter 重复切换与展开 | 焦点清晰可见，不跳页首；滚动位置合理；收起后能继续键盘导航，无焦点丢失或内容被遮挡 |
| 8 | 阅读事件时间轴和数据说明 | 同一时间轴里显示 W36 19 个宏观事件及财报数据缺口，重叠事件不重复；双时区、事件来源和静态时间可辨认，不能把财报缺口显示为“没有财报” |
| 9 | 保持同一标签，依次检查 1280×800、390×844，均为 100% | 卡片、标的、解释与长条件不重叠；无整页横向滚动；信息主次清晰，文本可读；窄屏仍能筛选、展开和回到顶部 |
| 10 | 检查本页 Console 与 Network；若内置 Browser 本次不提供检查面板，记录该项 NOT RUN | 页面 HTML 200；业务请求无外部网络；无脚本、CSP 或加载错误。本地 favicon 204 不算错误 |
| 11 | 同一标签将路径临时改为 `/not-available/`，随后返回起始 URL | 无效路径 404，不泄漏目录或原始文件；返回后正常加载，未写入 DB。该步骤仅验服务错误边界，不伪造业务错误状态 |
| 12 | 关闭筛选、收起详情、恢复桌面视口并刷新 | 默认视图恢复，数据源时间不被刷新成“刚刚”；本轮是静态文档，不展示假加载成功、假实时刷新或假新 weekly revision |

**状态覆盖边界**：真实页面已涵盖 partial、blocked、计划缺口和筛选 empty。历史 revision 的 stale 已由数据库回读验证，不为了截图修改真实依赖；stale/无周度/完整计划状态由第 12 节合成工件另外验证。浏览器加载失败与业务数据 empty 不混用。本轮不声明尚未运行的状态或视觉结果通过。

**重置与清理**：保留原 DB、备份与私有运行证据；验收后在启动预览的终端按 Ctrl+C 停止这个服务。若终端不可见，先用 `/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN` 核对准确进程与上述脚本，本轮启动 PID 为 98290，PID 可能已变化，不能直接按旧 PID 杀进程或停止未知服务。不开新标签、不删除目录、不把临时服务设为开机或周期启动。

| 验收对象 | 视口/状态 | 人工 PASS/FAIL/NOT RUN | 备注或截图路径 |
|---|---|---|---|
| 同一 UI 与信息顺序 |  | PENDING |  |
| 真实覆盖、不可计算指标与计划缺口 |  | PENDING |  |
| 筛选、键盘、焦点、滚动 |  | PENDING |  |
| 响应式与可读性 |  | PENDING |  |
| 事件、时区、新鲜度与错误边界 |  | PENDING |  |
| Console/Network 与重置 |  | PENDING |  |

### 17.6 对抗式复核、回滚与维护

规格复核主动尝试证伪“已完成周度”“已有具体 W36 买卖计划”“当前快照等于历史周末”“低质量成交可精确统计”“统一页面没有重复事件”等说法；相应缺口留在 UI 和数据库中。工程复核检查原行保持、追加修订身份、重试不重复 run、敏感字段、源时间戳、单页和渲染不反写数据库。保留的实质缺口为成交消歧、历史计划、财报覆盖、W36 参数和用户人工验收。

可复用但尚未推广的项目内候选：修订身份包含错误分类，恢复脚本不能只凭 payload 相同断言 reused；资金流叙述应受实际证据状态约束；日周事件合成应基于显式事件身份去重；原生 CLI 兼容还需核验每进程日志/遥测边界。本轮以私有组装保护真实输出，未宣称通用投影器或事件契约完成修复。没有改写全局 Skill、记忆或开发路径图。

回滚边界：程序切回保留的指定旧版本需先核对二进制与来源契约，不混用版本数据；数据库新修订与本轮 run 均保留，不能覆盖主库或删除新记录来伪装未执行。UI 可停止本轮 loopback 服务并保留 HTML，不触及自动化或历史页面。所有权仍属用户；本轮未 commit、merge、push、PR、发布、切换生产或设置长期预览服务。新需求必须另行对齐，不由“长期本地授权”推导券商写入权限。

## 18. 已批准的统一 V2 展示修正（2026-08-31）

### 18.1 决策、证据与授权边界

用户指出内部数据规范外露、事件难以扫读、当前持仓与未持仓买入计划混淆，以及原五类策略丢失；在逐项对齐方案后明确回复“确认”。本节记录这次修正，不把此前页面的验收结论沿用到新构建。实施已批准，人工验收 **PENDING**。

已核验原因：第 17 节私有组装把已有持仓的技术参考复制到 `plan`；日度事件按上海日期分组，而周度追加为一个长列表；渲染器直接把诊断 note、boundary、reconciliation、证据引用和版本标识插入 HTML。原五类策略的完整名称在已读任务、旧 V2 和当前项目中没有可靠记录，必须保留缺口，不能把四种技术 setup 或买入后管理拼成五类。

选择：修正既有 V2 的内容投影、原生控件和样式，不新建周度页面/导航，也不另造数据系统。排除“仅把原始信息藏进折叠框”（仍会外露）和“把技术参考当成已确认计划”（语义不成立）。保留原数据校验、私有审计与历史快照，HTML 仅取用户需要的交易信息。

批准修改范围为本项目 V2 渲染脚本、模板、必要的数据组装和回归测试、相关项目 Skill 契约及本报告；重建原 loopback 入口的私有页面，并保留旧 HTML 回退。官方美联储及地区联储公开日历仅用于核对讲话排期，不扩展行情/券商数据来源。没有批准券商写入、自动化、数据库迁移、历史删除、全局 Skill/记忆、开发路径图、Wiki/Obsidian、commit/merge/push/deploy 或市赚率接入。

### 18.2 展示契约与状态边界

- 同一页面、同一内容顺序。周度只在对应内容中补充，独立显示其实际复盘区间和更新时间，不因日度渲染而假更新。
- 诊断规范、hash、分区/版本身份、源响应、内部字段名与采集流程不得进入 HTML（包括折叠内容）；保留交易结论、条件、实际时间与简短可行动的待确认事项。原证据不删、不反写、不替换。
- 仅展示 `.US` 身份已核验的持仓、买入候选及标的级操作；显示名称去掉后缀，内部身份不变。未标明市场或其他市场的行不得推定为美股。混合市场汇总无法安全拆分时不得把总数当美股数；历史数据库照常保留。
- 当前持仓连同其自身计划留在持仓视图；买入计划仅容纳未持有的标的。旧输入若重复出现，不能静默遗失一个不同的有效计划：应拒绝歧义或明确保留在持仓行，技术参考不得凭空提升为计划。
- 五类策略是独立研究分类，不等于 setup。支持经确认的原始分类标签，名称未补齐时不编造、不把空候选补成持仓副本；缺口保留为一次简短提示。
- 事件以明确的日历参考时点换算纽约日期，展示本周及下周的周一至周日日期桶；纽约午夜、跨月/年、夏令时与北京时间跨日均按真实时区换算。W35 历史复盘窗口不决定当前展望的“本周”。
- 事件默认可按日收起；关键行展示时间、事件、受影响对象和直接可读的情景影响。按用户复审意见删除来源显示与“观察点与来源”入口；来源证据只留私有包。同一事件按纽约时点、稳定标题及对象去重；不同发布时间或不同事件不合并。
- 美联储讲话仅纳入官方证据能核验的排期；来源覆盖失败、过期或未发布不等于“无讲话”。实际/预期/取消/未验证状态不能由系统日期自动改写为已发生。
- 无事前计划的周度指标仍不可计算；本轮不补造历史计划、胜率或执行率，不生成未经审阅的入场/加仓区间。

### 18.3 实施、失败模式、维护与回滚

实施顺序：先修正可复用投影与模板，再补充反例回归，最后用原私有事实重建页面及一次官方排期核对。原数据库只读核验、不做结构迁移；私有工件保持目录 0700 / 文件 0600。公开日历采集失败只影响事件覆盖，不中断其余已核验交易事实。

| 情形 | 可见行为 | 保持不变的边界 |
|---|---|---|
| 没有未持仓候选 / 五类名缺失 | 买入计划明确待补充 / 分类待确认 | 不复制持仓，不编造名称 |
| 混合市场 / 未知市场 | 仅投影已核验美股；不可拆分汇总不冒充美股统计 | 不删除历史或修改来源身份 |
| 日历来源不全 / 官方无可核验排期 | 空日期简洁显示“暂无已收录事件”；不显示覆盖状态标签 | 诊断仍保留私有，不宣称穷尽排期 |
| 历史周度陈旧 / 日度刷新 | 周度日期和陈旧提示保留 | 不自动刷新周度、移动已确认计划 |
| 内部字段混入业务文案 | 该内部说明不进入 UI，必要缺口以简短文字表达 | 完整诊断仍留私有工件 |
| HTML/服务失败 | 无效路由 404；修复后可回读同一入口 | 不列目录、不暴露原始工件 |

可访问性采用现有无脚本原生 radio/checkbox/details；焦点可见，状态不只靠颜色，筛选空态需明确。用户明确仅桌面使用，本轮不做手机或窄屏验收；此前历史清单中的手机条目不再作为当前构建的验收要求。观测以私有验证清单、HTTP 内容指纹和浏览器可见证据为准。所有权与维护仍在本项目，日/周生成遵守同一展示契约；不修改全局规则。回滚只恢复保留的旧 HTML 或本次精确代码改动，不回滚数据库、不删除新旧记录。

### 18.4 用户复审后的精简与影响解释

用户认可日期分桶方向，并明确要求：不做手机兼容检查；事件无收录即可为空，去掉“部分可用”“预期”和“观察点与来源”；保留有用观察并直述偏离预期的可能利多/利空；删除周度操作计数，日度只关注实际成交。用户同时指定，完成这些修正后先讨论当前持仓管理，再讨论未来一周整体计划。此范围已明确请求执行，计划本身仍未确认。

落实边界：

- `execution_count` 是私有展示输入的兼容性可选字段，不是数据库迁移。只有明确大于零的美股行进入“上一交易日成交”；不从“提交订单”或“已成交”文案猜测。零成交与明细未核对分开；日/周订单计数均不进入本次 UI。历史订单及周度 operations 私有证据仍保留。
- 根因修复：旧过滤器把单独的“修订”认作内部版本诊断，导致“前值修订、工资和修订值”整段被替换。新规则只挡明确的版本/编号用语，正常经济分析保留；没有可用观察时不插入重复占位。
- 事件影响是 Codex 对已核验排期的条件推演，不是 Longbridge 发布的结论。比较实际数据相对预期、拍卖相对可比基准或讲话相对预期的差异，不补造任何未来公布值、市场一致预期数值或实际资产反应。区分增长与利率通道，特别保留失业上升、就业增长、工资压力、原油去库、国债投标倍数的方向差异。
- UI 隐藏来源不等于删除溯源；官方讲话姓名及 HTTPS 域名继续验证，覆盖状态保持原值。取消、信息冲突与真实陈旧仍有具体提示，不能借精简文案把不完整证据升级为完整。
- 本次只用缓存重建；日历参考时间、官方排期证据核对时间、行情截至时间和历史周度生成时间保持不变。页面内容生成时间可以更新，但不是又读取一次市场。

一般影响机制核对依据（用于解释方法，不是新增行情/排期提供商；以下条件应用均为 Codex 推演）：美联储研究区分政策意外、经济信息与资产定价通道，说明不能把偏鹰/偏鸽机械当作确定涨跌。[美联储研究](https://www.federalreserve.gov/econres/feds/the-effect-of-the-federal-reserve-on-the-stock-market-magnitudes-channels-and-shocks.htm) 黄金受实际利率、通胀预期与经济风险等因素影响；白银还有显著工业需求，不能简单复制黄金方向。[芝加哥联储研究](https://www.chicagofed.org/publications/chicago-fed-letter/2021/464)、[CME 白银说明](https://www.cmegroup.com/openmarkets/metals/2023/Silver-A-Bridge-Between-Industrial-and-Precious-Metals.html) 库存是原油供需缓冲，价格与库存的因果关系双向；单次累库/去库仍需结合供给、消费和其他扰动。[EIA 库存与油价说明](https://www.eia.gov/finance/markets/crudeoil/balance.php)

代码与合成反例已经落地；最终工件、桌面检查与人工验收状态将在下节逐项记录，不能以旧构建的结果代替。

### 18.5 最终构建与验证证据

- 分支：`codex/trading-review-incremental-state`；HEAD：`3e9bafbbd56994a5005254d272b3fb75f8f9ed6b`；本节是其上未提交的已批准修正，不代表分支已合并或发布。
- 同一入口：[本地复盘 UI](http://127.0.0.1:8765/w35-20260831/)。最终 HTML SHA-256：`194c507b63240a7167de3fa4e28f64e6995be02d2148f3e001d036bc5c6c8d47`。当前代码重新渲染同一私有包，与此 HTML 和 HTTP 回读逐字节一致。
- 私有证据目录：`/private/tmp/trading-center-review-runtime/2026-08-31/ui-v2-correction.jfMlP0/`。`verification.json` 保存数据不变、展示与安全断言；`publish-verification.json` 保存发布前后指纹与 HTTP 回读；`07-events-desktop-final.png` 是本节最终工件的桌面截图。较早截图对应旧构建，不作为本次结果。
- 自动回归：**163 tests，0 failures，0 errors**；其中 V2 renderer 65 项。项目 Skill 校验 `Skill is valid!`，`git diff --check` 通过。预期拒绝测试中出现的 blocked 输出不是运行失败。
- 定向证伪覆盖：虚构成交文案没有逐行成交计数时不得出现；零成交不能冒充明细缺失；已知成交不可超过总数；内部版本号隐藏但经济前值修订保留；去掉来源展示后仍拒绝非官方或异常 URL；空日期不显示覆盖警告，但私有 partial 仍不可冒充 complete；真实取消/陈旧信息保留；周度操作块不进入 HTML。
- 页面回读：一个 `<main>`；两周 14 个日期桶、33 条去重事件（包括 2 条官方核验的联储讲话）；事件来源链接 0、常规“预期”标签 0、已移除占位 0；只展示有明确成交计数的美股标的级记录，无订单计数。持仓范围保持不变，没有复制持仓填充未持仓候选。具体持仓与成交记录数留在私有验收证据，不随源码发布。
- 数据边界：本轮展示重建没有券商调用或数据库写入；原六份源工件和数据库指纹前后一致。行情、排期核对、官方排期证据与历史周度时间保留，不把内容重新渲染当作数据刷新。
- 服务边界：页面 GET 200、内容指纹匹配；无效路径和 `/daily-dashboard.json` GET 均 404。HTML 无脚本、iframe、外部加载；没有改服务绑定、自动化或生产链路。

Agent 的当前桌面可见检查：复用同一个用户标签，不调整视口；实际 Browser 面板为 **775×796**（桌面应用内现有面板，不是手机模拟）。鼠标展开 9/1 后，讲话与 PMI 的上下行情景直接显示，来源控件不存在；展开 9/4 后正常“前值修订／工资和修订值”可见，未出现“观察条件待确认”。日度成交和持仓 DOM 回读匹配，未出现页面级横向溢出；完成后关闭新增展开项，筛选关闭、持仓视图选中，页面停在“持仓 × 计划”。未新增标签，未执行手机或窄屏矩阵。

键盘激活与完整焦点顺序留待人工检查：此前自动按键没有产生可确认的状态变化，不能记作键盘 PASS；本次只确认可见焦点样式和鼠标展开。浏览器 Console/Network 面板未另行检查，记 **NOT RUN**；HTTP 及静态安全检查是独立自动证据，不替代这两项。

### 18.6 桌面人工检查清单与未决事项

本清单取代第 12、17 节针对本轮构建的手机/窄屏要求。只用一个内置 Browser 标签，约 3–5 分钟；不需要读源代码、不刷新券商、不改数据库。**人工验收 PENDING**，此前的 PASS 不自动适用于本节新构建。

起点：第 18.5 节分支、HEAD 与 HTML 指纹；[同一本地 URL](http://127.0.0.1:8765/w35-20260831/)。使用桌面窗口、浏览器缩放 **100%**；本次检查记录的当前面板 **775×796**，不另测手机尺寸或响应式断点。先刷新一次恢复默认，再回页首。真实测试数据为已缓存 W35 复盘及 8/31 起两周日历；行情不是刷新时的实时报价，8/31–9/4 的计划仍待讨论。

| 顺序 | 操作 | 应看到的结果 |
|---|---|---|
| 1 | 在现有标签打开或刷新入口，从页首向下阅读 | 保留同一日度 V2 骨架，没有新增周度页面/导航；数据截至时间与周度区间可辨认，不显示账号、成本、具体盈亏或后台字段 |
| 2 | 查看“上一交易日成交” | 只显示已核验成交及其与计划的关系；无未成交委托、订单计数和“周度操作摘要”；没有用提交订单代替成交 |
| 3 | 在“持仓 × 计划”查看默认持仓，再切到未持仓买入计划 | 持仓连同其管理计划在一起；未持仓页为空并说明候选与原五类名称尚缺，不能复制当前持仓；四项周度指标仍不可计算，不显示假 0% |
| 4 | 回持仓，依次开/关“只看接近触发”“只看待确认”，展开一条观察条件后收起 | 无结果时说明如何复原；已有技术参考不变成已确认买卖/加仓区间；当前行与展开内容对齐、字重层级可读，操作后滚动位置合理 |
| 5 | 滚到事件，查看本周/下周的周一至周日与日期；展开 9/1、9/4 | 本周为 8/31–9/6，下周为 9/7–9/13；纽约时间与北京日期时间清楚；无“部分可用”、独立“预期”标签、“观察点与来源”或来源链接；高/低于预期和鹰/鸽影响直接可读，正常经济修订说明保留 |
| 6 | 展开没有已收录事件的日期（如 9/5），再收起 | 简洁的“暂无已收录事件”，不显示“排期待补充”或覆盖警告；有事件的日期仍按时间顺序展示，不因空日期改变其他状态 |
| 7 | 用 Tab / Shift+Tab 导航，Space 切换筛选、Enter 或 Space 展开日期/详情 | 控件有明确可见焦点，鼠标和键盘均能操作；不跳页首、不丢失焦点，收起后能继续导航；阅读与滚动无内容遮挡。本项人工实测前保持 PENDING |
| 8 | 如可打开 Console/Network，刷新一次检查，再将地址临时改为 `/not-available/` 后返回 | 本页 HTML 200，favicon 204 可接受；无业务外部请求及脚本/CSP 错误；错误路径 404，不显示目录/原始包。没有检查面板则记 NOT RUN，不用猜测替代 |
| 9 | 返回原 URL，关闭筛选和额外展开项，回到持仓区 | 回到当前持仓视图；数据时间不变、计划没有自动确认。页面可继续用于后续持仓讨论 |

状态覆盖：真实页面覆盖计划待确认、指标不可计算、未持仓空态、筛选空态与空日期；静态文档不伪造业务 loading 或后台实时刷新。真实陈旧/来源失败/完整已确认计划这次不人为制造：自动 fixture 已检查，人工未出现则记录 NOT RUN，不修改真实库或资料来凑状态。网络断开/服务停止是页面无法加载，不应解读为“无事件/无成交”。

清理与回退：保持当前标签、正常桌面尺寸及默认筛选，预览服务留给用户继续讨论；不新设常驻服务。验收结束若要停止预览，先核对监听 8765 的进程确为第 17.5 节私有脚本，再由其终端 Ctrl+C 停止，不能按历史 PID 盲目终止其他进程。回退保留原 `previous-index.html`（SHA-256 `6c5d05f96abff476f01b3545ef57d2bc59942f946c8c90ba030161be8d8fdc9f`）；回退需核对目标及当前指纹，只恢复批准的 UI，不做 git reset 或数据库回滚。没有删除真实订单、成交、旧快照或周度修订。

| 人工验收项 | PASS / FAIL / NOT RUN | 备注 |
|---|---|---|
| 同一桌面 UI 与信息层级 | PENDING | |
| 仅实际成交、无周度计数 | PENDING | |
| 持仓 / 未持仓分离、筛选与展开 | PENDING | |
| 日历分周、空日期与情景解释 | PENDING | |
| 键盘、焦点与滚动 | PENDING | |
| Console / Network / 错误路由 / 重置 | PENDING | |

未决事项不随本轮展示修改消失：原五类策略的完整名称仍待用户提供；没有已核验未持仓候选；历史计划权威与 8/26 成交核对缺口仍在；未接入市赚率，未构造或确认未来一周真实价格计划。下一步按用户要求从现有持仓定位、持有周期和可接受风险讨论，不倒填历史买入计划，不把技术参考直接转为可执行计划。期权不能直接套用首版做多 underlying 的区间规则。

对抗式结论：规格层面已移除本轮指定内容，同时保留真正会改变使用方式的取消/冲突/陈旧提示；工程层面源校验、路径权限、无脚本、时间语义与数据库不变检查通过。未发现本轮必须继续修复的实现问题；键盘及完整人工验收仍未确认。复用经验已限于项目 V2 契约：展示精简不升级证据状态，成交必须有明确证明，经济“修订”不能被内部版本过滤误伤。未向全局 Skill、记忆或开发路径图推广新规则。

## 19. 已批准改动的 main 收敛与发布前检查（2026-08-31）

用户明确要求将本轮相关分支与已完成改动统一到 `main` 并推送。只收敛本仓库已批准的 Skill、源码、模板、合成测试和脱敏工程文档；不发布真实 SQLite、账户或交易事实、私有 HTML/JSON、运行日志、凭据或临时采集脚本。

只读核验时，本地两个 `codex/trading-review-*` 分支同指 `3e9bafb`；远端 `codex/trading-review-semantics` 同指该提交，`agent/trading-review-initial@f44044b` 为其祖先，没有分叉提交需要冲突合并。远端尚无 `main`，默认分支仍为 `agent/trading-review-initial`。本次采用保留线性历史、提交已批准改动、创建并推送 `main` 的最小方式；不强推、不删除旧分支，不自动修改 GitHub 默认分支设置。

发布前检查：163 项本地自动测试通过、项目 Skill 校验通过；V1 renderer、模板与测试继续保留。对抗式规格复核发现 Skill 周度流程仍要求已移除的操作摘要，已同步为仅实际成交的最新展示规则，避免下次运行重新引入。工程检查覆盖缺失引用、敏感值和发布文件范围；公开报告去掉具体持仓与成交记录数，原私有证据不变。现有 Linux CI 在测试前仅创建固定 owner-only 临时目录，避免新路径门禁测试因 Linux 没有 `/private/tmp` 而在准备阶段失败；不改变生产路径或放宽权限，远端 CI 结果必须与本地通过分开记录。

`开发路径图.md` 仅将现有已批准文本纳入版本历史，不修改任何内容。其旧手机验收、临时页面及服务边界文字与后续需求存在时间差：当前 UI 以第 18 节和最新展示契约为准；未来常驻服务或路线图调整仍须展示准确方案或 diff 后批准。本次也不把历史设计参考图当作当前 UI 验收证据。

暂存差异检查保留 Markdown 标准硬换行的行末双空格（本报告头部与路线图原文），没有为消除格式提示改写用户路线图；代码及其他空白错误另行检查。此例外不代表无条件忽略 `git diff --check` 的失败。

回滚与未决：旧分支及 V1 保留；源码回退只能采用明确提交的反向变更，不 reset/clean 共享工作树，不回滚真实数据库。推送不启用 LaunchAgent、不实现 Obsidian Bridge、不确认任何真实交易计划，不改变人工验收 PENDING。Git 完成状态以本地提交及推送后远端 `main` 的同一提交回读为准；本节仅记录授权和发布前检查。

## 20. 已批准的本地常驻展示服务（2026-08-31）

> 历史方案：下文 Python 服务尚未部署时，用户新增确认了 TS Web UI 与 Obsidian 桥接范围。当前实施、精确文本门禁、运行证据和人工验收以 [合并架构报告](ts-web-and-obsidian-bridge.md) 为准；不再执行下文旧 Python Web 命令。

### 20.1 决策、批准与事实

用户在讨论“复盘仍依赖临时脚本和页面、数据库与 Obsidian Bridge 的关系”后，批准本节方案并明确要求实施。范围是：统一 V2 在 `127.0.0.1:8765` 常驻；GitHub 默认分支切到 `main`；Obsidian Bridge 暂不接入。最新口述中的误读价格作废；持仓管理只记录待讨论意向，不确认计划或改券商委托。

实施起点为 `main@9474b6cdbacf19e228769e2a8a04a474058c7d84`，远端同 SHA，已有 CI 成功。GitHub 默认分支现已从 `agent/trading-review-initial` 切换为 `main`，设置页回读成功；未删除旧分支。已有 SQLite v3、每日分区、分析缓存和周度 revision 保持为事实权威；现有 HTML 从私有临时目录提供。数据库尚不能单独还原全部解释文案及展示字段，不能把“有数据库”说成“页面已可完整重建”。

采用最小分工：原增量 runner 负责事实入库；显式发布命令负责固定展示投影与原子发布；只读 HTTP 服务负责读取已发布 HTML。复用同一 V2 renderer 和模板，不增加前端框架、数据库、周度页面或服务端交易接口。稳定入口为 `/`，保留 `/w35-20260831/` 的历史记录入口；历史入口固定到对应发布，不随未来新日期改指。

### 20.2 私有目录与接口边界

- 数据库仍为 `~/Library/Application Support/MarsTradingCenter/trading-review.sqlite3`，不迁移、不新增表、不写历史计划。
- 展示安装根为 `~/Library/Application Support/MarsTradingCenter/web-ui/`；目录 0700、文件 0600，拒绝符号链接、其他所有者、权限过宽、Git/Vault 内路径。生产命令只允许这个明确根；测试只在显式隔离测试根运行。
- 展示快照只保留固定白名单结构：每日可见模块与必要时间、覆盖和证据字段，以及可选周度增量。剔除账户模块、账户标签/时间、后台数据说明、未成交及非美股操作行、委托计数。不持久化原始响应、完整日历、凭据、账户或券商 ID、成本、费用。相关事件的来源证据可保留供验证，但不由 HTTP 提供。
- 先完整验证原每日/周度包，再生成独立的展示投影；不能补造账户字段以通过旧 Schema。展示投影自身严格拒绝未知字段。重复使用同一模块校验和渲染实现；日度/周度源时间、覆盖、确认状态不升级。
- `trading_review_web.py publish` 是正式发布入口：接受现有合格私有每日包，可从只读 SQLite 获取指定周度的最新 revision，也可复用已保存展示快照。首次必须有合格日度输入。日度复用周度时不修改其生成时间或数据库；显式更新周度才读取该 revision。私有输入仍是正常采集边界，不再是常驻服务依赖。
- `rebuild` 从持久快照及安装时固定的 renderer/template 重建，不依赖临时采集目录；它不是重新取行情或重新运行 Codex。
- 发布以不可变内容目录加原子索引切换完成。先写完并回读校验快照和 HTML，再替换索引；写者加锁，防止并发覆盖。保留上一版和历史发布，不做自动清理。失败不更新当前指针，也不删除成功版本；损坏的当前工件只返回不可用，不静默显示别的日期。
- 安装的服务器、发布脚本、renderer 与模板固定为一份本地版本，带内容指纹；不能运行临时目录或随分支切换漂移的服务器脚本。升级必须显式安装，旧版本保留用于回退。

现有数据库只读入口必须使用 `mode=ro`、`query_only`，仅接受当前已迁移 Schema；不得通过普通可写 `open_state_store` 隐式迁移、创建库或调整 journal。读完立即关闭，不以 `immutable=1` 读取可能有活动 WAL 的数据库。日度缺失的展示文案不会凭数据库补造；采集端继续产出已校验日包，发布端负责稳定投影和交付，不新增采集调度。

### 20.3 服务、权限与维护责任

用户级 `~/Library/LaunchAgents/com.marstradingcenter.web-ui.plist` 使用已核验兼容 Apple Silicon 的系统 Python、绝对安装路径、`RunAtLoad`、`KeepAlive` 与重启节流。登录后启动、异常退出后重启，关闭 Codex 不影响它；退出 macOS 用户登录或关机后服务停止，不宣称系统级全天在线。只绑定 `127.0.0.1:8765`，不占用 Mars Reader 的端口，不改其配置或认证。

HTTP 仅支持 GET/HEAD 的固定页面和健康状态，不列目录、不提供 JSON/SQLite/日志/源码、不写数据。校验 Host、拒绝跨站读取、无 CORS、无脚本/外部资源、无缓存；健康状态只包含服务/发布身份与数据时间，不含持仓。未知路径 404，缺失或损坏发布 503；操作系统绑定冲突应显式失败，不能自动杀掉占用进程或更换端口。

localhost 不是身份认证：同一电脑上能发请求的本地进程可访问页面；浏览器跨站防护不能防御已控制本机用户的程序。用户已选择个人本地服务，本次不新增密码、网络访问或远程暴露。日志仅脱敏启动/失败类别，不记请求路径正文、账户内容或原始异常。提供 status/start/restart/stop/uninstall；卸载只撤下该 LaunchAgent，保留数据库、快照、源码安装和历史发布，删除历史需另行确认。

项目维护者负责显式发布、安装升级、故障排查与磁盘占用检查；此轮不新增后台抓取、备份周期、自动清理、开机自交易、Codex 自动化或 Vault 写入。Skill 入口只增加按需读取的常驻展示使用指引，不改变其他运行的更窄授权。

### 20.4 取舍、依赖与失败矩阵

拒绝继续只用临时目录，因为重启/清理后无法恢复；拒绝直接把完整私有 JSON 复制到长期目录，因为包含非展示账户字段；拒绝另建全栈服务和第二数据库，因为本次目标只是稳定交付已生成页面；暂不做 DB-only 自动生成所有文案，因为现有持久字段不足，补造会破坏证据边界。选择本地固定展示快照作为派生产物，SQLite 继续是事实/周度/计划权威。

依赖只有 macOS launchd、系统 Python 标准库、现有 V2 renderer/template，以及显式读取周度时的 SQLite v3。安装与 LaunchAgent 写入需要对应目录权限；权限拒绝应保留准备成果、报告未切换，不绕过系统限制。

| 状态/失败 | 可见结果与处理 | 不允许的降级 |
|---|---|---|
| 首次缺日度输入 | publish 拒绝；无发布时 HTTP 503 | 周度专用页、假空账户 |
| 新输入或源校验失败 | 旧索引及旧页面保持；命令失败 | 更新时间、空列表或成功状态冒充 |
| 日度刷新而周度未重算 | 周度内容及生成时间不变 | 自动确认或重算周度指标 |
| 当前文件缺失/损坏/权限错误 | HTTP 503；维护命令报告失败 | 目录列表、原始异常或静默换日期 |
| 并发发布/意外中断 | 锁串行；索引只指向完整发布 | 半份 HTML 或混合日周版本 |
| 文件路径、链接、所有者异常 | 拒绝读写 | 自动放宽权限、跟随链接 |
| 数据日期老、周度 stale | 保留真实时间和提示 | 服务运行时间当成数据新鲜度 |
| 端口已占用 | 安装/启动失败，核对已知预览后手动交接 | 杀未知 PID、绑定外网或偷偷换端口 |
| 进程异常退出 | launchd 节流重启；状态回读验证 | 只凭 plist 就宣称运行稳定 |
| 用户退出登录/机器休眠 | 不提供可用性保证；恢复后检查状态 | 等同于系统 daemon 或市场实时监控 |

### 20.5 阶段、迁移与回滚

1. 记录批准和边界；新增严格投影、发布/重建、只读服务与测试，不改路线图。
2. 对真实修正包做一次发布前干跑：输出与既有 HTML 逐字节比较；没有新行情、计划确认或 DB 写入。
3. 写入固定安装和持久快照，核对文件权限、内容指纹和 DB 不变证据；新程序先通过隔离端口的合成数据测试。
4. 只停止已核实属于本任务的临时预览终端；加载本 LaunchAgent，回读 HTTP、监听地址、进程归属与重启状态。失败时撤下本 Agent，并通过保留的旧预览脚本恢复原 URL，不覆盖其输入。
5. 提交并推送此次公开代码/脱敏报告，实机运行与 CI 证据分开记录。用户人工验收保持 PENDING。

回滚粒度是本 Agent 的固定代码版本和发布索引，旧版本与原临时工件均保留；不回滚 SQLite、不删除历史分区、周度 revision 或旧分支。源码回滚用明确反向提交，不 reset/clean。GitHub 默认分支可显式改回旧分支，但本次无需回退。安装结果、发布身份与最终提交在 20.7 节记录，不能先填 PASS。

### 20.6 一标签桌面人工验收

仅桌面，无手机/窄屏兼容要求。沿用现有内置 Browser 标签；正常桌面面板、100% 缩放，不设模拟设备。起始筛选关闭、持仓视图选中、事件默认收起。准确 build/发布指纹见 20.7；基准数据是这次已校验私有记录，不制作假真实交易来凑状态。总计约 5 分钟，需注销/重启的可用性项另行做，未做标 NOT RUN。

| 顺序 | 人工任务 | 预期结果 |
|---|---|---|
| 1 | 同一标签打开 `http://127.0.0.1:8765/`，再访问 `/w35-20260831/` | 都能查看本轮统一日/周页面；历史链接固定到这份记录；标题与真实数据时间清楚，无账户或后台字段 |
| 2 | 查看市场/判断、成交、持仓和未持仓计划页签；切换筛选后清空 | 与第 18 节内容一致；只看实际美股成交，持仓不混入待买候选；没有周度计数/P&L 板块；空候选不伪造条目 |
| 3 | 展开本周/下周各一个日期及一个空日期 | 星期/日期、双时区与情景影响可读；空日期简洁；无来源控件/“部分可用”/独立“预期”标签；未公布不能冒充实际值 |
| 4 | 展开持仓详情和更新说明；Tab/Shift+Tab、Space/Enter 操作后滚动 | 焦点清楚、键盘可用、收起后不丢焦点、不跳页首；长文换行，无页面级横向溢出或遮挡 |
| 5 | 刷新；如可用查看 Console/Network | 只重载保存页面，日/周时间不更新；无业务外部请求、脚本/CSP 错误；静态页不伪造 loading/自动刷新 |
| 6 | 地址改 `/daily-dashboard.json` 及 `/not-available/`，再返回 `/` | 都为 404，无私有内容/目录；返回后正常。发布缺失/损坏的 503 用隔离自动测试证明，不改真实资料来触发 |
| 7 | 在维护命令显示 running 后关闭 Codex，再重新打开原 URL（或系统浏览器） | 登录会话仍在时页面可访问；服务不依赖原终端。注销/重启后未实测则记 NOT RUN，不推断 PASS |
| 8 | 关闭额外展开项、恢复持仓视图，留在持仓区 | 保留一个原标签、原桌面尺寸；没有确认计划或修改交易。若需停止，执行明确 stop 命令；恢复用 start |

真实记录可验：待确认计划、不可计算指标、空候选、空日期与历史数据时间。完整计划、来源取消/冲突、403/405/503、并发中断、权限/链接攻击、服务退出重启等另用隔离合成自动证据；不能将自动覆盖记成人工 PASS。HTTP 只读服务没有上传、保存或实时交易状态。Console/Network 未查看时记录 NOT RUN。

| 人工项目 | PASS / FAIL / NOT RUN | 备注 |
|---|---|---|
| 统一内容、日/周时间与历史入口 | PENDING | |
| 鼠标/键盘/焦点/滚动与桌面可读性 | PENDING | |
| 空态/待确认/错误路由/Console/Network | PENDING | |
| 关闭 Codex 后可用 | PENDING | |
| 注销/重启后登录启动 | NOT RUN | |

### 20.7 实施与证据（待回读）

批准状态：已批准上述本地服务、默认分支变更与相关项目 Skill 更新；未批准 Obsidian Bridge、路线图新文本或任何交易执行。代码验证、安装运行及人工验收分别记录，当前不可互相代替。个人持仓讨论只进入 owner-only 展示内容，不能写进公开报告、Git 或倒填历史计划。尚缺策略五类名称、市赚率数据接入和完整新周交易计划等问题不在此次服务安装中补造。
