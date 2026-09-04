# 交易日记助手与 Al Brooks PA

本仓库维护一个“动态当前计划 + 历史快照 + 每日复盘 + 独立 PA 分析”的窄流程。`daily-trade-journal` 负责读取当前计划和适用的事前快照、核对上一完成美股交易日的必要只读事实、采访用户并编排复盘及计划变化；`al-brooks-pa` 只针对用户已经筛选的标的，用日线判断 Cycle、量价、EMA10/20 和位置，盘中只用已完成 1H K 线作为操作依据。

## 日记编排流程

日记入口先读取 `25 投资交易/30 交易计划/当前交易计划.md`、适用的 `历史快照/*.md`、相关旧日记和当天旧记录，再按需用 Longbridge CLI 只读核对上一完成美股交易日的成交与必要持仓字段。历史核对不能使用今天更新后的当前计划倒推昨天的交易。

先恢复既有讨论并采访用户，再读取必要事实、做计划对齐和 PA 更新。观察是有效的计划状态，但不会自动执行交易；只有明确相反或禁止才是“偏离计划”，缺失、不完整、未确认或时间无效证据保持“无法核对”。用户确认操作来自未持久化盘中计划时，记录计划内与历史记录缺口，不把缺口判为偏离。

展示每日复盘草稿和当前计划变化后必须等待用户明确确认。日常写入目标是：

- `25 投资交易/10 每日复盘`
- 有确认后的计划变化时再写 `25 投资交易/30 交易计划`

`25 投资交易/20 周度复盘` 只在周度回顾任务中汇总每日复盘，不是每日默认写入目标。无变化返回 `no_op`。写入前重读 live 文件并检查并发；写后先做文件系统回读，再按顺序做 Obsidian CLI 回读。

## Al Brooks PA 边界

PA Skill 面向用户已筛选的做多标的，不选股、不做基本面研究、不下单、不管理账户、不自动写 Obsidian。日线判断底部反转/wedge pop、突破后整理/二次进场、extension/buy climax 等阶段，并结合量价和 EMA10/20；盘中只用已完成 1H K 线确认做多信号。到达关键位置不是买点，输出必须包含位置门槛、1H 信号、跟随/二次进场和失效后等待。

PA 参考按需分层：分析流程与证据门禁见 [pa-framework.md](skills/al-brooks-pa/references/pa-framework.md)，用户做多 Cycle 与具体 setup 见 [setup-catalog.md](skills/al-brooks-pa/references/setup-catalog.md)，稳定的脱敏表达偏好见 [personal-trading-profile.md](skills/al-brooks-pa/references/personal-trading-profile.md)。输出按日线阶段与位置、1H 做多信号、跟随/二次进场和失效后等待收敛。

## 日内交易 Set

QQQ 等已筛选标的的日内交易 Set 只是当日有效计划模式，不建立 UI、数据库或后台执行状态。Set 在新的确认版本中形成或修订；当天生成日记时，`$al-brooks-pa` 的关注清单随草稿生成。Set、观察清单和计划都不会触发自动下单。

## 数据、隐私与失败状态

broker fact 与 journal 状态分开：前者是 `complete`/`empty`/`blocked`，后者是 `interview_required`/`awaiting_confirmation`/`written`。broker `complete` 仅表示所需只读响应可解析，不代表账户、成交或 PA 结论；任何缺口均保留 `blocked`、`PENDING` 或 `NOT RUN`，不能把缺失变成无成交或已验证。

公开投影、普通日志和草稿不包含账户/请求标识、原始券商响应、凭据、订单或成交 ID、价格数量费用、余额盈亏、完整期权合约、到期日、行权价或原始代码。只读核对不修改订单；不读取余额、购买力或盈亏，不恢复 Web UI、SQLite、Bridge，不更改自动化配置。

本地验证只证明对应约束：两个 Skill 的 `quick_validate`、Python 回归测试和 `git diff --check` 不等于真实 Longbridge 核对、Obsidian 写入、自动化运行或人类验收。真实外部写入与人类验收在本批均需单独回读，未验证时保持 `NOT RUN`/`PENDING`。
