import test from 'node:test';
import assert from 'node:assert/strict';
import { validate, project } from '../../skills/trading-center-review/web/data.ts';
import { render, nyInstant, calendarRows } from '../../skills/trading-center-review/web/render.ts';
import { fixture, weekly, snapshot, normalized, pythonRender, python } from './helpers.ts';
import type { PlanDetail } from '../../skills/trading-center-review/web/types.ts';

for (const state of ['complete', 'partial', 'empty', 'stale']) test(`TS preserves the Python V2 DOM and all text for ${state}`, () => {
  const view = snapshot(state);
  assert.equal(normalized(render(view)), normalized(pythonRender(view)));
  assert.equal((render(view).match(/<main\b/g) ?? []).length, 1);
  assert.equal((render(view).match(/class="v2-calendar-day"/g) ?? []).length, 14);
});
test('daily-only and legacy weekly-calendar merge preserve the same UI', () => {
  const d = project(fixture(), null);
  assert.equal(normalized(render(d)), normalized(pythonRender(d)));
  const w = weekly(); w.sections.events[0]!.label = '2026-08-31T14:00:00Z';
  const v = project(fixture(), w);
  assert.equal(normalized(render(v)), normalized(pythonRender(v)));
});
test('actual fills, five original categories and plan lifecycle remain distinct', () => {
  const d = fixture();
  d.operations.items = []; d.operations.market_scope = 'US'; d.operations.executions.count = 0;
  const view = project(d, weekly());
  assert.match(render(view), /上一交易日无已成交记录/);
  view.daily.positions_plans.strategy_categories = ['甲', '乙', '丙', '丁', '戊'];
  const candidate = structuredClone(view.daily.positions_plans.items[0]!);
  candidate.symbol = 'NEW.US'; candidate.display_name = 'NEW'; candidate.tab = 'plan'; candidate.strategy_category = '甲';
  candidate.plan_detail = JSON.parse(python("import sys,json; sys.path.insert(0,'tests'); from test_render_trade_review_dashboard_v2 import plan_detail; print(json.dumps(plan_detail()))")) as PlanDetail;
  view.daily.positions_plans.items.push(candidate);
  const admitted = validate(view);
  assert.equal(normalized(render(admitted)), normalized(pythonRender(admitted)));
  assert.equal((render(admitted).match(/class="v2-strategy-group"/g) ?? []).length, 6); // Five confirmed labels plus the original uncategorized candidate.
  for (const category of ['甲', '乙', '丙', '丁', '戊']) assert.ok(render(admitted).includes(`<h2>${category}<span>`));
  assert.match(render(admitted), /抄底反转（右侧确认）/);
  assert.doesNotMatch(render(admitted), /data-zone-kind="add"/);
});
test('data boundary rejects private/unknown data, invalid originals, fake fills, non-US and future weekly', () => {
  const mutations = [
    (v: any) => { v.daily.account = {}; }, (v: any) => { v.daily.meta.account_label = 'private'; },
    (v: any) => { v.daily.operations.orders = {}; }, (v: any) => { v.daily.positions_plans.items[0].symbol = 'DEMO.HK'; },
    (v: any) => { v.weekly.meta.period_end = '2026-09-04'; }, (v: any) => { v.daily.unknown = 'extra'; },
  ];
  for (const mutate of mutations) { const v = snapshot(); mutate(v); assert.throws(() => validate(v)); }
  const original = fixture(); original.account.unknown = 'unapproved'; assert.throws(() => project(original, null));
});
test('event conflicts are not optimistic; DST gaps and folds fail closed', () => {
  const v = snapshot();
  const event = v.daily.events.groups[0]?.events[0]; assert.ok(event);
  v.daily.events.groups[0]!.events.push({ ...event, status: '未验证', data_status: 'partial' });
  const merged = calendarRows(v.daily, null).find(r => r.title === event.title)!;
  assert.equal(merged.status, '未验证');
  assert.equal(nyInstant('2026-03-06', '08:30').toISOString(), '2026-03-06T13:30:00.000Z');
  assert.equal(nyInstant('2026-03-09', '08:30').toISOString(), '2026-03-09T12:30:00.000Z');
  assert.throws(() => nyInstant('2026-03-08', '02:30'));
  assert.throws(() => nyInstant('2026-11-01', '01:30'));
});
test('internal diagnostics stay out but economic revisions remain visible', () => {
  const v = snapshot(); v.daily.codex_analysis.facts = [{ label: '就业修订', text: '就业数据向下修订，需重新评估增长。' }];
  v.daily.codex_analysis.gaps = [{ label: '内部', text: 'partition revision source_scope' }];
  const html = render(validate(v));
  assert.match(html, /就业数据向下修订/); assert.doesNotMatch(html, /partition revision/);
  assert.doesNotMatch(html, /<script|<iframe|onload=|onclick=/i);
});
test('radar presents actual values and percentage changes, without a fabricated strength', () => {
  const v = snapshot();
  v.daily.market.items[0]!.value = 123.45;
  v.daily.market.items[0]!.change_pct = -1.25;
  v.daily.market.items[0]!.direction = 'down';
  v.daily.market.items[0]!.strength = 0;
  const html = render(validate(v));
  assert.match(html, /<span>最新值<\/span><span>涨跌幅<\/span>/);
  assert.match(html, /v2-market-value"><strong>123\.45<\/strong>/);
  assert.match(html, /v2-market-direction[^>]*><strong>−1\.25%<\/strong>/);
  assert.doesNotMatch(html, /v2-meter-dot|aria-label="强度|<span>强度<\/span>/);
});
test('valuation is inline, scoped, formula-checked and shares the Python projection', () => {
  const v = snapshot();
  const row = v.daily.positions_plans.items[0]!;
  row.valuation = { symbol: row.symbol, instrument_type: 'company', as_of: '2026-08-31T10:00:00Z', pe_ttm: '20', roe_pct: '20', roe_period_end: '2025-12-31', roe_period_label: 'FY 2025', roe_basis: 'annual', roe_quality: 'positive_income_equity', pr: '1.00000000', status: 'available', gap: '', source: 'Longbridge' };
  const admitted = validate(v), html = render(admitted);
  assert.match(html, /市赚率 <b>1\.00<\/b>/);
  assert.match(html, /ROE <b>20\.00%<\/b> · FY 2025/);
  assert.equal(normalized(html), normalized(pythonRender(admitted)));
  row.valuation.pr = '100';
  assert.throws(() => validate(v));
  row.valuation.pr = '1.00000000'; row.valuation.symbol = 'UNRELATED.US';
  assert.throws(() => validate(v));
});
test('actual leveraged-ETF symbol, underlying and observation asset stay auditable', () => {
  const v: any = snapshot();
  const row = v.daily.positions_plans.items[0];
  row.symbol = 'NVDL.US'; row.display_name = 'NVDL'; row.valuation = null;
  row.instrument = { tool_kind: 'single_stock_leveraged_etf', underlying: 'NVDA.US' };
  row.execution_context = {
    tool_kind: 'single_stock_leveraged_etf', trade_symbol: 'NVDL.US',
    observation_symbol: 'NVDA.US', observation_timeframe: '1D',
    trigger_timeframe: '1D', trigger_basis: 'bar_close', exception_note: null,
  };
  row.plan_detail = JSON.parse(python("import sys,json; sys.path.insert(0,'tests'); from test_render_trade_review_dashboard_v2 import plan_detail; print(json.dumps(plan_detail()))")) as PlanDetail;
  row.plan_detail.underlying = 'NVDA.US'; row.plan_detail.execution_context = structuredClone(row.execution_context);
  row.plan_detail.evidence.symbol = 'NVDA.US'; row.plan_detail.evidence.period = '1D';
  v.weekly.review_episodes = [{
    market_date: '2026-08-28', trade_symbol: 'NVDL.US', underlying: 'NVDA.US',
    tool_kind: 'single_stock_leveraged_etf', side: 'buy', plan_id: null,
    plan_version: null, observation_timeframe: '1D', trigger_timeframe: '1D',
    trigger_basis: 'bar_close', coverage_status: 'uncovered',
    compliance_status: 'unassessable', outcome_status: 'unverifiable',
    deviation_type: null, reason: '事前计划待确认', next_rule: '先确认计划', data_status: 'partial',
  }];
  Object.assign(v.weekly.execution_metrics, {
    eligible_episode_count: 1, covered_episode_count: 0, assessable_episode_count: 0,
    compliant_episode_count: 0, resolved_episode_count: 0, successful_episode_count: 0,
    open_episode_count: 0, flat_episode_count: 0, unverifiable_episode_count: 1,
    review_needed_count: 1, coverage_rate: 0, execution_rate: null, plan_win_rate: null,
    data_status: 'partial', gap: '计划与周期待确认',
  });
  const admitted = validate(v), html = render(admitted);
  assert.match(html, /实际交易对象：NVDL/);
  assert.match(html, /观察对象：NVDA/);
  assert.equal(normalized(html), normalized(pythonRender(admitted)));
  const wrongCompany = structuredClone(v);
  wrongCompany.daily.positions_plans.items[0].instrument.underlying = 'TSLA.US';
  assert.throws(() => validate(wrongCompany));
});
test('observation-only context stays neutral and plan context wins', () => {
  const v: any = snapshot();
  const row = v.daily.positions_plans.items[0];
  v.daily.positions_plans.items = [row];
  row.instrument = { tool_kind: 'stock', underlying: row.symbol };
  row.execution_context = {
    tool_kind: 'stock', trade_symbol: row.symbol, observation_symbol: row.symbol,
    observation_timeframe: '4H', trigger_timeframe: '4H', trigger_basis: 'bar_close', exception_note: null,
  };
  row.plan_coverage = '不应出现的计划覆盖';
  row.trigger_distance = { label: '不应出现的触发距离', value: '不应出现的距离值', tone: 'red' };
  row.signals = ['不应出现的验证信号']; row.invalidation = ['不应出现的失效条件']; row.next_checks = ['不应出现的计划检查'];
  const admitted = validate(v), html = render(admitted), body = html.split('<body>', 2)[1]!;
  assert.match(body, /观察口径（非交易计划）/);
  assert.match(body, /观察周期：4小时线 · 触发周期：4小时线 · 触发方式：收线确认/);
  assert.match(body, /不生成自动触发/);
  for (const marker of ['不应出现的计划覆盖', '不应出现的触发距离', '不应出现的距离值', '不应出现的验证信号', '不应出现的失效条件', '不应出现的计划检查']) assert.doesNotMatch(body, new RegExp(marker));
  assert.doesNotMatch(body, /class="v2-plan-checks"|v2-near-trigger/);
  assert.equal(normalized(html), normalized(pythonRender(admitted)));

  const planned: any = snapshot(), plannedRow = planned.daily.positions_plans.items[0];
  const context = {
    tool_kind: 'stock', trade_symbol: plannedRow.symbol, observation_symbol: plannedRow.symbol,
    observation_timeframe: '1D', trigger_timeframe: '1D', trigger_basis: 'bar_close', exception_note: null,
  };
  plannedRow.plan_detail = JSON.parse(python("import sys,json; sys.path.insert(0,'tests'); from test_render_trade_review_dashboard_v2 import plan_detail; print(json.dumps(plan_detail()))"));
  Object.assign(plannedRow.plan_detail, { underlying: plannedRow.symbol, execution_context: context });
  Object.assign(plannedRow.plan_detail.evidence, { symbol: plannedRow.symbol, period: '1D' });
  delete plannedRow.execution_context;
  const plannedHtml = render(validate(planned));
  assert.match(plannedHtml, /观察周期：日线 · 触发周期：日线/);
  assert.equal(normalized(plannedHtml), normalized(pythonRender(validate(planned))));
});
