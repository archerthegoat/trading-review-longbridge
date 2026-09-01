#!/usr/bin/env python3
"""Small data boundary for the TS UI. No rendering, file writes, or broker access."""
from __future__ import annotations

import argparse
import json
import sys

import render_trade_review_dashboard_v2 as dashboard

LIMIT = 8 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse(content):
    def invalid(_):
        raise ValueError("non-finite JSON")
    return json.loads(content, object_pairs_hook=unique_object, parse_constant=invalid)


def adapt(operation, value):
    if operation == "validate":
        return dashboard.validate_display_snapshot(value)
    if operation == "project":
        if not isinstance(value, dict) or set(value) != {"daily", "weekly"}:
            raise ValueError("project requires daily and weekly")
        return dashboard.project_display_snapshot(value["daily"], value["weekly"])
    if operation == "weekly":
        return dashboard.project_weekly_display(value)
    if operation == "weekly-db":
        import trading_review_state as state
        if not isinstance(value, dict) or set(value) != {"review_key"}:
            raise ValueError("weekly-db requires review_key")
        with state.read_state_store() as store:
            review = store.get_weekly_review(value["review_key"])
            if review is None:
                raise ValueError("weekly review does not exist")
            packet = dashboard.project_weekly_display(dashboard.build_weekly_packet(review))
            if packet["meta"].get("market_scope") != "US":
                raise ValueError("DB weekly display lacks verified US scope; retain the published weekly projection")
            return packet
    if operation == "enrich-db":
        import trading_review_state as state
        with state.read_state_store() as store:
            return dashboard.enrich_display_from_state(value, store)
    raise ValueError("unsupported data operation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("validate", "project", "weekly", "weekly-db", "enrich-db"))
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("input exceeds limit")
        value = parse(raw)
        result = adapt(args.operation, value)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        return 0
    except (ValueError, TypeError, KeyError, OSError):
        # Do not echo private input, paths, or upstream exception text.
        sys.stderr.write("display_data_gate_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
