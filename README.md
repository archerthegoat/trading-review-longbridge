# trading-review

中文优先的交易中心复盘 Skill：把交易事实、用户陈述、公开市场证据、未验证缺口和待确认计划分开组织，默认不读取或写入券商/知识库的敏感数据。

## 安装

列出仓库中的可安装 Skill：

```bash
npx skills add archerthegoat/trading-review --list
```

安装到当前项目的 Codex Skill 目录：

```bash
npx skills add archerthegoat/trading-review \
  --skill trading-center-review \
  --agent codex
```

安装到当前用户的全局 Codex Skill 目录：

```bash
npx skills add archerthegoat/trading-review \
  --skill trading-center-review \
  --agent codex \
  --global
```

仓库名是 `trading-review`；稳定的 Skill 名是 `trading-center-review`。消费者项目生成的 `skills-lock.json` 属于消费者的安装记录，不属于本源仓库。

## Skill 内容

入口文件为 [`skills/trading-center-review/SKILL.md`](skills/trading-center-review/SKILL.md)，并配套：

- `references/`：授权、Longbridge、Wiki 和分析路由边界。
- `assets/`：每日录入、每日复盘、交易想法、周度计划和周度复盘模板。
- `scripts/`：交易日、公开市场观察池、事件信息、受限 Longbridge 导入和草稿校验脚本。
- `agents/openai.yaml`：Codex 显示信息和默认调用提示。

## 固定工作流边界

- 周二至周五做每日复盘；周六 09:00（Asia/Shanghai）做周度复盘；周一、周日不触发。
- 周六周度复盘覆盖整周账户/交易/盈亏授权状态、计划与实际、下周计划重整和重要事件预览。
- 每日复盘必须同时保留“当天交易日重要事件”和“下一美股交易日重要事件”，使用带 Asia/Shanghai、美东时间、事件、状态、来源与数据状态的五列表格。
- Longbridge 默认只读当前持仓快照；订单、成交、账户净值、盈亏、资金流和对账单需要本线程明确授权、明确时间范围和 Git 工作树外的私有输出目录。
- 飞书 Wiki 写入必须经过目标、内容、范围展示和明确确认，并在写入后回读；未确认时只生成本地草稿。
- 不能用当前持仓、用户口述或成功读取的局部数据冒充整周事实；查询失败保留“未验证/查询失败”，成功空数组才可写“接口在该窗口返回 0 条”。

## 本地验证

在仓库根目录运行：

```bash
python3 -m unittest discover -s tests -v
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
