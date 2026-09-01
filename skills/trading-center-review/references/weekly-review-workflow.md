# 周度复盘工作流

## 范围与目标

覆盖完整 America/New_York 交易周，预期交易日来自已核验 Longbridge 日历。周度只更新周度增量；日度继续使用同一页面、同一数据库，不另开板块。

重点是有没有按事前计划执行。具体盈亏、收益率、TWR、完整归因和现金流不作为新复盘的采集或展示要求。盈利但违规仍复盘；按计划止损是执行合规、计划结果失败，二者不能混为一谈。

## 采集、分类与持久化

1. 读取周初已确认计划及全部确认增量，保留 plan_id、version、hash、确认和生效时间。
2. 获取完整预期交易日，运行 `weekly-plan`；complete/成功 empty 交易分区复用，仅补缺失或非成功日期。
3. 运行 `weekly-aggregate`。取消未成交、FX、IPO、公司行动排除；旧 v2 历史仍按 `市场日期 × underlying × buy/sell`，新工具级事实按 `市场日期 × actual trade symbol × buy/sell`，不再把同公司正股与期权折成一笔。
4. 确需判定价格/时点时，在授权的同轮 owner-only 目录中机械评估执行事实；原始执行价格、合约身份和券商 ID 不进入数据库、Codex 输入或 UI。规则无法机械核验时为 unassessable，不凭事后收益打分。
5. 分类为 coverage、compliance、outcome、deviation、reason、next_rule；合格交易不能选择性遗漏。没有计划权威或原始事实时保持 blocked，不补齐。
6. 私有输入继续使用 `trading-review-weekly-private-facts.v2`，不含账户/P&L 模块。旧 underlying 级输入不带 execution_basis，输出历史兼容的 `trading-review-weekly-state.v2`；新工具级输入必须显式带 `plan.execution_basis=instrument-episode.v1`，每行包含经核验的实际工具、symbol 和周期口径并输出 v3。成功空周仍输出 v3，不能按 assessment 是否有行猜版本。两种语义不能混在同一复盘，旧记录不补造工具。
7. `ingest-weekly` 追加 SQLite v5 的周度 revision，验证全部分类能与当前 hash 校验的日分区对应。旧 P&L 表不写入。
8. 从 SQLite 回读 `weekly-dashboard-packet`；与合格每日包交给同一个 V2 renderer，生成单页 HTML。缺每日包则 blocked，不生成周度专用页。
9. 已明确启用常驻服务时，按 [正式发布指引](local-web-service.md) 发布这份经过范围和内容核验的日/周页面；可复用持久日度展示快照，但它的源日期必须覆盖所回看的完整周度窗口。旧 DB 未保存的 UI 文案/范围声明仍须明确核验，不补造。

历史 revision 的源时间不刷新。日分区被修订时，旧周度只派生 stale；每日不追加周度 revision，不重算周度指标。

## 指标口径

- 计划覆盖率：事前 confirmed 计划覆盖数 / eligible episode 数。v3 还必须匹配实际工具、交易 symbol、观察/触发周期和触发方式；证据不足保持不可评估。
- 按计划执行率：compliant / 可核验的 covered episode 数，必须与覆盖率同屏。
- 计划胜率：success / (success + failure)；open、flat、unverifiable 排除并展示数量。
- 需复盘数：不合规、失败、无覆盖/不可判断或有明确偏差的 episode。

分母为零显示“不可计算”，不能写 0% 或 100%。complete 与“所有交易合规”无关：它只表示输入覆盖完整。

当前数据库只保留市场日期，不保留原始执行时点。只有计划覆盖该完整纽约日界时才可持久化为 covered；同日确认/到期若无法证明前后关系，必须标记未覆盖/不可核验，不能声称事前覆盖。不得给已结束的 W35 事后补计划或推算胜率。

## 单页呈现与确认

周度背景分别进入原市场雷达和 Codex 判断；四个指标、异常 episode、计划复核与下周草案进入原持仓 × 计划；下周事件进入同一时间轴；数据说明分别标明日/周来源时间。不再生成周度操作摘要或订单计数；日度“上一交易日成交”只展示明确已成交的美股记录。完整订单证据仍留在私有审计链路，不能因 UI 精简而删除。

默认只展开四个指标和关键缺口，具体需要复盘的交易再展开查看。工具级 episode 明确显示脱敏后的实际交易对象、工具和观察周期；单股杠杆 ETF 不只显示其公司 underlying，LEAP 不显示具体合约身份。下周事件保留双时区和证据边界，过期预期不能自动冒充已发生。

复盘确认与交易计划确认是两个门。用户确认一份复盘，不会自动确认其中的下一周计划或买入后的加仓草案。人工浏览器验收未明确 PASS 时保持 PENDING。

要同步周度日记，先按知识中心交接边界生成脱敏 pending 预览，再对该确切正文取得“复盘完成”。严格 producer 在同一事务中绑定来源版本、事实/计划摘要和完整包；旧弱 confirm() 记录不足以授权 Bridge。没有 plan_hash、依赖或执行证据时保留缺口；不把生成日记当作已确认下周交易计划。
