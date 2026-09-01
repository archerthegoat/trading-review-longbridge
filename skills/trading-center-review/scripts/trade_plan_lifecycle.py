#!/usr/bin/env python3
"""Persist a constructed draft, explicitly confirm it, or enrich the daily UI.

No broker or model calls. Confirmation always names the exact stored draft
version and content hash, and requires a separate human-confirmation flag.
All JSON artifacts remain in the owner-only private runtime directory.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import construct_trade_plan as constructor
import render_trade_review_dashboard_v2 as dashboard
import run_incremental_review as runner
import trading_review_state as state


def project_draft(value: Any) -> Dict[str, Any]:
    keys = {
        "schema_version", "data_status", "plan_status", "plan_readiness",
        "plan_id", "version", "symbol", "display_name", "direction",
        "setup_type", "plan_stage", "generated_at", "expires_at",
        "parent_plan_id", "parent_plan_version", "initial_buy_episode_key",
        "constraints", "source", "evidence_id", "evidence", "zones",
        "conditions", "gaps", "boundary", "content_hash",
        "execution_context",
    }
    required = keys - {"execution_context"}
    draft = constructor._strict_object(value, keys, required, "$draft")
    if draft["schema_version"] not in {constructor.DRAFT_SCHEMA, constructor.DRAFT_SCHEMA_V2} or draft["plan_status"] != "draft":
        raise state.StateContractError("only a constructed draft can be saved")
    contextual = draft["schema_version"] == constructor.DRAFT_SCHEMA_V2
    if contextual != ("execution_context" in draft):
        raise state.StateContractError("execution context requires draft v2")
    if draft["data_status"] not in {"complete", "partial"}:
        raise state.StateContractError("blocked technical evidence cannot be persisted as a plan")
    expected_readiness = "ready_for_confirmation" if draft["data_status"] == "complete" else "observation_only"
    if draft["plan_readiness"] != expected_readiness:
        raise state.StateContractError("draft readiness conflicts with data status")
    content = {key: item for key, item in draft.items() if key != "content_hash"}
    if constructor._hash(content) != draft["content_hash"]:
        raise state.StateContractError("draft content hash mismatch")
    source_keys = {"provider", "capability", "period", "timezone", "adjustment", "requested_start", "requested_end", "as_of"}
    if contextual:
        source_keys.add("symbol")
    source = constructor._strict_object(draft["source"], source_keys, source_keys, "$draft.source")
    if (source["provider"], source["capability"], source["period"]) != ("Longbridge", "kline history", "1D"):
        raise state.StateContractError("plan evidence must come from Longbridge completed daily bars")
    constraint_keys = {"holding_horizon_sessions", "minimum_reward_risk", "max_invalidation_pct", "tick_size"}
    constraints = constructor._strict_object(draft["constraints"], constraint_keys, constraint_keys, "$draft.constraints")
    evidence_keys = {
        "contract_version", "bars_used", "latest_close", "ema20", "ema50", "ema200",
        "ema_5d_direction", "atr14", "regime", "bottom_reversal_confirmed",
        "bottom_context_present", "levels", "reward_risk", "invalidation_pct",
    }
    evidence = constructor._strict_object(draft["evidence"], evidence_keys, evidence_keys, "$draft.evidence")
    if not isinstance(draft["zones"], list):
        raise state.StateContractError("draft zones must be an array")
    zones = []
    for raw in draft["zones"]:
        zone_keys = {"kind", "low", "high", "currency", "condition", "derived_from"}
        zone = constructor._strict_object(raw, zone_keys, zone_keys, "$draft.zones[]")
        zones.append({**zone, "data_status": "complete"})
    if draft["setup_type"] == "bottom_reversal" and any(row["kind"] == "entry" for row in zones):
        if evidence["bottom_reversal_confirmed"] is not True or evidence["bottom_context_present"] is not True:
            raise state.StateContractError("bottom entry requires both context and right-side confirmation")
    projection = {
        "schema_version": "trading-plan-state.v2" if contextual else "trading-plan-state.v1",
        "plan_id": draft["plan_id"], "version": draft["version"],
        "underlying": draft["symbol"], "direction": draft["direction"],
        "plan_stage": draft["plan_stage"], "setup_type": draft["setup_type"],
        "plan_status": "draft", "generated_at": draft["generated_at"],
        "effective_at": None, "confirmed_at": None, "expires_at": draft["expires_at"],
        "evidence": {
            "evidence_id": draft["evidence_id"], "source": source["provider"],
            "as_of": source["as_of"], "timezone": source["timezone"],
            "adjustment": source["adjustment"], "bars_used": evidence["bars_used"],
            "atr14": evidence["atr14"],
        },
        "constraints": {key: constraints[key] for key in ("minimum_reward_risk", "max_invalidation_pct")},
        "content_hash": draft["content_hash"],
        "supersedes_version": draft["version"] - 1 if draft["version"] > 1 else None,
        "parent_plan_id": draft["parent_plan_id"],
        "parent_plan_version": draft["parent_plan_version"],
        "initial_buy_episode_key": draft["initial_buy_episode_key"],
        "data_status": draft["data_status"], "zones": zones,
    }
    if contextual:
        projection["execution_context"] = copy.deepcopy(draft["execution_context"])
        projection["evidence"].update(symbol=source["symbol"], period=source["period"])
    state.normalize_plan_version(projection)
    return projection


def confirm_draft(
    store: state.StateStore, *, plan_id: str, draft_version: int,
    expected_hash: str, confirmed_at: str, user_confirmed: bool,
) -> state.PlanVersionResult:
    if user_confirmed is not True:
        raise state.StateContractError("separate human confirmation is required for this draft")
    draft = store.get_plan_version(plan_id, draft_version)
    if draft is None or draft["plan_status"] != "draft" or draft["content_hash"] != expected_hash:
        raise state.StateContractError("confirmation must match the exact saved draft and hash")
    confirmed = copy.deepcopy(draft)
    confirmed.update({
        "version": draft_version + 1, "supersedes_version": draft_version,
        "plan_status": "confirmed", "confirmed_at": confirmed_at, "effective_at": confirmed_at,
    })
    return store.put_plan_version(confirmed)


def dashboard_detail(plan: Mapping[str, Any], *, as_of: str, quote: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    state.normalize_plan_version(dict(plan))
    now = dt.datetime.fromisoformat(state._timestamp(as_of, "as_of").replace("Z", "+00:00"))
    generated = dt.datetime.fromisoformat(plan["generated_at"].replace("Z", "+00:00"))
    if now < generated:
        raise state.StateContractError("daily packet predates the plan evidence")
    if plan["plan_status"] == "confirmed" and now < dt.datetime.fromisoformat(plan["effective_at"].replace("Z", "+00:00")):
        raise state.StateContractError("daily cutoff predates plan effectiveness")
    fields = {
        "plan_id", "version", "plan_stage", "plan_status", "setup_type",
        "evidence", "zones", "parent_plan_id", "parent_plan_version", "initial_buy_episode_key",
        "underlying",
    }
    if "execution_context" in plan:
        fields.add("execution_context")
    detail = {key: copy.deepcopy(plan[key]) for key in fields}
    expiry = dt.datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00"))
    if now >= expiry:
        detail["plan_status"] = "expired"  # Display only; never rewrite the stored plan.
    relation = "unavailable"
    if quote is not None:
        quote_keys = {"source", "price", "as_of", "data_status"}
        if "execution_context" in plan:
            quote_keys.add("symbol")
        item = constructor._strict_object(dict(quote), quote_keys, quote_keys, "$quote")
        if item["source"] != "Longbridge":
            raise state.StateContractError("quote relation is Longbridge-only")
        if "execution_context" in plan and item["symbol"] != plan["execution_context"]["observation_symbol"]:
            raise state.StateContractError("quote must match the plan observation asset")
        status = state._status(item["data_status"], "$quote.data_status")
        quote_at = dt.datetime.fromisoformat(state._timestamp(item["as_of"], "$quote.as_of").replace("Z", "+00:00"))
        if quote_at > now:
            raise state.StateContractError("quote cannot be newer than the daily cutoff")
        # Quote freshness is provided by the collector's explicit session/cutoff
        # contract, not inferred from weekends or from arbitrary wall-clock age.
        if status == "stale":
            relation = "stale"
        elif status == "complete":
            price = Decimal(state._decimal(item["price"], "$quote.price"))
            if price <= 0:
                raise state.StateContractError("quote price must be positive")
            action_kind = "entry" if plan["plan_stage"] == "pre_entry" else "add"
            zones = [row for row in plan["zones"] if row["kind"] == action_kind]
            if len(zones) == 1:
                relation = "below" if price < Decimal(zones[0]["low"]) else "above" if price > Decimal(zones[0]["high"]) else "inside"
    detail["quote_relation"] = relation
    dashboard._validate_plan_detail(detail, "$plan_detail")
    return detail


def enrich_daily(packet: Any, plan: Mapping[str, Any], quote: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    daily = copy.deepcopy(dashboard.validate_packet(packet))
    detail = dashboard_detail(plan, as_of=daily["meta"]["generated_at"], quote=quote)
    context = plan.get("execution_context")
    matches = [row for row in daily["positions_plans"]["items"] if (
        (context is None and row["symbol"] == plan["underlying"])
        or (context is not None and row["symbol"] == context["trade_symbol"]
            and row.get("instrument") is not None
            and state.instruments.matches(
                context, row["symbol"], row["instrument"], underlying=plan["underlying"]
            ))
    )]
    if not matches:
        raise state.StateContractError("plan underlying must already exist in the daily plan table")
    for row in matches:
        row["plan_detail"] = detail
        if context is not None:
            row["execution_context"] = copy.deepcopy(context)
        row["plan_coverage"] = "已确认计划" if detail["plan_status"] == "confirmed" else "已到期，需重新确认" if detail["plan_status"] == "expired" else "待确认草案，不计入覆盖率"
        if detail["plan_status"] != "confirmed":
            row["has_gap"] = True
            row["gap"] = "；".join(filter(None, [row["gap"], row["plan_coverage"]]))
            # Confirmation and evidence completeness are separate axes.
            # A fully evidenced draft is not incomplete solely for awaiting approval.
            if detail["plan_status"] == "expired" or plan["data_status"] != "complete":
                row["data_status"] = "stale" if detail["plan_status"] == "expired" else "partial"
                daily["positions_plans"]["status"] = "partial"
                daily["meta"]["overall_status"] = "partial"
    return dashboard.validate_packet(daily)


def validate_confirmation_clock(confirmed_at: str, *, exact_replay: bool = False) -> None:
    stamp = dt.datetime.fromisoformat(state._timestamp(confirmed_at, "confirmed_at").replace("Z", "+00:00"))
    now = dt.datetime.fromisoformat(state.utc_now().replace("Z", "+00:00"))
    if not exact_replay and not dt.timedelta(0) <= now - stamp <= dt.timedelta(minutes=5):
        raise state.StateContractError("new confirmation must use the current clock; backdating is prohibited")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("save-draft")
    save.add_argument("--input", type=Path, required=True)
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--plan-id", required=True)
    confirm.add_argument("--draft-version", type=int, required=True)
    confirm.add_argument("--content-hash", required=True)
    confirm.add_argument("--confirmed-at", required=True)
    confirm.add_argument("--user-confirmed", action="store_true")
    enrich = commands.add_parser("enrich-daily")
    enrich.add_argument("--input", type=Path, required=True)
    enrich.add_argument("--plan-id", required=True)
    enrich.add_argument("--version", type=int, required=True)
    enrich.add_argument("--quote-input", type=Path)
    for command in (save, confirm, enrich):
        command.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = runner._private_path(args.output, "output")
        value = None
        quote = None
        if args.command in {"save-draft", "enrich-daily"}:
            path = runner._private_path(args.input, "input", require_existing=True)
            value = json.loads(path.read_text(encoding="utf-8"))
            value = project_draft(value) if args.command == "save-draft" else dashboard.validate_packet(value)
        if args.command == "confirm":
            if not args.user_confirmed:
                raise state.StateContractError("separate human confirmation is required")
            state._sha256(args.content_hash, "content_hash")
            state._timestamp(args.confirmed_at, "confirmed_at")
        if args.command == "enrich-daily" and args.quote_input:
            path = runner._private_path(args.quote_input, "quote", require_existing=True)
            quote = json.loads(path.read_text(encoding="utf-8"))
        if args.command != "save-draft" and not args.state_db.is_file():
            raise state.StateContractError("plan state database does not exist")
        with state.open_state_store(args.state_db) as store:
            if args.command == "save-draft":
                result = store.put_plan_version(value)._asdict()
            elif args.command == "confirm":
                prior = store.get_plan_version(args.plan_id, args.draft_version + 1)
                exact_replay = prior is not None and prior["plan_status"] == "confirmed" and prior["content_hash"] == args.content_hash and prior["confirmed_at"] == args.confirmed_at
                validate_confirmation_clock(args.confirmed_at, exact_replay=exact_replay)
                result = confirm_draft(
                    store, plan_id=args.plan_id, draft_version=args.draft_version,
                    expected_hash=args.content_hash, confirmed_at=args.confirmed_at,
                    user_confirmed=args.user_confirmed,
                )._asdict()
            else:
                plan = store.get_plan_version(args.plan_id, args.version)
                if plan is None:
                    raise state.StateContractError("plan version does not exist")
                result = enrich_daily(value, plan, quote)
            runner._write_private_json(output, result)
        print(json.dumps({"status": "completed", "command": args.command, "output": str(output)}))
        return 0
    except (state.StateStoreError, constructor.PlanConstructionError, dashboard.DashboardRenderError,
            OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "blocked", "error_category": "plan_lifecycle_contract_failure"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
