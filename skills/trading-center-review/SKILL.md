---
name: trading-center-review
description: 基于 Longbridge 授权只读事实、已确认周计划和 owner-only 增量状态，生成每日盘前或周度交易复盘、V2 私人看板与确认后脱敏交接包；不下单、不写券商、不读取凭据。
---

# 交易中心复盘

把交易事实、计划、Codex 判断、数据缺口和用户确认状态放在一条可追溯链路中。默认保持 Skill-first：不创建 Plugin、MCP、Codex App 或 marketplace。后台服务不自动安装；仅在明确批准时启用只读本地展示服务，不增加采集或交易权限。

## 选择运行模式

先读取当前项目中实际存在的 `AGENTS.md`、`CONTEXT.md`、当前 ISO 周计划和已确认每日增量。文件普通缺失不生成占位；只有缺失使授权、计划权威或事实边界不清时才阻塞。

每次都读取：

- [授权、隐私与外部状态边界](references/authorization-and-data-boundary.md)
- [增量状态与缓存契约](references/incremental-state-contract.md)

然后只加载当前模式需要的文件：

- 每日盘前：读取 [每日盘前工作流](references/daily-review-workflow.md)、[Longbridge 数据契约](references/longbridge-dashboard-data-contract.md)、[V2 看板契约](references/dashboard-visualization-contract.md)。
- 周度复盘：读取 [周度复盘工作流](references/weekly-review-workflow.md)、[Longbridge 导入契约](references/longbridge-import-contract.md) 与 [V2 看板契约](references/dashboard-visualization-contract.md)。
- 市场、计划或交易解释：读取 [Codex 分析边界](references/analysis-skill-routing.md)。
- 构造或确认价格区间计划：读取 [EMA 计划与生命周期](references/trade-plan-workflow.md)。
- 每笔新计划明确区分正股、单股杠杆 ETF、LEAP Call 与其实际交易/观察标的；观察周期是 K 线判断周期，不是持有或兑现周期。未确认工具/周期不回填，其他周期数据不冒充当前仅支持的已完成日线区间。
- 持仓/买入候选市赚率：使用 `collect_scoped_valuations.py` 与 `trading_review_valuation.py` 固定投影；PR = PE(TTM) / 年度 ROE 百分点，展示财年/期末/读取时间，不自动贴低估标签、不扩展同业、不用于 ETF。估值附着标的，不另建页面；读取授权边界的年度报告注意事项。
- 用户已批准本地常驻展示、发布或维护：读取 [本地展示服务](references/local-web-service.md)；只操作固定目录和该服务的 LaunchAgent。
- 准备脱敏日记草稿、确认后入队或运行知识中心 receiver：读取 [知识中心交接边界](references/knowledge-handoff-contract.md)；实施授权不能替代每份复盘的最终确认。
- 仅当用户显式要求过渡期 Feishu Wiki 写入时，才读取 [Wiki 记录结构](references/feishu-wiki-record-structure.md)。

调度语义为 Asia/Shanghai 周二至周五每日、周六周度、周一和周日不运行。手动运行必须明确选择 daily 或 weekly。

## 共享事实与授权门禁

