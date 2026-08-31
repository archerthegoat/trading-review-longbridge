# Longbridge 看板数据契约

本文件只定义交易复盘 Web UI V2 可消费的 Longbridge 只读字段边界。它不是当前账户权限或实时接口可用性的证明；每次运行仍需在私有运行目录验证权限、返回结构、时间范围和数据状态。

## 1. 来源边界

V2 只接受以下输入：

1. Longbridge 只读事实。
2. 项目内已经确认的周度计划和每日增量。
3. 2026-08-31 明确批准的限定例外：美联储及地区联储官网公开讲话排期，仅用于事件补充。不能借此补行情、财报或任意新闻。

不接入其他券商、外部行情、新闻、宏观数据提供商或远程摘要服务。除上述讲话排期例外，Longbridge 不支持、未授权、字段不完整或返回失败时，保留对应状态，不用其他来源补齐。

原始 Longbridge 响应只能留在：

/private/tmp/trading-center-review-runtime/<run-date>/

页面、测试夹具和项目文件只使用经过字段白名单和隐私检查的脱敏聚合。

## 2. 能力与 V2 模块映射

| Longbridge 能力 | V2 模块 | 可展示的最小信息 | 不能推断 |
| --- | --- | --- | --- |
| assets | account（私有校验） | 总资产、现金、购买力、基础币种、快照时间进入私有校验与 Codex 输入，不渲染 | 周初/周末历史值、跨账户完整性、对账完成 |
| positions | account（私有校验）、positions_plans | 读取时的标的、持仓状态和计划关联 | 收盘持仓、历史持仓、成交事实 |
| order history | operations | 前一美股交易日的脱敏订单数量、窗口和标的级聚合 | 订单编号、完整订单行、价格和成本 |
| order executions history | operations | 前一美股交易日的脱敏成交数量、窗口和标的级聚合 | 成交编号、完整成交行、价格和成本 |
| quote | market | Longbridge 真实标的或代理的最新值、涨跌幅、阶段和时间 | 未返回标的的行情、指数本体与代理的等价性 |
| capital | market | 标的级资金流、标的和时间窗口 | 全市场资金流、市场方向或因果关系 |
| market-temp | market | Longbridge 返回的市场温度及时间 | 风险偏好的确定性结论 |
| finance-calendar | events | 相关美股财报或有风险通道的事件、双时区、状态和来源 | 与持仓/计划无关的事件、无来源的事件影响 |
| macrodata | market、events | Longbridge 能提供且通过相关性筛选的宏观字段或事件 | 官方政策结论、完整宏观日历、新闻事实 |
| profit-analysis | account、data_note | 在授权和时间口径均满足时的明确盈亏聚合 | 未授权时的账户盈亏、收盘对账、资金流因果 |

capital 只能写作“标的资金流”。不得用价格乘成交量推断资金方向，也不得把单一标的资金流改写为“全市场资金流”。

## 3. 时间契约

### 3.1 每日

- review_date 由上游 Longbridge 交易日历选择为 America/New_York 的已完成美股交易日；renderer 只独立校验周一至周五和窗口，不独立核验交易所假日。
- 订单和成交使用该纽约日历日的半开窗口 [ny_start, ny_end)。
- 同时记录 RFC 3339 UTC 半开窗口 [utc_start, utc_end)。
- RFC3339 必须含 `T`、秒和时区；纽约端点必须精确为 review_date 当地 00:00:00 至次日当地 00:00:00，America/New_York offset 与相应日期由 ZoneInfo 验证，UTC 端点必须与纽约端点表示同一时刻。
- generated_at 必须是带 `+08:00` offset 的 Asia/Shanghai RFC3339 时间。
- market_as_of 和 account_snapshot_at 必须各自保留来源时间。
- 当前持仓和账户均为读取时快照，不代表收盘持仓或完整对账。

只有成功解析的订单/成交空数组才可显示“该窗口返回 0 条”。接口失败、限流、部分返回或关键字段缺失必须标为 partial 或 blocked，不得变成空数组。

### 3.2 周度

周度账户结果、整周订单/成交和盈亏必须使用明确授权的完整 America/New_York 交易周窗口。不能使用周五快照替代整周结果，也不能使用上一交易日数据填充缺失周度字段。

## 4. V2 状态

模块状态允许：

- complete：必需字段齐全且可核验。
- partial：部分字段或范围缺失，但剩余内容可区分。
- empty：接口成功且返回空结果。
- stale：结果存在但超过当前运行允许的新鲜度。
- blocked：接口、权限、结构或隐私门禁不允许继续。

整体状态只允许 complete、partial、blocked。empty 和 stale 作为模块状态保留。

状态一致性规则：empty 父模块不得有子项；market/account/order/position/event 的 empty 子项不得有非空事实数值或事实文本；complete 父模块不得有非 complete 子项；partial/stale 在存在可解释的非 complete 子项时保留子项状态，否则必须有解释性 note；blocked 子项不能被父级掩盖。成功空事件在日报 Markdown 中固定为事件名“无已确认事件（相关筛选后）”、状态“已发生”、来源“相关筛选已完成并返回空”，表示筛选完成而非市场事件。

## 5. 资产与代理规则

市场概览目标为：

- 标普 500。
- 纳斯达克 100。
- 比特币。
- 原油。
- 黄金。
- 美国十年期国债收益率或 Longbridge 可用代理。

每次先查询 Longbridge 实际支持的标的。如果只能使用 ETF 或其他代理，必须展示 Longbridge 返回的真实标的名称，并明确它是代理。不得捏造 symbol，不得把代理名称改写成指数本身。

市场 item 必须同时提供 `is_proxy` 布尔值和 `proxy_for`：代理时为 true 且 proxy_for 非空，非代理时为 false 且 proxy_for 为 null。页面只有在已声明代理时才显示代理关系。

事件组日期固定是 Asia/Shanghai 日历日；`et_date + et_time` 必须和 `date + shanghai_time` 转换为同一时刻。成功相关筛选为空时记录 empty，不将空结果改写为未验证。事件 item 为空时不携带事实字段。

## 6. 私有 account 字段与页面边界

V2 私有 JSON 允许校验的账户字段只有：

- 聚合金额。
- 基础币种。
- 快照时间。
- 脱敏状态和数据范围。

这些字段不渲染到 HTML。页面不得出现账户概览、金额、基础币种、账户快照时间或金额显隐控件；它们只用于私有事实校验和 Codex 条件式判断。

禁止字段和值：

- 账户标识。
- 订单/成交/交易 ID。
- 凭据、Cookie、API key、令牌和密码。
- 成本、佣金和完整对账单。
- 原始券商响应或未投影字段。
- 后台模型、reviewer、tool、agent、人工/浏览器验收或 Schema/V2 调试状态；Codex 作为用户要求的分析标签可以保留。

renderer 在生成 HTML 前执行递归字段白名单、结构校验、值级敏感信息检查和输出路径检查。任一检查失败，不生成成功 HTML。

## 7. 失败与回退

除已批准的官网讲话排期补充，V2 没有外部数据回退。失败处理顺序固定为：

1. 保留成功读取的确定性模块。
2. 对失败模块写明脱敏错误类别和数据状态。
3. 若关键字段不可用，整体标为 partial 或 blocked。
4. 不把失败渲染成成功空列表。
5. 不用计划、旧快照、用户口述或测试夹具替代 Longbridge 已验证事实。
