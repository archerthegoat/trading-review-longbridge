---
name: daily-trade-journal
description: 生成上一已完成美股交易日的脱敏实际动作并与事前确认计划对齐；仅在用户明确说“确认写入”后写入对应 Obsidian 每日日记。
---

# 每日交易日记

把一次每日复盘限定为两件事：上一已完成 NY 交易日发生了哪些实际动作，以及这些动作是否符合此前已经确认的计划。保持事实、对齐结果和用户补充分开。

## 读取边界

1. 必须使用显式 Longbridge 交易日历 artifact 确定上一已完成的美股交易日和该日的半开 NY 时间区间；缺失或无效 artifact 不得产出 `complete` 或 `empty`。
2. 只读取该区间内的只读 `order executions --history`、此前确认的周计划和已确认的日内修订。先将成交投影为 underlying、动作和工具，再继续复盘。
3. 不读取或推断 assets、positions、orders、quotes、events、news、valuations、flows、P&L、SQLite、凭据、账户信息或具体期权合约。
4. 只允许以下工具标签：`正股`、`单股杠杆 ETF`、`0DTE 期权`、`其他期权`、`无法识别`。0DTE 只能依据到期日和 review date 的机械相等判定，绝不能写成 Long Call。
5. 将同一 underlying、动作、工具的拆分成交合并。不要输出数量、价格、成本、佣金、账户、订单/成交 ID、原始券商响应或具体期权身份。

## 投影和对齐

使用本 Skill 附带的纯确定性脚本；脚本不调用 Longbridge、Vault 或数据库：

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 \
  skills/daily-trade-journal/scripts/project_daily_trade_journal.py \
  --review-date <YYYY-MM-DD> \
  --raw-executions <owner-only-json> \
  --trading-calendar <calendar-json> \
  --confirmed-plans <confirmed-plan-json> \
  --output <owner-only-json>
```

计划证据必须是此前已确认版本；只有显式 `confirmed` / `active` 状态或无冲突的 `confirmed=true` 才能参与匹配，缺少确认依据、`draft` 或 `pending` 只能得到 `无法核对`。可以另传 `--weekly-plan` 和 `--intraday-revisions`，它们会在同一确定性投影中合并。只要输入格式、时间窗口、工具上下文或隐私边界不明确，保留 `status=blocked`；成功解析的无成交结果才是 `status=empty`。`status=complete` 不代表每个动作都能对齐计划：单行对齐值仍只能是 `按计划`、`偏离计划` 或 `无法核对`。

对齐规则必须机械执行：只有 prior evidence 精确匹配 underlying、action 和 tool 才是 `按计划`；计划明确了不同动作/工具或明确禁止才是 `偏离计划`；缺少、冲突、过期或不完整证据都是 `无法核对`。不得从持仓、盈亏、行情、习惯或模型常识补齐。

## 交付和写入门禁

1. 先在当前任务中展示最终脱敏版本，最多询问两个澄清问题。不要把草稿称为已写入。
2. 只有用户在当前任务中明确说出“确认写入”，才可以写入：
   `25 投资交易/10 每日复盘/<review_date>.md`
3. 只写 `complete` 或成功 `empty` 版本；`blocked`、未确认、目标并发变化、隐私校验失败或回读不一致时不得写入。
4. 写前重读 live target 并检查并发变化。新建文件使用最小模板；已有文件只管理 `<!-- daily-trade-journal:managed:start -->` 到 `<!-- daily-trade-journal:managed:end -->`，保留 `## 我的补充` 和全部非 managed 文本。相同 managed 内容为 `no_op`；不同内容没有“修订版确认”时为 `deferred`。
5. 如需替换，使用 owner-only 临时文件和原子替换；随后先做文件系统回读，再按顺序做 Obsidian CLI 回读。不要使用 Bridge、outbox、receiver、receipt、数据库、UI、远端同步、Feishu 或 Wiki。

本 Skill 是仓库唯一产品；旧综合能力已删除。自动化可以创建独立任务调用本 Skill，但不得绕过“确认写入”门禁、启用 8765 UI、Bridge/SQLite 或自动写入。
