# 知识中心交接边界

## 所有权

交易中心拥有事实、计划、确认和 producer；知识中心拥有 Vault 外安装的 receiver 与日记写入。Web HTTP 服务、页面刷新及只读 producer 不写 Vault。receiver 不读券商、不写交易数据库、不设 watcher、不轮询、不反向改机器计划。

## 确认门

可以先生成明确 pending 的脱敏草稿及预览。只有当前 run 同时满足以下条件时，才可正式 confirm / enqueue：

- 用户读过确切脱敏草稿，明确说“复盘完成”或作出等价的当前版本确认；持仓原则确认、服务长期授权或旧聊天不能替代。
- 交接引用的 facts hash、plan hash 和 confirmation version 可确定。
- 数据缺口和 `data_status` 保持原样。
- 包中没有账户数值、仓位数量、订单/成交聚合明细、上游 ID、成本、佣金、凭据、原始响应或完整事件馈送。

confirmed + partial 仍是 partial；确认不升级事实完整性。

## 已批准契约

准确 Schema、路径、模板、单向同步和版本/编辑保护已获用户确认。权威为项目 `docs/architecture/ts-web-and-obsidian-bridge.md` 第 3、10 节；不要重复索要已批准的模板文本，也不要把实施授权视为一份复盘完成。

- Schema 为 `confirmed-investment-review.v1`，固定字段由 `review_journal_contract.py` 校验；weekly_metrics 仅周度填写，日度不重算。数值只走已批准的周度比例/分母字段。
- 日记不包含精确价格、数量、账户金额、成本、费用、盈亏、券商 ID、凭据、私有路径或完整机器计划。自由文本隐私/标记检查是保守门，不是理解所有敏感表达的保证；人类仍须核对。拒绝时重新拟定并确认草稿，不静默删改成“已确认”。
- 日度来源由 v4 runner 追加，周度引用既有 weekly_reviews.revision。无 plan_hash、无精确依赖、旧弱确认链均拒绝；不补造历史确认。facts_as_of 是依赖中最早的 collected_at，称“事实资料采集起点”，不是最新行情时间。
- 全部来源/链/摘要检查和确认追加在同一 BEGIN IMMEDIATE 内。完整草稿/确认包在固定私有不可变工件中，DB 绑定整个确切包摘要；producer 不从网页、聊天或新模型正文重建。

## 最小候选内容

候选包含固定来源、复盘区间、确认版本、facts/plan hash、状态、脱敏 sections 和允许的周度指标。没有数据的比例不算零，partial/empty/stale 不因确认变 complete。

候选不包含任何数据库路径、账户事实、私有 HTML 路径或外部系统 token。

知识中心任务写入后必须由它自己回读，并单独报告目标、版本和失败；交易中心不代报成功。

## 显式运行与恢复

1. `review_bridge_producer.py prepare --review-key <key> --text <私有JSON>`：输入仅 sections、gap_categories，来源、时间和指标从 DB 绑定，生成 pending 预览。
2. 展示确切预览，待“复盘完成”后运行 `confirm --approved-draft-hash <hash> --confirmation-text 复盘完成`。此时只是 confirmed_not_enqueued。
3. `enqueue --payload-hash <hash>`：只读一致快照核验时点的新鲜度，写固定 outbox。不宣称文件入队与并发 DB writer 线性化，历史确认不是实时行情或交易授权。
4. `install_review_bridge.py status` 验证知识中心代码；用返回的绝对 receiver 路径运行 `sync --payload <确切outbox文件>`，独立全文回读后才报告 synced。

私有目录：`~/Library/Application Support/MarsTradingCenter/bridge/` 下 drafts、confirmed、outbox；知识中心安装、intent、receipt 在 `~/Library/Application Support/MarsKnowledgeCenter/trading-review-bridge/state/`。0700/0600，代码不进 Vault。`install_review_bridge.py install/status/rollback` 管理固定安装，历史不自动删除。

目标：`25 投资交易/10 每日复盘/YYYY-MM-DD 交易复盘.md`、`25 投资交易/20 周度复盘/YYYY-MM-DD 至 YYYY-MM-DD 周度复盘.md`。只替换唯一托管块；frontmatter、标题、我的补充原字节保留。frontmatter 是创建时元信息，当前确认版本以受管正文、同步状态以外部 receipt 为准。

目标在任一 Markdown leaf 打开即 deferred。更新必须在 Obsidian 原子 Vault.process 的同步回调内再次核对完整旧内容、文件身份、打开状态和标记，不用外部 read/replace；其他脚本不要并发写同一文件。首次创建为同目录 0600 排他 no-clobber 发布，已有无 receipt 日记不覆盖。

写前持久化不可变 intent，文件名绑定版本及完整内容摘要，恢复时同时核对文件名摘要和 canonical bytes；文件内自报的新 hash 不能成为依据。再次独立核对已确认包的受管正文、旧 receipt 的受管块及文件身份。写后独立 `obsidian vault=Mars知识库vault read path=<精确路径>` 匹配，退出零本身不算成功。写后中断/回读失败为 written_pending_readback：已写匹配只补回读，仍是未手改旧内容才重试，其余 conflict；破损或被改写的 intent 不当“未写入”。同版本同 hash 幂等，同版本不同 hash、旧版重放、缺版、手改托管正文或符号/硬链接均拒绝。

残留锁不按超时抢占；`recover-lock` 必须指定已核实退出的 PID，活 PID 不移除。隔离样本和只读能力核验不替代真实日记人工验收。Feishu 成功不等于 Obsidian 成功。
