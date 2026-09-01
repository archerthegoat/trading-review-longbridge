"""Fixed instrument and observation context. No provider, storage, or inference.

An underlying identifies a company; it is not an execution instrument. Option
identities stay at the approved underlying-only projection, so this module
cannot distinguish contracts or label a generic option as a LEAP.
"""

from __future__ import annotations

import re
from typing import Any, Dict


TOOL_KINDS = frozenset({"stock", "single_stock_leveraged_etf", "leap_call"})
TIMEFRAMES = frozenset({"1H", "4H", "1D", "1W"})
CONTEXT_FIELDS = (
    "tool_kind", "trade_symbol", "observation_symbol", "observation_timeframe",
    "trigger_timeframe", "trigger_basis", "exception_note",
)
FACT_FIELDS = ("tool_kind", "underlying")
LABELS = {"stock": "正股", "single_stock_leveraged_etf": "单股杠杆 ETF", "leap_call": "LEAP Call", "unknown": "工具待确认"}
PERIOD_LABELS = {"1H": "1小时线", "4H": "4小时线", "1D": "日线", "1W": "周线"}
COMPACT_OPTION_IDENTITY_RE = re.compile(r"\d{6,8}[CPcp]\d+")
TEXT_OPTION_IDENTITY_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][A-Z0-9.\-]{0,20})?"
    r"\d{6,8}[CP]\d{5,}(?:\.US)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
HASH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})(?![A-Za-z0-9])"
)


class InstrumentContractError(ValueError):
    pass


def safe_symbol(value: Any, *, option: bool = True) -> str:
    """Also accepts legacy market tickers, never a full option contract."""
    if not isinstance(value, str):
        raise InstrumentContractError("symbol must be text")
    base = value.removesuffix(":OPTION") if option else value
    if not re.fullmatch(r"[A-Za-z0-9.^=\-]{1,32}", base) or COMPACT_OPTION_IDENTITY_RE.search(base):
        raise InstrumentContractError("symbol contains contract identity or unsupported encoding")
    return value


def contains_contract_identity(value: Any) -> bool:
    """Detect a concrete compact option identity embedded in ordinary text.

    Compact identities are case-insensitive in untrusted text. Full SHA-1 and
    SHA-256 tokens are removed first so a hash-shaped audit identity is not
    mistaken for an option contract.
    """
    return isinstance(value, str) and TEXT_OPTION_IDENTITY_RE.search(HASH_TOKEN_RE.sub("", value)) is not None


def _object(value: Any, fields: tuple, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise InstrumentContractError(f"{path} requires its exact fixed fields")
    return dict(value)


def us_symbol(value: Any, *, option: bool = False) -> str:
    safe_symbol(value, option=option)
    if not isinstance(value, str):
        raise InstrumentContractError("instrument symbol must be text")
    base = value.removesuffix(":OPTION") if option else value
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,20}\.US", base) or re.search(r"\d{6}[CP]\d", base):
        raise InstrumentContractError("only a US ticker or sanitized underlying:OPTION is allowed")
    return value


def normalize_instrument(value: Any, trade_symbol: str, *, allow_unknown: bool = True) -> Dict[str, Any]:
    row = _object(value, FACT_FIELDS, "instrument")
    us_symbol(trade_symbol, option=True)
    us_symbol(row["underlying"])
    if row["tool_kind"] not in TOOL_KINDS | ({"unknown"} if allow_unknown else set()):
        raise InstrumentContractError("unsupported trading tool")
    kind = row["tool_kind"]
    if kind == "stock" and trade_symbol != row["underlying"]:
        raise InstrumentContractError("stock trade symbol must equal its underlying")
    if kind == "single_stock_leveraged_etf" and (trade_symbol.endswith(":OPTION") or trade_symbol == row["underlying"]):
        raise InstrumentContractError("single-stock leveraged ETF needs its own ticker and a distinct underlying")
    if kind == "leap_call" and trade_symbol != row["underlying"] + ":OPTION":
        raise InstrumentContractError("LEAP identity must use the sanitized underlying:OPTION projection")
    if kind == "unknown" and trade_symbol not in {row["underlying"], row["underlying"] + ":OPTION"}:
        raise InstrumentContractError("unknown tool symbol and underlying disagree")
    return row


