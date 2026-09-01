"""Version-bound sources and confirmation checks. No broker or Vault access."""

from __future__ import annotations

import trading_review_state as state
from review_journal_contract import COUNTS, RATES, JournalError, instant, metrics, validate_payload

DAILY_DATASETS = ("account_snapshot", "positions_snapshot", "trades", "market_snapshots", "relevant_events")
DEP_COLUMNS = ("dataset", "period_start", "period_end", "contract_version", "partition_revision", "payload_hash")


def _dependency_rows(store, table, key, revision):
    if table not in {"daily_review_source_dependencies", "weekly_review_dependencies"}:
        raise JournalError("unsupported_dependency_source")
    return [dict(r) for r in store.connection.execute(f"SELECT {','.join(DEP_COLUMNS)} FROM {table} WHERE review_key=? AND review_revision=? ORDER BY dataset,period_start,period_end,contract_version", (key, revision))]


def _dependencies(store, table, key, revision):
    rows = _dependency_rows(store, table, key, revision)
    if not rows:
        raise JournalError("review_has_no_bound_dependencies")
    result, times = [], []
    for dep in rows:
        current = store._latest_partition(dep["dataset"], dep["period_start"], dep["period_end"], dep["contract_version"])
        if current is None or current["revision"] != dep["partition_revision"] or current["payload_hash"] != dep["payload_hash"]:
            raise JournalError("review_dependencies_changed")
        result.append((dep, dict(current)))
        times.append(current["collected_at"])
    return result, min(times, key=instant)


def _analysis_exists(store, facts, plan, contract):
    return store.connection.execute("SELECT 1 FROM analysis_snapshots WHERE facts_hash=? AND plan_hash=? AND contract_version=?", (facts, plan, contract)).fetchone() is not None


def assert_daily_source_recovery(store, manifest: dict) -> None:
    """Reject a completed run replay after a newer or conflicting lineage."""
    key = "daily:" + manifest["review_period"]["start"]
    run = store.connection.execute("SELECT * FROM runs WHERE run_id=?", (manifest["run_id"],)).fetchone()
    if (
        run is None
        or run["mode"] != "daily"
        or run["finished_at"] is None
        or run["period_start"] != run["period_end"]
        or run["period_start"] != manifest["review_period"]["start"]
        or (run["started_at"], run["finished_at"], run["source_contract_version"], run["data_status"])
        != (manifest["generated_at"], manifest["generated_at"], manifest["source_contract_version"], manifest["data_status"])
    ):
        raise JournalError("daily_source_run_identity_mismatch")
    latest = store.connection.execute(
        "SELECT * FROM daily_review_sources WHERE review_key=? ORDER BY revision DESC LIMIT 1",
        (key,),
    ).fetchone()
    source_identity = ("facts_hash", "plan_hash", "data_status", "source_contract_version", "analysis_contract_version", "generated_at")
    if latest is not None and all(latest[k] == manifest[k] for k in source_identity):
        return
    if latest is not None and instant(latest["generated_at"]) >= instant(manifest["generated_at"]):
        raise JournalError("daily_source_run_superseded")
    other_runs = store.connection.execute(
        "SELECT run_id,finished_at FROM runs WHERE mode='daily' AND period_start=? AND period_end=? AND finished_at IS NOT NULL AND run_id<>?",
        (run["period_start"], run["period_end"], run["run_id"]),
    )
    if any(instant(row["finished_at"]) >= instant(run["finished_at"]) for row in other_runs):
        raise JournalError("daily_source_run_superseded")


