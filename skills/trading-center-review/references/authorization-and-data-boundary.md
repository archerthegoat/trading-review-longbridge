# 授权与数据边界

## 默认边界

- 可读：项目内公开契约、模板、脱敏 fixtures、当前周计划和用户在当前运行中主动提供的计划信息。
- Longbridge 只做当前运行明确授权的只读读取。
- 不接入其他券商，不下单、不改单、不撤单，不读取或输出凭据、Cookie、API key、账户标识、订单/成交 ID、成本或原始响应。
- 原始账户、持仓和市场响应只写入 Git 工作树外的私有运行目录；只有通过固定白名单的精确投影可写入 owner-only SQLite。
- AGENTS.md 或 CONTEXT.md 的普通缺失不单独生成用户可见状态；只有缺失导致授权、计划权威或事实边界不清时，才记录为实质缺口。
- 2026-08-31 用户批准统一 V2 修正时，明确允许从美联储及地区联储官网读取公开讲话排期。这是事件补充的限定例外，不是其他券商、行情、宏观数据商、财报或新闻回退许可；不读取登录态、不付费、不写外部系统。记录查询时点、来源与覆盖缺口，无排期不等于无讲话；参见权威架构报告第 18 节。既有自动化的更窄授权仍不扩大。

## Longbridge 权限

Skill 本身不产生权限。当前默认可例行同步的最小事实是 positions 当前快照；外部自动化若已为当前运行声明更窄的默认只读授权，可在该边界内执行。每日订单和成交只覆盖当前运行允许的前一 America/New_York 美股交易日窗口；assets、quote、capital、market-temp、finance-calendar、macrodata 和 profit-analysis 只在能力、字段和当前运行权限均满足时读取。

扩大以下任一范围都需要新的明确授权：

- 新增 Longbridge 能力。
- 新增账户字段。
- 扩大日期窗口。
- 读取整周订单、成交、盈亏或账户结果。
- 读取资金流或对账单。
- 写入券商、Wiki、Obsidian 或其他外部系统。

能力可发现或连接检查通过，不等于账户完整、历史完整、字段有权限或已经对账。

