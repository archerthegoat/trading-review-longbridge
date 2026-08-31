import { readFileSync } from 'node:fs';
import type { CalendarEvent, Daily, DisplaySnapshot, PlanDetail, Position, Status, Weekly, WeeklyItem, WeeklySection } from './types.ts';

const TEMPLATE = new URL('../assets/trade-review-dashboard-v2-standalone.html', import.meta.url);
const MARKER = '<!--__TRADING_REVIEW_DASHBOARD_V2_BODY__-->';
const labels: Record<Status, string> = { complete: '已完成', partial: '部分可用', empty: '暂无数据', stale: '数据陈旧', blocked: '待核对' };
const diagnostic = /(?:\b(?:hash|sha-?256|revision|partition|payload|underlying|setup|CLI|schema|snapshot_at|source_scope|finance-calendar|macrodata|projection)\b|\b[a-z]+(?:_[a-z0-9]+)+\b|\b[0-9a-f]{32,}\b|半开|白名单|勾稽|消歧|分区|修订版本|修订编号|修订号|版本修订|字段|接口|投影|数据规范|数据契约|\/private\/|\/Users\/)/i;
const nonUS = /\b[A-Z0-9][A-Z0-9.\-]*\.(?:HK|SH|SZ|SG|JP)\b/i;
const isUS = (s: string) => /^[A-Z][A-Z0-9.\-]*\.US$/i.test(s);
export const escape = (value: unknown): string => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#x27;');
const ui = (value: unknown, fallback = '待核对') => {
  const text = String(value);
  return escape(diagnostic.test(text) || nonUS.test(text) ? fallback : text.replace(/(?<=[A-Za-z0-9])\.US\b/gi, ''));
};
const badge = (s: Status) => `<span class="v2-status-badge v2-status-${escape(s)}">${escape(labels[s])}</span>`;
const number = (n: number | null) => n === null ? '不可用' : n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n: number | null) => n === null ? '不可用' : `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(2)}%`;
const rfc = (s: string) => /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(s) && Number.isFinite(Date.parse(s));

