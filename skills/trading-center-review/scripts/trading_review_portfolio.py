"""Append-only scoped valuation and non-executable holding principles."""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

import trading_review_state as state
import trading_review_instruments as instruments
from trading_review_valuation import FIELDS, decimal, validate_valuation


def put_valuations(store, rows: list, *, allowed_symbols: Iterable[str]) -> int:
    scope = set(allowed_symbols)
    try:
        for symbol in scope:
            instruments.us_symbol(symbol, option=False)
    except instruments.InstrumentContractError as exc:
        raise state.StateContractError("valuation_scope_requires_us_underlyings") from exc
    admitted = [validate_valuation(row) for row in rows]
    if len({r["symbol"] for r in admitted}) != len(admitted) or any(r["symbol"] not in scope for r in admitted):
        raise state.StateContractError("valuation_outside_explicit_portfolio_scope")
    count = 0
    with store._write():
        for row in admitted:
            existing = store.connection.execute("SELECT * FROM valuation_observations WHERE symbol=? AND as_of=?", (row["symbol"], row["as_of"])).fetchone()
            if existing:
                if dict(existing) != row:
                    raise state.StateContractError("valuation_observation_conflict")
                continue
            store.connection.execute(f"INSERT INTO valuation_observations VALUES ({','.join('?' for _ in FIELDS)})", tuple(row[k] for k in FIELDS))
            count += 1
    return count


def latest_valuations(store, symbols: Iterable[str]) -> dict:
    result = {}
    for symbol in set(symbols):
        try:
            instruments.us_symbol(symbol, option=False)
        except instruments.InstrumentContractError as exc:
            raise state.StateContractError("valuation_read_requires_us_underlyings") from exc
        row = store.connection.execute("SELECT * FROM valuation_observations WHERE symbol=? ORDER BY julianday(as_of) DESC LIMIT 1", (symbol,)).fetchone()
        if row:
            result[symbol] = validate_valuation(dict(row), symbol=symbol)
    return result


INTENT_FIELDS = {"underlying", "confirmed_at", "thesis", "holding_policy", "add_policy", "review_price", "possible_add_price", "trigger_basis", "execution_authorized"}


def put_management_intent(store, value: dict, *, user_confirmed: bool = False) -> dict:
    if user_confirmed is not True or not isinstance(value, dict) or set(value) != INTENT_FIELDS:
        raise state.StateContractError("management_intent_requires_explicit_confirmation")
    row = dict(value)
    try:
        instruments.us_symbol(row["underlying"], option=False)
    except instruments.InstrumentContractError as exc:
        raise state.StateContractError("management_intent_requires_us_underlying") from exc
    if row["execution_authorized"] is not False or row["trigger_basis"] != "unconfirmed":
        raise state.StateContractError("management_principles_are_not_execution_authority")
    row["confirmed_at"] = state._timestamp(row["confirmed_at"], "confirmed_at")
    for field in ("thesis", "holding_policy", "add_policy"):
        row[field] = state._text(row[field], field)
    for field in ("review_price", "possible_add_price"):
        if row[field] is not None and decimal(row[field]) <= 0:
            raise state.StateContractError("management_observation_price_invalid")
    content = state.content_hash(row)
    with store._write():
        previous = store.connection.execute("SELECT * FROM holding_management_intents WHERE underlying=? ORDER BY version DESC LIMIT 1", (row["underlying"],)).fetchone()
        if previous and previous["content_hash"] == content:
            return dict(previous)
        if previous and dt.datetime.fromisoformat(row["confirmed_at"].replace("Z", "+00:00")) <= dt.datetime.fromisoformat(previous["confirmed_at"].replace("Z", "+00:00")):
            raise state.StateContractError("management_confirmation_must_advance")
        row.update(version=previous["version"] + 1 if previous else 1, supersedes_version=previous["version"] if previous else None, content_hash=content)
        store.connection.execute("INSERT INTO holding_management_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", tuple(row[k] for k in state.EXPECTED_COLUMNS["holding_management_intents"]))
    return row
