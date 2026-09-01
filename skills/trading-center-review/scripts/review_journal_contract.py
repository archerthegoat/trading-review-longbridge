"""Strict de-identified review envelope and inert Markdown; no filesystem or DB."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA = "confirmed-investment-review.v1"
MAX_BYTES = 256 * 1024
START = "<!-- trading-review:managed:start -->"
END = "<!-- trading-review:managed:end -->"
SECTIONS = ("executions", "plan_actual", "holdings_understanding", "events", "facts", "interpretation", "conditions", "pending", "lessons", "confirmed_plan_summary")
COUNTS = ("eligible_episode_count", "covered_episode_count", "assessable_episode_count", "compliant_episode_count", "resolved_episode_count", "successful_episode_count", "review_needed_count")
RATES = {"coverage_rate": ("covered_episode_count", "eligible_episode_count"), "execution_rate": ("compliant_episode_count", "assessable_episode_count"), "plan_win_rate": ("successful_episode_count", "resolved_episode_count")}
KEYS = {"schema_version", "review_type", "review_key", "review_date", "period_start", "period_end", "confirmation_status", "confirmation_version", "supersedes_confirmation_version", "confirmed_at", "source_revision", "facts_hash", "plan_hash", "facts_as_of", "generated_at", "data_status", "gap_categories", "sections", "weekly_metrics", "payload_hash"}
STATUSES = {"complete", "partial", "empty", "stale"}
STATUS_LABELS = {"complete": "已齐备", "partial": "有明确缺口", "empty": "本期无适用记录", "stale": "资料较旧", "blocked": "暂不可评估"}
HASH = re.compile(r"[0-9a-f]{64}\Z")
# Deliberately conservative: precision belongs in the private trading state.
# Plain prose can be re-drafted and re-approved, never silently redacted.
PRIVATE_TEXT = re.compile(
    r"\d|[<>\[\]`\x00-\x1f\x7f]|https?://|obsidian:|file:|www\.|/Users/|/private/|"
    r"(?:access|refresh)[_ -]?token|api[_ -]?key|bearer\s|authorization\s*[:=]|"
    r"(?:account|order|execution|trade|client)[_ -]?(?:id|number|no)\b|"
    r"(?:账户|订单|成交|交易)(?:编号|标识|号码)|凭据|密码|私钥|"
    r"[A-Za-z0-9_-]{24,}|"
    r"[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+\s*(?:股|张|手|份|元|美元|美金|港币|块|成|倍|点|%|％)|"
    r"(?:价位|价格|买入价|卖出价|加仓位|加仓价|止损价|成本|费用|盈亏|盈利|亏损|仓位|数量|金额|本金|跌破|突破|站上|站稳|回踩|回到|降至|涨至)[为是约在：:\s]*[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+|"
    r"[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]{3,}",
    re.IGNORECASE,
)


class JournalError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise JournalError("noncanonical_json") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_json(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise JournalError("journal_payload_too_large")
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise JournalError("duplicate_json_key")
            obj[key] = value
        return obj
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(JournalError("nonfinite_json")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise JournalError("invalid_journal_json") from exc
    if not isinstance(value, dict):
        raise JournalError("journal_requires_object")
    return value


def exact(value: Any, keys: set, code: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise JournalError(code)
    return value


def integer(value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 1000000000:
        raise JournalError("invalid_journal_integer")
    return value


def instant(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)", value):
        raise JournalError("invalid_journal_timestamp")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JournalError("invalid_journal_timestamp") from exc


def date(value: Any) -> dt.date:
    try:
        if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value:
            raise ValueError()
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise JournalError("invalid_journal_date") from exc


def safe_text(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2000 or (not allow_empty and not value.strip()) or PRIVATE_TEXT.search(value):
        raise JournalError("journal_privacy_or_markup_rejected")
    if any(chr(0x202a) <= c <= chr(0x202e) or chr(0x2066) <= c <= chr(0x2069) for c in value):
        raise JournalError("journal_bidi_control_rejected")
    return value


def text_list(value: Any) -> list:
    if not isinstance(value, list) or len(value) > 100:
        raise JournalError("invalid_journal_list")
    for item in value:
        safe_text(item)
    return value


def metrics(value: Any) -> dict:
    row = exact(value, set(COUNTS) | set(RATES) | {"data_status", "gap"}, "weekly_metric_fields_mismatch")
    if row["data_status"] not in STATUSES | {"blocked"}:
        raise JournalError("weekly_metric_status_invalid")
    for key in COUNTS:
        integer(row[key])
    if row["gap"] is not None:
        safe_text(row["gap"])
    if row["data_status"] in {"partial", "stale", "blocked"} and not row["gap"]:
        raise JournalError("weekly_metric_gap_required")
    for rate, (num, den) in RATES.items():
        if row[num] > row[den]:
            raise JournalError("weekly_metric_denominator_mismatch")
        actual = row[rate]
        expected = row[num] / row[den] if row[den] else None
        if actual is None:
            if expected is not None:
                raise JournalError("weekly_metric_rate_missing")
        elif type(actual) not in (int, float) or not math.isfinite(actual) or expected is None or abs(actual - expected) > 1e-9:
            raise JournalError("weekly_metric_rate_mismatch")
    if any(row[key] > row["eligible_episode_count"] for key in COUNTS):
        raise JournalError("weekly_metric_population_mismatch")
    if row["data_status"] in {"blocked", "empty"} and any(row[k] for k in COUNTS):
        raise JournalError("blocked_or_empty_metrics_cannot_contain_counts")
    return row


def validate_payload(value: Any, *, draft: bool = False) -> dict:
    row = exact(value, KEYS, "journal_fields_mismatch")
    if len(canonical(row)) > MAX_BYTES:
        raise JournalError("journal_payload_too_large")
    if row["schema_version"] != ("investment-review-draft.v1" if draft else SCHEMA) or row["confirmation_status"] != ("pending" if draft else "confirmed"):
        raise JournalError("journal_requires_confirmed_schema")
    kind = row["review_type"]
    start, end, review_date = date(row["period_start"]), date(row["period_end"]), date(row["review_date"])
    if kind not in {"daily", "weekly"} or start > end or review_date != end:
        raise JournalError("journal_window_mismatch")
    expected_key = f"daily:{start}" if kind == "daily" else f"weekly:{start}:{end}"
    if row["review_key"] != expected_key or kind == "daily" and start != end:
        raise JournalError("journal_key_mismatch")
    version = integer(row["confirmation_version"], minimum=1)
    integer(row["source_revision"], minimum=1)
    supersedes = row["supersedes_confirmation_version"]
    if (version == 1 and supersedes is not None) or (version > 1 and supersedes != version - 1) or type(supersedes) is bool:
        raise JournalError("journal_confirmation_chain_mismatch")
    if draft and row["confirmed_at"] is not None:
        raise JournalError("draft_cannot_claim_confirmation_time")
    generated, confirmed, facts_at = instant(row["generated_at"]), instant(row["generated_at"] if draft else row["confirmed_at"]), instant(row["facts_as_of"])
    if not facts_at <= generated <= confirmed or end > confirmed.astimezone(ZoneInfo("America/New_York")).date():
        raise JournalError("journal_timestamp_order_mismatch")
    if row["data_status"] not in STATUSES:
        raise JournalError("blocked_review_cannot_export")
    text_list(row["gap_categories"])
    if row["data_status"] in {"partial", "stale"} and not row["gap_categories"]:
        raise JournalError("journal_gaps_required")
    if row["data_status"] == "complete" and row["gap_categories"]:
        raise JournalError("complete_review_cannot_hide_gaps")
    for field in ("facts_hash", "plan_hash", "payload_hash"):
        if not isinstance(row[field], str) or not HASH.fullmatch(row[field]):
            raise JournalError("invalid_journal_hash")
    exact(row["sections"], set(SECTIONS), "journal_sections_mismatch")
    for section in SECTIONS:
        text_list(row["sections"][section])
    if kind == "daily" and row["weekly_metrics"] is not None:
        raise JournalError("daily_cannot_recompute_weekly_metrics")
    if kind == "weekly":
        metrics(row["weekly_metrics"])
    if digest({k: v for k, v in row.items() if k != "payload_hash"}) != row["payload_hash"]:
        raise JournalError("journal_payload_hash_mismatch")
    return row


def relative_path(row: dict) -> str:
    validate_payload(row)
    if row["review_type"] == "daily":
        return f'25 投资交易/10 每日复盘/{row["review_date"]} 交易复盘.md'
    return f'25 投资交易/20 周度复盘/{row["period_start"]} 至 {row["period_end"]} 周度复盘.md'


def markdown_text(text: str) -> str:
    safe_text(text)
    return re.sub(r"([\\*_#~|])", r"\\\1", text)


def managed_body(row: dict, *, draft: bool = False) -> str:
    validate_payload(row, draft=draft)
    sections = row["sections"]
    def bullets(key):
        return "\n".join("- " + markdown_text(t) for t in sections[key]) or "- 未记录。"
    version_label = "待确认版本" if draft else "已确认版本"
    time_line = f'- 草稿生成时间：{row["generated_at"]}' if draft else f'- 确认时间：{row["confirmed_at"]}'
    blocks = [START, "## 状态与缺口", f'- {version_label}：{row["confirmation_version"]}', time_line,
              f'- 数据情况：{STATUS_LABELS[row["data_status"]]}', f'- 事实资料采集起点：{row["facts_as_of"]}',
              '- 缺口：' + ('；'.join(markdown_text(t) for t in row["gap_categories"]) or '无已知缺口'),
              "## 成交与计划执行", bullets("executions")]
    if row["review_type"] == "weekly":
        m = row["weekly_metrics"]
        blocks.append("## 周度执行质量")
        if m["data_status"] in {"blocked", "empty"}:
            blocks.append("- 暂不可计算。" if m["data_status"] == "blocked" else "- 本期无适用交易；比例不计算。")
        else:
            for rate, label in (("coverage_rate", "计划覆盖率"), ("execution_rate", "按计划执行率"), ("plan_win_rate", "计划胜率")):
                num, den = RATES[rate]
                value = "不可计算" if m[rate] is None else f'{m[rate] * 100:.1f}%'
                blocks.append(f'- {label}：{value}（{m[num]} / {m[den]}）')
            blocks.append(f'- 需具体复盘：{m["review_needed_count"]} 笔')
        if m["gap"]:
            blocks.append("- 统计缺口：" + markdown_text(m["gap"]))
    blocks += ["## 计划 vs 实际", bullets("plan_actual"), "## 当前持仓认知摘要", bullets("holdings_understanding"), "## 相关事件", bullets("events"),
               "## Codex 判断", "### 事实依据", bullets("facts"), "### 解释或推断", bullets("interpretation"),
               "### 条件式风险与失效条件", bullets("conditions"), "### 待验证", bullets("pending"), "## 过程复盘", bullets("lessons"),
               "## 已确认计划摘要", bullets("confirmed_plan_summary"), "## Revision log", f'- {"待确认版本" if draft else "当前确认版本"} {row["confirmation_version"]}；' + (f'{"拟替代" if draft else "替代"}确认版本 {row["supersedes_confirmation_version"]}。' if row["supersedes_confirmation_version"] else ('尚未确认。' if draft else '首次确认。')), END]
    return "\n\n".join(blocks)


def new_note(row: dict) -> str:
    body = managed_body(row)
    title = relative_path(row).rsplit("/", 1)[1][:-3]
    values = {"type": "investment-review", "review_type": row["review_type"], "review_key": row["review_key"], "review_date": row["review_date"],
              "period_start": row["period_start"], "period_end": row["period_end"], "confirmation_status": "confirmed", "confirmation_version": row["confirmation_version"],
              "data_status": row["data_status"], "gap_categories": row["gap_categories"], "source_week": "" if row["review_type"] == "daily" else row["period_start"],
              "source_revision": row["source_revision"], "payload_hash": row["payload_hash"], "supersedes_confirmation_version": row["supersedes_confirmation_version"],
              "facts_as_of": row["facts_as_of"], "generated_at": row["generated_at"], "confirmed_at": row["confirmed_at"], "ingest_status": "written_pending_readback",
              "created": row["confirmed_at"][:10], "updated": row["confirmed_at"][:10]}
    frontmatter = "\n".join(k + ": " + json.dumps(v, ensure_ascii=False, allow_nan=False) for k, v in values.items())
    return f'---\n{frontmatter}\n---\n\n# {title}\n\n{body}\n\n## 我的补充\n\n> 此处由我自由记录；同步不会覆盖这部分。\n\n-\n'


def split_managed(text: str) -> tuple[str, str, str]:
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
        raise JournalError("managed_markers_conflict")
    start, end = text.index(START), text.index(END) + len(END)
    if "## 我的补充" not in text[end:]:
        raise JournalError("manual_section_missing")
    return text[:start], text[start:end], text[end:]
