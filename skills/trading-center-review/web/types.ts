/** Data is admitted by the pinned Python evidence validator, not a type assertion. */
export type Status = 'complete' | 'partial' | 'empty' | 'stale' | 'blocked';
export type Tone = 'neutral' | 'blue' | 'green' | 'amber' | 'red';
export type Direction = 'up' | 'down' | 'flat';
interface Module { status: Status; note?: string; title: string }
interface LabeledText { label: string; text: string }
export interface PlanDetail {
  plan_id: string; version: number; plan_stage: 'pre_entry' | 'position_management';
  underlying?: string;
  plan_status: 'draft' | 'confirmed' | 'expired';
  setup_type: 'pullback' | 'breakout' | 'range' | 'bottom_reversal' | 'position_management';
  parent_plan_id: string | null; parent_plan_version: number | null; initial_buy_episode_key: string | null;
  execution_context?: ExecutionContext;
  quote_relation: 'below' | 'inside' | 'above' | 'stale' | 'unavailable';
  evidence: { evidence_id: string; source: 'Longbridge'; as_of: string; timezone: 'America/New_York'; adjustment: string; bars_used: number; atr14: string; symbol?: string; period?: '1D' };
  zones: { kind: 'observation' | 'entry' | 'add' | 'reduce' | 'exit' | 'invalidation'; low: string; high: string; currency: string; condition: string; derived_from: string; data_status: Status }[];
}
export interface Instrument { tool_kind: 'stock' | 'single_stock_leveraged_etf' | 'leap_call' | 'unknown'; underlying: string }
export interface ExecutionContext { tool_kind: 'stock' | 'single_stock_leveraged_etf' | 'leap_call'; trade_symbol: string; observation_symbol: string | null; observation_timeframe: '1H' | '4H' | '1D' | '1W' | null; trigger_timeframe: '1H' | '4H' | '1D' | '1W' | null; trigger_basis: 'bar_close' | 'intrabar_touch' | 'unconfirmed'; exception_note: string | null }
export interface Position {
  symbol: string; display_name: string; tab: 'holdings' | 'plan'; role: string; holding_state: string;
  plan_coverage: string; trigger_distance: { label: string; value: string; tone: Tone };
  near_trigger: boolean; signals: string[]; invalidation: string[]; next_checks: string[];
  has_gap: boolean; gap: string; boundary: string; data_status: Status;
  plan_detail?: PlanDetail | null; strategy_category?: string | null; valuation?: Valuation | null;
  instrument?: Instrument; execution_context?: ExecutionContext;
}
export interface Valuation {
  symbol: string; instrument_type: 'company' | 'fund'; as_of: string;
  pe_ttm: string | null; roe_pct: string | null; roe_period_end: string | null;
  roe_period_label: string | null; roe_basis: 'annual' | null; pr: string | null;
  roe_quality: 'positive_income_equity' | 'nonpositive' | 'unverified' | 'not_applicable';
  status: 'available' | 'unavailable' | 'not_applicable' | 'stale'; gap: string; source: 'Longbridge';
}
export interface CalendarEvent {
  et_date: string; et_time: string; shanghai_time: string; title: string;
  status: '已发生' | '预期' | '未公布' | '未验证' | '已取消'; source: string;
  data_status: Status; object: string; impact_channel: string;
  watch_for?: string; kind?: 'macro' | 'earnings' | 'fed_speech'; speaker?: string; source_url?: string;
}
export interface Daily {
  meta: { review_label: string; review_date: string; generated_at: string; market_as_of: string;
    previous_trading_window: { label: string; market_date: string; ny_start: string; ny_end: string; utc_start: string; utc_end: string };
    period_label: string; overall_status: Status };
  market: Module & { source_scope?: string; basis?: 'completed_close'; market_date?: string;
    environment?: { status: 'complete' | 'partial'; headline: string; pricing_signals: LabeledText[];
      cross_asset_confirmation: string; next_session_watch: string };
    items: {
    name: string; symbol: string; is_proxy: boolean; proxy_for: string | null;
    value: number | null; change_pct: number | null; direction: Direction; strength: number;
    state: string; session: string; as_of: string; risk_note: string; data_status: Status; unavailable_reason?: string;
    capital_flow?: { label: string; direction: Direction; value: number | null; as_of: string; data_status: Status }
  }[] };
  codex_analysis: Module & { headline: string; facts: LabeledText[]; interpretation: LabeledText[]; risks: LabeledText[]; gaps: LabeledText[];
    checks: { if: string; then: string; else: string; evidence_refs: string[]; boundary: string }[] };
  operations: Module & { window_label: string; market_scope?: 'US';
    executions: { count: number | null; data_status: Status; note: string };
    items: { symbol: string; display_name: string; action: string; role: string; state: string; plan_relation: string; data_status: Status; execution_count?: number | null }[] };
  positions_plans: Module & { items: Position[]; strategy_categories?: string[] };
  events: Module & { display_timezone: string; reference_at?: string; coverage?: { label: string; status: Status; note: string }[];
    groups: { date: string; label: string; range: string; events: CalendarEvent[] }[] };
}
export interface Metrics {
  eligible_episode_count: number; covered_episode_count: number; assessable_episode_count: number;
  compliant_episode_count: number; resolved_episode_count: number; successful_episode_count: number;
  open_episode_count: number; flat_episode_count: number; unverifiable_episode_count: number;
  review_needed_count: number; coverage_rate: number | null; execution_rate: number | null;
  plan_win_rate: number | null; data_status: Status; gap: string | null;
}
export type WeeklySection = 'market_radar' | 'judgement' | 'operations' | 'positions_plan' | 'plan_review' | 'next_week' | 'events' | 'data_note';
export interface WeeklyItem { label: string; summary: string; boundary: string; evidence_kind: string; item_kind: string; data_status: Status }
export interface Weekly {
  schema_version: 'trading-review-weekly-dashboard.v2';
  meta: { review_label: string; period_start: string; period_end: string; generated_at: string;
    overall_status: Status; freshness: 'current' | 'stale'; confirmation_status: 'pending' | 'confirmed'; market_scope?: 'US' };
  execution_metrics: Metrics;
  review_episodes: { market_date: string; underlying: string; side: string; plan_id: string | null; plan_version: number | null;
    coverage_status: 'covered' | 'uncovered'; compliance_status: 'compliant' | 'non_compliant' | 'unassessable';
    outcome_status: 'success' | 'failure' | 'open' | 'flat' | 'unverifiable'; deviation_type: string | null;
    reason: string; next_rule: string; data_status: Status; trade_symbol?: string; tool_kind?: Instrument['tool_kind'];
    observation_timeframe?: ExecutionContext['observation_timeframe']; trigger_timeframe?: ExecutionContext['trigger_timeframe']; trigger_basis?: ExecutionContext['trigger_basis'] }[];
  sections: Record<WeeklySection, WeeklyItem[]>;
}
export interface DisplaySnapshot { schema_version: 'trading-review-display.v1'; daily: Daily; weekly: Weekly | null }
