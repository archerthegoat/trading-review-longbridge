#!/usr/bin/env python3
"""Validate a Markdown review draft's structure and obvious secret leakage."""

from __future__ import annotations

import re
import sys
from pathlib import Path


DAILY_REQUIRED_HEADINGS = (
    "数据与授权状态",
    "复盘阶段",
    "前一美股交易日订单与成交",
    "昨日参考持仓与当前持仓",
    "快照净变化",
    "当周最新计划与计划 vs 实际",
    "当天交易日重要事件",
    "下一美股交易日重要事件",
    "事件对当前持仓/计划的主要影响",
    "过程复盘",
    "明日缺口与行动",
    "Wiki 写入分类与确认门",
    "最终状态",
)
EVENT_TABLE_HEADER = "| Asia/Shanghai 时间 | 美东时间 | 事件 | 状态 | 来源与数据状态 |"
DAILY_EVENT_HEADINGS = ("当天交易日重要事件", "下一美股交易日重要事件")
WEEKLY_EVENT_HEADINGS = ("下周重要大事件预览", "下周市场日程")
DATE_CELL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?$")
EVENT_FALLBACK_MARKERS = ("无已确认事件", "接口失败", "查询失败", "未返回")
EVENT_STATUS_VALUES = {"已发生", "预期", "未公布", "未验证"}
EVENT_IMPACT_HEADING = "事件对当前持仓/计划的主要影响"
ACTUAL_VALUE_RE = re.compile(r"(?:^|[；;])公告\s*[:=：＝]\s*([^；;|]+)")
MISSING_EVENT_VALUE_MARKERS = {"", "--", "—", "不可用", "N/A", "NA", "null", "None"}
SUCCESS_EMPTY_TITLE = "无已确认事件（相关筛选后）"
SUCCESS_EMPTY_SOURCE = "相关筛选已完成并返回空"
SECRET_PATTERNS = {
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "credential": (
        r"(?i)(?:"
        r"(?<![A-Za-z0-9_])(?:access|refresh)\s*[_ -]?\s*token(?![A-Za-z0-9_])|"
        r"(?<![A-Za-z0-9_])client\s*[_ -]?\s*secret(?![A-Za-z0-9_])|"
        r"(?<![A-Za-z0-9_])api\s*[_ -]?\s*key(?![A-Za-z0-9_])|"
        r"authorization\s*[:=：＝]\s*\S+|bearer\s+\S+|"
        r"(?:cookie|password|secret|credential)\s*[:=：＝]\s*\S+|"
        r"(?:账户编号|账户标识|订单\s*(?:id|号|编号)|成交\s*(?:id|号|编号)|凭据)"
        r"\s*[:=：＝]\s*\S+)"
    ),
    "broker credential": r"(?i)\b(?:longbridge|broker).{0,30}(?:password|credential|token)\s*[:=：＝]",
}


def event_contract_errors(text: str) -> list[str]:
    """Require visible, timezone-complete event sections with real or explicit fallback rows."""
    errors: list[str] = []
    if EVENT_TABLE_HEADER not in text:
        errors.append("missing event table header: Asia/Shanghai/美东/事件/状态/来源与数据状态")

    if "每日盘前复盘" in text:
        for heading in DAILY_EVENT_HEADINGS:
            errors.extend(_validate_event_section(text, heading, "daily"))
    elif "周度复盘" in text:
        if not any(heading in text for heading in WEEKLY_EVENT_HEADINGS):
            errors.append("missing weekly event section: 下周重要大事件预览")
        else:
            for heading in WEEKLY_EVENT_HEADINGS:
                if heading in text:
                    errors.extend(_validate_event_section(text, heading, "weekly"))
    elif not any(heading in text for heading in (*DAILY_EVENT_HEADINGS, *WEEKLY_EVENT_HEADINGS)):
        errors.append("missing event section heading")
    return errors


