#!/usr/bin/env python3
"""Validate a Markdown review draft's structure and obvious secret leakage."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED = ("数据与授权状态", "未验证", "本周", "下周")
EVENT_TABLE_HEADER = "| Asia/Shanghai 时间 | 美东时间 | 事件 | 状态 | 来源与数据状态 |"
DAILY_EVENT_HEADINGS = ("当天交易日重要事件", "下一美股交易日重要事件")
WEEKLY_EVENT_HEADINGS = ("下周重要大事件预览", "下周市场日程")
SECRET_PATTERNS = {
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "access token": r"(?i)\b(?:access[_ -]?token|api[_ -]?key|secret[_ -]?key)\s*[:=]",
    "broker credential": r"(?i)\b(?:longbridge|broker).{0,30}(?:password|credential|token)\s*[:=]",
}


def event_contract_errors(text: str) -> list[str]:
    """Require a visible, timezone-complete event section in review drafts."""
    errors: list[str] = []
    if EVENT_TABLE_HEADER not in text:
        errors.append("missing event table header: Asia/Shanghai/美东/事件/状态/来源与数据状态")

    if "每日盘前复盘" in text:
        errors.extend(f"missing daily event section: {heading}" for heading in DAILY_EVENT_HEADINGS if heading not in text)
    elif "周度复盘" in text:
        if not any(heading in text for heading in WEEKLY_EVENT_HEADINGS):
            errors.append("missing weekly event section: 下周重要大事件预览")
    elif not any(heading in text for heading in (*DAILY_EVENT_HEADINGS, *WEEKLY_EVENT_HEADINGS)):
        errors.append("missing event section heading")
    return errors


def validate_draft_text(text: str) -> list[str]:
    errors = [f"missing required marker: {item}" for item in REQUIRED if item not in text]
    errors.extend(event_contract_errors(text))
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
