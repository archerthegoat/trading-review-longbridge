# 分析 Skill 路由与 Longbridge 能力边界

本项目需要分析时，优先复用当前 Codex 会话中可发现的 `trading-research-system` Skill；不要默认由交易中心 Skill 自行泛化投资分析。Skill 负责路由、证据组织和输出结构，确定性脚本负责字段映射、日期过滤、价格/EMA 计算、去重和脱敏。

## 路由表

| 需求 | 首选 Skill | 用途 | 项目边界 |
| --- | --- | --- | --- |
| 周度复盘、下周交易规划、周末深度更新 | `weekly-trading-plan` | 汇总上周过程、市场状态、宏观/事件、持仓影响和候选计划 | 不把候选池写成买卖指令；Longbridge 成交/订单仍需本线程单独授权 |
| 每日盘前、盘面变化、点位滚动更新 | `daily-market-tracking` | 只输出相对上一版计划发生的变化、触发接近/失效和下一次检查 | 公开行情需带时间戳；Longbridge 默认只同步当前持仓 |
| 实际交易复盘、成交事实、开仓/平仓复盘 | `trade-review` | 对照计划与实际执行，记录信号、纪律、结果和可复用规则 | 只有本线程明确授权后才读取 Longbridge 成交/订单；未授权时只记录用户口述 |
| 宏观、行业、公司、候选标的研究 | `macro-equity-research` | 形成 Macro Regime、Financial Conditions、行业/公司 thesis 和候选池 | Longbridge macrodata 不是政策或新闻的唯一来源；需官方/高可信来源核验 |
| 请求不明确或跨多个研究流程 | `trading-research` | 选择最具体的子 Skill 并按自然顺序编排 | 不能用路由 Skill 绕过授权门禁 |
| 研究报告、PDF、链接或文章核验 | `research-report-intake`（如当前会话可用） | 生成摘要、Claim Ledger 和 Verification Queue | 文章是 thesis 输入，不直接作为事实或交易依据 |
| 组合暴露、仓位集中、风险预算 | `portfolio-risk`（如当前会话可用） | 将已授权持仓事实转成组合风险读数 |

## Longbridge 三类能力不要混用

参考已安装研究插件中的 `longbridge-skill-adapter`：

- `longbridge_broker_skill`：可提供持仓、成交/交易、订单状态等 broker-live 事实；本项目默认只允许持仓，成交和订单需要本线程再次明确授权。
- `longbridge_terminal_cli`：读取用户保存的只读 CLI JSON，映射为持仓快照；不得运行下单、撤单、改单等命令。
- `longbridge_macrodata`：提供利率、收益率、信用、美元、原油和流动性等宏观指标；它不证明账户持仓或成交，也不能替代政策/新闻的官方来源。

Longbridge broker skill、Terminal CLI 和 macrodata 的状态必须分别标记为 `available`、`unauthorized`、`not_installed`、`missing` 或 `stale`。一个能力可用，不代表另外两个能力可用。

## 每次分析前的门禁

1. 先判断当前需求属于上表哪一类；能使用具体 Skill 时，不用泛化的“投资分析”流程。
2. 检查该 Skill 是否在当前 Codex 会话中可发现、可调用。只在其他聊天或磁盘上存在但当前会话没有暴露时，标记为 `not_installed`；不声称已经调用，也不擅自安装。
3. 先运行项目已有的确定性脚本完成机械事实；Skill 只负责研究组织、证据分层、反方与失效条件，不重新计算关键数值。
4. 在输出中注明：`分析 Skill`、`数据能力`、`数据时间戳`、`证据状态` 和 `下一次核验`。
5. Skill 的通用能力不能扩大本项目授权：Longbridge 默认只读当前持仓；成交、订单、资金、盈亏和账户报表必须逐次授权；任何 Skill 都不得执行券商写操作。
6. 若没有合适 Skill，退回到“脚本事实 + 用户口述 + 明确缺口”的受限分析，并写明 `无可用分析 Skill，未进行外部投资推断`。

## 推荐输出顺序

`事实 -> 假设 -> 多头/空头逻辑 -> 失效条件 -> 组合风险 -> 下一步核验`。

把 Skill 生成的候选、观察区和触发条件标记为研究输出，不等同于下单建议，也不自动写入 Wiki；经用户确认后，才把脱敏后的计划摘要写入 Wiki。
