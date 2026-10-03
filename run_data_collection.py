import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


LIVE_FALLBACK_KEYS = frozenset({"FRED_API_KEY", "ESTAT_APP_ID", "STATS_NZ_API_KEY",
                              "OCP_APIM_SUBSCRIPTION_KEY"})


def _fallback_state(path, state):
    """Persist only operational fields, never subprocess output or credentials."""
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _seed_fallback_status(source, destination):
    """Preserve budgets; caller holds the destination's collector write lock."""
    import re
    import live_data
    def read(path):
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise ValueError("INVALID_PROVIDER_STATUS") from None
        if not isinstance(value, dict) or not isinstance(value.get("providers", {}), dict):
            raise ValueError("INVALID_PROVIDER_STATUS")
        return value
    local, remote = read(destination), read(source)
    providers = local.get("providers")
    if providers is None:
        providers = {}
    incoming = remote.get("providers", {})
    for host, info in (incoming.items() if isinstance(incoming, dict) else []):
        if not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+", host):
            continue
        if not isinstance(info, dict):
            raise ValueError("INVALID_PROVIDER_STATUS")
        if info.get("retry_after_at") is not None and live_data.timestamp(info["retry_after_at"]) is None:
            raise ValueError("INVALID_PROVIDER_STATUS")
        deadline = live_data.timestamp(info.get("retry_after_at"))
        old = providers.get(host, {})
        if not isinstance(old, dict):
            raise ValueError("INVALID_PROVIDER_STATUS")
        if old.get("retry_after_at") is not None and live_data.timestamp(old["retry_after_at"]) is None:
            raise ValueError("INVALID_PROVIDER_STATUS")
        previous = live_data.timestamp(old.get("retry_after_at"))
        if deadline and (previous is None or deadline > previous):
            providers[host] = dict(old, retry_after_at=deadline.isoformat())
    local["providers"] = providers
    _fallback_state(destination, local)


def _seed_fallback_live(source, destination):
    """Use one observation batch while retaining later local budget marks."""
    import live_data
    incoming = live_data._load_file(source)
    if not incoming:
        return
    live_data.merge_bls_attempts(incoming, live_data.read_bls_attempts(destination))
    live_data.save(incoming, destination)


def maybe_start_live_fallback(keys=None):
    """Retired compatibility entry point; UI provider collection is disabled.

    Live views consume the commit-pinned public central collector batch via
    shared_snapshot. This function never reads keys, seeds budgets or launches
    subprocesses, including when invoked by older integrations.
    """
    return "disabled"


def load_status():
    defaults = {
        "last_run_timestamp": "N/A", "last_run_status": "N/A",
        "last_run_error": None, "total_successful_runs": 0,
        "total_partial_runs": 0, "total_failed_runs": 0, "history": []
    }
    try:
        with open("data_collection_status.json", "r", encoding="utf-8") as handle:
            defaults.update(json.load(handle))
    except (OSError, ValueError):
        pass
    return defaults


def save_status(status):
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix=".collection-", dir=".")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, "data_collection_status.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def import_collection_app():
    """Skip the existing UI/network guards during import, then enable capture."""
    import streamlit as st
    previous_mock_mode = getattr(st, "_mock_mode", False)
    st._mock_mode = True
    try:
        st.session_state["active_live_model_weights"] = None
        st.session_state["demo_mode_chk"] = False
        return importlib.import_module("app")
    finally:
        st._mock_mode = previous_mock_mode


def _policy_summary(rates):
    states = {currency: rate.get("verification_status", rate.get("status", "UNAVAILABLE"))
              for currency, rate in rates.items() if isinstance(rate, dict)}
    verified = sum("🟢" in state for state in states.values())
    status = "SUCCESS" if len(states) == 8 and verified == 8 else ("PARTIAL" if verified else "FAILED")
    return {"status": status, "currencies": states}


def _run_component(call):
    try:
        result = call()
        if not isinstance(result, dict):
            return {"status": "FAILED", "issues": [{"reason": "MISSING_COLLECTION_SUMMARY"}]}
        return result
    except Exception as error:
        return {"status": "FAILED", "issues": [{"reason": type(error).__name__}]}


def collect(app):
    components = {}
    components["policy_rates"] = _run_component(lambda: _policy_summary(app.refresh_all_verified_policy_rates(fred_key=app.FRED_KEY)))
    components["eodhd"] = _run_component(app.prefetch_eodhd_production_data)
    components["snapshots"] = _run_component(app.save_all_g10_live_snapshots)
    components["outcomes"] = _run_component(app.update_open_outcomes)
    states = [component.get("collection_status", component.get("status", "FAILED")) for component in components.values()]
    overall = "SUCCESS" if all(state == "SUCCESS" for state in states) else ("FAILED" if all(state == "FAILED" for state in states) else "PARTIAL")
    return overall, components


def update_daily_markers(timestamp, components, live_only, path=Path("daily_collection_status.json"), finished_at=None,
                         reserve_provider_attempt=False):
    """Persist the pre-provider reservation and daily snapshot lifecycle."""
    if live_only and not reserve_provider_attempt:
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    fields = ("last_daily_attempt_slot_utc", "last_daily_attempt_at",
              "last_daily_completed_slot_utc", "last_daily_completed_at",
              "last_provider_attempt_at")
    marker = {field: existing.get(field) if isinstance(existing.get(field), str) else None
              for field in fields}
    if reserve_provider_attempt:
        marker["last_provider_attempt_at"] = timestamp
    if live_only:
        _fallback_state(path, marker)
        return
    started = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    slot = started.replace(hour=22, minute=0, second=0, microsecond=0)
    if started < slot:
        from datetime import timedelta
        slot -= timedelta(days=1)
    slot_utc = slot.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    marker["last_daily_attempt_slot_utc"] = slot_utc
    marker["last_daily_attempt_at"] = timestamp
    live_core_state = components.get("live_core", {}).get("collection_status",
                    components.get("live_core", {}).get("status"))
    if live_core_state in ("SUCCESS", "PARTIAL") and all(
           components.get(name, {}).get("collection_status",
                components.get(name, {}).get("status")) == "SUCCESS"
           for name in ("snapshots", "outcomes")):
        marker["last_daily_completed_slot_utc"] = slot_utc
        marker["last_daily_completed_at"] = finished_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _fallback_state(path, marker)


