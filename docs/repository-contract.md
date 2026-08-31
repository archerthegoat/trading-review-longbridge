# trading-review-longbridge 仓库契约

## 目标

本仓库是稳定 Skill `trading-center-review` 的可安装源。它维护模式路由、Longbridge 只读边界、SQLite schema/迁移、增量 runner、V1/V2 renderer、模板和测试，不承载个人账户数据或生产运行时。

## 目录与单一入口

~~~text
trading-review-longbridge/
├── README.md
├── 开发路径图.md
├── docs/
├── skills/trading-center-review/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── assets/
│   ├── references/
│   └── scripts/
└── tests/
~~~

`SKILL.md` 是唯一入口，保持共享门禁和按模式 reference 路由。详细每日、周度、SQLite、V2、分析与知识交接规则不重复堆入入口。

仓库名与 Skill 名有意分开，以保持 `$trading-center-review` 和现有自动化提示兼容。

## Skill-first 边界

本阶段不包含：

- `.codex-plugin/plugin.json`
- MCP server
- Codex App
- marketplace 条目
- hooks 或常驻后台服务

Plugin 决策门见架构报告。未来 Plugin 只能包装现有 Skill，不能要求重写它或改变数据库格式。

## 数据与状态

- Longbridge 是唯一券商边界，只运行当前任务已经授权的只读能力和窗口。
- 原始响应、机械日历和账户明细只写 Git 外的私有运行目录。
- owner-only SQLite 只接收固定白名单投影；路径、表、字段、迁移和禁止值见 `references/incremental-state-contract.md`。
- complete/成功 empty 分区可复用；partial、stale、blocked 和缺失分区必须重试。
- 数据状态与用户确认状态保持两轴。
- Schema v3 追加计划版本/区间、underlying episode 分类与执行质量指标；v2 P&L 表仅保留历史，新入口不再写入。禁止自由 JSON、原始执行价格、费用/佣金或具体期权合约身份。
- 周度来源每个获授权 run 仍需显式读取；历史 revision 不自动变成下一周数据。每日运行对 `weekly_*` 表为零写入。
- 查询失败不能变成空数据；当前快照不能替代历史、收盘或整周事实。

## UI 与分析

- V1 renderer、模板和测试保留为回滚线。
- V2 只消费严格固定 JSON，拒绝未知字段、敏感字段/值、外部 URL、脚本、iframe、运行时网络和 Git 内输入输出。
- V2 私有 account 模块继续校验，但不渲染账户概览、金额、基础币种、账户快照时间或金额控件。
- 每日与周度共用一个 V2 renderer/template 和原每日骨架；不设置日/周切换或独立 panel。无周度显示“尚未生成”，无合格每日包禁止生成页面。周度执行指标 blocked 可在单页中显示明确缺口。
- 计划统一 EMA20/50/200，增加右侧 bottom_reversal；pre_entry 不含 add，实际买入核验后才可生成 position_management 草案并再次确认。
- Codex 只读取脱敏固定事实，按 `facts_hash + plan_hash + analysis_contract_version` 缓存。
- 事实、解释、条件式检查和缺口分开；不生成无条件交易指令。

## 外部写入

安装或运行 Skill 不会：

- 创建或修改自动化。
- 写入 Longbridge。
- 创建或写入 Feishu Wiki。
- 创建或写入 Obsidian。
- 创建生产数据库，除非用户显式运行增量 runner。
- push、PR、merge、发布或切换生产。

用户当前 run 确认“复盘完成”后，只生成脱敏知识交接候选。知识中心任务是 Obsidian 唯一写入者。Feishu 过渡写入仍需要单独预览、确认和回读。

## 迁移与回滚

- 源码基线为 `codex/trading-review-semantics@3e9bafb`，实施分支为 `codex/trading-review-incremental-state`。
- 损坏的旧 `web-ui-v2` 只作恢复证据，不修补其 Git 元数据。
- 不复制日志、runtime、records、broker 数据、缓存或 `__pycache__`。
- 数据库迁移前生成 owner-only 备份；失败回滚并保持旧库。
- 自动化切回 V1 时忽略 SQLite 即可。

## 验证与验收

自动门至少覆盖：

- V1/V2 renderer 回归。
- complete、empty、partial、stale、blocked。
- SQLite 路径、权限、Schema、revision、幂等、锁和迁移失败。
- 每日 collection plan、固定脱敏 ingest、分析缓存和 manifest 隐私。
- 周度预期交易日完整性。
- 草稿结构、事件状态、成功空语义和敏感值。
- Skill quick validation、`git diff --check` 和静态离线扫描。

真实每日 shadow、真实周度 shadow、自动浏览器/视觉检查和用户人工 PASS 分开记录。自动测试通过不等于真实数据覆盖或人工验收通过。

发布、自动化切换和真实数据保留/清理策略均需单独批准。
