# 增量状态与缓存契约

## 目的

owner-only SQLite 只保存可重复使用的白名单事实，不保存原始 Longbridge 响应。它用于决定当前运行哪些分区可以复用、哪些必须重读，并保留修订历史。

## 路径与权限

默认路径：


`/Users/archer/Library/Application Support/MarsTradingCenter/trading-review.sqlite3`

要求：

- 绝对路径，位于 Git、Obsidian、Wiki 和 `/private/tmp` 之外。
- 不允许路径任一已有组件为符号链接。
- 父目录必须由当前用户拥有且为 0700。
- DB、WAL、SHM 和迁移备份必须由当前用户拥有且为 0600。
- 已有文件权限过宽、所有者不符或 Schema 比当前代码新时 fail closed。
- SQLite 使用 foreign keys、WAL、`busy_timeout=5000`、单写者和事务；写锁超时不无限重试。

## Schema v5

Schema v5 保留 schema v1 的日度表：

- `schema_meta`
- `runs`
- `partitions`
- `account_snapshots`
- `position_snapshots`
- `trade_aggregates`
- `market_snapshots`
- `relevant_events`
- `analysis_snapshots`
- `confirmations`

并保留 schema v2 的固定周度表：

- `weekly_reviews`
- `weekly_review_dependencies`
- `weekly_module_statuses`
- `weekly_performance`
- `weekly_attributions`
- `weekly_cash_flow_aggregates`
- `weekly_review_items`

v3 只追加 `plan_versions`、`plan_zones`、`trade_episode_assessments`、`weekly_execution_metrics`。完整字段、状态机与回滚见项目权威 [架构报告](../../../docs/architecture/trading-center-skill-incremental-state.md)。

v4 追加 `daily_review_sources`、`daily_review_source_dependencies`、`journal_confirmation_bindings`、`valuation_observations`、`holding_management_intents`。固定列、主外键见 `trading_review_state.py` 的 EXPECTED_COLUMNS/SCHEMA_V4_SQL；新表只追加，禁止更新/删除，不补造旧日度来源或历史确认。完整 Bridge 包仍在私有不可变工件中，DB 仅存绑定与摘要。边界见 `docs/architecture/ts-web-and-obsidian-bridge.md` 第 10 节。

v5 再追加 `plan_execution_contexts`、`trade_instrument_facts`、`weekly_execution_bases`、`instrument_episode_assessments`。新计划版本固定保存实际工具、交易/观察标的、观察/触发周期和触发口径；交易事实与原 trades 分区版本、聚合自然键共同绑定。工具级周度使用 v3 输入并按实际 symbol 区分正股、单股杠杆 ETF 和 LEAP；旧 v2/underlying 级历史不回填、不重算。完整字段和回滚边界见 `docs/architecture/ts-web-and-obsidian-bridge.md` 第 11 节。

不得增加 `raw_json`、`payload_json`、`metadata_json` 或类似自由逃生列。每种持久化事实必须有显式列和字段白名单。

允许新增保存：日度白名单、周度 period/revision、日分区依赖、模块状态、固定周度条目、已确认计划投影和区间、underlying 级 episode 分类与执行指标。`weekly_performance`、`weekly_attributions`、`weekly_cash_flow_aggregates` 仅保留历史；新周度入口不再写入，UI 不读取。

永久禁止：账户标识、上游订单/成交/交易/请求 ID、凭据、Cookie、API key、成本、佣金、原始响应、完整对账单、完整日历、完整新闻和具体期权合约身份字段。

## 分区与修订

`partitions` 的身份由以下字段组成：

`dataset + period_start + period_end + contract_version + revision`

- latest 为 `complete` 或成功 `empty`：默认 cache hit。
- latest 为 `partial`、`stale`、`blocked` 或不存在：retry。
- 相同状态和相同 payload hash：记录本轮 run，但不复制分区或事实行。
- payload hash 或状态变化：新增 revision，并记录 `supersedes_revision`。
- 新事实完成白名单、时间和敏感值校验后，才与分区在同一事务提交。
- 新写入失败不删除旧 complete revision。
- 每日采集计划在 cache hit 时回读并校验事实行与 `payload_hash`，以固定 `cached_partition` 交给当轮事实包；hash 不一致时 fail closed。

## 每日策略

每次刷新：

- `account_snapshot`
- `positions_snapshot`
- `market_snapshots`
- `relevant_events`

按前一 America/New_York 已完成交易日缓存：

- `trades`

计划文件只保存内容 hash。Codex 分析缓存只在 `facts_hash + plan_hash + analysis_contract_version` 三者完全一致时复用。

