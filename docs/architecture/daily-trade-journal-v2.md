# daily-trade-journal v2 架构报告

## 1. 目标、批准范围与状态

本报告记录 2026-09-03 已确认的第一批实现：把原交易日记入口收敛为“日记编排”，并新增独立的 `al-brooks-pa` Skill，为用户已筛选标的（本批包含 QQQ 日内交易 Set 的最小场景）提供可核验的 PA 关注清单和 Call/Put 计划对齐能力。

本批包含：

- `skills/al-brooks-pa/`：独立 Al Brooks Price Action Skill 及 UI 元数据。
- `skills/daily-trade-journal/`：日记编排入口及现有离线投影脚本的窄改。
- `tests/test_project_daily_trade_journal.py`：期权方向对齐、`intraday_revisions` 复用和隐私回归。
- `README.md` 与本报告：架构、生命周期、边界和验证说明。

本批不包含选股、基本面研究、下单、真实券商访问、Obsidian 写入、UI/SQLite/Bridge、自动化配置、数据库、真实计划迁移、路线图修改、合并 `main` 或推送 `main`。人类功能验收为 `PENDING`；真实 Longbridge、真实 Obsidian 写入和自动化运行均为 `NOT RUN`，不能由本地测试升级为完成。

## 2. 角色与接口

| 组件 | 责任 | 明确不负责 |
| --- | --- | --- |
| `daily-trade-journal` | 读取确认计划和旧日记；核对上一完成美股交易日必要只读事实；最多两问采访；编排草稿和确认写入 | PA 形态分析、选股、执行交易、账户看板、自动写入 |
| `al-brooks-pa` | 面向已筛选标的，以日线背景和 1H 执行参考输出条件化 PA 与 `盘中关注要点` | 选股、下单、账户管理、Obsidian 写入、调用其他研究 Skill |
| `project_daily_trade_journal.py` | 接收已采集 JSON，做时间/计划/期权方向对齐，并生成固定脱敏投影 | 调用券商、读取数据库、写入 Obsidian、暴露完整合约身份 |

日记流程在用户完成采访回复后显式调用 `$al-brooks-pa`。PA 结果只是草稿中的观察材料；必须先展示草稿，收到用户明确“确认写入”后才进入 Obsidian 写入阶段。

## 3. 日常状态流

1. 读取 `25 投资交易` 的当前确认计划、相关旧日记和当天旧记录，保留用户手写内容。旧 immutable 版本只能只读参考，不静默迁移或覆盖。
2. 以 Longbridge CLI 只读核对上一完成美股交易日的成交和必要持仓字段。按确认时间、`underlying`、动作、工具对齐，优先复用 `intraday_revisions`；本仓库脚本只处理已采集输入，不声称已执行真实 CLI。
3. 先展示脱敏 broker facts 与对齐结果，再最多问两条必要问题。观察可以保持有效的计划状态，但不能驱动自动交易；只有明确相反或禁止的证据才是偏离计划。
4. 用户回复后显式调用 `$al-brooks-pa`，把盘中关注要点带入草稿，并区分用户自述、数据事实和分析推断。
5. 展示 Markdown 草稿。未收到“确认写入”时只保持 `awaiting_confirmation`；收到确认后才在既有目录中写入并按顺序回读。

### 双状态契约

| 状态轴 | 值 | 含义 |
| --- | --- | --- |
| broker fact | `complete` | 所需只读响应可解析；不代表账户、成交、计划匹配或 PA 结论 |
| broker fact | `empty` | 已确认的查询范围无记录；不等于所有范围无交易 |
| broker fact | `blocked` | 数据缺失、冲突、过期、非法或无法安全投影 |
| journal | `interview_required` | 尚无足够用户叙述，不能补写 |
| journal | `awaiting_confirmation` | 草稿已展示，尚未获“确认写入” |
| journal | `written` | 仅在写入及规定回读完成后使用；本批真实写入未运行 |

缺失、不完整、未确认、时间无效或不能证明同一计划版本时，alignment 保持“无法核对”。不能把缺失事实写成无成交、无持仓或已完成 PA。

## 4. PA 分析合同

`al-brooks-pa` 只接收当前上下文中用户已筛选的标的、用户自述以及明确来源的 K 线/数据。日线是背景，1H 是执行参考；周期、来源、截至时间或 K 线状态缺失时报告缺口，不能用日线替代 1H。

证据足够时可描述趋势、交易区间、通道、突破与失败、信号 K、跟随、回踩、微型双底/双顶和 measured move。每个判断分成：

- 用户自述：用户观察或主观解释，原样保留不升级为事实；
- 数据事实：来源、时间、周期和 K 线完成状态明确的可见数据；
- 分析推断：基于上述证据的条件判断，并写失效条件。

输出必须包含多周期结构、条件化场景和 `盘中关注要点`。关注点是“若……则观察……”的清单，不是自动交易指令；不强行给概率、胜率、结果评价或未经证实的目标价。期权统一使用 `Call`、`Put`、`Long Call`、`Long Put`，不以期权方向反推未经证实的标的结论。

## 5. 期权方向与公开投影

