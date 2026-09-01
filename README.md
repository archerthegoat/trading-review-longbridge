# trading-review-longbridge

中文优先的交易中心复盘 Skill。它把 Longbridge 授权只读事实、已确认计划、Codex 条件式判断、数据缺口和用户确认状态分开，并用 owner-only SQLite 复用已完成交易日的白名单事实。

本仓库保持 Skill-first，不包含 Plugin、MCP server、Codex App、marketplace、账户数据或运行时状态。提供用户明确批准后才安装的本地只读展示服务；Skill 安装本身不启动后台进程。

## 本地常驻 UI

已批准启用时，用正式发布入口将同一日/周 V2 保存到 Git/Vault 外的 owner-only 展示目录，地址为 `http://127.0.0.1:8765/`。用户级 LaunchAgent 在登录后启动；关闭 Codex 不会关掉页面服务。SQLite 迁移只追加白名单表，周度不随每日重新计算，页面生成、发布和常驻 HTTP 现由 TypeScript 实现，Python/SQLite 保留为数据边界。持久估值通过 `publish --enrich-db` 直读 SQLite 后重建，不依赖临时页面。Obsidian 是确认后由知识中心接收的独立单向桥接，不是网页写接口；其准确 Schema/模板门禁见新架构报告。

完整决策与验收见 [TS 与 Obsidian 架构报告](docs/architecture/ts-web-and-obsidian-bridge.md)。发布、重建、安装、状态、启停和回滚命令见 [本地展示服务指引](skills/trading-center-review/references/local-web-service.md)。常驻只保证服务入口，不等于数据自动更新或人工验收通过。

## 安装

查看可安装 Skill：

~~~bash
npx skills add archerthegoat/trading-review-longbridge --list
~~~

安装稳定 Skill `trading-center-review`：

~~~bash
npx skills add archerthegoat/trading-review-longbridge \
  --skill trading-center-review
~~~

需要用户级安装时追加 `--global`。安装不会创建自动化、数据库、飞书资源或 Obsidian 内容。

## 目录

- `skills/trading-center-review/SKILL.md`：简短模式路由与共享门禁。
- `references/`：每日、周度、Longbridge、SQLite、V2、分析和知识交接契约。
- `scripts/trading_review_state.py`：owner-only SQLite schema、权限、迁移、分区、revision、缓存和确认状态。
- `scripts/run_incremental_review.py`：先规划采集、再摄入固定脱敏包的每日/周度增量 runner。
- `scripts/render_trade_review_dashboard.py`：V1 回滚 renderer。
- `scripts/render_trade_review_dashboard_v2.py`：V2 严格 Schema、隐私和离线 renderer。
- `scripts/construct_trade_plan.py` 与 `trade_plan_lifecycle.py`：Longbridge EMA 条件式区间、不可变草案/确认版本与原每日计划卡片衔接。
- `scripts/trading_review_instruments.py`：实际工具、交易/观察标的、判断/触发周期与跨工具匹配的唯一规则边界。
- `assets/`：不含真实数据的模板。
- `tests/`：V1/V2、状态内核、增量 runner 和失败门禁测试。
- `docs/architecture/trading-center-skill-incremental-state.md`：已批准架构、迁移、回滚和人工验收合同。

## 固定边界

- 周二至周五每日盘前，周六周度，周一和周日不运行。
- Longbridge 是唯一券商边界；不下单、不改撤单、不读取凭据、不接入其他券商。
- 原始响应只留在 `/private/tmp/trading-center-review-runtime/<run-date>/<run-id>/`。
- SQLite 只接收显式白名单，不保存账户标识、上游交易 ID、成本、佣金、凭据、原始响应、完整日历或通用 metadata JSON。
- complete/成功 empty 交易分区复用；partial、stale、blocked 或缺失分区重读。
- current assets、positions、市场快照和相关事件每次刷新。
- `data_status` 与 `confirmation_status` 分开；confirmed + partial 仍是 partial。
- Codex 分析只在 `facts_hash + plan_hash + analysis_contract_version` 改变时重跑。
- V2 HTML 不显示账户概览、金额、基础币种、账户快照时间或金额显隐控件。
- 每日/周度共用原每日骨架；周度只更新执行纪律增量，不另设模式或 P&L 看板。初始计划不写加仓，买入后再单独确认持仓管理草案。
- 用户当前 run 未说“复盘完成”前，不写 Wiki、周计划或 Obsidian。
- 知识中心任务是 Obsidian 唯一写入者；Feishu 只在显式过渡期请求时加载其 reference。

## 增量使用

生成每日采集计划：

~~~bash
python3 skills/trading-center-review/scripts/run_incremental_review.py daily-plan \
  --review-date 2026-08-28 \
  --plan-file /absolute/path/to/current-week-plan.md \
  --source-contract-version source.v1 \
  --output /private/tmp/trading-center-review-runtime/2026-08-29/run-id/daily-plan.json
~~~

若 trades 命中 complete/成功 empty 缓存，计划中的 `cached_partition` 会带回经 hash 校验的固定聚合，采集端直接复制到事实包；不得为了重建缓存再次读取券商。

