"""Fixed scalar valuation evidence; no account data or automatic peer expansion."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import trading_review_instruments as instruments


FIELDS = (
    "symbol", "instrument_type", "as_of", "pe_ttm", "roe_pct", "roe_period_end",
    "roe_period_label", "roe_basis", "roe_quality", "pr", "status", "gap", "source",
)


class ValuationError(ValueError):
    pass


def decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        raise ValuationError("valuation_number_must_be_decimal_text")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValuationError("invalid_valuation_number") from exc
    if not number.is_finite() or abs(number) > Decimal("1000000000000"):
        raise ValuationError("invalid_valuation_number")
    return number


def calculate_pr(pe_ttm: str, roe_pct: str) -> str:
    pe, roe = decimal(pe_ttm), decimal(roe_pct)
    if pe <= 0 or roe <= 0:
        raise ValuationError("nonpositive_earnings_or_equity_return")
    return format((pe / roe).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP), "f")


def validate_valuation(value: Any, *, symbol: str | None = None) -> dict:
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValuationError("valuation_fields_mismatch")
    row = dict(value)
    try:
        instruments.us_symbol(row["symbol"], option=False)
    except instruments.InstrumentContractError as exc:
        raise ValuationError("valuation_requires_us_underlying") from exc
    if symbol is not None and row["symbol"] != symbol:
        raise ValuationError("valuation_underlying_mismatch")
    if row["instrument_type"] not in {"company", "fund"} or row["source"] != "Longbridge":
        raise ValuationError("valuation_source_or_instrument_mismatch")
    try:
        as_of = dt.datetime.fromisoformat(row["as_of"].replace("Z", "+00:00"))
        if as_of.tzinfo is None or "T" not in row["as_of"]:
            raise ValueError()
    except (AttributeError, ValueError, TypeError) as exc:
        raise ValuationError("valuation_as_of_invalid") from exc
    if row["status"] not in {"available", "unavailable", "not_applicable", "stale"}:
        raise ValuationError("valuation_status_invalid")
    if row["roe_quality"] not in {"positive_income_equity", "nonpositive", "unverified", "not_applicable"}:
        raise ValuationError("valuation_quality_invalid")
    if (
        not isinstance(row["gap"], str)
        or len(row["gap"]) > 200
        or re.search(r"[<>\x00-\x1f]|/Users/|/private/", row["gap"])
        or instruments.contains_contract_identity(row["gap"])
    ):
        raise ValuationError("valuation_gap_invalid")
    for key in ("pe_ttm", "roe_pct", "pr"):
        if row[key] is not None:
            decimal(row[key])
    has_period = row["roe_period_end"] is not None
    if has_period:
        try:
            end = dt.date.fromisoformat(row["roe_period_end"])
            if end.isoformat() != row["roe_period_end"] or end > as_of.astimezone(ZoneInfo("America/New_York")).date():
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise ValuationError("valuation_report_period_invalid") from exc
        if not isinstance(row["roe_period_label"], str) or not re.fullmatch(r"FY \d{4}", row["roe_period_label"]):
            raise ValuationError("valuation_report_label_invalid")
        if row["roe_basis"] != "annual" or row["roe_pct"] is None:
            raise ValuationError("valuation_report_basis_invalid")
        if (as_of.date() - end).days > 550 and row["status"] == "available":
            raise ValuationError("valuation_report_is_stale")
    elif any(row[k] is not None for k in ("roe_period_label", "roe_basis", "roe_pct")):
        raise ValuationError("roe_requires_verifiable_annual_period")
    if row["instrument_type"] == "fund":
        if row["status"] != "not_applicable" or any(row[k] is not None for k in ("pe_ttm", "roe_pct", "pr", "roe_period_end")):
            raise ValuationError("fund_has_no_corporate_pr")
    if row["status"] == "available":
        if row["instrument_type"] != "company" or not has_period or row["pe_ttm"] is None or row["gap"] or row["roe_quality"] != "positive_income_equity":
            raise ValuationError("available_pr_requires_complete_evidence")
        if row["pr"] != calculate_pr(row["pe_ttm"], row["roe_pct"]):
            raise ValuationError("pr_calculation_mismatch")
    elif row["pr"] is not None or not row["gap"].strip():
        raise ValuationError("unavailable_pr_requires_null_and_reason")
    return row


def project_annual_roe(raw: Any, symbol: str, as_of: str) -> Mapping[str, str] | None:
    """Admit one reported annual ROE row, discarding all other response fields."""
    if not isinstance(raw, dict) or raw.get("report") != "af":
        raise ValuationError("annual_report_identity_mismatch")
    indicators = raw.get("list", {}).get("IS", {}).get("indicators", [])
    identities = {v for v in [raw.get("symbol"), *[(r.get("entry") or {}).get("symbol") for r in indicators]] if v}
    # CLI 0.28 may leave the root symbol blank; require its nested identity.
    if identities != {symbol}:
        raise ValuationError("annual_report_identity_mismatch")
    values = []
    incomes = {}
    for indicator in raw.get("list", {}).get("IS", {}).get("indicators", []):
        for account in indicator.get("accounts", []):
            if account.get("field") == "NetProfit":
                for point in account.get("values", []):
                    if point.get("value") not in {None, ""}:
                        key = (point.get("period"), point.get("fp_end"))
                        number = decimal(point["value"])
                        if key in incomes and incomes[key] != number:
                            raise ValuationError("conflicting_annual_income")
                        incomes[key] = number
    for indicator in raw.get("list", {}).get("IS", {}).get("indicators", []):
        for account in indicator.get("accounts", []):
            if account.get("field") != "ROE" or account.get("percent") is not True:
                continue
            for point in account.get("values", []):
                if not re.fullmatch(r"FY \d{4}", str(point.get("period", ""))) or point.get("value") in {None, ""}:
                    continue
                number = decimal(point["value"])
                end = dt.datetime.fromtimestamp(int(point["fp_end"]), ZoneInfo("America/New_York")).date()
                observed = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()
                if end > observed:
                    raise ValuationError("future_annual_report")
                income = incomes.get((point.get("period"), point.get("fp_end")))
                quality = "unverified" if income is None else "positive_income_equity" if income > 0 and number > 0 else "nonpositive"
                values.append({"roe_pct": str(number), "roe_period_end": end.isoformat(), "roe_period_label": point["period"], "roe_basis": "annual", "roe_quality": quality})
    values.sort(key=lambda r: r["roe_period_end"], reverse=True)
    if len(values) > 1 and values[0]["roe_period_end"] == values[1]["roe_period_end"] and values[0] != values[1]:
        raise ValuationError("conflicting_annual_roe")
    return values[0] if values else None
