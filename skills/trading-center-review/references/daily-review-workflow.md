# 每日盘前工作流

## 时间与事实范围

`review_date` 是 America/New_York 的已完成美股交易日。订单和成交使用该纽约日历日的半开窗口 `[ny_start, ny_end)`，同时记录表示同一时刻的 UTC 半开窗口。

默认每日事实范围由当前运行授权决定，最多包括：

- 当前账户和 positions 读取时快照。
- 前一交易日订单/成交聚合。
- Longbridge 可用的 quote、capital、market-temp、finance-calendar、macrodata 和明确授权的 profit-analysis 字段。
- 已单独批准的六个市场代理最近两根已完成 1D 收盘；只用于收盘市场环境，不与 quote 混用。
- 当前周计划、已确认每日增量和明确候选池。

当前快照不能替代历史、收盘或完整账户事实。快照净变化必须与订单/成交勾稽分开。

## 采集顺序

1. 读取计划权威与已确认增量，计算 plan hash。
2. 运行 `daily-plan`，先确认 trades 是 cache hit 还是需要读取；命中时直接使用计划内经 hash 校验的 `cached_partition`，不再次读取券商。
3. 只运行计划要求的 Longbridge 只读命令；原始结果写入当前 run 的 0700 私有目录。
4. 将结果投影为不含模型输出的 `trading-review-incremental-facts.v1`。未知字段和禁止值不得进入包。
5. 运行 `daily-analysis-plan`。`run_codex` 才生成新 Codex 固定 JSON；`reuse` 原样使用缓存中的 model、status、generated_at 和 output。
6. 把选定分析与同一事实包合并为 `trading-review-incremental-input.v1`，运行 `ingest-daily` 并回读 `run-manifest.json`。
7. 用同一事实边界生成私有 Markdown、V2 JSON 和 standalone HTML。
8. 日度市场环境启用时，运行 `refresh_market_close_environment.py`：它只读取固定六个公开代理，以 review_date 与前一完成日线替换市场雷达，并在同一私有展示快照中生成可追溯的收盘定价判断。命令失败不覆盖上次成功页；权益基准不齐则明确不形成判断。
9. 当前机器已明确安装常驻服务时，读取 [本地展示发布指引](local-web-service.md)，用 `node skills/trading-center-review/web/cli.ts publish --daily-input ...` 或经校验的 `--display-input ...` 发布；未提供新周度输入时复用持久周度。未启用服务则保持私有 HTML，不自行安装或改调度。

生成页面前可只读最近周度 revision，用同一 renderer 合入原模块；不刷新周度时间、不写周度表。计划区间由已保存版本提供，`trade_plan_lifecycle.py enrich-daily` 只把区间卡片合入既有标的行。quote 只更新关系，不能移动区间；构造新 draft 或确认版本不是每日自动动作。

## 私有 Markdown 顺序

1. 数据与授权状态。
2. 复盘阶段。
3. 前一美股交易日订单与成交。
4. 昨日参考持仓与当前持仓。
5. 快照净变化。
6. 当周最新计划与计划 vs 实际。
7. 当天交易日重要事件。
8. 下一美股交易日重要事件。
9. 事件对当前持仓/计划的主要影响。
10. 过程复盘。
11. 明日缺口与行动。
12. Wiki 写入分类与确认门。
13. 最终状态。

运行 `scripts/validate_review_draft.py`。缺失必需章节、事件表、事件影响摘要、正确公告状态或成功空语义时 blocked。

## 事件契约

两张事件表使用同一五列：

`Asia/Shanghai 时间 | 美东时间 | 事件 | 状态 | 来源与数据状态`

状态只能是：已发生、预期、未公布、未验证。

- 公告字段有非空有效值：已发生。
- 公告为空且事件时点未到：预期。
- 时点已过但仍无公告：未公布。
- 日期、时间、关键字段或查询不完整：未验证。

成功相关筛选为空时使用精确审计行：事件名“无已确认事件（相关筛选后）”、状态“已发生”、来源“相关筛选已完成并返回空”。这里的“已发生”只表示筛选动作完成。

财报只保留唯一映射且命中持仓 underlying、有效计划或候选池的美股事件。宏观、政策、监管和行业事件必须有明确风险通道。没有官方排期时不生成 Fed 占位。

影响摘要只映射相关事件视图，使用：

`事件 | 影响对象/风险通道 | 主要影响 | 证据与边界`

## 用户可见交付

日报只提供通过 Schema、隐私和离线检查的私有 V2 HTML 链接与简短状态。不粘贴完整 Markdown、账户事实、完整日历或机械输出。

过程复盘回答“是否按事前计划执行”，不展开具体盈亏。计划胜率和执行率只在周度/手动周度更新；每日引用最近周度时必须保留原 period 和 freshness。

用户未明确记录浏览器 PASS 时，V2 人工验收为 PENDING，V1 保持回滚线。

日记可以先按知识中心交接边界准备脱敏 pending 预览；必须在用户确认该确切草稿和本次复盘完成后，才由严格 producer 追加来源/正文绑定并入队。不使用旧的弱 confirm() 记录冒充 Bridge 确认，不因每日刷新而自动同步。
