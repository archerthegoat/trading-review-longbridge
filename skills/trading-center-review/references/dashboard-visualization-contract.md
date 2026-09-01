# 交易复盘 Web UI V2 看板契约

## 1. 交付定位

在 Design QA 和用户浏览器 PASS 之前，V2 只是基于脱敏事实和已确认计划生成的 owner-only standalone HTML 影子验收工件；V1 仍是生产每日主链路和回滚基线。两道验收门通过后，是否切换主交付另行确认。V2 统一承载每日盘前复盘和周度复盘，不负责下单、账户管理、聊天、Agent 运行或远程请求。

V1 renderer、模板和测试保持兼容；V2 使用一个 renderer、一个模板和一个 `<main>`。每日骨架为唯一信息架构，周度增量嵌入原模块；禁止每日/周度模式切换、独立周度 panel、脚本或运行时网络。

渲染命令：

~~~bash
python3 skills/trading-center-review/scripts/render_trade_review_dashboard_v2.py \
  --daily-input /private/tmp/trading-center-review-runtime/<run-date>/daily-dashboard.json \
  --weekly-input /private/tmp/trading-center-review-runtime/<run-date>/weekly-dashboard.json \
  --output /private/tmp/trading-center-review-runtime/<run-date>/trade-review-dashboard-v2-standalone.html
~~~

输入和输出都必须位于 /private/tmp/trading-center-review-runtime 下，并且不在任何 Git 工作树内。渲染器使用临时文件、原子改名和 0600 文件权限。

以上是原始私有渲染边界；用户明确启用常驻展示后，另由 [正式发布入口](local-web-service.md) 生成账户字段已剔除的 `trading-review-display.v1` 派生快照，并在固定 owner-only 目录提供同一 HTML。独立展示 Schema 严格允许 `schema_version/daily/weekly`，daily 不含 account/data_note，meta 不含账户标签/时间，operations 不含 orders/reconciliation 且只保留明确美股成交。周度不持久保留未渲染的 operations/data_note 列表。不能放宽原 renderer 的临时输入路径或补假账户通过校验。页面布局、时间和人工验收门不因安装服务改变。

## 2. 视觉与信息架构

视觉目标是 docs/design/trading-center-web-ui-v2-option-2.png，即方案 2“风险雷达双栏”。

桌面布局：

1. 页面顶部显示每日日期和独立的周度 period/freshness；无周度时显示“尚未生成”，无合格每日包时不生成页面。
2. 顶部显示盘前复盘、已复盘美股交易日和数据截止时间，不设置日/周导航。
3. 每日上部全宽显示市场风险雷达；Codex 分析保留在私有展示快照与审计链，不再渲染独立盘前判断面板。
4. 每日随后显示上一交易日成交、持仓 × 计划、全宽事件和折叠数据说明。
5. 周度市场背景与周度判断/纪律不进入前台；四个执行指标与异常交易/下周草案注入原持仓 × 计划，下周事件进入同一时间轴，日/周新鲜度进入同一数据说明。不显示周度订单/成交笔数摘要。

用户明确只在桌面使用（2026-08-31 复审）；验收使用一个正常桌面 Browser 标签，不做手机兼容、窄屏适配或手机视口矩阵。既有 CSS 的自动折行不构成手机支持承诺。

日期和双时区必须使用正常网格或弹性布局，不使用固定宽度绝对定位。

## 3. 顶层 Schema

Schema ID 为 trading-review-dashboard.v2。

顶层严格只允许以下字段：

meta、market、account、codex_analysis、operations、positions_plans、events、data_note。

未知字段默认拒绝。所有字符串在 HTML 输出前必须进行纯文本转义。

周度 Schema ID 为 `trading-review-weekly-dashboard.v2`。顶层严格只允许：

`schema_version`、`meta`、`execution_metrics`、`review_episodes`、`sections`。

