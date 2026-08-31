# Longbridge 交易复盘导入契约

本契约只覆盖交易复盘 V2 可以消费的 Longbridge 只读能力。它描述字段边界，不证明当前账户权限、实时可用性或返回完整性；每次运行必须重新检查。

## 1. 允许能力

| 能力 | V2 用途 | 允许聚合 | 明确禁止 |
| --- | --- | --- | --- |
| assets | account 私有校验与 SQLite 白名单投影 | 总资产、现金、购买力、基础币种、快照时间 | 账户标识、原始资产行、历史账户状态、页面金额展示 |
| positions | account、positions_plans | 当前读取时快照、标的和角色映射 | 由快照推断成交、收盘持仓或历史持仓 |
| order history | operations | 指定窗口订单数量和标的级脱敏关系 | 订单编号、完整订单行、价格、成本 |
| order executions history | operations | 指定窗口成交数量和标的级脱敏关系 | 成交编号、完整成交行、价格、成本 |
| quote | market | 实际返回标的或代理的最新值、涨跌幅、阶段和时间 | 捏造 symbol、把代理标成指数本体 |
| capital | market | 标的级资金流和时间窗口 | 全市场资金流、方向推断、因果结论 |
| market-temp | market | Longbridge 返回的市场温度字段和时间 | 把温度单独解释成确定性风险偏好 |
| finance-calendar | events | 相关事件、双时区、状态、来源和风险通道 | 无关财报、完整日历复制、无排期占位 |
| macrodata | market、events | Longbridge 返回且通过筛选的宏观字段/事件 | 外部政策事实、完整新闻或宏观馈送 |
| kline history | 条件式计划证据 | 已完成 1D OHLCV，明确复权/时区，EMA20/50/200、ATR14、可追溯结构位 | 全市场扫描、未完成日线、SMA 替代、其他 provider 回退 |

V2 不调用其他券商，也不使用外部数据源回退。Longbridge 不支持、未授权、限流、字段缺失或返回错误时，保留状态并停止事实升级。

## 2. 每日时间窗口

review_date 由上游 Longbridge 交易日历选择为 America/New_York 的已完成美股交易日；renderer 只独立校验周一至周五和窗口，不独立核验交易所假日。

订单和成交使用该纽约日历日的半开窗口：

- ny_start
- ny_end

同时记录 RFC 3339 UTC 半开窗口：

- utc_start
- utc_end

RFC3339 必须含 `T`、秒和时区；纽约端点必须精确为 review_date 当地 00:00:00 至次日当地 00:00:00，America/New_York offset 与相应日期由 ZoneInfo 验证，UTC 端点必须与纽约端点表示同一时刻。

generated_at 必须是带 `+08:00` offset 的 Asia/Shanghai RFC3339 时间。market_as_of 和 account_snapshot_at 分别记录其来源时间。

只有成功解析的空数组才可以写“接口在该窗口返回 0 条”。失败、限流、部分返回或结构不完整不能写成空数组。

## 3. 当前快照与历史事实

positions 和 assets 的成功返回只能表示读取时快照。它们不等于：

- 收盘持仓。
- 历史持仓。
- 成交事实。
- 完整账户对账。
- 整周资产结果。

订单/成交只有在指定窗口成功解析后才进入 operations。快照净变化不能单独证明订单或成交。

## 4. 市场资产目标

优先查询 Longbridge 实际支持的：

- 标普 500。
- 纳斯达克 100。
- 比特币。
- 原油。
- 黄金。
- 美国十年期国债收益率或可用代理。

如果只能取得 ETF 或其他代理，必须展示 Longbridge 返回的真实标的或代理名称，并明确代理关系。不得捏造 symbol。

市场 item 必须同时提供 `is_proxy` 布尔值和 `proxy_for`：代理时为 true 且 proxy_for 非空，非代理时为 false 且 proxy_for 为 null。页面只有在已声明代理时才显示代理关系。

capital 字段只可称为“标的资金流”。不得由价格乘成交量推断资金方向，不得把单一标的流量写成全市场流量。

## 5. 事件筛选

事件来源只使用 Longbridge finance-calendar 和 macrodata 返回的字段。

财报必须：

- 能唯一映射为美股 ticker。
- 命中当前持仓 underlying、本周有效计划或明确候选池。

宏观、政策、监管和行业事件必须对美股整体或当前持仓/计划存在明确风险通道。没有官方排期时不生成 Fed 占位行。

每条事件必须保留：

- Asia/Shanghai 时间。
- America/New_York 日期和时间（`et_date`、`et_time`）。
- 具体事件名称。
- 状态：已发生、预期、未公布或未验证。
- 来源和数据状态。
- 影响对象和风险通道。

事件组日期固定表示 Asia/Shanghai 日历日；`date + shanghai_time` 与 `et_date + et_time` 必须转换为同一时刻。

## 6. 导入前后检查

1. 在当前运行中确认 Longbridge、只读、具体能力和具体日期窗口。
2. 先运行增量 collection plan；cache hit 的已完成交易日不重复读取。
3. 对 CLI 先做能力/Schema 检查，再运行计划要求且获授权的最小读取。
4. 保存原始响应和机械输出到私有运行目录，不复制到 Git 或 SQLite。
5. 对输入执行字段白名单、结构、时间和敏感值检查，生成 `trading-review-incremental-input.v1`。
6. 运行增量 ingest，回读不含账户数值和标的事实的 manifest。
7. 输出 V2 脱敏事实包，注明来源、时间、覆盖范围和缺口。
8. 仅在所有关键门禁通过后生成 HTML。

CLI 入口示意：

~~~bash
longbridge check --format json
longbridge assets --format json
longbridge positions --format json
longbridge order --history --start <ny-start> --end <ny-end> --format json
longbridge order executions --history --start <ny-start> --end <ny-end> --format json
~~~

实际版本、参数和权限必须在运行前重新核验。任何可能执行写操作的命令都不属于本契约。

新周度运行不再读取/投影 profit-analysis、by-market 或 cash-flow；v2 历史表仅保留，v3 执行指标不得从其中推算。为了核验具体规则，授权的机械评估可在私有临时目录使用原始执行价格/时点，输出仅为分类标签；原始价格仍不得进入 SQLite、Codex 输入、Markdown 或 UI。

计划 Kline 每次单标的构造，调用方将整轮名单限制在已批准持仓/候选池内且最多 20 个，窗口不超过 550 自然日。至少 319 根已完成日线；剔除最多一根末尾未完成线后，验证价格、成交量、唯一纽约市场日期、顺序、窗口和 source.as_of。数据超过五个自然日或来源参数不可核验时 blocked。实际命令参数必须从当前可执行版本核验，不能从此文档猜测 CLI 参数。

## 7. 状态规则

| 情况 | 状态 | 处理 |
| --- | --- | --- |
| 字段齐全且时间有效 | complete | 可进入 V2 模块 |
| 部分字段或范围缺失 | partial | 展示可用部分和缺口 |
| 成功空数组 | empty | 明确记录成功空结果 |
| 数据超过允许新鲜度 | stale | 保留时间，不升级为当前事实 |
| 权限、返回、结构或隐私失败 | blocked | 不生成成功 HTML |

失败不能渲染成成功空列表，也不能使用计划、用户口述或旧快照替代 Longbridge 事实。

父子状态必须一致：empty 父模块不得含子项，market/account/order/position/event 的 empty 子项不得有非空事实数值或事实文本；complete 父模块不得含非 complete 子项；partial/stale 若没有可解释的非 complete 子项必须提供 note，blocked 子项不得被父级掩盖。
