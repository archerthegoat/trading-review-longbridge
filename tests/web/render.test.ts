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