`sections` 固定为 `market_radar`、`judgement`、`operations`、`positions_plan`、`plan_review`、`next_week`、`events`、`data_note`。周度 `meta` 额外记录 `freshness=current|stale` 与独立的 `confirmation_status=pending|confirmed`。数据模块 blocked 可以在整体 partial 的周度页面中显示明确缺口；整体 blocked、Schema/隐私/路径失败仍禁止生成 HTML。

周度只展示计划覆盖率、按计划执行率、计划胜率、需复盘数、需具体复盘的交易与计划/事件/缺口。周度 operations 仍可留在私有包供校验，但不再显示计数摘要。具体盈亏、收益率、归因和现金流不进入周度包或 HTML。账户总额、上游 ID、费用/佣金和具体期权合约身份同样禁止。

`execution_metrics` 保留各分子分母、open/flat/unverifiable 排除数和 data_status/gap；百分比必须与计数一致，零分母为 null。`review_episodes` 只包含需要复盘的异常、失败和不可核验项，数量必须与 review_needed_count 一致。指标 blocked 时显示不可计算，不把缺失写成 0%。

模块状态允许：

- complete：必需字段齐全且可核验。
- partial：部分字段或范围缺失，但剩余内容可区分。
- empty：接口成功且返回空结果。
- stale：结果存在但超过允许新鲜度。
- blocked：接口、权限、结构或隐私门禁不允许继续。

整体状态只允许 complete、partial、blocked；empty 和 stale 保留为模块状态。

## 4. 模块字段

### meta

必需字段：

- review_label
- account_label
- review_date：由上游 Longbridge 交易日历选择的 America/New_York 已完成美股交易日，格式 YYYY-MM-DD；renderer 只独立校验周一至周五，不声称独立核验交易所假日。
- generated_at：Asia/Shanghai 的严格 RFC3339 时间，必须带 `T`、秒和 `+08:00` 对应的时区 offset。
- market_as_of
- account_snapshot_at
- period_label
- overall_status
- previous_trading_window

previous_trading_window 必须包含：

- label
- market_date
- ny_start
- ny_end
- utc_start
- utc_end

ny_start/ny_end 和 utc_start/utc_end 必须是严格 RFC3339（含 `T`、秒和时区），两组半开端点必须表示同一时刻范围。ny_start 必须是 review_date 当地 00:00:00，ny_end 必须是次日当地 00:00:00；端点的 offset 由 `America/New_York` 对相应日期机械验证，不能用其他时区 offset 冒充 ET；UTC 端点必须是 `Z` 或零 offset。DST 由 ZoneInfo 处理，假日选择仍属于上游交易日历责任。

### market

模块字段：

- status
- title
- source_scope
- items
- note（可选）

每个 item 必须包含：

- name
- symbol：Longbridge 返回的真实标的或代理。
- is_proxy：布尔值；使用代理时必须为 true。
- proxy_for：is_proxy=true 时为非空的被代理对象，is_proxy=false 时必须为 null。
- value
- change_pct
- direction：up、down 或 flat。
- strength：0 至 3。
- state
- session
- as_of
- risk_note
- data_status

`state` 和 `data_status` 继续用于私有验证与方向着色门禁，但市场雷达前台只显示资产/指数、最新值和涨跌幅，不显示逐行“状态”列、模块完成徽标、重复来源时间或周度市场背景。

可选字段：

- unavailable_reason
- capital_flow：只表示该标的的资金流，包含 label、direction、value、as_of、data_status。

不能把 capital 写成全市场资金流，不能用 close × volume 推断资金方向。

当 symbol 是常见代理标的时，必须显式声明 is_proxy=true 和 proxy_for。页面只有在 is_proxy=true 时才显示“代理：…”，不得把未声明的代理名称写成指数本体。

### account（私有校验模块，不渲染）

模块字段：

- status
- title
- base_currency
- snapshot_at
- metrics
- note（可选）

每个 metric 必须包含 label、value、kind、data_status；kind 为 money、number 或 text，可选 note。

该模块用于私有事实校验和 Codex 结构化分析，不生成账户概览，不展示金额、基础币种、快照时间或金额显隐控件。禁止账户标识、成本、佣金、订单/成交 ID 和原始响应。