现有执行解析已经从期权代码仅在私有内存中提取 `OptionContract.right`（C/P），并根据到期日与复盘日机械分类为 `0DTE 期权` 或 `其他期权`。本批不改变公开 schema `daily-trade-journal-facts.v2`。

计划侧增加一个内部 `option_right` 维度：

- `Call`、`Long Call` -> `Call`；`Put`、`Long Put` -> `Put`；原有普通工具别名不变。
- 方向明确的计划与同 `underlying`、动作、确认时间有效且解析出的 `right` 相同的期权事实对齐；它可覆盖 0DTE 与其他期权两个既有类别，但不暴露到公开投影。
- 泛化 `其他期权` 仍按既有类别精确匹配，不推断 Call/Put，也不把 0DTE 与其他期权混同。
- 方向缺失、代码无法解析、计划未确认或版本时间不适用时保持“无法核对”；明确禁止仍是“偏离计划”。

公开 `executions` 仅保留证券的 `underlying`、动作和对齐结果；期权额外保留既有类别标签。完整合约、到期日、行权价、原始代码、账户/请求标识、订单/成交 ID、价格、数量、费用、余额、盈亏、凭据和原始券商响应不得进入公开投影、普通日志、草稿或 Git。现有明确授权的 owner-only 私有预览继续独立受限，不扩大本批授权。

## 6. QQQ 日内交易 Set 生命周期

QQQ 等已筛选标的的日内交易 Set 只是“当日有效计划模式”：

1. 用户形成或修订 Set 后，只有在确认链完成时才进入新的确认版本；观察、候选或采访回答本身不会升级成计划。
2. 当日复盘按确认时间和工具做只读对齐。Set 不建立 UI、数据库、后台执行状态或自动化任务。
3. 用户采访回复后，`$al-brooks-pa` 生成盘中关注要点；这些要点随当日日记草稿生成，不单独持久化为执行状态。
4. 用户明确确认写入后，才把用户确认段落与关注要点写入既有日记路径；失败或未确认即停止。

Set 的有效性是计划语义，不是券商执行授权；它不会让任何交易自动按计划发生。

## 7. 失败矩阵与回滚

| 阶段 | 失败/未知 | 处理 | 回滚边界 |
| --- | --- | --- | --- |
| 输入/日历 | 数据缺失、来源过旧、时间无效或窗口不明 | 保留原始错误，结果为 `blocked` 或 alignment“无法核对” | 不写公开成功结果 |
| 计划选择 | 未确认、确认时间晚于成交、冲突版本、动作/工具不完整 | 不猜测；不把观察升级计划 | 保留旧确认版本 |
| 期权解析 | right/日期/身份结构非法或方向不一致 | fail closed；不得降级为正股或泛化匹配 | 不产生私有预览/成功投影 |
| 采访 | 没有用户叙述或缺口未补齐 | 最多两问，保持 `interview_required` | 不生成日记事实 |
| 草稿 | 用户未明确“确认写入” | 只展示草稿，保持 `awaiting_confirmation` | 不触碰 Vault |
| 写入前 | live 文件并发变化 | 重读并停止，不覆盖新内容 | 保留原文件 |
| 写入后 | 文件系统或 Obsidian CLI 回读失败 | 报告“写入未验证”，停止后续写入 | 不自动覆盖或声称成功 |

本批不迁移真实计划/日记、不配置自动化、不改 UI/SQLite/Bridge。代码回退使用后续反向提交，不改写 `main` 历史；任何真实外部写入仍须另行确认。

## 8. 依赖、维护与验证

运行依赖仅为现有 Python 标准库和已确认的输入文件；脚本不引入 Longbridge SDK、数据库或新的适配层。Longbridge CLI 真实可用性、账户授权和 Obsidian CLI 回读能力是运行时前置条件，未在本批验证。

本地检查分为独立证据层：

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 /Users/archer/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/al-brooks-pa
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 /Users/archer/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/daily-trade-journal
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -v
git diff --check
```

Skill validator 只检查 frontmatter、命名和脚手架占位符；Python 测试证明输入/对齐/隐私回归；`diff --check` 证明补丁格式。它们都不证明真实 broker fact、真实 PA 数据、Obsidian `written`、自动化、部署或人类验收。

## 9. 未决风险与来源证据

未决风险包括：真实 Longbridge CLI 返回形状与已采集输入的持续兼容性、计划来源是否能稳定提供确认时间和工具方向、1H 数据的完成状态与时效、用户对“确认写入”的明确表达、以及真实 Obsidian 回读。下一步若要扩展，必须先由人确认范围和外部权限，不应从本批自动推导。

本报告的实现依据为：`skills/daily-trade-journal/SKILL.md` 的现有写入/隐私约束、`skills/daily-trade-journal/scripts/project_daily_trade_journal.py` 的固定脱敏投影与 `intraday_revisions` 输入、现有回归测试，以及本批新增的 `skills/al-brooks-pa/SKILL.md`。架构批准状态为“第一批范围已确认”；本地实现和自动检查可单独报告，真实运行与人类接受仍为 `NOT RUN`/`PENDING`。
