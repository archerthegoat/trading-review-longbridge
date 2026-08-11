# trading-review-longbridge 仓库契约

## 目标

本仓库是 `trading-center-review` 的可安装源仓库。它提供 Agent Skill、受控的数据边界、模板、确定性脚本和最小回归测试，不承载任何个人账户或运行时状态。

## 目录契约

```text
trading-review-longbridge/
├── README.md
├── .gitignore
├── .github/workflows/ci.yml
├── docs/
├── skills/
│   └── trading-center-review/
│       ├── SKILL.md
│       ├── agents/openai.yaml
│       ├── assets/
│       ├── references/
│       └── scripts/
└── tests/
```

`skills/trading-center-review/SKILL.md` 是唯一 Skill 入口。仓库名与 Skill 名有意分开：前者是 `trading-review-longbridge`，后者是稳定的 `trading-center-review`，以保持 `$trading-center-review` 调用和已存在的自动化提示兼容。

## 调度语义

- 周二至周五：每日盘前复盘。
- 周六 09:00（Asia/Shanghai）：周度复盘与下周计划重整。
- 周一、周日：不触发。

仓库只记录语义和验收合同；实际调度器、时区、目标项目和失败重试属于外部运行配置，不能通过安装 Skill 自动创建或覆盖。

## 数据边界

- Longbridge 是当前唯一的券商数据接入边界，默认只读当前持仓快照，不提供交易执行或其他券商接入。订单、成交、账户净值、盈亏、资金流和对账单必须有本线程明确授权、明确窗口和 Git 工作树外的私有输出位置。
- 任何查询或解析失败都保留失败分类和“未验证”；成功返回空列表才允许写“接口在该窗口返回 0 条”。
- 事件信息是可见交付的一部分：财报、宏观和观察池相关事件不能只停留在私有工件中；每日两段事件必须使用同一五列表格合同。
- 飞书 Wiki 写入是独立确认门。未确认时只生成草稿；写入后必须回读，失败时不得声称已完成。

## 无 Wiki 时的初始化

安装 Skill 不会自动创建飞书资源。初始化顺序固定为：

1. 用 `lark-cli auth status --json --verify` 检查用户授权；`token_missing` 或 `needs_refresh` 先完成 `lark-cli auth login --domain wiki,docs --json`，不能把授权失败当成 Wiki 不存在。
2. 用 `lark-cli wiki +space-list --as user --page-all --format json` 动态发现已有 Space；不猜 `space_id`，不复用无关业务 Space。
3. 确认没有独立 Space 后，用 `wiki +space-create --as user --name ... --description ... --dry-run --format json` 预演；用户单独确认后才执行真实创建。
4. 用 `wiki +node-list --as user --space-id <space_id> --page-all --format json` 读取根节点；缺失时先预演 `+node-create --dry-run`，确认后创建并回读。
5. 如需 bot 写入，先由 Space 管理员把 bot 加入 Space，再分别用 `--as user`、`--as bot` 验证可见性；bot 不能代替用户创建 Space。
6. 文档写入按精确标题幂等检查、展示写入包、用户确认、写入、`docs +fetch` 回读的顺序执行。

完整命令和失败状态见 [`feishu-wiki-record-structure.md`](../skills/trading-center-review/references/feishu-wiki-record-structure.md)。

## 安装与锁定

可选：先查看源仓库中的 Skill（不会执行安装）：

```bash
npx skills add archerthegoat/trading-review-longbridge --list
npx skills add archerthegoat/trading-review-longbridge --skill trading-center-review
```

若消费者项目生成 `skills-lock.json`，该文件记录消费者的安装版本，不能作为源仓库运行配置提交回来。

## 迁移和回滚

发布新版本前，在隔离项目中运行 npx 安装、Python 测试、草稿校验和事件章节检查；再做至少一次每日与一次周六周度 shadow run。运行时切换必须单独完成，并保留旧 Skill 路径作为回滚点。发布仓库的新分支或版本不会自动切换现有自动化，也不会扩大券商或 Wiki 权限。