已批准的估值扩展：只对当前美股持仓 underlying 与明确计划买入的未持有标的读取 Longbridge PE(TTM) 和可核对的年度 ROE/报告期。`compare` 必须显式指定范围内标的，单标的不使用自动补同业模式，而使用 `calc-index SYMBOL --fields pe`；[Longbridge 官方 CLI 文档](https://open.longbridge.com/docs/cli/market-data/calc-index)将 `pe` 定义为 PE (TTM)，并要求显式 symbol。ETF 不用企业 PR。`financial-report --report af` 获取年度报告，不能带会覆盖为最新季度/半年度摘要的 `--latest`。响应 root/nested 标的身份、年度利润/权益回报和期间都能核对才计算；不回退到未知期间 ROE。固定标量可入 v4 状态表，不扩展账户读取、其他来源或自动化范围。

本地已确认扩展：Longbridge `kline history` 仅用于当前持仓 underlying 与已确认候选池的计划构造，单次最多 20 个标的、每标的最近最多 550 个自然日、明确复权的已完成 1D 日线。必须先实机核验能力；不可运行时 blocked，不安装、不回退 yfinance。周度执行规则可在私有机械评估中使用已授权执行事实，但不持久化原始成交价格或券商 ID。新周度不为复盘额外读取 profit-analysis、归因或现金流。

2026-09-01 用户另行批准收盘市场环境扩展：日度复盘只读 `SPY.US`、`QQQ.US`、`IEF.US`、`GLD.US`、`USO.US`、`IBIT.US` 六个既有公开代理的最近已完成 1D 日线，固定 `adjust=none`、`session=intraday`，只取 review_date 与前一根完成日线计算收盘涨跌，查询回看最多 14 个自然日。六项收盘证据齐备时，允许每次日度复盘调用同一 Longbridge 提供商的 LongbridgeAI 公共分析 Skill 一次，使用固定收盘提示，仅接收同一 review_date 的结论、最多三条支持事实和下一交易日验证条件；不保存原始回答或引用。该扩展不新增标的、数据提供商、盘前/夜盘 quote、持续刷新、调度、账户、持仓、订单或成交权限；任一命令失败不生成新成功发布；任一收盘缺失时只保留 partial 雷达和明确缺口。

## 每日窗口

- review_date 必须是 America/New_York 的已完成美股交易日。
- 订单和成交窗口使用纽约日历日半开区间 [ny_start, ny_end)。
- 同时记录 RFC 3339 UTC 半开区间 [utc_start, utc_end)。
- generated_at 使用 Asia/Shanghai。
- 当前持仓和账户资产标记为读取时快照，不代表收盘状态。

只有成功解析的空数组才可显示“接口在该窗口返回 0 条”。失败、限流、部分成功、关键字段缺失或权限不足必须标记 partial 或 blocked，不得变成空列表。

## 输出位置

原始 Longbridge 响应、含明细的草稿和运行日志必须位于：

/private/tmp/trading-center-review-runtime/<run-date>/

V2 HTML 和 JSON 输出必须在该私有根目录下且位于 Git 工作树之外。输出文件权限为 0600，父目录权限为 0700，写入使用临时文件和原子改名。

2026-08-31 用户明确批准的限定例外：通过正式发布命令，将严格删去账户模块/标签/时间、后台说明、委托计数及无成交行的固定展示快照与 HTML 保存到 `~/Library/Application Support/MarsTradingCenter/web-ui/`。仍为 0700/0600，不能复制完整输入包或原始响应。只读 LaunchAgent 提供 `127.0.0.1:8765`，不新增采集/调度/券商/数据库写入或 Obsidian 权限。详见 [本地展示服务](local-web-service.md) 及 `docs/architecture/ts-web-and-obsidian-bridge.md`。TS 负责渲染/发布/HTTP，Python 保留证据校验；Bridge 准确 Schema/模板和保护边界已另获批准，经独立 receiver 执行，仍需每份复盘确认。其他运行保持原默认边界。

项目内允许保留：

- Skill 和 references。
- 不含真实账户或交易事实的模板。
- 不含敏感数据的测试 fixtures。
- 确定性脚本和自动化测试。

owner-only SQLite 默认位于 `/Users/archer/Library/Application Support/MarsTradingCenter/trading-review.sqlite3`。允许持久化的字段、路径门禁和禁止字段见 [增量状态与缓存契约](incremental-state-contract.md)。数据库不进入 Git、Vault、Wiki 或临时目录。

## V2 页面边界

在 Design QA 和用户浏览器 PASS 之前，V2 仅作为影子验收工件；V1 仍是生产每日主链路和回滚基线。不得把 V2 自动化通过写成生产切换或人工验收通过。

V2 页面允许展示：

- Longbridge 返回的市场字段和时间。
- 当前持仓与确认计划的关系。
- 已核验的美股实际成交及标的级脱敏说明；未成交委托、订单计数和周度操作摘要不进入 HTML。
- 相关事件、双时区和直接可读的条件式影响；来源证据留在私有包，不显示来源链接、覆盖状态标签或独立“预期”标签。真实取消、冲突和陈旧仍保留具体提示，空日期不冒充覆盖完整。
- Codex 对上述事实的结构化解释和条件式检查。
- EMA 条件式区间、明确区分的草案/确认版本、买入后再次确认的加仓草案，以及周度执行质量指标。

V2 页面禁止展示：

- 账户概览、账户金额、基础币种、账户快照时间和金额显隐控件。
- 账户标识。
- 订单、成交或交易 ID。
- 凭据、Cookie、API key、令牌和密码。
- 成本、佣金和完整对账单。
- 原始券商响应、完整新闻或日历馈送。
- 具体周度盈亏、收益率、TWR、完整归因和现金流；历史数据库保留不等于 UI 继续展示。
- 后台模型、reviewer、tool、agent、人工/浏览器验收或 Schema/V2 调试状态；只有影响当前用户动作的 Wiki 确认门可保留，Codex 分析标签可保留。

## Codex 分析边界

Codex 只读取本地确定性脚本生成的脱敏固定字段包。它可以组织事实、解释风险、对照已确认计划并列出条件式检查；不能自行读取 Longbridge、计算关键数值、替代授权或生成无条件交易指令。

分析结构必须区分：

- 已确认事实。
- 事实解释。
- 主要风险。
- 条件式检查。
- 未验证缺口。

分析失败时保留确定性事实并标记分析不可用，不把缺失摘要写成成功。

## Wiki 和 Obsidian

知识中心 receiver 是 Vault 唯一的 Bridge 写入者；交易中心只负责脱敏草稿、当前版本确认和只读入队。`confirmed-investment-review.v1` 准确文本、模板和保护边界已获批；实施授权与实际复盘确认是独立门。没有严格 DB 绑定不能入队，管理原则确认或服务长期授权不等于整份复盘完成。见 [知识中心交接边界](knowledge-handoff-contract.md)。

飞书 Wiki 写入是独立确认门。必须先展示本轮脱敏增量、目标节点和字段范围，确认后写入并回读；没有确认、写入失败或回读失败时，不显示写入完成，也不生成依赖写入完成的后续关注点。

## 失败分类

| 情况 | 状态 | 行为 |
| --- | --- | --- |
| Longbridge 能力不可发现 | not_installed / missing | 不调用、不安装、不用其他来源补齐 |
| 当前运行无对应权限 | unauthorized | 保留未验证缺口 |
| 返回失败或结构不完整 | partial / blocked | 保留脱敏错误类别 |
| 成功返回空数组 | empty | 明确记录成功空结果 |
| 超过允许新鲜度 | stale | 保留原始时间并停止当前事实升级 |
| Schema 或隐私检查失败 | blocked | 不生成成功 HTML |
