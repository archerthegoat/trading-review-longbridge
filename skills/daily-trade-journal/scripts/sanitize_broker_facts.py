#!/usr/bin/env python3
"""Offline, atomic broker preview. Never collect data, write files, or echo errors.

The caller must capture provider stdout/stderr in memory, verify collection and
logging safety, and pass a successful JSON response. ``complete`` means this
input batch validated, not that a broker account or trading window is complete.
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

SCHEMA = "daily-trade-journal-broker-preview.v2"
# Do not allow an unrecognised contract identifier to fall back to a ticker.
# Digit-bearing equity roots need separately verified support; fail closed now.
SAFE_UNDERLYING = re.compile(r"[A-Z][A-Z.\-]{0,14}\.US\Z")
BLOCKED = {"schema_version": SCHEMA, "status": "blocked", "rows": [],
           "reason": "input_validation_failed"}


def _position_rows(value):
    """Extract only supported position rows from list or US account wrappers."""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise ProjectionError("invalid batch")

    expected_lists = ("stock_list", "option_list", "crypto_list", "cash_list")
    if any(key not in value for key in expected_lists):
        raise ProjectionError("invalid batch")
    lists = {}
    for key in expected_lists:
        rows = value[key]
        if not isinstance(rows, list):
            raise ProjectionError("invalid batch")
        lists[key] = rows
    # Silently dropping an unsupported asset class would make "complete" false.
    if lists["crypto_list"]:
        raise ProjectionError("unsupported position class")

    extracted = []
    for key in ("stock_list", "option_list"):
        for raw in lists[key]:
            if not isinstance(raw, dict):
                raise ProjectionError("invalid row")
            row = dict(raw)
            counter_id = row.get("counter_id")
            if "symbol" in row and counter_id is not None and row["symbol"] != counter_id:
                raise ProjectionError("conflicting symbol")
            if "symbol" not in row:
                row["symbol"] = counter_id
            provider_underlying = row.get("underlying_counter_id")
            if provider_underlying is not None:
                if "underlying" in row and row["underlying"] != provider_underlying:
                    raise ProjectionError("conflicting underlying")
                row["underlying"] = provider_underlying
            extracted.append(row)
    return extracted


def sanitize(value, *, kind, review_date=None, as_of_date=None, cutoffs=()):
    """Return only approved fields, after every row has validated in memory.

    Cutoff relations compare the execution time TO each supplied public cutoff.
    Equal timestamps never establish causal ordering. Sequence ranks ties alike.
    Unknown metadata is discarded, not copied; relevant conflicting fields fail.
    """
    if kind == "executions":
        if review_date is None or as_of_date is not None:
            raise ProjectionError("invalid date mode")
        reference_date = parse_date(review_date)
    elif kind == "positions":
        if as_of_date is None or review_date is not None or cutoffs:
            raise ProjectionError("invalid date mode")
        reference_date = parse_date(as_of_date)
        value = _position_rows(value)
    else:
        raise ProjectionError("invalid mode")
    boundaries = [parse_instant(item) for item in cutoffs]
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise ProjectionError("invalid batch")
    staged = []
    for row in value:
        if not isinstance(row, dict) or "symbol" not in row:
            raise ProjectionError("invalid row")
        underlying, same_day, is_option, option = _parse_symbol(row["symbol"], reference_date)
        if not SAFE_UNDERLYING.fullmatch(underlying):
            raise ProjectionError("unsupported symbol")
        if kind == "executions":
            keys = {"symbol", "side", "time", "executed_at", "filled_at", "instrument", "underlying"}
            fact = project_execution({k: v for k, v in row.items() if k in keys}, reference_date)
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
    result = {"schema_version": SCHEMA, "status": "complete" if staged else "empty",
              "kind": kind, "rows": [p[1] for p in staged]}
    result["review_date" if kind == "executions" else "as_of_date"] = reference_date.isoformat()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("executions", "positions"), required=True)
    parser.add_argument("--review-date")
    parser.add_argument("--as-of-date")
    parser.add_argument("--cutoff", action="append", default=[])
    args = parser.parse_args()
    try:
        value = parse_json_bytes(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        result = sanitize(value, kind=args.kind, review_date=args.review_date,
                          as_of_date=args.as_of_date, cutoffs=args.cutoff)
    except Exception:
        # Provider/JSON exception text may contain secrets. Never print it or a traceback.
        result = BLOCKED
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
