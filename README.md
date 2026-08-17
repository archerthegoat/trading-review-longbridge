# trading-review-longbridge

中文优先的交易中心复盘 Agent Skill：把交易事实、用户陈述、公开市场证据、未验证缺口和待确认计划分开组织，默认不读取或写入券商/知识库的敏感数据。

## 安装

可选：先列出仓库中的可安装 Skill（不会执行安装）：

```bash
npx skills add archerthegoat/trading-review-longbridge --list
```

安装到当前项目的 Skill 目录（省略 `--agent` 时由 CLI 自动选择或提示目标 Agent）：

```bash
npx skills add archerthegoat/trading-review-longbridge \
  --skill trading-center-review
```

安装到当前用户的全局 Skill 目录：

```bash
npx skills add archerthegoat/trading-review-longbridge \
  --skill trading-center-review \
  --global
```

仓库名是 `trading-review-longbridge`；稳定的 Skill 名是 `trading-center-review`。消费者项目生成的 `skills-lock.json` 属于消费者的安装记录，不属于本源仓库。

## Skill 内容

入口文件为 [`skills/trading-center-review/SKILL.md`](skills/trading-center-review/SKILL.md)，并配套：

- `references/`：授权、Longbridge、Wiki、分析路由和 standalone 看板可视化边界。
- `assets/`：每日录入、每日复盘、交易想法、周度计划、周度复盘和固定 standalone HTML 模板。
- `scripts/`：交易日、公开市场观察池、事件信息、受限 Longbridge 导入、DeepSeek 脱敏摘要、standalone 看板渲染和草稿校验脚本。
- `agents/openai.yaml`：Codex 显示信息和默认调用提示。

## 固定工作流边界

- 周二至周五做每日复盘；周六 09:00（Asia/Shanghai）做周度复盘；周一、周日不触发。
- 周六周度复盘覆盖整周账户/交易/盈亏授权状态、计划与实际、下周计划重整和重要事件预览。
- 每日复盘必须同时保留“当天交易日重要事件”和“下一美股交易日重要事件”，使用带 Asia/Shanghai、美东时间、事件、状态、来源与数据状态的五列表格。
- Longbridge 只提供受限的只读数据边界；不下单、不改撤单、不读取凭据。当前 Skill 不提供其他券商接入。
- Longbridge 默认只读当前持仓快照；订单、成交、账户净值、盈亏、资金流和对账单需要本线程明确授权、明确时间范围和 Git 工作树外的私有输出目录。
- 飞书 Wiki 写入必须经过目标、内容、范围展示和明确确认，并在写入后回读；未确认时只生成本地草稿。
- DeepSeek 摘要是可选的外部文本整理层；只能接收 `trading-center-summary.v1` 白名单事实包，返回闭合 JSON，任何输入、网络、输出或隐私校验失败都保持阻塞状态。
- HTML 看板使用 `trading-review-dashboard.v1` 私有 JSON 和固定模板直接生成，不使用 `document.write`、iframe 或外部 CDN；真实复盘 JSON 与生成后的 HTML 都必须留在 Git 工作树外。
- 不能用当前持仓、用户口述或成功读取的局部数据冒充整周事实；查询失败保留“未验证/查询失败”，成功空数组才可写“接口在该窗口返回 0 条”。

## 飞书 Wiki 初始化

安装 Skill 不会自动创建飞书 Wiki、Space、节点或文档。只有在交易中心没有独立 Wiki 时，才按下面的顺序初始化；初始化和后续写入都必须由用户单独确认。

### 1. 先检查用户授权

```bash
lark-cli auth status --json --verify
```

如果返回 `token_missing`、`needs_refresh` 或缺少 Wiki/Docx 权限，不要据此判断 Wiki 不存在。先由用户完成授权：

```bash
lark-cli auth login --domain wiki,docs --json
```

然后重新运行 `auth status --json --verify`。`lark-cli` 需要在当前环境先完成配置和登录；初始化命令不会替代授权。

### 2. 动态查找已有 Space

```bash
lark-cli wiki +space-list --as user --page-all --format json
```

优先复用已经存在、用途明确的交易研究 Space，不猜 `space_id`，也不把交易内容写进其他业务 Space。Space 存在但目标节点不存在时，只初始化节点，不重复创建 Space。

### 3. 确认没有 Space 后，先预演再创建

建议名称为“交易投研中心”，但名称和描述必须由用户确认。先只预演：

```bash
lark-cli wiki +space-create \
  --as user \
  --name "交易投研中心" \
  --description "交易计划、每日复盘、周度复盘与已确认的研究摘要" \
  --dry-run \
  --format json
```

用户明确确认预演内容后，才去掉 `--dry-run` 执行一次。记录返回的 `space_id`，然后重新列出 Space 做回读；不要重复执行创建命令，也不要用 bot 身份创建 Space。

### 4. 初始化根节点并验证可见性

先读取根节点：

```bash
lark-cli wiki +node-list \
  --as user \
  --space-id <space_id> \
  --page-all \
  --format json
```

根节点不存在时，先用 `+node-create --dry-run` 预演一个“交易中心复盘”文档节点，再经用户确认后创建。创建后重新 `+node-list`，保存真实的节点 token；不猜 token，不使用 `0` 或占位 token。

### 5. 让自动化可读写，并单独验证 bot

如果后续复盘自动化使用 bot 写入子文档，需由 Space 管理员在飞书中把该 bot 加入这个 Space，并确认它能读取目标节点。然后分别用 `--as user` 和 `--as bot` 动态列出 Space/节点；用户可见性和 bot 可见性不是同一个成功条件。bot 不能绕过用户授权创建 Space。

### 6. 首次文档写入仍要走确认门

每次创建复盘文档前，先按精确标题检查同名节点；展示 `run_id`、目标节点、标题、正文摘要和字段范围，用户明确确认后才写入。已有 Space 且 bot 已加入时，使用 `docs +create` 在真实父节点下创建文档，写入后用 `docs +fetch` 回读标题、正文、版本和权限结果。文档创建成功但 `permission_grant` 失败时，必须分别报告，不能把二者合并成“初始化成功”。

详细的 Space 树、L0/L1/L2 写入边界和 `run_id` 完成门禁见 [`feishu-wiki-record-structure.md`](skills/trading-center-review/references/feishu-wiki-record-structure.md)。

## 本地验证

在仓库根目录运行：

```bash
python3 -m unittest discover -s tests -v
# 仅验证本地目录能被发现，不执行安装
npx skills add . --list
```

需要验证单个草稿时：

```bash
python3 skills/trading-center-review/scripts/validate_review_draft.py /absolute/path/to/draft.md
```

验证通过只表示结构、事件表格和明显凭据模式检查通过，不代表券商数据完整、账户已对账或结论正确。

## 隐私与发布

不要把持仓数量/成本、订单或成交明细、账户标识、原始券商响应、凭据、日志、研究记录、运行时草稿或 Wiki 原始响应提交到仓库。仓库中的脚本只定义受限读取和脱敏边界；实际运行输出必须落在 Git 工作树外的私有目录。

调度器、Longbridge CLI 登录状态和飞书授权属于运行环境，不由这个仓库自动创建或扩大。升级 Skill 后，应在隔离环境完成 npx 安装、脚本测试、事件章节检查和至少一次每日/一次周六周度 shadow run，再切换生产运行时。