- Longbridge 是唯一券商边界，只做当前任务已经明确授权的只读能力和时间窗口。
- Skill 本身不授予新权限。若外部自动化已经给当前运行声明了更窄的默认只读授权，可在那一范围内执行；否则扩大能力、字段或窗口前必须停下确认。
- 原始响应、机械日历、账户明细和日志只放在 `/private/tmp/trading-center-review-runtime/<run-date>/<run-id>/`。
- 不读取或输出凭据、Cookie、API key、账户标识、订单/成交/交易 ID、成本、佣金或完整对账单。
- 当前 assets 和 positions 只是读取时快照；快照变化不能证明历史成交、收盘状态或完整账户对账。
- 成功空数组与失败分开：只有解析成功的空数组才是 `empty`；失败、限流、字段缺失或权限不足保留 `partial` / `blocked`。
- 不用计划、旧快照、用户口述、测试 fixture 或 Codex 补齐缺失券商事实。
- Codex 只读取固定脱敏字段包，区分事实、解释、条件式检查和缺口，不生成无条件交易指令。
- 统一 V2 只展示美股持仓和未持仓买入候选：各自的计划归于同一标的，不能把持仓技术参考复制到买入页。原五类策略仅使用用户确认的名称，不能以 setup 补齐。事件按纽约日期分本周/下周及每日；内部诊断不进入 HTML。具体规则见看板展示契约。
- 2026-08-31 已批准的来源例外仅为美联储及地区联储官网的公开讲话排期；不扩展行情、财报或账户来源，不表示授权自动化新增采集任务。

## 增量执行

默认状态库：

`/Users/archer/Library/Application Support/MarsTradingCenter/trading-review.sqlite3`

数据库只在显式运行增量 runner 时创建。它必须位于 Git、Obsidian、Wiki 和临时目录之外；父目录 0700，DB/WAL/SHM/迁移备份 0600。拒绝符号链接、非当前用户所有者和权限过宽文件。

每日先生成采集计划：

~~~bash
/usr/bin/python3 -B skills/trading-center-review/scripts/run_incremental_review.py daily-plan \
  --review-date <America/New_York 已完成交易日> \
  --plan-file <当前周计划> \
  --source-contract-version <版本> \
  --output /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/daily-plan.json
~~~

计划固定刷新当前账户快照、持仓、市场快照和相关事件；前一交易日交易分区只有在缺失或最新状态为 partial、stale、blocked 时读取。complete 或成功 empty 分区复用，并在私有计划的 `cached_partition` 中返回经 payload hash 复核的固定聚合，供事实包直接使用。

采集端必须先把 Longbridge 结果投影为不含模型输出的 `trading-review-incremental-facts.v1` 固定脱敏包，并在调用 Codex 前执行：

~~~bash
/usr/bin/python3 -B skills/trading-center-review/scripts/run_incremental_review.py daily-analysis-plan \
  --input /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/daily-facts.json \
  --output /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/analysis-plan.json
~~~

只有 `action=run_codex` 才生成新分析；`action=reuse` 必须原样使用 `cached_analysis` 的 model、status、generated_at 和 output。把选定分析与同一事实包合并为 `trading-review-incremental-input.v1` 后再执行：

~~~bash
/usr/bin/python3 -B skills/trading-center-review/scripts/run_incremental_review.py ingest-daily \
  --input /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/daily-input.json \
  --manifest /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/run-manifest.json
~~~

不得把原始 Longbridge JSON、自由 metadata 或未知字段交给 runner。写入按分区事务完成；相同 payload hash 不复制事实行，变化生成新 revision，失败不删除旧 complete revision。

Codex 分析缓存键固定为：

`facts_hash + plan_hash + analysis_contract_version`

复用旧分析时必须显示原生成时间；相同缓存键却带新时间、模型、状态或输出会 fail closed，不能冒充本次新分析。

## 每日交付

每日用同一事实边界生成：

1. 私有 Markdown 审计稿。
2. `trading-review-dashboard.v2` 私有 JSON。
3. owner-only standalone HTML。
4. 不含账户数值、仓位数量或标的级事实的 `trading-review-run-manifest.v1`。

运行草稿校验和 V2 renderer。每日对话只交付通过 Schema、隐私和离线检查的 HTML 链接及简短状态；Markdown、完整事件工件和账户事实留在私有目录。

本机已明确启用常驻展示时，通过 `node skills/trading-center-review/web/cli.ts publish` 发布同一校验结果，交付 `http://127.0.0.1:8765/`；日度未指定周度输入则复用持久周度内容。已持久化估值需要重建展示时显式加 `--enrich-db`，只读取限定标的白名单，不依赖临时页面，也不刷新行情时间。不能把服务常驻当成自动生成新数据。未安装时仍使用私有 HTML 工件，不自行安装。

