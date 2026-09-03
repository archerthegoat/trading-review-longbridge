# 交易日记助手与 Al Brooks PA

本仓库维护一个“日记编排 + 独立 PA 分析”的窄流程。`daily-trade-journal` 负责读取已确认计划、核对上一完成美股交易日的必要只读事实、采访用户和编排草稿；`al-brooks-pa` 只针对用户已经筛选的标的，用日线作背景、1H 作执行参考，输出条件化 Al Brooks Price Action 分析和 `盘中关注要点`。

## 日记编排流程

日记入口先读取 `25 投资交易` 中的当前确认计划、相关旧日记和当天旧记录，再用 Longbridge CLI 只读核对上一完成美股交易日的成交与必要持仓字段。核对按确认时间、`underlying`、动作和工具进行；期权 `right` 在内部对齐为 `Call`/`Put`，公开结果不暴露合约身份。已有 `intraday_revisions` 是优先复用的计划输入。

先展示脱敏 broker facts 和计划对齐结果，再最多询问两条必要问题。只有用户回复后才显式调用 `$al-brooks-pa`，并把分析生成的盘中关注要点带入 Markdown 草稿。观察是有效的计划状态，但不会自动执行交易；只有明确相反或禁止才是“偏离计划”，缺失、不完整、未确认或时间无效证据都是“无法核对”。用户没有当日叙述时不自行补写。

展示草稿后必须等待用户明确说“确认写入”，才写入现有 Obsidian 目录：

- `25 投资交易/10 每日复盘`
- `25 投资交易/20 周度复盘`
- `25 投资交易/30 已确认计划摘要`

无变化返回 `no_op`。写入前重读 live 文件并检查并发；写后先做文件系统回读，再按顺序做 Obsidian CLI 回读。

## Al Brooks PA 边界

PA Skill 面向用户已筛选的单一标的或交易 Set，不选股、不做基本面研究、不下单、不管理账户、不自动写 Obsidian，也不调用其他研究 Skill。它可以在证据足够时描述趋势、交易区间、通道、突破/失败、信号 K、跟随、回踩、微型双底/双顶和 measured move；每项均区分用户自述、数据事实和分析推断，并注明周期、来源、截至时间及 K 线是否完成。事件、触发、观察窗口、支持证据和反证都明确时，可给约 40–50%、50–60% 或 60–70% 的宽区间主观估计；这不是 Brooks 固定分档或回测胜率，缺口时暂不量化，且标的方向不等于 Long Call/Long Put 盈利概率。

PA 参考按需分层：分析流程与证据门禁见 [pa-framework.md](skills/al-brooks-pa/references/pa-framework.md)，五类 setup 见 [setup-catalog.md](skills/al-brooks-pa/references/setup-catalog.md)，稳定的脱敏表达偏好见 [personal-trading-profile.md](skills/al-brooks-pa/references/personal-trading-profile.md)。输出仍收敛为简短结构判断、1–3 个条件情景和盘中关注要点。

## 日内交易 Set

QQQ 等已筛选标的的日内交易 Set 只是当日有效计划模式，不建立 UI、数据库或后台执行状态。Set 在新的确认版本中形成或修订；当天生成日记时，`$al-brooks-pa` 的关注清单随草稿生成。Set、观察清单和计划都不会触发自动下单。

## 数据、隐私与失败状态

broker fact 与 journal 状态分开：前者是 `complete`/`empty`/`blocked`，后者是 `interview_required`/`awaiting_confirmation`/`written`。broker `complete` 仅表示所需只读响应可解析，不代表账户、成交或 PA 结论；任何缺口均保留 `blocked`、`PENDING` 或 `NOT RUN`，不能把缺失变成无成交或已验证。

公开投影、普通日志和草稿不包含账户/请求标识、原始券商响应、凭据、订单或成交 ID、价格数量费用、余额盈亏、完整期权合约、到期日、行权价或原始代码。只读核对不修改订单；不读取余额、购买力或盈亏，不恢复 Web UI、SQLite、Bridge，不更改自动化配置。

本地验证只证明对应约束：两个 Skill 的 `quick_validate`、Python 回归测试和 `git diff --check` 不等于真实 Longbridge 核对、Obsidian 写入、自动化运行或人类验收。真实外部写入与人类验收在本批均需单独回读，未验证时保持 `NOT RUN`/`PENDING`。
