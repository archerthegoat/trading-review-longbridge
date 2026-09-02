# Daily Trade Journal

一个极简、隐私优先的 Codex 每日交易日记 Skill。

它每天只做两件事：

1. 汇总上一已完成美股交易日的脱敏实际动作。
2. 核对这些动作是否符合成交前已经确认的交易计划。

不做行情看板，不分析新闻和事件，也不展示账户、盈亏、价格或成交数量。

## 输出长什么样

```text
复盘交易日：YYYY-MM-DD

- 标的 A｜买入｜正股｜按计划
- 标的 B｜卖出｜0DTE 期权｜无法核对
```

每条动作只保留：

- 标的 underlying
- 买入或卖出
- 正股、单股杠杆 ETF、0DTE 期权、其他期权或无法识别
- 按计划、偏离计划或无法核对

同一标的、动作和工具的拆分成交会被合并。

## 安装

```bash
npx skills add archerthegoat/trading-review-longbridge \
  --skill daily-trade-journal
```

## 使用

安装后，可以在 Codex 中这样开始：

```text
使用 daily-trade-journal，复盘上一已完成美股交易日。
先展示脱敏草稿，不要直接写入 Obsidian。
```

Skill 使用以下证据：

- Longbridge 美股交易日历
- 上一已完成交易日范围内的只读 execution history
- 成交前已经明确确认的周计划或日内修订

草稿、闲聊、待确认内容以及事后推断不能作为计划证据。

## 隐私边界

公开输出和持久化结果不会包含：

- 数量、价格、成本、佣金或盈亏
- 账户、订单、成交、请求或会话 ID
- 原始券商响应
- 具体期权合约身份
- 凭据、Cookie 或 API Key

输入结构、交易日窗口、计划确认状态或隐私边界不明确时，结果必须保持 `blocked`，不能猜测补齐。

0DTE 只根据到期日是否等于复盘交易日进行机械判断，不会被推断成 Long Call。

## Obsidian 写入

Skill 默认只生成草稿，不自动写入 Obsidian。

只有用户在当前任务中明确回复“确认写入”后，才允许更新：

```text
25 投资交易/10 每日复盘/<review_date>.md
```

写入只管理 `daily-trade-journal` 区块，保留“我的补充”和其他用户文本。写入前检查并发变化，写入后分别进行文件系统和 Obsidian CLI 回读。

自动化可以定时创建独立复盘任务，但不能绕过确认门禁或自动写入日记。

## 项目结构

```text
skills/daily-trade-journal/
├── SKILL.md
├── agents/openai.yaml
└── scripts/project_daily_trade_journal.py

tests/test_project_daily_trade_journal.py
开发路径图.md
```

确定性的投影脚本只负责脱敏、聚合和计划对齐，不直接调用券商、数据库或 Obsidian。

## 开发验证

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 \
  -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 \
  "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" \
  skills/daily-trade-journal

npx --yes skills add . --list
git diff --check
```

产品边界、自动化计划和尚未完成的真实运行门禁见 [开发路径图](开发路径图.md)。