### codex_analysis

模块字段：

- status
- title
- headline
- facts
- interpretation
- risks
- checks
- gaps
- note（可选）

facts、interpretation、risks、gaps 的每项都包含 label 和 text。

checks 的每项都包含：

- if
- then
- else
- evidence_refs
- boundary

Codex 判断必须和确定性事实分开；条件式检查不能变成无条件交易指令。

该模块继续由 Schema 验证并保留在私有展示快照，供审计与后续分析使用；前台不渲染独立 Codex 盘前判断、待确认事项、条件式行动或周度判断与纪律。隐藏不改变原分析状态，也不删除来源事实。

### operations

模块字段：

- status
- title
- window_label
- orders
- executions
- items
- reconciliation
- note（可选）

orders 和 executions 各包含 count、data_status、note。count 可以为 null，但不能在失败时伪造为 0。

items 只保留标的级脱敏聚合，包含：

- symbol
- display_name
- action
- role
- state
- plan_relation
- reconciliation
- data_status

每行另可提供 `execution_count`（非负整数或 null）：大于零才有资格进入成交展示；缺失或 null 不得从 action/state 文案反推成交，零值委托不展示。已知行成交总数不能超过已核验总数。`market_scope=US` 表示聚合已经按美股范围核验；未知范围不当作美股统计。

只有已核验美股成交总数为零时才显示“上一交易日无已成交记录”；总数非零但缺少逐项证明时显示“成交明细尚待核对”，不伪装为零。订单/成交计数为 `data_status=empty` 时，count 只能是 0 或 null；标的级 item 的空状态只能保留标识字段和中性占位，不得带事实文本或非空数值。订单计数留在私有包，不进入成交 UI。

### positions_plans

模块字段：

- status
- title
- items
- note（可选）

每项包含：

- symbol
- data_status
- display_name
- tab：holdings 或 plan。
- role
- holding_state
- plan_coverage
- trigger_distance：label、value、tone。
- near_trigger
- signals
- invalidation
- next_checks
- has_gap
- gap
- boundary

`symbol` 必须非空。期权先映射 underlying；不在页面记录 strike、到期日、成本或交易标识。`data_status=empty` 的持仓 item 不得携带事实字段；成功空持仓用空 items 表示。

可选 `plan_detail` 在私有输入中包含 plan_id/version、stage/status/setup、Longbridge evidence、zones、父计划/初始买入派生键和 quote_relation。页面仅在同一持仓/候选行详情内展示已翻译的形态、确认状态、区间、条件及技术参考时间；不显示计划 ID、版本身份、哈希、派生公式代码和原始证据。使用 EMA20/50/200、ATR14，不展示 SMA。`pre_entry` 禁止 add；bottom_reversal 无右侧确认仅观察；position_management 的 add 在确认前明确“待单独确认”。quote 只计算区间关系，不改写已确认价格。

### events

模块字段：

- status
- title
- display_timezone
- groups
- note（可选）

group 包含 date、label、range、events。

group.date 固定表示 Asia/Shanghai 的日历日。每个 event 还必须有 et_date；`date + shanghai_time` 与 `et_date + et_time` 必须经时区转换表示同一时刻。et_date 可以因跨日而不同，但不能省略或凭展示文本猜测。

event 必须包含：

- shanghai_time
- et_date
- et_time
- title
- status：已发生、预期、未公布或未验证。
- source
- data_status
- impact_channel
- object

只展示与当前持仓 underlying、本周有效计划、明确候选池或美股整体有明确风险通道的事件。无关财报和无排期占位不进入页面。事件 item 的 `data_status=empty` 不得携带事实字段，成功空事件用空 groups 表示；日报模板中的“无已确认事件（相关筛选后）”行固定使用“已发生”，来源必须写“相关筛选已完成并返回空”，这只表示筛选动作完成，不表示市场事件已发生。

### data_note

模块字段：

- status
- title
- items
- boundary