采集端只执行计划要求且已授权的 Longbridge 只读命令，并先生成不含模型输出的 `trading-review-incremental-facts.v1` 固定脱敏包。先查询三段式分析缓存：

~~~bash
python3 skills/trading-center-review/scripts/run_incremental_review.py daily-analysis-plan \
  --input /private/tmp/trading-center-review-runtime/2026-08-29/run-id/daily-facts.json \
  --output /private/tmp/trading-center-review-runtime/2026-08-29/run-id/analysis-plan.json
~~~

`action=reuse` 时必须原样使用 `cached_analysis` 及其原生成时间；`action=run_codex` 时才运行 Codex。将该分析与同一事实包合并为 `trading-review-incremental-input.v1` 后再写入：

~~~bash
python3 skills/trading-center-review/scripts/run_incremental_review.py ingest-daily \
  --input /private/tmp/trading-center-review-runtime/2026-08-29/run-id/daily-input.json \
  --manifest /private/tmp/trading-center-review-runtime/2026-08-29/run-id/run-manifest.json
~~~

周度先提供已核验交易日清单：

~~~bash
python3 skills/trading-center-review/scripts/run_incremental_review.py weekly-plan \
  --expected-trade-dates 2026-08-24,2026-08-25,2026-08-26,2026-08-27,2026-08-28 \
  --source-contract-version source.v1 \
  --output /private/tmp/trading-center-review-runtime/2026-08-29/run-id/weekly-plan.json
~~~

周度执行证据仍须在获授权运行中核验；SQLite 保存计划、episode 分类和机械执行指标（v3 起；当前追加至 v5），不把旧周报当成下一周数据源。工具级新周度状态为 v3，旧 underlying 级 v2 只做兼容；两者都不包含账户/P&L 模块，旧表只保留历史。固定链路为：

~~~bash
python3 skills/trading-center-review/scripts/project_weekly_review.py \
  --input /private/tmp/trading-center-review-runtime/2026-08-29/run-id/weekly-private-facts.json \
  --output /private/tmp/trading-center-review-runtime/2026-08-29/run-id/weekly-state.json

python3 skills/trading-center-review/scripts/run_incremental_review.py ingest-weekly \
  --input /private/tmp/trading-center-review-runtime/2026-08-29/run-id/weekly-state.json \
  --manifest /private/tmp/trading-center-review-runtime/2026-08-29/run-id/weekly-state-manifest.json

python3 skills/trading-center-review/scripts/run_incremental_review.py weekly-dashboard-packet \
  --review-key weekly:2026-08-24:2026-08-28 \
  --output /private/tmp/trading-center-review-runtime/2026-08-29/run-id/weekly-dashboard.json

python3 skills/trading-center-review/scripts/render_trade_review_dashboard_v2.py \
  --daily-input /private/tmp/trading-center-review-runtime/2026-08-29/run-id/daily-dashboard.json \
  --weekly-input /private/tmp/trading-center-review-runtime/2026-08-29/run-id/weekly-dashboard.json \
  --output /private/tmp/trading-center-review-runtime/2026-08-29/run-id/trade-review-dashboard.html
~~~

每日与周度使用同一个 V2 renderer/template、一个 `<main>` 和原每日模块顺序，没有模式切换。每日只读周度 revision，不写周度表；周度内容只在周度或手动周度运行追加。无每日包时不能生成周度专用页。

计划使用 EMA20/50/200、ATR14 和可追溯结构位，包含右侧抄底 setup；构造、逐版本确认和买入后加仓的具体门禁见 [计划工作流](skills/trading-center-review/references/trade-plan-workflow.md)。

## 本地验证

~~~bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 \
  /Users/archer/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/trading-center-review
~~~

本地测试验证本身不代表真实 Longbridge 覆盖、账户对账、人工浏览器验收或生产切换成功；2026-08-30 的 W35 真实周度 shadow、迁移回读和 Browser 自动检查是独立运行证据。

## 生产状态

W35 首个历史周度 revision 的状态保持 `partial / current / pending`；缺少事前计划，不能回填执行率或胜率。v3 单页合并、数据库迁移回读与 Browser 证据以 [权威架构报告](docs/architecture/trading-center-skill-incremental-state.md) 为准。旧自动化和 V1 回滚线不变；新实现的用户浏览器 PASS 与自动化切换仍为 PENDING。

2026-09-01 补充：同一套桌面 TS UI 的限定标的市赚率、雷达数值、管理原则及工具/观察周期兼容已部署到 `8765`；v5 追加迁移已备份并验证旧表逻辑内容不变。市赚率由发布入口直读 SQLite，不再依赖临时页面。确认版 Obsidian 单向 Bridge 接收器已安装在 Vault 外；尚未入队真实确认包或同步真实日记。197 项 Python、22 项 TS、类型检查和 Skill 校验通过，内置 Browser 自动读回通过；人工 UI/Bridge 验收仍为 PENDING。当前边界、回滚和清单见 [TS 与 Obsidian 报告第 10—11 节](docs/architecture/ts-web-and-obsidian-bridge.md#10-本次批准的实施边界与补充契约)。

本分支不自动 push、PR、merge、修改自动化或清理私有数据。上述本地发布、数据库迁移与接收器安装均来自本次明确授权；其他外部状态变化仍需单独授权。
