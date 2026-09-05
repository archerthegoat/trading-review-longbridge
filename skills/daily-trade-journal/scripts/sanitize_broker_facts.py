#!/usr/bin/env python3
"""Offline, atomic broker preview. Never collect data, write files, or echo errors.

The caller must capture provider stdout/stderr in memory, verify collection and
logging safety, and pass a successful JSON array. ``complete`` means this input
batch validated, not that a broker account or trading window is complete.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from project_daily_trade_journal import (
    MAX_INPUT_BYTES, MAX_ROWS, OPTION_RIGHT_LABELS, ProjectionError,
    _parse_symbol, _safe_instrument, parse_date, parse_instant,
    parse_json_bytes, project_execution,
)

SCHEMA = "daily-trade-journal-broker-preview.v1"
# Do not allow an unrecognised contract identifier to fall back to a ticker.
# Digit-bearing equity roots need separately verified support; fail closed now.
SAFE_UNDERLYING = re.compile(r"[A-Z][A-Z.\-]{0,14}\.US\Z")
BLOCKED = {"schema_version": SCHEMA, "status": "blocked", "rows": [],
           "reason": "input_validation_failed"}


def sanitize(value, *, kind, review_date, cutoffs=()):
    """Return only approved fields, after every row has validated in memory.

    Cutoff relations compare the execution time TO each supplied public cutoff.
    Equal timestamps never establish causal ordering. Sequence ranks ties alike.
    Unknown metadata is discarded, not copied; relevant conflicting fields fail.
    """
    review = parse_date(review_date)
    boundaries = [parse_instant(item) for item in cutoffs]
    if kind not in {"executions", "positions"} or (kind == "positions" and boundaries):
        raise ProjectionError("invalid mode")
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise ProjectionError("invalid batch")
    staged = []
    for row in value:
        if not isinstance(row, dict) or "symbol" not in row:
            raise ProjectionError("invalid row")
        underlying, same_day, is_option, option = _parse_symbol(row["symbol"], review)
        if not SAFE_UNDERLYING.fullmatch(underlying):
            raise ProjectionError("unsupported symbol")
        if kind == "executions":
            keys = {"symbol", "side", "time", "executed_at", "filled_at", "instrument", "underlying"}
            fact = project_execution({k: v for k, v in row.items() if k in keys}, review)
            tool = fact.tool
            if fact.option:
                tool = ("0DTE " if same_day else "") + OPTION_RIGHT_LABELS[fact.option.right]
            safe = {"underlying": underlying, "action": fact.action, "tool": tool}
            safe["cutoff_relations"] = [
                "before" if fact.instant < boundary else "after" if fact.instant > boundary else "equal"
                for boundary in boundaries
            ]
            staged.append((fact.instant, safe))
        else:
            if "underlying" in row and row["underlying"] != underlying:
                raise ProjectionError("conflicting underlying")
            tool = "无法识别"
            if "instrument" in row:
                tool = _safe_instrument(row["instrument"], underlying=underlying,
                                        is_option=is_option, same_day=same_day)
            if option:
                tool = ("0DTE " if same_day else "") + OPTION_RIGHT_LABELS[option.right]
            staged.append((None, {"underlying": underlying, "tool": tool}))
    if kind == "executions":
        staged.sort(key=lambda pair: pair[0])
        ranks = {instant: index + 1 for index, instant in enumerate(sorted({p[0] for p in staged}))}
        for instant, safe in staged:
            safe["sequence"] = ranks[instant]
    return {"schema_version": SCHEMA, "status": "complete" if staged else "empty",
            "kind": kind, "review_date": review.isoformat(), "rows": [p[1] for p in staged]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("executions", "positions"), required=True)
    parser.add_argument("--review-date", required=True)
    parser.add_argument("--cutoff", action="append", default=[])
    args = parser.parse_args()
    try:
        value = parse_json_bytes(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        result = sanitize(value, kind=args.kind, review_date=args.review_date, cutoffs=args.cutoff)
    except Exception:
        # Provider/JSON exception text may contain secrets. Never print it or a traceback.
        result = BLOCKED
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
