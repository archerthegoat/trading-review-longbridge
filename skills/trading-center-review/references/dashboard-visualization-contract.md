# 复盘看板可视化契约

## 目的

将每日盘前与周度复盘稳定渲染为直接可打开的 standalone HTML，同时把组合级逻辑、账户证据、执行复盘、逐标的计划和全市场事件日历分开。HTML 只作为私有阅读工件，不是券商记录、交易指令或公开报告。

## 固定信息架构

输出顺序不得改变：

1. `交易风格与整体逻辑`：市场基调、组合级动作和候选方向。仓位集中、行业轮动等组合判断只放这里，不生成伪标的计划。
2. `账户与交易证据`：先展示证据边界，再展示成交与按标的归属的损益。默认不采集、不展示资金流水。
3. `本周损益与执行复盘`：分开记录逻辑是否失效、执行纪律问题和当前持仓管理。
4. `当前有效的标的交易计划`：一张卡只对应一个明确 ticker；二选一候选可在各自卡片和共同 callout 中表达。部署状态、证据复核、行业暴露或系统流程不得成为计划卡。
5. `本周与下周重要事件`：使用 Longbridge Calendar/News 或其他获准的全市场宏观、政策、监管和行业财报数据，不以持仓池代替市场事件筛选；事件不混入计划卡。

## 渲染方式

使用：

```bash
python3 scripts/render_trade_review_dashboard.py \
  --input /私有运行目录/review-dashboard.json \
  --output /私有运行目录/trade-review-dashboard-standalone.html
```

输入与输出都必须位于 Git 工作树外。渲染器从 `assets/trade-review-dashboard-standalone.html` 读取固定样式，直接生成一个 HTML 文档；禁止改回 `document.write`、动态脚本注入、iframe `srcdoc` 或依赖外部 CDN 的实现。所有输入文本均按纯文本转义，不能在 JSON 中注入 HTML。

## 输入结构

顶层 `schema_version` 固定为 `trading-review-dashboard.v1`。不需要的可选区块可省略；`eyebrow`、`title`、`subtitle` 必须提供。

```json
{
  "schema_version": "trading-review-dashboard.v1",
  "eyebrow": "交易研究中心 · 周度复盘",
  "title": "每日盘前与周度复盘",
  "subtitle": "事实、组合逻辑、标的计划和市场事件分栏呈现。",
  "badges": [
    {"label": "周度", "value": "YYYY-MM-DD → YYYY-MM-DD", "tone": "blue"}
  ],
  "status": {
    "title": "已纳入 · 已授权证据可用",
    "detail": "说明数据覆盖、缺口和快照时间。",
    "tone": "green"
  },
  "summary_cards": [
    {"kicker": "市场基调", "title": "组合级标题", "text": "组合级判断和条件。", "tone": "blue"}
  ],
  "summary_note": "区分用户判断、机械事实和待核对内容。",
  "account": {
    "metrics": [
      {"label": "周期账户净变动", "value": "+0.00", "meta": "起点 → 终点", "tone": "green"}
    ],
    "evidence": [
      {"label": "覆盖范围", "value": "说明授权窗口与缺失字段"}
    ],
    "note": "不同接口按各自口径聚合，不把缺失字段补成事实。",
    "pnl": [
      {"symbol": "DEMO", "value": 0.0}
    ],
    "pnl_note": "每个 ticker 独立归属，不使用“某标的或其他”。"
  },
  "review_cards": [
    {
      "kicker": "执行纪律",
      "title": "复盘主题",
      "text": "事实、原因和后续约束。",
      "meta": ["数据状态"],
      "tone": "amber"
    }
  ],
  "plan_callout": "说明当前计划基线与候选席位关系。",
  "plans": [
    {
      "symbol": "DEMO",
      "name": "标的级计划",
      "subtitle": "当前角色",
      "state": "观察",
      "state_tone": "amber",
      "open": true,
      "blocks": [
        {"label": "触发", "value": "只记录已确认条件", "full": false},
        {"label": "失效", "value": "只记录已确认条件", "full": false},
        {"label": "边界", "value": "计划不等于执行", "full": true}
      ]
    }
  ],
  "excluded": [
    {"symbol": "DEMO2", "reason": "不在当前观察列表"}
  ],
  "event_groups": [
    {
      "label": "下周",
      "range": "MM-DD → MM-DD",
      "events": [
        {
          "date": "MM-DD",
          "time": "Asia/Shanghai / ET",
          "title": "宏观或行业事件",
          "meta": "数据状态摘要",
          "kind": "macro",
          "tag": "宏观 · 预期",
          "source": "Longbridge Calendar",
          "status": "预期 / 未公布 / 已发生 / 未验证",
          "impact": "影响通道，不写买卖指令",
          "open": true
        }
      ]
    }
  ],
  "event_note": "说明窗口、返回上限和未验证项。",
  "footer": "来源、时间范围和证据口径。"
}
```

允许的 `tone`：`neutral`、`blue`、`green`、`amber`、`red`。允许的事件 `kind`：`news`、`macro`、`earnings`、`risk`。损益 `value` 必须为数值，显示格式和条形长度由脚本机械计算。

## 数据与发布边界

- 模板、渲染脚本、契约和无真实数据的测试可以进入 Git。
- 每期 JSON、渲染后的 HTML、账户净值、逐标的损益、持仓、成交与个人计划必须留在私有运行目录，不得提交或推送。
- 看板只能呈现已获授权的数据范围。模板存在不构成账户读取授权。
- 不把资金流水业务类型作为复盘完成门槛；只有用户明确要求资金勾稽时，才在 Markdown 私有证据中单独处理，默认看板不显示。
- 输出文件权限固定为仅所有者可读写。模板或 Schema 校验失败时返回 `blocked`，不得生成半成品并声称完成。

## 验收检查

1. 直接用浏览器打开生成文件，无网络请求、无控制台语法错误、无 iframe、无 `document.write`。
2. 桌面宽度和手机宽度均可读；表格可横向滚动，计划和事件可用原生 `details` 展开。
3. 证据边界位于损益表上方；资金流水、部署信息和证据复核任务不出现在计划区。
4. 计划卡全部有 ticker；组合级风格与行业暴露只在摘要区。
5. 事件区来自全市场日历窗口，保留来源、状态、时间和影响通道。
6. 输入中的 `<script>`、引号和 HTML 标签在输出中均被转义为文本。
