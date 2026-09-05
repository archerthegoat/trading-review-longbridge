# 交易日记助手与 Al Brooks PA

本仓库维护一个“动态当前计划 + 历史快照 + 每日复盘 + 独立 PA 分析”的窄流程。`daily-trade-journal` 负责读取当前计划和适用的事前快照、核对上一完成美股交易日的必要只读事实、采访用户并编排复盘及计划变化；`al-brooks-pa` 只针对用户已经筛选的标的，用日线判断 Cycle、量价、EMA10/20 和位置，盘中只用已完成 1H K 线作为操作依据。

## 日记编排流程

日记入口先读取 `25 投资交易/30 交易计划/当前交易计划.md`、适用的 `历史快照/*.md`、相关旧日记和当天旧记录，再按需用 Longbridge CLI 只读核对上一完成美股交易日的成交与必要持仓字段。历史核对不能使用今天更新后的当前计划倒推昨天的交易。

先恢复既有讨论、读取成交与标的走势，展示可核验的计划对齐，再采访主观理由和剩余缺口。尚未检查写“待核验”，已查仍缺证据才说明具体无法核对原因。观察不能升级为执行计划；客观证据足够时不因用户尚未解释动机而否定对齐。用户事后确认计划内独立记录，不补造事前证据。

晨间分别标明北京时间 run_date、最近已完成的美股 review_date、当晚 plan_date。周一通常补看上周五，休市按交易日历处理；当晚休市不自动顺延计划。复盘只用成交时已完成且可获得的 K 线，晨间计划用最新完成数据制定未来条件。具体时区、快照选择及脱敏输入见 [来源与记录契约](skills/daily-trade-journal/references/storage-and-recording.md)。

展示每日复盘草稿和当前计划变化后必须等待用户明确确认。日常写入目标是：

- `25 投资交易/10 每日复盘`
- 有确认后的计划变化时再写 `25 投资交易/30 交易计划`

`25 投资交易/20 周度复盘` 只在周度回顾任务中汇总每日复盘，不是每日默认写入目标。无成交仍检查持仓、当晚计划和未决事项；均无待处理变化才返回 `no_op`。单项缺数据不阻塞其他独立分析。写入前重读 live 文件并检查并发；写后先做文件系统回读，再按顺序做 Obsidian CLI 回读。

## Al Brooks PA 边界

PA Skill 面向用户已筛选的做多标的，不选股、不做基本面研究、不下单、不管理账户、不自动写 Obsidian。日线判断底部反转/wedge pop、突破后整理/二次进场、extension/buy climax 等阶段，并结合量价和 EMA10/20；盘中只用已完成 1H K 线确认做多信号。到达关键位置不是买点，输出必须包含位置门槛、1H 信号、跟随/二次进场和失效后等待。

PA 参考按需分层：分析流程与证据门禁见 [pa-framework.md](skills/al-brooks-pa/references/pa-framework.md)，用户做多 Cycle 与具体 setup 见 [setup-catalog.md](skills/al-brooks-pa/references/setup-catalog.md)，稳定的脱敏表达偏好见 [personal-trading-profile.md](skills/al-brooks-pa/references/personal-trading-profile.md)。输出按日线阶段与位置、1H 做多信号、跟随/二次进场和失效后等待收敛。

## 日内交易 Set

QQQ 等已筛选标的的日内交易 Set 只是当日有效计划模式，不建立 UI、数据库或后台执行状态。Set 在新的确认版本中形成或修订；当天生成日记时，`$al-brooks-pa` 的关注清单随草稿生成。Set、观察清单和计划都不会触发自动下单。

## 数据、隐私与失败状态

broker fact 与 journal 状态分开：前者是 `complete`/`empty`/`blocked`，后者是 `interview_required`/`awaiting_confirmation`/`written`。脱敏入口的 `complete` 仅表示输入批次所需字段通过校验；采集成功、分页和窗口覆盖需另行核验，不代表账户完整性、PA 条件成立或日记已写入。旧投影器没有行情输入，只提供字段初步核对；最终结论需计划与成交时可见走势。内存脱敏入口为 [sanitize_broker_facts.py](skills/daily-trade-journal/scripts/sanitize_broker_facts.py)，解析失败不输出原始字段。

公开投影、普通日志和草稿不包含账户/请求标识、原始券商响应、凭据、订单或成交 ID、价格数量费用、余额盈亏、完整期权合约、到期日、行权价或原始代码。只读核对不修改订单；不读取余额、购买力或盈亏，不恢复 Web UI、SQLite、Bridge，不更改自动化配置。

本地验证只证明对应约束：两个 Skill 的 `quick_validate`、Python 回归测试和 `git diff --check` 不等于真实 Longbridge 核对、Obsidian 写入、自动化运行或人类验收。真实外部写入与人类验收在本批均需单独回读，未验证时保持 `NOT RUN`/`PENDING`。