export function localParts(instant: Date, zone: string): { date: string; time: string } {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(instant).map(x => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}:${p.second}` };
}
export function nyInstant(date: string, clock: string): Date {
  const time = clock.length === 5 ? `${clock}:00` : clock;
  const target = Date.parse(`${date}T${time}Z`);
  const offsets = new Set<number>();
  for (const delta of [-86400000, 0, 86400000]) {
    const candidate = new Date(target + delta);
    const local = localParts(candidate, 'America/New_York');
    offsets.add(Date.parse(`${local.date}T${local.time}Z`) - candidate.valueOf());
  }
  const matches = [...offsets].map(o => new Date(target - o)).filter(d => {
    const p = localParts(d, 'America/New_York');
    return p.date === date && p.time === time;
  });
  if (matches.length !== 1) throw new Error('ambiguous_or_nonexistent_event_time');
  return matches[0]!;
}
const dayAdd = (s: string, days: number) => new Date(Date.parse(`${s}T12:00:00Z`) + days * 86400000).toISOString().slice(0, 10);
const timeLabel = (s: string) => {
  if (!rfc(s)) return s.replaceAll('Asia/Shanghai', '北京').replaceAll(' ET', ' 纽约');
  const p = localParts(new Date(s), 'Asia/Shanghai');
  return `${p.date} ${p.time.slice(0, 5)} 北京`;
};
const period = (w: Weekly) => `${w.meta.period_start} 至 ${w.meta.period_end}`;
function weeklyContext(w: Weekly | null): string {
  if (!w) return '<div class="v2-weekly-context"><span>周度复盘尚未生成</span></div>';
  const state = [w.meta.freshness === 'current' ? '' : '内容陈旧，请重新复核', w.meta.confirmation_status === 'confirmed' ? '已确认' : '待确认'].filter(Boolean).join(' · ');
  return `<div class="v2-weekly-context"><strong>周度复盘 · ${escape(period(w))}</strong><span>${escape(state)}</span><small>周度更新：${escape(timeLabel(w.meta.generated_at))}</small></div>`;
}
function weeklyRows(items: WeeklyItem[]): string {
  const rows = items.filter(r => ui(r.label, '') && ui(r.summary, ''));
  return rows.length ? rows.map(r => `<article class="v2-weekly-item"><div class="v2-weekly-item-head"><strong>${ui(r.label)}</strong>${badge(r.data_status)}</div><p>${ui(r.summary)}</p></article>`).join('') : '<p class="v2-empty-inline">交易结论待补充</p>';
}
function weeklyInline(w: Weekly | null, section: WeeklySection, heading: string): string {
  if (!w || !w.sections[section].length) return '';
  const rows = ['operations', 'positions_plan', 'next_week'].includes(section) && w.meta.market_scope !== 'US' ? '<p class="v2-empty-inline">美股范围尚待核对。</p>' : weeklyRows(w.sections[section]);
  return `<details class="v2-weekly-inline" data-weekly-section="${escape(section)}"><summary><strong>${escape(heading)}</strong><span>${escape(period(w))}</span></summary><div class="v2-weekly-stack">${rows}</div></details>`;
}
function header(d: Daily, w: Weekly | null): string {
  const m = d.meta;
  return `<header class="v2-header"><div class="v2-brand"><strong>美股复盘</strong></div><div class="v2-header-meta"><span>日度回看</span><strong>${escape(m.review_date)}</strong><span>（ET）</span></div><div class="v2-header-meta v2-header-cutoff"><span>内容更新</span><strong>${escape(timeLabel(m.generated_at))}</strong></div></header>
  <div class="v2-boundary-strip"><span>${ui(m.review_label, '盘前观察与交易纪律')}</span><span>行情截至 ${escape(timeLabel(m.market_as_of))}</span>${badge(m.overall_status)}</div>${weeklyContext(w)}`;
}
function market(d: Daily, w: Weekly | null): string {
  const m = d.market;
  const rows = m.items.map(r => {
    const direction = m.status === 'complete' && r.data_status === 'complete' ? r.direction : 'flat';
    const flow = r.capital_flow ? `<small class="v2-flow">标的资金流 · ${escape(r.capital_flow.label)} ${number(r.capital_flow.value)} · ${escape(labels[r.capital_flow.data_status])}</small>` : '';
    const unavailable = r.unavailable_reason ? `<small class="v2-unavailable-note">${ui(r.unavailable_reason, '报价待核对')}</small>` : '';
    const dots = [0, 1, 2].map(i => `<span class="v2-meter-dot${i < r.strength ? ' is-on' : ''}" aria-hidden="true"></span>`).join('');
    return `<div class="v2-market-row"><div class="v2-market-name"><strong>${ui(r.name)}</strong><small>${ui(r.symbol)}${r.is_proxy ? ` · 代理：${ui(r.proxy_for)}` : ''} · ${ui(r.session)}</small>${flow}${unavailable}</div>
      <div class="v2-market-direction v2-direction-${direction}"><strong>${{ up: '↑', down: '↓', flat: '→' }[r.direction]}</strong><small>${escape(pct(r.change_pct))}</small></div><div class="v2-market-strength" aria-label="强度 ${r.strength}/3">${dots}</div><div class="v2-market-state"><strong>${ui(r.state)}</strong><small>${escape(number(r.value))}</small></div></div>`;
  }).join('') || '<div class="v2-empty">暂无已确认市场数据</div>';
  return `<section class="v2-market" aria-labelledby="market-heading"><div class="v2-section-title"><h1 id="market-heading">市场风险雷达</h1>${badge(m.status)}</div><p class="v2-side-note">相对昨日收盘；代理价格不等同于指数或收益率。</p><div class="v2-market-head" role="row"><span>资产/指数</span><span>方向</span><span>强度</span><span>状态</span></div><div class="v2-market-list" role="table">${rows}</div><p class="v2-side-note">Longbridge · ${escape(timeLabel(d.meta.market_as_of))}</p>${weeklyInline(w, 'market_radar', '周度市场背景')}</section>`;
}
function analysis(d: Daily, w: Weekly | null): string {
  const a = d.codex_analysis;
  const items = (rows: { label: string; text: string }[], empty: string) => rows.filter(r => ui(r.text, '')).map(r => `<li><strong>${ui(r.label, '观察')}</strong><span>${ui(r.text)}</span></li>`).join('') || `<li class="v2-empty-inline">${escape(empty)}</li>`;
  const checks = a.checks.map(c => `<div class="v2-check-row"><div><strong>如果</strong><span>${ui(c.if, '条件待确认')}</span></div><div><strong>则</strong><span>${ui(c.then, '先核对计划再行动')}</span></div><div><strong>否则</strong><span>${ui(c.else, '等待确认，不新增动作')}</span></div></div>`).join('') || '<p class="v2-empty-inline">暂无条件式检查</p>';
  return `<section class="v2-judgement" aria-labelledby="judgement-heading"><div class="v2-section-title"><h1 id="judgement-heading">Codex 盘前判断 <span>（核心结论）</span></h1>${badge(a.status)}<span class="v2-period">${ui(d.meta.period_label, '盘前观察')}</span></div><p class="v2-headline">${ui(a.headline, '先核对持仓计划，再评估新的买入机会。')}</p><div class="v2-analysis-grid"><section class="v2-analysis-card v2-card-fact"><h2>已确认事实</h2><ul>${items(a.facts, '暂无已确认事实')}</ul></section><section class="v2-analysis-card v2-card-risk"><h2>主要风险</h2><ul>${items(a.risks, '暂无已确认主要风险')}</ul></section></div><section class="v2-analysis-card v2-card-interpretation"><h2>Codex 解释</h2><ul>${items(a.interpretation, '暂无额外解释')}</ul></section><section class="v2-checks"><h2>今日条件式行动</h2><div class="v2-check-list">${checks}</div></section><details class="v2-analysis-card v2-card-gap"><summary>待确认事项</summary><ul>${items(a.gaps, '当前没有额外缺口')}</ul></details>${weeklyInline(w, 'judgement', '周度判断与纪律')}</section>`;
}
function operations(d: Daily): string {
  const o = d.operations, e = o.executions;
  const us = o.items.filter(r => isUS(r.symbol));
  const filled = us.filter(r => (r.execution_count ?? 0) > 0 && !['empty', 'blocked'].includes(r.data_status));
  const confirmed = o.market_scope === 'US' && us.length === o.items.length && ['complete', 'empty'].includes(e.data_status) && e.count !== null;
  let rows = filled.map(r => `<li><strong>${ui(r.action)} · ${ui(r.display_name)}</strong><span>${ui(r.role, '')} · ${ui(r.state, '待核对')} · ${ui(r.plan_relation, '执行是否符合计划待核对')}</span></li>`).join('');
  if (filled.length && confirmed && filled.reduce((n, r) => n + r.execution_count!, 0) < e.count!) rows += '<li class="v2-empty-inline">另有成交明细尚待核对。</li>';
  if (!rows) rows = confirmed && e.count === 0 ? '<li class="v2-empty-inline">上一交易日无已成交记录。</li>' : '<li class="v2-empty-inline">成交明细尚待核对。</li>';
  return `<section class="v2-operations" aria-labelledby="operations-heading"><div class="v2-section-title"><h1 id="operations-heading">上一交易日成交</h1><span class="v2-section-note">只看实际成交 · 对照事前计划</span></div><div class="v2-operation-meta"><span>${escape(d.meta.review_date)} · 纽约交易日</span>${e.data_status === 'stale' ? '<span>成交记录较旧，请重新核对。</span>' : ''}</div><ul class="v2-operations-list">${rows}</ul></section>`;
}
function planDetail(d: PlanDetail | null | undefined): string {
  if (!d) return '';
  const setup = { pullback: '趋势回调', breakout: '突破确认', range: '区间交易', bottom_reversal: '抄底反转（右侧确认）', position_management: '买入后仓位管理' };
  const zones = { observation: '观察区间', entry: '建仓区间', add: '加仓区间', reduce: '减仓区间', exit: '退出区间', invalidation: '失效区间' };
  const quotes = { below: '报价低于区间', inside: '报价位于区间', above: '报价高于区间', stale: '报价陈旧，区间保持不变', unavailable: '报价不可用，区间保持不变' };
  const zoneRows = d.zones.map(z => `<div class="v2-plan-zone" data-zone-kind="${z.kind}"><strong>${zones[z.kind]}${z.kind === 'add' && d.plan_status !== 'confirmed' ? ' · 待单独确认' : ''}</strong><span>${escape(number(Number(z.low)))}–${escape(number(Number(z.high)))} ${escape(z.currency)}</span><small>${ui(z.condition, '条件待确认')}</small></div>`).join('');
  const action = d.plan_stage === 'pre_entry' ? 'entry' : 'add';
  const readiness = !d.zones.some(z => z.kind === action) ? '仅观察：确认条件未齐，暂无可执行区间。' : d.plan_status !== 'confirmed' ? '区间仅为草案；该版本经你确认后才生效。' : '仅在该版本的全部条件满足时有效，不是无条件买卖指令。';
  return `<div class="v2-plan-detail"><div class="v2-plan-detail-header"><strong>${setup[d.setup_type]}</strong><span class="v2-status-badge v2-status-${{ draft: 'partial', confirmed: 'complete', expired: 'stale' }[d.plan_status]}">${{ draft: '待确认草案', confirmed: '已确认计划', expired: '已到期' }[d.plan_status]}</span><small>${quotes[d.quote_relation]}</small></div><small class="v2-plan-evidence">技术参考：${escape(d.evidence.as_of)} 收盘 · EMA20/50/200 · ATR14 ${escape(d.evidence.atr14)}</small><div class="v2-plan-zones">${zoneRows}</div><small class="v2-plan-evidence">${readiness}</small></div>`;
}
function planRow(r: Position, verified: boolean): string {
  const t = r.trigger_distance;
  const tone = !verified && ['red', 'green'].includes(t.tone) ? 'amber' : t.tone;
  const list = (v: string[], empty: string) => v.length ? v.map(x => `<span>${ui(x, empty)}</span>`).join('') : `<span class="v2-unavailable">${escape(empty)}</span>`;
  const classes = ['v2-plan-row', `v2-tab-${r.tab}`, r.near_trigger ? 'v2-near-trigger' : '', r.has_gap ? 'v2-has-gap' : ''].filter(Boolean).join(' ');
  const symbolNote = ui(r.symbol) === ui(r.display_name) ? '' : `<small>${ui(r.symbol)}</small>`;
  const detailLabel = !r.plan_detail ? '查看观察条件与下一步' : r.tab === 'holdings' ? '查看持仓计划' : '查看买入计划';
  return `<div class="${classes}" data-tab="${r.tab}"><div class="v2-plan-symbol"><strong>${ui(r.display_name)}</strong>${symbolNote}</div><div class="v2-plan-role"><strong>${ui(r.role, r.tab === 'holdings' ? '持仓' : '买入候选')}</strong><small>${ui(r.holding_state, r.tab === 'holdings' ? '本次读取时持仓' : '尚未持有')}</small></div><div class="v2-plan-coverage">${ui(r.plan_coverage, '计划待确认')}</div><div class="v2-trigger v2-tone-${tone}"><small>${ui(t.label, '触发条件')}</small><strong>${ui(t.value, '待确认')}</strong></div><details class="v2-plan-checks"><summary>${detailLabel}</summary><div class="v2-plan-check-grid"><div class="v2-plan-list"><strong>验证信号</strong>${list(r.signals, '信号待确认')}</div><div class="v2-plan-list"><strong>失效条件</strong>${list(r.invalidation, '失效条件待确认')}</div><div class="v2-plan-list"><strong>下一步检查</strong>${list(r.next_checks, '先确认计划')}</div></div>${r.has_gap && r.gap ? `<small class="v2-gap-label">${ui(r.gap, '计划条件待确认')}</small>` : ''}${planDetail(r.plan_detail)}</details></div>`;
}
function executionStrip(w: Weekly | null): string {
  if (!w) return '';
  const m = w.execution_metrics, scoped = w.meta.market_scope === 'US';
  const unavailable = ['blocked', 'empty'].includes(m.data_status) || !scoped;
  const rate = (v: number | null) => v === null || unavailable ? '不可计算' : `${(v * 100).toFixed(1)}%`;
  const cards = [
    ['计划覆盖率', rate(m.coverage_rate), `事前计划 ${m.covered_episode_count} / 适用 ${m.eligible_episode_count}`],
    ['按计划执行率', rate(m.execution_rate), `完全遵守 ${m.compliant_episode_count} / 可评估 ${m.assessable_episode_count}`],
    ['计划胜率', rate(m.plan_win_rate), `成功 ${m.successful_episode_count} / 已结案 ${m.resolved_episode_count}`],
    ['需复盘', unavailable ? '不可计算' : `${m.review_needed_count} 笔`, '违规、计划失败或证据不足'],
  ].map(([l, v, denominator]) => `<div class="v2-execution-metric"><small>${escape(l)}</small><strong>${escape(v)}</strong><span>${escape(unavailable ? '' : denominator)}</span></div>`).join('');
  const excluded = unavailable ? '' : `排除于胜率分母：未结束 ${m.open_episode_count} · 持平 ${m.flat_episode_count} · 不可核验 ${m.unverifiable_episode_count}。`;
  const note = !scoped ? '美股统计范围待核对，暂不展示比例。' : m.data_status === 'empty' ? '本周没有适用的交易。' : '缺少事前计划或完整执行记录，暂不能评估。';
  const gap = unavailable ? `<p class="v2-execution-gap">${escape(note)}</p>` : m.gap ? `<p class="v2-execution-gap">${ui(m.gap, '部分交易仍待复核。')}</p>` : '';
  return `<div class="v2-execution-quality" aria-label="周度计划执行质量"><div class="v2-execution-heading"><strong>周度执行质量</strong><span>${escape(period(w))}</span>${badge(scoped ? m.data_status : 'partial')}</div><div class="v2-execution-metrics">${cards}</div><p class="v2-execution-exclusions">${escape(excluded)}</p>${gap}</div>`;
}
function weeklyReview(w: Weekly | null): string {
  if (!w) return '';
  const episodes = w.review_episodes.filter(r => isUS(r.underlying)).map(r => {
    const compliance = { compliant: '按计划执行', non_compliant: '未按计划执行', unassessable: '执行不可评估' }[r.compliance_status];
    const outcome = { success: '计划成功', failure: '计划失败', open: '尚未结束', flat: '结果持平', unverifiable: '结果不可核验' }[r.outcome_status];
    return `<article class="v2-episode-review"><div><strong>${escape(r.market_date)} · ${ui(r.underlying)} · ${r.side === 'buy' ? '买入' : r.side === 'sell' ? '卖出' : '交易'}</strong>${badge(r.data_status)}</div><p>${compliance} · ${outcome}</p><small>${r.plan_id === null ? '无事前已确认计划' : '依据事前已确认计划复核'}</small><p><strong>原因：</strong>${ui(r.reason, '原因待复核')}</p><p><strong>下一条规则：</strong>${ui(r.next_rule, '先核对事前计划')}</p></article>`;
  });
  return (episodes.length ? `<details class="v2-weekly-inline v2-episode-details"><summary><strong>需具体复盘 · ${episodes.length} 笔</strong><span>只看执行与规则</span></summary><div class="v2-weekly-stack">${episodes.join('')}</div></details>` : '') + weeklyInline(w, 'positions_plan', '周度持仓计划回看') + weeklyInline(w, 'plan_review', '计划复核与纪律') + weeklyInline(w, 'next_week', '后续计划待确认');
}
function positions(d: Daily, w: Weekly | null): string {
  const p = d.positions_plans;
  const held = p.items.filter(r => isUS(r.symbol) && r.tab === 'holdings');
  const plans = p.items.filter(r => isUS(r.symbol) && r.tab === 'plan').filter(r => {
    const existing = held.find(h => h.symbol.toUpperCase() === r.symbol.toUpperCase());
    if (existing && r.plan_detail && JSON.stringify(r.plan_detail) !== JSON.stringify(existing.plan_detail)) throw new Error('held_plan_requires_reconciliation');
    return !existing;
  });
  const rows = (rs: Position[]) => rs.map(r => planRow(r, p.status === 'complete')).join('');
  const holdingsBody = rows(held) || '<p class="v2-empty v2-view-empty">暂无已核验的当前持仓。</p>';
  const categories = p.strategy_categories ?? [];
  let plansBody = categories.map(c => { const members = plans.filter(r => r.strategy_category === c); return `<section class="v2-strategy-group"><h2>${ui(c, '分类待确认')}<span>${members.length} 个候选</span></h2>${rows(members) || '<p class="v2-empty-inline">暂未加入候选</p>'}</section>`; }).join('');
  const uncategorized = plans.filter(r => !r.strategy_category);
  if (uncategorized.length) plansBody += `<section class="v2-strategy-group"><h2>待分类</h2>${rows(uncategorized)}</section>`;
  if (!plans.length) plansBody += '<p class="v2-empty v2-view-empty">暂无已核验的未持仓买入候选。已有仓位请切换至“当前持仓及计划”。</p>';
  const hc = held.length || !plans.length ? ' checked' : '', pc = hc ? '' : ' checked';
  const filterEmpty = '<p class="v2-empty v2-filter-empty" role="status">没有符合当前筛选条件的标的；取消筛选可查看全部。</p>';
  return `<section class="v2-plans" aria-labelledby="plans-heading"><div class="v2-section-title v2-plans-title"><h1 id="plans-heading">持仓 × 计划</h1>${badge(p.status)}</div>${executionStrip(w)}<div class="v2-plan-controls"><div class="v2-tabs" role="group" aria-label="持仓和计划视图"><label for="v2-view-holdings"><input class="v2-state" id="v2-view-holdings" type="radio" name="v2-plan-view"${hc} aria-label="查看当前持仓及计划" aria-controls="v2-plan-panel">当前持仓及计划 <small>${held.length}</small></label><label for="v2-view-plan"><input class="v2-state" id="v2-view-plan" type="radio" name="v2-plan-view"${pc} aria-label="查看未持仓买入计划" aria-controls="v2-plan-panel">未持仓买入计划 <small>${plans.length}</small></label></div><div class="v2-filters" role="group" aria-label="持仓和计划筛选"><label for="v2-filter-near"><input class="v2-state" id="v2-filter-near" type="checkbox" aria-label="只看接近触发" aria-controls="v2-plan-panel">只看接近触发</label><label for="v2-filter-gap"><input class="v2-state" id="v2-filter-gap" type="checkbox" aria-label="只看待确认" aria-controls="v2-plan-panel">只看待确认</label></div></div><div id="v2-plan-panel" class="v2-plan-scroll" role="region" aria-label="持仓与计划内容"><div class="v2-plan-list-grid" data-holdings-checked="${hc.trim()}" data-plan-checked="${pc.trim()}"><div class="v2-plan-view v2-tab-holdings"><p class="v2-plan-view-note">已有仓位的计划随持仓查看；买入之后才评估加仓。</p><div class="v2-plan-head"><span>标的</span><span>持仓状态</span><span>自身计划</span><span>触发条件</span></div>${holdingsBody}${held.length ? filterEmpty : ''}</div><div class="v2-plan-view v2-tab-plan">${!categories.length ? '<p class="v2-plan-view-note">原五类策略名称待确认；不会用技术形态替代。</p>' : ''}${plansBody}${plans.length ? filterEmpty : ''}</div></div></div>${weeklyReview(w)}</section>`;
}
export function calendarRows(d: Daily, w: Weekly | null): CalendarEvent[] {
  const rows = structuredClone(d.events.groups.flatMap(g => g.events));
  if (w && !d.events.reference_at) {
    const known = new Set(rows.map(r => `${r.et_date}|${r.et_time.slice(0, 5)}|${r.title.trim().toLowerCase()}`));
    for (const row of w.sections.events) {
      if (!rfc(row.label) || !ui(row.summary, '')) continue;
      const instant = new Date(row.label), et = localParts(instant, 'America/New_York'), sh = localParts(instant, 'Asia/Shanghai');
      const key = `${et.date}|${et.time.slice(0, 5)}|${row.summary.trim().toLowerCase()}`;
      if (known.has(key)) continue;
      rows.push({ et_date: et.date, et_time: et.time.slice(0, 5), shanghai_time: sh.time.slice(0, 5), title: row.summary, status: '未验证', source: '排期待核对', data_status: 'partial', object: '影响对象待补充', impact_channel: '影响因素待补充' });
      known.add(key);
    }
  }
  const unique = new Map<string, CalendarEvent>();
  for (const row of rows) {
    if (!ui(row.title, '') || nonUS.test(row.object)) continue;
    const key = `${row.et_date}|${row.et_time}|${row.title.trim().toLowerCase()}|${row.object.trim().toLowerCase()}`;
    const old = unique.get(key);
    if (!old) unique.set(key, row);
    else {
      if (old.status !== row.status || old.data_status !== row.data_status) { old.status = '未验证'; old.data_status = 'partial'; }
      for (const k of ['watch_for', 'speaker', 'source_url', 'kind'] as const) if (!(k in old) && k in row) Object.assign(old, { [k]: row[k] });
    }
  }
  const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
  return [...unique.values()].sort((a, b) => compare(a.et_date, b.et_date) || compare(a.et_time, b.et_time) || compare(a.title, b.title));
}
const eventTitle = (v: string) => v.replace(/^美国\s*[,，]\s*/, '');
function eventRow(e: CalendarEvent): string {
  const instant = nyInstant(e.et_date, e.et_time), sh = localParts(instant, 'Asia/Shanghai');
  const speech = e.kind === 'fed_speech' ? `<span class="v2-speech-tag">联储讲话 · ${ui(e.speaker)}</span>` : '';
  let status = e.status === '已取消' ? '<span class="v2-event-status v2-event-status-unverified">已取消</span>' : '';
  if (e.data_status === 'stale') status += '<span class="v2-event-caution">排期较旧，请重新核对</span>';
  else if (e.status === '未验证' || e.data_status === 'blocked') status += '<span class="v2-event-caution">事件信息待核对</span>';
  const watch = (e.watch_for ?? '').split(/\r?\n/).map(s => ui(s.trim(), '')).filter(Boolean).map(s => `<p class="v2-event-watch">${s}</p>`).join('');
  return `<article class="v2-event-row"><div class="v2-event-times"><strong>${e.et_time.slice(0, 5)} 纽约</strong><small>${sh.date.slice(5)} ${sh.time.slice(0, 5)} 北京</small></div><div class="v2-event-main"><div class="v2-event-title"><strong>${ui(eventTitle(e.title))}</strong>${speech}${status}</div><p class="v2-event-impact"><strong>${ui(e.object, '影响对象待补充')}</strong> · ${ui(e.impact_channel, '影响因素待补充')}</p>${watch}</div></article>`;
}
function events(d: Daily, w: Weekly | null): string {
  const e = d.events, reference = localParts(new Date(e.reference_at ?? d.meta.generated_at), 'America/New_York').date;
  const weekday = (new Date(`${reference}T12:00:00Z`).getUTCDay() + 6) % 7;
  const monday = dayAdd(reference, -weekday);
  const rows = calendarRows(d, w).filter(r => r.et_date >= monday && r.et_date < dayAdd(monday, 14));
  const weeks = ['本周', '下周'].map((label, wi) => {
    const start = dayAdd(monday, 7 * wi), end = dayAdd(start, 6);
    const days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((dayLabel, di) => {
      const day = dayAdd(start, di), members = rows.filter(r => r.et_date === day);
      const titles = members.slice(0, 2).map(r => eventTitle(r.title)).join(' / ') + (members.length > 2 ? ' 等' : '');
      return `<details class="v2-calendar-day" data-date="${day}"${day === reference && members.length ? ' open' : ''}><summary><span class="v2-calendar-date"><strong>${dayLabel}</strong><time datetime="${day}">${day.slice(5).replace('-', '/')}</time></span><span class="v2-calendar-preview">${members.length ? ui(titles, '事件信息待核对') : '暂无已收录事件'}</span><small>${members.length} 项</small></summary><div class="v2-event-list">${members.map(eventRow).join('') || '<p class="v2-empty-inline">暂无已收录事件。</p>'}</div></details>`;
    }).join('');
    return `<section class="v2-calendar-week"><h2>${label}<span>${start} — ${end}</span></h2>${days}</section>`;
  }).join('');
  return `<section class="v2-events" aria-labelledby="events-heading"><div class="v2-section-title"><h1 id="events-heading">重要事件与时间轴</h1><span class="v2-section-note">按纽约日期分桶 · 同时显示北京时间</span></div><p class="v2-calendar-asof">日历核对：${escape(timeLabel(e.reference_at ?? d.meta.generated_at))} · 以下为情景分析，不是已公布结果或确定涨跌。</p>${e.status === 'stale' ? '<p class="v2-calendar-asof">日历较旧，使用前请重新核对排期。</p>' : ''}<div class="v2-calendar-weeks">${weeks}</div></section>`;
}
function dataNote(d: Daily, w: Weekly | null): string {
  return `<details class="v2-data-note"><summary><strong>更新与使用说明</strong><span>点击展开</span></summary><div class="v2-data-content"><p>行情截至：${escape(timeLabel(d.meta.market_as_of))}</p><p>周度更新：${w ? escape(timeLabel(w.meta.generated_at)) : '尚未生成'}。周度内容不随每日页面刷新而重新计算。</p><p>刷新仅重载这份记录，不代表新行情；未确认的计划不能直接执行。</p></div></details>`;
}

/** Only call with a snapshot admitted by the data boundary. No user template overrides. */
export function render(snapshot: DisplaySnapshot): string {
  const template = readFileSync(TEMPLATE, 'utf8');
  if (template.split(MARKER).length !== 2 || /<script|<iframe|<link|<img|<object|<embed|<svg|srcdoc|\bon[a-z][a-z0-9_-]*\s*=|javascript:|@import|url\(|https?:\/\//i.test(template)) throw new Error('unsafe_bundled_template');
  const d = snapshot.daily, w = snapshot.weekly;
  const body = `${header(d, w)}<div class="v2-top-grid"><div class="v2-market-pane">${market(d, w)}</div>${analysis(d, w)}</div>${operations(d)}${positions(d, w)}${events(d, w)}${dataNote(d, w)}`;
  return template.replace(MARKER, `<div class="v2-shell"><main class="v2-unified-view">${body}</main></div>`).replace('<title>交易中心 · 盘前复盘 V2</title>', '<title>交易中心 · 复盘</title>');
}