V2 页面固定为市场风险雷达约 42% / Codex 判断约 58%，随后是上一交易日成交、持仓 × 计划、全宽事件和折叠数据说明。成交区只展示有明确成交证据的美股记录，不显示未成交委托、订单计数或周度操作摘要。账户字段继续进入私有 Schema 校验和 Codex 输入，但不渲染账户概览、金额、基础币种、快照时间或金额显隐控件。

## 周度交付

周度先由 Longbridge 交易日历提供本周预期交易日，再运行 `weekly-plan`。所有预期日期的交易分区为 complete 或成功 empty，交易模块才是完整；任一缺失或非成功状态都保留 partial/blocked 并只补缺口。

周度正文覆盖完整 America/New_York 交易周、订单/成交、事前已确认计划与全部确认增量、是否按计划执行、下周保留/删除/重写/新增草案、相关事件和风险。复盘重点是执行纪律，不再重复券商具体盈亏。

SQLite v3 追加计划版本、区间、trade episode 分类和执行指标；v5 追加工具/周期上下文及工具级 episode。旧 underlying 级运行保留 `trading-review-weekly-state.v2`，新工具级运行使用 v3；不写收益、归因或现金流历史表。覆盖率、执行率、计划胜率和需复盘数由已验证分类计算；无事前计划权威、实际工具或成交证据时保持不可评估/blocked，不能从历史盈亏推算。

周度生成 `trading-review-weekly-dashboard.v2`，与每日包交给同一个 V2 renderer/template，保留每日骨架和一个 `<main>`，没有日/周模式切换或独立周度 panel。周度附加内容只由周度或手动周度运行更新；每日只读最近周度 revision，对周度表零写入。无每日包时不生成周度专用页面。周度正文仍是私有审计工件。

计划构造统一使用 Longbridge 已完成日线、EMA20/50/200、ATR14 和可追溯结构位；支持 `pullback`、`breakout`、`range`、右侧确认的 `bottom_reversal`。初始 `pre_entry` 不允许加仓区间；实际买入经数据库事实验证后才生成 `position_management` 草案，并再次逐版本确认。

## 确认、Wiki 与知识中心

`data_status` 和 `confirmation_status` 是两条独立轴。confirmed + partial 只表示用户确认了一个明确保留缺口的版本，不表示数据完整。

用户在当前 run 明确说“复盘完成”前：

- 不写 Wiki。
- 不修改当前周计划。
- 不生成依赖外部写入成功的盘中关注点。
- 不写 Obsidian。

确认后，交易中心只生成脱敏交接候选；知识中心任务是 Obsidian 唯一写入者。Feishu 仍处于单独确认的过渡边界，不因安装或运行本 Skill 自动初始化、写入或退役。

## 生产与回滚

- V1 renderer、模板和测试保留为生产回滚线。
- V2 和增量 runner 在真实每日 shadow、真实周度 shadow、自动安全检查和人工浏览器 PASS 前保持影子状态。
- 自动化切换、push、PR、merge、发布、Plugin 化和真实数据清理都需要单独授权。
- 自动测试、Agent 视觉检查和人工验收分别报告；没有用户明确 PASS 时，人工验收保持 PENDING。

## 本地验证

~~~bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 \
  /Users/archer/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/trading-center-review
~~~

TS 展示与服务另执行 `npm ci --ignore-scripts`、`npm run typecheck`、`npm run test:web`；生产运行不依赖 node_modules，要求可执行的原生 Node 24.12+。迁移电脑后必须核验实际架构，不能沿用失效的 Intel 命令路径。

校验通过只证明代码和固定契约满足测试，不证明 Longbridge 覆盖、账户对账、真实 shadow、Wiki/Obsidian 写入或人工浏览器验收成功。