## 周度策略

预期交易日必须来自 Longbridge 交易日历或当前运行已经核验的等价事实，不能只用“周一至周五”猜测交易所假日。

所有预期交易日的 `trades` 分区都为 complete 或成功 empty，交易模块才是 complete。周度聚合只读取每个交易日最新可复用 revision。

旧 underlying 级周度来源投影成 `trading-review-weekly-state.v2`；新工具级来源在 `plan.execution_basis=instrument-episode.v1` 时投影成 v3，即使该周 episode 为空也保留 v3，不能根据数组是否有行猜版本。两者都追加一个 `weekly_reviews` revision。依赖必须指向写入时最新的日度交易分区 revision/hash；旧依赖后来变化时，只在读取时返回 `stale`，不更新旧 revision。

新 v2/v3 输入的 performance 必须 null、attributions/cash_flow_aggregates 必须为空。旧 v1 仅用于历史兼容测试/回读，公共 runner 拒绝新 v1 写入。v2 历史只保留 underlying 级语义；v3 使用实际脱敏 symbol、tool_kind 和 underlying，具体期权合约身份始终拒绝。

指标由 episode 分类机械计算，输入不接受调用方自行填写的分子分母或百分比。分类自然键必须与 hash 验证的 eligible 日度交易集合一致；无证据不得以空集合冒充成功。W35 没有事前确认计划的历史缺口保持 blocked。

计划初版先保存 draft，确认指定 version/hash 时追加新版本；原版本不可变。`pre_entry` 禁止 add；持仓管理引用已确认父计划及可在完整日交易分区中验证的实际买入派生键，不能只传布尔值。所有检查和追加写入在同一写事务中完成。

周度来源每次获授权运行仍重新读取。历史 weekly revision 只用于历史展示、确认和依赖新鲜度，不得当作下一周可重建来源。每日运行可以只读最近 weekly revision 组成统一 HTML，但对所有 `weekly_*` 表必须零写入。

## 迁移与备份

- 新库在同一事务顺序建立 v1、v2、v3、v4、v5 表，不创建空备份。
- 已有 v1/v2/v3/v4 数据库迁移到 v5 前，在同一 0700 目录生成 0600 时间戳备份；v5 DDL 前先扫描旧结构化 symbol/underlying 和周度文本中的紧凑期权身份，发现即阻塞且不重写历史。旧表内容核对不变，回滚须同时恢复兼容代码和数据库，不能仅回退旧代码打开新库。
- 验证已关闭、无 WAL/SHM/journal 辅助文件的独立备份时，允许显式 `mode=ro&immutable=1`，避免 WAL 格式备份在受限目录中尝试创建 SHM。禁止对活动主库或带辅助文件的备份使用 immutable；主库指纹须读取同一事务快照。
- DDL 和 `user_version` 更新在事务内完成。
- 迁移失败回滚，关闭新代码并保留旧库与备份。
- 若错误发生在迁移提交后的备份/结果校验阶段，先只读检查实际版本与完整性，不把命令退出 1 当作回滚，更不能直接重复迁移；校验恢复结果须保留原错误。
- 首版不自动删除备份。

## 运行 manifest

日度使用 `trading-review-run-manifest.v1`；周度使用 `trading-review-weekly-state-manifest.v1`。manifest 只记录 run/review identity、区间、status、revision/action、hash、依赖 freshness、Schema/contract 版本和工件路径，不复制私有事实值。

manifest 不包含：账户金额、基础币种、仓位数量、标的级订单/成交数量、标的名、事件正文或上游 ID。

## CLI

`scripts/run_incremental_review.py` 提供：

- `daily-plan`
- `daily-analysis-plan`
- `ingest-daily`
- `weekly-plan`
- `weekly-aggregate`
- `ingest-weekly`
- `weekly-dashboard-packet`

`scripts/project_weekly_review.py` 在入库前验证私有周度事实与最新日度交易分区 hash 一致，并执行 underlying/执行分类白名单投影。计划构造、保存、确认与每日卡片合成见 [计划工作流](trade-plan-workflow.md)。

runner 不直接调用 Longbridge。先生成 collection plan，再由获授权的采集端读取最小数据并投影为固定脱敏包。交易 cache hit 在任何券商读取前检查；分析 cache hit 在固定事实包形成后、调用 Codex 前检查。缓存分析必须连同原始 model、status 和 generated_at 原样复用。同一 review_date 的相同来源身份可用原 generated_at 幂等恢复；不同来源身份必须使 generated_at 严格前进，早于或同时于已绑定新来源的孤儿 run 拒绝回放。