item 包含 label、value、state。该区默认折叠，只展示数据时间、模块状态、快照边界和用户可处理缺口。

## 5. 原生交互

不使用运行时 JavaScript。所有首版交互通过原生控件和 CSS 完成：

- 当前持仓 / 交易计划切换。
- 只看接近触发。
- 只看缺口。
- details 展开/收起。

控件必须支持键盘、具有可见焦点和清晰标签。刷新恢复默认状态，不保存操作。

## 6. 安全门

模板和生成 HTML 必须：

- 无 script、iframe、srcdoc、eval、document.write。
- 无外部 URL、外部字体、图片、脚本或网络请求。
- 无动态 HTML 注入。
- 无账户标识、订单/成交 ID、凭据、Cookie、API key、成本、佣金或原始券商响应。值级扫描拒绝 ASCII/全角冒号或等号后的 Authorization、Bearer、access_token、refresh_token、client_secret、api_key 及中文敏感标识。
- 不把后台模型、reviewer、tool、agent、人工/浏览器验收或 Schema/V2 调试状态注入用户可见字段；用户要求的 Codex 分析标签属于允许的产品文案。
- 输出文件权限为 0600。

renderer 还必须拒绝：

- 顶层或嵌套未知字段。
- 日期、时间、状态或关键字段格式错误。
- 敏感字段名或敏感值。
- overall_status 为 blocked 的包。
- 任意模块 status 为 blocked 的包。
- 输入或输出位于 Git 工作树内的路径。

## 7. 状态表现

红绿颜色只用于 complete 状态下已经核验的方向、变化或触发距离；complete 状态标签和已发生事件使用蓝色。partial、stale、empty、blocked 以及不可用/未验证内容使用中性或琥珀色。

失败和缺口不能被渲染成成功空列表：

父子状态必须保持一致：

- empty 父模块不得含有子项；非空子项不得携带非空事实项或配 empty。market/account/order/position/event 的 `data_status=empty` 只能保留结构标识、中性占位和解释性缺口；数值必须为空，方向/强度必须为中性。成功空事件使用“成功为空/无已确认事件（相关筛选后）”语义，不默认升级为未验证。
- complete 父模块的所有可见子项必须 complete。
- partial/stale 可以保留可区分的 complete 与非 complete 子项；若子项全部 complete 或没有子项，必须提供解释性 note。blocked 子项不能被 partial/stale 掩盖。
- blocked 模块或整体不生成成功 HTML。

| 状态 | 看板行为 |
| --- | --- |
| complete | 正常展示并保留来源时间 |
| partial | 展示可用部分和缺口 |
| empty | 展示成功空结果说明 |
| stale | 展示原始时间和陈旧标签 |
| blocked | 不生成成功 HTML，CLI 返回失败 |

## 8. 验收

自动检查至少覆盖：

1. 五种 fixture 状态。
2. Schema 和未知字段拒绝。
3. 值级敏感信息拒绝。
4. HTML 转义。
5. account 模块接受私有校验，但账户标签、金额、基础币种、快照时间和金额控件不进入 HTML。
6. 页面顺序和双时区布局。
7. 无运行时脚本和外部请求。
8. 0600 权限、原子写入和 Git 路径拒绝。
9. V1 全量测试继续通过。

浏览器、视觉和人工验收单独记录，自动测试通过不等于人工验收通过。

单页增量、指标与计划生命周期的完整内置 Browser 清单以 [权威架构报告第 12 节](../../../docs/architecture/trading-center-skill-incremental-state.md) 为准。日度 blocked 模块仍禁止页面；周度局部指标 blocked 可作为每日页内明确缺口，不因此创建替代页面。

## 9. 统一展示修正（2026-08-31 已批准）

本节收窄前述私有输入到 HTML 的投影，不放宽校验。最新真实构建和人工检查清单见权威架构报告第 18 节。

