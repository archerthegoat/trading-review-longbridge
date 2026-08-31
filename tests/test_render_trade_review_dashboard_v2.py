from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "skills" / "trading-center-review" / "scripts" / "render_trade_review_dashboard_v2.py"
TEMPLATE_PATH = ROOT / "skills" / "trading-center-review" / "assets" / "trade-review-dashboard-v2-standalone.html"
FIXTURES = ROOT / "tests" / "fixtures"

SPEC = importlib.util.spec_from_file_location("render_trade_review_dashboard_v2", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load V2 renderer")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture(name: str):
    return json.loads((FIXTURES / f"dashboard_v2_{name}.json").read_text(encoding="utf-8"))


def weekly_packet():
    base_item = {
        "label": "示例",
        "summary": "已脱敏的周度事实",
        "boundary": "仅限固定周度窗口",
        "evidence_kind": "fact",
        "item_kind": "risk",
        "data_status": "complete",
    }
    sections = {name: [] for name in MODULE.WEEKLY_SECTION_NAMES}
    sections["market_radar"] = [{**base_item, "data_status": "partial"}]
    sections["judgement"] = [base_item]
    sections["operations"] = [{**base_item, "item_kind": "plan_actual"}]
    sections["positions_plan"] = [{**base_item, "item_kind": "plan_actual"}]
    sections["plan_review"] = [
        {
            **base_item,
            "summary": "计划权威缺失",
            "evidence_kind": "gap",
            "item_kind": "gap",
            "data_status": "blocked",
        }
    ]
    sections["next_week"] = [
        {
            **base_item,
            "summary": "等待确认计划后再生成",
            "evidence_kind": "draft",
            "item_kind": "add",
            "data_status": "blocked",
        }
    ]
    sections["events"] = [base_item]
    sections["data_note"] = [
        {**base_item, "evidence_kind": "gap", "item_kind": "gap", "data_status": "partial"}
    ]
    return {
        "schema_version": MODULE.WEEKLY_SCHEMA_VERSION,
        "meta": {
            "review_label": "周度复盘",
            "period_start": "2026-08-24",
            "period_end": "2026-08-28",
            "generated_at": "2026-08-30T08:00:00+08:00",
            "overall_status": "partial",
            "freshness": "current",
            "confirmation_status": "pending",
            "market_scope": "US",
        },
        "execution_metrics": {
            **{key: 0 for key in MODULE.WEEKLY_METRIC_COUNT_FIELDS},
            **{key: None for key in MODULE.WEEKLY_METRIC_RATE_FIELDS},
            "data_status": "blocked",
            "gap": "缺少事前计划或执行证据",
        },
        "review_episodes": [],
        "sections": sections,
    }


def plan_detail(*, stage="pre_entry", status="draft", setup="bottom_reversal", actionable=False):
    zones = [
        {"kind": "observation", "low": "95", "high": "97", "currency": "USD", "condition": "进入后观察止跌", "derived_from": "ema20_atr14", "data_status": "complete"},
        {"kind": "invalidation", "low": "91", "high": "92", "currency": "USD", "condition": "结构收盘失效", "derived_from": "swing_low_minus_atr", "data_status": "complete"},
    ]
    if actionable:
        zones.append({"kind": "add" if stage == "position_management" else "entry", "low": "98", "high": "100", "currency": "USD", "condition": "右侧确认后才有效", "derived_from": "ema20_reclaim", "data_status": "complete"})
        zones.append({"kind": "reduce", "low": "111", "high": "112", "currency": "USD", "condition": "到达目标区评估减仓", "derived_from": "confirmed_swing_high", "data_status": "complete"})
    return {
        "plan_id": "demo-plan",
        "version": 1,
        "plan_stage": stage,
        "plan_status": status,
        "setup_type": setup,
        "evidence": {
            "evidence_id": "a" * 64,
            "source": "Longbridge", "as_of": "2026-08-28",
            "timezone": "America/New_York", "adjustment": "forward",
            "bars_used": 319, "atr14": "3.5",
        },
        "zones": zones,
        "parent_plan_id": "parent-demo" if stage == "position_management" else None,
        "parent_plan_version": 1 if stage == "position_management" else None,
        "initial_buy_episode_key": "2026-08-27|DEMO.US|buy" if stage == "position_management" else None,
        "quote_relation": "unavailable",
    }


def event_calendar(reference, *instants):
    packet = fixture("complete")
    groups = {}
    for index, text in enumerate(instants):
        instant = dt.datetime.fromisoformat(text).astimezone(MODULE.NY_TZ)
        shanghai = instant.astimezone(MODULE.SHANGHAI_TZ)
        day = shanghai.date().isoformat()
        group = groups.setdefault(day, {"date": day, "label": "合成事件", "range": "合成日期", "events": []})
        group["events"].append({
            "shanghai_time": shanghai.strftime("%H:%M"),
            "et_date": instant.date().isoformat(), "et_time": instant.strftime("%H:%M"),
            "title": f"合成事件{index}", "status": "预期", "source": "Longbridge",
            "data_status": "complete", "impact_channel": "增长预期影响估值", "object": "指数与科技股",
        })
    packet["events"].update(reference_at=reference, groups=list(groups.values()))
    return packet


class DashboardV2RendererTests(unittest.TestCase):
    def render(self, packet):
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        return MODULE.render_dashboard(packet, template)

    def test_non_blocked_fixture_set_validates_and_renders(self):
        for name in ("complete", "partial", "empty", "stale"):
            with self.subTest(name=name):
                packet = fixture(name)
                MODULE.validate_packet(packet)
                rendered = self.render(packet)
                self.assertNotIn("data-schema-version", rendered)
                self.assertNotIn("盘前复盘 V2", rendered)
                self.assertNotIn("<script", rendered.lower())
                self.assertNotIn("<iframe", rendered.lower())

    def test_blocked_fixture_is_rejected_without_success_render(self):
        packet = fixture("blocked")
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "blocked"):
            MODULE.validate_packet(packet)

    def test_complete_overall_status_cannot_hide_partial_module(self):
        packet = fixture("complete")
        packet["meta"]["overall_status"] = "complete"
        packet["account"]["status"] = "partial"
        packet["account"]["note"] = "账户模块状态需与总体状态对照"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "conflicts"):
            MODULE.validate_packet(packet)

    def test_page_order_matches_option_two(self):
        rendered = self.render(fixture("complete"))
        labels = [
            "市场风险雷达",
            "Codex 盘前判断",
            "上一交易日成交",
            "持仓 × 计划",
            "重要事件与时间轴",
            "更新与使用说明",
        ]
        positions = [rendered.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("v2-top-grid", rendered)
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: minmax(380px, 42fr) minmax(0, 58fr)", template)
        self.assertNotIn("v2-bottom-grid", rendered)
        self.assertIn(".v2-plans, .v2-events { border-bottom:", template)

    def test_native_controls_cover_tabs_filters_and_details(self):
        rendered = self.render(fixture("complete"))
        for control_id in (
            "v2-view-holdings",
            "v2-view-plan",
            "v2-filter-near",
            "v2-filter-gap",
        ):
            self.assertIn(f'id="{control_id}"', rendered)
        self.assertIn("<details class=\"v2-data-note\">", rendered)
        self.assertIn('role="group" aria-label="持仓和计划视图"', rendered)
        self.assertIn('role="group" aria-label="持仓和计划筛选"', rendered)
        self.assertIn('id="v2-plan-panel"', rendered)
        self.assertIn('aria-controls="v2-plan-panel"', rendered)
        self.assertNotIn('id="v2-show-money"', rendered)
        self.assertNotIn('aria-controls="v2-account-panel"', rendered)
        self.assertNotIn("aria-selected", rendered)
        self.assertNotIn("<script", rendered.lower())

    def test_keyboard_controls_are_anchored_inside_the_visible_plan_section(self):
        rendered = self.render(fixture("complete"))
        body = rendered.split("<body>", 1)[1]
        plan_start = body.index('<section class="v2-plans"')
        first_control = body.index('<input class="v2-state"')
        self.assertGreater(first_control, plan_start)
        self.assertIn('.v2-plan-controls label:has(input:focus-visible)', rendered)

    def test_account_facts_remain_validated_but_are_not_rendered(self):
        packet = fixture("complete")
        packet["meta"]["account_label"] = "仅私有账户标签"
        packet["account"]["metrics"].append(
            {
                "label": "仅私有账户字段",
                "value": "不应进入主界面",
                "kind": "text",
                "data_status": "complete",
            }
        )
        rendered = self.render(packet)
        MODULE.validate_packet(packet)
        for marker in (
            "账户概览",
            "仅私有账户标签",
            "仅私有账户字段",
            "不应进入主界面",
            packet["account"]["base_currency"],
            packet["account"]["snapshot_at"],
        ):
            self.assertNotIn(marker, rendered)

    def test_empty_and_stale_states_remain_distinguishable(self):
        empty = self.render(fixture("empty"))
        stale = self.render(fixture("stale"))
        self.assertIn("暂无数据", empty)
        self.assertIn("暂无已收录事件", empty)
        self.assertIn("数据陈旧", stale)
        self.assertIn("陈旧快照", stale)

    def test_non_complete_market_direction_is_neutral(self):
        partial = self.render(fixture("partial"))
        stale = self.render(fixture("stale"))
        self.assertIn("v2-market-direction v2-direction-flat", partial)
        self.assertIn("v2-market-direction v2-direction-flat", stale)
        self.assertNotIn("v2-market-direction v2-direction-down", partial)
        self.assertNotIn("v2-market-direction v2-direction-down", stale)
        self.assertNotIn("v2-market-direction v2-direction-up", stale)

    def test_non_complete_plan_trigger_tone_is_amber(self):
        packet = fixture("partial")
        packet["positions_plans"]["items"][0]["trigger_distance"]["tone"] = "red"
        rendered = self.render(packet)
        self.assertIn('class="v2-trigger v2-tone-amber"', rendered)
        self.assertNotIn('class="v2-trigger v2-tone-red"', rendered)

    def test_proxy_semantics_are_required_and_visible(self):
        packet = fixture("complete")
        MODULE.validate_packet(packet)
        rendered = self.render(packet)
        self.assertIn("代理：标普 500", rendered)
        self.assertIn("DEMO.BTC · 连续交易", rendered)

        mislabeled = fixture("complete")
        mislabeled["market"]["items"][0]["symbol"] = "SPY.US"
        mislabeled["market"]["items"][0]["is_proxy"] = False
        mislabeled["market"]["items"][0]["proxy_for"] = None
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "known proxy"):
            MODULE.validate_packet(mislabeled)

        undeclared = fixture("complete")
        undeclared["market"]["items"][0]["is_proxy"] = True
        undeclared["market"]["items"][0]["proxy_for"] = ""
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "proxy_for"):
            MODULE.validate_packet(undeclared)

    def test_unknown_top_level_field_is_rejected(self):
        packet = fixture("complete")
        packet["unexpected_field"] = "x"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "unsupported field"):
            MODULE.validate_packet(packet)

    def test_unknown_nested_field_is_rejected(self):
        packet = fixture("complete")
        packet["market"]["items"][0]["unexpected_field"] = "x"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "unsupported field"):
            MODULE.validate_packet(packet)

    def test_empty_market_and_events_cannot_contain_children(self):
        market_packet = fixture("empty")
        market_packet["market"]["items"].append(
            copy.deepcopy(fixture("partial")["market"]["items"][0])
        )
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty status"):
            MODULE.validate_packet(market_packet)

        events_packet = fixture("empty")
        events_packet["events"]["groups"].append(
            copy.deepcopy(fixture("partial")["events"]["groups"][0])
        )
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty status"):
            MODULE.validate_packet(events_packet)

    def test_partial_or_stale_parent_requires_explainable_child_state(self):
        for name in ("partial", "stale"):
            packet = fixture(name)
            for row in packet["market"]["items"]:
                row["data_status"] = "complete"
                if row["value"] is None:
                    row["value"] = 1.0
                if row["change_pct"] is None:
                    row["change_pct"] = 0.0
            with self.subTest(name=name), self.assertRaisesRegex(
                MODULE.DashboardRenderError, "requires a note"
            ):
                MODULE.validate_packet(packet)

    def test_blocked_child_cannot_be_hidden_by_parent(self):
        packet = fixture("complete")
        packet["market"]["items"][0]["data_status"] = "blocked"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "blocked child"):
            MODULE.validate_packet(packet)

    def test_invalid_calendar_or_event_time_is_rejected(self):
        invalid_date = fixture("complete")
        invalid_date["meta"]["review_date"] = "2026-02-30"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "real calendar date"):
            MODULE.validate_packet(invalid_date)

        invalid_time = fixture("complete")
        invalid_time["events"]["groups"][0]["events"][0]["et_time"] = "25:90"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "HH:MM"):
            MODULE.validate_packet(invalid_time)

    def test_window_requires_strict_rfc3339_zone_and_matching_instants(self):
        space_separated = fixture("complete")
        space_separated["meta"]["previous_trading_window"]["ny_start"] = "2026-08-28 00:00:00-04:00"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "T and timezone"):
            MODULE.validate_packet(space_separated)

        wrong_ny_offset = fixture("complete")
        wrong_ny_offset["meta"]["previous_trading_window"]["ny_start"] = "2026-08-28T00:00:00+09:00"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "America/New_York"):
            MODULE.validate_packet(wrong_ny_offset)

        mismatched_utc = fixture("complete")
        mismatched_utc["meta"]["previous_trading_window"]["utc_start"] = "2026-08-28T05:00:00Z"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "same instant"):
            MODULE.validate_packet(mismatched_utc)

    def test_daily_window_requires_weekday_exact_ny_midnight_and_shanghai_generated_at(self):
        weekend = fixture("complete")
        weekend["meta"]["review_date"] = "2026-08-29"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "Monday-Friday"):
            MODULE.validate_packet(weekend)

        not_midnight = fixture("complete")
        not_midnight["meta"]["previous_trading_window"]["ny_start"] = "2026-08-28T01:00:00-04:00"
        not_midnight["meta"]["previous_trading_window"]["utc_start"] = "2026-08-28T05:00:00Z"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "exact NY midnight"):
            MODULE.validate_packet(not_midnight)

        utc_generated_at = fixture("complete")
        utc_generated_at["meta"]["generated_at"] = "2026-08-29T00:45:12Z"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "Asia/Shanghai"):
            MODULE.validate_packet(utc_generated_at)

    def test_daily_window_validates_winter_new_york_dst_offset(self):
        packet = fixture("complete")
        packet["meta"]["review_date"] = "2026-01-09"
        packet["meta"]["generated_at"] = "2026-01-10T08:45:12+08:00"
        window = packet["meta"]["previous_trading_window"]
        window["market_date"] = "2026-01-09"
        window["ny_start"] = "2026-01-09T00:00:00-05:00"
        window["ny_end"] = "2026-01-10T00:00:00-05:00"
        window["utc_start"] = "2026-01-09T05:00:00Z"
        window["utc_end"] = "2026-01-10T05:00:00Z"
        MODULE.validate_packet(packet)

    def test_event_timezones_must_describe_one_instant(self):
        packet = fixture("complete")
        packet["events"]["groups"][0]["events"][0]["et_date"] = "2026-08-28"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "same instant"):
            MODULE.validate_packet(packet)

    def test_sensitive_field_name_is_rejected(self):
        packet = fixture("complete")
        packet["account"]["account_id"] = "synthetic"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "forbidden field"):
            MODULE.validate_packet(packet)

    def test_sensitive_value_is_rejected(self):
        packet = fixture("complete")
        packet["codex_analysis"]["headline"] = "Bearer synthetic-token-value"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "forbidden sensitive value"):
            MODULE.validate_packet(packet)

    def test_sensitive_value_variants_are_rejected_in_allowed_fields(self):
        variants = (
            "ACCESS TOKEN = 'synthetic'",
            '"access_token" = "synthetic"',
            'Refresh Token: "synthetic"',
            "CLIENT SECRET = 'synthetic'",
            "Authorization=synthetic",
            "Authorization：synthetic",
            "Authorization: Bearer synthetic-token",
            "Bearer synthetic-token",
            "Bearer token",
            "API KEY: 'synthetic'",
            '"api key": "synthetic"',
            "key='synthetic'",
            "access_token",
            "refresh_token",
            "client_secret",
            "api_key",
            "账户编号：synthetic",
            "账户标识＝synthetic",
            "订单ID: synthetic",
            "成交ID＝synthetic",
            "凭据：synthetic",
        )
        for value in variants:
            packet = fixture("complete")
            packet["data_note"]["items"][0]["value"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                MODULE.DashboardRenderError, "forbidden sensitive value"
            ):
                MODULE.validate_packet(packet)

    def test_internal_state_injection_is_rejected_but_codex_label_is_allowed(self):
        provider_name = "Deep" + "Seek"
        variants = (
            provider_name + " 摘要状态",
            "AGENTS.md 缺失",
            "CONTEXT.md 缺失",
            "内部 reviewer status",
            "人工浏览器验收未运行",
            "V2 调试信息",
            "reviewer",
            "tool",
            "agent",
            "schema",
            "V2",
            "AGENTS",
            "CONTEXT",
        )
        for value in variants:
            packet = fixture("complete")
            packet["data_note"]["items"][0]["value"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                MODULE.DashboardRenderError, "internal UI state"
            ):
                MODULE.validate_packet(packet)
        allowed = fixture("complete")
        allowed["data_note"]["items"][0]["value"] = "Codex 结构化判断已生成"
        MODULE.validate_packet(allowed)

    def test_empty_child_cannot_carry_factual_values_but_partial_can_retain_verified_rows(self):
        market = fixture("complete")
        market["meta"]["overall_status"] = "partial"
        market["market"]["status"] = "partial"
        empty_row = market["market"]["items"][0]
        empty_row["data_status"] = "empty"
        empty_row["value"] = None
        empty_row["change_pct"] = None
        empty_row["direction"] = "flat"
        empty_row["strength"] = 0
        empty_row["state"] = "暂无数据"
        empty_row["session"] = "未知"
        empty_row["risk_note"] = "不可用"
        MODULE.validate_packet(market)

        factual_market = fixture("complete")
        factual_market["market"]["items"][0]["data_status"] = "empty"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty data"):
            MODULE.validate_packet(factual_market)

        factual_account = fixture("complete")
        factual_account["account"]["metrics"][0]["data_status"] = "empty"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty data"):
            MODULE.validate_packet(factual_account)

        factual_orders = fixture("complete")
        factual_orders["operations"]["orders"]["data_status"] = "empty"
        factual_orders["operations"]["orders"]["count"] = 1
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty data"):
            MODULE.validate_packet(factual_orders)

        factual_operation_item = fixture("complete")
        factual_operation_item["operations"]["items"][0]["data_status"] = "empty"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty data"):
            MODULE.validate_packet(factual_operation_item)

        factual_position = fixture("complete")
        factual_position["positions_plans"]["items"][0]["data_status"] = "empty"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty position item"):
            MODULE.validate_packet(factual_position)

        factual_event = fixture("complete")
        factual_event["events"]["groups"][0]["events"][0]["data_status"] = "empty"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "empty event item"):
            MODULE.validate_packet(factual_event)

    def test_empty_parent_modules_cannot_contain_child_items(self):
        sources = {
            "market": ("items", fixture("complete")["market"]["items"][:1]),
            "account": ("metrics", fixture("complete")["account"]["metrics"][:1]),
            "operations": ("items", fixture("complete")["operations"]["items"][:1]),
            "positions_plans": ("items", fixture("complete")["positions_plans"]["items"][:1]),
        }
        for module_name, (child_key, child_rows) in sources.items():
            packet = fixture("empty")
            packet[module_name][child_key] = copy.deepcopy(child_rows)
            with self.subTest(module=module_name), self.assertRaisesRegex(
                MODULE.DashboardRenderError, "empty status"
            ):
                MODULE.validate_packet(packet)

        non_empty_order_counts = fixture("empty")
        non_empty_order_counts["operations"]["orders"]["data_status"] = "complete"
        non_empty_order_counts["operations"]["orders"]["count"] = 1
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "factual children"):
            MODULE.validate_packet(non_empty_order_counts)

    def test_position_symbol_is_required_and_non_empty(self):
        packet = fixture("complete")
        packet["positions_plans"]["items"][0]["symbol"] = ""
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "symbol must not be empty"):
            MODULE.validate_packet(packet)

    def test_plan_detail_uses_ema_and_pre_entry_has_no_add_zone(self):
        packet = fixture("complete")
        packet["positions_plans"]["items"][0]["plan_detail"] = plan_detail(
            setup="pullback", status="confirmed", actionable=True
        )
        rendered = self.render(packet)
        self.assertIn("EMA20/50/200", rendered)
        self.assertIn('建仓区间', rendered)
        self.assertNotIn('data-zone-kind="add"', rendered)
        self.assertNotIn("SMA", rendered)
        packet["positions_plans"]["items"][0]["plan_detail"]["zones"][-1]["kind"] = "add"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "pre_entry"):
            MODULE.validate_packet(packet)

    def test_bottom_reversal_without_confirmation_is_observation_only(self):
        packet = fixture("complete")
        packet["positions_plans"]["items"][0]["plan_detail"] = plan_detail()
        rendered = self.render(packet)
        self.assertIn("抄底反转（右侧确认）", rendered)
        self.assertIn("仅观察：确认条件未齐", rendered)
        self.assertNotIn('data-zone-kind="entry"', rendered)

    def test_position_management_add_is_a_separately_confirmed_draft(self):
        packet = fixture("complete")
        detail = plan_detail(
            stage="position_management", setup="position_management", actionable=True
        )
        packet["positions_plans"]["items"][0]["plan_detail"] = detail
        rendered = self.render(packet)
        self.assertIn("加仓区间 · 待单独确认", rendered)
        detail["initial_buy_episode_key"] = None
        with self.assertRaises(MODULE.DashboardRenderError):
            MODULE.validate_packet(packet)

    def test_quote_relation_does_not_move_frozen_plan_zones(self):
        packet = fixture("complete")
        detail = plan_detail(setup="pullback", status="confirmed", actionable=True)
        packet["positions_plans"]["items"][0]["plan_detail"] = detail
        before = self.render(packet)
        detail["quote_relation"] = "stale"
        after = self.render(packet)
        for marker in ("95.00–97.00", "91.00–92.00", "98.00–100.00"):
            self.assertIn(marker, before)
            self.assertIn(marker, after)
        self.assertIn("报价陈旧，区间保持不变", after)

    def test_untrusted_text_is_escaped(self):
        packet = fixture("complete")
        packet["codex_analysis"]["headline"] = '<b data-x="1">演示</b>'
        rendered = self.render(packet)
        self.assertNotIn('<b data-x="1">演示</b>', rendered)
        self.assertIn("&lt;b data-x=&quot;1&quot;&gt;演示&lt;/b&gt;", rendered)

    def test_template_is_static_and_offline(self):
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertEqual(template.count(MODULE.BODY_MARKER), 1)
        self.assertIn("<!doctype html>", template.lower())
        for marker in (
            "<script",
            "<iframe",
            "srcdoc",
            "document.write",
            "eval(",
            "fetch(",
            "https://",
            "http://",
        ):
            self.assertNotIn(marker, template.lower())
        self.assertIn("Content-Security-Policy", template)

    def test_cli_rejects_custom_template_even_if_it_has_a_body_marker(self):
        private_root = MODULE.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="v2-template-test-") as directory:
            template_path = Path(directory) / "replacement.html"
            template_path.write_text(
                "<!doctype html><style>body{}</style>" + MODULE.BODY_MARKER,
                encoding="utf-8",
            )
            with tempfile.TemporaryDirectory(prefix="v2-template-input-", dir=str(private_root)) as private_directory:
                private_path = Path(private_directory)
                input_path = private_path / "input.json"
                output_path = private_path / "output.html"
                input_path.write_text(json.dumps(fixture("complete"), ensure_ascii=False), encoding="utf-8")
                input_path.chmod(0o600)
                exit_code = MODULE.main(
                    [
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--template",
                        str(template_path),
                    ]
                )
                self.assertEqual(exit_code, 2)
                self.assertFalse(output_path.exists())

    def test_cli_writes_private_owner_only_html(self):
        private_root = MODULE.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="v2-render-test-", dir=str(private_root)) as directory:
            directory_path = Path(directory)
            input_path = directory_path / "input.json"
            output_path = directory_path / "output.html"
            input_path.write_text(json.dumps(fixture("complete"), ensure_ascii=False), encoding="utf-8")
            input_path.chmod(0o600)
            exit_code = MODULE.main(["--input", str(input_path), "--output", str(output_path)])
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertIn("市场风险雷达", output_path.read_text(encoding="utf-8"))

    def test_cli_rejects_input_inside_git_worktree(self):
        private_root = MODULE.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="v2-path-test-", dir=str(private_root)) as directory:
            output_path = Path(directory) / "output.html"
            exit_code = MODULE.main(
                ["--input", str(FIXTURES / "dashboard_v2_complete.json"), "--output", str(output_path)]
            )
            self.assertEqual(exit_code, 2)
            self.assertFalse(output_path.exists())

    def test_cli_rejects_non_owner_only_private_input(self):
        private_root = MODULE.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="v2-permission-test-", dir=str(private_root)) as directory:
            directory_path = Path(directory)
            input_path = directory_path / "input.json"
            output_path = directory_path / "output.html"
            input_path.write_text(json.dumps(fixture("complete"), ensure_ascii=False), encoding="utf-8")
            input_path.chmod(0o644)
            exit_code = MODULE.main(["--input", str(input_path), "--output", str(output_path)])
            self.assertEqual(exit_code, 2)
            self.assertFalse(output_path.exists())

    def test_fixture_symbols_are_synthetic_and_no_sensitive_keys_are_present(self):
        for name in ("complete", "partial", "empty", "stale", "blocked"):
            packet = fixture(name)
            serialized = json.dumps(packet, ensure_ascii=False).lower()
            self.assertNotIn("account_id", serialized)
            self.assertNotIn("order_id", serialized)
            self.assertNotIn("execution_id", serialized)
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("bearer ", serialized)
            self.assertTrue(
                all(
                    not str(row.get("symbol", "")).startswith(("NASDAQ", "NYSE", "US."))
                    for row in packet["market"]["items"]
                )
            )

    def test_validation_does_not_mutate_input_packet(self):
        packet = fixture("complete")
        before = copy.deepcopy(packet)
        MODULE.validate_packet(packet)
        self.assertEqual(packet, before)

    def test_us_only_holdings_and_unheld_plans_do_not_mutate_private_input(self):
        packet = fixture("complete")
        held = packet["positions_plans"]["items"][0]
        duplicate = {**copy.deepcopy(held), "tab": "plan"}
        other_market = {**copy.deepcopy(held), "symbol": "DEMO.HK", "display_name": "其他市场占位"}
        unknown_market = {**copy.deepcopy(held), "symbol": "UNKNOWN", "display_name": "未知市场占位"}
        packet["positions_plans"]["items"] += [duplicate, other_market, unknown_market]
        before = copy.deepcopy(packet)
        rendered = self.render(packet)
        self.assertEqual(len(MODULE._display_position_rows(packet["positions_plans"])), 3)
        self.assertNotIn("其他市场占位", rendered)
        self.assertNotIn("未知市场占位", rendered)
        self.assertNotRegex(rendered, r"\.US\b")
        self.assertEqual(packet, before)

    def test_different_real_plan_cannot_be_silently_dropped_as_duplicate(self):
        packet = fixture("complete")
        duplicate = {**copy.deepcopy(packet["positions_plans"]["items"][0]), "tab": "plan", "plan_detail": plan_detail()}
        packet["positions_plans"]["items"].append(duplicate)
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "explicit holding assignment"):
            self.render(packet)

    def test_position_management_cannot_be_an_unheld_buy_plan(self):
        packet = fixture("complete")
        packet["positions_plans"]["items"][2]["plan_detail"] = plan_detail(stage="position_management", setup="position_management")
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "belongs to holdings"):
            self.render(packet)

    def test_five_confirmed_strategy_labels_are_preserved_not_inferred_from_setup(self):
        packet = fixture("complete")
        labels = [f"合成分类{index}" for index in range(1, 6)]
        packet["positions_plans"]["strategy_categories"] = labels
        packet["positions_plans"]["items"][2]["strategy_category"] = labels[2]
        rendered = self.render(packet)
        self.assertEqual(rendered.count('<section class="v2-strategy-group">'), 5)
        for label in labels:
            self.assertIn(label, rendered)
        self.assertNotIn("原五类策略名称待确认", rendered)
        packet["positions_plans"]["items"][2]["strategy_category"] = "未经确认的分类"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "confirmed five"):
            self.render(packet)

    def test_missing_categories_and_empty_buy_candidates_are_explicit(self):
        packet = fixture("complete")
        packet["positions_plans"]["items"] = [row for row in packet["positions_plans"]["items"] if row["tab"] == "holdings"]
        rendered = self.render(packet)
        self.assertIn("原五类策略名称待确认", rendered)
        self.assertIn("暂无已核验的未持仓买入候选", rendered)
        self.assertNotIn('data-tab="plan"', rendered)
        self.assertIn("没有符合当前筛选条件的标的", rendered)

    def test_mixed_market_counts_are_not_displayed_as_us_counts(self):
        packet = fixture("complete")
        packet["operations"].pop("market_scope")
        packet["operations"]["orders"]["count"] = 123456
        packet["operations"]["executions"]["count"] = 654321
        packet["operations"]["items"][0]["symbol"] = "DEMO.HK"
        packet["operations"]["items"][0]["display_name"] = "非美股操作占位"
        rendered = self.render(packet)
        self.assertNotIn("123456", rendered)
        self.assertNotIn("654321", rendered)
        self.assertNotIn("非美股操作占位", rendered)
        packet["operations"]["market_scope"] = "US"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "US operation scope"):
            self.render(packet)

    def test_operations_only_show_explicit_fills_not_submissions_or_action_claims(self):
        packet = fixture("complete")
        filled = packet["operations"]["items"][0]
        filled["display_name"] = "已成交占位"
        submitted = {**copy.deepcopy(filled), "display_name": "未成交委托占位", "execution_count": 0, "action": "提交订单"}
        legacy = {**copy.deepcopy(filled), "display_name": "仅文案声称成交占位", "action": "已成交"}
        legacy.pop("execution_count")
        packet["operations"]["items"] += [submitted, legacy]
        packet["operations"]["orders"]["count"] = 987654
        before = copy.deepcopy(packet)
        rendered = self.render(packet)
        self.assertIn("已成交占位", rendered)
        for marker in ("未成交委托占位", "仅文案声称成交占位", "提交订单", "987654", "订单 <strong>"):
            self.assertNotIn(marker, rendered)
        self.assertEqual(packet, before)

    def test_zero_fills_and_missing_fill_details_are_not_conflated(self):
        packet = fixture("complete")
        packet["operations"]["executions"]["count"] = 0
        packet["operations"]["items"][0]["execution_count"] = 0
        self.assertIn("上一交易日无已成交记录", self.render(packet))
        packet["operations"]["executions"]["count"] = 1
        packet["operations"]["items"][0].pop("execution_count")
        rendered = self.render(packet)
        self.assertIn("成交明细尚待核对", rendered)
        self.assertNotIn("上一交易日无已成交记录", rendered)
        packet["operations"]["items"][0]["execution_count"] = 1
        packet["operations"]["executions"]["count"] = 2
        self.assertIn("另有成交明细尚待核对", self.render(packet))

    def test_execution_count_is_validated_and_cannot_exceed_verified_total(self):
        for invalid in (-1, True, "1", 1.5):
            packet = fixture("complete")
            packet["operations"]["items"][0]["execution_count"] = invalid
            with self.subTest(value=invalid), self.assertRaises(MODULE.DashboardRenderError):
                self.render(packet)
        packet = fixture("complete")
        packet["operations"]["items"][0]["execution_count"] = 2
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "confirmed fills exceed"):
            self.render(packet)

    def test_private_diagnostics_do_not_enter_collapsed_html(self):
        packet = fixture("complete")
        packet["data_note"]["items"][0]["value"] = "仅审计可见占位"
        packet["operations"]["reconciliation"] = "机械勾稽秘密占位"
        packet["positions_plans"]["items"][0]["plan_detail"] = plan_detail()
        packet["codex_analysis"]["facts"][0]["text"] = "已读取 source_scope 和 hash 占位"
        packet["codex_analysis"]["checks"][0]["evidence_refs"] = ["内部引用占位"]
        weekly = weekly_packet()
        weekly["sections"]["data_note"][0]["summary"] = "周度审计占位"
        weekly["sections"]["judgement"][0]["boundary"] = "后台边界占位"
        rendered = MODULE.render_unified_dashboard(daily_packet=packet, weekly_packet=weekly, template=TEMPLATE_PATH.read_text())
        for marker in ("仅审计可见占位", "机械勾稽秘密占位", "source_scope", "hash 占位", "内部引用占位", "周度审计占位", "后台边界占位", "demo-plan", "a" * 64, "ema20_atr14", "半开区间"):
            self.assertNotIn(marker, rendered, msg=f"internal marker leaked: {marker}")
        self.assertIn("EMA20/50/200", rendered)

    def test_calendar_uses_reference_new_york_week_not_historical_review_week(self):
        packet = event_calendar("2026-08-31T16:00:00+08:00", "2026-09-06T23:30:00-04:00", "2026-09-07T08:30:00-04:00", "2026-09-14T08:30:00-04:00")
        rendered = self.render(packet)
        self.assertIn("2026-08-31 — 2026-09-06", rendered)
        self.assertIn("2026-09-07 — 2026-09-13", rendered)
        self.assertEqual(rendered.count('class="v2-calendar-day"'), 14)
        sunday = rendered.split('data-date="2026-09-06"', 1)[1].split('data-date="2026-09-07"', 1)[0]
        self.assertIn("合成事件0", sunday)
        self.assertIn("09-07 11:30 北京", sunday)
        self.assertNotIn("合成事件2", rendered)

    def test_calendar_week_does_not_advance_at_shanghai_monday_midnight(self):
        packet = event_calendar("2026-08-31T03:00:00+08:00", "2026-08-30T20:00:00-04:00")
        rendered = self.render(packet)
        self.assertIn("2026-08-24 — 2026-08-30", rendered)
        self.assertIn("2026-08-31 — 2026-09-06", rendered)

    def test_calendar_new_year_and_dst_conversion_are_stable(self):
        packet = event_calendar("2026-12-31T08:00:00-05:00", "2027-01-01T13:00:00-05:00")
        rendered = self.render(packet)
        self.assertIn("2026-12-28 — 2027-01-03", rendered)
        self.assertIn("01-02 02:00 北京", rendered)
        packet = event_calendar("2026-03-08T04:00:00-04:00", "2026-03-08T03:30:00-04:00")
        self.assertIn("03-08 15:30 北京", self.render(packet))

    def test_event_dedup_preserves_objects_and_disagreement_is_not_promoted(self):
        packet = event_calendar("2026-08-31T16:00:00+08:00", "2026-09-01T09:05:00-04:00")
        first = packet["events"]["groups"][0]["events"][0]
        packet["events"]["groups"][0]["events"] += [copy.deepcopy(first), {**copy.deepcopy(first), "object": "另一标的"}]
        self.assertEqual(self.render(packet).count('<article class="v2-event-row">'), 2)
        packet["events"]["groups"][0]["events"][1]["status"] = "已取消"
        rows = MODULE._calendar_rows(packet, None)
        self.assertEqual(rows[0]["status"], "未验证")
        self.assertEqual(rows[0]["data_status"], "partial")
        rendered = self.render(packet)
        event_section = rendered.split('aria-labelledby="events-heading"', 1)[1]
        self.assertNotIn('class="v2-status-badge v2-status-complete"', event_section)
        self.assertNotIn('class="v2-status-badge v2-status-partial"', event_section)
        self.assertIn("事件信息待核对", event_section)

    def test_fed_speeches_need_official_source_and_show_impact(self):
        packet = event_calendar("2026-08-31T16:00:00+08:00", "2026-09-01T09:05:00-04:00")
        event = packet["events"]["groups"][0]["events"][0]
        event.update(kind="fed_speech", speaker="合成官员", source="美联储官网", source_url="https://www.federalreserve.gov/newsevents/calendar.htm", watch_for="观察利率预期是否改变")
        rendered = self.render(packet)
        self.assertIn("联储讲话", rendered)
        self.assertIn("合成官员", rendered)
        self.assertIn("观察利率预期是否改变", rendered)
        self.assertIn("增长预期影响估值", rendered)
        self.assertNotIn("观察点与来源", rendered)
        self.assertNotIn(event["source_url"], rendered)
        self.assertNotIn(">预期<", rendered)
        for url in ("https://example.com/calendar", "javascript:alert(1)", "https://federalreserve.gov.evil.test/", "https://www.federalreserve.gov:bad/calendar", "https://www.federalreserve.gov/calendar?q=private"):
            event["source_url"] = url
            with self.subTest(url=url), self.assertRaises(MODULE.DashboardRenderError):
                self.render(packet)
        event.pop("source_url")
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "official calendar source"):
            self.render(packet)

    def test_empty_calendar_days_are_simple_while_coverage_remains_private(self):
        packet = event_calendar("2026-08-31T16:00:00+08:00")
        packet["events"].update(status="partial", note="讲话排期待核对", coverage=[{"label": "联储讲话", "status": "partial", "note": "地区联储排期待核对"}])
        packet["meta"]["overall_status"] = "partial"
        before = copy.deepcopy(packet)
        rendered = self.render(packet)
        event_section = rendered.split('aria-labelledby="events-heading"', 1)[1]
        self.assertIn("暂无已收录事件", event_section)
        for marker in ("排期待补充", "地区联储排期待核对", "部分可用", "v2-calendar-coverage", "完整覆盖"):
            self.assertNotIn(marker, event_section)
        self.assertEqual(packet, before)
        packet["events"]["status"] = "complete"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "coverage"):
            self.render(packet)

    def test_economic_revisions_are_not_mistaken_for_internal_revision_ids(self):
        packet = event_calendar("2026-08-31T16:00:00+08:00", "2026-09-04T08:30:00-04:00")
        packet["events"]["groups"][0]["events"][0]["watch_for"] = "结合前值修订，不能只看新增就业。\n留意工资和修订值。\n内部修订版本不应外露。\n内部修订号不应外露。"
        rendered = self.render(packet)
        self.assertIn("结合前值修订，不能只看新增就业。", rendered)
        self.assertIn("留意工资和修订值。", rendered)
        self.assertNotIn("内部修订版本不应外露", rendered)
        self.assertNotIn("内部修订号不应外露", rendered)
        self.assertNotIn("观察条件待确认", rendered)
        self.assertEqual(rendered.count('<p class="v2-event-watch">'), 2)

    def test_specific_cancelled_and_stale_event_warnings_remain_visible(self):
        packet = event_calendar("2026-08-31T16:00:00+08:00", "2026-09-04T08:30:00-04:00")
        event = packet["events"]["groups"][0]["events"][0]
        event["status"] = "已取消"
        self.assertIn(">已取消<", self.render(packet))
        event["status"], event["data_status"] = "预期", "stale"
        packet["events"]["status"] = "stale"
        packet["meta"]["overall_status"] = "partial"
        rendered = self.render(packet)
        self.assertIn("排期较旧，请重新核对", rendered)
        self.assertNotIn(">预期<", rendered)

    def test_weekly_only_page_is_rejected(self):
        packet = weekly_packet()
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        validated = MODULE.validate_weekly_packet(packet)
        self.assertEqual(validated["meta"]["freshness"], "current")
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "daily packet is required"):
            MODULE.render_weekly_dashboard(packet, template)

    def test_weekly_packet_is_inline_and_has_no_result_or_mode_panels(self):
        rendered = MODULE.render_unified_dashboard(
            daily_packet=fixture("complete"),
            weekly_packet=weekly_packet(),
            template=TEMPLATE_PATH.read_text(encoding="utf-8"),
        )
        for marker in (
            "周度复盘",
            "持仓 × 计划",
            "计划覆盖率",
            "按计划执行率",
            "计划胜率",
            "计划复核与纪律",
            "后续计划待确认",
            "更新与使用说明",
        ):
            self.assertIn(marker, rendered)
        self.assertEqual(rendered.count("<main "), 1)
        self.assertEqual(rendered.count('class="v2-shell"'), 1)
        for marker in (
            "v2-mode-daily", "v2-mode-weekly", "v2-daily-panel", "v2-weekly-panel",
            "本周结果", "周度收益", "周度收益率", "标的归因", "现金流与数据说明",
            "周度操作摘要",
        ):
            self.assertNotIn(marker, rendered)
        self.assertNotIn("initial_asset_value", rendered)
        self.assertNotIn("ending_asset_value", rendered)
        self.assertNotIn("buying_power", rendered)
        self.assertNotIn("<script", rendered.lower())

    def test_unified_daily_order_is_unchanged_with_weekly_increments(self):
        rendered = MODULE.render_unified_dashboard(
            daily_packet=fixture("complete"),
            weekly_packet=weekly_packet(),
            template=TEMPLATE_PATH.read_text(encoding="utf-8"),
        )
        headings = (
            'id="market-heading"', 'id="judgement-heading"',
            'id="operations-heading"', 'id="plans-heading"', 'id="events-heading"',
        )
        offsets = [rendered.index(heading) for heading in headings]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn('data-weekly-section="operations"', rendered)
        self.assertGreater(rendered.index('aria-label="周度计划执行质量"'), rendered.index('id="plans-heading"'))

    def test_weekly_stale_and_blocked_states_are_not_conflated(self):
        stale = weekly_packet()
        stale["meta"]["freshness"] = "stale"
        rendered = MODULE.render_weekly_dashboard(
            stale, TEMPLATE_PATH.read_text(encoding="utf-8"), daily_packet=fixture("complete")
        )
        self.assertIn("内容陈旧，请重新复核", rendered)
        blocked = weekly_packet()
        blocked["meta"]["overall_status"] = "blocked"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "blocked"):
            MODULE.validate_weekly_packet(blocked)

    def test_weekly_packet_rejects_option_identity_and_sensitive_fields(self):
        option_identity = weekly_packet()
        option_identity["sections"]["operations"][0]["summary"] = "DEMO.US:OPTION"
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "option contract identity"):
            MODULE.validate_weekly_packet(option_identity)
        sensitive = weekly_packet()
        sensitive["sections"]["operations"][0]["order_id"] = "synthetic"
        with self.assertRaises(MODULE.DashboardRenderError):
            MODULE.validate_weekly_packet(sensitive)

    def test_weekly_blocked_metrics_are_unavailable_not_zero_percent(self):
        rendered = MODULE.render_weekly_dashboard(
            weekly_packet(), TEMPLATE_PATH.read_text(encoding="utf-8"),
            daily_packet=fixture("complete"),
        )
        metric_body = rendered.split('class="v2-execution-metrics"', 1)[1].split('</div>\n        <p', 1)[0]
        self.assertIn("不可计算", metric_body)
        self.assertNotIn("0.0%", metric_body)
        invalid = weekly_packet()
        invalid["execution_metrics"]["coverage_rate"] = 0.0
        with self.assertRaisesRegex(MODULE.DashboardRenderError, "zero denominator"):
            MODULE.validate_weekly_packet(invalid)

    def test_legacy_readback_does_not_project_result_tables_or_result_narrative(self):
        readback = {
            "period_start": "2026-08-24", "period_end": "2026-08-28",
            "generated_at": "2026-08-30T08:00:00+08:00", "data_status": "partial",
            "freshness": {"status": "current"}, "confirmation_status": "pending",
            "performance": {"profit": "7777.77"},
            "attributions": [{"profit": "8888.88"}],
            "cash_flow_aggregates": [{"amount": "9999.99"}],
            "review_items": [
                {"subject": "周度判断｜结果与归因", "summary": "周度收益 7777.77", "evidence_boundary": "旧口径", "evidence_kind": "fact", "item_kind": "risk", "data_status": "partial"},
                {"subject": "计划复核｜缺口", "summary": "事前计划缺失", "evidence_boundary": "不反推计划", "evidence_kind": "gap", "item_kind": "gap", "data_status": "blocked"},
            ],
        }
        packet = MODULE.build_weekly_packet(readback)
        encoded = json.dumps(packet, ensure_ascii=False)
        for marker in ("7777.77", "8888.88", "9999.99", "performance", "attributions", "cash_flow_aggregates"):
            self.assertNotIn(marker, encoded)
        self.assertEqual(packet["execution_metrics"]["data_status"], "blocked")


if __name__ == "__main__":
    unittest.main()