def _section_body(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    remainder = text[start + len(heading) :]
    next_heading = re.search(r"\n##\s+", remainder)
    return remainder[: next_heading.start()] if next_heading else remainder


def _event_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if (
            len(cells) < 5
            or cells[0] == "Asia/Shanghai 时间"
            or (cells[0] and set(cells[0]) <= {"-", ":"})
        ):
            continue
        rows.append(cells[:5])
    return rows


def _has_explicit_fallback(row: list[str]) -> bool:
    return any(marker in cell for marker in EVENT_FALLBACK_MARKERS for cell in row[2:5])


def _has_actual_announcement(source_cell: str) -> bool:
    match = ACTUAL_VALUE_RE.search(source_cell)
    if not match:
        return False
    value = match.group(1).strip()
    return value not in MISSING_EVENT_VALUE_MARKERS and value.lower() not in {"n/a", "na", "null", "none"}


def _success_empty_errors(row: list[str], heading: str) -> list[str]:
    title, status, source = row[2], row[3], row[4]
    uses_empty_title = title == SUCCESS_EMPTY_TITLE
    uses_empty_source = SUCCESS_EMPTY_SOURCE in source
    errors: list[str] = []
    if uses_empty_title:
        if status != "已发生" or not uses_empty_source:
            errors.append(
                f"success-empty event row must use 已发生 and exact filter completion source in section: {heading}"
            )
    elif uses_empty_source:
        errors.append(f"success-empty source requires exact empty event name in section: {heading}")
    return errors


def _validate_event_section(text: str, heading: str, mode: str) -> list[str]:
    if heading not in text:
        return [f"missing {mode} event section: {heading}"]
    section = _section_body(text, heading)
    errors: list[str] = []
    if EVENT_TABLE_HEADER not in section:
        errors.append(f"missing event table header in section: {heading}")
        return errors
    rows = _event_rows(section)
    if not rows:
        errors.append(f"missing event rows in section: {heading}")
        return errors
    if not any(DATE_CELL_RE.fullmatch(row[0]) or _has_explicit_fallback(row) for row in rows):
        errors.append(f"missing dated or explicit fallback event row in section: {heading}")
    for row in rows:
        if row[3] not in EVENT_STATUS_VALUES:
            errors.append(f"invalid event status in section: {heading}: {row[3]}")
        errors.extend(_success_empty_errors(row, heading))
        if (_has_actual_announcement(row[2]) or _has_actual_announcement(row[4])) and row[3] != "已发生":
            errors.append(f"actual announcement must be marked 已发生 in section: {heading}: {row[2]}")
    return errors


def validate_draft_text(text: str) -> list[str]:
    errors: list[str] = []
    if "每日盘前复盘" in text:
        errors.extend(
            f"missing required daily section: {heading}"
            for heading in DAILY_REQUIRED_HEADINGS
            if f"## {heading}" not in text
        )
    elif "周度复盘" in text:
        for marker in ("数据与授权状态", "本周", "下周"):
            if marker not in text:
                errors.append(f"missing required marker: {marker}")
    errors.extend(event_contract_errors(text))
    if "每日盘前复盘" in text and EVENT_IMPACT_HEADING not in text:
        errors.append(f"missing daily event impact summary: {EVENT_IMPACT_HEADING}")
    errors.extend(
        f"possible {label} found" for label, pattern in SECRET_PATTERNS.items() if re.search(pattern, text)
    )
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_review_draft.py <draft.md>")
        return 2

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"FAIL: draft not found: {path}")
        return 2
    if path.suffix.lower() != ".md":
        print("FAIL: draft must be a Markdown (.md) file")
        return 2

    text = path.read_text(encoding="utf-8")
    errors = validate_draft_text(text)
    if errors:
        print("FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1

    print("PASS: required review markers present; no obvious credential patterns found.")
    print("This does not verify broker coverage, account reconciliation, factual accuracy, or authorization.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
