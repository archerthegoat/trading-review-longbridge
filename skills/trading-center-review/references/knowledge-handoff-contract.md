# 知识中心交接边界

## 所有权

交易中心维护交易事实、确认状态和脱敏交接候选；个人知识中心任务是 Obsidian Vault 的唯一写入者。此 Skill 不创建 Vault 目录、不改模板、不做双写，也不把 SQLite 当作知识库。

## 确认门

只有当前 run 同时满足以下条件时，才可准备交接候选：

- 用户明确说“复盘完成”或作出等价的当前版本确认。
- 交接引用的 facts hash、plan hash 和 confirmation version 可确定。
- 数据缺口和 `data_status` 保持原样。
- 包中没有账户数值、仓位数量、订单/成交聚合明细、上游 ID、成本、佣金、凭据、原始响应或完整事件馈送。

confirmed + partial 仍是 partial；确认不升级事实完整性。

## 当前未冻结事项

`confirmed-investment-review.v1` 的最终字段、Obsidian 路径、模板和写入方式仍由个人知识中心架构决定。在该 Schema 获得准确文本审批前：

- 交易中心只生成可审阅的脱敏 Markdown/JSON 候选，不宣称正式 ingest 包完成。
- 不直接写 Vault。
- 不推断目标路径或模板。
- 不把 Feishu 成功当作 Obsidian 成功。

## 最小候选内容

候选可以包含：本轮本地 run 标识、复盘区间、确认版本、facts hash、plan hash、data status、确认后的计划变化摘要、复盘结论、可复用规则、公开事件影响摘要和明确缺口。

候选不包含任何数据库路径、账户事实、私有 HTML 路径或外部系统 token。

知识中心任务写入后必须由它自己回读，并单独报告目标、版本和失败；交易中心不代报成功。
