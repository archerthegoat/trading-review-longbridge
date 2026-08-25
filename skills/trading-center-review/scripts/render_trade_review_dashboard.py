#!/usr/bin/env python3
"""Render a private trading review packet as a stable standalone HTML dashboard.

The renderer uses only the Python standard library, escapes every user-provided
value, emits no JavaScript, iframe, or document.write call, and keeps private
input/output outside Git worktrees.  The bundled HTML asset owns the visual
design so recurring reviews retain the same information hierarchy and styling.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "trading-review-dashboard.v1"
BODY_MARKER = "<!--__TRADING_REVIEW_DASHBOARD_BODY__-->"
DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "trade-review-dashboard-standalone.html"
)

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "eyebrow",
        "title",
        "subtitle",
        "badges",
        "status",
        "summary_cards",
        "summary_note",
        "account",
        "review_cards",
        "plan_callout",
        "plans",
        "excluded",
        "event_groups",
        "event_note",
        "footer",
    }
)
TONE_VALUES = frozenset({"neutral", "blue", "green", "amber", "red"})
EVENT_KIND_VALUES = frozenset({"news", "macro", "earnings", "risk"})
PLAN_TAB_VALUES = frozenset({"holdings", "plan"})


class DashboardRenderError(RuntimeError):
    """A fail-closed dashboard validation or rendering error."""


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DashboardRenderError(f"{path} must be an object")
    return value


def _array(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DashboardRenderError(f"{path} must be an array")
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str] | frozenset[str], path: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise DashboardRenderError(f"unsupported field at {path}: {', '.join(unknown[:3])}")


def _text(value: object, path: str, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise DashboardRenderError(f"{path} must be a string")
    stripped = value.strip()
    if required and not stripped:
        raise DashboardRenderError(f"{path} must not be empty")
    return stripped


def _boolean(value: object, path: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise DashboardRenderError(f"{path} must be a boolean")
    return value


def _tone(value: object, path: str, *, default: str = "neutral") -> str:
    if value is None:
        return default
    result = _text(value, path)
    if result not in TONE_VALUES:
        raise DashboardRenderError(f"{path} must be one of {sorted(TONE_VALUES)}")
    return result


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _class_tone(value: str) -> str:
    return "" if value == "neutral" else f" {value}"


def _section_head(index: str, eyebrow: str, title: str, note: str) -> str:
    return (
        '<div class="trc-section-head"><div>'
        f'<div class="trc-eyebrow">{_escape(index)} · {_escape(eyebrow)}</div>'
        f'<h2>{_escape(title)}</h2></div>'
        f'<div class="trc-section-note">{_escape(note)}</div></div>'
    )


def _render_header(packet: dict[str, Any]) -> str:
    badges: list[str] = []
    for index, raw in enumerate(_array(packet.get("badges", []), "$.badges")):
        item = _object(raw, f"$.badges[{index}]")
        _reject_unknown(item, {"label", "value", "tone"}, f"$.badges[{index}]")
        label = _text(item.get("label"), f"$.badges[{index}].label")
        value = _text(item.get("value"), f"$.badges[{index}].value")
        tone = _tone(item.get("tone"), f"$.badges[{index}].tone")
        badges.append(
            f'<span class="trc-badge{_class_tone(tone)}"><strong>{_escape(label)}</strong> {_escape(value)}</span>'
        )

    eyebrow = _text(packet.get("eyebrow"), "$.eyebrow")
    title = _text(packet.get("title"), "$.title")
    subtitle = _text(packet.get("subtitle"), "$.subtitle")
    return (
        '<header class="trc-header">'
        '<div class="trc-header-copy">'
        f'<div class="trc-eyebrow">{_escape(eyebrow)}</div>'
        f'<h1>{_escape(title)}</h1><p class="trc-subtitle">{_escape(subtitle)}</p>'
        '</div>'
        f'<div class="trc-badges">{"".join(badges)}</div>'
        '</header>'
    )


def _render_status(packet: dict[str, Any]) -> str:
    raw = packet.get("status")
    if raw is None:
        return ""
    item = _object(raw, "$.status")
    _reject_unknown(item, {"title", "detail", "tone"}, "$.status")
    title = _text(item.get("title"), "$.status.title")
    detail = _text(item.get("detail"), "$.status.detail")
    tone = _tone(item.get("tone"), "$.status.tone", default="blue")
    return (
        f'<div class="trc-status{_class_tone(tone)}" role="status">'
        '<span class="trc-status-dot" aria-hidden="true"></span>'
        '<div class="trc-status-copy">'
        f'<span class="trc-status-title">{_escape(title)}</span>'
        f'<span class="trc-status-detail">{_escape(detail)}</span>'
        '</div></div>'
    )


def _render_summary(packet: dict[str, Any]) -> str:
    raw_cards = _array(packet.get("summary_cards", []), "$.summary_cards")
    if not raw_cards:
        return ""
    cards: list[str] = []
    for index, raw in enumerate(raw_cards):
        item = _object(raw, f"$.summary_cards[{index}]")
        _reject_unknown(item, {"kicker", "title", "text", "tone"}, f"$.summary_cards[{index}]")
        tone = _tone(item.get("tone"), f"$.summary_cards[{index}].tone")
        cards.append(
            f'<article class="trc-summary-card{_class_tone(tone)}">'
            f'<div class="trc-summary-kicker">{_escape(_text(item.get("kicker"), f"$.summary_cards[{index}].kicker"))}</div>'
            f'<h3>{_escape(_text(item.get("title"), f"$.summary_cards[{index}].title"))}</h3>'
            f'<p>{_escape(_text(item.get("text"), f"$.summary_cards[{index}].text"))}</p>'
            '</article>'
        )
    note = _text(packet.get("summary_note"), "$.summary_note", required=False)
    note_html = f'<div class="trc-summary-note">{_escape(note)}</div>' if note else ""
    return (
        '<section class="trc-section" id="summary">'
        + _section_head("01", "策略摘要", "交易风格与整体逻辑", "先呈现组合级判断，再进入标的计划")
        + f'<div class="trc-summary-grid">{"".join(cards)}</div>{note_html}</section>'
    )


def _format_pnl(value: float) -> str:
    if value > 0:
        return f"+{value:,.2f}"
    if value < 0:
        return f"−{abs(value):,.2f}"
    return "0.00"


def _pnl_tone(value: float) -> str:
    if value > 0:
        return "trc-positive"
    if value < 0:
        return "trc-negative"
    return "trc-neutral"


def _render_account(packet: dict[str, Any], *, section_index: str = "02") -> str:
    raw = packet.get("account")
    if raw is None:
        return ""
    account = _object(raw, "$.account")
    _reject_unknown(account, {"metrics", "evidence", "note", "pnl", "pnl_note"}, "$.account")

    metrics: list[str] = []
    for index, raw_metric in enumerate(_array(account.get("metrics", []), "$.account.metrics")):
        item = _object(raw_metric, f"$.account.metrics[{index}]")
        _reject_unknown(item, {"label", "value", "meta", "tone"}, f"$.account.metrics[{index}]")
        tone = _tone(item.get("tone"), f"$.account.metrics[{index}].tone")
        value_class = {
            "green": " trc-positive",
            "red": " trc-negative",
            "neutral": "",
            "blue": "",
            "amber": "",
        }[tone]
        metrics.append(
            '<div class="trc-metric">'
            f'<div class="trc-metric-label">{_escape(_text(item.get("label"), f"$.account.metrics[{index}].label"))}</div>'
            f'<div class="trc-metric-value{value_class}">{_escape(_text(item.get("value"), f"$.account.metrics[{index}].value"))}</div>'
            f'<div class="trc-metric-meta">{_escape(_text(item.get("meta"), f"$.account.metrics[{index}].meta", required=False))}</div>'
            '</div>'
        )

    evidence: list[str] = []
    for index, raw_evidence in enumerate(_array(account.get("evidence", []), "$.account.evidence")):
        item = _object(raw_evidence, f"$.account.evidence[{index}]")
        _reject_unknown(item, {"label", "value"}, f"$.account.evidence[{index}]")
        evidence.append(
            '<div class="trc-evidence-row">'
            f'<div class="trc-evidence-label">{_escape(_text(item.get("label"), f"$.account.evidence[{index}].label"))}</div>'
            f'<div class="trc-evidence-value">{_escape(_text(item.get("value"), f"$.account.evidence[{index}].value"))}</div>'
            '</div>'
        )

    pnl_items: list[tuple[str, float]] = []
    for index, raw_pnl in enumerate(_array(account.get("pnl", []), "$.account.pnl")):
        item = _object(raw_pnl, f"$.account.pnl[{index}]")
        _reject_unknown(item, {"symbol", "value"}, f"$.account.pnl[{index}]")
        symbol = _text(item.get("symbol"), f"$.account.pnl[{index}].symbol")
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DashboardRenderError(f"$.account.pnl[{index}].value must be a number")
        pnl_items.append((symbol, float(value)))

    rows = "".join(
        '<tr>'
        f'<td>{_escape(symbol)}</td>'
        f'<td class="trc-num {_pnl_tone(value)}">{_escape(_format_pnl(value))}</td>'
        f'<td class="trc-num">{"正" if value > 0 else "负" if value < 0 else "平"}</td>'
        '</tr>'
        for symbol, value in pnl_items
    )
    maximum = max((abs(value) for _, value in pnl_items), default=0.0)
    bars = "".join(
        '<div class="trc-bar-row">'
        f'<span>{_escape(symbol)}</span>'
        '<span class="trc-bar-track">'
        f'<span class="trc-bar-fill{" negative" if value < 0 else ""}" style="width:{0 if maximum == 0 else max(2, round(abs(value) / maximum * 100))}%"></span>'
        '</span>'
        f'<span class="trc-bar-value {_pnl_tone(value)}">{_escape(_format_pnl(value))}</span>'
        '</div>'
        for symbol, value in pnl_items
    )

    note = _text(account.get("note"), "$.account.note", required=False)
    pnl_note = _text(account.get("pnl_note"), "$.account.pnl_note", required=False)
    metrics_html = f'<div class="trc-account-summary">{"".join(metrics)}</div>' if metrics else ""
    evidence_html = f'<div class="trc-evidence">{"".join(evidence)}</div>' if evidence else ""
    note_html = f'<div class="trc-small-note">{_escape(note)}</div>' if note else ""
    pnl_html = ""
    if pnl_items:
        pnl_html = (
            '<div class="trc-pnl-layout">'
            '<div><div class="trc-subcard-head"><h3>成交与损益概览</h3><span>按标的独立归属</span></div>'
            '<div class="trc-table-wrap"><table><thead><tr><th>标的</th><th class="trc-num">周期损益</th><th class="trc-num">结果</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
            + (f'<div class="trc-small-note">{_escape(pnl_note)}</div>' if pnl_note else "")
            + f'</div><div class="trc-bars">{bars}</div></div>'
        )

    return (
        '<section class="trc-section" id="account">'
        + _section_head(section_index, "账户与交易", "账户与交易证据", "先看证据边界，再看成交与损益；不展示资金流水")
        + f'<div class="trc-card">{metrics_html}{evidence_html}{note_html}{pnl_html}</div></section>'
    )


def _render_reviews(packet: dict[str, Any]) -> str:
    raw_cards = _array(packet.get("review_cards", []), "$.review_cards")
    if not raw_cards:
        return ""
    cards: list[str] = []
    for index, raw in enumerate(raw_cards):
        item = _object(raw, f"$.review_cards[{index}]")
        _reject_unknown(item, {"kicker", "title", "text", "meta", "tone"}, f"$.review_cards[{index}]")
        tone = _tone(item.get("tone"), f"$.review_cards[{index}].tone")
        meta = "".join(
            f'<span>{_escape(_text(value, f"$.review_cards[{index}].meta[{meta_index}]"))}</span>'
            for meta_index, value in enumerate(_array(item.get("meta", []), f"$.review_cards[{index}].meta"))
        )
        cards.append(
            f'<article class="trc-review-card{_class_tone(tone)}">'
            f'<div class="trc-review-kicker">{_escape(_text(item.get("kicker"), f"$.review_cards[{index}].kicker"))}</div>'
            f'<h3>{_escape(_text(item.get("title"), f"$.review_cards[{index}].title"))}</h3>'
            f'<p>{_escape(_text(item.get("text"), f"$.review_cards[{index}].text"))}</p>'
            f'<div class="trc-review-meta">{meta}</div></article>'
        )
    return (
        '<section class="trc-section" id="reviews">'
        + _section_head("03", "周度复盘要点", "本周损益与执行复盘", "区分逻辑判断、执行纪律与持仓管理")
        + f'<div class="trc-card"><div class="trc-review-grid">{"".join(cards)}</div></div></section>'
    )


def _render_plans(packet: dict[str, Any]) -> str:
    raw_plans = _array(packet.get("plans", []), "$.plans")
    raw_excluded = _array(packet.get("excluded", []), "$.excluded")
    if not raw_plans and not raw_excluded:
        return ""
    cards: list[str] = []
    tab_cards: dict[str, list[str]] = {"holdings": [], "plan": []}
    is_daily = not _array(packet.get("summary_cards", []), "$.summary_cards") and not _array(
        packet.get("review_cards", []), "$.review_cards"
    )
    for index, raw in enumerate(raw_plans):
        item = _object(raw, f"$.plans[{index}]")
        _reject_unknown(item, {"symbol", "name", "subtitle", "state", "state_tone", "open", "blocks", "tab"}, f"$.plans[{index}]")
        symbol = _text(item.get("symbol"), f"$.plans[{index}].symbol")
        tab = _text(item.get("tab"), f"$.plans[{index}].tab", required=False) or "plan"
        if tab not in PLAN_TAB_VALUES:
            raise DashboardRenderError(f"$.plans[{index}].tab must be one of {sorted(PLAN_TAB_VALUES)}")
        state_tone = _tone(item.get("state_tone"), f"$.plans[{index}].state_tone", default="green")
        blocks: list[str] = []
        for block_index, raw_block in enumerate(_array(item.get("blocks", []), f"$.plans[{index}].blocks")):
            block = _object(raw_block, f"$.plans[{index}].blocks[{block_index}]")
            _reject_unknown(block, {"label", "value", "full"}, f"$.plans[{index}].blocks[{block_index}]")
            full = _boolean(block.get("full"), f"$.plans[{index}].blocks[{block_index}].full")
            blocks.append(
                f'<div class="trc-plan-block{" full" if full else ""}">'
                f'<div class="trc-plan-block-label">{_escape(_text(block.get("label"), f"$.plans[{index}].blocks[{block_index}].label"))}</div>'
                f'<div class="trc-plan-block-value">{_escape(_text(block.get("value"), f"$.plans[{index}].blocks[{block_index}].value"))}</div>'
                '</div>'
            )
        card = (
            f'<details class="trc-plan-card"{" open" if _boolean(item.get("open"), f"$.plans[{index}].open") else ""}>'
            '<summary class="trc-plan-summary">'
            f'<span class="trc-ticker">{_escape(symbol)}</span>'
            f'<span><span class="trc-plan-name">{_escape(_text(item.get("name"), f"$.plans[{index}].name"))}</span>'
            f'<span class="trc-plan-sub">{_escape(_text(item.get("subtitle"), f"$.plans[{index}].subtitle", required=False))}</span></span>'
            f'<span class="trc-plan-state{_class_tone(state_tone)}">{_escape(_text(item.get("state"), f"$.plans[{index}].state"))}</span>'
            f'</summary><div class="trc-plan-body">{"".join(blocks)}</div></details>'
        )
        cards.append(card)
        tab_cards[tab].append(card)

    excluded: list[str] = []
    for index, raw in enumerate(raw_excluded):
        item = _object(raw, f"$.excluded[{index}]")
        _reject_unknown(item, {"symbol", "reason"}, f"$.excluded[{index}]")
        symbol = _text(item.get("symbol"), f"$.excluded[{index}].symbol")
        reason = _text(item.get("reason"), f"$.excluded[{index}].reason")
        excluded.append(f'<span class="trc-chip red"><strong>{_escape(symbol)}</strong> {_escape(reason)}</span>')

    callout = _text(packet.get("plan_callout"), "$.plan_callout", required=False)
    callout_html = (
        '<div class="trc-plan-callout"><span class="trc-plan-callout-mark">!</span>'
        f'<div>{_escape(callout)}</div></div>'
        if callout
        else ""
    )
    if is_daily:
        def panel(panel_id: str, items: list[str], empty: str) -> str:
            content = "".join(items) or f'<div class="trc-small-note">{_escape(empty)}</div>'
            return f'<div id="trc-plan-panel-{panel_id}" class="trc-plan-tab-panel" role="tabpanel"><div class="trc-plan-grid">{content}</div></div>'

        plans_html = (
            '<div class="trc-plan-tabs" role="tablist" aria-label="交易计划视图">'
            '<input class="trc-plan-tab-input" type="radio" name="trc-plan-tab" id="trc-plan-tab-holdings" checked>'
            '<label class="trc-plan-tab-label" for="trc-plan-tab-holdings" role="tab">当前持仓</label>'
            '<input class="trc-plan-tab-input" type="radio" name="trc-plan-tab" id="trc-plan-tab-plan">'
            '<label class="trc-plan-tab-label" for="trc-plan-tab-plan" role="tab">Plan</label>'
            '<div class="trc-plan-tab-panels">'
            + panel("holdings", tab_cards["holdings"], "当前没有已确认的持仓计划。")
            + panel("plan", tab_cards["plan"], "当前没有未持仓的 Plan。")
            + '</div></div>'
        )
        section_head = _section_head("02", "交易计划", "交易计划", "按当前持仓与未持仓的 Plan 分开；五类策略只作研究分类")
    else:
        plans_html = f'<div class="trc-plan-grid">{"".join(cards)}</div>'
        section_head = _section_head("04", "标的计划", "当前有效的标的交易计划", "只放具体标的；组合级方向留在摘要，不混入系统流程")
    return (
        '<section class="trc-section" id="plans">'
        + section_head
        + callout_html
        + plans_html
        + (f'<div class="trc-excluded">{"".join(excluded)}</div>' if excluded else "")
        + '</section>'
    )


def _render_events(packet: dict[str, Any], *, index: str = "05") -> str:
    raw_groups = _array(packet.get("event_groups", []), "$.event_groups")
    if not raw_groups:
        return ""
    groups: list[str] = []
    for group_index, raw_group in enumerate(raw_groups):
        group = _object(raw_group, f"$.event_groups[{group_index}]")
        _reject_unknown(group, {"label", "range", "events"}, f"$.event_groups[{group_index}]")
        events: list[str] = []
        for event_index, raw_event in enumerate(_array(group.get("events", []), f"$.event_groups[{group_index}].events")):
            item = _object(raw_event, f"$.event_groups[{group_index}].events[{event_index}]")
            path = f"$.event_groups[{group_index}].events[{event_index}]"
            _reject_unknown(item, {"date", "time", "title", "meta", "kind", "tag", "source", "status", "impact", "open"}, path)
            kind = _text(item.get("kind"), f"{path}.kind")
            if kind not in EVENT_KIND_VALUES:
                raise DashboardRenderError(f"{path}.kind must be one of {sorted(EVENT_KIND_VALUES)}")
            events.append(
                f'<details class="trc-event-item {kind}"{" open" if _boolean(item.get("open"), f"{path}.open") else ""}>'
                '<summary class="trc-event-summary">'
                f'<span class="trc-event-time">{_escape(_text(item.get("date"), f"{path}.date"))}<br>{_escape(_text(item.get("time"), f"{path}.time"))}</span>'
                f'<span class="trc-event-title">{_escape(_text(item.get("title"), f"{path}.title"))}</span>'
                f'<span class="trc-event-meta">{_escape(_text(item.get("meta"), f"{path}.meta"))}</span>'
                f'<span class="trc-event-tag">{_escape(_text(item.get("tag"), f"{path}.tag"))}</span>'
                '</summary><div class="trc-event-detail">'
                '<div class="trc-detail-cell"><div class="trc-detail-label">事件源</div>'
                f'<div class="trc-detail-value">{_escape(_text(item.get("source"), f"{path}.source"))}</div></div>'
                '<div class="trc-detail-cell"><div class="trc-detail-label">状态</div>'
                f'<div class="trc-detail-value">{_escape(_text(item.get("status"), f"{path}.status"))}</div></div>'
                '<div class="trc-detail-cell"><div class="trc-detail-label">影响通道</div>'
                f'<div class="trc-detail-value">{_escape(_text(item.get("impact"), f"{path}.impact"))}</div></div>'
                '</div></details>'
            )
        groups.append(
            '<div class="trc-day">'
            f'<div class="trc-day-label">{_escape(_text(group.get("label"), f"$.event_groups[{group_index}].label"))}'
            f'<small>{_escape(_text(group.get("range"), f"$.event_groups[{group_index}].range"))}</small></div>'
            f'{"".join(events)}</div>'
        )
    event_note = _text(packet.get("event_note"), "$.event_note", required=False)
    return (
        '<section class="trc-section" id="events">'
        + _section_head(index, "相关事件", "持仓与计划相关重要事件", "全量日历私存；只展示相关美股财报与明确风险通道")
        + f'<div class="trc-card"><div class="trc-calendar">{"".join(groups)}'
        + (f'<div class="trc-calendar-footnote">{_escape(event_note)}</div>' if event_note else "")
        + '</div></div></section>'
    )


def validate_packet(value: object) -> dict[str, Any]:
    packet = _object(value, "$")
    _reject_unknown(packet, TOP_LEVEL_KEYS, "$")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise DashboardRenderError(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("eyebrow", "title", "subtitle"):
        _text(packet.get(key), f"$.{key}")
    return packet


def render_dashboard(packet: object, template: str) -> str:
    """Validate a dashboard packet and render one direct standalone document."""
    validated = validate_packet(packet)
    if template.count(BODY_MARKER) != 1:
        raise DashboardRenderError("HTML template must contain exactly one dashboard body marker")
    is_daily = not _array(validated.get("summary_cards", []), "$.summary_cards") and not _array(
        validated.get("review_cards", []), "$.review_cards"
    )
    body = "".join(
        (
            _render_header(validated),
            _render_status(validated),
            "" if is_daily else _render_summary(validated),
            _render_account(validated, section_index="01" if is_daily else "02"),
            "" if is_daily else _render_reviews(validated),
            _render_plans(validated),
            _render_events(validated, index="03" if is_daily else "05"),
        )
    )
    footer = _text(validated.get("footer"), "$.footer", required=False)
    if footer:
        body += f'<footer class="trc-footer">{_escape(footer)}</footer>'
    return template.replace(BODY_MARKER, body)


def _git_root_for(path: Path) -> Path | None:
    resolved = path.expanduser().resolve()
    probe = resolved if resolved.is_dir() else resolved.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _require_private_path(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if _git_root_for(resolved) is not None:
        raise DashboardRenderError(f"{label} must be outside every Git worktree")
    return resolved


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Private dashboard JSON packet")
    parser.add_argument("--output", required=True, type=Path, help="Private standalone HTML output")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="Bundled public HTML template")
    args = parser.parse_args(argv)

    try:
        input_path = _require_private_path(args.input, "input")
        output_path = _require_private_path(args.output, "output")
        packet = json.loads(input_path.read_text(encoding="utf-8"))
        template = args.template.expanduser().resolve().read_text(encoding="utf-8")
        rendered = render_dashboard(packet, template)
        _write_private(output_path, rendered)
    except (DashboardRenderError, OSError, json.JSONDecodeError) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {"status": "completed", "schema_version": SCHEMA_VERSION, "output": str(output_path)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