def collection_exit_code(overall, components, live_only):
    """Separate a completed partial dataset from a failed collection job."""
    if overall == "SUCCESS":
        return 0

    def state(name):
        component = components.get(name, {})
        return component.get("collection_status", component.get("status", "FAILED")) if isinstance(component, dict) else "FAILED"

    if state("live_core") not in ("SUCCESS", "PARTIAL"):
        return 1
    if live_only:
        return 0
    # A partial CORE/policy dataset is already blocked factor by factor in the
    # published cache. Missing snapshots or a crashed component are job errors.
    completed_daily = (state("policy_rates") in ("SUCCESS", "PARTIAL")
                       and state("snapshots") == "SUCCESS"
                       and state("outcomes") == "SUCCESS")
    return 0 if completed_daily else 1


def main():
    import fcntl
    import live_data
    from provider_transport import CollectorTransport, provider_day_counts
    os.environ["FX_COLLECTOR"] = "1"
    os.environ.pop("FX_READ_CORE_CACHE", None)
    live_only = "--live-only" in sys.argv
    with open(".data_collection.lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Data collection already running; no duplicate collection started.")
            return 1
        status = load_status()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Write the daily attempt before any provider request. The workflow's
        # always-run commit step can then retain it after a collector crash.
        update_daily_markers(timestamp, {}, live_only)
        try:
            app = import_collection_app()
            if not isinstance(app.requests, CollectorTransport):
                raise RuntimeError("DURABLE_COLLECTOR_TRANSPORT_REQUIRED")
            app.requests.enable_durable_usage()
            components = {}
            components["policy_rates"] = _run_component(lambda: _policy_summary(app.refresh_all_verified_policy_rates(fred_key=app.FRED_KEY)))
            # EODHD's limited daily budget is handled by its persisted collector.
            # Do not spend it every 30 minutes on unlicensed event datasets.
            components["live_core"] = _run_component(lambda: live_data.collect(app))
            os.environ["FX_READ_CORE_CACHE"] = "1"
            if not live_only:
                components["snapshots"] = _run_component(app.save_all_g10_live_snapshots)
                components["outcomes"] = _run_component(app.update_open_outcomes)
            states = [v.get("collection_status", v.get("status", "FAILED")) for v in components.values()]
            overall = "SUCCESS" if all(v == "SUCCESS" for v in states) else "FAILED" if all(v == "FAILED" for v in states) else "PARTIAL"
            error = None if overall == "SUCCESS" else "One or more collection components are incomplete; see components."
        except Exception as exception:
            overall, components, error = "FAILED", {}, type(exception).__name__
        if ("app" in locals() and isinstance(app.requests, CollectorTransport)
                and app.requests.persistence_failed):
            # Do not replace an unreadable/failed operational ledger with the
            # loader's defaults and thereby grant a fresh provider budget.
            print("Data collection: FAILED (PROVIDER_ATTEMPT_PERSISTENCE_FAILED)")
            return 1
        # Request reservations/outcomes were already saved during I/O. Reload
        # them instead of adding this run's counters a second time or replacing
        # its confirmed cooldown with the status from before collection.
        status = load_status()
        status.update({"last_run_timestamp": timestamp, "last_run_status": overall,
                       "last_run_error": error, "components": components})
        status["mode"] = "live" if live_only else "daily"
        # Preserve these across subsequent live-only runs. The attempt marker
        # prevents duplicate provider requests; completion remains distinct.
        update_daily_markers(timestamp, components, live_only)
        if "app" in locals() and isinstance(app.requests, CollectorTransport):
            old_providers = status.get("providers", {})
            old_providers = old_providers if isinstance(old_providers, dict) else {}
            day = datetime.now(timezone.utc).date().isoformat()
            providers = {}
            for host, old in old_providers.items():
                if not isinstance(old, dict):
                    providers[host] = old
                    continue
                combined = dict(old, requests_this_run=0, outcomes_this_run={}, last_failure_at=None, status="NOT_REQUESTED")
                combined.update(app.requests.usage.get(host, {}))
                combined.update(provider_day_counts(combined, day))
                providers[host] = combined
            for host, usage in app.requests.usage.items():
                if host not in providers:
                    providers[host] = dict(usage, **provider_day_counts(usage, day))
            status["providers"] = providers
        counter = {"SUCCESS": "total_successful_runs", "PARTIAL": "total_partial_runs", "FAILED": "total_failed_runs"}[overall]
        status[counter] = status.get(counter, 0) + 1
        status["history"] = (status.get("history", []) + [{"timestamp": timestamp, "status": overall, "error": error}])[-50:]
        save_status(status)
        print("Data collection:", overall)
        for name, result in components.items():
            print(name + ":", result.get("collection_status", result.get("status", "FAILED")))
        return collection_exit_code(overall, components, live_only)


if __name__ == "__main__":
    sys.exit(main())
