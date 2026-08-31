# EMA 价格区间计划与生命周期

## 输入与证据

用户给出或逐项确认 underlying、long 方向、setup、持有/等待交易日、最低风险收益比、最大计划失效幅度、tick size 与到期时间。首版只支持美股 underlying 的做多计划，不把期权价格或空头规则套用进去。

Longbridge `kline history` 是唯一来源；能力、字段、复权与已完成日线语义必须实机核验。整轮最多 20 个当前持仓/已确认候选，每标的最多 550 自然日。失败不安装、不换 provider，也不让 Codex 猜价格。

`construct_trade_plan.py` 消费私有 `trading-plan-request.v1`：计划元数据/用户约束、source、bars，以及仅持仓管理使用的父计划/已验证买入派生键。完整固定字段由脚本校验，未知字段拒绝。

- source：Longbridge、kline history、1D、America/New_York、forward/backward、requested_start/end、as_of。
- bars：timestamp、open、high、low、close、volume、is_complete；时间和纽约市场日期必须唯一递增且在窗口内。
- 最多移除一根末尾未完成日线，之后至少 319 根；as_of 必须等于最后完成日，生成时距其不超过五个自然日。
- EMA20/50/200 以对应前 N 根均值初始化后递推；这里的均值只用于 EMA 初始化，不是 SMA 信号。
- ATR14 使用 Wilder 平滑；左右各两根确认 swing，按 0.5 ATR 聚类，保留结构日期/方法。

价格按 tick 向外取整。用进入区上沿、失效区下沿、首个退出目标下沿检验最不利计划风险收益；不满足门槛只观察。该门槛不证明胜率，跳空与滑点仍可能越过计划边界。

## Setup 与阶段

- pullback：非 bear 结构中回踩企稳的条件式进入。
- breakout：突破/回踩条件明确且有下一阻力目标。
- range：range 结构中的支撑确认与阻力退出。
- bottom_reversal：最近十根进入 120 日低位附近或明显下偏离后，还需更高低点并收复前高，或重新站上 EMA20；没有右侧确认只有观察区。
- position_management：实际买入以后，原逻辑成立并出现有利结构才考虑 add。

`pre_entry` 只有 observation、entry、reduce/exit、invalidation，绝不预设 add。未满足条件的观察草案也可保存，但不能确认成可执行计划。

## 保存、确认与显示

~~~bash
python3 skills/trading-center-review/scripts/construct_trade_plan.py \
  --input /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/plan-request.json \
  --output /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/plan-draft.json

python3 skills/trading-center-review/scripts/trade_plan_lifecycle.py save-draft \
  --input /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/plan-draft.json \
  --output /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/plan-saved.json
~~~

保存前重算 content_hash，投影只保留 SQLite 白名单。技术证据不合格的 blocked 工件不写成计划。初次必须 v1 draft；新内容追加 draft，不覆盖旧版本。

展示具体草案内容、价格区间、失效和到期、version/hash。只有用户明确确认这一个草案后，才执行：

~~~bash
python3 skills/trading-center-review/scripts/trade_plan_lifecycle.py confirm \
  --plan-id <本次计划ID> --draft-version <已展示版本> \
  --content-hash <已展示的完整hash> --confirmed-at <实际确认时间RFC3339> \
  --user-confirmed \
  --output /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/plan-confirmed.json
~~~

确认追加下一版本，保留原始内容 hash、证据和区间。`--user-confirmed` 不是绕过门禁的默认选项；实施授权、复盘确认、此前其他版本的确认都不能代替本次确认。新 CLI 确认时间必须处于当前时钟前五分钟内，禁止倒填或未来时间；只有已落库的精确版本/hash/确认时间重放可跳过当前时钟检查。

持仓管理必须引用 confirmed pre_entry 父版本及 `market_date|underlying|buy` 派生键。状态层会查验最新完整日分区、执行数量与 payload hash，确认买入证据先于草案；不能用布尔值或持仓快照替代买入事实。加仓草案须再次单独确认。

~~~bash
python3 skills/trading-center-review/scripts/trade_plan_lifecycle.py enrich-daily \
  --input /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/daily-dashboard.json \
  --plan-id <计划ID> --version <要展示的版本> \
  --output /private/tmp/trading-center-review-runtime/<run-date>/<run-id>/daily-with-plan.json
~~~

只合入已有标的行，不新建页面或重排每日结构。可选 `--quote-input` 接收固定 Longbridge source/price/as_of/data_status，来源状态必须已由采集端按交易时段和 cutoff 验证。报价只改变 below/inside/above/stale/unavailable，旧区间和计划 hash 不变。到期只派生显示，不静默改数据库。

## 不完整结果

真实 Kline 不可用时可以验证代码与合成 fixture，但不能生成看似真实的价格区间。周度统计仍遵守事前计划与完整执行证据门；不存在合格版本就保留缺口，不事后补计划。