def normalize_context(value: Any, *, underlying: str, ready: bool = False, allow_unknown: bool = False) -> Dict[str, Any]:
    row = _object(value, CONTEXT_FIELDS, "execution_context")
    normalize_instrument({"tool_kind": row["tool_kind"], "underlying": underlying}, row["trade_symbol"], allow_unknown=allow_unknown)
    if row["observation_symbol"] is not None:
        us_symbol(row["observation_symbol"])
        if row["observation_symbol"] not in {underlying, row["trade_symbol"]}:
            raise InstrumentContractError("observation must use the underlying or the actual traded ticker")
    if row["tool_kind"] in {"stock", "leap_call"} and row["observation_symbol"] not in {None, underlying}:
        raise InstrumentContractError("stock/LEAP levels observe the underlying, not an option premium")
    for field in ("observation_timeframe", "trigger_timeframe"):
        if row[field] is not None and row[field] not in TIMEFRAMES:
            raise InstrumentContractError("unsupported observation/trigger timeframe")
    if row["trigger_timeframe"] is None:
        row["trigger_timeframe"] = row["observation_timeframe"]
    if row["trigger_basis"] not in {"bar_close", "intrabar_touch", "unconfirmed"}:
        raise InstrumentContractError("unsupported trigger basis")
    note = row["exception_note"]
    if note is not None and (not isinstance(note, str) or not note.strip() or len(note) > 500):
        raise InstrumentContractError("exception_note must be null or bounded nonempty text")
    if contains_contract_identity(note):
        raise InstrumentContractError("exception_note contains contract identity")
    if row["trigger_timeframe"] != row["observation_timeframe"] and (row["observation_timeframe"] is None or note is None):
        raise InstrumentContractError("different trigger timeframe needs an explicit agreed exception")
    if ready and (row["tool_kind"] == "unknown" or row["observation_symbol"] is None or row["observation_timeframe"] is None or row["trigger_basis"] == "unconfirmed"):
        raise InstrumentContractError("executable plan needs an explicit tool, observation asset/timeframe and trigger basis")
    return row


def validate_daily_evidence(context: Dict[str, Any], *, symbol: Any, period: Any) -> None:
    # This is a capability boundary, not a mapping from holding duration.
    if symbol != context["observation_symbol"]:
        raise InstrumentContractError("price evidence symbol differs from the observation asset")
    if period != "1D" or context["observation_timeframe"] != period:
        raise InstrumentContractError("automatic price zones require matching completed 1D observation evidence")


def matches(context: Dict[str, Any], trade_symbol: str, instrument: Any, *, underlying: str) -> bool:
    if not isinstance(instrument, dict):
        return False
    return (
        context["trade_symbol"] == trade_symbol
        and context["tool_kind"] == instrument.get("tool_kind")
        and instrument.get("underlying") == underlying
        and instrument.get("tool_kind") in TOOL_KINDS
    )


def display_trade_symbol(trade_symbol: str, tool_kind: str) -> str:
    if tool_kind == "leap_call" and trade_symbol.endswith(":OPTION"):
        return f"{trade_symbol.removesuffix(':OPTION').removesuffix('.US')} LEAP（脱敏）"
    return trade_symbol.removesuffix(".US")


def context_text(context: Dict[str, Any]) -> str:
    traded = display_trade_symbol(context["trade_symbol"], context["tool_kind"])
    symbol = context["observation_symbol"]
    asset = "待确认" if symbol is None else symbol.removesuffix(".US")
    period = PERIOD_LABELS.get(context["observation_timeframe"], "待确认")
    basis = {"bar_close": "收线确认", "intrabar_touch": "盘中触及", "unconfirmed": "待确认"}[context["trigger_basis"]]
    trigger = PERIOD_LABELS.get(context["trigger_timeframe"], "待确认")
    extra = f"；触发周期：{trigger}（预先约定的例外）" if context["trigger_timeframe"] != context["observation_timeframe"] else ""
    return f"交易工具：{LABELS[context['tool_kind']]} · 实际交易对象：{traded} · 观察对象：{asset} · 观察周期：{period} · 触发方式：{basis}{extra}"