- 内部诊断不出现在页面任何位置，包括折叠内容。data_note、boundary、reconciliation、evidence_refs 与计划身份仍在私有工件，不直接渲染。业务说明若混入字段名、hash、分区、CLI 等流程文本，不渲染该段原文，必要时使用简短业务提示；不能据此把数据状态升级为完整。过滤不得误伤“前值修订、工资修订”等正常财经用语；观察文案缺失时不批量填入“观察条件待确认”。
- 持仓与买入计划按真实持有状态分开，均只接收已核验 `.US` 身份；页面去掉后缀，内部身份不变。保留旧市场数据，不修改历史分区。持仓管理计划不能放进未持仓买入页。旧数据复制的无计划技术参考可去重，不同的有效计划发生归属冲突须先修正组装，不能静默丢弃。
- 可选 `positions_plans.strategy_categories` 是用户确认的五个不同标签，顺序保留；行上可选 `strategy_category` 必须属于这五个标签。缺失则明确待确认，未分配行归“待分类”；没有买入候选时展示空态，不填入已有持仓。策略分类独立于技术 setup。
- 生成器须从美股明细重算日度计数，方可写入 `operations.market_scope=US`；不得因过滤了可见行就沿用全市场总数。周度渲染副本的 `meta.market_scope=US` 也只在操作、计划及指标均来自核验美股范围后填写。旧导出未声明范围的数值不展示为美股比例；这不是数据库结构迁移，也不修改旧汇总。
- 完整日周合并事件由生成器放入 `events.groups`，附可选 RFC3339 `reference_at` 表示事件核对时间，不能改用历史复盘日或刷新浏览器的系统日期。renderer 从纽约参考日算出本周和下周共 14 个日期桶。输入 group.date 仍是上海日期，必须先通过同一时刻校验再分桶。
- 带 `reference_at` 的完整合并日历必须在私有 `events.coverage` 中分别保留宏观日历、联储讲话和持仓相关财报三类覆盖；状态可以是 complete、partial、empty 或 stale，但不得省略一类来冒充完整。常驻发布不得用缺失/更早的日历核对时点，或缺少当前 14 日窗口内既有事件的新包静默覆盖当前成功页面；取消必须作为同一事件的明确状态保留。进入新周后，已经落在新 14 日窗口之外的旧事件可以正常退出。
- `reference_at` 表示新版合并日历，周度历史 events 不再另加长列表；旧输入无该字段时，仅兼容时间可解析的周度事件，影响未知保持待补充，不能把 boundary 解释为影响事实。日期桶之外的事件不进入这两周展望。去重保留不同对象/时刻，并把冲突状态降为待核对。
- 事件可选 `watch_for`、`kind`（macro/earnings/fed_speech）、`speaker`、`source_url`；讲话必须有姓名和经允许的官网 HTTPS 来源。来源与链接只留私有证据，HTML 不显示来源、不带该 URL，也不设置“观察点与来源”控件；仍严格校验官方域名等安全条件。无脚本/无外部请求的页面边界不变。
- 不显示常规“预期”与“部分可用”事件标签；保留明确取消、具体信息冲突或陈旧的简短提示，不能因时间已过就自行改为“已发生”。`watch_for` 的有用情景直接展示，可按换行分段；说明相对预期偏高/偏低或偏鹰/偏鸽时的影响及关键反例，不伪造公布值、预测值或确定涨跌。失业/裁员、就业增长、油库存、国债拍卖等必须分别解释，金银与原油也不能机械同向。
- 可选 `events.coverage` 的 label/status/note 保留私有核验；非完整覆盖不能被 complete 父状态掩盖。成功空、未发布、读取失败、覆盖不全与陈旧在证据中区分；无事件日期统一简洁显示“暂无已收录事件”，不追加覆盖警告，也不宣称已穷尽所有事件。默认只展开核对日，日期概览可扫读。
- 使用原生 radio/checkbox/details；筛选无结果需说明如何复原。仅按用户实际桌面场景检查，双周日期不强制七列横向阅读。日/周日期、行情时间和排期核对时间独立；纯展示重建与刷新静态页均不能假更新市场/排期来源时间。