def record_daily_source(store, manifest: dict) -> int:
    """Called after ingestion; verifies exact DB partitions under the write lock."""
    if manifest["mode"] != "daily" or set(manifest["modules"]) != set(DAILY_DATASETS):
        raise JournalError("daily_source_modules_mismatch")
    key = "daily:" + manifest["review_period"]["start"]
    with store._write():
        assert_daily_source_recovery(store, manifest)
        run = store.connection.execute("SELECT * FROM runs WHERE run_id=?", (manifest["run_id"],)).fetchone()
        if run is None or run["mode"] != "daily" or run["finished_at"] is None or run["period_start"] != run["period_end"] or run["period_start"] != manifest["review_period"]["start"]:
            raise JournalError("daily_source_run_not_complete")
        if (run["source_contract_version"], run["data_status"], run["finished_at"]) != (manifest["source_contract_version"], manifest["data_status"], manifest["generated_at"]):
            raise JournalError("daily_source_run_mismatch")
        dependencies, fingerprint, times = [], {}, []
        for name in DAILY_DATASETS:
            module = manifest["modules"][name]
            contract = manifest["source_contract_version"] + ":" + name
            current = store._latest_partition(name, run["period_start"], run["period_end"], contract)
            if current is None or current["revision"] != module["revision"] or current["status"] != module["status"]:
                raise JournalError("daily_source_dependency_changed")
            dependencies.append({"dataset": name, "period_start": run["period_start"], "period_end": run["period_end"], "contract_version": contract, "partition_revision": current["revision"], "payload_hash": current["payload_hash"]})
            fingerprint[name] = {k: current[k] for k in ("payload_hash", "status", "error_category")}
            times.append(current["collected_at"])
        if state.content_hash(fingerprint) != manifest["facts_hash"]:
            raise JournalError("daily_source_facts_hash_mismatch")
        if not _analysis_exists(store, manifest["facts_hash"], manifest["plan_hash"], manifest["analysis_contract_version"]):
            raise JournalError("daily_source_analysis_missing")
        facts_at = min(times, key=instant)
        latest = store.connection.execute("SELECT * FROM daily_review_sources WHERE review_key=? ORDER BY revision DESC LIMIT 1", (key,)).fetchone()
        if latest is not None and all(latest[k] == manifest[k] for k in ("facts_hash", "plan_hash", "data_status", "source_contract_version", "analysis_contract_version", "generated_at")):
            old = _dependency_rows(store, "daily_review_source_dependencies", key, latest["revision"])
            if old == sorted(dependencies, key=lambda d: d["dataset"]):
                return latest["revision"]
            raise JournalError("daily_source_same_identity_dependencies_changed")
        revision = latest["revision"] + 1 if latest is not None else 1
        values = (key, revision, run["run_id"], run["period_start"], run["period_end"], run["source_contract_version"], manifest["analysis_contract_version"], manifest["facts_hash"], manifest["plan_hash"], facts_at, manifest["generated_at"], manifest["data_status"])
        store.connection.execute("INSERT INTO daily_review_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
        store.connection.executemany("INSERT INTO daily_review_source_dependencies VALUES (?,?,?,?,?,?,?,?)", [(key, revision, *(d[k] for k in DEP_COLUMNS)) for d in dependencies])
        return revision


def source_for(store, key: str, revision: int | None = None) -> dict:
    kind = key.split(":", 1)[0]
    if kind not in {"daily", "weekly"}:
        raise JournalError("unsupported_review_key")
    table = "daily_review_sources" if kind == "daily" else "weekly_reviews"
    source = store.connection.execute(f"SELECT * FROM {table} WHERE review_key=? ORDER BY revision DESC LIMIT 1", (key,)).fetchone()
    if source is None or revision is not None and source["revision"] != revision:
        raise JournalError("review_source_missing_or_superseded")
    s = dict(source)
    expected_key = f'daily:{s["period_start"]}' if kind == "daily" else f'weekly:{s["period_start"]}:{s["period_end"]}'
    if key != expected_key or not isinstance(s["plan_hash"], str) or not state.SHA256_RE.fullmatch(s["plan_hash"]):
        raise JournalError("review_source_plan_or_window_missing")
    run = store.connection.execute("SELECT * FROM runs WHERE run_id=?", (s["run_id"],)).fetchone()
    if run is None or run["mode"] != kind or run["finished_at"] is None or (run["period_start"], run["period_end"], run["source_contract_version"], run["data_status"]) != (s["period_start"], s["period_end"], s["source_contract_version"], s["data_status"]):
        raise JournalError("review_source_run_mismatch")
    deps, facts_at = _dependencies(store, "daily_review_source_dependencies" if kind == "daily" else "weekly_review_dependencies", key, s["revision"])
    if kind == "daily":
        if len(deps) != len(DAILY_DATASETS) or {d["dataset"] for d, _ in deps} != set(DAILY_DATASETS):
            raise JournalError("daily_source_incomplete_dependencies")
        for dep, _ in deps:
            if (dep["period_start"], dep["period_end"], dep["contract_version"]) != (s["period_start"], s["period_end"], s["source_contract_version"] + ":" + dep["dataset"]):
                raise JournalError("daily_source_dependency_scope_mismatch")
        fingerprint = {d["dataset"]: {k: p[k] for k in ("payload_hash", "status", "error_category")} for d, p in deps}
        if state.content_hash(fingerprint) != s["facts_hash"] or s["facts_as_of"] != facts_at:
            raise JournalError("daily_source_digest_mismatch")
        if not _analysis_exists(store, s["facts_hash"], s["plan_hash"], s["analysis_contract_version"]):
            raise JournalError("daily_source_analysis_missing")
        weekly_metrics = None
    else:
        if state.content_hash([d for d, _ in deps]) != s["dependency_hash"]:
            raise JournalError("weekly_dependency_digest_mismatch")
        record = store.connection.execute("SELECT * FROM weekly_execution_metrics WHERE review_key=? AND review_revision=?", (key, s["revision"])).fetchone()
        if record is None:
            raise JournalError("weekly_execution_metrics_not_bound")
        weekly_metrics = {k: record[k] for k in (*COUNTS, "data_status", "gap")}
        weekly_metrics["gap"] = {"confirmed_plan_or_execution_evidence_missing": "事前计划或执行证据不足", "confirmed_plan_authority_missing": "事前计划依据不足"}.get(weekly_metrics["gap"], weekly_metrics["gap"])
        for rate, (num, den) in RATES.items():
            exact_rate = record[num] / record[den] if record[den] else None
            stored = None if record[rate] is None else float(record[rate])
            if (stored is None) != (exact_rate is None) or stored is not None and abs(stored - exact_rate) > 0.00000051:
                raise JournalError("stored_weekly_rate_mismatch")
            weekly_metrics[rate] = exact_rate
        metrics(weekly_metrics)
    if s["data_status"] == "blocked" or instant(facts_at) > instant(s["generated_at"]):
        raise JournalError("review_source_blocked_or_timestamp_invalid")
    return {"review_type": kind, "review_key": key, "review_date": s["period_end"], "period_start": s["period_start"], "period_end": s["period_end"], "source_revision": s["revision"], "facts_hash": s["facts_hash"], "plan_hash": s["plan_hash"], "facts_as_of": facts_at, "source_generated_at": s["generated_at"], "data_status": s["data_status"], "weekly_metrics": weekly_metrics}


def verify_source(store, payload: dict) -> None:
    source = source_for(store, payload["review_key"], payload["source_revision"])
    for key, value in source.items():
        if key == "source_generated_at":
            if instant(payload["generated_at"]) < instant(value):
                raise JournalError("journal_predates_source")
        elif payload[key] != value:
            raise JournalError("journal_source_binding_mismatch")


def insert_confirmation(store, payload: dict) -> str:
    """All source validation and append operations share BEGIN IMMEDIATE."""
    validate_payload(payload)
    with store._write():
        verify_source(store, payload)
        key, version = payload["review_key"], payload["confirmation_version"]
        latest = store.connection.execute("SELECT c.*,b.payload_hash FROM confirmations c LEFT JOIN journal_confirmation_bindings b USING(review_key,confirmation_version) WHERE c.review_key=? ORDER BY c.confirmation_version DESC LIMIT 1", (key,)).fetchone()
        if latest and latest["confirmation_version"] == version and latest["payload_hash"] == payload["payload_hash"]:
            return "reused"
        if latest and latest["payload_hash"] is None:
            raise JournalError("legacy_confirmation_chain_unbound")
        expected = latest["confirmation_version"] + 1 if latest else 1
        if version != expected or payload["supersedes_confirmation_version"] != (version - 1 if latest else None):
            raise JournalError("confirmation_chain_changed")
        if latest and instant(payload["confirmed_at"]) <= instant(latest["confirmed_at"]):
            raise JournalError("confirmation_time_must_advance")
        store.connection.execute("INSERT INTO confirmations VALUES (?,?,?,?,?,?)", (key, version, "confirmed", payload["confirmed_at"], payload["facts_hash"], payload["supersedes_confirmation_version"]))
        store.connection.execute("INSERT INTO journal_confirmation_bindings VALUES (?,?,?,?,?,?,?,?,?)", tuple(payload[k] for k in ("review_key", "confirmation_version", "review_type", "source_revision", "plan_hash", "facts_as_of", "generated_at", "data_status", "payload_hash")))
    return "written"


def verify_confirmation(store, payload: dict) -> None:
    validate_payload(payload)
    verify_source(store, payload)
    row = store.connection.execute("SELECT c.*,b.review_type,b.source_revision,b.plan_hash,b.facts_as_of,b.generated_at,b.data_status,b.payload_hash FROM confirmations c JOIN journal_confirmation_bindings b USING(review_key,confirmation_version) WHERE c.review_key=? ORDER BY c.confirmation_version DESC LIMIT 1", (payload["review_key"],)).fetchone()
    latest = store.connection.execute("SELECT MAX(confirmation_version) FROM confirmations WHERE review_key=?", (payload["review_key"],)).fetchone()[0]
    if row is None or latest != payload["confirmation_version"] or any(row[k] != payload[k] for k in row.keys() if k != "supersedes_version") or row["supersedes_version"] != payload["supersedes_confirmation_version"]:
        raise JournalError("strict_confirmation_binding_missing_or_mismatched")
